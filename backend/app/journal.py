"""Журнал действий: кто и что поменял в Директе, с шагами отката."""

from __future__ import annotations

import contextvars
import json
import logging
import re
from contextlib import contextmanager
from itertools import zip_longest

from psycopg.types.json import Jsonb

from app.db import pool

log = logging.getLogger(__name__)

ACTION_TITLES = {
    "pause": "Остановка кампаний",
    "resume": "Запуск кампаний",
    "set_budget": "Изменение дневного бюджета",
    "budget": "Бюджеты и цена цели",
    "archive": "Архивация кампаний",
    "unarchive": "Возврат из архива",
    "delete": "Удаление кампаний",
    "create": "Создание кампании",
    "update": "Изменение кампании",
    "settings": "Изменение настроек кампании",
    "duplicate": "Копия кампании",
    "moderate": "Отправка на модерацию",
    "negative_list_apply": "Применение списка минус-фраз",
    "keywords": "Изменение ключевых фраз",
    "ads": "Изменение объявлений",
    "adgroups": "Изменение групп",
    "queries": "Поисковые запросы",
    "negatives": "Минус-фразы",
    "exclude_sites": "Запрет площадок",
    "bid_adjust": "Корректировки ставок",
    "links": "Ссылки по шаблону сайта",
    "undo": "Откат",
}

# Группы для фильтра «Тип» в истории: что меняли — деньги, минус-слова, состояние или содержимое кампании.
ACTION_KINDS = {
    "budget": ("set_budget", "budget", "bid_adjust"),
    "negatives": ("negatives", "negative_list_apply", "queries", "exclude_sites"),
    "state": ("pause", "resume", "archive", "unarchive", "delete", "moderate"),
    "edits": ("create", "update", "settings", "duplicate", "keywords", "ads", "adgroups", "links"),
    "undo": ("undo",),
}

# --- Кто действует -----------------------------------------------------------------------------
# Журнал показывает источник действия: пользователь, ИИ-агент или правило автоматизации.
# Агент и правила оборачивают свои вызовы в acting_as(...), сами функции действий об этом не знают.

_source: contextvars.ContextVar[str] = contextvars.ContextVar("action_source", default="user")
# Составное действие (откат, первая группа новой кампании) пишет одну запись сам,
# вложенные вызовы в это время журнал не трогают.
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("journal_quiet", default=False)


@contextmanager
def acting_as(source: str):
    token = _source.set(source if source in ("user", "ai", "rule") else "user")
    try:
        yield
    finally:
        _source.reset(token)


@contextmanager
def journal_quiet():
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


# Записи с откатом, сделанные за один запрос кабинета или одну фоновую задачу: кабинет предлагает
# «Отменить» прямо в сообщении о результате, не отправляя в историю.
_undoable: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("journal_undoable", default=None)


@contextmanager
def collect_undoable():
    ids: list[int] = []
    token = _undoable.set(ids)
    try:
        yield ids
    finally:
        _undoable.reset(token)


def note_undoable(log_id: int) -> None:
    ids = _undoable.get()
    if ids is not None:
        ids.append(log_id)


# Все записи, сделанные одной фоновой задачей: уведомление о её сбое ведёт на строку «Истории».
_logged: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("journal_logged", default=None)


@contextmanager
def collect_logged():
    ids: list[int] = []
    token = _logged.set(ids)
    try:
        yield ids
    finally:
        _logged.reset(token)


def single_undoable(ids: list[int]) -> int | None:
    """Отменить одной кнопкой можно, только если действие оставило ровно одну запись с откатом."""
    return ids[0] if len(ids) == 1 else None


# Предложение ИИ, которое сейчас выполняется: его записи журнала помечаются целью «ai_action:N» —
# так карточка в чате находит, что вернуть («Вернуть как было»), без отдельной колонки.
AI_ACTION_TARGET = "ai_action:"
_ai_action: contextvars.ContextVar[int | None] = contextvars.ContextVar("journal_ai_action", default=None)


@contextmanager
def for_ai_action(action_id: int | None):
    token = _ai_action.set(int(action_id) if action_id else None)
    try:
        yield
    finally:
        _ai_action.reset(token)


def ai_action_target(action_id: int) -> str:
    return f"{AI_ACTION_TARGET}{int(action_id)}"


def ai_action_logs(user_id: int, action_ids: list[int]) -> dict[int, list[dict]]:
    """Записи журнала, сделанные выполнением предложений ИИ: {id предложения: [записи по порядку]}.

    Откаты («Откат: …») копируют цели исходной записи — их не берём (undo_of)."""
    ids = sorted({int(i) for i in action_ids or []})
    if not ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, action, title, status, undo, undone_at, targets, created_at
            FROM action_logs
            WHERE user_id = %s AND undo_of IS NULL AND targets && %s
            ORDER BY id
            """,
            (user_id, [ai_action_target(i) for i in ids]),
        ).fetchall()
    wanted = {ai_action_target(i): i for i in ids}
    out: dict[int, list[dict]] = {}
    for r in rows:
        for t in r["targets"] or []:
            if t in wanted:
                out.setdefault(wanted[t], []).append(r)
    return out


def can_undo(row: dict) -> bool:
    """Запись можно откатить: есть шаги, выполнилась хотя бы частично и ещё не откачена."""
    undo = row.get("undo") or {}
    return bool(undo.get("steps")) and row.get("status") in ("ok", "partial") and not row.get("undone_at")


def current_source() -> str:
    return _source.get()


# --- Запись ------------------------------------------------------------------------------------


# Ответ Директа на большое действие бывает огромным. Раньше JSON обрезали по длине — получался битый
# текст, и история теряла причины ошибок («Директ не прислал причину»). Теперь ужимаем саму структуру:
# длинные списки и строки короче, вложенность мельче, а тексты ошибок остаются целиком.
_DUMP_LIMIT = 8000
_KEEP_KEYS = frozenset({"message", "error", "errors", "warning", "warnings"})
# (элементов в списке, символов в строке, глубина) — от мягкого к жёсткому; берём первый вариант, что влез.
_SHRINK_STEPS = ((100, 1000, 6), (40, 300, 4), (15, 120, 3), (5, 60, 2), (2, 40, 1))
# Ошибки ужимаем мягко; построже — только последней ступенью, если их самих тысячи.
_KEEP_SOFT = (200, 2000)
_KEEP_HARD = (50, 500)
# Хвост ужатого списка — «… и ещё 120», список глубже предела — «… 40 шт.».
_MORE_RE = re.compile(r"^… (?:и ещё )?(\d+)(?: шт\.)?$")


def _dump(value) -> str:
    """JSON для payload / result: всегда целый; большой — ужат по структуре, ошибки в нём полностью."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= _DUMP_LIMIT:
        return text
    data = json.loads(text)
    steps = [(step, _KEEP_SOFT) for step in _SHRINK_STEPS] + [(_SHRINK_STEPS[-1], _KEEP_HARD)]
    for (items, chars, depth), keep in steps:
        text = json.dumps(_shrink(data, items, chars, depth, keep), ensure_ascii=False)
        if len(text) <= _DUMP_LIMIT:
            break
    return text


def _shrink(value, items: int, chars: int, depth: int | None, keep: tuple[int, int]):
    """Копия value с короткими списками и строками. Под ключами ошибок — пределы keep и любая глубина."""
    deeper = None if depth is None else depth - 1
    if isinstance(value, str):
        return value if len(value) <= chars else value[: max(chars - 1, 1)] + "…"
    if isinstance(value, list):
        if depth is not None and depth <= 0:
            return f"… {len(value)} шт."
        picked = _pick(value, items)
        out = [_shrink(value[i], items, chars, deeper, keep) for i in picked]
        if len(picked) < len(value):
            out.append(f"… и ещё {len(value) - len(picked)}")
        return out
    if isinstance(value, dict):
        res: dict = {}
        dropped = 0
        for key, item in value.items():
            if key in _KEEP_KEYS:
                res[key] = _shrink(item, keep[0], keep[1], None, keep)
            elif len(res) >= max(items * 2, 20):
                dropped += 1
            elif depth is not None and depth <= 0 and isinstance(item, (list, dict)):
                # Глубже не идём: вместо вложенного — сколько там было.
                res[key] = f"… {len(item)} шт." if isinstance(item, list) else "…"
            else:
                res[key] = _shrink(item, items, chars, deeper, keep)
        if dropped:
            res["…"] = f"ещё {dropped}"
        return res
    return value


def _pick(values: list, limit: int) -> list[int]:
    """Какие элементы списка оставить: сначала с ошибками — их история и показывает, затем по порядку."""
    if len(values) <= limit:
        return list(range(len(values)))
    bad = [i for i, v in enumerate(values) if _has_problem(v)][:limit]
    taken = set(bad)
    rest = [i for i in range(len(values)) if i not in taken][: limit - len(bad)]
    return sorted(bad + rest)


def _has_problem(value) -> bool:
    return isinstance(value, dict) and bool(
        value.get("error")
        or value.get("errors")
        or value.get("message")
        or value.get("status") == "error"
        or value.get("ok") is False
    )


def _load(text: str | None):
    return _parse(text)[0]


def _parse(text: str | None) -> tuple[object, bool]:
    """→ (значение, обрезана ли запись). Старые записи резали по 4000 символов посреди JSON — чиним, что уцелело."""
    if not text:
        return None, False
    try:
        return json.loads(text), False
    except ValueError:
        pass
    fixed = _repair(text)
    return (text if fixed is None else fixed), True


def _repair(text: str):
    """Закрываем оборванную строку и скобки; не вышло — отступаем к последней запятой или скобке."""
    stack: list[str] = []
    cuts: list[tuple[int, str]] = []  # (позиция, закрывающие скобки): здесь текст можно обрезать
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            cuts.append((i + 1, "".join(reversed(stack))))
        elif ch in "}]":
            if stack:
                stack.pop()
            cuts.append((i + 1, "".join(reversed(stack))))
        elif ch == ",":
            cuts.append((i, "".join(reversed(stack))))
    tail = "".join(reversed(stack))
    back = [text[:pos] + closers for pos, closers in reversed(cuts[-200:])]
    if in_str:
        # Короткий обрывок строки (кусок фразы или причины) лучше выбросить целиком;
        # длинную строку (одна огромная причина) — закрыть, чтобы не потерять её начало.
        body = text[:-1] if esc else re.sub(r"\\u[0-9a-fA-F]{0,3}$", "", text)
        closed = [body + '"' + tail]
        near = bool(cuts) and len(text) - cuts[-1][0] <= 1500
        candidates = back + closed if near else closed + back
    else:
        candidates = [text + tail] + back
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def _id_list(payload: dict) -> list[int]:
    """Кампании из параметров действия — по порядку, без повторов."""
    out: list[int] = []

    def add(value) -> None:
        if isinstance(value, bool):
            return
        try:
            rid = int(value)
        except (TypeError, ValueError):
            return
        if rid not in out:
            out.append(rid)

    if payload.get("campaign_row_id"):
        add(payload["campaign_row_id"])
    for key in ("campaign_row_ids", "campaign_ids"):
        values = payload.get(key)
        # Ужатый список кончается строкой «… и ещё N» — её пропустит int().
        if isinstance(values, (list, tuple, set)):
            for value in values:
                add(value)
    return out


def _ids(payload: dict) -> set[int]:
    return set(_id_list(payload))


def _workspace_for(user_id: int, payload: dict) -> int | None:
    """Проект действия — если все затронутые кампании лежат в одном."""
    ids = _ids(payload)
    if not ids:
        return None
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT workspace_id FROM workspace_campaigns
            WHERE user_id = %s AND campaign_row_id = ANY(%s)
            """,
            (user_id, list(ids)),
        ).fetchall()
    return int(rows[0]["workspace_id"]) if len(rows) == 1 else None


_CAMPAIGNS_OF = {
    "adgroup": "SELECT DISTINCT campaign_row_id AS id FROM direct_adgroups WHERE user_id = %s AND id = ANY(%s)",
    "keyword": """
        SELECT DISTINCT g.campaign_row_id AS id FROM direct_keywords k
        JOIN direct_adgroups g ON g.id = k.adgroup_row_id
        WHERE k.user_id = %s AND k.id = ANY(%s)
    """,
    "ad": """
        SELECT DISTINCT g.campaign_row_id AS id FROM direct_ads ad
        JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
        WHERE ad.user_id = %s AND ad.id = ANY(%s)
    """,
}


def campaigns_of(user_id: int, kind: str, ids: list[int]) -> list[int]:
    """Кампании, в которых лежат группы / ключи / объявления — для проекта записи."""
    if not ids:
        return []
    with pool.connection() as conn:
        rows = conn.execute(_CAMPAIGNS_OF[kind], (user_id, list(ids))).fetchall()
    return sorted(int(r["id"]) for r in rows if r["id"] is not None)


def log_action(
    user_id: int,
    action: str,
    payload: dict,
    result: dict,
    status: str,
    *,
    workspace_id: int | None = None,
    title: str | None = None,
    undo: dict | None = None,
    targets: list[str] | None = None,
    undo_of: int | None = None,
) -> int | None:
    """
    undo — {"label": "Вернуть бюджеты", "danger": bool, "steps": [{"op": ..., "args": {...}}]};
    targets — затронутые объекты («campaign:12», «keyword:5»); по умолчанию кампании из payload.
    """
    if _quiet.get():
        return None
    payload = payload or {}
    if workspace_id is None:
        workspace_id = _workspace_for(user_id, payload)
    if targets is None:
        targets = [f"campaign:{i}" for i in sorted(_ids(payload))]
    if _ai_action.get():
        targets = [*targets, ai_action_target(_ai_action.get())]
    if undo is not None and not undo.get("steps"):
        undo = None
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO action_logs (
                user_id, action, payload, result, status, workspace_id, source, title, undo, targets, undo_of
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id,
                action,
                _dump(payload),
                _dump(result),
                status,
                workspace_id,
                _source.get(),
                (title or ACTION_TITLES.get(action) or action)[:300],
                Jsonb(undo) if undo is not None else None,
                sorted(set(targets)),
                undo_of,
            ),
        ).fetchone()
    log_id = int(row["id"])
    logged = _logged.get()
    if logged is not None:
        logged.append(log_id)
    if undo is not None:
        note_undoable(log_id)
    if _source.get() == "user" and status != "error":
        # Правка самим пользователем — действие дня. ИИ и правила — нет: их ход уже засчитан вопросом ИИ.
        from app import activity

        activity.action(user_id)
    return log_id


def list_action_logs(
    user_id: int,
    limit: int = 50,
    *,
    workspace_id: int | None = None,
    source: str | None = None,
    kind: str | None = None,
    before_id: int | None = None,
    campaign_row_id: int | None = None,
) -> list[dict]:
    """campaign_row_id — только записи про эту кампанию, её группы, объявления и фразы."""
    limit = max(1, min(int(limit), 200))
    where = ["l.user_id = %s"]
    params: list = [user_id]
    if workspace_id is not None:
        where.append("l.workspace_id = %s")
        params.append(workspace_id)
    if campaign_row_id:
        rid = int(campaign_row_id)
        where.append(_CAMPAIGN_FILTER)
        params += [rid, user_id, rid, user_id, rid, user_id, rid, _payload_campaign_re(rid), _campaign_re(rid)]
    if source in ("user", "ai", "rule"):
        where.append("l.source = %s")
        params.append(source)
    if kind in ACTION_KINDS:
        where.append("l.action = ANY(%s)")
        params.append(list(ACTION_KINDS[kind]))
    if before_id:
        where.append("l.id < %s")
        params.append(before_id)
    params.append(limit)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT l.id, l.action, l.title, l.source, l.workspace_id, w.name AS workspace_name,
                   l.payload, l.result, l.status, l.created_at, l.undo, l.undone_at, l.undone_by, l.undo_of,
                   l.targets
            FROM action_logs l
            LEFT JOIN workspaces w ON w.id = l.workspace_id
            WHERE {' AND '.join(where)}
            ORDER BY l.id DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
    return describe_logs(user_id, rows)


def describe_logs(user_id: int, rows: list[dict]) -> list[dict]:
    """Строки журнала как в истории: заголовок, «было → стало» словами, кампании, откат."""
    # Кампании всех записей страницы — одним запросом: названия для строк и ссылки на кампанию в сервисе.
    parsed = [(_parse(r["payload"]), _parse(r["result"])) for r in rows]
    ids: list[int] = []
    for r, ((payload, _c1), (result, _c2)) in zip(rows, parsed):
        ids.extend(_row_campaigns(r, payload, result))
    campaigns = _campaign_cards(user_id, sorted(set(ids)))
    return [_log_out(r, campaigns, parsed=p) for r, p in zip(rows, parsed)]


# Записи про кампанию: цель — сама кампания, её группа, объявление или фраза (цели ставит log_action),
# а запрет площадок целей не пишет — его кампании только в ответе ({"campaign_row_id": N, …}).
# Удалённых фраз, объявлений и групп в таблицах уже нет — по целям их запись не найти; кампания есть
# в параметрах (campaign_row_ids), а у отката — в его параметрах (run_undo берёт их у исходной записи).
_CAMPAIGN_FILTER = """(
    l.targets && ARRAY(
        SELECT 'campaign:' || %s::bigint
        UNION ALL
        SELECT 'adgroup:' || g.id FROM direct_adgroups g WHERE g.user_id = %s AND g.campaign_row_id = %s
        UNION ALL
        SELECT 'ad:' || a.id FROM direct_ads a JOIN direct_adgroups g ON g.id = a.adgroup_row_id
        WHERE g.user_id = %s AND g.campaign_row_id = %s
        UNION ALL
        SELECT 'keyword:' || k.id FROM direct_keywords k JOIN direct_adgroups g ON g.id = k.adgroup_row_id
        WHERE g.user_id = %s AND g.campaign_row_id = %s
    )::text[]
    OR l.payload ~ %s
    OR (l.action = 'exclude_sites' AND l.result ~ %s)
)"""


def _campaign_re(rid: int) -> str:
    return f'"campaign_row_id": ?{int(rid)}[^0-9]'


def _payload_campaign_re(rid: int) -> str:
    """Кампания в параметрах записи: «"campaign_row_id": N» или N в списке «"campaign_row_ids": [...]»
    (ключи — как у _id_list)."""
    return f'"campaign_(row_ids?|ids)": ?(\\[([^]]*[^0-9])?)?{int(rid)}[^0-9]'


def _campaign_cards(user_id: int, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.campaign_id, wc.workspace_id
            FROM direct_campaigns c
            LEFT JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
            WHERE c.user_id = %s AND c.id = ANY(%s)
            """,
            (user_id, ids),
        ).fetchall()
    return {
        int(r["id"]): {
            "id": int(r["id"]),
            "name": r["name"] or "",
            "workspace_id": r["workspace_id"],
            "direct_id": r["campaign_id"],
        }
        for r in rows
    }


def _row_campaigns(r: dict, payload, result) -> list[int]:
    """Кампании записи по порядку: из параметров, из targets, из ответа (запрет площадок кладёт их только туда)."""
    ids = _id_list(payload) if isinstance(payload, dict) else []
    for target in r.get("targets") or []:
        if isinstance(target, str) and target.startswith("campaign:"):
            rid = _int(target.split(":", 1)[1])
            if rid is not None and rid not in ids:
                ids.append(rid)
    if isinstance(result, dict):
        for key in ("items", "errors"):
            for item in _items(result.get(key)):
                rid = _int(item.get("campaign_row_id")) if isinstance(item, dict) else None
                if rid is not None and rid not in ids:
                    ids.append(rid)
    return ids


def _log_out(r: dict, campaigns: dict[int, dict] | None = None, *, parsed=None) -> dict:
    undo = r.get("undo") or None
    (payload, payload_cut), (result, result_cut) = parsed or (_parse(r["payload"]), _parse(r["result"]))
    cut = payload_cut or result_cut
    campaigns = campaigns or {}
    cards = [campaigns[i] for i in _row_campaigns(r, payload, result) if i in campaigns]
    ctx = _Ctx(payload, result, undo, {c["id"]: c["name"] for c in cards if c["name"]})
    action, status = r["action"], r["status"]
    problems = _safe(_problems, action, status, ctx, cut)
    if not problems and status in _FAILED:
        problems = [_NO_REASON]
    return {
        "id": r["id"],
        "action": action,
        "title": r["title"] or ACTION_TITLES.get(action) or action,
        "source": r["source"],
        "workspace_id": r["workspace_id"],
        "workspace_name": r.get("workspace_name"),
        "payload": payload,
        "result": result,
        "status": status,
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        # Откатить можно один раз и только то, что хотя бы частично выполнилось.
        "undo": (
            {"label": undo.get("label") or "Откатить", "danger": bool(undo.get("danger"))}
            if undo and status in ("ok", "partial") and not r.get("undone_at")
            else None
        ),
        "undone_at": r["undone_at"].isoformat() if r.get("undone_at") else None,
        "undone_by": r.get("undone_by"),
        "undo_of": r.get("undo_of"),
        # Простой режим: что поменялось словами, что не применилось и почему, ссылки на кампании.
        "changes": _safe(_changes, action, status, ctx),
        "problems": problems,
        "campaigns": cards[:3],
        "campaigns_total": len(cards),
        # Старая запись, обрезанная при сохранении: часть ответа Директа потеряна.
        "cut": cut,
    }


def _safe(fn, *args) -> list[str]:
    """Подпись строки истории не должна ронять всю историю: на странной записи — просто без неё."""
    try:
        return fn(*args)
    except Exception:
        log.warning("journal wording %s failed", fn.__name__, exc_info=True)
        return []


# --- История словами ---------------------------------------------------------------------------
# Простой режим истории: под заголовком 1–3 строки «было → стало», при ошибке — причина полными словами.
# Всё собирается из payload / result / шагов отката — новых колонок не нужно.

_NBSP = "\u00a0"
_FAILED = ("partial", "error", "failed")
_CAMPAIGNS = ("кампания", "кампании", "кампаний")
_GEN_CAMPAIGNS = ("кампании", "кампаний", "кампаний")
_IN_CAMPAIGNS = ("кампании", "кампаниях", "кампаниях")
_GROUPS = ("группа", "группы", "групп")
_GEN_GROUPS = ("группы", "групп", "групп")
_IN_GROUPS = ("группе", "группах", "группах")
_PHRASES = ("фраза", "фразы", "фраз")
_ADS = ("объявление", "объявления", "объявлений")
_REGIONS = ("регион", "региона", "регионов")
_SITELINKS = ("набор быстрых ссылок", "набора быстрых ссылок", "наборов быстрых ссылок")
_QUERIES = ("запрос", "запроса", "запросов")
_STEPS = ("шаг", "шага", "шагов")
_VALUES = ("значение", "значения", "значений")
_EDITS = ("изменение", "изменения", "изменений")
_ERRORS = ("ошибка", "ошибки", "ошибок")

_NO_REASON = "Директ не прислал причину. Попробуйте ещё раз или откройте кампанию в Директе."
_LOST_REASON = "Причина в этой старой записи не сохранилась — откройте кампанию и проверьте её в Директе."
_LOST_DETAILS = "Часть подробностей в этой старой записи не сохранилась."

# Коды ошибок сервиса, если Директ не прислал своего текста.
_CODE_TEXTS = {
    "direct": "Директ не прислал причину",
    "token-expired": "доступ к Директу истёк — подключите аккаунт заново",
    "unauthorized": "доступ к Директу истёк — подключите аккаунт заново",
    "oauth-unavailable": "сервер авторизации Яндекса не ответил — повторите позже",
    "direct-denied": "нет доступа к API Директа",
    "budget": "Директ не принял сумму",
    "limit": "превышен лимит Директа",
    "negatives-limit": "минус-фраз больше 20 000 символов — это лимит Директа",
    "not-found": "не найдено в Директе — возможно, удалено",
    "busy": "в это время шло другое изменение — повторите позже",
    "archived": "кампания в архиве",
    "unsupported": "Директ не поддерживает это для такого типа кампании",
    "units": "закончились баллы API Директа — повторите позже",
    "empty": "нечего было менять",
    "disabled": "аккаунт Директа выключен",
    "strategy": "Директ не принял стратегию",
}
# Слова, с которых причина начинается с заглавной всегда: имена собственные и аббревиатуры.
_PROPER = ("Директ", "Яндекс", "Метрик", "API", "ЮKassa", "Telegram", "ИИ")
_MESSAGE_RE = re.compile(r'"message":\s*"((?:[^"\\]|\\.)+)')


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def _num(n) -> str:
    return f"{int(n):,}".replace(",", _NBSP)


def _n(n: int, forms: tuple[str, str, str]) -> str:
    return f"{_num(n)} {_plural(n, forms)}"


def _int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rub(value: float) -> str:
    whole = abs(value - round(value)) < 0.005
    text = f"{round(value):,}" if whole else f"{value:,.2f}"
    return text.replace(",", _NBSP).replace(".", ",")


def _money(rub, empty: str = "не задан") -> str:
    value = _float(rub)
    return f"{_rub(value)}{_NBSP}₽" if value else empty


def _span(values: list[float], empty: str) -> str:
    """«800–1 200 ₽» — когда у кампаний было по-разному."""
    vals = sorted({round(v, 2) for v in values if v})
    if not vals:
        return empty
    if len(vals) == 1:
        return _money(vals[0])
    return f"{_rub(vals[0])}–{_money(vals[-1])}"


def _pct(value) -> str:
    return f"{_float(value) or 0:+g}{_NBSP}%".replace("-", "−")


def _adj(pct: int) -> str:
    """Корректировка ставки словами — как в срезах статистики."""
    if pct == 0:
        return "не показывать"
    diff = pct - 100
    return "без корректировки" if diff == 0 else f"{diff:+d}{_NBSP}%".replace("-", "−")


def _clip(text, limit: int = 300) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _q(text, limit: int = 60) -> str:
    return f"«{_clip(text, limit)}»"


def _list(values, total: int = 0, limit: int = 3) -> str:
    """«a», «b», «c» и ещё 5."""
    values = [v for v in values if v]
    shown = values[:limit]
    total = max(total, len(values))
    text = ", ".join(_q(v, 40) for v in shown)
    return text + (f" и ещё {_num(total - len(shown))}" if total > len(shown) else "")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _low(text: str) -> str:
    """Причина после двоеточия — со строчной; имена собственные и аббревиатуры не трогаем."""
    if text.startswith(_PROPER) or len(text) < 2 or not (text[0].isupper() and text[1].islower()):
        return text
    return text[0].lower() + text[1:]


def _short_url(url) -> str:
    text = re.sub(r"^https?://(www\.)?", "", str(url or "")).rstrip("/")
    return _clip(text, 50) if text else "не задана"


def _signed(plus: int, minus: int) -> str:
    parts = ([f"+{_num(plus)}"] if plus else []) + ([f"−{_num(minus)}"] if minus else [])
    return ", ".join(parts) or "без изменений"


def _items(value) -> list:
    """Элементы списка без хвоста «… и ещё N» от ужатия."""
    if not isinstance(value, list):
        return []
    return [v for v in value if not (isinstance(v, str) and _MORE_RE.match(v))]


def _size(value) -> int:
    """Сколько было на самом деле: число, длина списка с учётом «… и ещё N», «… 40 шт.»."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = _MORE_RE.match(value)
        return int(m.group(1)) if m else 0
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, list):
        total = 0
        for item in value:
            m = _MORE_RE.match(item) if isinstance(item, str) else None
            total += int(m.group(1)) if m else 1
        return total
    return 0


class _Ctx:
    """Что известно о записи: параметры, ответ Директа, шаги отката и названия кампаний."""

    def __init__(self, payload, result, undo=None, names: dict[int, str] | None = None):
        self.p = payload if isinstance(payload, dict) else {}
        self.r = result if isinstance(result, dict) else {}
        self.raw = result if isinstance(result, str) else ""
        steps = undo.get("steps") if isinstance(undo, dict) else None
        self.steps = [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
        self.names: dict[int, str] = dict(names or {})
        for key in ("items", "errors"):
            for item in _items(self.r.get(key)):
                if isinstance(item, dict) and item.get("name"):
                    rid = _int(item.get("campaign_row_id"))
                    if rid is not None:
                        self.names.setdefault(rid, str(item["name"]))

    def args(self, op: str) -> list[dict]:
        return [s["args"] for s in self.steps if s.get("op") == op and isinstance(s.get("args"), dict)]

    def name(self, rid) -> str | None:
        rid = _int(rid)
        return self.names.get(rid) if rid is not None else None


# --- Что поменялось ----------------------------------------------------------------------------


def _changes(action: str, status: str, c: _Ctx) -> list[str]:
    """До трёх строк «было → стало». У невыполненного действия — пусто: там важна причина."""
    if status in ("error", "failed"):
        return []
    fn = _CHANGE_LINES.get(action)
    lines = [line for line in (fn(c) if fn else []) if line]
    if not lines:
        applied = _int(c.r.get("applied"))
        lines = [f"Применено изменений: {_num(applied)}"] if applied else []
    if status == "partial":
        # Замечания при частичном успехе и так стоят в причинах — не повторяем.
        lines = [line for line in lines if not line.startswith("Замечание:")]
    return [_cap(_clip(line, 240)) for line in _fit(lines)]


def _fit(lines: list[str]) -> list[str]:
    return lines if len(lines) <= 3 else lines[:2] + [f"И ещё {_n(len(lines) - 2, _EDITS)}"]


_STATE_LINES = {
    "pause": ("Показы", "идут", "на паузе"),
    "suspend": ("Показы", "идут", "на паузе"),
    "resume": ("Показы", "на паузе", "идут"),
    "archive": ("Статус", "в работе", "в архиве"),
    "unarchive": ("Статус", "в архиве", "на паузе"),
}


def _state_line(action: str, n: int, forms: tuple[str, str, str]) -> str:
    what, was, now = _STATE_LINES[action]
    return f"{what}: {was} → {now}" + (f", {_n(n, forms)}" if n > 1 else "")


def _ch_campaign_state(action: str):
    def lines(c: _Ctx) -> list[str]:
        n = _int(c.r.get("applied"))
        if n is None:
            n = _size(c.p.get("campaign_ids"))
        if action == "delete":
            return [f"Удалено из Директа: {_n(n, _CAMPAIGNS)}"] if n else []
        return [_state_line(action, n, _CAMPAIGNS)]

    return lines


def _money_diff(label: str, pairs: list[tuple], forms, c: _Ctx | None = None, empty: str = "не задан") -> list[str]:
    """pairs — (id, было ₽, стало ₽). Одинаково у всех — одной строкой, иначе по кампаниям или диапазоном."""
    olds = {round(o, 2) for _r, o, _new in pairs}
    news = {round(new, 2) for _r, _o, new in pairs}
    tail = f", {_n(len(pairs), forms)}" if len(pairs) > 1 else ""
    if len(olds) == 1 and len(news) == 1:
        return [f"{label}: {_money(pairs[0][1], empty)} → {_money(pairs[0][2], empty)}{tail}"]
    if c is not None and len(pairs) <= 3 and all(c.name(r) for r, _o, _new in pairs):
        return [f"{label} {_q(c.name(r), 40)}: {_money(o, empty)} → {_money(new, empty)}" for r, o, new in pairs]
    before = _span([o for _r, o, _new in pairs], empty)
    after = _span([new for _r, _o, new in pairs], empty)
    return [f"{label}: {before} → {after}{tail}"]


def _ch_set_budget(c: _Ctx) -> list[str]:
    new = _float(c.p.get("daily_budget_rub"))
    pct = _float(c.p.get("budget_change_pct"))
    pairs = []
    for args in c.args("campaign_budgets"):
        budgets = args.get("budgets")
        for rid, kopecks in (budgets.items() if isinstance(budgets, dict) else []):
            old = (_float(kopecks) or 0) / 100
            if new is not None:
                now = new
            elif pct is not None and old:
                now = round(old * 100 * (1 + pct / 100)) / 100
            else:
                continue
            pairs.append((_int(rid), old, now))
    if pairs:
        return _money_diff("Дневной бюджет", pairs, _CAMPAIGNS, c, "без ограничения")
    if new is not None:
        return [f"Дневной бюджет: {_money(new)}"]
    return [f"Дневной бюджет: {_pct(pct)}"] if pct is not None else []


def _ch_budget(c: _Ctx) -> list[str]:
    items = [i for i in _items(c.r.get("items")) if isinstance(i, dict)]
    changed = [i for i in items if i.get("changed") and (i.get("before") or i.get("after"))]
    lines: list[str] = []
    if changed:
        pairs = {(str(i.get("before") or "—"), str(i.get("after") or "—")) for i in changed}
        if len(pairs) == 1:
            before, after = next(iter(pairs))
            tail = f", {_n(len(changed), _CAMPAIGNS)}" if len(changed) > 1 else ""
            lines.append(f"{before} → {after}{tail}")
        else:
            shown = changed if len(changed) <= 3 else changed[:2]
            for i in shown:
                name = i.get("name") or c.name(i.get("campaign_row_id")) or "кампания"
                lines.append(f"{_q(name, 40)}: {i.get('before') or '—'} → {i.get('after') or '—'}")
            if len(changed) > len(shown):
                lines.append(f"И ещё {_n(len(changed) - len(shown), _CAMPAIGNS)}")
    else:
        parts = []
        if _float(c.p.get("weekly_budget_rub")):
            parts.append(f"недельный бюджет {_money(c.p['weekly_budget_rub'])}")
        if _float(c.p.get("daily_budget_rub")):
            parts.append(f"дневной бюджет {_money(c.p['daily_budget_rub'])}")
        if _float(c.p.get("budget_change_pct")):
            parts.append(f"бюджет {_pct(c.p['budget_change_pct'])}")
        if _float(c.p.get("target_change_pct")):
            parts.append(f"цена цели {_pct(c.p['target_change_pct'])}")
        if parts and _int(c.r.get("applied")):
            lines.append(", ".join(parts))
    unchanged = _int(c.r.get("unchanged"))
    if unchanged:
        lines.append(f"Без изменений: {_n(unchanged, _CAMPAIGNS)}")
    return lines[:3]


def _adj_label(dimension: str, key: str) -> str:
    try:
        from app.slices import AGE_LABELS, DEVICE_LABELS, GENDER_LABELS
    except Exception:
        AGE_LABELS = DEVICE_LABELS = GENDER_LABELS = {}
    if dimension == "device":
        return DEVICE_LABELS.get(key, key)
    if dimension == "demography":
        gender, _sep, age = key.partition("|")
        parts = [GENDER_LABELS.get(gender, ""), AGE_LABELS.get(age, "")]
        if parts[0]:
            return " ".join(p for p in parts if p)
        return f"Возраст {parts[1]}" if parts[1] else key
    if dimension == "hour":
        hour = _int(key)
        return f"{hour}:00–{hour + 1}:00" if hour is not None else key
    return key


def _ch_bid_adjust(c: _Ctx) -> list[str]:
    dimension = str(c.p.get("dimension") or "")
    values = c.p.get("values")
    if not isinstance(values, dict):
        return []
    new = {str(k): _int(v) for k, v in values.items() if _int(v) is not None}
    olds: dict[str, set[int]] = {}
    for args in c.args("bid_modifiers"):
        prev = args.get("values")
        for key, value in (prev.items() if isinstance(prev, dict) else []):
            if _int(value) is not None:
                olds.setdefault(str(key), set()).add(_int(value))
    if dimension == "hour":
        for args in c.args("campaign_settings"):
            changes = args.get("changes")
            if not isinstance(changes, dict) or "schedule" not in changes:
                continue
            grid = changes.get("schedule")
            for key in new:
                hour = _int(key)
                if hour is None or not 0 <= hour <= 23:
                    continue
                if not isinstance(grid, list):
                    olds.setdefault(key, set()).add(100)  # расписания не было — везде 100 %
                    continue
                # Выключенные часы корректировка не трогает — по ним «было» не считаем.
                column = {_int(row[hour]) for row in grid if isinstance(row, list) and len(row) > hour and row[hour]}
                olds.setdefault(key, set()).update(v for v in column if v is not None)
    by_value = dimension == "hour" or bool(c.steps)
    lines: list[str] = []
    regions: dict[tuple[str, str], int] = {}
    for key, pct in new.items():
        old = olds.get(key)
        if by_value and not old:
            continue  # по шагам отката видно: здесь ничего не поменялось
        was = (_adj(next(iter(old))) if len(old) == 1 else "по-разному") if old else ""
        now = _adj(pct)
        if was == now:
            continue
        if dimension == "region":
            regions[(was, now)] = regions.get((was, now), 0) + 1
            continue
        label = _adj_label(dimension, key)
        lines.append(f"{label}: {was} → {now}" if was else f"{label}: {now}")
    for (was, now), count in regions.items():
        label = _cap(_n(count, _REGIONS))
        lines.append(f"{label}: {was} → {now}" if was else f"{label}: {now}")
    if len(lines) > 3:
        lines = lines[:2] + [f"И ещё {_n(len(lines) - 2, _VALUES)}"]
    return lines


def _ch_exclude_sites(c: _Ctx) -> list[str]:
    changed = [i for i in _items(c.r.get("items")) if isinstance(i, dict) and (_int(i.get("added")) or 0) > 0]
    if not changed:
        return []
    if len(changed) == 1:
        item = changed[0]
        added = _int(item["added"])
        rid = _int(item.get("campaign_row_id"))
        for args in c.args("campaign_settings"):
            prev = (args.get("changes") or {}).get("excluded_sites")
            if _int(args.get("campaign_row_id")) == rid and isinstance(prev, list):
                return [f"Запрещённых площадок: {_num(len(prev))} → {_num(len(prev) + added)}"]
        return [f"Запрещённые площадки: +{_num(added)}"]
    sites = _int(c.r.get("sites")) or sum(_int(i["added"]) for i in changed)
    return [f"Запрещённые площадки: +{_num(sites)} в {_n(len(changed), _IN_CAMPAIGNS)}"]


def _neg_lines(c: _Ctx, *, where: bool = True, phrases: bool = True) -> list[str]:
    """Минус-фразы по шагу отката: в нём ровно то, что добавили (remove) и убрали (add)."""
    added: dict[str, None] = {}
    removed: dict[str, None] = {}
    campaigns: set[str] = set()
    groups: set[str] = set()
    for args in c.args("negatives"):
        for kind, bucket in (("campaigns", campaigns), ("adgroups", groups)):
            part = args.get(kind)
            for rid, change in (part.items() if isinstance(part, dict) else []):
                if not isinstance(change, dict):
                    continue
                plus = [p for p in _items(change.get("remove")) if isinstance(p, str)]
                minus = [p for p in _items(change.get("add")) if isinstance(p, str)]
                if plus or minus:
                    bucket.add(str(rid))
                added.update(dict.fromkeys(plus))
                removed.update(dict.fromkeys(minus))
    if not added and not removed:
        plus, minus = _int(c.r.get("added")) or 0, _int(c.r.get("removed")) or 0
        return [f"Минус-фразы: {_signed(plus, minus)}"] if plus or minus else []
    place = ""
    if where:
        parts = []
        if campaigns:
            name = c.name(next(iter(campaigns))) if len(campaigns) == 1 else None
            if name:
                parts.append(f"кампании {_q(name, 40)}")
            else:
                parts.append("кампании" if len(campaigns) == 1 else _n(len(campaigns), _IN_CAMPAIGNS))
        if groups:
            parts.append("группе" if len(groups) == 1 else _n(len(groups), _IN_GROUPS))
        place = " в " + " и ".join(parts) if parts else ""
    lines = [f"Минус-фразы: {_signed(len(added), len(removed))}{place}"]
    if phrases and added:
        lines.append(f"Добавлены: {_list(list(added))}")
    if phrases and removed:
        lines.append(f"Убраны: {_list(list(removed))}")
    return lines


def _ch_negatives(c: _Ctx) -> list[str]:
    return _neg_lines(c)


def _ch_queries(c: _Ctx) -> list[str]:
    negatives = _neg_lines(c)
    if not negatives and (_int(c.r.get("negative")) or 0) > 0:
        negatives = [f"Минус-фразы: +{_num(c.r['negative'])}"]
    other = []
    keywords = _int(c.r.get("keywords"))
    if keywords is None and c.p.get("action") == "add_keyword":
        keywords = _int(c.r.get("applied"))
    if keywords:
        other.append(f"Новые ключевые фразы: {_num(keywords)}")
    target = _int(c.r.get("target"))
    if target:
        other.append(f"Отмечено целевыми: {_n(target, _QUERIES)}")
    # Сами минус-фразы — только если больше нечего сказать: счётчики важнее.
    return negatives[:1] + other if other else negatives[:2]


def _ch_negative_list(c: _Ctx) -> list[str]:
    updated = _int(c.r.get("updated")) or 0
    added, removed = _int(c.r.get("added")) or 0, _int(c.r.get("removed")) or 0
    if added or removed:
        return [f"Минус-фразы: {_signed(added, removed)}" + (f" в {_n(updated, _IN_CAMPAIGNS)}" if updated else "")]
    return [f"Обновлено: {_n(updated, _CAMPAIGNS)}"] if updated else []


def _ch_keywords(c: _Ctx) -> list[str]:
    p, r = c.p, c.r
    if "phrases" in p:  # добавление фраз в группу
        phrases = [x for x in _items(p.get("phrases")) if isinstance(x, str)]
        created = _int(r.get("created"))
        errors = _size(r.get("errors"))
        lines = []
        if errors and created is not None:
            lines.append(f"Новые фразы: {_num(created)} из {_num(created + errors)}")
        elif created or phrases:
            lines.append(f"Новые фразы: {_list(phrases, created or 0)}")
        if _float(p.get("bid_rub")):
            lines.append(f"Ставка новых фраз: {_money(p['bid_rub'])}")
        return lines
    action = p.get("action")
    if action:
        n = _int(r.get("applied"))
        if n is None:
            n = _size(p.get("keyword_row_ids"))
        if action == "set_bid":
            new = _float(p.get("bid_rub"))
            pairs = []
            for args in c.args("keyword_bids"):
                bids = args.get("bids")
                for kid, micros in (bids.items() if isinstance(bids, dict) and new else []):
                    if _float(micros) is not None:
                        pairs.append((_int(kid), _float(micros) / 1_000_000, new))
            if pairs:
                return _money_diff("Ставка", pairs, _PHRASES)
            return [f"Ставка: {_money(new)}" + (f", {_n(n, _PHRASES)}" if n > 1 else "")] if new else []
        if action == "delete":
            return [f"Удалено: {_n(n, _PHRASES)}"] if n else []
        return [_state_line(action, n, _PHRASES)] if action in _STATE_LINES else []
    lines = []
    for args in c.args("keyword_update"):
        if "keyword" in args:
            lines.append(f"Текст: {_q(args['keyword'])} → {_q(r.get('keyword'))}")
        if "bid_rub" in args:
            new = (_float(r.get("bid_micros")) or 0) / 1_000_000
            lines.append(f"Ставка: {_money(args['bid_rub'])} → {_money(new)}")
    return lines


_EXTRAS = (
    ("sitelinks", "быстрые ссылки"),
    ("sitelink_set_id", "быстрые ссылки"),
    ("callouts", "уточнения"),
    ("callout_ids", "уточнения"),
    ("images", "картинки"),
    ("image_hashes", "картинки"),
)


def _extras_text(extras) -> str:
    if not isinstance(extras, dict):
        return ""
    names: list[str] = []
    for key, label in _EXTRAS:
        if key in extras and label not in names:
            names.append(label)
    return ", ".join(names)


def _list_field(one: str, many: str, old, new) -> list[str]:
    """Заголовки и тексты объявления: что именно поменялось в списке вариантов."""
    old = [str(x) for x in _items(old)]
    new = [str(x) for x in _items(new)]
    diff = [(i, a, b) for i, (a, b) in enumerate(zip_longest(old, new)) if a != b]
    if not diff:
        return []
    if len(old) <= 1 and len(new) <= 1:
        return [f"{one}: {_q(old[0]) if old else 'не было'} → {_q(new[0]) if new else 'убран'}"]
    if len(diff) == 1:
        i, a, b = diff[0]
        if a is None:
            return [f"{one} {i + 1} добавлен: {_q(b)}"]
        if b is None:
            return [f"{one} {i + 1} убран: {_q(a)}"]
        return [f"{one} {i + 1}: {_q(a)} → {_q(b)}"]
    return [f"{many}: изменено {_num(len(diff))} из {_num(max(len(old), len(new)))}"]


def _ch_ads(c: _Ctx) -> list[str]:
    p, r = c.p, c.r
    if "source_ad_row_ids" in p:  # варианты для A/B-теста
        lines = [f"Новые варианты для A/B-теста: {_num(_size(r.get('created')))}"]
        moderated = _size(r.get("moderated"))
        if moderated:
            lines.append(f"Отправлено на модерацию: {_n(moderated, _ADS)}")
        return lines
    action = p.get("action")
    if action:
        n = _int(r.get("applied"))
        if n is None:
            n = _size(p.get("ad_row_ids"))
        if action == "delete":
            return [f"Удалено: {_n(n, _ADS)}"] if n else []
        return [_state_line(action, n, _ADS)] if action in _STATE_LINES else []
    changes = p.get("changes")
    if not isinstance(changes, dict):
        if "adgroup_row_id" not in p:
            return []
        lines = []  # новое объявление: заголовок уже в названии записи
        texts = _items(p.get("texts"))
        if texts:
            lines.append(f"Текст: {_q(texts[0], 80)}")
        if p.get("href"):
            lines.append(f"Ссылка: {_short_url(p['href'])}")
        if _extras_text(p.get("extras")):
            lines.append(f"Расширения: {_extras_text(p.get('extras'))}")
        return lines
    if set(changes) == {"extras"}:
        n = _int(r.get("applied")) or _size(p.get("ad_row_ids"))
        text = _extras_text(changes.get("extras")) or "изменены"
        return [f"Расширения: {text}" + (f", {_n(n, _ADS)}" if n > 1 else "")]
    back: dict = {}
    for args in c.args("ad_update"):
        back.update(args)
    lines = []
    for key in ("titles", "texts", "title2", "href"):
        if key not in back or key not in changes:
            continue  # в откат попадает только то, что правда поменялось
        old, new = back[key], changes[key]
        if key == "titles":
            lines += _list_field("Заголовок", "Заголовки", old, new)
        elif key == "texts":
            lines += _list_field("Текст", "Тексты", old, new)
        elif key == "title2":
            lines.append(f"Доп. заголовок: {_q(old) if old else 'не было'} → {_q(new) if new else 'убран'}")
        else:
            lines.append(f"Ссылка: {_short_url(old)} → {_short_url(new)}")
    if "extras" in changes:
        lines.append(f"Расширения: {_extras_text(changes['extras']) or 'изменены'}")
    return lines


def _ch_adgroups(c: _Ctx) -> list[str]:
    p, r = c.p, c.r
    if "groups" in r and "updated" in r:  # регионы показа
        groups, updated = _int(r.get("groups")) or 0, _int(r.get("updated")) or 0
        if groups and updated < groups:
            return [f"Регионы обновлены: {_num(updated)} из {_n(groups, _GEN_GROUPS)}"]
        return [f"Регионы обновлены в {_n(updated, _IN_GROUPS)}"] if updated else []
    if "adgroup_row_ids" in p:
        n = _int(r.get("applied"))
        n = _size(p.get("adgroup_row_ids")) if n is None else n
        return [f"Удалено: {_n(n, _GROUPS)}"] if n else []
    if "adgroup_row_id" not in p:  # новая группа
        if not p.get("name"):
            return []
        regions = _size(p.get("region_ids"))
        return [f"Новая группа {_q(p['name'])}" + (f", {_n(regions, _REGIONS)} показа" if regions else "")]
    lines = []
    for args in c.args("adgroup_update"):
        if "name" in args and p.get("name"):
            lines.append(f"Название группы: {_q(args['name'])} → {_q(p['name'])}")
        if "tracking_params" in args:
            lines.append("Метки ссылок группы изменены")
    lines += _neg_lines(c, where=False)[:2]
    return lines


_NOTHING = object()  # прежнее значение неизвестно


def _date(value) -> str:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{match.group(3)}.{match.group(2)}.{match.group(1)}" if match else "не задана"


def _phrase_list(value) -> list[str]:
    if isinstance(value, str):
        return [x.strip() for x in value.splitlines() if x.strip()]
    return [str(x) for x in _items(value)]


def _strategy_label(strategy) -> str:
    if not isinstance(strategy, dict) or not (strategy.get("search") or strategy.get("network")):
        return ""
    try:
        from app.actions import strategy_label

        return strategy_label(strategy)
    except Exception:
        return ""


def _strategy_lines(old, new) -> list[str]:
    if not isinstance(new, dict):
        return []
    label = _strategy_label(new)
    if old is _NOTHING or not isinstance(old, dict):
        return [f"Стратегия: {label}"] if label else []
    before = _strategy_label(old)
    if label and before and label != before:
        return [f"Стратегия: {before} → {label}"]
    try:
        from app.actions import STRATEGY_PARAMS
    except Exception:
        STRATEGY_PARAMS = {}
    lines = []
    for key, title in STRATEGY_PARAMS.items():
        if key not in new or new.get(key) == old.get(key):
            continue
        if key == "goal_id":
            lines.append("Цель стратегии изменена")
        else:
            name = title.replace(", ₽", "")
            lines.append(f"{name}: {_money(old.get(key))} → {_money(new.get(key))}")
    return lines


def _setting_lines(key: str, old, new) -> list[str] | None:
    """Строки про одну настройку; [] — не поменялась; None — словами не описываем."""
    known = old is not _NOTHING
    if known and old == new:
        return []
    if key == "name":
        return [f"Название: {_q(old)} → {_q(new)}" if known else f"Название: {_q(new)}"]
    if key == "daily_budget_rub":
        now = _money(new, "без ограничения")
        return [f"Дневной бюджет: {_money(old, 'без ограничения')} → {now}" if known else f"Дневной бюджет: {now}"]
    if key in ("start_date", "end_date"):
        label = "Дата начала" if key == "start_date" else "Дата окончания"
        return [f"{label}: {_date(old)} → {_date(new)}" if known else f"{label}: {_date(new)}"]
    if key in ("negative_keywords", "excluded_sites"):
        label = "Минус-фразы кампании" if key == "negative_keywords" else "Запрещённые площадки"
        now = _phrase_list(new)
        if not known or old is None:
            return [f"{label}: {_num(len(now))} в списке"]
        was = _phrase_list(old)
        return [f"{label}: {_signed(len(set(now) - set(was)), len(set(was) - set(now)))}"]
    if key == "strategy":
        return _strategy_lines(old, new)
    if key == "tracking_params":
        return ["Метки ссылок кампании убраны" if not new else "Метки ссылок кампании изменены"]
    if key == "schedule":
        return ["Расписание показов изменено"]
    if key == "consider_working_weekends":
        return [f"Рабочие выходные: {'учитываются' if new else 'не учитываются'}"]
    if key == "priority_goals":
        return ["Ключевые цели изменены"]
    return None


def _ch_settings(c: _Ctx) -> list[str]:
    changes = c.p.get("changes")
    if not isinstance(changes, dict):
        return []
    previous: dict = {}
    for args in c.args("campaign_settings"):
        if isinstance(args.get("changes"), dict):
            previous.update(args["changes"])
    try:
        from app.actions import SETTINGS_LABELS
    except Exception:
        SETTINGS_LABELS = {}
    lines: list[str] = []
    other: list[str] = []
    for key, new in changes.items():
        if key == "consider_working_weekends" and "schedule" in changes:
            continue  # часть расписания
        got = _setting_lines(key, previous.get(key, _NOTHING), new)
        if got is None:
            other.append(SETTINGS_LABELS.get(key, key))
        else:
            lines += got
    if other:
        lines.append("Изменено: " + ", ".join(other))
    return lines


def _ch_update(c: _Ctx) -> list[str]:
    lines = []
    for args in c.args("campaign_update"):
        if "name" in args and c.p.get("name"):
            lines.append(f"Название: {_q(args['name'])} → {_q(c.p['name'])}")
    new = _float(c.p.get("daily_budget_rub"))
    if new:
        olds = [
            (_float(k) or 0) / 100
            for args in c.args("campaign_budgets")
            for k in (args.get("budgets") or {}).values()
        ]
        old = f"{_money(olds[0], 'без ограничения')} → " if olds else ""
        lines.append(f"Дневной бюджет: {old}{_money(new)}")
    lines += _neg_lines(c, where=False, phrases=False)[:1]
    return lines


def _ch_create(c: _Ctx) -> list[str]:
    groups = [g for g in _items(c.r.get("groups")) if isinstance(g, dict)]
    keywords = sum(_size(g.get("keywords")) for g in groups)
    ads = sum(1 for g in groups if g.get("ad"))
    parts = []
    if groups:
        parts.append(_n(len(groups), _GROUPS))
    if keywords:
        parts.append(_n(keywords, _PHRASES))
    if ads:
        parts.append(_n(ads, _ADS))
    lines = []
    if parts:
        lines.append("Черновик в Директе: " + ", ".join(parts))
    elif c.r.get("campaign_id"):
        lines.append("Черновик кампании в Директе")
    label = _strategy_label(c.p.get("strategy"))
    if label:
        lines.append(f"Стратегия: {label}")
    lines += _warning_line(c)
    return lines


def _warning_line(c: _Ctx) -> list[str]:
    warnings = [w for w in _items(c.r.get("warnings")) if isinstance(w, str) and w.strip()]
    if not warnings:
        return []
    more = f" (и ещё {_num(len(warnings) - 1)})" if len(warnings) > 1 else ""
    return [f"Замечание: {_low(_clip(warnings[0], 160))}{more}"]


def _ch_duplicate(c: _Ctx) -> list[str]:
    parts = []
    for key, forms in (("groups", _GROUPS), ("keywords", _PHRASES), ("ads", _ADS)):
        n = _size(c.r.get(key))
        if n:
            parts.append(_n(n, forms))
    lines = ["Скопировано: " + ", ".join(parts)] if parts else []
    return lines + _warning_line(c)


def _ch_moderate(c: _Ctx) -> list[str]:
    sent = _int(c.r.get("sent")) or 0
    return [f"Отправлено на модерацию: {_n(sent, _ADS)}"] if sent else []


def _ch_links(c: _Ctx) -> list[str]:
    lines = []
    params = c.p.get("params") if isinstance(c.p.get("params"), dict) else {}
    own: dict = {}
    for args in c.args("campaign_link_params"):
        if isinstance(args.get("own"), dict):
            own.update(args["own"])
    for key, value in params.items():
        now = value or "по шаблону сайта"
        lines.append(f"Метка {key}: {own.get(key) or 'по шаблону сайта'} → {now}" if c.steps else f"Метка {key}: {now}")
    fixed = []
    tracking = c.r.get("tracking_fixed")
    if tracking is True:
        fixed.append("параметры кампании")
    elif (_int(tracking) or 0) > 0:
        fixed.append(f"параметры {_n(_int(tracking), _GEN_CAMPAIGNS)}")
    for key, forms in (("groups_fixed", _GROUPS), ("ads_fixed", _ADS), ("sitelinks_fixed", _SITELINKS)):
        n = _int(c.r.get(key)) or 0
        if n:
            fixed.append(_n(n, forms))
    if fixed:
        lines.append("Исправлены ссылки: " + ", ".join(fixed))
    return lines


# Шаги отката пишутся повелительно («Вернуть …») — в истории это уже сделано.
_PAST = (
    ("Вернуть", "Вернули"),
    ("Удалить", "Удалили"),
    ("Запустить", "Запустили"),
    ("Остановить", "Остановили"),
    ("Архивировать", "Архивировали"),
    ("Добавить", "Добавили"),
    ("Убрать", "Убрали"),
)


def _past(text: str) -> str:
    for now, done in _PAST:
        if text.startswith(now + " "):
            return done + text[len(now):]
    if text.startswith("Минус-фразы"):
        return text.replace(": убрать ", ": убрали ").replace(": вернуть ", ": вернули ").replace("; вернуть ", "; вернули ")
    return text


def _ch_undo(c: _Ctx) -> list[str]:
    steps = [s for s in _items(c.r.get("steps")) if isinstance(s, dict) and s.get("status") == "ok" and s.get("text")]
    lines = [_clip(_past(str(s["text"])), 160) for s in steps]
    return lines if len(lines) <= 3 else lines[:2] + [f"И ещё {_n(len(lines) - 2, _STEPS)}"]


_CHANGE_LINES = {
    "pause": _ch_campaign_state("pause"),
    "resume": _ch_campaign_state("resume"),
    "archive": _ch_campaign_state("archive"),
    "unarchive": _ch_campaign_state("unarchive"),
    "delete": _ch_campaign_state("delete"),
    "set_budget": _ch_set_budget,
    "budget": _ch_budget,
    "bid_adjust": _ch_bid_adjust,
    "exclude_sites": _ch_exclude_sites,
    "negatives": _ch_negatives,
    "queries": _ch_queries,
    "negative_list_apply": _ch_negative_list,
    "keywords": _ch_keywords,
    "ads": _ch_ads,
    "adgroups": _ch_adgroups,
    "settings": _ch_settings,
    "update": _ch_update,
    "create": _ch_create,
    "duplicate": _ch_duplicate,
    "moderate": _ch_moderate,
    "links": _ch_links,
    "undo": _ch_undo,
}


# --- Что не применилось ------------------------------------------------------------------------


def _reason(item) -> str:
    """Причина одной ошибки: текст Директа, иначе расшифровка кода; пусто — причины нет."""
    if isinstance(item, str):
        text = item.strip()
        return _CODE_TEXTS.get(text, text)
    if not isinstance(item, dict):
        return ""
    message = item.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    for key in ("error", "code", "reason"):
        code = item.get(key)
        if isinstance(code, str) and code.strip():
            code = code.strip()
            return code if " " in code else _CODE_TEXTS.get(code, "")
    return ""


def _who(item, c: _Ctx) -> str:
    """Чья ошибка: кампания по названию, иначе аккаунт."""
    if not isinstance(item, dict):
        return ""
    for key in ("name", "campaign_name", "title"):
        if isinstance(item.get(key), str) and item[key].strip():
            return _q(item[key], 40)
    name = c.name(item.get("campaign_row_id"))
    if not name and item.get("kind") == "campaigns":
        name = c.name(item.get("id"))
    if name:
        return _q(name, 40)
    if item.get("client_login"):
        return f"аккаунт {item['client_login']}"
    return ""


def _unescape(text: str) -> str:
    try:
        return json.loads(f'"{text}"')
    except ValueError:
        return text


def _failures(action: str, c: _Ctx) -> list[tuple[str, str]]:
    """(чья, причина) по всем ошибкам записи."""
    out: list[tuple[str, str]] = []
    errors = c.r.get("errors")
    if isinstance(errors, dict):
        errors = [errors]
    for item in _items(errors):
        out.append((_who(item, c), _reason(item)))
    if not any(reason for _who_, reason in out):
        top = _reason({"message": c.r.get("message"), "error": c.r.get("error")})
        if top:
            out.append(("", top))
    if not out and action in ("create", "duplicate"):
        out += [("", w) for w in _items(c.r.get("warnings")) if isinstance(w, str) and w.strip()]
    if not out and c.raw:
        # Запись не разобрать даже починкой — достаём тексты ошибок прямо из строки.
        out += [("", _unescape(m.group(1))) for m in _MESSAGE_RE.finditer(c.raw)]
    return out


def _counts(action: str, c: _Ctx) -> tuple[int, int] | None:
    """(не применилось, всего) — для «2 из 10 не применились»; None — не посчитать честно."""
    p, r = c.p, c.r
    errors = r.get("errors")
    n_err = _size(errors) if isinstance(errors, (list, int)) else 0
    if action in ("pause", "resume", "archive", "unarchive", "delete", "set_budget", "budget", "bid_adjust"):
        return n_err, _size(p.get("campaign_ids"))
    if action == "exclude_sites":
        return n_err, _size(r.get("items")) + n_err
    if action == "negative_list_apply":
        if not all(isinstance(e, dict) and e.get("campaign_row_id") for e in _items(errors)):
            return None  # ошибка на весь аккаунт — сколько кампаний в нём, не знаем
        return n_err, (_int(r.get("updated")) or 0) + n_err
    if action == "moderate":
        drafts = _int(r.get("drafts")) or 0
        return drafts - (_int(r.get("sent")) or 0), drafts
    if action == "adgroups" and "groups" in r:
        groups = _int(r.get("groups")) or 0
        return groups - (_int(r.get("updated")) or 0), groups
    if action == "keywords" and "phrases" in p:
        return n_err, (_int(r.get("created")) or 0) + n_err
    if action in ("keywords", "ads") and p.get("action"):
        total = _size(p.get("keyword_row_ids") if action == "keywords" else p.get("ad_row_ids"))
        return total - (_int(r.get("applied")) or 0), total
    if action == "queries":
        done = sum(_size(r.get(k)) for k in ("negative", "target", "applied", "skipped"))
        return n_err, done + n_err
    return None


def _undo_problems(c: _Ctx) -> list[str]:
    """Откат: какие шаги не прошли и почему — шаг своими словами, затем причина."""
    steps = [s for s in _items(c.r.get("steps")) if isinstance(s, dict)]
    failed = [s for s in steps if s.get("status") == "error"]
    if not failed:
        return []
    lines = [f"Не выполнено шагов: {_num(len(failed))} из {_num(len(steps))}"] if len(steps) > 1 else []
    for step in failed[:5]:
        reason = _reason(step) or _CODE_TEXTS["direct"]
        text = _clip(step.get("text"), 120)
        lines.append(f"{_cap(text)} — {_low(reason)}" if text else _cap(reason))
    if len(failed) > 5:
        lines.append(f"И ещё {_n(len(failed) - 5, _STEPS)}")
    return lines


def _join_who(whos: list[str], limit: int = 3) -> str:
    shown = whos[:limit]
    return ", ".join(shown) + (f" и ещё {_num(len(whos) - len(shown))}" if len(whos) > len(shown) else "")


def _problems(action: str, status: str, c: _Ctx, cut: bool = False) -> list[str]:
    """Что не применилось и почему — словами, без сырого JSON."""
    if status not in _FAILED:
        return []
    if action == "undo":
        lines = _undo_problems(c)
        if lines:
            return lines
    head = ""
    # В обрезанной старой записи часть ошибок потеряна — «сколько из скольких» уже не посчитать.
    counts = None if cut else _counts(action, c)
    if counts:
        failed, total = counts
        if total > 1 and 0 < failed <= total:
            verb = "не применилось" if failed % 10 == 1 and failed % 100 != 11 else "не применились"
            head = f"{_num(failed)} из {_num(total)} {verb}"
    by_reason: dict[str, list[str]] = {}
    for who, reason in _failures(action, c):
        if not reason:
            continue
        whos = by_reason.setdefault(_clip(reason, 300), [])
        if who and who not in whos:
            whos.append(who)
    generic = _CODE_TEXTS["direct"]
    if list(by_reason) == [generic] and not by_reason[generic]:
        by_reason = {}  # только «Директ не прислал причину» — скажем это полностью, с советом
    if not by_reason:
        tail = _LOST_REASON if cut else _NO_REASON
        return [f"{head}. {tail}" if head else tail]
    lines = []
    if len(by_reason) == 1:
        reason, whos = next(iter(by_reason.items()))
        if head:
            lines.append(f"{head}: {_low(reason)}" + (f" ({_join_who(whos)})" if whos else ""))
        elif len(whos) == 1:
            lines.append(_cap(f"{whos[0]}: {_low(reason)}"))
        else:
            lines.append(_cap(reason) + (f" ({_join_who(whos)})" if whos else ""))
    else:
        if head:
            lines.append(f"{head}:")
        reasons = list(by_reason.items())
        for reason, whos in reasons[:6]:
            lines.append(_cap(reason) + (f" — {_join_who(whos)}" if whos else ""))
        if len(reasons) > 6:
            lines.append(f"И ещё {_n(len(reasons) - 6, _ERRORS)}")
    if cut:
        lines.append(_LOST_DETAILS)
    return lines
