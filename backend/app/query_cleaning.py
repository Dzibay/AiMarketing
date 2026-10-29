"""Чистка поисковых запросов пространства: очередь, разметка ИИ, конфликты с ключами, память решений.

Запросы всех кампаний пространства сводятся в одну очередь по нормализованному тексту
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
from collections import defaultdict
from collections.abc import Callable

from app import ai
from app.adgroups import _parse_phrases
from app.brief import brief_prompt
from app.db import pool
from app.journal import journal_quiet, log_action
from app.negative_lists import MAX_WORDS, phrase_key, phrase_problems
from app.negatives import REASONS, inverse, patch_negatives, targets_of
from app.workspaces import require_workspace
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
LEVELS = ("adgroup", "campaign")
AI_BATCH = 40
CLASSIFY_MAX = 500
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


def workspace_search_campaigns(user_id: int, workspace_id: int) -> list[dict]:
    """Кампании пространства, у которых бывают поисковые запросы, — для фильтра и загрузки."""
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
            conflicts = find_conflicts(r["negative_phrase"], label["level"], r, ctx["keywords"])
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


def check_phrases(user_id: int, workspace_id: int, items: list[dict]) -> list[dict]:
    """Проверка правленых минус-фраз перед применением: ошибки формата и конфликты с ключами."""
    require_workspace(user_id, workspace_id)
    items = [i for i in items or [] if isinstance(i, dict) and i.get("query_norm")][:APPLY_MAX]
    rows = _rows_for(user_id, workspace_id, [str(i["query_norm"]) for i in items])
    index = _keyword_index(user_id, sorted({int(c) for r in rows.values() for c in r["campaign_row_ids"]}))
    out = []
    for item in items:
        row = rows.get(str(item["query_norm"]))
        # Как при применении: пустая фраза — значит, минусуем сам запрос.
        phrase = clean_negative(str(item.get("negative_phrase") or "").strip() or (row or {}).get("query") or "")
        level = effective_level(item.get("level"), row)
        problems = phrase_problems([phrase]) if phrase else ["Пустая минус-фраза"]
        conflicts = find_conflicts(phrase, level, row, index) if row and phrase else []
        out.append(
            {
                "query_norm": item["query_norm"],
                "negative_phrase": phrase,
                "problems": problems,
                "conflicts": [_conflict_out(k) for k in conflicts[:5]],
                "conflicts_total": len(conflicts),
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
                                      WHEN query_labels.verdict = EXCLUDED.verdict THEN query_labels.applied_at END,
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
                ORDER BY updated_at DESC LIMIT %s
                """,
                (workspace_id, verdict, limit_each),
            ).fetchall()
    return out


def examples_prompt(examples: dict[str, list[dict]]) -> str:
    lines = []
    for r in examples.get("negative") or []:
        lines.append(f"- «{r['query']}» → минус «{r['negative_phrase'] or r['query']}»")
    for r in examples.get("target") or []:
        lines.append(f"- «{r['query']}» → целевой")
    return "\n".join(lines)


# --- Разметка: правила и ИИ ---------------------------------------------------------------------


def _memory_rules(workspace_id: int) -> list[tuple[str, list[str], str, str]]:
    """Подтверждённые минус-фразы пространства → (фраза, основы, уровень, исходный запрос)."""
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
    return [(r["negative_phrase"], stems(r["negative_phrase"]), r["level"], r["query"]) for r in rows]


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
    for phrase, neg, level, query in memory:
        if blocks_stems(neg, query_stems):
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
    group_ids = sorted({int(g) for r in rows for g in r["adgroup_row_ids"]})
    negatives = _negatives_index(user_id, campaign_ids, group_ids)
    memory = _memory_rules(workspace_id)
    brief_rules = _brief_rules(ws.get("brief") or {})
    labels: list[dict] = []
    rest: list[dict] = []
    for r in rows:
        hit = rule_label(r, memory, brief_rules, negatives)
        if hit:
            labels.append({**hit, "query_norm": r["norm"], "query": r["query"]})
        else:
            rest.append(r)
    return {"ws": ws, "workspace_id": workspace_id, "rows": rows, "labels": labels, "rest": rest, "campaign_ids": campaign_ids}


def _classify_system(user_id: int, plan: dict) -> str:
    text = examples_prompt(memory_examples(plan["workspace_id"]))
    return _SYSTEM.format(
        brief=brief_prompt(plan["ws"]),
        keywords=_keywords_sample(_keyword_index(user_id, plan["campaign_ids"])),
        examples="Прошлые решения маркетолога — следуй им:\n" + text + "\n\n" if text else "",
    )


def _usage_out(usage: dict) -> dict | None:
    if not usage["calls"]:
        return None
    return {
        "calls": usage["calls"],
        "tokens_in": usage["tokens_in"],
        "tokens_out": usage["tokens_out"],
        "cost_rub": round(usage["cost_rub"], 4) if usage["priced"] else None,
        "estimated": usage["estimated"],
    }


def classify_queries(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """Разметка очереди: сначала правила (Директ, память, бриф), остальное — ИИ пачками.

    ИИ — быстрая модель из настроек. Перед каждой пачкой — проверка, что её оценка влезает
    в остаток месячного лимита: что успели — сохранено, остальное ждёт следующего месяца или лимита.
    """
    progress = progress or (lambda *_a: None)
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
    if rest:
        try:
            cfg = ai.load_config(user_id, "classify", workspace_id)
        except YandexError as exc:
            cfg = None
            ai_used = False
            ai_error = exc.message or exc.code
        if cfg is not None:
            ai_used = True
            system = _classify_system(user_id, plan)
            ratio = ai.chars_per_token(cfg)
            out_per = ai.out_tokens_per_unit(cfg, "classify", OUT_TOKENS_PER_QUERY)
            done = rules_n
            with ai.meter() as spent:
                for start in range(0, len(rest), AI_BATCH):
                    batch = rest[start : start + AI_BATCH]
                    progress(done, len(rows), f"ИИ размечает запросы: {done} из {len(rows)}")
                    try:
                        left = ai.budget_left(cfg)
                        need = ai.cost_rub(cfg, *_batch_tokens(system, batch, ratio, out_per))
                        if left is not None and need is not None and need > left:
                            raise YandexError(
                                "ai-budget",
                                f"Месячного лимита на ИИ не хватает на следующие запросы: осталось {ai.format_rub(max(left, 0))}",
                            )
                        got = ai_labels(cfg, system, batch)
                    except YandexError as exc:
                        # Что успели — сохранено; ошибку показываем, если не вышло ничего.
                        ai_error = exc.message or exc.code
                        if not labels:
                            raise
                        break
                    suggest_labels(user_id, workspace_id, got)
                    labels += got
                    done += len(batch)
            usage = _usage_out(spent)
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
    """Оценка до запуска: сколько закроют правила, сколько уйдёт в ИИ, токены, рубли и остаток лимита.

    Токены входа — по символам тех же сообщений, что уйдут в модель, и фактическому числу символов
    на токен у этой модели; выхода — по среднему на запрос в прошлых разметках.
    """
    out = {
        "queries": 0, "rules": 0, "ai": 0, "batches": 0, "model": None, "tokens_in": 0, "tokens_out": 0,
        "cost_rub": None, "priced": False, "calibrated": False, "limit_rub": None, "spent_rub": None,
        "remaining_rub": None, "fits": None, "ai_error": None,
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
        out["ai_error"] = exc.message or exc.code
        return out
    system = _classify_system(user_id, plan)
    ratio = ai.chars_per_token(cfg)
    out_per = ai.out_tokens_per_unit(cfg, "classify", OUT_TOKENS_PER_QUERY)
    batches = [rest[i : i + AI_BATCH] for i in range(0, len(rest), AI_BATCH)]
    tokens = [_batch_tokens(system, b, ratio, out_per) for b in batches]
    cost = ai.cost_rub(cfg, sum(t[0] for t in tokens), sum(t[1] for t in tokens))
    out.update(
        batches=len(batches),
        model=cfg["model_name"],
        tokens_in=sum(t[0] for t in tokens),
        tokens_out=sum(t[1] for t in tokens),
        cost_rub=None if cost is None else round(cost, 4),
        priced=cost is not None,
        calibrated=ratio != ai.CHARS_PER_TOKEN or out_per != OUT_TOKENS_PER_QUERY,
    )
    left = ai.budget_left(cfg)
    if left is not None and cost is not None:
        out.update(limit_rub=cfg["limit_rub"], spent_rub=round(cfg["limit_rub"] - left, 2), remaining_rub=round(left, 2))
        # Пачки идут по порядку и останавливаются на первой, что не влезает в остаток.
        fits, acc = 0, 0.0
        for b, t in zip(batches, tokens):
            acc += ai.cost_rub(cfg, *t)
            if acc > left:
                break
            fits += len(b)
        out["fits"] = fits
    return out


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
                    "message": "Пересекается с вашими ключевыми фразами",
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


def sync_workspace_queries(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """Задача: свежие поисковые запросы всех поисковых кампаний пространства."""
    from app.search_queries import sync_campaign_search_queries

    progress = progress or (lambda *_a: None)
    workspace_id = int(payload.get("workspace_id") or 0)
    require_workspace(user_id, workspace_id)
    days = max(1, min(int(payload.get("days") or 14), 90))
    campaigns = [c for c in workspace_search_campaigns(user_id, workspace_id) if c["state"] != "ARCHIVED"]
    if not campaigns:
        raise YandexError("empty", "В пространстве нет поисковых кампаний")
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
