"""Чистка поисковых запросов проекта: очередь, разметка ИИ, конфликты с ключами, память решений.

Запросы всех кампаний проекта сводятся в одну очередь по нормализованному тексту
(регистр и ё/е не различаются, как в Директе). Решение по запросу хранится в query_labels:
неподтверждённое — подсказка (ИИ, память, бриф), подтверждённое — решение маркетолога.
Подтверждённые больше не переспрашиваем, а их минус-фразы и примеры уходят в правила и в промпт ИИ.

Совпадение минус-фразы с запросом или ключом — приближённое: слова сравниваются по основе
(простое отсечение окончаний), стоп-слова не учитываются, порядок слов неважен — как в Директе.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from app import ai, billing
from app.adgroups import _parse_phrases
from app.brief import brief_prompt
from app.db import pool
from app.journal import journal_quiet, log_action
from app.negative_lists import MAX_WORDS, phrase_key, phrase_problems
from app.negatives import REASONS, inverse, patch_negatives, targets_of
from app.workspaces import list_workspaces, require_workspace
from app.yandex import YandexError

log = logging.getLogger("app.query_cleaning")

Progress = Callable[[int, int, str], None]

# Та же нормализация, что phrase_key, но в SQL: очередь и метки сходятся по ней.
NORM_SQL = r"regexp_replace(replace(replace(lower(btrim(q.query)), 'ё', 'е'), 'Ё', 'е'), '\s+', ' ', 'g')"

TABS = ("new", "negative", "target", "done", "all")
_TAB_WHERE = {
    "new": "l.id IS NULL",
    "negative": "l.confirmed = FALSE AND l.verdict = 'negative'",
    "target": "l.confirmed = FALSE AND l.verdict = 'target'",
    "done": "l.confirmed = TRUE",
    "all": "TRUE",
}
VERDICTS = ("negative", "target")
# Пересечение с ключом решили кавычками у ключа: запрос решён, а минус-фразы нет.
KEYWORD_QUOTED_REASON = "Ключ взят в кавычки — минус-фраза не нужна"
LEVELS = ("adgroup", "campaign")
AI_BATCH = 40
CLASSIFY_MAX = 500
# Бесплатные правила после загрузки: до стольких новых запросов проекта за раз (сначала дорогие).
# Загрузка заново кладёт все запросы периода, так что каждый раз проходим тот же верх очереди: держим его
# небольшим, чтобы правила не тормозили загрузку (тысяча строк — доли секунды даже при сотнях минус-фраз).
RULES_MAX = 1000
# «Разметить все проекты»: по проекту — столько же запросов, сколько у кнопки в проекте.
ALL_LIMIT = 300
# Разметка проекта — одна за раз (кнопка в проекте и «Разметить все проекты»): вторая ждёт первую столько,
# проверяя каждые CLASSIFY_POLL_SECONDS, и берёт то, что осталось без метки.
CLASSIFY_WAIT_SECONDS = 15 * 60
CLASSIFY_POLL_SECONDS = 3.0
# Сбои самого ИИ и лимита (плюс ai-* и мост — ai.is_provider_error): после них «Разметить все проекты» размечает
# остальные проекты только правилами. Прочие ошибки касаются одного проекта (удалили, закрыли, занят).
AI_STOP_ERRORS = frozenset({"units-exhausted", "subscription-expired", "feature-locked"})
APPLY_MAX = 500
EXAMPLES_EACH = 40
# Ответ ИИ на один запрос — около 50 токенов (id, вердикт, причина до 80 символов, фраза, уровень);
# после первых разметок оценка берёт фактическое среднее этой модели.
OUT_TOKENS_PER_QUERY = 50
SEARCH_TYPES = ("TEXT_CAMPAIGN", "UNIFIED_CAMPAIGN", "DYNAMIC_TEXT_CAMPAIGN")

# --- Слова и основы -----------------------------------------------------------------------------

STOP_WORDS = frozenset(
    "в во на и или с со к ко по о об обо от до для из у за над под при без про через не ни а но же ли бы "
    "как что это то".split()
)
_ENDINGS = tuple(
    sorted(
        {
            "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ться", "тся",
            "ешь", "ете", "ите", "ить", "ать", "ять", "еть", "уть", "ией", "иям", "ием", "иях",
            "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ых", "их", "ом", "ем",
            "ам", "ям", "ах", "ях", "ов", "ев", "ей", "ию", "ия", "ья", "ье", "ью", "ут", "ют", "ат",
            "ят", "ит", "ет", "ла", "ло", "ли",
            "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
        },
        key=len,
        reverse=True,
    )
)
_WORD = re.compile(r"[0-9a-zа-я]+")
_CYRILLIC = re.compile(r"[а-я]")
_MINUS_TAIL = re.compile(r"\s+-(?=\S)")
_NEG_ALLOWED = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ\s!+\-'\".\[\]]")


def stem(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if len(word) <= 3 or not _CYRILLIC.search(word):
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def stems(text: str) -> list[str]:
    """Основы значимых слов; если значимых нет — всех (минус-фраза «как» тоже что-то значит)."""
    words = _WORD.findall(str(text or "").lower().replace("ё", "е"))
    significant = [w for w in words if w not in STOP_WORDS]
    return [stem(w) for w in (significant or words)]


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 2:
        return False
    return a.startswith(b) or b.startswith(a)


def blocks_stems(negative: list[str], text: list[str]) -> bool:
    """Минус-фраза отсекает текст, если каждое её слово есть в тексте (в любой форме и порядке)."""
    return bool(negative) and all(any(_same(n, t) for t in text) for n in negative)


def blocks(negative: str, text: str) -> bool:
    return blocks_stems(stems(negative), stems(text))


def keyword_text(raw: str) -> str:
    """Ключ без «-минус-слов» и операторов."""
    text = _MINUS_TAIL.split(str(raw or "").strip())[0]
    return " ".join(re.sub(r'["\[\]+!]', " ", text).split())


def clean_negative(raw: str) -> str:
    """Минус-фраза без лишних символов, «-» в начале слов и плюсов-модификаторов."""
    text = _NEG_ALLOWED.sub(" ", str(raw or ""))
    words = []
    for word in text.split():
        word = word.lstrip("-+").strip(".")
        if word and word not in ("!", '"'):
            words.append(word)
    return " ".join(words)[:500]


def _iso(value) -> str | None:
    return value.isoformat() if value else None


# --- Очередь ------------------------------------------------------------------------------------


def _agg_sql(extra: str = "") -> str:
    return f"""
        SELECT {NORM_SQL} AS norm,
               MIN(q.query) AS query,
               SUM(q.impressions)::bigint AS impressions,
               SUM(q.clicks)::bigint AS clicks,
               SUM(q.cost_kopecks)::bigint AS cost_kopecks,
               SUM(q.conversions)::bigint AS conversions,
               array_agg(DISTINCT q.campaign_row_id) AS campaign_row_ids,
               array_remove(array_agg(DISTINCT q.adgroup_row_id), NULL) AS adgroup_row_ids,
               array_remove(array_agg(DISTINCT NULLIF(q.matched_keyword, '')), NULL) AS matched,
               MIN(q.date_from) AS date_from,
               MAX(q.date_to) AS date_to
        FROM direct_search_queries q
        JOIN workspace_campaigns wc ON wc.campaign_row_id = q.campaign_row_id
        WHERE q.user_id = %s AND wc.workspace_id = %s AND btrim(q.query) <> '' {extra}
        GROUP BY 1
    """


def _filters(search: str, min_cost_rub, no_conversions: bool) -> tuple[list[str], list]:
    where: list[str] = []
    params: list = []
    key = phrase_key(search or "")
    if key:
        where.append("strpos(a.norm, %s) > 0")
        params.append(key)
    if min_cost_rub not in (None, ""):
        try:
            kopecks = int(round(float(min_cost_rub) * 100))
        except (TypeError, ValueError):
            kopecks = 0
        if kopecks > 0:
            where.append("a.cost_kopecks >= %s")
            params.append(kopecks)
    if no_conversions:
        where.append("a.conversions = 0")
    return where, params


def query_queue(
    user_id: int,
    workspace_id: int,
    *,
    tab: str = "new",
    search: str = "",
    min_cost_rub=None,
    no_conversions: bool = False,
    campaign_row_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    require_workspace(user_id, workspace_id)
    tab = tab if tab in TABS else "new"
    limit = max(1, min(int(limit or 100), 300))
    offset = max(0, int(offset or 0))
    extra, agg_params = "", [user_id, workspace_id]
    if campaign_row_id:
        extra = "AND q.campaign_row_id = %s"
        agg_params.append(int(campaign_row_id))
    where, params = _filters(search, min_cost_rub, no_conversions)
    base = f"WITH a AS ({_agg_sql(extra)}) "
    join = "FROM a LEFT JOIN query_labels l ON l.workspace_id = %s AND l.query_norm = a.norm"
    common = " AND ".join(where) or "TRUE"
    with pool.connection() as conn:
        counts = conn.execute(
            base
            + f"""
            SELECT COUNT(*) FILTER (WHERE l.id IS NULL) AS new,
                   COUNT(*) FILTER (WHERE {_TAB_WHERE['negative']}) AS negative,
                   COUNT(*) FILTER (WHERE {_TAB_WHERE['target']}) AS target,
                   COUNT(*) FILTER (WHERE l.confirmed = TRUE) AS done,
                   COUNT(*) AS all,
                   COALESCE(SUM(a.cost_kopecks) FILTER (WHERE l.id IS NULL), 0) AS new_cost,
                   COALESCE(SUM(a.cost_kopecks) FILTER (WHERE {_TAB_WHERE['negative']}), 0) AS negative_cost,
                   MIN(a.date_from) AS date_from, MAX(a.date_to) AS date_to
            {join}
            WHERE {common}
            """,
            [*agg_params, workspace_id, *params],
        ).fetchone()
        rows = conn.execute(
            base
            + f"""
            SELECT a.*, l.verdict, l.reason, l.negative_phrase, l.level, l.source, l.confirmed,
                   l.applied_at, l.updated_at AS label_at
            {join}
            WHERE {common} AND {_TAB_WHERE[tab]}
            ORDER BY a.cost_kopecks DESC, a.clicks DESC, a.norm
            LIMIT %s OFFSET %s
            """,
            [*agg_params, workspace_id, *params, limit, offset],
        ).fetchall()
        synced = conn.execute(
            """
            SELECT MAX(q.synced_at) AS synced_at FROM direct_search_queries q
            JOIN workspace_campaigns wc ON wc.campaign_row_id = q.campaign_row_id
            WHERE q.user_id = %s AND wc.workspace_id = %s
            """,
            (user_id, workspace_id),
        ).fetchone()
    ctx = _context(user_id, rows)
    return {
        "tab": tab,
        "items": [_row_out(r, ctx) for r in rows],
        "counts": {k: int(counts[k] or 0) for k in TABS},
        "new_cost_rub": round(int(counts["new_cost"] or 0) / 100, 2),
        "negative_cost_rub": round(int(counts["negative_cost"] or 0) / 100, 2),
        "total": int(counts[tab] or 0),
        "date_from": _iso(counts["date_from"]),
        "date_to": _iso(counts["date_to"]),
        "synced_at": _iso(synced["synced_at"]) if synced else None,
        "campaigns": workspace_search_campaigns(user_id, workspace_id),
    }


HIGHLIGHT_ROWS = 5
_NOT_MINUSED = "COALESCE(NOT (l.confirmed = TRUE AND l.verdict = 'negative'), TRUE)"


def query_highlights(user_id: int, workspace_id: int, *, limit: int = HIGHLIGHT_ROWS) -> dict:
    """Простой режим «Запросов»: самые дорогие запросы без заявок (ещё не убранные в минус)
    и сколько минус-фраз добавлено за 30 дней."""
    require_workspace(user_id, workspace_id)
    join = "FROM a LEFT JOIN query_labels l ON l.workspace_id = %s AND l.query_norm = a.norm"
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            WITH a AS ({_agg_sql()})
            SELECT a.*, l.verdict, l.reason, l.negative_phrase, l.level, l.source, l.confirmed,
                   l.applied_at, l.updated_at AS label_at
            {join}
            WHERE a.conversions = 0 AND a.cost_kopecks > 0 AND {_NOT_MINUSED}
            ORDER BY a.cost_kopecks DESC, a.clicks DESC, a.norm
            LIMIT %s
            """,
            (user_id, workspace_id, workspace_id, max(1, min(int(limit), 20))),
        ).fetchall()
        removed = conn.execute(
            """
            SELECT COUNT(DISTINCT lower(negative_phrase)) FILTER (WHERE negative_phrase <> '') AS phrases,
                   COUNT(*) AS queries
            FROM query_labels
            WHERE workspace_id = %s AND user_id = %s AND verdict = 'negative' AND confirmed = TRUE
              AND applied_at >= NOW() - INTERVAL '30 days'
            """,
            (workspace_id, user_id),
        ).fetchone()
    ctx = _context(user_id, rows)
    return {
        "costly": [_row_out(r, ctx) for r in rows],
        "removed_30d": {"phrases": int(removed["phrases"] or 0), "queries": int(removed["queries"] or 0)},
    }


def portfolio_highlights(user_id: int, *, limit: int = HIGHLIGHT_ROWS) -> dict:
    """То же по всем проектам: дорогие запросы без заявок одним списком, у строки — её проект."""
    costly: list[dict] = []
    removed = {"phrases": 0, "queries": 0}
    for ws in list_workspaces(user_id):
        if ws["locked"]:
            continue
        h = query_highlights(user_id, ws["id"], limit=limit)
        project = {"id": ws["id"], "name": ws["name"]}
        costly.extend({**row, "project": project} for row in h["costly"])
        for key in removed:
            removed[key] += h["removed_30d"][key]
    costly.sort(key=lambda r: (-r["cost_rub"], -r["clicks"], r["query_norm"]))
    return {"costly": costly[:limit], "removed_30d": removed}


def portfolio_queue(
    user_id: int,
    *,
    tab: str = "new",
    search: str = "",
    min_cost_rub=None,
    no_conversions: bool = False,
    workspace_id: int | None = None,
    limit: int = 100,
) -> dict:
    """Общая очередь разбора при «Все проекты»: очереди проектов одним списком по расходу, у строки — её проект.
    Разметка у каждого проекта своя, поэтому решения применяются через проект строки."""
    tab = tab if tab in TABS else "new"
    limit = max(1, min(int(limit or 100), 300))
    items: list[dict] = []
    projects: list[dict] = []
    counts = {k: 0 for k in TABS}
    new_cost = negative_cost = 0.0
    for ws in list_workspaces(user_id):
        if ws["locked"] or (workspace_id and ws["id"] != workspace_id):
            continue
        q = query_queue(
            user_id, ws["id"], tab=tab, search=search, min_cost_rub=min_cost_rub,
            no_conversions=no_conversions, limit=limit,
        )
        project = {"id": ws["id"], "name": ws["name"]}
        items.extend({**row, "project": project} for row in q["items"])
        for key in TABS:
            counts[key] += q["counts"][key]
        new_cost += q["new_cost_rub"]
        negative_cost += q["negative_cost_rub"]
        projects.append(
            {
                **project,
                "client_name": ws.get("client_name") or "",
                "counts": q["counts"],
                "new_cost_rub": q["new_cost_rub"],
                "negative_cost_rub": q["negative_cost_rub"],
                "date_from": q["date_from"],
                "date_to": q["date_to"],
                "synced_at": q["synced_at"],
                "search_campaigns": len(q["campaigns"]),
            }
        )
    items.sort(key=lambda r: (-r["cost_rub"], -r["clicks"], r["query_norm"]))
    projects.sort(key=lambda p: (-p["new_cost_rub"] - p["negative_cost_rub"], p["name"].lower()))
    return {
        "tab": tab,
        "items": items[:limit],
        "counts": counts,
        "total": counts[tab],
        "new_cost_rub": round(new_cost, 2),
        "negative_cost_rub": round(negative_cost, 2),
        "projects": projects,
    }


def workspace_search_campaigns(user_id: int, workspace_id: int) -> list[dict]:
    """Кампании проекта, у которых бывают поисковые запросы, — для фильтра и загрузки."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.campaign_id, c.name, c.state, c.campaign_type,
                   (SELECT COUNT(*) FROM direct_search_queries q WHERE q.campaign_row_id = c.id) AS queries,
                   (SELECT MAX(q.synced_at) FROM direct_search_queries q WHERE q.campaign_row_id = c.id) AS synced_at
            FROM workspace_campaigns wc
            JOIN direct_campaigns c ON c.id = wc.campaign_row_id
            WHERE wc.workspace_id = %s AND wc.user_id = %s AND c.campaign_type = ANY(%s)
            ORDER BY c.state = 'ARCHIVED', c.name
            """,
            (workspace_id, user_id, list(SEARCH_TYPES)),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "campaign_id": r["campaign_id"],
            "name": r["name"],
            "state": r["state"],
            "queries": int(r["queries"] or 0),
            "synced_at": _iso(r["synced_at"]),
        }
        for r in rows
    ]


def _keyword_index(user_id: int, campaign_ids: list[int]) -> tuple[dict[int, list[dict]], dict[int, list[dict]]]:
    """Ключи кампаний → (по группам, по кампаниям), с основами слов."""
    by_group: dict[int, list[dict]] = defaultdict(list)
    by_campaign: dict[int, list[dict]] = defaultdict(list)
    if not campaign_ids:
        return by_group, by_campaign
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT k.id, k.keyword, k.adgroup_row_id, g.campaign_row_id, g.name AS adgroup_name
            FROM direct_keywords k
            JOIN direct_adgroups g ON g.id = k.adgroup_row_id
            WHERE k.user_id = %s AND g.campaign_row_id = ANY(%s)
            """,
            (user_id, list(campaign_ids)),
        ).fetchall()
    for r in rows:
        raw = str(r["keyword"] or "")
        if raw.startswith("---"):  # автотаргетинг
            continue
        text = keyword_text(raw)
        if not text:
            continue
        item = {
            "id": r["id"],
            "keyword": text,
            "adgroup_row_id": r["adgroup_row_id"],
            "adgroup_name": r["adgroup_name"],
            "campaign_row_id": r["campaign_row_id"],
            "stems": stems(text),
        }
        by_group[int(r["adgroup_row_id"])].append(item)
        by_campaign[int(r["campaign_row_id"])].append(item)
    return by_group, by_campaign


def effective_level(level, row: dict | None) -> str:
    """Куда фраза ляжет на самом деле: запрос без группы (например, из автотаргетинга) минусуем в кампании."""
    level = level if level in LEVELS else "adgroup"
    return "campaign" if level == "adgroup" and not (row or {}).get("adgroup_row_ids") else level


def find_conflicts(phrase: str, level: str, row: dict, index) -> list[dict]:
    """Ключи, которые пересекаются с минус-фразой там, куда она ляжет."""
    by_group, by_campaign = index
    neg = stems(phrase)
    if not neg:
        return []
    if level == "adgroup" and row.get("adgroup_row_ids"):
        candidates = [k for g in row["adgroup_row_ids"] for k in by_group.get(int(g), [])]
    else:
        candidates = [k for c in row.get("campaign_row_ids") or [] for k in by_campaign.get(int(c), [])]
    seen, out = set(), []
    for k in candidates:
        if k["id"] not in seen and blocks_stems(neg, k["stems"]):
            seen.add(k["id"])
            out.append(k)
    return out


def conflict_note(conflicts: list[dict]) -> str:
    """«заденет ключ «окна пвх»» — для карточек, журнала и подсказок чистки."""
    words = ", ".join(f"«{k['keyword']}»" for k in conflicts[:3])
    more = f" и ещё {len(conflicts) - 3}" if len(conflicts) > 3 else ""
    return ("заденет ключ " if len(conflicts) == 1 else "заденет ключи ") + words + more


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


def campaign_conflicts(user_id: int, campaign_row_id: int, phrases: list[str]) -> dict[str, list[dict]]:
    """Минус-фразы кампании, которые отключат её же ключи → {фраза: [ключи]}."""
    index = _keyword_index(user_id, [int(campaign_row_id)])
    row = {"campaign_row_ids": [int(campaign_row_id)], "adgroup_row_ids": []}
    out: dict[str, list[dict]] = {}
    for phrase in phrases:
        hits = find_conflicts(clean_negative(phrase), "campaign", row, index)
        if hits:
            out[phrase] = hits
    return out


def _negatives_index(user_id: int, campaign_ids: list[int], adgroup_ids: list[int]) -> dict:
    """Текущие минус-фразы (локальная копия) кампаний и групп, с основами."""
    out: dict = {"campaigns": {}, "adgroups": {}, "group_campaign": {}}
    with pool.connection() as conn:
        if campaign_ids:
            for r in conn.execute(
                "SELECT id, negative_keywords FROM direct_campaigns WHERE user_id = %s AND id = ANY(%s)",
                (user_id, list(campaign_ids)),
            ).fetchall():
                out["campaigns"][int(r["id"])] = [(p, stems(p)) for p in _parse_phrases(r["negative_keywords"])]
        if adgroup_ids:
            for r in conn.execute(
                "SELECT id, campaign_row_id, negative_keywords FROM direct_adgroups WHERE user_id = %s AND id = ANY(%s)",
                (user_id, list(adgroup_ids)),
            ).fetchall():
                out["adgroups"][int(r["id"])] = [(p, stems(p)) for p in _parse_phrases(r["negative_keywords"])]
                out["group_campaign"][int(r["id"])] = int(r["campaign_row_id"])
    return out


def blocked_info(row: dict, negatives: dict, query_stems: list[str]) -> tuple[str, str] | None:
    """(минус-фраза, уровень), если запрос уже отсекается везде, где показывался (до следующей выгрузки)."""

    def hit(phrases) -> str | None:
        for phrase, neg in phrases:
            if blocks_stems(neg, query_stems):
                return phrase
        return None

    found, level = None, "campaign"
    places = row.get("adgroup_row_ids") or []
    for gid in places:
        campaign = negatives["group_campaign"].get(int(gid))
        phrase = hit(negatives["campaigns"].get(campaign, []))
        if phrase is None:
            phrase = hit(negatives["adgroups"].get(int(gid), []))
            level = "adgroup"
        if phrase is None:
            return None
        found = found or phrase
    if places:
        return found, level
    for cid in row.get("campaign_row_ids") or []:
        phrase = hit(negatives["campaigns"].get(int(cid), []))
        if phrase is None:
            return None
        found = found or phrase
    return (found, "campaign") if found else None


def blocked_by(row: dict, negatives: dict, query_stems: list[str]) -> str | None:
    info = blocked_info(row, negatives, query_stems)
    return info[0] if info else None


def _context(user_id: int, rows: list[dict]) -> dict:
    campaign_ids = sorted({int(c) for r in rows for c in r["campaign_row_ids"] or []})
    group_ids = sorted({int(g) for r in rows for g in r["adgroup_row_ids"] or []})
    names: dict = {"campaigns": {}, "adgroups": {}}
    with pool.connection() as conn:
        if campaign_ids:
            for r in conn.execute("SELECT id, name FROM direct_campaigns WHERE id = ANY(%s)", (campaign_ids,)).fetchall():
                names["campaigns"][int(r["id"])] = r["name"]
        if group_ids:
            for r in conn.execute(
                "SELECT id, name, campaign_row_id FROM direct_adgroups WHERE id = ANY(%s)", (group_ids,)
            ).fetchall():
                names["adgroups"][int(r["id"])] = (r["name"], r["campaign_row_id"])
    return {
        "names": names,
        "keywords": _keyword_index(user_id, campaign_ids),
        "negatives": _negatives_index(user_id, campaign_ids, group_ids),
    }


def _row_out(r: dict, ctx: dict) -> dict:
    cost = int(r["cost_kopecks"] or 0)
    clicks = int(r["clicks"] or 0)
    conv = int(r["conversions"] or 0)
    label = None
    conflicts: list[dict] = []
    if r.get("verdict"):
        label = {
            "verdict": r["verdict"],
            "reason": r["reason"] or "",
            "negative_phrase": r["negative_phrase"] or "",
            "level": effective_level(r["level"], r),
            "source": r["source"] or "",
            "confirmed": bool(r["confirmed"]),
            "applied_at": _iso(r.get("applied_at")),
        }
        if r["verdict"] == "negative" and r["negative_phrase"]:
            place = r
            if label["applied_at"] and label["level"] == "adgroup" and r.get("adgroup_row_ids"):
                # Уже в Директе на уровне групп: пересечения — только там, где фраза стоит на самом деле
                # (после «только в группы без ключа» группы с ключом её не получили).
                placed = _groups_with(r["adgroup_row_ids"], r["negative_phrase"], ctx["negatives"])
                place = {**r, "adgroup_row_ids": placed} if placed else None
            if place is not None:
                conflicts = find_conflicts(r["negative_phrase"], label["level"], place, ctx["keywords"])
    names = ctx["names"]
    return {
        "query_norm": r["norm"],
        "query": r["query"],
        "impressions": int(r["impressions"] or 0),
        "clicks": clicks,
        "cost_rub": round(cost / 100, 2),
        "conversions": conv,
        "cpc_rub": round(cost / 100 / clicks, 2) if clicks else None,
        "campaigns": [{"id": c, "name": names["campaigns"].get(int(c), "")} for c in r["campaign_row_ids"] or []],
        "adgroups": [
            {"id": g, "name": names["adgroups"].get(int(g), ("", None))[0], "campaign_row_id": names["adgroups"].get(int(g), ("", None))[1]}
            for g in r["adgroup_row_ids"] or []
        ],
        "matched_keywords": list(r["matched"] or [])[:5],
        "label": label,
        "conflicts": [_conflict_out(k) for k in conflicts[:5]],
        "conflicts_total": len(conflicts),
        "blocked_by": blocked_by(r, ctx["negatives"], stems(r["norm"])),
    }


def _groups_with(groups, phrase: str, negatives: dict) -> list[int]:
    """Группы, в минус-фразах которых (локальная копия) эта фраза уже есть."""
    key = phrase_key(clean_negative(phrase))
    return [int(g) for g in groups or [] if any(phrase_key(p) == key for p, _ in negatives["adgroups"].get(int(g), []))]


def _conflict_out(k: dict) -> dict:
    return {
        "keyword_row_id": k["id"],
        "keyword": k["keyword"],
        "adgroup_row_id": k["adgroup_row_id"],
        "adgroup_name": k["adgroup_name"],
    }


def _rows_for(user_id: int, workspace_id: int, norms: list[str]) -> dict[str, dict]:
    if not norms:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            f"WITH a AS ({_agg_sql(f'AND {NORM_SQL} = ANY(%s)')}) SELECT * FROM a",
            (user_id, workspace_id, list(norms)),
        ).fetchall()
    return {r["norm"]: r for r in rows}


def _kept_queries(user_id: int, workspace_id: int) -> list[dict]:
    """Запросы проекта, которые резать нельзя: вы отметили их целевыми или по ним были конверсии
    (если только вы сами не решили минусовать такой запрос)."""
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            WITH a AS ({_agg_sql()})
            SELECT a.norm, a.query, a.conversions, a.campaign_row_ids, a.adgroup_row_ids,
                   COALESCE(l.confirmed = TRUE AND l.verdict = 'target', FALSE) AS target
            FROM a LEFT JOIN query_labels l ON l.workspace_id = %s AND l.query_norm = a.norm
            WHERE (l.confirmed = TRUE AND l.verdict = 'target')
               OR (a.conversions > 0 AND COALESCE(NOT (l.confirmed = TRUE AND l.verdict = 'negative'), TRUE))
            ORDER BY a.conversions DESC, a.cost_kopecks DESC, a.norm
            """,
            (user_id, workspace_id, workspace_id),
        ).fetchall()
    return [{**r, "stems": stems(r["norm"])} for r in rows]


def _kept_hits(phrase: str, level: str, row: dict, kept: list[dict], skip: set[str]) -> list[dict]:
    """Нужные запросы, которые минус-фраза отсечёт там же, куда ляжет (группа или кампания запроса)."""
    neg = stems(phrase)
    if level == "adgroup" and row.get("adgroup_row_ids"):
        key, places = "adgroup_row_ids", {int(g) for g in row["adgroup_row_ids"]}
    else:
        key, places = "campaign_row_ids", {int(c) for c in row.get("campaign_row_ids") or []}
    out = []
    for k in kept:
        if k["norm"] in skip or not places.intersection(int(x) for x in k[key] or []):
            continue
        if blocks_stems(neg, k["stems"]):
            out.append(
                {
                    "query_norm": k["norm"],
                    "query": k["query"],
                    "conversions": int(k["conversions"] or 0),
                    "target": bool(k["target"]),
                }
            )
    return out


def check_phrases(user_id: int, workspace_id: int, items: list[dict]) -> list[dict]:
    """Проверка правленых минус-фраз перед применением: ошибки формата, конфликты с ключами
    и нужные запросы (целевые по вашему решению или с конверсиями), которые фраза тоже отсечёт.
    Запросы из этой же проверки нужными не считаем — их минусуют вместе."""
    require_workspace(user_id, workspace_id)
    items = [i for i in items or [] if isinstance(i, dict) and i.get("query_norm")][:APPLY_MAX]
    rows = _rows_for(user_id, workspace_id, [str(i["query_norm"]) for i in items])
    index = _keyword_index(user_id, sorted({int(c) for r in rows.values() for c in r["campaign_row_ids"]}))
    kept = _kept_queries(user_id, workspace_id) if rows else []
    together = {str(i["query_norm"]) for i in items}
    out = []
    for item in items:
        row = rows.get(str(item["query_norm"]))
        # Как при применении: пустая фраза — значит, минусуем сам запрос.
        phrase = clean_negative(str(item.get("negative_phrase") or "").strip() or (row or {}).get("query") or "")
        level = effective_level(item.get("level"), row)
        problems = phrase_problems([phrase]) if phrase else ["Пустая минус-фраза"]
        conflicts = find_conflicts(phrase, level, row, index) if row and phrase else []
        hits = _kept_hits(phrase, level, row, kept, together) if row and phrase else []
        out.append(
            {
                "query_norm": item["query_norm"],
                "negative_phrase": phrase,
                "problems": problems,
                "conflicts": [_conflict_out(k) for k in conflicts[:5]],
                "conflicts_total": len(conflicts),
                "targets_hit": hits[:5],
                "targets_total": len(hits),
            }
        )
    return out


# --- Метки и память решений ---------------------------------------------------------------------

_LABEL_COLUMNS = "query_norm, query, verdict, reason, negative_phrase, level, source, confirmed, applied_at"


def snapshot_labels(workspace_id: int, norms: list[str]) -> list[dict]:
    """Метки до изменения — для отката; у новых запросов previous = None."""
    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT {_LABEL_COLUMNS} FROM query_labels WHERE workspace_id = %s AND query_norm = ANY(%s)",
            (workspace_id, list(norms)),
        ).fetchall()
    found = {r["query_norm"]: {**r, "applied_at": _iso(r["applied_at"])} for r in rows}
    return [{"query_norm": n, "previous": found.get(n)} for n in norms]


def restore_labels(user_id: int, workspace_id: int, labels: list[dict]) -> int:
    """Шаг отката: вернуть метки как были (или убрать, если их не было)."""
    n = 0
    with pool.connection() as conn:
        for item in labels or []:
            norm = str(item.get("query_norm") or "")
            prev = item.get("previous")
            if not norm:
                continue
            if not prev:
                conn.execute(
                    "DELETE FROM query_labels WHERE workspace_id = %s AND user_id = %s AND query_norm = %s",
                    (workspace_id, user_id, norm),
                )
            else:
                conn.execute(
                    f"""
                    INSERT INTO query_labels (user_id, workspace_id, {_LABEL_COLUMNS}, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (workspace_id, query_norm) DO UPDATE SET
                        query = EXCLUDED.query, verdict = EXCLUDED.verdict, reason = EXCLUDED.reason,
                        negative_phrase = EXCLUDED.negative_phrase, level = EXCLUDED.level,
                        source = EXCLUDED.source, confirmed = EXCLUDED.confirmed,
                        applied_at = EXCLUDED.applied_at, updated_at = NOW()
                    """,
                    (
                        user_id,
                        workspace_id,
                        norm,
                        prev.get("query") or "",
                        prev.get("verdict") if prev.get("verdict") in VERDICTS else "target",
                        prev.get("reason") or "",
                        prev.get("negative_phrase") or "",
                        prev.get("level") if prev.get("level") in LEVELS else "adgroup",
                        prev.get("source") or "user",
                        bool(prev.get("confirmed")),
                        prev.get("applied_at"),
                    ),
                )
            n += 1
    return n


def suggest_labels(user_id: int, workspace_id: int, suggestions: list[dict]) -> int:
    """Подсказки (ИИ, память, бриф): подтверждённые решения не перетираем."""
    n = 0
    with pool.connection() as conn:
        for s in suggestions:
            cur = conn.execute(
                """
                INSERT INTO query_labels (user_id, workspace_id, query_norm, query, verdict, reason,
                                          negative_phrase, level, source, confirmed, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, NOW())
                ON CONFLICT (workspace_id, query_norm) DO UPDATE SET
                    query = EXCLUDED.query, verdict = EXCLUDED.verdict, reason = EXCLUDED.reason,
                    negative_phrase = EXCLUDED.negative_phrase, level = EXCLUDED.level,
                    source = EXCLUDED.source, updated_at = NOW()
                WHERE query_labels.confirmed = FALSE
                """,
                (
                    user_id,
                    workspace_id,
                    s["query_norm"],
                    s.get("query") or s["query_norm"],
                    s["verdict"],
                    (s.get("reason") or "")[:300],
                    s.get("negative_phrase") or "",
                    s.get("level") if s.get("level") in LEVELS else "adgroup",
                    s.get("source") or "ai",
                ),
            )
            n += cur.rowcount
    return n


def remember_decisions(user_id: int, workspace_id: int, decisions: list[dict]) -> None:
    """Решения маркетолога: подтверждено, больше не спрашиваем; applied — уже применено в Директе."""
    with pool.connection() as conn:
        for d in decisions:
            conn.execute(
                """
                INSERT INTO query_labels (user_id, workspace_id, query_norm, query, verdict, reason,
                                          negative_phrase, level, source, confirmed, applied_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'user', TRUE,
                        CASE WHEN %s THEN NOW() END, NOW())
                ON CONFLICT (workspace_id, query_norm) DO UPDATE SET
                    query = EXCLUDED.query, verdict = EXCLUDED.verdict,
                    reason = COALESCE(NULLIF(EXCLUDED.reason, ''),
                                      CASE WHEN query_labels.verdict = EXCLUDED.verdict THEN query_labels.reason END, ''),
                    negative_phrase = EXCLUDED.negative_phrase, level = EXCLUDED.level,
                    source = CASE WHEN query_labels.verdict = EXCLUDED.verdict THEN query_labels.source ELSE 'user' END,
                    confirmed = TRUE,
                    applied_at = CASE WHEN %s THEN NOW()
                                      WHEN query_labels.verdict = EXCLUDED.verdict
                                           AND (EXCLUDED.verdict <> 'negative' OR EXCLUDED.negative_phrase <> '')
                                      THEN query_labels.applied_at END,
                    updated_at = NOW()
                """,
                (
                    user_id,
                    workspace_id,
                    d["query_norm"],
                    d.get("query") or d["query_norm"],
                    d["verdict"],
                    (d.get("reason") or "")[:300],
                    d.get("negative_phrase") or "" if d["verdict"] == "negative" else "",
                    d.get("level") if d.get("level") in LEVELS else "adgroup",
                    bool(d.get("applied")),
                    bool(d.get("applied")),
                ),
            )


def list_decisions(
    user_id: int, workspace_id: int, *, verdict: str | None = None, search: str = "", limit: int = 100, offset: int = 0
) -> dict:
    require_workspace(user_id, workspace_id)
    where = ["workspace_id = %s", "user_id = %s", "confirmed = TRUE"]
    params: list = [workspace_id, user_id]
    if verdict in VERDICTS:
        where.append("verdict = %s")
        params.append(verdict)
    key = phrase_key(search or "")
    if key:
        where.append("(strpos(query_norm, %s) > 0 OR strpos(lower(negative_phrase), %s) > 0)")
        params += [key, key]
    sql_where = " AND ".join(where)
    with pool.connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM query_labels WHERE {sql_where}", params).fetchone()
        rows = conn.execute(
            f"""
            SELECT {_LABEL_COLUMNS}, updated_at FROM query_labels WHERE {sql_where}
            ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s
            """,
            [*params, max(1, min(int(limit), 300)), max(0, int(offset))],
        ).fetchall()
    return {
        "items": [
            {
                "query_norm": r["query_norm"],
                "query": r["query"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "negative_phrase": r["negative_phrase"],
                "level": r["level"],
                "applied_at": _iso(r["applied_at"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ],
        "total": int(total["n"]),
    }


def forget_decisions(user_id: int, workspace_id: int, norms: list[str]) -> dict:
    """Забыть решения: запросы вернутся в разбор. Минус-фразы в Директе остаются."""
    require_workspace(user_id, workspace_id)
    norms = [str(n) for n in norms or [] if str(n or "").strip()][:APPLY_MAX]
    if not norms:
        raise YandexError("empty")
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM query_labels WHERE workspace_id = %s AND user_id = %s AND query_norm = ANY(%s)",
            (workspace_id, user_id, norms),
        )
    return {"forgotten": cur.rowcount}


def memory_examples(workspace_id: int, limit_each: int = EXAMPLES_EACH) -> dict[str, list[dict]]:
    with pool.connection() as conn:
        out = {}
        for verdict in VERDICTS:
            out[verdict] = conn.execute(
                """
                SELECT query, negative_phrase, level FROM query_labels
                WHERE workspace_id = %s AND confirmed = TRUE AND verdict = %s
                  AND NOT (negative_phrase = '' AND reason = %s)
                ORDER BY updated_at DESC LIMIT %s
                """,
                (workspace_id, verdict, KEYWORD_QUOTED_REASON, limit_each),
            ).fetchall()
    # Память «для всех проектов» — после решений самого проекта и без того, что он отметил целевым.
    from app.shared_cleaning import shared_examples

    have = {(r["negative_phrase"] or "").lower() for r in out.get("negative") or []}
    extra = [e for e in shared_examples(workspace_id, limit_each) if e["negative_phrase"].lower() not in have]
    out["negative"] = list(out.get("negative") or []) + extra[: max(limit_each - len(out.get("negative") or []), 0)]
    return out


def examples_prompt(examples: dict[str, list[dict]]) -> str:
    lines = []
    for r in examples.get("negative") or []:
        lines.append(f"- «{r['query']}» → минус «{r['negative_phrase'] or r['query']}»")
    for r in examples.get("target") or []:
        lines.append(f"- «{r['query']}» → целевой")
    return "\n".join(lines)


# --- Разметка: правила и ИИ ---------------------------------------------------------------------


def _memory_rules(workspace_id: int) -> list[tuple]:
    """Подтверждённые минус-фразы проекта → (фраза, основы, уровень, исходный запрос); за ними — память
    «для всех проектов» пятым полем "shared" (слабее: проверяется после проектной, целевые проекта не трогает)."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (lower(negative_phrase)) negative_phrase, level, query
            FROM query_labels
            WHERE workspace_id = %s AND confirmed = TRUE AND verdict = 'negative' AND negative_phrase <> ''
            ORDER BY lower(negative_phrase), updated_at DESC
            """,
            (workspace_id,),
        ).fetchall()
    own = [(r["negative_phrase"], stems(r["negative_phrase"]), r["level"], r["query"]) for r in rows]
    from app.shared_cleaning import shared_rules

    return own + shared_rules(workspace_id)


def _brief_rules(brief: dict) -> list[tuple[str, list[str]]]:
    out = []
    for item in (brief or {}).get("not_ours") or []:
        phrase = clean_negative(item)
        if phrase and not phrase_problems([phrase]):
            out.append((phrase, stems(phrase)))
    return out


def rule_label(row: dict, memory, brief_rules, negatives: dict) -> dict | None:
    """Подсказка без ИИ: уже отсекается в Директе, похоже на прошлое решение, «не наше» из брифа."""
    query_stems = stems(row["norm"])
    already = blocked_info(row, negatives, query_stems)
    if already:
        # Тот же уровень, где фраза уже стоит: подтверждение ничего не добавит в Директ.
        return {
            "verdict": "negative",
            "negative_phrase": already[0],
            "level": already[1],
            "source": "direct",
            "reason": f"Уже отсекается минус-фразой «{already[0]}»",
        }
    for rule in memory:
        phrase, neg, level, query = rule[:4]
        if blocks_stems(neg, query_stems):
            if len(rule) > 4 and rule[4] == "shared":
                return {
                    "verdict": "negative",
                    "negative_phrase": phrase,
                    "level": level,
                    "source": "memory",
                    "reason": f"Вы минусуете «{phrase}» во всех проектах",
                    "_shared": True,
                }
            return {
                "verdict": "negative",
                "negative_phrase": phrase,
                "level": level,
                "source": "memory",
                "reason": f"Как «{query}» — вы минусовали «{phrase}»",
            }
    for phrase, neg in brief_rules:
        if blocks_stems(neg, query_stems):
            return {
                "verdict": "negative",
                "negative_phrase": phrase,
                "level": "campaign",
                "source": "brief",
                "reason": f"В брифе «не наше»: {phrase}",
            }
    return None


_SYSTEM = """Ты — специалист по контекстной рекламе в Яндекс Директе. Разбираешь поисковые запросы, по которым показывались объявления клиента, и для каждого решаешь: целевой он или его нужно добавить в минус-фразы.

{brief}

Ключевые фразы кампаний (выборка):
{keywords}

{examples}Правила:
- negative — запрос явно не про наш товар или услугу: чужой товар, не наша география, информационный интерес («как сделать», «своими руками», «бесплатно», «скачать», «отзывы» без покупки), вакансии, учёба, б/у, если продаём новое.
- target — запрос про наш товар или услугу, даже без конверсий. Сомневаешься — target: отминусовать нужное дороже, чем пропустить лишнее.
- negative (поле) — короткая минус-фраза из 1–2 слов самого запроса, которая отсекает именно лишний смысл. Не бери слова, которые есть в наших ключевых фразах. Только слова из запроса, без операторов и знаков.
- level — "campaign", если смысл чужд всему бизнесу; "adgroup", если запрос подходит другому нашему товару, но не этой группе.
- reason — до 80 символов, по-русски, почему так.

Ответ — только JSON-объект, без текста вокруг:
{{"items": [{{"id": 1, "verdict": "negative", "reason": "...", "negative": "...", "level": "campaign"}}]}}
По одному элементу на каждый запрос из списка, id — как во входных данных."""


def _keywords_sample(index, limit: int = 60) -> str:
    seen, out = set(), []
    for items in index[1].values():
        for k in items:
            key = phrase_key(k["keyword"])
            if key not in seen:
                seen.add(key)
                out.append(k["keyword"])
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return "\n".join(f"- {k}" for k in out) or "- (ключей нет)"


def _valid_negative(raw: str, query: str) -> str:
    """Минус-фраза ИИ: только слова запроса и не длиннее лимита Директа; иначе — сам запрос."""
    phrase = clean_negative(raw)
    query_stems = stems(query)
    words = [w for w in phrase.split() if any(_same(s, q) for s in stems(w) for q in query_stems)]
    if not words:
        words = clean_negative(query).split()
    words = words[:MAX_WORDS]
    phrase = " ".join(words)
    return phrase if not phrase_problems([phrase]) else ""


def _batch_messages(system: str, batch: list[dict]) -> list[dict]:
    payload = [
        {
            "id": n + 1,
            "q": r["query"],
            "kw": (r["matched"] or [""])[0],
            "clicks": int(r["clicks"] or 0),
            "cost": round(int(r["cost_kopecks"] or 0) / 100, 2),
            "conv": int(r["conversions"] or 0),
        }
        for n, r in enumerate(batch)
    ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "Запросы:\n" + json.dumps(payload, ensure_ascii=False)},
    ]


def _batch_tokens(system: str, batch: list[dict], chars_per_token: float, out_per_query: float) -> tuple[int, int]:
    """Оценка токенов одной пачки: вход — по символам сообщения, выход — по числу запросов."""
    chars = len(json.dumps(_batch_messages(system, batch), ensure_ascii=False))
    return math.ceil(chars / chars_per_token), math.ceil(len(batch) * out_per_query)


def ai_labels(cfg: dict, system: str, batch: list[dict]) -> list[dict]:
    data = ai.complete_json({**cfg, "units": len(batch)}, _batch_messages(system, batch))
    out = []
    seen = set()
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("id")) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(batch) or idx in seen:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            continue
        seen.add(idx)
        row = batch[idx]
        label = {
            "query_norm": row["norm"],
            "query": row["query"],
            "verdict": verdict,
            "reason": " ".join(str(item.get("reason") or "").split())[:200],
            "source": "ai",
            "level": item.get("level") if item.get("level") in LEVELS else "campaign",
            "negative_phrase": "",
        }
        if verdict == "negative":
            label["negative_phrase"] = _valid_negative(str(item.get("negative") or ""), row["query"])
            if not label["negative_phrase"]:
                continue
        out.append(label)
    return out


def _classify_plan(user_id: int, payload: dict) -> dict | None:
    """Очередь разметки и подсказки правил — общее у разметки и её оценки. None — размечать нечего."""
    workspace_id = int(payload.get("workspace_id") or 0)
    ws = require_workspace(user_id, workspace_id)
    limit = max(1, min(int(payload.get("limit") or 200), CLASSIFY_MAX))
    relabel = bool(payload.get("relabel"))
    extra, params = "", [user_id, workspace_id]
    if payload.get("campaign_row_id"):
        extra = "AND q.campaign_row_id = %s"
        params.append(int(payload["campaign_row_id"]))
    pending = "l.id IS NULL" + (" OR l.confirmed = FALSE" if relabel else "")
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            WITH a AS ({_agg_sql(extra)})
            SELECT a.* FROM a LEFT JOIN query_labels l ON l.workspace_id = %s AND l.query_norm = a.norm
            WHERE {pending}
            ORDER BY a.cost_kopecks DESC, a.clicks DESC, a.norm
            LIMIT %s
            """,
            [*params, workspace_id, limit],
        ).fetchall()
    if not rows:
        return None
    campaign_ids = sorted({int(c) for r in rows for c in r["campaign_row_ids"]})
    labels, rest = _rule_split(user_id, workspace_id, ws.get("brief") or {}, rows)
    return {"ws": ws, "workspace_id": workspace_id, "rows": rows, "labels": labels, "rest": rest, "campaign_ids": campaign_ids}


def _rule_split(user_id: int, workspace_id: int, brief: dict, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Бесплатные правила по строкам очереди → (подсказки правил, остальное — для ИИ)."""
    campaign_ids = sorted({int(c) for r in rows for c in r["campaign_row_ids"]})
    group_ids = sorted({int(g) for r in rows for g in r["adgroup_row_ids"]})
    negatives = _negatives_index(user_id, campaign_ids, group_ids)
    memory = _memory_rules(workspace_id)
    brief_rules = _brief_rules(brief)
    labels: list[dict] = []
    rest: list[dict] = []
    index = None
    for r in rows:
        hit = rule_label(r, memory, brief_rules, negatives)
        if hit and hit.pop("_shared", False):
            # Общая память не знает ключей этого проекта: задевает ключи кампании запроса — решать ИИ или вам.
            if index is None:
                index = _keyword_index(user_id, campaign_ids)
            if find_conflicts(hit["negative_phrase"], hit["level"], r, index):
                hit = None
        if hit:
            labels.append({**hit, "query_norm": r["norm"], "query": r["query"]})
        else:
            rest.append(r)
    return labels, rest


def _classify_system(user_id: int, plan: dict) -> str:
    text = examples_prompt(memory_examples(plan["workspace_id"]))
    return _SYSTEM.format(
        brief=brief_prompt(plan["ws"]),
        keywords=_keywords_sample(_keyword_index(user_id, plan["campaign_ids"])),
        examples="Прошлые решения маркетолога — следуй им:\n" + text + "\n\n" if text else "",
    )


@contextmanager
def _classify_lock(workspace_id: int, progress: Progress) -> Iterator[None]:
    """Разметка проекта — одна за раз: иначе кнопка в проекте и «Разметить все проекты» шлют в ИИ одни и те же
    запросы и тратят лимит дважды. Advisory lock на сессии: соединение держим до конца разметки, транзакцию — нет.
    Занято дольше CLASSIFY_WAIT_SECONDS — ошибка busy (задача повторится сама)."""
    deadline = time.monotonic() + CLASSIFY_WAIT_SECONDS
    with pool.connection() as conn:
        while not conn.execute(
            "SELECT pg_try_advisory_lock(hashtext('query_classify'), %s::int) AS ok", (workspace_id,)
        ).fetchone()["ok"]:
            if time.monotonic() >= deadline:
                raise YandexError("busy", "Запросы этого проекта уже размечаются — дождитесь конца и повторите.")
            progress(0, 0, "Ждём: запросы проекта уже размечаются")
            time.sleep(CLASSIFY_POLL_SECONDS)
        conn.commit()
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('query_classify'), %s::int)", (workspace_id,))


def _unlabelled(workspace_id: int, batch: list[dict], relabel: bool) -> list[dict]:
    """Строки пачки, которые всё ещё ждут ИИ: пока шла разметка, часть могли решить вы или закрыть правила
    после загрузки. relabel — переразметка подсказок: пропускаем только решённые."""
    with pool.connection() as conn:
        taken = {
            r["query_norm"]
            for r in conn.execute(
                "SELECT query_norm FROM query_labels WHERE workspace_id = %s AND query_norm = ANY(%s)"
                + (" AND confirmed = TRUE" if relabel else ""),
                (workspace_id, [r["norm"] for r in batch]),
            ).fetchall()
        }
    return [r for r in batch if r["norm"] not in taken]


def classify_queries(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """Разметка очереди: сначала правила (Директ, память, бриф), остальное — ИИ пачками.

    ИИ — класс модели задачи «разметка» из админки. Перед каждой пачкой — проверка, что её оценка влезает
    в остаток лимита единиц: что успели — сохранено, остальное ждёт следующего периода.
    rules_only — только правила, без ИИ (разметка всех проектов после того, как ИИ упёрся в лимит).
    Одна разметка проекта за раз (_classify_lock); очередь берём уже под замком.
    """
    progress = progress or (lambda *_a: None)
    with _classify_lock(int(payload.get("workspace_id") or 0), progress):
        return _classify_queries(user_id, payload, progress)


def _classify_queries(user_id: int, payload: dict, progress: Progress) -> dict:
    plan = _classify_plan(user_id, payload)
    if plan is None:
        return {"labeled": 0, "negative": 0, "target": 0, "rules": 0, "ai": None, "left": 0, "usage": None}
    workspace_id, rows, labels, rest = plan["workspace_id"], plan["rows"], plan["labels"], plan["rest"]
    progress(0, len(rows), "Память решений и бриф")
    rules_n = len(labels)
    suggest_labels(user_id, workspace_id, labels)

    ai_used: bool | None = None
    ai_error = None
    usage = None
    if rest and not payload.get("rules_only"):
        try:
            cfg = ai.load_config(user_id, "classify", workspace_id)
        except YandexError as exc:
            cfg = None
            ai_used = False
            ai_error = ai.user_error(exc)
        if cfg is not None:
            ai_used = True
            system = _classify_system(user_id, plan)
            ratio = ai.chars_per_token(cfg)
            out_per = ai.out_tokens_per_unit(cfg, "classify", OUT_TOKENS_PER_QUERY)
            done = rules_n
            relabel = bool(payload.get("relabel"))
            with ai.meter() as spent:
                for start in range(0, len(rest), AI_BATCH):
                    chunk = rest[start : start + AI_BATCH]
                    progress(done, len(rows), f"ИИ размечает запросы: {done} из {len(rows)}")
                    done += len(chunk)
                    # Уже размеченные за это время (вами, правилами после загрузки) в ИИ не шлём.
                    batch = _unlabelled(workspace_id, chunk, relabel)
                    if not batch:
                        continue
                    try:
                        ai.check_budget(cfg, ai.units_for(cfg, *_batch_tokens(system, batch, ratio, out_per)))
                        got = ai_labels(cfg, system, batch)
                    except YandexError as exc:
                        # Что успели — сохранено; ошибку показываем, если не вышло ничего. Текст — для кабинета:
                        # сбой провайдера словами пользователя (ai.user_error), подробности — в ai_errors.
                        ai_error = ai.user_error(exc)
                        if not labels:
                            raise
                        break
                    suggest_labels(user_id, workspace_id, got)
                    labels += got
            usage = ai.usage_out(spent, user_id)
    progress(len(rows), len(rows), "Готово")
    with pool.connection() as conn:
        left = conn.execute(
            f"""
            WITH a AS ({_agg_sql()})
            SELECT COUNT(*) AS n FROM a LEFT JOIN query_labels l ON l.workspace_id = %s AND l.query_norm = a.norm
            WHERE l.id IS NULL
            """,
            (user_id, workspace_id, workspace_id),
        ).fetchone()
    return {
        "labeled": len(labels),
        "negative": sum(1 for x in labels if x["verdict"] == "negative"),
        "target": sum(1 for x in labels if x["verdict"] == "target"),
        "rules": rules_n,
        "ai": ai_used,
        "ai_error": ai_error,
        "left": int(left["n"]),
        "usage": usage,
    }


def estimate_classify(user_id: int, payload: dict) -> dict:
    """Оценка до запуска: сколько закроют правила, сколько уйдёт в ИИ и какая это доля месячного лимита.

    Токены входа — по символам тех же сообщений, что уйдут в модель, и фактическому числу символов
    на токен у этой модели; выхода — по среднему на запрос в прошлых разметках. Пользователю — только проценты.
    """
    out = {
        "queries": 0, "rules": 0, "ai": 0, "batches": 0, "percent": None, "left_percent": None,
        "calibrated": False, "fits": None, "ai_error": None,
    }
    plan = _classify_plan(user_id, payload)
    if plan is None:
        return out
    rest = plan["rest"]
    out.update(queries=len(plan["rows"]), rules=len(plan["labels"]), ai=len(rest))
    if not rest:
        return out
    try:
        cfg = ai.load_config(user_id, "classify", plan["workspace_id"])
    except YandexError as exc:
        out["ai_error"] = ai.user_error(exc)
        return out
    batches, needs, calibrated = _plan_needs(user_id, plan, cfg)
    out.update(batches=len(batches), percent=billing.share(user_id, sum(needs)), calibrated=calibrated)
    left = ai.budget_left(cfg)
    if left is not None:
        out["left_percent"] = billing.share(user_id, max(left, 0.0))
        # Пачки идут по порядку и останавливаются на первой, что не влезает в остаток.
        fits, acc = 0, 0.0
        for b, need in zip(batches, needs):
            acc += need
            if acc > left:
                break
            fits += len(b)
        out["fits"] = fits
    return out


def _plan_needs(user_id: int, plan: dict, cfg: dict) -> tuple[list[list[dict]], list[float], bool]:
    """Пачки ИИ по плану разметки и оценка каждой в единицах; calibrated — по фактическим замерам модели."""
    rest = plan["rest"]
    system = _classify_system(user_id, plan)
    ratio = ai.chars_per_token(cfg)
    out_per = ai.out_tokens_per_unit(cfg, "classify", OUT_TOKENS_PER_QUERY)
    batches = [rest[i : i + AI_BATCH] for i in range(0, len(rest), AI_BATCH)]
    needs = [ai.units_for(cfg, *_batch_tokens(system, b, ratio, out_per)) for b in batches]
    return batches, needs, ratio != ai.CHARS_PER_TOKEN or out_per != OUT_TOKENS_PER_QUERY


# --- Бесплатные правила после загрузки и разметка всех проектов ----------------------------------


def fill_rule_labels(user_id: int, workspace_id: int, labels: list[dict]) -> int:
    """Подсказки правил — только в пустые места: решения маркетолога и подсказки ИИ не трогаем."""
    if not labels:
        return 0
    cols: dict[str, list] = {k: [] for k in ("norm", "query", "verdict", "reason", "phrase", "level", "source")}
    for s in labels:
        cols["norm"].append(s["query_norm"])
        cols["query"].append(s.get("query") or s["query_norm"])
        cols["verdict"].append(s["verdict"])
        cols["reason"].append((s.get("reason") or "")[:300])
        cols["phrase"].append(s.get("negative_phrase") or "")
        cols["level"].append(s.get("level") if s.get("level") in LEVELS else "adgroup")
        cols["source"].append(s.get("source") or "memory")
    with pool.connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO query_labels (user_id, workspace_id, query_norm, query, verdict, reason,
                                      negative_phrase, level, source, confirmed, updated_at)
            SELECT %s, %s, t.norm, t.query, t.verdict, t.reason, t.phrase, t.level, t.source, FALSE, NOW()
            FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[])
                 AS t(norm, query, verdict, reason, phrase, level, source)
            ON CONFLICT (workspace_id, query_norm) DO NOTHING
            """,
            (user_id, workspace_id, *cols.values()),
        )
        return max(cur.rowcount, 0)


def label_by_rules(user_id: int, workspace_id: int) -> int:
    """Новые запросы проекта — через бесплатные правила (Директ, память решений, бриф): без ИИ и без лимита.

    Размечаем только запросы без метки, остальные не трогаем. → сколько подсказок добавили.
    """
    ws = require_workspace(user_id, workspace_id)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            WITH a AS ({_agg_sql()})
            SELECT a.* FROM a LEFT JOIN query_labels l ON l.workspace_id = %s AND l.query_norm = a.norm
            WHERE l.id IS NULL
            ORDER BY a.cost_kopecks DESC, a.clicks DESC, a.norm
            LIMIT %s
            """,
            (user_id, workspace_id, workspace_id, RULES_MAX),
        ).fetchall()
    if not rows:
        return 0
    labels, _rest = _rule_split(user_id, workspace_id, ws.get("brief") or {}, rows)
    return fill_rule_labels(user_id, workspace_id, labels)


def _loaded_workspaces(user_id: int, workspace_ids, campaign_row_ids, account_id) -> list[int]:
    """Открытые проекты, чьи запросы только что загрузили: по проектам, кампаниям или кабинету."""
    ids = {int(w) for w in workspace_ids or []}
    with pool.connection() as conn:
        if campaign_row_ids:
            ids |= {
                int(r["workspace_id"])
                for r in conn.execute(
                    "SELECT workspace_id FROM workspace_campaigns WHERE user_id = %s AND campaign_row_id = ANY(%s)",
                    (user_id, [int(c) for c in campaign_row_ids]),
                )
            }
        if account_id:
            ids |= {
                int(r["workspace_id"])
                for r in conn.execute(
                    """
                    SELECT DISTINCT wc.workspace_id FROM workspace_campaigns wc
                    JOIN direct_campaigns c ON c.id = wc.campaign_row_id
                    WHERE wc.user_id = %s AND c.account_id = %s
                    """,
                    (user_id, int(account_id)),
                )
            }
    if not ids:
        return []
    return sorted(int(w["id"]) for w in list_workspaces(user_id) if not w["locked"] and int(w["id"]) in ids)


def label_after_load(
    user_id: int, *, workspace_ids=None, campaign_row_ids=None, account_id: int | None = None
) -> int:
    """После загрузки запросов (кнопкой или по расписанию) — бесплатные правила по затронутым проектам.

    Без ИИ, без лимита и без изменений в Директе. Сбой правил загрузку не роняет — только в лог.
    """
    try:
        targets = _loaded_workspaces(user_id, workspace_ids, campaign_row_ids, account_id)
    except Exception:
        log.exception("rule labels after load: user %s", user_id)
        return 0
    n = 0
    for workspace_id in targets:
        try:
            n += label_by_rules(user_id, workspace_id)
        except Exception:
            log.exception("rule labels after load: workspace %s", workspace_id)
    return n


def _pending_projects(user_id: int) -> list[dict]:
    """Открытые проекты с запросами без разметки → [{id, name, new, cost}], сначала с большим расходом на них."""
    out = []
    for ws in list_workspaces(user_id):
        if ws["locked"]:
            continue
        with pool.connection() as conn:
            row = conn.execute(
                f"""
                WITH a AS ({_agg_sql()})
                SELECT COUNT(*) AS n, COALESCE(SUM(a.cost_kopecks), 0) AS cost
                FROM a LEFT JOIN query_labels l ON l.workspace_id = %s AND l.query_norm = a.norm
                WHERE l.id IS NULL
                """,
                (user_id, ws["id"], ws["id"]),
            ).fetchone()
        if int(row["n"]):
            out.append({"id": int(ws["id"]), "name": ws["name"], "new": int(row["n"]), "cost": int(row["cost"])})
    out.sort(key=lambda p: (-p["cost"], p["name"].lower(), p["id"]))
    return out


def estimate_classify_all(user_id: int) -> dict:
    """Оценка «Разметить все проекты» без вызова ИИ: по проектам с новыми запросами — сколько закроют правила,
    сколько уйдёт в ИИ и какая это доля месячного лимита; сколько проектов успеем при нынешнем остатке.

    Порядок — тот же, что у задачи: пачки идут подряд и останавливаются на первой, что не влезает в остаток;
    дальше — только правила. Пользователю — только проценты.
    """
    out = {
        "projects": [], "queries": 0, "rules": 0, "ai": 0, "percent": None, "left_percent": None,
        "fits_projects": 0, "fits_queries": 0, "calibrated": False, "ai_error": None, "per_project": ALL_LIMIT,
    }
    pending = _pending_projects(user_id)
    if not pending:
        return out
    cfg, room = None, None
    try:
        cfg = ai.load_config(user_id, "classify")
        room = ai.budget_left(cfg)
        ai.check_budget(cfg)
    except YandexError as exc:
        out["ai_error"] = ai.user_error(exc)
        room = 0.0
    spent = total = 0.0
    stopped = False
    for p in pending:
        plan = _classify_plan(user_id, {"workspace_id": p["id"], "limit": ALL_LIMIT})
        if plan is None:
            continue
        # queries — сколько возьмём за этот запуск (не больше ALL_LIMIT), new — сколько всего ждёт разметки.
        item = {
            "id": p["id"], "name": p["name"], "queries": len(plan["rows"]), "new": max(p["new"], len(plan["rows"])),
            "rules": len(plan["labels"]), "ai": len(plan["rest"]), "percent": None, "fits": True,
        }
        done = item["rules"]
        if plan["rest"] and cfg is not None:
            batches, needs, calibrated = _plan_needs(user_id, plan, cfg)
            out["calibrated"] = out["calibrated"] or calibrated
            total += sum(needs)
            item["percent"] = billing.share(user_id, sum(needs))
            for b, need in zip(batches, needs):
                if stopped or (room is not None and spent + need > room):
                    stopped = True
                    break
                spent += need
                done += len(b)
        item["fits"] = done == item["queries"]
        out["projects"].append(item)
        out["queries"] += item["queries"]
        out["rules"] += item["rules"]
        out["ai"] += item["ai"]
        out["fits_queries"] += done
        out["fits_projects"] += int(item["fits"])
    if cfg is not None and out["ai"]:
        out["percent"] = billing.share(user_id, total)
    if room is not None:
        out["left_percent"] = billing.share(user_id, max(room, 0.0))
    return out


def ai_stopped(exc: YandexError) -> bool:
    """Сбой самого ИИ или лимита — повторять ИИ в следующих проектах бессмысленно."""
    code = exc.code or ""
    return code.startswith("ai-") or ai.is_provider_error(code) or code in AI_STOP_ERRORS


def classify_all_queries(user_id: int, payload: dict | None = None, progress: Progress | None = None) -> dict:
    """Задача «Разметить все проекты»: по каждому проекту с новыми запросами — та же разметка, что кнопкой в проекте.

    Порядок — как в оценке. Как только ИИ упёрся в лимит или сбоил, остальные проекты размечаем только
    правилами: лимит не перерасходуем и сбой не повторяем. Проекты без новых запросов пропускаем.
    """
    progress = progress or (lambda *_a: None)
    pending = _pending_projects(user_id)
    total = len(pending)
    sums = {"labeled": 0, "negative": 0, "target": 0, "rules": 0, "left": 0}
    items: list[dict] = []
    ai_error = None
    with ai.meter() as spent:
        for n, p in enumerate(pending):
            name = p["name"]

            def step(_done: int, _total: int, text: str, n=n, name=name) -> None:
                progress(n, total, f"{name} — {text}")

            step(0, 0, "Готовим очередь")
            try:
                res = classify_queries(
                    user_id,
                    {"workspace_id": p["id"], "limit": ALL_LIMIT, "rules_only": ai_error is not None},
                    step,
                )
            except YandexError as exc:
                # Сбой ИИ или лимита: дальше — только правила. Беда одного проекта (удалили, закрыли, занят) —
                # его пропускаем, а ИИ для остальных не выключаем.
                if ai_stopped(exc):
                    ai_error = ai_error or ai.user_error(exc)
                items.append({"id": p["id"], "name": name, "labeled": 0, "left": p["new"], "error": True})
                sums["left"] += p["new"]
                continue
            if res.get("ai_error"):
                ai_error = ai_error or res["ai_error"]
            for key in sums:
                sums[key] += int(res.get(key) or 0)
            items.append({"id": p["id"], "name": name, "labeled": int(res["labeled"]), "left": int(res["left"])})
    progress(total, total, "Готово")
    return {
        **sums,
        "projects": sum(1 for x in items if x["labeled"]),
        "skipped": sum(1 for x in items if x.get("error")),
        "ai_error": ai_error,
        "usage": ai.usage_out(spent, user_id),
        "items": items,
    }


# --- Применение решений -------------------------------------------------------------------------


def apply_negative_entries(user_id: int, entries: list[dict], *, force: bool = False) -> tuple[dict, dict]:
    """
    entries — [{"key", "phrase", "level", "campaign_row_ids", "adgroup_row_ids"}].
    → (результат patch_negatives, {key: {"status", "message", "conflicts", "level", "phrase"}}).
    """
    outcomes: dict = {}
    camp_changes: dict[int, dict] = defaultdict(lambda: {"add": []})
    group_changes: dict[int, dict] = defaultdict(lambda: {"add": []})
    placed: dict = {}
    index = None
    if not force:
        index = _keyword_index(user_id, sorted({int(c) for e in entries for c in e.get("campaign_row_ids") or []}))
    for e in entries:
        phrase = clean_negative(e.get("phrase") or "")
        problems = phrase_problems([phrase]) if phrase else ["Пустая минус-фраза"]
        level = effective_level(e.get("level"), e)
        if problems:
            outcomes[e["key"]] = {"status": "error", "message": problems[0], "phrase": phrase, "level": level}
            continue
        if index is not None:
            conflicts = find_conflicts(phrase, level, e, index)
            if conflicts:
                outcomes[e["key"]] = {
                    "status": "skipped",
                    "message": _upper_first(conflict_note(conflicts)),
                    "conflicts": [_conflict_out(k) for k in conflicts[:5]],
                    "phrase": phrase,
                    "level": level,
                }
                continue
        if level == "adgroup":
            ids = [("adgroups", int(g)) for g in e["adgroup_row_ids"]]
        else:
            ids = [("campaigns", int(c)) for c in e.get("campaign_row_ids") or []]
        for kind, rid in ids:
            bucket = (group_changes if kind == "adgroups" else camp_changes)[rid]
            if phrase not in bucket["add"]:
                bucket["add"].append(phrase)
        placed[e["key"]] = (ids, phrase, level)
    result = patch_negatives(user_id, campaigns=dict(camp_changes), adgroups=dict(group_changes))
    failed = {}
    for s in result["skipped"]:
        failed[(s["kind"], int(s["id"]))] = REASONS.get(s["reason"], s["reason"])
    for err in result["errors"]:
        failed[(err["kind"], int(err["id"]))] = err.get("message") or "ошибка Директа"
    for key, (ids, phrase, level) in placed.items():
        bad = [failed[t] for t in ids if t in failed]
        if not ids or len(bad) == len(ids):
            status = "error"
        else:
            status = "partial" if bad else "ok"
        outcomes[key] = {"status": status, "message": bad[0] if bad else "", "phrase": phrase, "level": level}
    return result, outcomes


def add_target_keywords(user_id: int, by_group: dict[int, list[tuple[str, str]]], bid_rub) -> tuple[list[int], dict]:
    """Целевые запросы → ключевые фразы групп. → (id созданных фраз, {key: (status, message)})."""
    from app.keywords import create_keywords

    created_ids: list[int] = []
    outcomes: dict = {}
    for group_id, pairs in by_group.items():
        for start in range(0, len(pairs), 100):
            chunk = pairs[start : start + 100]
            try:
                with journal_quiet():
                    res = create_keywords(user_id, adgroup_row_id=group_id, phrases=[p for _k, p in chunk], bid_rub=bid_rub)
            except YandexError as exc:
                for key, _p in chunk:
                    outcomes[key] = ("error", exc.message or exc.code)
                continue
            made = {phrase_key(i.get("keyword") or ""): i["id"] for i in res.get("items") or []}
            created_ids += list(made.values())
            for key, phrase in chunk:
                ok = phrase_key(phrase) in made
                outcomes[key] = ("ok", "") if ok else ("error", "Директ не добавил фразу")
    return created_ids, outcomes


def _clean_items(items) -> list[dict]:
    out, seen = [], set()
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        norm = str(raw.get("query_norm") or "").strip()
        verdict = raw.get("verdict")
        if not norm or verdict not in VERDICTS or norm in seen:
            continue
        seen.add(norm)
        out.append(
            {
                "query_norm": norm,
                "verdict": verdict,
                "negative_phrase": str(raw.get("negative_phrase") or "").strip(),
                "level": raw.get("level") if raw.get("level") in LEVELS else "adgroup",
                "add_keyword": bool(raw.get("add_keyword")),
                "adgroup_row_id": int(raw["adgroup_row_id"]) if raw.get("adgroup_row_id") else None,
            }
        )
    if not out:
        raise YandexError("empty", "Не выбрано ни одного запроса")
    if len(out) > APPLY_MAX:
        raise YandexError("limit", f"Не больше {APPLY_MAX} запросов за раз")
    return out


def needs_direct(items) -> bool:
    """Нужны ли вызовы Директа (иначе решение можно сохранить сразу, без задачи)."""
    return any(
        isinstance(i, dict) and (i.get("verdict") == "negative" or i.get("add_keyword")) for i in items or []
    )


def apply_decisions(
    user_id: int,
    workspace_id: int,
    items: list[dict],
    *,
    force: bool = False,
    bid_rub=None,
    progress: Progress | None = None,
) -> dict:
    """Подтверждённые решения: минус-фразы — в Директ, целевые — в память (и в ключи, если просили)."""
    progress = progress or (lambda *_a: None)
    require_workspace(user_id, workspace_id)
    items = _clean_items(items)
    rows = _rows_for(user_id, workspace_id, [i["query_norm"] for i in items])
    out: dict[str, dict] = {}
    entries = []
    for item in items:
        row = rows.get(item["query_norm"])
        if row is None:
            out[item["query_norm"]] = {"status": "error", "message": "Запроса нет в отчёте — обновите запросы"}
            continue
        if item["verdict"] == "negative":
            entries.append(
                {
                    "key": item["query_norm"],
                    "phrase": item["negative_phrase"] or row["query"],
                    "level": item["level"],
                    "campaign_row_ids": row["campaign_row_ids"],
                    "adgroup_row_ids": row["adgroup_row_ids"],
                }
            )
    total = len(items)
    result = {"campaigns": {}, "adgroups": {}, "skipped": [], "errors": []}
    if entries:
        progress(0, total, "Минус-фразы в Директ")
        result, neg_out = apply_negative_entries(user_id, entries, force=force)
        out.update(neg_out)

    # Целевые: в ключевые фразы группы, если просили.
    by_group: dict[int, list[tuple[str, str]]] = defaultdict(list)
    allowed_groups = None
    for item in items:
        row = rows.get(item["query_norm"])
        if item["verdict"] != "target" or row is None:
            continue
        if not item["add_keyword"]:
            out[item["query_norm"]] = {"status": "ok", "message": ""}
            continue
        group_id = item["adgroup_row_id"] or (row["adgroup_row_ids"][0] if row["adgroup_row_ids"] else None)
        if item["adgroup_row_id"]:
            if allowed_groups is None:
                allowed_groups = _workspace_groups(user_id, workspace_id)
            if group_id not in allowed_groups:
                group_id = None
        phrase = keyword_text(row["query"])
        if not group_id:
            out[item["query_norm"]] = {"status": "error", "message": "Не выбрана группа для ключевой фразы"}
        elif len(phrase.split()) > MAX_WORDS:
            out[item["query_norm"]] = {"status": "error", "message": f"В ключевой фразе больше {MAX_WORDS} слов"}
        else:
            by_group[int(group_id)].append((item["query_norm"], phrase))
    created_ids: list[int] = []
    if by_group:
        progress(len(entries), total, "Ключевые фразы")
        created_ids, kw_out = add_target_keywords(user_id, by_group, bid_rub)
        for key, (status, message) in kw_out.items():
            out[key] = {"status": status, "message": message}

    decisions = []
    for item in items:
        res = out.get(item["query_norm"]) or {}
        if res.get("status") not in ("ok", "partial"):
            continue
        row = rows[item["query_norm"]]
        decisions.append(
            {
                "query_norm": item["query_norm"],
                "query": row["query"],
                "verdict": item["verdict"],
                "negative_phrase": res.get("phrase") or item["negative_phrase"],
                "level": res.get("level") or item["level"],
                "applied": item["verdict"] == "negative" or item["add_keyword"],
            }
        )
    previous = snapshot_labels(workspace_id, [d["query_norm"] for d in decisions]) if decisions else []
    if decisions:
        remember_decisions(user_id, workspace_id, decisions)

    statuses = [r["status"] for r in out.values()]
    ok_n = sum(1 for s in statuses if s in ("ok", "partial"))
    status = "ok" if ok_n == len(statuses) else ("partial" if ok_n else "error")
    negative_ok = sum(1 for d in decisions if d["verdict"] == "negative")
    target_ok = len(decisions) - negative_ok
    log_id = None
    if decisions or any(s == "error" for s in statuses):
        log_id = log_query_action(
            user_id,
            workspace_id,
            title=_apply_title(negative_ok, target_ok, len(created_ids)),
            payload={"query_norms": [i["query_norm"] for i in items][:50], "force": force},
            summary={
                "negative": negative_ok,
                "target": target_ok,
                "keywords": len(created_ids),
                "skipped": sum(1 for s in statuses if s == "skipped"),
                "errors": sum(1 for s in statuses if s == "error"),
            },
            status=status,
            negatives=result,
            created_keyword_ids=created_ids,
            previous_labels={workspace_id: previous},
            campaign_row_ids=sorted({int(c) for r in rows.values() for c in r["campaign_row_ids"]}),
        )
    progress(total, total, "Готово")
    return {
        "items": [{"query_norm": k, **v} for k, v in out.items()],
        "negative": negative_ok,
        "target": target_ok,
        "keywords": len(created_ids),
        "skipped": sum(1 for s in statuses if s == "skipped"),
        "errors": sum(1 for s in statuses if s == "error"),
        "status": status,
        "log_id": log_id,
    }


def _workspace_groups(user_id: int, workspace_id: int) -> set[int]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT g.id FROM direct_adgroups g
            JOIN workspace_campaigns wc ON wc.campaign_row_id = g.campaign_row_id
            WHERE wc.workspace_id = %s AND g.user_id = %s
            """,
            (workspace_id, user_id),
        ).fetchall()
    return {int(r["id"]) for r in rows}


def _apply_title(negative: int, target: int, keywords: int) -> str:
    parts = []
    if negative:
        parts.append(f"в минус {negative}")
    if target:
        parts.append(f"целевых {target}")
    if keywords:
        parts.append(f"в ключи {keywords}")
    return "Чистка запросов: " + (", ".join(parts) or "без изменений")


def log_query_action(
    user_id: int,
    workspace_id: int | None,
    *,
    title: str,
    payload: dict,
    summary: dict,
    status: str,
    negatives: dict,
    created_keyword_ids: list[int],
    previous_labels: dict[int, list[dict]],
    campaign_row_ids: list[int],
) -> int | None:
    steps = []
    neg_args = inverse(negatives)
    if neg_args:
        steps.append({"op": "negatives", "args": neg_args})
    if created_keyword_ids:
        steps.append({"op": "keywords_action", "args": {"keyword_row_ids": created_keyword_ids, "action": "delete"}})
    for ws_id, labels in previous_labels.items():
        if labels:
            steps.append({"op": "query_labels_restore", "args": {"workspace_id": ws_id, "labels": labels}})
    return log_action(
        user_id,
        "queries",
        {**payload, "campaign_row_ids": campaign_row_ids},
        summary,
        status,
        workspace_id=workspace_id,
        title=title,
        undo={"label": "Вернуть как было", "steps": steps},
        targets=targets_of(negatives) + [f"keyword:{i}" for i in created_keyword_ids] + [f"campaign:{c}" for c in campaign_row_ids],
    )


# --- Пересечение минус-фразы с ключами: готовые выходы --------------------------------------------

# Сколько уточнённых фраз предлагать и сколько ключей за раз брать в кавычки.
RESOLVE_NARROW_MAX = 3
RESOLVE_KEYWORDS_MAX = 5
_STALE = "Этот вариант уже не подходит — откройте выбор заново"


def quoted_keyword(raw: str) -> str | None:
    """«окна пвх -бесплатно» → «"окна пвх" -бесплатно». None — фраза уже в кавычках или скобках."""
    parts = _MINUS_TAIL.split(str(raw or "").strip())
    main = parts[0].strip()
    if not main or main.startswith("---") or any(ch in main for ch in '"[]'):
        return None
    return f'"{main}"' + "".join(f" -{t.strip()}" for t in parts[1:] if t.strip())


def has_extra_word(keyword_stems: list[str], query_stems: list[str]) -> bool:
    """В запросе есть значимое слово, которого нет в ключе, — ключ в кавычках такой запрос не возьмёт."""
    return any(not any(_same(q, k) for k in keyword_stems) for q in query_stems)


def narrow_candidates(phrase: str, query: str) -> list[str]:
    """Фраза + одно слово из запроса, которого в ней ещё нет (в порядке слов запроса, без стоп-слов)."""
    neg = stems(phrase)
    out, seen = [], set()
    for word in _WORD.findall(str(query or "").lower().replace("ё", "е")):
        if word in STOP_WORDS:
            continue
        s = stem(word)
        if s in seen or any(_same(s, n) for n in neg):
            continue
        seen.add(s)
        out.append(f"{phrase} {word}")
    return out


def _group_keywords(user_id: int, group_ids: list[int]) -> list[dict]:
    """Ключи групп как есть в Директе (с операторами) — для правки кавычками."""
    if not group_ids:
        return []
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT k.id, k.keyword, k.adgroup_row_id, g.name AS adgroup_name
            FROM direct_keywords k JOIN direct_adgroups g ON g.id = k.adgroup_row_id
            WHERE k.user_id = %s AND k.adgroup_row_id = ANY(%s) AND COALESCE(k.state, '') <> 'ARCHIVED'
            ORDER BY k.id
            """,
            (user_id, list(group_ids)),
        ).fetchall()
    out = []
    for r in rows:
        raw = str(r["keyword"] or "")
        text = keyword_text(raw)
        if raw.startswith("---") or not text:
            continue
        out.append({**r, "text": text, "stems": stems(text)})
    return out


def _source_keywords(row: dict, keywords: list[dict]) -> list[dict]:
    """Ключи, по которым пришёл запрос: из отчёта Директа, а если его нет — ключи групп, чьи слова все есть в запросе."""
    matched = {phrase_key(keyword_text(m)) for m in row.get("matched") or [] if m}
    found = [k for k in keywords if phrase_key(k["text"]) in matched]
    if not found:
        q_stems = stems(row["query"])
        found = [k for k in keywords if blocks_stems(k["stems"], q_stems)]
    return found


def _lost_queries(user_id: int, workspace_id: int, keywords: list[dict], skip_norm: str) -> dict:
    """Какие ещё запросы проекта перестанут приходить, если ключи взять в кавычки (запросы с лишними словами)."""
    if not keywords:
        return {"total": 0, "with_conversions": 0, "examples": []}
    by_text = {(int(k["adgroup_row_id"]), phrase_key(k["text"])): k for k in keywords}
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {NORM_SQL} AS norm, MIN(q.query) AS query, q.adgroup_row_id, q.matched_keyword,
                   SUM(q.conversions)::bigint AS conversions
            FROM direct_search_queries q
            JOIN workspace_campaigns wc ON wc.campaign_row_id = q.campaign_row_id
            WHERE q.user_id = %s AND wc.workspace_id = %s AND q.adgroup_row_id = ANY(%s)
            GROUP BY 1, 3, 4
            """,
            (user_id, workspace_id, sorted({int(k["adgroup_row_id"]) for k in keywords})),
        ).fetchall()
    lost: dict[str, dict] = {}
    for r in rows:
        k = by_text.get((int(r["adgroup_row_id"]), phrase_key(keyword_text(r["matched_keyword"] or ""))))
        if k is None or r["norm"] == skip_norm or not has_extra_word(k["stems"], stems(r["norm"])):
            continue
        item = lost.setdefault(r["norm"], {"query": r["query"], "conversions": 0})
        item["conversions"] += int(r["conversions"] or 0)
    items = sorted(lost.values(), key=lambda i: (-i["conversions"], i["query"]))
    return {
        "total": len(items),
        "with_conversions": sum(1 for i in items if i["conversions"]),
        "examples": [i["query"] for i in items[:3]],
    }


def _label_of(workspace_id: int, norm: str) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT verdict, negative_phrase, level FROM query_labels WHERE workspace_id = %s AND query_norm = %s",
            (workspace_id, norm),
        ).fetchone()


def _hits_out(hits: list[dict]) -> dict:
    return {"targets_hit": hits[:3], "targets_total": len(hits)}


def conflict_options(
    user_id: int, workspace_id: int, query_norm: str, *, negative_phrase: str | None = None, level: str | None = None
) -> dict:
    """
    Минус-фраза задевает ключи — готовые выходы с тем, что получится:
    groups — та же фраза только в группы запроса, где задетого ключа нет;
    narrow — фраза плюс слово из запроса, которая ключи уже не задевает (та же проверка find_conflicts);
    keyword — минус не ставить, а взять в кавычки ключи, по которым пришёл запрос (это правка ключей в Директе).
    """
    require_workspace(user_id, workspace_id)
    norm = phrase_key(query_norm or "")
    row = _rows_for(user_id, workspace_id, [norm]).get(norm)
    if row is None:
        raise YandexError("not-found", "Запроса нет в отчёте — обновите запросы")
    label = _label_of(workspace_id, norm) or {}
    from_label = label.get("negative_phrase") if label.get("verdict") == "negative" else ""
    phrase = clean_negative(str(negative_phrase or "").strip() or from_label or row["query"])
    level = effective_level(level or label.get("level"), row)
    index = _keyword_index(user_id, [int(c) for c in row["campaign_row_ids"]])
    conflicts = find_conflicts(phrase, level, row, index) if phrase else []
    out = {
        "query_norm": norm,
        "query": row["query"],
        "phrase": phrase,
        "level": level,
        "conflicts": [_conflict_out(k) for k in conflicts[:5]],
        "conflicts_total": len(conflicts),
        "options": [],
    }
    if not conflicts:
        return out
    kept = _kept_queries(user_id, workspace_id)
    skip = {norm}
    groups = [int(g) for g in row["adgroup_row_ids"] or []]
    names: dict[int, str] = {}
    if groups:
        with pool.connection() as conn:
            for r in conn.execute("SELECT id, name FROM direct_adgroups WHERE id = ANY(%s)", (groups,)).fetchall():
                names[int(r["id"])] = r["name"] or ""

    # 1. Только в группы, где задетого ключа нет (и фразы там ещё нет — иначе повтор ничего не добавит).
    has = set(_groups_with(groups, phrase, _negatives_index(user_id, [], groups))) if groups else set()
    safe, left_out = [], []
    for g in groups:
        hits = find_conflicts(phrase, "adgroup", {**row, "adgroup_row_ids": [g]}, index)
        if hits:
            left_out.append({"id": g, "name": names.get(g, ""), "keywords": [k["keyword"] for k in hits[:3]]})
        elif g not in has:
            safe.append(g)
    if safe:
        out["options"].append(
            {
                "key": "groups",
                "kind": "groups",
                "phrase": phrase,
                "level": "adgroup",
                "adgroup_row_ids": safe,
                "groups": [{"id": g, "name": names.get(g, "")} for g in safe],
                "left_out": left_out,
                **_hits_out(_kept_hits(phrase, "adgroup", {**row, "adgroup_row_ids": safe}, kept, skip)),
            }
        )

    # 2. Уточнить фразу словом из запроса.
    narrowed = 0
    for candidate in narrow_candidates(phrase, row["query"]):
        if narrowed >= RESOLVE_NARROW_MAX:
            break
        if phrase_problems([candidate]) or find_conflicts(candidate, level, row, index):
            continue
        narrowed += 1
        out["options"].append(
            {
                "key": f"narrow:{phrase_key(candidate)}",
                "kind": "narrow",
                "phrase": candidate,
                "level": level,
                **_hits_out(_kept_hits(candidate, level, row, kept, skip)),
            }
        )

    # 3. Ключи, по которым пришёл запрос, — в кавычки (минус не нужен).
    sources = _source_keywords(row, _group_keywords(user_id, groups))
    q_stems = stems(row["query"])
    if 0 < len(sources) <= RESOLVE_KEYWORDS_MAX and all(
        quoted_keyword(k["keyword"]) and has_extra_word(k["stems"], q_stems) for k in sources
    ):
        out["options"].append(
            {
                "key": "keyword",
                "kind": "keyword",
                "keywords": [
                    {
                        "keyword_row_id": k["id"],
                        "keyword": k["keyword"],
                        "new_keyword": quoted_keyword(k["keyword"]),
                        "adgroup_row_id": k["adgroup_row_id"],
                        "adgroup_name": k["adgroup_name"] or "",
                    }
                    for k in sources
                ],
                "lost": _lost_queries(user_id, workspace_id, sources, norm),
            }
        )
    return out


def resolve_conflict(
    user_id: int,
    workspace_id: int,
    query_norm: str,
    option: str,
    *,
    negative_phrase: str | None = None,
    level: str | None = None,
) -> dict:
    """Применить выбранный выход. Варианты пересчитываются заново — клиенту не верим на слово."""
    found = conflict_options(user_id, workspace_id, query_norm, negative_phrase=negative_phrase, level=level)
    opt = next((o for o in found["options"] if o["key"] == option), None)
    if opt is None:
        raise YandexError("stale", _STALE)
    norm = found["query_norm"]
    row = _rows_for(user_id, workspace_id, [norm])[norm]
    campaign_ids = sorted(int(c) for c in row["campaign_row_ids"])
    if opt["kind"] == "keyword":
        return _resolve_by_keywords(user_id, workspace_id, found, opt, campaign_ids)

    entry = {
        "key": norm,
        "phrase": opt["phrase"],
        "level": opt["level"],
        "campaign_row_ids": row["campaign_row_ids"],
        "adgroup_row_ids": opt.get("adgroup_row_ids") or row["adgroup_row_ids"],
    }
    result, outcomes = apply_negative_entries(user_id, [entry])
    res = outcomes[norm]
    applied = res["status"] in ("ok", "partial")
    previous = snapshot_labels(workspace_id, [norm]) if applied else []
    if applied:
        remember_decisions(
            user_id,
            workspace_id,
            [
                {
                    "query_norm": norm,
                    "query": row["query"],
                    "verdict": "negative",
                    "negative_phrase": res["phrase"],
                    "level": res["level"],
                    "applied": True,
                }
            ],
        )
    where = "в группы без задетых ключей" if opt["kind"] == "groups" else "без пересечения с ключами"
    log_id = log_query_action(
        user_id,
        workspace_id,
        title=f"Минус-фраза «{res['phrase']}» {where}",
        payload={"query_norms": [norm], "resolve": opt["kind"]},
        summary={"negative": 1 if applied else 0, "target": 0, "keywords": 0, "skipped": 0, "errors": 0 if applied else 1},
        status=res["status"] if res["status"] in ("ok", "partial") else "error",
        negatives=result,
        created_keyword_ids=[],
        previous_labels={workspace_id: previous},
        campaign_row_ids=campaign_ids,
    )
    return {
        "query_norm": norm,
        "kind": opt["kind"],
        "status": res["status"],
        "message": res.get("message") or "",
        "phrase": res["phrase"],
        "level": res["level"],
        "log_id": log_id,
    }


def _resolve_by_keywords(user_id: int, workspace_id: int, found: dict, opt: dict, campaign_ids: list[int]) -> dict:
    """Ключи — в кавычки, каждый отдельной правкой в Директе; одна запись журнала с откатом всех правок."""
    from app.keywords import update_keyword

    norm = found["query_norm"]
    done: list[dict] = []
    message = ""
    for k in opt["keywords"]:
        try:
            with journal_quiet():
                update_keyword(user_id, int(k["keyword_row_id"]), keyword=k["new_keyword"])
        except YandexError as exc:
            message = exc.message or exc.code
            break
        done.append(k)
    status = "ok" if len(done) == len(opt["keywords"]) else ("partial" if done else "error")
    previous = snapshot_labels(workspace_id, [norm]) if done else []
    if done:
        # Запрос решён, но минус-фразы в Директе нет: фразу не запоминаем — иначе память подсказывала бы
        # её другим запросам, ИИ брал бы в примеры, а «убрано за 30 дней» считало бы лишнюю фразу.
        remember_decisions(
            user_id,
            workspace_id,
            [
                {
                    "query_norm": norm,
                    "query": found["query"],
                    "verdict": "negative",
                    "negative_phrase": "",
                    "level": found["level"],
                    "reason": KEYWORD_QUOTED_REASON,
                    "applied": False,
                }
            ],
        )
    steps = [{"op": "keyword_update", "args": {"keyword_row_id": int(k["keyword_row_id"]), "keyword": k["keyword"]}} for k in done]
    if previous:
        steps.append({"op": "query_labels_restore", "args": {"workspace_id": workspace_id, "labels": previous}})
    first = done[0] if done else opt["keywords"][0]
    more = f" и ещё {len(opt['keywords']) - 1}" if len(opt["keywords"]) > 1 else ""
    log_id = log_action(
        user_id,
        "queries",
        {"query_norms": [norm], "resolve": "keyword", "campaign_row_ids": campaign_ids},
        {"keywords_changed": len(done), "errors": 0 if status == "ok" else 1},
        status,
        workspace_id=workspace_id,
        title=f"Ключ «{first['keyword']}»{more} в кавычки вместо минус-фразы «{found['phrase']}»",
        undo={"label": "Вернуть как было", "steps": steps},
        targets=[f"keyword:{int(k['keyword_row_id'])}" for k in opt["keywords"]] + [f"campaign:{c}" for c in campaign_ids],
    )
    return {
        "query_norm": norm,
        "kind": "keyword",
        "status": status,
        "message": message,
        "keywords": [{"keyword_row_id": k["keyword_row_id"], "keyword": k["new_keyword"]} for k in done],
        "log_id": log_id,
    }


def sync_workspace_queries(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """Задача: свежие поисковые запросы всех поисковых кампаний проекта."""
    from app.search_queries import sync_campaign_search_queries

    progress = progress or (lambda *_a: None)
    workspace_id = int(payload.get("workspace_id") or 0)
    require_workspace(user_id, workspace_id)
    days = max(1, min(int(payload.get("days") or 14), 90))
    campaigns = [c for c in workspace_search_campaigns(user_id, workspace_id) if c["state"] != "ARCHIVED"]
    if not campaigns:
        raise YandexError("empty", "В проекте нет поисковых кампаний")
    queries = 0
    errors: list[dict] = []
    first_error: YandexError | None = None
    for n, camp in enumerate(campaigns):
        progress(n, len(campaigns), f"Запросы: {camp['name']}")
        try:
            queries += int(sync_campaign_search_queries(user_id, int(camp["id"]), days=days).get("queries") or 0)
        except YandexError as exc:
            first_error = first_error or exc
            errors.append({"campaign_row_id": camp["id"], "name": camp["name"], "error": exc.message or exc.code})
    if first_error is not None and len(errors) == len(campaigns):
        raise first_error
    progress(len(campaigns), len(campaigns), "Готово")
    return {"campaigns": len(campaigns), "queries": queries, "errors": errors[:20], "days": days}
