"""Скрытые проблемы: «⋯» у сигнала в «Где горит» и в карточках «Сервис заметил».

Три способа скрыть (problem_snoozes.mode):
- days     — одну проблему на 7 дней;
- intended — «так задумано»: одну проблему, пока она не станет хуже;
- type     — весь тип сигнала для клиента (папки проекта) или, если папки нет, для проекта.

Проблема узнаётся по устойчивому ключу problem_key(): тип + объект (кампания, кабинет, логин, клиент
или проект). Скрытая проблема не попадает ни в списки, ни в счётчики строк, ни в утренний дайджест,
ни в колокольчик и Telegram — фильтр стоит в build_overview, а срочные уведомления сверяются отсюда же.

«Так задумано» и «на 7 дней» сами снимаются, когда проблема стала хуже снимка, сделанного при скрытии:
выросла серьёзность, деньги на счёте кончились, дней на балансе стало вдвое меньше, появилась новая
причина отказа (или отклонённых объявлений стало больше). Скрытие типа живёт, пока его не вернут, но
прячет только то, что не серьёзнее скрытого (снимок type_snapshot): «на модерации» не спрячет отказ
модерации, «денег на 4 дня» — кончившиеся деньги. Такая проблема видна, само скрытие типа остаётся.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app.clock import MSK
from app.db import pool

MODES = ("days", "intended", "type")
DAYS_HIDE = 7
# «Так задумано» без самой проблемы дольше трёх дней — проблема ушла, скрытие больше не нужно.
INTENDED_FORGET_DAYS = 3
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
# Скрытие типа без снимка серьёзности (старые записи) — как скрытое «внимание»: критичное всё равно видно.
TYPE_DEFAULT_SEVERITY = "warning"

# Подписи типов для строки «Скрыто»: «Все сигналы «Деньги на счёте»».
KIND_LABELS = {
    "balance": "Деньги на счёте",
    "budget": "Бюджет месяца",
    "kpi": "Цель по цене заявки",
    "counter": "Счётчик Метрики",
    "stats_error": "Статистика не загружается",
    "stats_stale": "Статистика давно не обновлялась",
    "account_sync": "Кампании кабинета не обновляются",
    "token": "Доступ к Яндексу",
    "connection_sync": "Ошибка синхронизации логина",
    "moderation": "Модерация кампании",
    "stopped": "Кампания перестала показываться",
    "ads_rejected": "Отклонённые объявления",
    "spend_spike": "Расход вырос",
    "spend_drop": "Расход упал",
    "cpa_spike": "Цена заявки выросла",
    "no_conversions": "Расход без заявок",
    "setup": "Проект не настроен",
}


class SnoozeError(Exception):
    pass


# --- Чистые правила (покрыты юнит-тестами) -------------------------------------------------------------


def _ws_ids(p: dict) -> list:
    return list(p.get("workspace_ids") or [p.get("workspace_id")])


def problem_key(p: dict) -> str:
    """Устойчивый ключ проблемы: тип + самый узкий объект. Заголовок и цифры в ключ не входят."""
    kind = p.get("kind") or ""
    if p.get("campaign_row_id"):
        return f"{kind}:c{p['campaign_row_id']}"
    if p.get("account_id"):
        return f"{kind}:a{p['account_id']}"
    if p.get("level") == "client" and p.get("client_id"):
        return f"{kind}:k{p['client_id']}"
    if p.get("object_name"):
        return f"{kind}:o{str(p['object_name'])[:200]}"
    return f"{kind}:w" + ",".join("-" if w is None else str(w) for w in _ws_ids(p))


def type_scope(p: dict, ws_client: dict) -> str | None:
    """Где скрывать «такой тип»: client:<id>, если все проекты проблемы в одной папке-клиенте,
    иначе ws:<id> для проблемы одного проекта. Проблема «Без проекта» или разных клиентов — None."""
    if p.get("level") == "client" and p.get("client_id"):
        return f"client:{p['client_id']}"
    ws_ids = _ws_ids(p)
    if not ws_ids or None in ws_ids:
        return None
    clients = {ws_client.get(w) for w in ws_ids}
    if len(clients) == 1 and None not in clients:
        return f"client:{clients.pop()}"
    if len(ws_ids) == 1:
        return f"ws:{ws_ids[0]}"
    return None


def snapshot_of(p: dict) -> dict:
    """Что запомнить при скрытии, чтобы потом понять, что стало хуже."""
    return {"severity": p.get("severity") or "info", "metric": dict(p.get("metric") or {})}


def is_worse(snapshot: dict, p: dict) -> bool:
    """Проблема стала хуже снимка: серьёзность выросла, деньги кончились, дней на балансе вдвое меньше,
    новая причина или больше отклонённых объявлений."""
    snapshot = snapshot or {}
    if SEVERITY_RANK.get(p.get("severity"), 0) > SEVERITY_RANK.get(snapshot.get("severity"), 0):
        return True
    old, new = snapshot.get("metric") or {}, p.get("metric") or {}
    if old.get("balance") is not None and new.get("balance") is not None and old["balance"] > 0 >= new["balance"]:
        return True
    if old.get("days") is not None and new.get("days") is not None and old["days"] > 0 and new["days"] <= old["days"] / 2:
        return True
    if set(new.get("reasons") or []) - set(old.get("reasons") or []) - {""}:
        return True
    if old.get("count") is not None and new.get("count") is not None and new["count"] > old["count"]:
        return True
    return False


def _money_out(p: dict) -> bool:
    balance = (p.get("metric") or {}).get("balance")
    return p.get("kind") == "balance" and balance is not None and balance <= 0


def type_snapshot(p: dict, old: dict | None = None) -> dict:
    """Снимок скрытия типа: самая серьёзная скрытая проблема, для денег — кончались ли они.
    Повторное скрытие того же типа (проблема была серьёзнее) только расширяет снимок."""
    old = old or {}
    severities = [p.get("severity") or "info"] + ([old["severity"]] if old.get("severity") else [])
    out = {"severity": max(severities, key=lambda v: SEVERITY_RANK.get(v, 0))}
    if p.get("kind") == "balance":
        out["money_out"] = bool(old.get("money_out")) or _money_out(p)
    return out


def type_worse(snapshot: dict, p: dict) -> bool:
    """Проблема серьёзнее того, что скрыли типом: выше серьёзность или деньги кончились, а скрывали, пока были."""
    snapshot = snapshot or {}
    hidden = snapshot.get("severity") or TYPE_DEFAULT_SEVERITY
    if SEVERITY_RANK.get(p.get("severity"), 0) > SEVERITY_RANK.get(hidden, 0):
        return True
    return _money_out(p) and not snapshot.get("money_out")


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def active(snooze: dict, now: datetime) -> bool:
    until = _aware(snooze.get("until"))
    return until is None or until > now


def split(problems: list[dict], snoozes: list[dict], ws_client: dict, now: datetime) -> dict:
    """Делит проблемы на видимые и скрытые.

    → {"visible": [...], "hidden": {snooze_id: [problem, ...]}, "broken": {id}, "seen": {id}}
    broken — скрытия, которые пора снять (истекли или проблема стала хуже); seen — скрытия,
    чья проблема сейчас есть (для «так задумано» это продлевает жизнь записи). Проблему серьёзнее
    скрытого типом показываем, а само скрытие типа не снимаем.
    """
    by_key = {s["problem_key"]: s for s in snoozes if s["mode"] != "type"}
    by_type = {(s["kind"], s["scope"]): s for s in snoozes if s["mode"] == "type"}
    visible: list[dict] = []
    hidden: dict[int, list[dict]] = {}
    broken: set[int] = {int(s["id"]) for s in snoozes if s["mode"] == "days" and not active(s, now)}
    seen: set[int] = set()
    for p in problems:
        scope = type_scope(p, ws_client)
        s = by_type.get((p.get("kind"), scope)) if scope else None
        if s is not None and type_worse(s.get("snapshot") or {}, p):
            s = None
        if s is None:
            s = by_key.get(problem_key(p))
            if s is not None and (int(s["id"]) in broken or is_worse(s.get("snapshot") or {}, p)):
                broken.add(int(s["id"]))
                s = None
        if s is None:
            visible.append(p)
            continue
        hidden.setdefault(int(s["id"]), []).append(p)
        seen.add(int(s["id"]))
    return {"visible": visible, "hidden": hidden, "broken": broken, "seen": seen}


def is_hidden(p: dict, snoozes: list[dict], ws_client: dict, now: datetime) -> bool:
    """Скрыта ли проблема (для срочных уведомлений: событие проверяется как проблема того же типа
    и с той же серьёзностью). Серьёзнее скрытого — не скрыта, как и в сводке."""
    scope = type_scope(p, ws_client)
    for s in snoozes:
        if s["mode"] == "type":
            if scope and s["kind"] == p.get("kind") and s["scope"] == scope and not type_worse(s.get("snapshot") or {}, p):
                return True
        elif s["problem_key"] == problem_key(p) and active(s, now) and not is_worse(s.get("snapshot") or {}, p):
            return True
    return False


def scope_info(scope: str | None, ws_names: dict, client_names: dict) -> dict | None:
    """Подпись для пункта меню «Скрыть такой тип для этого клиента/проекта»."""
    if not scope:
        return None
    level, _, raw = scope.partition(":")
    try:
        obj_id = int(raw)
    except ValueError:
        return None
    if level == "client":
        return {"key": scope, "level": "client", "name": client_names.get(obj_id) or ""}
    return {"key": scope, "level": "project", "name": ws_names.get(obj_id) or ""}


def place_of(p: dict) -> str:
    """Где проблема — одной строкой: проект (или клиент) и объект."""
    project = p.get("workspace_name") or p.get("client_name") or ""
    obj = p.get("object_name") or ""
    return " · ".join(x for x in (project, obj if obj != project else "") if x)


def _scope_place(info: dict | None) -> str:
    if not info:
        return ""
    what = "Клиент" if info["level"] == "client" else "Проект"
    return f"{what} «{info['name']}»" if info["name"] else what


def _note(s: dict, now: datetime) -> str:
    if s["mode"] == "days":
        until = _aware(s.get("until")) or now
        return f"Скрыто до {until.astimezone(MSK):%d.%m}"
    if s["mode"] == "intended":
        return "Так задумано — покажем снова, если станет хуже"
    return "Скрыт весь тип — покажем, если станет хуже"


def hidden_rows(
    snoozes: list[dict], hidden: dict, ws_client: dict, ws_names: dict, client_names: dict, now: datetime
) -> list[dict]:
    """Строки «Скрыто: N». Скрытие одной проблемы — пока она есть; скрытие типа — всегда (его можно вернуть)."""
    rows = []
    for s in sorted(snoozes, key=lambda s: _aware(s.get("created_at")) or now, reverse=True):
        sid = int(s["id"])
        matched = hidden.get(sid) or []
        if s["mode"] != "type" and not matched:
            continue
        if s["mode"] == "type":
            info = scope_info(s["scope"], ws_names, client_names)
            level, _, raw = s["scope"].partition(":")
            if level == "client":
                ws_ids = [w for w, c in ws_client.items() if raw.isdigit() and c == int(raw)]
            else:
                ws_ids = [int(raw)] if raw.isdigit() else []
            title = f"Все сигналы «{KIND_LABELS.get(s['kind'], s['title'] or 'Такие сигналы')}»"
            place = _scope_place(info)
        else:
            p = matched[0]
            ws_ids = [w for w in _ws_ids(p) if w is not None]
            title, place = p.get("title") or s["title"], place_of(p)
        severity = max((p.get("severity") or "info" for p in matched), key=lambda v: SEVERITY_RANK.get(v, 0), default="")
        rows.append(
            {
                "id": sid,
                "mode": s["mode"],
                "kind": s["kind"],
                "title": title,
                "place": place,
                "note": _note(s, now),
                "severity": severity,
                "count": len(matched),
                "workspace_ids": ws_ids,
            }
        )
    return rows


# --- База ---------------------------------------------------------------------------------------------


def load(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        return list(
            conn.execute(
                "SELECT * FROM problem_snoozes WHERE user_id = %s ORDER BY id", (user_id,)
            ).fetchall()
        )


def apply(
    user_id: int,
    problems: list[dict],
    *,
    ws_client: dict,
    ws_names: dict,
    client_names: dict,
    now: datetime,
) -> tuple[list[dict], list[dict]]:
    """Для build_overview: (видимые проблемы с ключами скрытия, строки «Скрыто»).

    Видимой проблеме добавляются key (для «⋯») и type_scope (можно ли скрыть тип и для кого).
    Заодно снимает истёкшие и ухудшившиеся скрытия и отмечает, что скрытые проблемы ещё есть.
    """
    for p in problems:
        p["key"] = problem_key(p)
        p["type_scope"] = scope_info(type_scope(p, ws_client), ws_names, client_names)
    snoozes = load(user_id)
    if not snoozes:
        return problems, []
    res = split(problems, snoozes, ws_client, now)
    forget = set(res["broken"])
    stale = now - timedelta(days=INTENDED_FORGET_DAYS)
    for s in snoozes:
        sid = int(s["id"])
        if s["mode"] == "intended" and sid not in res["seen"] and (_aware(s["last_seen_at"]) or now) < stale:
            forget.add(sid)
    touch = [
        int(s["id"])
        for s in snoozes
        if int(s["id"]) in res["seen"] and (_aware(s["last_seen_at"]) or now) < now - timedelta(hours=1)
    ]
    if forget or touch:
        with pool.connection() as conn:
            if forget:
                conn.execute(
                    "DELETE FROM problem_snoozes WHERE user_id = %s AND id = ANY(%s)", (user_id, sorted(forget))
                )
            if touch:
                conn.execute("UPDATE problem_snoozes SET last_seen_at = %s WHERE id = ANY(%s)", (now, touch))
    kept = [s for s in snoozes if int(s["id"]) not in forget]
    rows = hidden_rows(kept, res["hidden"], ws_client, ws_names, client_names, now)
    return res["visible"], rows


def create(user_id: int, key: str, mode: str, now: datetime | None = None) -> dict:
    """Скрыть проблему из текущей сводки по её ключу. Проблему ищем на сервере — снимок честный."""
    from app.overview import build_overview

    if mode not in MODES:
        raise SnoozeError("Неизвестный способ скрыть")
    key = (key or "").strip()
    if not key:
        raise SnoozeError("Не указано, что скрыть")
    now = now or datetime.now(timezone.utc)
    overview = build_overview(user_id, now=now)
    p = next((p for p in overview["problems"] if p.get("key") == key), None)
    if p is None:
        # Вторая вкладка со старой сводкой: сигнал уже скрыт — способ меняют через «Показать снова».
        if any(s["mode"] != "type" and s["problem_key"] == key for s in load(user_id)):
            raise SnoozeError("Этот сигнал уже скрыт — обновите страницу")
        raise SnoozeError("Этого сигнала уже нет — обновите страницу")
    ws_ids = [w for w in _ws_ids(p) if w is not None]
    with pool.connection() as conn:
        if mode == "type":
            scope = (p.get("type_scope") or {}).get("key")
            if not scope:
                raise SnoozeError("Этот сигнал нельзя скрыть для клиента целиком")
            # Тип уже скрыт, а эта проблема видна — она серьёзнее: снимок расширяем до неё.
            old = conn.execute(
                "SELECT snapshot FROM problem_snoozes WHERE user_id = %s AND mode = 'type' AND kind = %s AND scope = %s"
                " FOR UPDATE",
                (user_id, p["kind"], scope),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO problem_snoozes (user_id, mode, kind, scope, title, place, workspace_ids, snapshot)
                VALUES (%s, 'type', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, kind, scope) WHERE mode = 'type' DO UPDATE SET snapshot = EXCLUDED.snapshot
                """,
                (
                    user_id,
                    p["kind"],
                    scope,
                    (KIND_LABELS.get(p["kind"]) or p.get("title") or "")[:200],
                    _scope_place(p["type_scope"]),
                    ws_ids,
                    Jsonb(type_snapshot(p, old["snapshot"] if old else None)),
                ),
            )
        else:
            until = now + timedelta(days=DAYS_HIDE) if mode == "days" else None
            conn.execute(
                """
                INSERT INTO problem_snoozes (user_id, mode, problem_key, kind, title, place, workspace_ids,
                                             snapshot, until, created_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, problem_key) WHERE mode <> 'type' DO UPDATE
                SET mode = EXCLUDED.mode, title = EXCLUDED.title, place = EXCLUDED.place,
                    workspace_ids = EXCLUDED.workspace_ids, snapshot = EXCLUDED.snapshot, until = EXCLUDED.until,
                    created_at = EXCLUDED.created_at, last_seen_at = EXCLUDED.last_seen_at
                """,
                (
                    user_id,
                    mode,
                    key,
                    p["kind"],
                    (p.get("title") or "")[:300],
                    place_of(p)[:300],
                    ws_ids,
                    Jsonb(snapshot_of(p)),
                    until,
                    now,
                    now,
                ),
            )
    return {"ok": True}


def remove(user_id: int, snooze_id: int) -> dict:
    """«Показать снова»."""
    with pool.connection() as conn:
        row = conn.execute(
            "DELETE FROM problem_snoozes WHERE user_id = %s AND id = %s RETURNING id", (user_id, snooze_id)
        ).fetchone()
    if row is None:
        raise SnoozeError("Этот сигнал уже показывается")
    return {"ok": True}
