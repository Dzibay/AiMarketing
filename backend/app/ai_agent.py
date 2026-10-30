"""ИИ-агент пространства: чат с инструментами (данные и действия в Директе) и согласованием.

Чтение выполняется сразу. Каждое изменение — запись ai_actions: выполняется само, если это
разрешают настройки доступа пространства, иначе ждёт решения пользователя в чате.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import ai, experiments
from app.actions import BULK_ACTIONS, SETTINGS_LABELS, campaign_options
from app.brief import brief_filled, normalize_brief
from app.clock import today_msk
from app.db import pool
from app.journal import acting_as, list_action_logs
from app.query_cleaning import examples_prompt, memory_examples
from app.workspaces import (
    AI_CATEGORIES,
    SCHEME_FACT,
    fact_key,
    get_workspace,
    normalize_ai_access,
    normalize_ai_facts,
    remember_ai_facts,
    require_workspace,
    update_workspace,
    workspace_campaign_row_ids,
)
from app.yandex import YandexError

log = logging.getLogger(__name__)

MAX_STEPS = 8
HISTORY_MESSAGES = 60
TOOL_RESULT_LIMIT = 12_000
MAX_IDS = 100
MAX_QUESTIONS = 5
MAX_OPTIONS = 8
MAX_PLAN_STEPS = 12
MAX_CHANGES = 20
STALE_RUNNING = timedelta(minutes=10)
CATEGORY_LABELS = dict(AI_CATEGORIES)
CHAT_STATUSES = ("idle", "running", "waiting", "error")


# --- Утилиты ----------------------------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _for_ai(value):
    """Копейки и микроединицы → рубли, чтобы модель не путала порядок сумм."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key.endswith("_kopecks"):
                out[key[: -len("_kopecks")] + "_rub"] = round(item / 100, 2) if isinstance(item, (int, float)) else item
            elif key.endswith("_micros"):
                rub_key = key[: -len("_micros")] + "_rub"
                if rub_key not in value:
                    out[rub_key] = round(item / 1_000_000, 2) if isinstance(item, (int, float)) else item
            else:
                out[key] = _for_ai(item)
        return out
    if isinstance(value, list):
        return [_for_ai(v) for v in value]
    return value


def _dump(value, limit: int = TOOL_RESULT_LIMIT) -> str:
    text = json.dumps(_for_ai(value), ensure_ascii=False, default=str)
    if len(text) > limit:
        text = (
            text[:limit] + f"… [ответ обрезан: показано {limit} из {len(text)} знаков — об остальном выводов не "
            "делай, запроси по частям или скажи, что видел не всё]"
        )
    return text


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _money(rub) -> str:
    if rub is None:
        return "—"
    return f"{float(rub):,.0f}".replace(",", " ") + " ₽"


def _price(rub) -> str:
    """Ставки и цены клика — с копейками: 12.5 → «12,5 ₽»."""
    if rub is None:
        return "—"
    return f"{float(rub):.2f}".rstrip("0").rstrip(".").replace(".", ",") + " ₽"


def _line(value, limit: int) -> str:
    """Строка из аргументов модели в одну строку и не длиннее limit."""
    return " ".join(str(value if value is not None else "").split())[:limit]


def _preview(items: list[str], limit: int = 8) -> str:
    text = ", ".join(items[:limit])
    return text + (f" и ещё {len(items) - limit}" if len(items) > limit else "")


def _names(rows: list[dict], limit: int = 3) -> str:
    names = [f"«{r.get('name') or r.get('keyword') or r.get('title') or r.get('query') or r['id']}»" for r in rows]
    text = ", ".join(names[:limit])
    return text + (f" и ещё {len(names) - limit}" if len(names) > limit else "")


def _cname(row: dict) -> str:
    """«Название» кампании для карточки; у одноимённых в пространстве — с номером кампании, как в Директе."""
    return f"«{row['name']}»" + (f" №{row['campaign_id']}" if row.get("twin") and row.get("campaign_id") else "")


def _cnames(rows: list[dict], limit: int = 3) -> str:
    return _preview([_cname(r) for r in rows], limit)


def _split_new(phrases: list[str], existing: list[str]) -> tuple[list[str], list[str]]:
    """Фразы → (новые, уже есть); повторы внутри запроса не считаем."""
    from app.negative_lists import phrase_key

    known = {phrase_key(p) for p in existing}
    seen: set[str] = set()
    new: list[str] = []
    already: list[str] = []
    for phrase in phrases:
        key = phrase_key(phrase)
        if key in seen:
            continue
        seen.add(key)
        (already if key in known else new).append(phrase)
    return new, already


def _added(new: list[str], already: list[str], limit: int) -> str:
    """«+7 (13 уже есть): а, б…» — сколько фраз действительно добавится."""
    if not new:
        return "+0 — " + ("фраза уже есть" if len(already) == 1 else f"все {len(already)} уже есть")
    return f"+{len(new)}" + (f" ({len(already)} уже есть)" if already else "") + f": {_preview(new, limit)}"


def _ids(args: dict, key: str) -> list[int]:
    raw = args.get(key)
    if not isinstance(raw, list) or not raw:
        raise YandexError("args", f"Укажите {key} — непустой список id")
    try:
        ids = list(dict.fromkeys(int(v) for v in raw))
    except (TypeError, ValueError) as exc:
        raise YandexError("args", f"{key}: id должны быть числами") from exc
    if len(ids) > MAX_IDS:
        raise YandexError("args", f"Не больше {MAX_IDS} объектов за раз")
    return ids


def _int(args: dict, key: str) -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise YandexError("args", f"Укажите {key} (число)") from exc


def _float(args: dict, key: str) -> float | None:
    value = args.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise YandexError("args", f"{key} — число") from exc


def _bool(args: dict, key: str, default: bool) -> bool:
    value = args.get(key)
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "нет")
    return bool(value)


def _phrases(args: dict, key: str = "phrases") -> list[str]:
    raw = args.get(key)
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        raise YandexError("args", f"Укажите {key} — список фраз")
    out: list[str] = []
    for item in raw:
        phrase = str(item or "").strip()
        if phrase and phrase not in out:
            out.append(phrase)
    if not out:
        raise YandexError("args", "Список фраз пуст")
    return out[:1000]


def _pct_change(old, new) -> float:
    if not old:
        return math.inf
    return abs(float(new) / float(old) - 1) * 100


def _para(value, limit: int) -> str:
    """Текст с переносами: пробелы внутри строк схлопнуты, не больше одной пустой строки подряд."""
    out: list[str] = []
    for line in (" ".join(part.split()) for part in str(value if value is not None else "").splitlines()):
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()[:limit]


def _dmy(value) -> str:
    """Дата ISO → «28.10.2026»; не дата — как есть."""
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


def _count(n: int, one: str, few: str, many: str) -> str:
    """«1 фраза», «3 фразы», «11 фраз»."""
    if n % 100 in (11, 12, 13, 14):
        word = many
    elif n % 10 == 1:
        word = one
    elif n % 10 in (2, 3, 4):
        word = few
    else:
        word = many
    return f"{n} {word}"


def _goal_names(ctx) -> dict[int, str]:
    out: dict[int, str] = {}
    for goal in ctx.ws.get("goals") or []:
        try:
            out[int(goal["goal_id"])] = str(goal.get("name") or "")
        except (KeyError, TypeError, ValueError):
            continue
    return out


# --- Контекст пространства --------------------------------------------------------------------


class Context:
    def __init__(self, user_id: int, workspace_id: int):
        self.user_id = user_id
        self.ws_id = workspace_id
        self.ws = get_workspace(user_id, workspace_id)
        self.access = normalize_ai_access(self.ws.get("ai_access"))
        # Действие ai_actions, которое сейчас выполняется: эксперимент запоминает, из какого чата запущен.
        self.action: dict | None = None
        # Чат, в котором работает модель: новая кампания проверяется по таблицам, согласованным в нём.
        self.chat_id: int | None = None

    def account_ids(self) -> set[int]:
        return {int(a["id"]) for a in self.ws.get("accounts") or []}

    def require_account(self, account_id: int) -> dict:
        for account in self.ws.get("accounts") or []:
            if int(account["id"]) == account_id:
                return account
        raise YandexError("scope", "Кабинет не подключён к этому пространству")

    def campaigns(self, ids: list[int]) -> list[dict]:
        """Кампании пространства по id строк; чужие — ошибка."""
        allowed = set(workspace_campaign_row_ids(self.user_id, self.ws_id))
        foreign = [i for i in ids if i not in allowed]
        if foreign:
            raise YandexError("scope", f"Кампании {foreign} не входят в это пространство")
        with pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.campaign_id, c.name, c.state, c.status, c.campaign_type,
                       c.daily_budget_kopecks, c.account_id,
                       EXISTS (
                           SELECT 1 FROM direct_campaigns o
                           WHERE o.user_id = c.user_id AND o.id = ANY(%s) AND o.id <> c.id AND o.name = c.name
                       ) AS twin
                FROM direct_campaigns c
                WHERE c.user_id = %s AND c.id = ANY(%s)
                ORDER BY c.name, c.id
                """,
                (sorted(allowed), self.user_id, ids),
            ).fetchall()
        return rows

    def campaign(self, campaign_row_id: int) -> dict:
        return self.campaigns([campaign_row_id])[0]

    def _scoped(self, sql: str, ids: list[int], what: str) -> list[dict]:
        allowed = set(workspace_campaign_row_ids(self.user_id, self.ws_id))
        with pool.connection() as conn:
            rows = conn.execute(sql, (self.user_id, ids)).fetchall()
        found = {r["id"] for r in rows}
        missing = [i for i in ids if i not in found]
        if missing:
            raise YandexError("not-found", f"{what} {missing} не найдены")
        foreign = [r["id"] for r in rows if r["campaign_row_id"] not in allowed]
        if foreign:
            raise YandexError("scope", f"{what} {foreign} — из кампаний вне пространства")
        return rows

    def keywords(self, ids: list[int]) -> list[dict]:
        return self._scoped(
            """
            SELECT k.id, k.keyword, k.bid_micros, g.campaign_row_id
            FROM direct_keywords k JOIN direct_adgroups g ON g.id = k.adgroup_row_id
            WHERE k.user_id = %s AND k.id = ANY(%s)
            """,
            ids,
            "Ключевые фразы",
        )

    def ads(self, ids: list[int]) -> list[dict]:
        return self._scoped(
            """
            SELECT ad.id, ad.title, ad.type, ad.account_id, ad.adgroup_row_id, g.campaign_row_id
            FROM direct_ads ad JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            WHERE ad.user_id = %s AND ad.id = ANY(%s)
            """,
            ids,
            "Объявления",
        )

    def adgroup(self, adgroup_row_id: int) -> dict:
        return self._scoped(
            """
            SELECT g.id, g.name, g.type, g.account_id, g.campaign_row_id, c.campaign_type
            FROM direct_adgroups g JOIN direct_campaigns c ON c.id = g.campaign_row_id
            WHERE g.user_id = %s AND g.id = ANY(%s)
            """,
            [adgroup_row_id],
            "Группы",
        )[0]

    def queries(self, ids: list[int]) -> list[dict]:
        return self._scoped(
            "SELECT q.id, q.query, q.campaign_row_id FROM direct_search_queries q WHERE q.user_id = %s AND q.id = ANY(%s)",
            ids,
            "Поисковые запросы",
        )


def _allowed(access: dict, categories: list[str], change_pct: float | None) -> bool:
    if access.get("full_access"):
        return True
    cats = access.get("categories") or {}
    if not all(cats.get(c) for c in categories):
        return False
    if change_pct is not None and ("budget" in categories or "bids" in categories):
        return change_pct <= float(access.get("max_change_pct") or 0)
    return True


# --- Инструменты чтения -------------------------------------------------------------------------


def _range_args(args: dict) -> dict:
    from app.analytics import parse_range

    date_from, date_to = parse_range(args.get("date_from") or None, args.get("date_to") or None)
    return {"date_from": date_from, "date_to": date_to}


def _t_summary(ctx: Context, args: dict) -> dict:
    from app.analytics import workspace_summary

    out = workspace_summary(ctx.user_id, ctx.ws_id)
    out.pop("recent_actions", None)
    out["week"].pop("prev_by_day", None)
    return out


def _t_analytics(ctx: Context, args: dict) -> dict:
    from app.analytics import workspace_analytics

    out = workspace_analytics(ctx.user_id, ctx.ws_id, **_range_args(args), with_tops=False)
    out.pop("prev_by_day", None)
    if not args.get("include_days"):
        out.pop("by_day", None)
    return out


def _t_campaigns(ctx: Context, args: dict) -> dict:
    from app.analytics import workspace_analytics

    out = workspace_analytics(ctx.user_id, ctx.ws_id, **_range_args(args), with_tops=False)
    with pool.connection() as conn:
        strategies = {
            r["id"]: r["strategy"]
            for r in conn.execute(
                "SELECT id, strategy FROM direct_campaigns WHERE user_id = %s AND id = ANY(%s)",
                (ctx.user_id, [c["campaign_row_id"] for c in out["by_campaign"]]),
            ).fetchall()
        }
    for c in out["by_campaign"]:
        c["strategy"] = strategies.get(c["campaign_row_id"]) or ""
    return {
        "date_from": out["date_from"],
        "date_to": out["date_to"],
        "conversions_source": out["conversions_source"],
        "campaigns": out["by_campaign"],
    }


# Сколько знаков JSON get_campaign отдаёт на раздел. Вместе они влезают в TOOL_RESULT_LIMIT: иначе ответ
# обрезался бы посреди объявлений, и модель решила бы, что видела всё.
CAMPAIGN_BUDGET = {"negatives": 1000, "adgroups": 2500, "ads": 4300, "keywords": 2200}
CAMPAIGN_PARTS = {"adgroups": "группы", "ads": "объявления", "keywords": "фразы"}


def _offset(args: dict, key: str) -> int:
    try:
        return max(0, int(args.get(key) or 0))
    except (TypeError, ValueError) as exc:
        raise YandexError("args", f"{key} — число") from exc


def _fit(items: list, offset: int, budget: int) -> list:
    """Элементы начиная с offset, сколько влезает в budget знаков JSON (хотя бы один)."""
    out: list = []
    used = 0
    for item in items[offset:]:
        used += len(json.dumps(_for_ai(item), ensure_ascii=False, default=str)) + 1
        if out and used > budget:
            break
        out.append(item)
    return out


def _ad_brief(ad: dict) -> dict:
    out = {k: ad[k] for k in ("id", "adgroup_row_id", "type", "state", "status", "titles", "texts")}
    if ad["title2"]:
        out["title2"] = ad["title2"]
    out.update(href=ad["href"], sitelinks=bool(ad.get("sitelink_set_id")), images=len(ad["image_hashes"]))
    return out


def _t_campaign(ctx: Context, args: dict) -> dict:
    from app.adgroups import list_adgroups
    from app.ads import list_ads
    from app.direct_sync import get_campaign
    from app.keywords import list_keywords

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    groups = list_adgroups(ctx.user_id, cid)
    if args.get("adgroup_row_ids"):
        wanted = set(_ids(args, "adgroup_row_ids"))
        unknown = wanted - {int(g["id"]) for g in groups}
        if unknown:
            raise YandexError("args", f"В кампании нет групп {sorted(unknown)}")
        groups = [g for g in groups if int(g["id"]) in wanted]
    in_groups = {int(g["id"]) for g in groups}
    ads = [a for a in list_ads(ctx.user_id, campaign_row_id=cid) if int(a["adgroup_row_id"]) in in_groups]
    archived = sum(1 for a in ads if a["state"] == "ARCHIVED")
    ads = sorted((a for a in ads if a["state"] != "ARCHIVED"), key=lambda a: (int(a["adgroup_row_id"]), int(a["id"])))
    keywords = [k for k in list_keywords(ctx.user_id, campaign_row_id=cid) if int(k["adgroup_row_id"]) in in_groups]
    keywords.sort(key=lambda k: (k.get("period_cost_kopecks") or 0, k.get("period_clicks") or 0), reverse=True)

    per_group: dict[str, dict[int, int]] = {"ads": {}, "keywords": {}}
    for part, rows in (("ads", ads), ("keywords", keywords)):
        for r in rows:
            gid = int(r["adgroup_row_id"])
            per_group[part][gid] = per_group[part].get(gid, 0) + 1
    groups.sort(key=lambda g: (-(g.get("period_cost_kopecks") or 0), int(g["id"])))
    rows: dict[str, list[dict]] = {
        "adgroups": [
            {
                **{k: g[k] for k in ("id", "name", "status", "type", "region_ids")},
                "negative_keywords": g["negative_keywords"][:10],
                "negative_keywords_total": len(g["negative_keywords"]),
                "ads": per_group["ads"].get(int(g["id"]), 0),
                "keywords": per_group["keywords"].get(int(g["id"]), 0),
                **{k: g[k] for k in ("period_impressions", "period_clicks", "period_cost_kopecks", "period_conversions")},
            }
            for g in groups
        ],
        "ads": [],
        "keywords": [
            {k: kw.get(k) for k in (
                "id", "adgroup_row_id", "keyword", "state", "status", "bid_rub", "period_impressions", "period_clicks",
                "period_cost_kopecks", "period_conversions", "period_cpa_rub",
            )}
            for kw in keywords
        ],
    }
    # Одинаковые объявления — ссылкой на первое такое же: длинные комбинаторные тексты не повторяем.
    first: dict[str, int] = {}
    for ad in ads:
        brief = _ad_brief(ad)
        head = {k: brief.pop(k) for k in ("id", "adgroup_row_id", "state", "status")}
        same = json.dumps(brief, ensure_ascii=False, sort_keys=True)
        if same in first:
            rows["ads"].append(head | {"same_as": first[same]})
        else:
            first[same] = head["id"]
            rows["ads"].append(head | brief)

    camp = get_campaign(ctx.user_id, cid) or {}
    negatives = camp.get("negative_keywords") or []
    camp["negative_keywords"] = _fit(negatives, 0, CAMPAIGN_BUDGET["negatives"])
    camp["negative_keywords_total"] = len(negatives)
    stats = next((r for r in (*groups, *keywords) if r.get("stats_from")), None)
    out: dict = {"campaign": camp, "stats_period": f"{stats['stats_from']} — {stats['stats_to']}" if stats else None}
    cut = []
    for part, label in CAMPAIGN_PARTS.items():
        start = _offset(args, f"{part}_offset")
        shown = _fit(rows[part], start, CAMPAIGN_BUDGET[part])
        end, total = start + len(shown), len(rows[part])
        out[f"{part}_total"] = total
        out[part] = shown
        if start >= total > 0:
            cut.append(f"{label}: всего {total}, с {start + 1}-й ничего нет")
        elif start or end < total:
            cut.append(f"{label} {start + 1}–{end} из {total}" + (f" (дальше — {part}_offset={end})" if end < total else ""))
    out["ads_archived"] = archived
    out["ads_duplicates"] = sum(1 for a in rows["ads"] if "same_as" in a)
    if len(negatives) > len(camp["negative_keywords"]):
        cut.append(f"минус-фразы кампании {len(camp['negative_keywords'])} из {len(negatives)} (все — get_campaign_settings)")
    if cut:
        out["not_all_shown"] = (
            "Показано не всё: " + "; ".join(cut) + ". Об остальном выводов не делай — дозапроси (можно только нужные "
            "группы: adgroup_row_ids) или скажи маркетологу, что смотрел часть."
        )
    return out


def _t_settings(ctx: Context, args: dict) -> dict:
    from app.actions import get_campaign_settings

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    out = get_campaign_settings(ctx.user_id, cid)
    if out.get("schedule"):
        out["schedule"] = "задано почасовое расписание (7×24, % ставки)"
    return out


def _t_queries(ctx: Context, args: dict) -> dict:
    from app.search_queries import list_search_queries

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    limit = max(1, min(int(args.get("limit") or 100), 200))
    out = list_search_queries(
        ctx.user_id,
        cid,
        limit=limit,
        search=str(args.get("search") or ""),
        min_clicks=args.get("min_clicks"),
        min_cost_rub=args.get("min_cost_rub"),
        no_conversions=bool(args.get("no_conversions")),
        with_conversions=bool(args.get("with_conversions")),
    )
    if not out["total"] and not out["synced_at"]:
        out["hint"] = "Запросы ещё не загружены — вызови refresh_search_queries"
    return out


def _t_refresh_queries(ctx: Context, args: dict) -> dict:
    from app.search_queries import sync_campaign_search_queries

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    return sync_campaign_search_queries(ctx.user_id, cid, days=int(args.get("days") or 14))


def _t_tops(ctx: Context, args: dict) -> dict:
    from app.stats import structure_tops

    return structure_tops(ctx.user_id, campaign_row_ids=workspace_campaign_row_ids(ctx.user_id, ctx.ws_id))


def _t_actions(ctx: Context, args: dict) -> dict:
    limit = max(1, min(int(args.get("limit") or 20), 50))
    items = list_action_logs(ctx.user_id, limit, workspace_id=ctx.ws_id)
    return {
        "items": [
            {k: i.get(k) for k in ("id", "created_at", "source", "title", "action", "status", "error")}
            for i in items
        ]
    }


def _t_options(ctx: Context, args: dict) -> dict:
    return {
        "options": campaign_options(),
        "accounts": [
            {
                "id": a["id"],
                "client_login": a["client_login"],
                "name": a["name"],
                "sandbox": a.get("sandbox"),
                "counter_access": a.get("counter_access"),
            }
            for a in ctx.ws.get("accounts") or []
        ],
        "goals": ctx.ws.get("goals") or [],
        "counter_id": ctx.ws.get("counter_id"),
        "workspace_goal_id": ctx.ws.get("goal_id"),
    }


def _t_signals(ctx: Context, args: dict) -> dict:
    from app.insights import get_signals, signals_digest

    return {"digest": signals_digest(get_signals(ctx.user_id, ctx.ws_id))}


def _t_history(ctx: Context, args: dict) -> dict:
    from app.insights import campaign_history

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    return campaign_history(ctx.user_id, ctx.ws_id, cid)


def _t_remember(ctx: Context, args: dict) -> dict:
    """Память ассистента, а не изменение в Директе, — выполняется сразу; забыть можно в настройках."""
    from app.workspaces import remember_ai_fact

    facts = remember_ai_fact(
        ctx.user_id,
        ctx.ws_id,
        key=str(args.get("key") or ""),
        question=str(args.get("question") or ""),
        answer=str(args.get("answer") or ""),
    )
    ctx.ws["ai_facts"] = facts
    return {"remembered": True, "facts_total": len(facts)}


def _t_regions(ctx: Context, args: dict) -> dict:
    from app import geo

    return geo.search_regions(ctx.user_id, str(args.get("query") or ""))


WORDSTAT_MAX_PHRASES = 5
WORDSTAT_LIMIT, WORDSTAT_LIMIT_MAX = 20, 100


def _t_wordstat(ctx: Context, args: dict) -> dict:
    from app import wordstat

    raw = args.get("phrases")
    if isinstance(raw, str):
        raw = [raw]
    phrases = list(dict.fromkeys(" ".join(str(p).split()) for p in raw or [] if str(p).strip()))
    if not phrases:
        raise YandexError("args", "Укажите phrases — исходные фразы для Вордстата")
    # Квота Вордстата — 100 запросов в час на каталог, один запрос на фразу.
    if len(phrases) > WORDSTAT_MAX_PHRASES:
        raise YandexError("args", f"Не больше {WORDSTAT_MAX_PHRASES} фраз за вызов: квота Вордстата — 100 запросов в час")
    try:
        regions = [int(r) for r in args.get("region_ids") or []]
    except (TypeError, ValueError) as exc:
        raise YandexError("args", "region_ids: id регионов должны быть числами") from exc
    note = "Вордстат считает только регионы, откуда искали: исключения (минус) пропущены." if any(r < 0 for r in regions) else ""
    regions = [r for r in dict.fromkeys(regions) if r > 0]
    device = str(args.get("device") or "all")
    try:
        limit = max(1, min(int(args.get("limit") or WORDSTAT_LIMIT), WORDSTAT_LIMIT_MAX))
    except (TypeError, ValueError):
        limit = WORDSTAT_LIMIT
    api_key, folder_id = wordstat.credentials(ctx.user_id)
    items, errors = [], []
    for phrase in phrases:
        try:
            found = wordstat.top_requests(api_key, folder_id, phrase, regions=regions, device=device, num_phrases=limit)
        except YandexError as exc:
            # Доступ и квота одинаковы для всех фраз — дальше спрашивать бессмысленно.
            if exc.code != "args":
                raise
            errors.append({"phrase": phrase, "error": exc.message or exc.code})
            continue
        found["associations"] = found["associations"][:limit]
        items.append(found)
    out: dict = {"period": "последние 30 дней", "regions": regions or "все", "device": device, "items": items}
    if errors:
        out["errors"] = errors
    if note:
        out["note"] = note
    return out


def _t_experiments(ctx: Context, args: dict) -> dict:
    items = experiments.experiments_overview(ctx.user_id, ctx.ws_id)["items"]
    full = None
    if args.get("experiment_id") not in (None, ""):
        exp_id = _int(args, "experiment_id")
        if not any(e["id"] == exp_id for e in items):
            raise YandexError("not-found", f"Эксперимента #{exp_id} нет в этом пространстве")
        full = {exp_id}
    return {"digest": experiments.digest(items, full)}


def _days(args: dict, default: int = 30) -> int:
    try:
        return max(1, min(int(args.get("days") or default), 90))
    except (TypeError, ValueError):
        return default


def _scope_ids(ctx: Context, args: dict) -> list[int] | None:
    """campaign_row_ids из аргументов (проверены на пространство) или None — все кампании."""
    if args.get("campaign_row_ids") in (None, "", []):
        return None
    ids = _ids(args, "campaign_row_ids")
    ctx.campaigns(ids)
    return ids


def _t_headlines(ctx: Context, args: dict) -> dict:
    from app import headlines

    return headlines.headline_stats(ctx.user_id, ctx.ws_id, _scope_ids(ctx, args), _days(args))


_SLICE_FIELDS = ("key", "label", "impressions", "clicks", "cost_kopecks", "conversions", "ctr", "cr", "cpa_kopecks", "bounce_rate")


def _t_slices(ctx: Context, args: dict) -> dict:
    from app import slices

    dimension = str(args.get("dimension") or "")
    if dimension not in slices.DIMENSIONS:
        raise YandexError("args", f"dimension — одно из {list(slices.DIMENSIONS)}")
    report = slices.slice_report(
        ctx.user_id,
        {"workspace_id": ctx.ws_id, "dimension": dimension, "campaign_ids": _scope_ids(ctx, args), "days": _days(args)},
    )
    items = report["items"]
    limit = 40 if dimension != "hour" else 24
    out_items = []
    for r in items[:limit]:
        item = {k: r[k] for k in _SLICE_FIELDS if r.get(k) is not None}
        for k in ("visits", "kind", "reasons", "current_pct", "suggested_pct"):
            if r.get(k) not in (None, [], ""):
                item[k] = r[k]
        out_items.append(item)
    return {
        "dimension": dimension,
        "period": {"from": report["date_from"], "to": report["date_to"]},
        "goal_name": report["goal_name"],
        "totals": report["totals"],
        "items_total": len(items),
        "items": out_items,
        "note": "Суммы в копейках. Площадки — только РСЯ; корректировки ставок (current_pct, suggested_pct) — в %.",
    }


def _t_links(ctx: Context, args: dict) -> dict:
    from app import links

    out = links.workspace_link_issues(ctx.user_id, ctx.ws_id, _scope_ids(ctx, args))
    for c in out["campaigns"]:
        c.pop("ad_links", None)  # те же объявления, что в ads, — модели хватит одного списка
    if not out["configured"]:
        out["note"] = "Шаблон ссылки сайта не задан — попроси маркетолога задать его в настройках пространства"
    return out


def _t_ad_extras(ctx: Context, args: dict) -> dict:
    from app.ads import read_ad_extras

    row = ctx.ads([_int(args, "ad_row_id")])[0]
    out = read_ad_extras(ctx.user_id, int(row["id"]))
    return {
        "ad_row_id": row["id"],
        "type": out["type"],
        "sitelinks": out["sitelinks"],
        "callouts": out["callouts"],
        "image_hashes": out["image_hashes"],
        "max_images": out["max_images"],
    }


def _t_ad_images(ctx: Context, args: dict) -> dict:
    from app.ads import campaign_images

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    out = campaign_images(ctx.user_id, cid)
    out["hint"] = (
        "Хэш картинки — в extras.image_hashes объявления. in_campaign — картинки, что уже стоят в объявлениях "
        "кампании (ads — в скольких); library — остальные картинки кабинета"
    )
    return out


READ_TOOLS = {
    "get_workspace_summary": _t_summary,
    "get_analytics": _t_analytics,
    "list_campaigns": _t_campaigns,
    "get_campaign": _t_campaign,
    "get_campaign_settings": _t_settings,
    "get_search_queries": _t_queries,
    "refresh_search_queries": _t_refresh_queries,
    "get_structure_tops": _t_tops,
    "list_recent_actions": _t_actions,
    "get_campaign_options": _t_options,
    "get_signals": _t_signals,
    "get_campaign_history": _t_history,
    "remember_fact": _t_remember,
    "find_regions": _t_regions,
    "get_wordstat": _t_wordstat,
    "get_experiments": _t_experiments,
    "get_headline_stats": _t_headlines,
    "get_slices": _t_slices,
    "check_links": _t_links,
    "get_ad_extras": _t_ad_extras,
    "list_ad_images": _t_ad_images,
}


# --- Инструменты действий: prepare (проверка, описание, категории) и execute ------------------


class Plan:
    def __init__(self, summary: str, categories: list[str], change_pct: float | None = None):
        self.summary = summary
        self.categories = categories
        self.change_pct = change_pct


_CAMPAIGN_VERBS = {
    "pause": "Остановить",
    "resume": "Запустить",
    "set_budget": "Изменить дневной бюджет",
    "archive": "Архивировать",
    "unarchive": "Вернуть из архива",
    "delete": "Удалить",
}


def _p_campaigns_action(ctx: Context, args: dict) -> Plan:
    action = str(args.get("action") or "")
    if action not in BULK_ACTIONS:
        raise YandexError("args", f"action — одно из {sorted(BULK_ACTIONS)}")
    rows = ctx.campaigns(_ids(args, "campaign_row_ids"))
    summary = f"{_CAMPAIGN_VERBS[action]}: {_cnames(rows)}"
    if action in ("pause", "resume"):
        return Plan(summary, ["pause_resume"])
    if action != "set_budget":
        return Plan(summary, ["destructive"])
    pct = _float(args, "budget_change_pct")
    daily = _float(args, "daily_budget_rub")
    if pct is None and daily is None:
        raise YandexError("args", "Укажите daily_budget_rub или budget_change_pct")
    if daily is not None:
        change = max(_pct_change(r["daily_budget_kopecks"], daily * 100) for r in rows)
        olds = {r["daily_budget_kopecks"] for r in rows}
        before = _money(next(iter(olds)) / 100) if len(olds) == 1 and next(iter(olds)) else "текущий"
        summary += f" — {before} → {_money(daily)}"
    else:
        change = abs(pct)
        summary += f" {'+' if pct > 0 else '−'}{abs(pct):g}%"
    return Plan(summary, ["budget"], change)


def _x_campaigns_action(ctx: Context, args: dict) -> dict:
    from app.actions import bulk_campaign_action

    ids = _ids(args, "campaign_row_ids")
    ctx.campaigns(ids)
    return bulk_campaign_action(
        ctx.user_id,
        campaign_ids=ids,
        action=str(args["action"]),
        daily_budget_rub=_float(args, "daily_budget_rub"),
        budget_change_pct=_float(args, "budget_change_pct") if _float(args, "daily_budget_rub") is None else None,
        workspace_id=ctx.ws_id,
    )


_SETTINGS_LABELS = {**SETTINGS_LABELS, "negative_keywords": "минус-фразы (замена списка)"}


def _p_update_settings(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    changes = args.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise YandexError("args", "changes — объект с изменяемыми полями")
    unknown = [k for k in changes if k not in _SETTINGS_LABELS]
    if unknown:
        raise YandexError("args", f"Неизвестные поля: {unknown}. Допустимы: {list(_SETTINGS_LABELS)}")
    parts = []
    for key in changes:
        label = _SETTINGS_LABELS[key]
        if key == "daily_budget_rub":
            old = row["daily_budget_kopecks"] / 100 if row["daily_budget_kopecks"] else None
            label += f" {_money(old)} → {_money(changes[key]) if changes[key] else 'без ограничения'}"
        elif key == "name":
            label += f" → «{changes[key]}»"
        elif key == "strategy" and isinstance(changes[key], dict):
            from app.actions import strategy_label

            label += f" → {strategy_label({k: str(v).upper() for k, v in changes[key].items() if k in ('search', 'network')})}"
        parts.append(label)
    cats = []
    if set(changes) - {"daily_budget_rub", "negative_keywords"}:
        cats.append("campaigns")
    change = None
    if "daily_budget_rub" in changes:
        cats.append("budget")
        new = changes["daily_budget_rub"]
        change = _pct_change(row["daily_budget_kopecks"], float(new) * 100) if new else math.inf
    if "negative_keywords" in changes:
        cats.append("negatives")
    return Plan(f"Настройки {_cname(row)}: " + ", ".join(parts), cats, change)


def _x_update_settings(ctx: Context, args: dict) -> dict:
    from app.actions import update_campaign_settings

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    return update_campaign_settings(ctx.user_id, cid, dict(args["changes"]))


def _campaign_negatives(ctx: Context, campaign_row_id: int) -> list[str]:
    """Минус-фразы кампании по последнему синку."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT negative_keywords FROM direct_campaigns WHERE user_id = %s AND id = %s", (ctx.user_id, campaign_row_id)
        ).fetchone()
    return _json_list((row or {}).get("negative_keywords"))


def _p_add_negatives(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    new, already = _split_new(_phrases(args), _campaign_negatives(ctx, int(row["id"])))
    if _made_here(ctx, int(row["id"])):
        _require_approved(ctx, {"negative": new}, stages=[])
    return Plan(f"Минус-фразы кампании {_cname(row)}: {_added(new, already, 6)}", ["negatives"])


def _x_add_negatives(ctx: Context, args: dict) -> dict:
    """Добавляет только новые фразы; откат из журнала уберёт ровно их."""
    from app.negatives import add_negatives_logged

    cid = _int(args, "campaign_row_id")
    row = ctx.campaign(cid)
    phrases = _phrases(args)
    result = add_negatives_logged(
        ctx.user_id,
        campaigns={cid: {"add": phrases}},
        title=f"Минус-фразы кампании {_cname(row)}",
        payload={"phrases": phrases[:50]},
    )
    added = (result["campaigns"].get(cid) or {}).get("added") or []
    if not added:
        return {"added": [], "note": "все фразы уже есть"}
    return {"added": added, "skipped": [s["reason"] for s in result["skipped"]][:10]}


# --- Новая кампания по этапам ------------------------------------------------------------------
#
# Новую кампанию модель собирает по этапам, и каждый маркетолог согласует таблицей propose_items:
# цели → анализ → ключи и группы → заголовки и тексты → настройки. create_campaign с группами не
# пройдёт, пока этапы не согласованы, а фразы, минус-фразы, заголовки и тексты в ней — только из
# согласованных строк или из слов самого маркетолога. То же для фраз и объявлений, которые потом
# добавляются в кампанию, созданную в этом чате.

ITEM_STAGES = {
    "goals": "Цели кампании",
    "analysis": "Анализ",
    "keywords": "Ключи и группы",
    "negatives": "Минус-фразы",
    "ads": "Заголовки и тексты",
    "extras": "Ссылка и расширения",
    "settings": "Настройки",
    "other": "На согласование",
}
ITEM_KINDS = ("goal", "direction", "keyword", "negative", "title", "text", "extra", "setting", "other")
_STAGE_KINDS = {
    "goals": "goal",
    "analysis": "direction",
    "keywords": "keyword",
    "negatives": "negative",
    "extras": "extra",
    "settings": "setting",
}
_CONTENT_WORDS = {"keyword": "фразы", "negative": "минус-фразы", "title": "заголовки", "text": "тексты"}


def _item_key(text, kind: str = "") -> str:
    """Строка для сравнения: регистр, ё/е, знаки и операторы Директа не важны; у фраз — без минус-слов."""
    words = str(text or "").lower().replace("ё", "е").split()
    if kind in ("keyword", "negative"):
        if kind == "negative" and words:
            words[0] = words[0].lstrip("-")
        words = [w for w in words if not w.startswith("-")]
    return " ".join(re.findall(r"\w+", " ".join(words)))


def _stage_approvals(ctx: Context) -> tuple[set[str], set[str], str]:
    """Что маркетолог согласовал в этом чате: (этапы таблиц, ключи согласованных строк, его собственные слова)."""
    stages: set[str] = set()
    approved: set[str] = set()
    said: list[str] = []
    with pool.connection() as conn:
        cards = conn.execute(
            "SELECT tool, args, result FROM ai_actions WHERE chat_id = %s AND tool = ANY(%s) AND status = 'done'",
            (ctx.chat_id, list(ASK_TOOLS)),
        ).fetchall()
        said += [
            m["content"] or ""
            for m in conn.execute(
                "SELECT content FROM ai_messages WHERE chat_id = %s AND role = 'user'", (ctx.chat_id,)
            ).fetchall()
        ]
    for card in cards:
        result = card["result"] or {}
        said.append(str(result.get("comment") or ""))
        said += [str(a.get("answer") or "") for a in result.get("answers") or [] if isinstance(a, dict)]
        if card["tool"] != "propose_items":
            continue
        args = card["args"] or {}
        stages.add(args.get("stage") or "other")
        chosen = set(result.get("approved_rows") or [])
        for row in args.get("rows") or []:
            if row.get("n") in chosen and row.get("cells"):
                # Вид строки не важен: фраза могла быть согласована и в таблице анализа.
                approved.add(_item_key(row["cells"][0], "keyword"))
                approved.add(_item_key(row["cells"][0]))
    return stages, approved, f" {_item_key(' '.join(said))} "


def _require_approved(ctx: Context, content: dict[str, list[str]], *, stages: list[str]) -> None:
    """Новая кампания: этапы stages согласованы, а фразы, заголовки и тексты — из согласованных строк."""
    if ctx.chat_id is None:  # не из чата ИИ — согласовывать нечем
        return
    done, approved, said = _stage_approvals(ctx)
    missing = [ITEM_STAGES[s] for s in stages if s not in done]
    if missing:
        raise YandexError(
            "args",
            "Новая кампания собирается по этапам, и каждый маркетолог согласует таблицей propose_items. Ещё не "
            f"согласовано: {', '.join(missing)}. Пройди эти этапы по порядку, и только потом создавай кампанию",
        )
    bad: list[str] = []
    for kind, values in content.items():
        off: list[str] = []
        for value in values:
            key = _item_key(value, kind)
            if key and key not in approved and f" {key} " not in said and value not in off:
                off.append(value)
        if off:
            bad.append(f"{_CONTENT_WORDS[kind]} {_preview([f'«{v}»' for v in off], 5)}")
    if bad:
        raise YandexError(
            "args",
            "Этого маркетолог не согласовывал или снял галочку: " + "; ".join(bad) + ". Убери это или покажи "
            "новой таблицей propose_items и добавляй только после согласования",
        )


def _made_here(ctx: Context, campaign_row_id: int) -> bool:
    """Кампания создана create_campaign в этом чате — в неё добавляется только согласованное."""
    if ctx.chat_id is None:
        return False
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM ai_actions
            WHERE chat_id = %s AND tool = 'create_campaign' AND status = 'done'
              AND result -> 'campaign' ->> 'id' = %s
            LIMIT 1
            """,
            (ctx.chat_id, str(campaign_row_id)),
        ).fetchone()
    return row is not None


def _group_ad(ctx: Context, ad, name: str, account_id: int) -> tuple[list[str], list[str]] | None:
    """Объявление группы новой кампании → (заголовки, тексты); None — объявления нет. Проверка без запросов."""
    from app.ads import MAX_TEXTS, MAX_TITLES, TEXT_LEN, TITLE_LEN, clean_variants

    if ad in (None, {}):
        return None
    if not isinstance(ad, dict):
        raise YandexError("args", f"Группа «{name}»: ad — объект {{titles, texts, href}}")
    titles = clean_variants(_variants(ad, "titles") or [ad.get("title")], MAX_TITLES, TITLE_LEN, "заголовков")
    texts = clean_variants(_variants(ad, "texts") or [ad.get("text")], MAX_TEXTS, TEXT_LEN, "текстов")
    if not titles or not texts:
        raise YandexError("args", f"Группа «{name}»: в объявлении нужны titles и texts")
    if str(ad.get("title2") or "").strip():
        raise YandexError(
            "args", f"Группа «{name}»: объявление комбинаторное, второго заголовка нет — добавь его в titles"
        )
    _ad_extras_args(ctx, ad, "RESPONSIVE_AD", account_id)
    return titles, texts


def _p_create_campaign(ctx: Context, args: dict) -> Plan:
    from app import actions

    account = ctx.require_account(_int(args, "account_id"))
    spec = args.get("spec")
    if not isinstance(spec, dict) or not str(spec.get("name") or "").strip():
        raise YandexError("args", "spec — объект кампании, нужен хотя бы name")
    groups = actions.spec_groups(spec)
    negatives = spec.get("negative_keywords")
    content: dict[str, list[str]] = {
        "keyword": [],
        "negative": [str(p) for p in negatives] if isinstance(negatives, list) else [],
        "title": [],
        "text": [],
    }
    names: list[str] = []
    ads: dict[tuple, str] = {}
    href_default = bool(actions._ws_link_template(ctx.ws))
    for index, group in enumerate(groups):
        name = str(group.get("name") or f"Группа {index + 1}").strip()
        if name in names:
            raise YandexError("args", f"Две группы называются «{name}» — назови их по-разному")
        names.append(name)
        if group.get("keywords") not in (None, "", []):
            phrases = actions._phrases(group.get("keywords"))
            if len(phrases) > 200:
                raise YandexError("args", f"Группа «{name}»: больше 200 фраз — раздели её на несколько групп")
            content["keyword"] += phrases
        variants = _group_ad(ctx, group.get("ad"), name, int(account["id"]))
        if variants is None:
            continue
        if not str(group["ad"].get("href") or "").strip() and not href_default:
            raise YandexError("args", f"Группа «{name}»: укажи href объявления — шаблона ссылки в пространстве нет")
        key = (tuple(sorted(variants[0])), tuple(sorted(variants[1])))
        if key in ads:
            raise YandexError(
                "args", f"У групп «{ads[key]}» и «{name}» одинаковые объявления — каждой группе тексты под её фразы"
            )
        ads[key] = name
        content["title"] += variants[0]
        content["text"] += variants[1]
    if groups:
        stages = [
            "goals", "analysis", *(["keywords"] if content["keyword"] else []), *(["ads"] if ads else []), "settings",
        ]
        _require_approved(ctx, {k: v for k, v in content.items() if v}, stages=stages)

    details = []
    strategy = spec.get("strategy") or {}
    if isinstance(strategy, dict) and strategy.get("search"):
        details.append(f"стратегия {strategy.get('search')}")
    if spec.get("daily_budget_rub"):
        details.append(f"бюджет {_money(spec['daily_budget_rub'])}/день")
    if isinstance(strategy, dict) and strategy.get("weekly_budget_rub"):
        details.append(f"бюджет {_money(strategy['weekly_budget_rub'])}/нед.")
    if groups:
        details.append(
            f"{_count(len(groups), 'группа', 'группы', 'групп')}, {_count(len(content['keyword']), *_PHRASE_WORDS)}, "
            f"{_count(len(ads), 'объявление', 'объявления', 'объявлений')}"
        )
    extra = f" ({', '.join(details)})" if details else ""
    return Plan(
        f"Создать кампанию «{str(spec['name']).strip()}» в кабинете {account['client_login']}{extra}",
        ["campaigns"],
    )


def _x_create_campaign(ctx: Context, args: dict) -> dict:
    from app.actions import create_campaign, spec_groups

    account_id = _int(args, "account_id")
    ctx.require_account(account_id)
    spec = copy.deepcopy(args["spec"])
    groups = spec_groups(spec)
    for group in groups:
        ad = group.get("ad")
        # Расширения «как у объявления» — сразу набором: группе новой кампании их не у кого взять.
        if isinstance(ad, dict) and _has_extras(ad):
            ad["extras"] = _ad_extras_args(ctx, ad, "RESPONSIVE_AD", account_id, fetch=True)
            ad.pop("extras_from_ad_row_id", None)
    spec["groups"] = groups
    spec.pop("group", None)
    return create_campaign(ctx.user_id, account_id=account_id, spec=spec, workspace_id=ctx.ws_id)


def _p_duplicate(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    target = args.get("target_account_id")
    where = ""
    if target not in (None, ""):
        account = ctx.require_account(_int(args, "target_account_id"))
        where = f" в кабинет {account['client_login']}"
    name = str(args.get("name") or "").strip()
    return Plan(f"Копия кампании {_cname(row)}{where}" + (f" под именем «{name}»" if name else ""), ["campaigns"])


def _x_duplicate(ctx: Context, args: dict) -> dict:
    from app.actions import duplicate_campaign

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    target = args.get("target_account_id")
    if target not in (None, ""):
        ctx.require_account(int(target))
    return duplicate_campaign(
        ctx.user_id,
        cid,
        target_account_id=int(target) if target not in (None, "") else None,
        name=str(args.get("name") or "").strip() or None,
        workspace_id=ctx.ws_id,
    )


def _p_moderate(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    return Plan(f"Отправить на модерацию {_cname(row)}", ["campaigns"])


def _x_moderate(ctx: Context, args: dict) -> dict:
    from app.actions import moderate_campaign

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    return moderate_campaign(ctx.user_id, cid)


def _p_restart(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    name = str(args.get("name") or "").strip() or f"{row['name']} (копия)"
    steps = [f"копия «{name}» в том же кабинете", "на модерацию"]
    if _bool(args, "pause_source", True):
        steps.append("оригинал на паузу")
        return Plan(f"Перезапуск {_cname(row)} копией: " + " → ".join(steps), ["campaigns", "pause_resume"])
    return Plan(f"Перезапуск {_cname(row)} копией: " + " → ".join(steps) + ", оригинал работает", ["campaigns"])


def _x_restart(ctx: Context, args: dict) -> dict:
    """Копия в том же кабинете → модерация → пауза оригинала. Оригинал останавливаем, только если
    копия собрана целиком и ушла на модерацию, — иначе кампания осталась бы совсем без показов."""
    from app.actions import bulk_campaign_action, duplicate_campaign, moderate_campaign

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    copy = duplicate_campaign(
        ctx.user_id, cid, name=str(args.get("name") or "").strip() or None, workspace_id=ctx.ws_id
    )
    new = copy["campaign"]
    out = {
        "copy": {
            "campaign_row_id": new["id"],
            "name": new["name"],
            "status": copy["status"],
            "groups": copy["groups"],
            "keywords": copy["keywords"],
            "ads": copy["ads"],
        },
        "warnings": copy["warnings"],
        "source_paused": False,
    }
    try:
        out["moderation"] = moderate_campaign(ctx.user_id, new["id"])
    except YandexError as exc:
        out["moderation"] = {"status": "error", "error": exc.message or exc.code}
    if not _bool(args, "pause_source", True):
        out["note"] = "Оригинал работает — остановите его, когда копия пройдёт модерацию"
    elif copy["status"] != "ok" or not out["moderation"].get("sent"):
        out["note"] = "Оригинал не остановлен: копия собрана не полностью или не ушла на модерацию"
    else:
        try:
            bulk_campaign_action(ctx.user_id, campaign_ids=[cid], action="pause", workspace_id=ctx.ws_id)
            out["source_paused"] = True
        except YandexError as exc:
            out["note"] = f"Оригинал не остановлен: {exc.message or exc.code}"
    return out


def _geo_args(ctx: Context, args: dict) -> tuple[dict, list[int], dict | None]:
    """Кампания, регионы копии и кабинет назначения (None — тот же) для copy_campaign_geo."""
    from app import geo

    row = ctx.campaign(_int(args, "campaign_row_id"))
    region_ids = geo.parse_region_ids(args.get("region_ids"))
    if not any(rid > 0 for rid in region_ids):
        raise YandexError("args", "region_ids — хотя бы один регион показа (id из find_regions, без минуса)")
    unknown = geo.unknown_regions(ctx.user_id, region_ids)
    if unknown:
        raise YandexError("args", f"Неизвестные регионы {unknown} — найди id через find_regions")
    target = None
    if args.get("target_account_id") not in (None, ""):
        target = ctx.require_account(_int(args, "target_account_id"))
    return row, region_ids, target


def _geo_name(row: dict, region_ids: list[int], args: dict, regions: dict) -> str:
    from app import geo

    name = _line(args.get("name"), 255)
    return name or f"{row['name']} — {geo.region_label(region_ids, regions, limit=2)}"[:255]


def _p_copy_geo(ctx: Context, args: dict) -> Plan:
    from app import geo

    row, region_ids, target = _geo_args(ctx, args)
    regions = geo.load_regions(ctx.user_id)
    where = f" в кабинет {target['client_login']}" if target else ""
    steps = [f"копия «{_geo_name(row, region_ids, args, regions)}»{where}", geo.region_label(region_ids, regions)]
    if _bool(args, "moderate", True):
        steps.append("на модерацию")
    return Plan(f"Копия {_cname(row)} на новое гео: " + " → ".join(steps) + "; оригинал работает", ["campaigns"])


def _x_copy_geo(ctx: Context, args: dict) -> dict:
    """Копия → регионы во всех группах копии → модерация; оригинал не трогаем.

    Если регионы встали не во все группы, копию не отправляем на модерацию: с регионами оригинала она
    конкурировала бы с ним за те же показы.
    """
    from app import geo
    from app.actions import duplicate_campaign, moderate_campaign

    row, region_ids, target = _geo_args(ctx, args)
    copy = duplicate_campaign(
        ctx.user_id,
        row["id"],
        target_account_id=int(target["id"]) if target else None,
        name=_geo_name(row, region_ids, args, geo.load_regions(ctx.user_id)),
        workspace_id=ctx.ws_id,
    )
    new = copy["campaign"]
    out = {
        "copy": {
            "campaign_row_id": new["id"],
            "name": new["name"],
            "status": copy["status"],
            "groups": copy["groups"],
            "keywords": copy["keywords"],
            "ads": copy["ads"],
        },
        "warnings": copy["warnings"],
    }
    try:
        out["regions"] = geo.set_campaign_regions(ctx.user_id, new["id"], region_ids, workspace_id=ctx.ws_id)
    except YandexError as exc:
        out["regions"] = {"status": "error", "error": exc.message or exc.code}
    regions = out["regions"]
    if regions.get("error") or not regions.get("updated"):
        out["note"] = "Регионы не заменены — копия осталась черновиком с регионами оригинала, на модерацию не отправлена"
    elif regions.get("errors"):
        out["note"] = "Регионы заменены не во всех группах — копия осталась черновиком, на модерацию не отправлена"
    elif copy["status"] != "ok":
        out["note"] = "Копия собрана не полностью — проверьте её и отправьте на модерацию сами"
    elif not _bool(args, "moderate", True):
        out["note"] = "Копия — черновик: отправьте на модерацию, когда проверите"
    else:
        try:
            out["moderation"] = moderate_campaign(ctx.user_id, new["id"])
        except YandexError as exc:
            out["moderation"] = {"status": "error", "error": exc.message or exc.code}
    return out


_EXPERIMENT_NOTE = (
    "Сервис сам ничего не останавливает: расход держит недельный бюджет в Директе. Срок считается с первого "
    "дня показов; прогресс и итоги — get_experiments."
)


def _experiment_args(ctx: Context, args: dict) -> tuple[dict, list[dict], dict | None]:
    """Эксперимент, тестовые кампании и образец для start_experiment. Кампании идущего эксперимента не берём:
    две проверки на одной кампании смешали бы результаты."""
    d = experiments.normalize_definition(args)
    rows = ctx.campaigns(d["campaign_row_ids"])
    source = ctx.campaign(d["source_campaign_row_id"]) if d["source_campaign_row_id"] else None
    busy = experiments.busy_campaigns(ctx.user_id, ctx.ws_id)
    taken = [
        f"{_cname(r)} — в #{busy[int(r['id'])]['id']} «{busy[int(r['id'])]['title']}»"
        for r in rows
        if int(r["id"]) in busy
    ]
    if taken:
        raise YandexError(
            "busy",
            "Кампании уже в идущих экспериментах: " + "; ".join(taken)
            + ". Заверши эксперимент (finish_experiment) или возьми другие кампании",
        )
    return d, rows, source


def _p_start_experiment(ctx: Context, args: dict) -> Plan:
    d, rows, _source = _experiment_args(ctx, args)
    set_limit = _bool(args, "set_limit", True)
    limit = f"лимит {_money(d['weekly_budget_rub'])}/нед. на кампанию" + ("" if set_limit else " (ставит маркетолог)")
    summary = (
        f"Эксперимент «{d['title']}»: {_cnames(rows)}, {limit}, {_count(d['days'], 'день', 'дня', 'дней')}, "
        f"успех — {experiments.criterion_text(d)}"
    )
    if set_limit:
        # Новый потолок расхода — всегда на согласование, кроме полного доступа.
        return Plan(summary, ["campaigns", "budget"], math.inf)
    return Plan(summary, ["campaigns"])


def _x_start_experiment(ctx: Context, args: dict) -> dict:
    """Лимиты в Директе → запись эксперимента. Лимит не встал — эксперимент не записываем."""
    d, rows, _source = _experiment_args(ctx, args)
    limits = experiments.apply_limits(ctx.user_id, rows, d["weekly_budget_rub"]) if _bool(args, "set_limit", True) else []
    action = getattr(ctx, "action", None) or {}
    exp = experiments.create_experiment(
        ctx.user_id, ctx.ws_id, d, chat_id=action.get("chat_id"), action_id=action.get("id")
    )
    started = exp["started_on"]
    return {
        "experiment": {
            "id": exp["id"],
            "title": exp["title"],
            "started_on": _iso(started),
            "days": exp["days"],
            "ends_on": _iso(started + timedelta(days=int(exp["days"]) - 1)),
        },
        "limits": limits,
        "note": _EXPERIMENT_NOTE if limits else "Лимит в Директе не меняли — его ставит маркетолог. " + _EXPERIMENT_NOTE,
    }


def _variants_args(ctx: Context, args: dict) -> tuple[dict, list[dict], dict | None]:
    """Кампания-источник, варианты и эксперимент (None — без него) для create_campaign_variants."""
    from app import variants

    row = ctx.campaign(_int(args, "campaign_row_id"))
    items = variants.normalize_variants(args.get("variants"), row["name"])
    exp = None
    raw = args.get("experiment")
    if raw not in (None, "", {}):
        if not isinstance(raw, dict):
            raise YandexError("args", "experiment — объект {title, hypothesis, weekly_budget_rub, days, criterion}")
        # Тестовых кампаний ещё нет: id источника — заглушка для проверки, настоящие id встанут после копий.
        exp = experiments.normalize_definition(
            {**raw, "kind": raw.get("kind") or "variants", "campaign_row_ids": [row["id"]], "source_campaign_row_id": row["id"]}
        )
        for v in items:
            if v.get("weekly_budget_rub", 0) > exp["weekly_budget_rub"]:
                raise YandexError(
                    "args",
                    f"Вариант {v['n']}: бюджет {_money(v['weekly_budget_rub'])}/нед. больше лимита эксперимента "
                    f"{_money(exp['weekly_budget_rub'])}/нед.",
                )
    variants.check_source(ctx.user_id, row, items)
    return row, items, exp


def _p_create_variants(ctx: Context, args: dict) -> Plan:
    row, items, exp = _variants_args(ctx, args)
    names = ", ".join(f"«{v['name']}»" + (" (контроль)" if v.get("control") else "") for v in items)
    steps = [f"{_count(len(items), 'вариант', 'варианта', 'вариантов')} {_cname(row)}: {names}"]
    steps.append("на модерацию" if _bool(args, "moderate", False) else "черновиками")
    if exp:
        steps.append(
            f"эксперимент «{exp['title']}», лимит {_money(exp['weekly_budget_rub'])}/нед. на вариант, "
            f"{_count(exp['days'], 'день', 'дня', 'дней')}, успех — {experiments.criterion_text(exp)}"
        )
    else:
        steps.append("без эксперимента")
    summary = "Варианты кампании: " + " → ".join(steps) + "; оригинал работает"
    categories = ["campaigns"]
    parts = [*items, *(g for v in items for g in v.get("groups") or [])]
    if any("ads" in p for p in parts):
        categories.append("ads")
    if any("keywords" in p for p in parts):
        categories.append("keywords")
    if exp or any("weekly_budget_rub" in v for v in items):
        # Новые кампании с расходом — всегда на согласование, кроме полного доступа.
        return Plan(summary, [*categories, "budget"], math.inf)
    return Plan(summary, categories)


def _x_create_variants(ctx: Context, args: dict) -> dict:
    """Варианты по очереди → лимиты → эксперимент по собранным целиком вариантам. Оригинал не трогаем."""
    from app import variants

    row, items, exp = _variants_args(ctx, args)
    action = getattr(ctx, "action", None) or {}
    chat_id = action.get("chat_id")
    made = variants.create_variants(
        ctx.user_id,
        row["id"],
        items,
        workspace_id=ctx.ws_id,
        moderate=_bool(args, "moderate", False),
        tick=(lambda: _touch(chat_id)) if chat_id else None,
    )
    out: dict = {"source": {"campaign_row_id": row["id"], "name": row["name"]}, "variants": made}
    ready = [v for v in made if v.get("ready")]
    if exp is None:
        return out
    if not ready:
        out["experiment"] = {"status": "skipped", "note": "Ни один вариант не собран целиком — эксперимент не начат"}
        return out
    rows = [{"id": v["campaign_row_id"], "name": v["name"]} for v in ready]
    try:
        limits = experiments.apply_limits(ctx.user_id, rows, exp["weekly_budget_rub"])
    except YandexError as exc:
        out["experiment"] = {"status": "error", "error": exc.message or exc.code}
        return out
    d = {**exp, "campaign_row_ids": [r["id"] for r in rows]}
    created = experiments.create_experiment(ctx.user_id, ctx.ws_id, d, chat_id=chat_id, action_id=action.get("id"))
    started = created["started_on"]
    out["experiment"] = {
        "id": created["id"],
        "title": created["title"],
        "campaigns": len(rows),
        "started_on": _iso(started),
        "ends_on": _iso(started + timedelta(days=int(created["days"]) - 1)),
        "limits": limits,
        "note": _EXPERIMENT_NOTE,
    }
    if len(ready) < len(made):
        out["experiment"]["note"] += " Варианты с ошибками в эксперимент не вошли."
    return out


def _finish_args(ctx: Context, args: dict) -> tuple[dict, str, str, bool]:
    exp = experiments.get_experiment(ctx.user_id, _int(args, "experiment_id"), workspace_id=ctx.ws_id)
    if exp["status"] != "running":
        status = experiments.STATUS_LABELS.get(exp["status"], exp["status"])
        raise YandexError("finished", f"Эксперимент «{exp['title']}» уже {status}")
    cancel = _bool(args, "cancel", False)
    verdict, conclusion = experiments.normalize_finish(args.get("verdict"), args.get("conclusion"), cancel)
    if not cancel and not conclusion:
        raise YandexError("args", "Укажите conclusion — что узнали и что делать дальше")
    return exp, verdict, conclusion, cancel


def _p_finish_experiment(ctx: Context, args: dict) -> Plan:
    exp, verdict, _conclusion, cancel = _finish_args(ctx, args)
    if cancel:
        return Plan(f"Отменить эксперимент «{exp['title']}»", ["campaigns"])
    return Plan(f"Итоги эксперимента «{exp['title']}»: гипотеза {experiments.VERDICTS[verdict]}", ["campaigns"])


def _x_finish_experiment(ctx: Context, args: dict) -> dict:
    exp, verdict, conclusion, cancel = _finish_args(ctx, args)
    row = experiments.finish_experiment(
        ctx.user_id, int(exp["id"]), verdict=verdict, conclusion=conclusion, cancel=cancel, workspace_id=ctx.ws_id
    )
    return {
        "experiment": {"id": row["id"], "title": row["title"], "status": row["status"], "verdict": row["verdict"]},
        "note": "Кампании эксперимента не тронуты: остановить или масштабировать — отдельными действиями",
    }


def _p_create_adgroup(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    name = str(args.get("name") or "").strip()
    if not name:
        raise YandexError("args", "Укажите name группы")
    return Plan(f"Новая группа «{name}» в кампании {_cname(row)}", ["campaigns"])


def _x_create_adgroup(ctx: Context, args: dict) -> dict:
    from app.adgroups import create_adgroup

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    regions = [int(r) for r in args.get("region_ids") or [] if str(r).lstrip("-").isdigit()]
    return create_adgroup(ctx.user_id, campaign_row_id=cid, name=str(args["name"]), region_ids=regions or None)


_KEYWORD_ACTIONS = {"suspend": "Остановить", "resume": "Запустить", "delete": "Удалить", "set_bid": "Ставка"}


def _p_keywords_action(ctx: Context, args: dict) -> Plan:
    action = str(args.get("action") or "")
    if action not in _KEYWORD_ACTIONS:
        raise YandexError("args", f"action — одно из {list(_KEYWORD_ACTIONS)}")
    rows = ctx.keywords(_ids(args, "keyword_row_ids"))
    summary = f"{_KEYWORD_ACTIONS[action]} ключевые фразы: {_names(rows)}"
    if action in ("suspend", "resume"):
        return Plan(summary, ["pause_resume"])
    if action == "delete":
        return Plan(summary, ["destructive"])
    bid = _float(args, "bid_rub")
    if not bid or bid <= 0:
        raise YandexError("args", "Укажите bid_rub больше нуля")
    change = max(_pct_change(r["bid_micros"], bid * 1_000_000) for r in rows)
    return Plan(f"{summary} → {bid:g} ₽", ["bids"], change)


def _x_keywords_action(ctx: Context, args: dict) -> dict:
    from app.keywords import bulk_keyword_action

    ids = _ids(args, "keyword_row_ids")
    ctx.keywords(ids)
    return bulk_keyword_action(ctx.user_id, keyword_row_ids=ids, action=str(args["action"]), bid_rub=_float(args, "bid_rub"))


def _group_keywords(ctx: Context, adgroup_row_id: int) -> list[str]:
    with pool.connection() as conn:
        return [
            r["keyword"]
            for r in conn.execute(
                "SELECT keyword FROM direct_keywords WHERE user_id = %s AND adgroup_row_id = %s",
                (ctx.user_id, adgroup_row_id),
            ).fetchall()
        ]


def _p_add_keywords(ctx: Context, args: dict) -> Plan:
    group = ctx.adgroup(_int(args, "adgroup_row_id"))
    new, already = _split_new(_phrases(args), _group_keywords(ctx, int(group["id"])))
    if _made_here(ctx, int(group["campaign_row_id"])):
        _require_approved(ctx, {"keyword": new}, stages=[])
    bid = _float(args, "bid_rub")
    return Plan(
        f"Фразы в группу «{group['name']}»: {_added(new, already, 5)}" + (f" (ставка {bid:g} ₽)" if bid and new else ""),
        ["keywords"],
    )


def _x_add_keywords(ctx: Context, args: dict) -> dict:
    from app.keywords import create_keywords

    group_id = _int(args, "adgroup_row_id")
    ctx.adgroup(group_id)
    return create_keywords(ctx.user_id, adgroup_row_id=group_id, phrases=_phrases(args), bid_rub=_float(args, "bid_rub"))


_QUERY_ACTIONS = {
    "add_keyword": "Добавить как ключевые фразы",
    "negative_adgroup": "В минус-фразы группы",
    "negative_campaign": "В минус-фразы кампании",
}


def _p_queries_action(ctx: Context, args: dict) -> Plan:
    action = str(args.get("action") or "")
    if action not in _QUERY_ACTIONS:
        raise YandexError("args", f"action — одно из {list(_QUERY_ACTIONS)}")
    rows = ctx.queries(_ids(args, "query_row_ids"))
    if action == "add_keyword" and args.get("adgroup_row_id") not in (None, ""):
        ctx.adgroup(_int(args, "adgroup_row_id"))
    summary = f"{_QUERY_ACTIONS[action]}: {_names(rows, 5)}"
    return Plan(summary, ["keywords"] if action == "add_keyword" else ["negatives"])


def _x_queries_action(ctx: Context, args: dict) -> dict:
    from app.search_queries import bulk_search_query_action

    ids = _ids(args, "query_row_ids")
    ctx.queries(ids)
    group = args.get("adgroup_row_id")
    if group not in (None, ""):
        ctx.adgroup(int(group))
    return bulk_search_query_action(
        ctx.user_id,
        query_ids=ids,
        action=str(args["action"]),
        adgroup_row_id=int(group) if group not in (None, "") else None,
        bid_rub=_float(args, "bid_rub"),
    )


def _variants(args: dict, key: str) -> list[str] | None:
    """Список вариантов (titles/texts) из аргументов модели; None — не передан."""
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise YandexError("args", f"{key} — список строк")
    return [str(v).strip() for v in value if str(v or "").strip()]


def _new_ad(ctx: Context, args: dict) -> tuple[dict, str, list[str], list[str]]:
    """Группа, тип нового объявления и его варианты — с проверкой пределов Директа, без обрезки."""
    from app.ads import MAX_TEXTS, MAX_TITLES, TEXT_LEN, TITLE_LEN, clean_title2, clean_variants, new_ad_type

    group = ctx.adgroup(_int(args, "adgroup_row_id"))
    ad_type = new_ad_type(group)
    responsive = ad_type == "RESPONSIVE_AD"
    titles = clean_variants(
        _variants(args, "titles") or [args.get("title")], MAX_TITLES if responsive else 1, TITLE_LEN, "заголовков"
    )
    texts = clean_variants(_variants(args, "texts") or [args.get("text")], MAX_TEXTS if responsive else 1, TEXT_LEN, "текстов")
    if not titles:
        raise YandexError("args", "Укажите title или titles")
    if not texts:
        raise YandexError("args", "Укажите text или texts")
    if responsive and str(args.get("title2") or "").strip():
        raise YandexError(
            "args",
            "Новое объявление в этой группе комбинаторное — второго заголовка в нём нет: "
            "добавь его в titles отдельным вариантом или убери title2",
        )
    clean_title2(args.get("title2"))
    return group, ad_type, titles, texts


def _ad_extras_args(ctx: Context, args: dict, ad_type: str, account_id: int, *, fetch: bool = False) -> dict | None:
    """extras и extras_from_ad_row_id → extras для ads: свои ключи важнее взятых у другого объявления.
    fetch=False — только проверка для карточки, без запросов в Директ."""
    from app import ad_extras
    from app.ads import extras_like

    raw = args.get("extras")
    if raw is not None and not isinstance(raw, dict):
        raise YandexError("args", "extras — объект {sitelinks, callouts, image_hashes}")
    own = {k: v for k, v in (raw or {}).items() if k in ("sitelinks", "callouts", "image_hashes") and v is not None}
    for key, value in own.items():
        if not isinstance(value, list):
            raise YandexError("args", f"extras.{key} — список")
    if not all(isinstance(link, dict) for link in own.get("sitelinks") or []):
        raise YandexError("args", "extras.sitelinks — список объектов {title, href, description}")
    out: dict = {}
    source = args.get("extras_from_ad_row_id")
    if source not in (None, ""):
        src = ctx.ads([int(source)])[0]
        if int(src["account_id"]) != int(account_id):
            raise YandexError("args", "Расширения можно взять только у объявления из того же кабинета")
        if fetch:
            out = extras_like(ctx.user_id, int(src["id"]))
    out.update(own)
    ad_extras.clean_extras(out, ad_type)
    return out or None


def _has_extras(args: dict) -> bool:
    if args.get("extras_from_ad_row_id") not in (None, ""):
        return True
    extras = args.get("extras")
    return isinstance(extras, dict) and any(extras.get(k) is not None for k in ("sitelinks", "callouts", "image_hashes"))


def _twin_ads(ctx: Context, campaign_row_id: int, titles: list[str], texts: list[str]) -> list[dict]:
    """Объявления кампании с теми же заголовками и текстами, в любом порядке."""
    key = (sorted(titles), sorted(texts))
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT ad.id, ad.title, ad.text, ad.titles, ad.texts, ad.adgroup_row_id, g.name AS adgroup_name
            FROM direct_ads ad JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            WHERE ad.user_id = %s AND g.campaign_row_id = %s AND ad.state <> 'ARCHIVED'
            """,
            (ctx.user_id, campaign_row_id),
        ).fetchall()
    return [
        r
        for r in rows
        if (sorted(_json_list(r["titles"]) or [r["title"]]), sorted(_json_list(r["texts"]) or [r["text"]])) == key
    ]


def _p_create_ad(ctx: Context, args: dict) -> Plan:
    group, ad_type, titles, texts = _new_ad(ctx, args)
    if not str(args.get("href") or "").strip():
        raise YandexError("args", "Укажите href")
    if any(r["adgroup_row_id"] == group["id"] for r in _twin_ads(ctx, group["campaign_row_id"], titles, texts)):
        raise YandexError(
            "args",
            f"В группе «{group['name']}» уже есть объявление с теми же заголовками и текстами — второе такое же "
            "ничего не добавит. Поменяй формулировки или не создавай его",
        )
    _ad_extras_args(ctx, args, ad_type, group["account_id"])
    if _made_here(ctx, int(group["campaign_row_id"])):
        _require_approved(ctx, {"title": titles, "text": texts}, stages=[])
    more = ""
    if len(titles) + len(texts) > 2:
        more = f" ({_count(len(titles), 'заголовок', 'заголовка', 'заголовков')}, {_count(len(texts), 'текст', 'текста', 'текстов')})"
    return Plan(f"Объявление в группу «{group['name']}»: «{titles[0]}» — {texts[0]}{more}", ["ads"])


def _x_create_ad(ctx: Context, args: dict) -> dict:
    from app.ads import create_text_ad

    group, ad_type, titles, texts = _new_ad(ctx, args)
    return create_text_ad(
        ctx.user_id,
        adgroup_row_id=int(group["id"]),
        title2=str(args.get("title2") or ""),
        href=str(args["href"]),
        titles=titles,
        texts=texts,
        extras=_ad_extras_args(ctx, args, ad_type, group["account_id"], fetch=True),
    )


_AD_FIELDS = ("title", "title2", "text", "href", "titles", "texts")


def _p_update_ad(ctx: Context, args: dict) -> Plan:
    from app.ads import MAX_TEXTS, MAX_TITLES, TEXT_LEN, TITLE_LEN, clean_title2, clean_variants

    row = ctx.ads([_int(args, "ad_row_id")])[0]
    fields = [k for k in _AD_FIELDS if args.get(k) is not None]
    if not fields and not _has_extras(args):
        raise YandexError("args", "Укажите, что поменять: title, title2, text, href, titles, texts или extras")
    # Точные пределы — по типу из Директа при выполнении: тип в базе мог устареть.
    for key, limit, length, what in (("titles", MAX_TITLES, TITLE_LEN, "заголовков"), ("texts", MAX_TEXTS, TEXT_LEN, "текстов")):
        if args.get(key) is not None and not clean_variants(_variants(args, key), limit, length, what):
            raise YandexError("args", f"{key} не может быть пустым")
    for key, length in (("title", TITLE_LEN), ("text", TEXT_LEN)):
        if args.get(key) is not None:
            clean_variants([args[key]], 1, length)
    if args.get("title2") is not None:
        if row["type"] == "RESPONSIVE_AD":
            raise YandexError(
                "args", "Объявление комбинаторное — второго заголовка в нём нет: добавь его в titles отдельным вариантом"
            )
        clean_title2(args["title2"])
    _ad_extras_args(ctx, args, row["type"] or "TEXT_AD", row["account_id"])
    what = fields + (["расширения"] if _has_extras(args) else [])
    return Plan(f"Правка объявления «{row['title']}»: {', '.join(what)}", ["ads"])


def _x_update_ad(ctx: Context, args: dict) -> dict:
    from app.ads import update_text_ad

    row = ctx.ads([_int(args, "ad_row_id")])[0]
    return update_text_ad(
        ctx.user_id,
        int(row["id"]),
        **{k: str(args[k]) for k in ("title", "title2", "text", "href") if args.get(k) is not None},
        titles=_variants(args, "titles"),
        texts=_variants(args, "texts"),
        extras=_ad_extras_args(ctx, args, row["type"] or "TEXT_AD", row["account_id"], fetch=True),
    )


MAX_BULK_ADS = 30


def _ad_items(ctx: Context, args: dict) -> list[tuple[dict, dict]]:
    """Правки update_text_ads с объявлениями из базы, в порядке items."""
    items = args.get("items")
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        raise YandexError("args", "items — непустой список правок {ad_row_id, поля}")
    if len(items) > MAX_BULK_ADS:
        raise YandexError("args", f"За раз — до {MAX_BULK_ADS} объявлений")
    ids = [_int(i, "ad_row_id") for i in items]
    if len(set(ids)) < len(ids):
        raise YandexError("args", "Одно объявление указано дважды — собери его правки в одну запись")
    rows = {int(r["id"]): r for r in ctx.ads(ids)}
    return [(item, rows[i]) for item, i in zip(items, ids)]


def _ad_name(row: dict) -> str:
    return f"«{row['title'] or row['id']}»"


def _p_update_ads(ctx: Context, args: dict) -> Plan:
    pairs = _ad_items(ctx, args)
    for item, row in pairs:
        try:
            _p_update_ad(ctx, item)
        except YandexError as exc:
            raise YandexError("args", f"Объявление {_ad_name(row)}: {exc.message or exc.code}") from exc
    return Plan(f"Правка {_count(len(pairs), 'объявления', 'объявлений', 'объявлений')}: {_names([r for _i, r in pairs])}", ["ads"])


def _x_update_ads(ctx: Context, args: dict) -> dict:
    done, failed = [], []
    for item, row in _ad_items(ctx, args):
        try:
            _x_update_ad(ctx, item)
            done.append(_ad_name(row))
        except YandexError as exc:
            failed.append(f"{_ad_name(row)} — {exc.message or exc.code}")
    if failed:
        raise YandexError("ads", (f"Изменены: {', '.join(done)}; " if done else "") + "не изменены: " + "; ".join(failed))
    return {"updated": len(done), "ads": done}


_AD_ACTIONS = {
    "suspend": "Остановить",
    "resume": "Запустить",
    "archive": "Архивировать",
    "unarchive": "Вернуть из архива",
    "delete": "Удалить",
}


def _p_ads_action(ctx: Context, args: dict) -> Plan:
    action = str(args.get("action") or "")
    if action not in _AD_ACTIONS:
        raise YandexError("args", f"action — одно из {list(_AD_ACTIONS)}")
    rows = ctx.ads(_ids(args, "ad_row_ids"))
    cats = ["pause_resume"] if action in ("suspend", "resume") else ["destructive"]
    return Plan(f"{_AD_ACTIONS[action]} объявления: {_names(rows)}", cats)


def _x_ads_action(ctx: Context, args: dict) -> dict:
    from app.ads import bulk_ad_action

    ids = _ids(args, "ad_row_ids")
    ctx.ads(ids)
    return bulk_ad_action(ctx.user_id, ad_row_ids=ids, action=str(args["action"]))


def _links_args(ctx: Context, args: dict) -> tuple[list[dict], dict]:
    """Кампании с косяками ссылок из запрошенных: без шаблона или без косяков — ошибка для модели."""
    from app import links

    rows = ctx.campaigns(_ids(args, "campaign_row_ids"))
    issues = links.workspace_link_issues(ctx.user_id, ctx.ws_id, [r["id"] for r in rows])
    if not issues["configured"]:
        raise YandexError("args", "Шаблон ссылки сайта не задан — его задают в настройках пространства")
    bad = {c["campaign_row_id"]: c for c in issues["campaigns"]}
    picked = [r for r in rows if r["id"] in bad]
    if not picked:
        raise YandexError("args", "В этих кампаниях ссылки по шаблону — исправлять нечего")
    return picked, bad


def _p_fix_links(ctx: Context, args: dict) -> Plan:
    rows, bad = _links_args(ctx, args)
    ads = sum(bad[r["id"]]["ads_fixable"] for r in rows)
    summary = f"Ссылки по шаблону сайта: {_cnames(rows)}"
    if ads:
        summary += f"; {_count(ads, 'объявление', 'объявления', 'объявлений')} уйдут на модерацию"
    return Plan(summary, ["campaigns", "ads"] if ads else ["campaigns"])


def _x_fix_links(ctx: Context, args: dict) -> dict:
    from app import links

    rows, _bad = _links_args(ctx, args)
    return links.fix_links(ctx.user_id, [r["id"] for r in rows])


WRITE_TOOLS = {
    "campaigns_action": (_p_campaigns_action, _x_campaigns_action),
    "update_campaign_settings": (_p_update_settings, _x_update_settings),
    "add_campaign_negatives": (_p_add_negatives, _x_add_negatives),
    "create_campaign": (_p_create_campaign, _x_create_campaign),
    "duplicate_campaign": (_p_duplicate, _x_duplicate),
    "moderate_campaign": (_p_moderate, _x_moderate),
    "restart_campaign_copy": (_p_restart, _x_restart),
    "copy_campaign_geo": (_p_copy_geo, _x_copy_geo),
    "start_experiment": (_p_start_experiment, _x_start_experiment),
    "create_campaign_variants": (_p_create_variants, _x_create_variants),
    "finish_experiment": (_p_finish_experiment, _x_finish_experiment),
    "create_adgroup": (_p_create_adgroup, _x_create_adgroup),
    "keywords_action": (_p_keywords_action, _x_keywords_action),
    "add_keywords": (_p_add_keywords, _x_add_keywords),
    "search_queries_action": (_p_queries_action, _x_queries_action),
    "create_text_ad": (_p_create_ad, _x_create_ad),
    "update_text_ad": (_p_update_ad, _x_update_ad),
    "update_text_ads": (_p_update_ads, _x_update_ads),
    "ads_action": (_p_ads_action, _x_ads_action),
    "fix_links": (_p_fix_links, _x_fix_links),
}


# --- «Было → станет»: что именно изменит действие ----------------------------------------------
#
# Таблица строится при подготовке действия по тем же правилам, что и само изменение, и хранится в
# args["_diff"]: карточка показывает её вместо одной строки summary. Не вышло собрать — действие всё
# равно создаётся, карточка останется с summary.

_NO_DATA = "нет данных"
_PHRASE_WORDS = ("фраза", "фразы", "фраз")
_SITE_WORDS = ("площадка", "площадки", "площадок")
_STATE_LABELS = {
    "ON": "работает",
    "OFF": "не показывается",
    "SUSPENDED": "на паузе",
    "OFF_BY_MONITORING": "на паузе (сайт недоступен)",
    "ENDED": "закончилась",
    "CONVERTED": "переведена в другой тип",
    "ARCHIVED": "в архиве",
}
_STATUS_LABELS = {"DRAFT": "черновик", "MODERATION": "на модерации", "REJECTED": "отказ модерации"}
_ERROR_TEXTS = {
    "budget": "неверная сумма бюджета",
    "negatives-limit": "минус-фразы длиннее лимита Директа",
    "limit": "слишком много за раз",
}


def _state_label(state, status=None) -> str:
    if status in _STATUS_LABELS:
        return _STATUS_LABELS[status]
    return _STATE_LABELS.get(state or "", state or _NO_DATA)


def _fail(exc: YandexError) -> str:
    return "не выйдет: " + (exc.message or _ERROR_TEXTS.get(exc.code, exc.code))


def _try(fn) -> str:
    """«Станет» для карточки; если изменение не пройдёт проверку — почему."""
    try:
        return fn()
    except YandexError as exc:
        return _fail(exc)
    except (TypeError, ValueError, AttributeError):
        return "не выйдет: неверное значение"


def _ch(what: str, before, after) -> dict:
    return {"what": what, "before": before, "after": after}


def _json_list(raw) -> list:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    return list(raw) if isinstance(raw, list) else []


def _strategy_text(flat: dict | None, names: dict[int, str]) -> str:
    """Плоская стратегия (strategy_from_api) → «Оплата за конверсии + сети, CPA 500 ₽, цель 5001 «Заказ»»."""
    from app.strategy_info import STRATEGY_LABELS, brief_label, goal_label

    flat = flat or {}
    sides = {
        side: {"type": flat[side], "label": STRATEGY_LABELS.get(flat[side], flat[side])}
        for side in ("search", "network")
        if flat.get(side)
    }
    if not sides:
        return _NO_DATA
    parts = [brief_label(sides) or _NO_DATA]
    if flat.get("average_cpa_rub"):
        parts.append(f"CPA {_money(flat['average_cpa_rub'])}")
    if flat.get("average_cpc_rub"):
        parts.append(f"клик {_price(flat['average_cpc_rub'])}")
    if flat.get("weekly_budget_rub"):
        parts.append(f"{_money(flat['weekly_budget_rub'])}/нед.")
    if flat.get("bid_ceiling_rub"):
        parts.append(f"ставка до {_price(flat['bid_ceiling_rub'])}")
    if flat.get("goal_id") is not None:
        parts.append(f"цель {goal_label(flat['goal_id'], names)}")
    return ", ".join(parts)


def _daily_text(value, manual: bool | None, weekly: bool) -> str:
    """Дневной бюджет «станет» — по правилам update_campaign_settings и create_campaign."""
    if value in (None, "", 0):
        return "не задан"
    if manual is False:
        raise YandexError("budget", "Дневной бюджет — только для ручных ставок; у автостратегии задайте недельный")
    if weekly:
        raise YandexError("budget", "Задан недельный бюджет ручных ставок — дневной не нужен")
    amount = float(value)
    if amount <= 0:
        raise YandexError("budget")
    return _money(amount)


def _schedule_text(grid) -> str:
    from app.actions import SCHEDULE_PRESETS, schedule_grid

    if not grid:
        return SCHEDULE_PRESETS["always"]
    for key, text in SCHEDULE_PRESETS.items():
        if key != "always" and schedule_grid(key) == grid:
            return text
    return "своё почасовое расписание"


def _weekends_text(value) -> str:
    return "учитываются" if value else "не учитываются"


def _goals_text(goals: list[dict], names: dict[int, str]) -> str:
    from app.strategy_info import goal_label

    parts = [
        goal_label(g.get("goal_id"), names) + (f" — {_money(g['value_rub'])}" if g.get("value_rub") else "")
        for g in goals
        if isinstance(g, dict)
    ]
    return "; ".join(parts) or "не заданы"


def _attribution_text(model) -> str:
    from app.actions import ATTRIBUTION_MODELS

    return ATTRIBUTION_MODELS.get(model, model) if model else "не задана"


def _list_rows(what: str, old: list | None, build, key, words: tuple[str, str, str]) -> list[dict]:
    """Замена списка целиком: сколько было и станет, что добавится и что уберётся."""
    try:
        new = build()
    except YandexError as exc:
        return [_ch(what, _count(len(old), *words) if old is not None else _NO_DATA, _fail(exc))]
    if old is None:
        return [_ch(what, _NO_DATA, f"{_count(len(new), *words)}: {_preview(new)}" if new else "пусто")]
    old_keys = {key(p) for p in old}
    new_keys = {key(p) for p in new}
    added = [p for p in new if key(p) not in old_keys]
    removed = [p for p in old if key(p) not in new_keys]
    if not added and not removed:
        return [_ch(what, _count(len(old), *words), "без изменений")]
    rows = [_ch(what, _count(len(old), *words), _count(len(new), *words))]
    if added:
        rows.append(_ch(f"{what}: добавятся", "—", _preview(added)))
    if removed:
        rows.append(_ch(f"{what}: уберутся", _preview(removed), "—"))
    return rows


def _current_settings(ctx: Context, cid: int) -> dict:
    """Настройки кампании сейчас: из Директа, а если он недоступен — по последнему синку (не все поля)."""
    from app.actions import get_campaign_settings, strategy_from_api

    try:
        return get_campaign_settings(ctx.user_id, cid)
    except Exception as exc:  # таблица «было → станет» не должна ломать действие
        log.info("Настройки кампании %s для «было → станет» — из базы: %s", cid, exc)
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT name, campaign_type, start_date, daily_budget_kopecks, negative_keywords, counter_ids,
                   bidding_strategy, priority_goals, attribution_model
            FROM direct_campaigns WHERE user_id = %s AND id = %s
            """,
            (ctx.user_id, cid),
        ).fetchone()
    if row is None:
        return {}
    out: dict = {
        "name": row["name"],
        "type": row.get("campaign_type") or "",
        "start_date": _iso(row.get("start_date")),
        "daily_budget_rub": row["daily_budget_kopecks"] / 100 if row.get("daily_budget_kopecks") else None,
    }
    for key in ("negative_keywords", "counter_ids", "priority_goals"):
        if row.get(key) is not None:
            out[key] = _json_list(row[key])
    bidding = row.get("bidding_strategy")
    if isinstance(bidding, str):
        try:
            bidding = json.loads(bidding)
        except ValueError:
            bidding = None
    if isinstance(bidding, dict) and bidding:
        out["strategy"] = strategy_from_api(bidding)
    if row.get("attribution_model") is not None:
        out["attribution_model"] = row["attribution_model"]
    return out


_CAMPAIGN_AFTER = {
    "pause": "на паузе",
    "resume": "работает",
    "archive": "в архиве",
    "unarchive": "на паузе",
    "delete": "удалена",
}


def _d_campaigns_action(ctx: Context, args: dict) -> list[dict]:
    action = str(args["action"])
    rows = ctx.campaigns(_ids(args, "campaign_row_ids"))
    if action != "set_budget":
        return [_ch(_cname(r), _state_label(r["state"], r["status"]), _CAMPAIGN_AFTER[action]) for r in rows]
    daily = _float(args, "daily_budget_rub")
    pct = _float(args, "budget_change_pct")
    out = []
    for r in rows:
        old = r["daily_budget_kopecks"]
        if daily is not None:
            new = round(daily * 100)
        elif old:
            new = round(old * (1 + pct / 100))
        else:
            new = None
        if new is None:
            after = "не задан — укажите сумму"
        elif new <= 0:
            after = "не выйдет: неверная сумма бюджета"
        else:
            after = _money(new / 100)
        out.append(_ch(f"{_cname(r)}: дневной бюджет", _money(old / 100) if old else "не задан", after))
    return out


def _d_update_settings(ctx: Context, args: dict) -> list[dict]:
    from app import actions
    from app.negative_lists import phrase_key

    cid = _int(args, "campaign_row_id")
    row = ctx.campaign(cid)
    changes = dict(args.get("changes") or {})
    cur = _current_settings(ctx, cid)
    names = _goal_names(ctx)
    ctype = cur.get("type") or row.get("campaign_type") or "TEXT_CAMPAIGN"
    out: list[dict] = []

    def label(key: str) -> str:
        text = _SETTINGS_LABELS[key]
        return text[:1].upper() + text[1:]

    def before(key: str, fmt) -> str:
        return fmt(cur[key]) if key in cur else _NO_DATA

    # Ручные ли ставки станут (или останутся): от этого зависит, возможен ли дневной бюджет.
    manual, weekly = None, False
    if changes.get("strategy"):
        try:
            bidding, manual = actions.build_bidding_strategy(changes["strategy"], ctype)
            weekly = bool(actions.manual_weekly_limit(bidding))
        except (YandexError, TypeError, ValueError, AttributeError):
            pass
    elif (cur.get("strategy") or {}).get("search"):
        manual = bool(cur["strategy"].get("manual"))
        weekly = manual and bool(cur["strategy"].get("weekly_budget_rub"))

    for key, value in changes.items():
        if key not in _SETTINGS_LABELS:
            continue
        if key == "name":
            out.append(_ch(label(key), before(key, lambda v: f"«{v}»"), _try(lambda: f"«{actions._name(value)}»")))
        elif key in ("start_date", "end_date"):
            field = _SETTINGS_LABELS[key]

            def new_date() -> str:
                parsed = actions._date(value, field)
                if parsed:
                    return _dmy(parsed.isoformat())
                return "без изменений" if key == "start_date" else "не задана"

            out.append(_ch(label(key), before(key, lambda v: _dmy(v) if v else "не задана"), _try(new_date)))
        elif key == "strategy":
            if not value:
                continue
            out.append(
                _ch(
                    label(key),
                    before(key, lambda v: _strategy_text(v, names)),
                    _try(lambda: _strategy_text(actions.strategy_from_api(actions.build_bidding_strategy(value, ctype)[0]), names)),
                )
            )
            if cur.get("daily_budget_rub") and manual is not None and (not manual or weekly) and "daily_budget_rub" not in changes:
                out.append(_ch("Дневной бюджет", _money(cur["daily_budget_rub"]), "не задан — у этой стратегии его нет"))
        elif key == "daily_budget_rub":
            out.append(
                _ch(
                    label(key),
                    before(key, lambda v: _money(v) if v else "не задан"),
                    _try(lambda: _daily_text(value, manual, weekly)),
                )
            )
        elif key == "negative_keywords":
            out += _list_rows(
                "Минус-фразы", cur.get(key) if key in cur else None, lambda: actions._negatives(value), phrase_key, _PHRASE_WORDS
            )
        elif key == "excluded_sites":
            out += _list_rows(
                "Запрещённые площадки",
                cur.get(key) if key in cur else None,
                lambda: actions._sites(value),
                lambda s: str(s).lower(),
                _SITE_WORDS,
            )
        elif key == "schedule":
            try:
                grid = actions.schedule_grid(value)
                new = _schedule_text(grid)
            except YandexError as exc:
                grid, new = None, _fail(exc)
            out.append(_ch(label(key), before(key, _schedule_text), new))
            weekends = changes.get("consider_working_weekends", True) is not False
            old = cur.get("consider_working_weekends")
            if grid and (old != weekends if old is not None else "consider_working_weekends" in changes):
                out.append(_ch("Рабочие выходные", _weekends_text(old) if old is not None else _NO_DATA, _weekends_text(weekends)))
        elif key == "consider_working_weekends":
            if "schedule" not in changes:
                out.append(
                    _ch(label(key), before(key, _weekends_text), "без изменений — меняется только вместе с расписанием")
                )
        elif key == "counter_ids":

            def new_counters() -> str:
                counters = actions._counter_list(value)
                if not counters:
                    raise YandexError("counter", "Нужен хотя бы один счётчик Метрики")
                return ", ".join(str(c) for c in counters)

            out.append(_ch(label(key), before(key, lambda v: ", ".join(str(c) for c in v) or "нет"), _try(new_counters)))
        elif key == "priority_goals":

            def new_goals() -> str:
                goals = actions._priority_goals(value)
                return _goals_text([{"goal_id": g["GoalId"], "value_rub": g["Value"] / 1_000_000} for g in goals], names)

            out.append(_ch(label(key), before(key, lambda v: _goals_text(v, names)), _try(new_goals)))
        elif key == "attribution_model":

            def new_model() -> str:
                model = actions.attribution_for_api(value)
                if not model:
                    raise YandexError("attribution", "Неизвестная модель атрибуции")
                return _attribution_text(model)

            out.append(_ch(label(key), before(key, _attribution_text), _try(new_model)))
        elif key == "settings":
            values = value if isinstance(value, dict) else {}
            options = [o for o in values if o in actions.CAMPAIGN_SETTINGS]
            if not options:
                out.append(_ch(label(key), "—", "без изменений — нет известных опций"))
            for option in options:
                old = (cur.get("settings") or {}).get(option)
                out.append(
                    _ch(
                        actions.CAMPAIGN_SETTINGS[option][0],
                        ("вкл" if old else "выкл") if old is not None else _NO_DATA,
                        "вкл" if values[option] else "выкл",
                    )
                )
        elif key == "tracking_params":
            new = str(value or "").strip().lstrip("?")[:1024]
            out.append(_ch(label(key), before(key, lambda v: v or "нет"), new or "нет"))
        elif key == "client_info":
            new = str(value or "").strip()[:255]
            out.append(_ch(label(key), before(key, lambda v: v or "нет"), new or "нет"))
    return out


def _d_add_negatives(ctx: Context, args: dict) -> list[dict]:
    """Добавляются только новые фразы (как в add_negatives_logged); «было» — по последнему синку."""
    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    old = _campaign_negatives(ctx, cid)
    new, already = _split_new(_phrases(args), old)
    what = "Минус-фразы кампании"
    if not new:
        return [_ch(what, _count(len(old), *_PHRASE_WORDS), "без изменений — все фразы уже есть")]
    out = [
        _ch(what, _count(len(old), *_PHRASE_WORDS), _count(len(old) + len(new), *_PHRASE_WORDS)),
        _ch("Добавятся", "—", _preview(new)),
    ]
    if already:
        out.append(_ch("Уже есть", _preview(already), "без изменений"))
    return out


def _d_create_campaign(ctx: Context, args: dict) -> list[dict]:
    from app import actions, geo

    account = ctx.require_account(_int(args, "account_id"))
    spec = dict(args.get("spec") or {})
    names = _goal_names(ctx)
    ctype = str(spec.get("type") or "TEXT_CAMPAIGN").upper()
    out = [_ch("Кампания", "нет", f"«{str(spec.get('name') or '').strip()}» — черновик в кабинете {account['client_login']}")]
    strategy = dict(spec.get("strategy") or {}) if isinstance(spec.get("strategy"), dict) else {}
    if ctx.ws.get("goal_id") and not strategy.get("goal_id"):
        strategy["goal_id"] = ctx.ws["goal_id"]
    manual, weekly = None, False
    try:
        bidding, manual = actions.build_bidding_strategy(strategy, ctype)
        weekly = bool(actions.manual_weekly_limit(bidding))
        out.append(_ch("Стратегия", "—", _strategy_text(actions.strategy_from_api(bidding), names)))
    except YandexError as exc:
        out.append(_ch("Стратегия", "—", _fail(exc)))
    if spec.get("daily_budget_rub") not in (None, "", 0):
        out.append(_ch("Дневной бюджет", "—", _try(lambda: _daily_text(spec["daily_budget_rub"], manual, weekly))))
    negatives = spec.get("negative_keywords")
    if isinstance(negatives, list) and negatives:
        out.append(_ch("Минус-фразы кампании", "—", _preview([str(p) for p in negatives])))
    regions_all = geo.load_regions(ctx.user_id)
    template = actions._ws_link_template(ctx.ws)
    try:
        groups = actions.spec_groups(spec)
    except YandexError:
        groups = []
    for index, group in enumerate(groups):
        name = str(group.get("name") or f"Группа {index + 1}").strip()[:255] or f"Группа {index + 1}"
        phrases = actions._phrases(group.get("keywords"))[:200]
        regions = [int(r) for r in group.get("region_ids") or [] if str(r).lstrip("-").isdigit()] or [225]
        parts = [_count(len(phrases), *_PHRASE_WORDS), geo.region_label(regions, regions_all)]
        ad = group.get("ad") if isinstance(group.get("ad"), dict) else {}
        titles = _variants(ad, "titles") or ([ad["title"]] if ad.get("title") else [])
        texts = _variants(ad, "texts") or ([ad["text"]] if ad.get("text") else [])
        if titles and texts:
            href = str(ad.get("href") or "").strip() or ("ссылка по шаблону" if template else "без ссылки")
            parts.append(
                f"объявление: {_count(len(titles), 'заголовок', 'заголовка', 'заголовков')}, "
                f"{_count(len(texts), 'текст', 'текста', 'текстов')}, {href}"
            )
        else:
            parts.append("без объявления")
        out.append(_ch(f"Группа «{name}»", "нет", " · ".join(parts)))
        if titles and texts:
            out += [dict(r, what=f"{r['what']} «{name}»") for r in _extras_rows(ctx, ad)]
    return out


def _d_duplicate(ctx: Context, args: dict) -> list[dict]:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    where = "в том же кабинете"
    if args.get("target_account_id") not in (None, ""):
        where = f"в кабинете {ctx.require_account(_int(args, 'target_account_id'))['client_login']}"
    name = str(args.get("name") or "").strip() or f"{row['name']} (копия)"
    return [
        _ch("Кампания", "нет", f"«{name}» — черновик {where}"),
        _ch(f"Оригинал {_cname(row)}", _state_label(row["state"], row["status"]), "без изменений"),
    ]


def _d_moderate(ctx: Context, args: dict) -> list[dict]:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    return [_ch(_cname(row), _state_label(row["state"], row["status"]), "на модерации")]


def _d_restart(ctx: Context, args: dict) -> list[dict]:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    name = str(args.get("name") or "").strip() or f"{row['name']} (копия)"
    source = "на паузе, когда копия уйдёт на модерацию" if _bool(args, "pause_source", True) else "без изменений"
    return [
        _ch("Копия", "нет", f"«{name}» — на модерации"),
        _ch(f"Оригинал {_cname(row)}", _state_label(row["state"], row["status"]), source),
    ]


def _d_copy_geo(ctx: Context, args: dict) -> list[dict]:
    from app import geo

    row, region_ids, target = _geo_args(ctx, args)
    regions = geo.load_regions(ctx.user_id)
    where = f" в кабинете {target['client_login']}" if target else ""
    state = "на модерации" if _bool(args, "moderate", True) else "черновик"
    return [
        _ch("Копия", "нет", f"«{_geo_name(row, region_ids, args, regions)}» — {state}{where}"),
        _ch(
            "Регионы копии",
            geo.region_label(geo.campaign_regions(ctx.user_id, row["id"]), regions),
            geo.region_label(region_ids, regions),
        ),
        _ch(f"Оригинал {_cname(row)}", _state_label(row["state"], row["status"]), "без изменений"),
    ]


def _d_start_experiment(ctx: Context, args: dict) -> list[dict]:
    d, rows, source = _experiment_args(ctx, args)
    out: list[dict] = []
    if _bool(args, "set_limit", True):
        names = _goal_names(ctx)
        for row in rows:
            settings = _current_settings(ctx, int(row["id"]))
            try:
                plan = experiments.limit_plan({**settings, "name": row["name"]}, d["weekly_budget_rub"])
            except YandexError as exc:
                out.append(_ch(f"Лимит {_cname(row)}", _strategy_text(settings.get("strategy"), names), _fail(exc)))
                continue
            after = plan["after"] + (f"; {plan['note']}" if plan["note"] else "")
            out.append(_ch(f"Лимит {_cname(row)}", plan["before"], after))
    else:
        out.append(
            _ch("Лимит в Директе", "как есть", f"не меняем — {_money(d['weekly_budget_rub'])}/нед. на кампанию ставит маркетолог")
        )
    ends = today_msk() + timedelta(days=d["days"] - 1)
    out += [
        _ch("Срок теста", "—", f"{_count(d['days'], 'день', 'дня', 'дней')} с первого дня показов, примерно до {ends:%d.%m}"),
        _ch("Критерий успеха", "—", experiments.criterion_text(d)),
    ]
    if source:
        out.append(_ch("Сравнение", "—", f"с {_cname(source)}"))
    out.append(_ch("Остановка", "—", "только по вашему решению после итогов"))
    return out


def _variant_texts(spec: dict, group: bool = False) -> str:
    if spec.get("per_title"):
        n = _count(len(spec["titles"]), "объявление", "объявления", "объявлений")
        where = f"{n} — по одному на заголовок" + ("" if group else " в каждой группе")
        where += ", скопированные объявления остаются" if spec.get("keep_old") else ", скопированные объявления убираются"
    elif group:
        where = "тексты объявлений группы" if spec["mode"] == "replace" else "новое объявление"
    else:
        where = "во всех объявлениях копии" if spec["mode"] == "replace" else "новое объявление в каждой группе"
    parts = []
    if spec["titles"]:
        parts.append("заголовки: " + " | ".join(spec["titles"]))
    if spec["texts"]:
        parts.append("тексты: " + " | ".join(spec["texts"]))
    return f"{where} — " + "; ".join(parts)


def _variant_phrases(spec: dict) -> str:
    phrases = spec["phrases"]
    head = ", ".join(phrases[:5]) + (f" и ещё {len(phrases) - 5}" if len(phrases) > 5 else "")
    if spec["mode"] == "replace":
        return f"только {_count(len(phrases), 'фраза', 'фразы', 'фраз')}: {head}"
    return f"+{_count(len(phrases), 'фраза', 'фразы', 'фраз')}: {head}"


def _variant_groups(v: dict, source: list[dict]) -> tuple[str, str]:
    """Строка «до / после» для изменений по группам варианта."""
    from app import variants

    before = ", ".join(f"«{g['name']}»" for g in sorted(source, key=lambda g: int(g["id"])))
    after = []
    for g in v["groups"]:
        if g.get("remove"):
            after.append(f"без «{g['source_name']}»")
            continue
        content = []
        if "keywords" in g:
            content.append("фразы " + _variant_phrases(g["keywords"]))
        if "ads" in g:
            content.append(_variant_texts(g["ads"], group=True))
        if g.get("new"):
            head = f"новая группа «{g['name']}»"
        else:
            name = g.get("rename") or (variants.group_name(g["source_name"], v["label"]) if v.get("label") else "")
            head = f"«{g['source_name']}»" + (f" → «{name}»" if name and name != g["source_name"] else "")
        after.append(f"{head}: " + "; ".join(content) if content else head)
    return before, " / ".join(after)


def _d_create_variants(ctx: Context, args: dict) -> list[dict]:
    from app import geo, variants
    from app.actions import SCHEDULE_PRESETS
    from app.adgroups import list_adgroups

    row, items, exp = _variants_args(ctx, args)
    names = _goal_names(ctx)
    with_regions = any("region_ids" in v for v in items)
    regions = geo.load_regions(ctx.user_id) if with_regions else {}
    source_regions = geo.campaign_regions(ctx.user_id, row["id"]) if with_regions else []
    cur = (
        _current_settings(ctx, row["id"])
        if any(k in v for v in items for k in ("schedule", "priority_goals", "weekly_budget_rub"))
        else {}
    )
    state = "на модерацию" if _bool(args, "moderate", False) else "черновиками"
    source_groups = None
    out = [_ch("Новые кампании", "нет", f"{_count(len(items), 'копия', 'копии', 'копий')} {_cname(row)} — {state}")]
    for v in items:
        before, after = [], []
        if "region_ids" in v:
            before.append("регионы: " + geo.region_label(source_regions, regions))
            after.append("регионы: " + geo.region_label(v["region_ids"], regions))
        if "schedule" in v:
            before.append("показы: " + _schedule_text(cur.get("schedule")))
            after.append("показы: " + SCHEDULE_PRESETS[v["schedule"]])
        if "priority_goals" in v:
            before.append("ценность целей: " + _goals_text(cur.get("priority_goals") or [], names))
            merged = variants.merge_goals(cur.get("priority_goals") or [], v["priority_goals"])
            after.append("ценность целей: " + _goals_text(merged, names))
        if "weekly_budget_rub" in v:
            try:
                plan = experiments.limit_plan({**cur, "name": row["name"]}, v["weekly_budget_rub"], force=True)
                before.append("бюджет: " + plan["before"])
                after.append("бюджет: " + plan["after"])
            except YandexError as exc:
                after.append("бюджет: " + _fail(exc))
        title = f"«{v['name']}»"
        if v.get("control"):
            out.append(_ch(title, "—", "контрольная копия: всё как у оригинала"))
        if after:
            out.append(_ch(f"{title}: настройки", "; ".join(before), "; ".join(after)))
        content = []
        if "ads" in v:
            content.append(_variant_texts(v["ads"]))
        if "keywords" in v:
            content.append("фразы " + _variant_phrases(v["keywords"]))
        if content:
            if v.get("label"):
                content.append(f"изменённые группы — «… — {v['label']}»")
            out.append(_ch(f"{title}: объявления и фразы", "как у оригинала", "; ".join(content)))
        if "groups" in v:
            if source_groups is None:
                source_groups = list_adgroups(ctx.user_id, row["id"])
            out.append(_ch(f"{title}: группы", *_variant_groups(v, source_groups)))
    out.append(_ch(f"Оригинал {_cname(row)}", _state_label(row["state"], row["status"]), "без изменений"))
    if exp:
        ends = today_msk() + timedelta(days=exp["days"] - 1)
        out += [
            _ch("Лимит вариантов", "—", f"не больше {_money(exp['weekly_budget_rub'])}/нед. на каждый вариант"),
            _ch(
                "Эксперимент",
                "—",
                f"«{exp['title']}»: {_count(exp['days'], 'день', 'дня', 'дней')} с первого дня показов, примерно до "
                f"{ends:%d.%m}; успех — {experiments.criterion_text(exp)}; сравнение с оригиналом",
            ),
        ]
    else:
        out.append(_ch("Эксперимент", "—", "не запускается — варианты придётся сравнивать по отчётам"))
    return out


def _d_finish_experiment(ctx: Context, args: dict) -> list[dict]:
    exp, verdict, conclusion, cancel = _finish_args(ctx, args)
    status = experiments.STATUS_LABELS["cancelled" if cancel else "finished"]
    rows = [_ch(f"Эксперимент «{exp['title']}»", experiments.STATUS_LABELS["running"], status)]
    if verdict:
        rows.append(_ch("Гипотеза", "проверяется", experiments.VERDICTS[verdict]))
    if conclusion:
        rows.append(_ch("Вывод", "—", conclusion))
    rows.append(_ch("Кампании эксперимента", "как есть", "без изменений"))
    return rows


def _d_create_adgroup(ctx: Context, args: dict) -> list[dict]:
    from app import geo
    from app.adgroups import campaign_regions, list_adgroups

    row = ctx.campaign(_int(args, "campaign_row_id"))
    regions = [int(r) for r in args.get("region_ids") or [] if str(r).lstrip("-").isdigit()]
    inherited = not regions and bool(list_adgroups(ctx.user_id, int(row["id"])))
    label = geo.region_label(regions or campaign_regions(ctx.user_id, int(row["id"])), geo.load_regions(ctx.user_id))
    return [
        _ch("Группа", "нет", f"«{str(args.get('name') or '').strip()}» в кампании {_cname(row)}"),
        _ch("Регионы группы", "—", f"{label} (как у первой группы кампании)" if inherited else label),
    ]


_KEYWORD_AFTER = {"suspend": "на паузе", "resume": "работает", "delete": "удалена"}


def _d_keywords_action(ctx: Context, args: dict) -> list[dict]:
    action = str(args["action"])
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, keyword, state, status, bid_micros FROM direct_keywords
            WHERE user_id = %s AND id = ANY(%s) ORDER BY id
            """,
            (ctx.user_id, _ids(args, "keyword_row_ids")),
        ).fetchall()
    if action == "set_bid":
        bid = _float(args, "bid_rub")
        return [
            _ch(
                f"«{r['keyword']}»: ставка",
                _price(r["bid_micros"] / 1_000_000) if r.get("bid_micros") else "не задана",
                _price(bid),
            )
            for r in rows
        ]
    return [_ch(f"«{r['keyword']}»", _state_label(r["state"], r["status"]), _KEYWORD_AFTER[action]) for r in rows]


def _d_add_keywords(ctx: Context, args: dict) -> list[dict]:
    group = ctx.adgroup(_int(args, "adgroup_row_id"))
    phrases = _phrases(args)
    existing = _group_keywords(ctx, int(group["id"]))
    new, already = _split_new(phrases, existing)
    bid = _float(args, "bid_rub")
    if len(phrases) > 100:
        after = "не выйдет: не больше 100 фраз за раз"
    else:
        after = _count(len(existing) + len(new), *_PHRASE_WORDS)
    out = [_ch(f"Группа «{group['name']}»", _count(len(existing), *_PHRASE_WORDS), after)]
    if new:
        out.append(_ch("Добавятся", "—", _preview(new) + (f" (ставка {_price(bid)})" if bid else "")))
    if already:
        out.append(_ch("Уже в группе", _preview(already), "без изменений"))
    return out


def _d_queries_action(ctx: Context, args: dict) -> list[dict]:
    action = str(args["action"])
    rows = ctx.queries(_ids(args, "query_row_ids"))
    if action == "add_keyword" and args.get("adgroup_row_id") not in (None, ""):
        after = f"ключевая фраза в группе «{ctx.adgroup(_int(args, 'adgroup_row_id'))['name']}»"
    elif action == "add_keyword":
        after = "ключевая фраза в группе, откуда пришёл запрос"
    elif action == "negative_adgroup":
        after = "минус-фраза группы"
    else:
        after = "минус-фраза кампании"
    return [_ch(f"«{r['query']}»", "поисковый запрос", after) for r in rows]


def _extras_rows(ctx: Context, args: dict, current: dict | None = None) -> list[dict]:
    """Строки карточки про быстрые ссылки, уточнения и картинки; current — объявление из базы при правке."""

    def before(value: str) -> str:
        return "—" if current is None else value

    def images(n: int) -> str:
        return _count(n, "картинка", "картинки", "картинок") if n else "нет"

    empty = "нет" if current is None else "убрать"
    out = []
    source = args.get("extras_from_ad_row_id")
    if source not in (None, ""):
        src = ctx.ads([int(source)])[0]
        out.append(_ch("Расширения", before("текущие"), f"как у объявления «{src['title'] or src['id']}»"))
    extras = args.get("extras") if isinstance(args.get("extras"), dict) else {}
    if extras.get("sitelinks") is not None:
        titles = [str(link.get("title") or "").strip() for link in extras["sitelinks"] if isinstance(link, dict)]
        had = "есть" if (current or {}).get("sitelink_set_id") else "нет"
        out.append(_ch("Быстрые ссылки", before(had), " | ".join(t for t in titles if t) or empty))
    if extras.get("callouts") is not None:
        callouts = [str(c).strip() for c in extras["callouts"] if str(c or "").strip()]
        out.append(_ch("Уточнения", before("текущие"), " | ".join(callouts) or empty))
    if extras.get("image_hashes") is not None:
        n = len([h for h in extras["image_hashes"] if str(h or "").strip()])
        had = len(_json_list((current or {}).get("image_hashes")))
        out.append(_ch("Картинки", before(images(had)), images(n) if n else empty))
    return out


def _d_create_ad(ctx: Context, args: dict) -> list[dict]:
    group, ad_type, titles, texts = _new_ad(ctx, args)
    kind = "комбинаторное" if ad_type == "RESPONSIVE_AD" else "текстовое"
    out = [_ch("Объявление", "нет", f"{kind}, группа «{group['name']}»")]
    out.append(_ch("Заголовки" if len(titles) > 1 else "Заголовок", "—", " | ".join(titles)))
    if str(args.get("title2") or "").strip():
        out.append(_ch("Заголовок 2", "—", str(args["title2"]).strip()))
    out.append(_ch("Тексты" if len(texts) > 1 else "Текст", "—", " | ".join(texts)))
    out.append(_ch("Ссылка", "—", str(args.get("href") or "").strip()))
    twins = [r for r in _twin_ads(ctx, group["campaign_row_id"], titles, texts) if r["adgroup_row_id"] != group["id"]]
    if twins:
        groups = sorted({r["adgroup_name"] or str(r["adgroup_row_id"]) for r in twins})
        out.append(_ch("Такое же объявление уже есть", "—", _preview([f"«{g}»" for g in groups], 4)))
    return out + _extras_rows(ctx, args)


_AD_LABELS = {
    "title": "Заголовок",
    "title2": "Заголовок 2",
    "text": "Текст",
    "href": "Ссылка",
    "titles": "Заголовки",
    "texts": "Тексты",
}


def _d_update_ad(ctx: Context, args: dict) -> list[dict]:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT title, title2, text, href, titles, texts, sitelink_set_id, image_hashes
            FROM direct_ads WHERE user_id = %s AND id = %s
            """,
            (ctx.user_id, _int(args, "ad_row_id")),
        ).fetchone() or {}
    out = []
    for key in _AD_FIELDS:
        if args.get(key) is None:
            continue
        if key in ("titles", "texts"):
            old = " | ".join(str(v) for v in _json_list(row.get(key)))
            new = " | ".join(_variants(args, key) or [])
        else:
            old = str(row.get(key) or "")
            new = str(args[key]).strip()
        out.append(_ch(_AD_LABELS[key], old or "—", new or "—"))
    return out + _extras_rows(ctx, args, row)


def _d_update_ads(ctx: Context, args: dict) -> list[dict]:
    """Одна карточка на все объявления: строки с названием объявления, сначала — одинаковые тексты."""
    pairs = _ad_items(ctx, args)
    same: dict[str, list[str]] = {}
    for item, row in pairs:
        titles, texts = _variants(item, "titles"), _variants(item, "texts")
        if titles and texts:
            same.setdefault(json.dumps([sorted(titles), sorted(texts)], ensure_ascii=False), []).append(_ad_name(row))
    out = [_ch("Станут одинаковыми", "—", _preview(names, 4)) for names in same.values() if len(names) > 1]
    for item, row in pairs:
        out += [{**ch, "what": f"{_ad_name(row)}: {ch['what']}"} for ch in _d_update_ad(ctx, item)]
    return out


_AD_AFTER = {"suspend": "на паузе", "resume": "работает", "archive": "в архиве", "unarchive": "не показывается", "delete": "удалено"}


def _d_ads_action(ctx: Context, args: dict) -> list[dict]:
    action = str(args["action"])
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, title, state, status FROM direct_ads WHERE user_id = %s AND id = ANY(%s) ORDER BY id",
            (ctx.user_id, _ids(args, "ad_row_ids")),
        ).fetchall()
    return [_ch(f"«{r['title'] or r['id']}»", _state_label(r["state"], r["status"]), _AD_AFTER[action]) for r in rows]


def _d_fix_links(ctx: Context, args: dict) -> list[dict]:
    rows, bad = _links_args(ctx, args)
    out = []
    for r in rows:
        c = bad[r["id"]]
        if c["campaign_problems"]:
            out.append(_ch(f"{_cname(r)}: метки", c["tracking"] or "нет", c["fixed_tracking"]))
        for a in c["ads"]:
            what = f"{_cname(r)}: «{a['title'] or a['ad_id']}»"
            out.append(_ch(what, a["href"] or "нет", a["fixed_href"] if a["fixable"] else "правьте в Директе"))
    return out


_DIFFS = {
    "campaigns_action": _d_campaigns_action,
    "update_campaign_settings": _d_update_settings,
    "add_campaign_negatives": _d_add_negatives,
    "create_campaign": _d_create_campaign,
    "duplicate_campaign": _d_duplicate,
    "moderate_campaign": _d_moderate,
    "restart_campaign_copy": _d_restart,
    "copy_campaign_geo": _d_copy_geo,
    "start_experiment": _d_start_experiment,
    "create_campaign_variants": _d_create_variants,
    "finish_experiment": _d_finish_experiment,
    "create_adgroup": _d_create_adgroup,
    "keywords_action": _d_keywords_action,
    "add_keywords": _d_add_keywords,
    "search_queries_action": _d_queries_action,
    "create_text_ad": _d_create_ad,
    "update_text_ad": _d_update_ad,
    "update_text_ads": _d_update_ads,
    "ads_action": _d_ads_action,
    "fix_links": _d_fix_links,
}


def _changes(ctx: Context, tool: str, args: dict) -> list[dict]:
    """[{what, before, after}] для карточки действия; не вышло — пусто, карточка останется с summary."""
    build = _DIFFS.get(tool)
    if build is None:
        return []
    try:
        rows = build(ctx, args)
    except Exception:
        log.warning("«Было → станет» для %s не собрано", tool, exc_info=True)
        return []
    out = [
        {"what": _line(r["what"], 120), "before": _line(r["before"], 300) or "—", "after": _line(r["after"], 300) or "—"}
        for r in rows
    ]
    if len(out) > MAX_CHANGES:
        rest = len(out) - (MAX_CHANGES - 1)
        out = [*out[: MAX_CHANGES - 1], {"what": f"и ещё {rest}", "before": "", "after": ""}]
    return out


# --- Строки карточки с галочками ----------------------------------------------------------------
#
# Списки в карточке действия (фразы, заголовки, объявления, кампании) маркетолог видит строками с
# галочками: сняв галочку, он убирает строку из действия перед «Одобрить», не переписывая его словами.
# id строки — путь к значению в аргументах («phrases.3», «spec.groups.0.keywords.2»).

MAX_PICKS = 300


def _pick(path: str, text, section: str = "", kind: str = "other", note: str = "") -> dict:
    return {"id": path, "section": section, "kind": kind, "text": _line(text, 300), "note": note}


def _list_picks(args: dict, key: str, section: str = "", kind: str = "other", prefix: str = "") -> list[dict]:
    values = args.get(key)
    if not isinstance(values, list):
        return []
    return [_pick(f"{prefix}{key}.{i}", v, section, kind) for i, v in enumerate(values) if str(v or "").strip()]


def _row_picks(args: dict, key: str, rows: list[dict], field: str) -> list[dict]:
    names = {int(r["id"]): r.get(field) for r in rows}
    out = []
    for i, value in enumerate(args.get(key) or []):
        try:
            text = names.get(int(value)) or str(value)
        except (TypeError, ValueError):
            continue
        out.append(_pick(f"{key}.{i}", text))
    return out


def _k_phrases(kind: str):
    def build(ctx: Context, args: dict) -> list[dict]:
        return _list_picks(args, "phrases", kind=kind)

    return build


def _k_ad(ctx: Context, args: dict) -> list[dict]:
    return _list_picks(args, "titles", "Заголовки", "title") + _list_picks(args, "texts", "Тексты", "text")


def _k_update_ads(ctx: Context, args: dict) -> list[dict]:
    out = []
    for i, (item, row) in enumerate(_ad_items(ctx, args)):
        what = ", ".join(_AD_LABELS[k].lower() for k in _AD_FIELDS if item.get(k) is not None)
        out.append(_pick(f"items.{i}", row["title"] or row["id"], note=what or ("расширения" if _has_extras(item) else "")))
    return out


def _k_ids(key: str, lookup: str, field: str):
    def build(ctx: Context, args: dict) -> list[dict]:
        return _row_picks(args, key, getattr(ctx, lookup)(_ids(args, key)), field)

    return build


def _k_create_campaign(ctx: Context, args: dict) -> list[dict]:
    spec = args.get("spec") if isinstance(args.get("spec"), dict) else {}
    groups = spec.get("groups") if isinstance(spec.get("groups"), list) else []
    out: list[dict] = []
    for g, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        name = str(group.get("name") or f"Группа {g + 1}").strip()
        base = f"spec.groups.{g}"
        if len(groups) > 1:
            out.append(_pick(base, f"Вся группа «{name}»", name, "group"))
        out += _list_picks(group, "keywords", name, "keyword", f"{base}.")
        ad = group.get("ad")
        if isinstance(ad, dict):
            out += _list_picks(ad, "titles", name, "title", f"{base}.ad.")
            out += _list_picks(ad, "texts", name, "text", f"{base}.ad.")
    out += _list_picks(spec, "negative_keywords", "Минус-фразы кампании", "negative", "spec.")
    out += _list_picks(spec, "excluded_sites", "Запрещённые площадки", "other", "spec.")
    return out


_PICKS = {
    "add_campaign_negatives": _k_phrases("negative"),
    "add_keywords": _k_phrases("keyword"),
    "create_text_ad": _k_ad,
    "update_text_ad": _k_ad,
    "update_text_ads": _k_update_ads,
    "keywords_action": _k_ids("keyword_row_ids", "keywords", "keyword"),
    "ads_action": _k_ids("ad_row_ids", "ads", "title"),
    "search_queries_action": _k_ids("query_row_ids", "queries", "query"),
    "campaigns_action": _k_ids("campaign_row_ids", "campaigns", "name"),
    "fix_links": _k_ids("campaign_row_ids", "campaigns", "name"),
    "create_campaign": _k_create_campaign,
}


def _picks(ctx: Context, tool: str, args: dict) -> list[dict]:
    """Строки с галочками для карточки; одна строка — снимать нечего, пусто."""
    build = _PICKS.get(tool)
    if build is None:
        return []
    try:
        out = build(ctx, args)
    except Exception:
        log.warning("Строки карточки для %s не собраны", tool, exc_info=True)
        return []
    return out[:MAX_PICKS] if len([p for p in out if p["kind"] != "group"]) > 1 else []


def _top_paths(paths: list[str]) -> list[str]:
    """Пути без вложенных в другие снятые: строки снятой группы уходят вместе с ней."""
    kept: list[str] = []
    for path in sorted(set(paths), key=len):
        if not any(path.startswith(f"{p}.") for p in kept):
            kept.append(path)
    return kept


def _path_parts(path: str) -> list:
    return [int(p) if p.isdigit() else p for p in path.split(".")]


def _drop_paths(args: dict, paths: list[str]) -> dict:
    """Копия аргументов без значений по путям; внутри снятой группы её строки уже не важны."""
    out = copy.deepcopy(args)
    by_container: dict[tuple, list[int]] = {}
    for path in _top_paths(paths):
        parts = _path_parts(path)
        if not parts or not isinstance(parts[-1], int):
            continue
        by_container.setdefault(tuple(parts[:-1]), []).append(parts[-1])
    for container, indexes in by_container.items():
        node = out
        try:
            for part in container:
                node = node[part]
        except (KeyError, IndexError, TypeError):
            continue
        if not isinstance(node, list):
            continue
        for index in sorted(set(indexes), reverse=True):
            if 0 <= index < len(node):
                del node[index]
    return out


def _exclude_rows(user_id: int, row: dict, exclude) -> dict:
    """Снимает отмеченные строки из ждущего действия: заново проверяет его и пересобирает карточку.

    Проверка не прошла (например, сняли все заголовки) — ошибка, действие остаётся как было.
    """
    paths = [str(p) for p in exclude if str(p or "").strip()] if isinstance(exclude, list) else []
    args = dict(row.get("args") or {})
    picks = args.get("_items") or []
    allowed = {p["id"]: p for p in picks}
    paths = [p for p in paths if p in allowed]
    if not paths:
        return row
    clean = {k: v for k, v in args.items() if k not in ("_diff", "_items", "_removed")}
    new_args = _drop_paths(clean, paths)
    with pool.connection() as conn:
        ws_id = conn.execute("SELECT workspace_id FROM ai_chats WHERE id = %s", (row["chat_id"],)).fetchone()["workspace_id"]
    ctx = Context(user_id, ws_id)
    ctx.chat_id = row["chat_id"]
    prepare, _execute = WRITE_TOOLS[row["tool"]]
    try:
        plan = prepare(ctx, new_args)
    except YandexError as exc:
        raise YandexError("args", f"Без снятых строк действие не пройдёт: {exc.message or exc.code}") from exc
    top = set(_top_paths(paths))
    removed = [{"section": p["section"], "kind": p["kind"], "text": p["text"]} for p in picks if p["id"] in top]
    stored = dict(new_args)
    changes = _changes(ctx, row["tool"], new_args)
    if changes:
        stored["_diff"] = changes
    stored["_removed"] = removed
    with pool.connection() as conn:
        updated = conn.execute(
            "UPDATE ai_actions SET args = %s, summary = %s WHERE id = %s AND status = 'pending' RETURNING *",
            (Jsonb(stored), plan.summary[:1000], row["id"]),
        ).fetchone()
    if updated is None:
        raise YandexError("decided", "Решение уже принято")
    return updated


# --- Вопросы, план и таблицы: ждут ответа маркетолога в карточке ---------------------------


def _option_text(item) -> str:
    if isinstance(item, dict):
        item = item.get("label") or item.get("text") or item.get("value") or ""
    return _line(item, 200)


def _q_questions(ctx: Context, args: dict) -> dict:
    """ask_questions → карточка вопросов. Вопрос с key, на который маркетолог уже ответил, не задаём."""
    raw = args.get("questions")
    if isinstance(raw, (str, dict)):
        raw = [raw]
    known = {f["key"]: f for f in normalize_ai_facts(ctx.ws.get("ai_facts"))}
    questions: list[dict] = []
    answered: list[dict] = []
    keys: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"question": item}
        if not isinstance(item, dict):
            continue
        text = _line(item.get("question") or item.get("text"), 300)
        if not text:
            continue
        key = fact_key(item.get("key"))
        if key in keys:
            key = ""  # два вопроса с одним key — запомним только первый
        if key in known:
            answered.append({"key": key, "question": known[key]["question"] or text, "answer": known[key]["answer"]})
            continue
        if key:
            keys.add(key)
        raw_options = item.get("options")
        if isinstance(raw_options, str):
            raw_options = raw_options.splitlines()
        options: list[str] = []
        for option in raw_options if isinstance(raw_options, list) else []:
            label = _option_text(option)
            if label and label not in options:
                options.append(label)
        options = options[:MAX_OPTIONS]
        questions.append(
            {
                "n": len(questions) + 1,
                "key": key,
                "question": text,
                "options": options,
                "multiple": len(options) > 1 and _bool(item, "multiple", False),
                "remember": bool(key) and _bool(item, "remember", True),
            }
        )
    if len(questions) > MAX_QUESTIONS:
        raise YandexError("args", f"Не больше {MAX_QUESTIONS} вопросов за раз — оставь главные")
    if not questions and not answered:
        raise YandexError("args", "Нет ни одного вопроса с текстом")
    return {"understanding": _para(args.get("understanding"), 1500), "questions": questions, "answered": answered}


def _q_plan(ctx: Context, args: dict) -> dict:
    """propose_plan → карточка плана: шаги «было → станет → зачем», маркетолог отмечает нужные."""
    raw = args.get("steps")
    if isinstance(raw, (str, dict)):
        raw = [raw]
    steps: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        title = _line(item.get("title") or item.get("step"), 200)
        if not title:
            continue
        steps.append(
            {
                "n": len(steps) + 1,
                "title": title,
                "before": _line(item.get("before"), 300),
                "after": _line(item.get("after"), 300),
                "why": _line(item.get("why"), 300),
            }
        )
    if not steps:
        raise YandexError("args", "В плане нет ни одного шага с title")
    if len(steps) > MAX_PLAN_STEPS:
        raise YandexError("args", f"Не больше {MAX_PLAN_STEPS} шагов в плане — объедини мелкие")
    return {"goal": _para(args.get("goal"), 500), "steps": steps}


MAX_ITEM_ROWS = 200
MAX_ITEM_COLUMNS = 5
_ITEM_COLUMNS = {
    "goals": ["Что", "Значение", "Комментарий"],
    "analysis": ["Направление", "Показов в месяц", "Откуда", "Комментарий"],
    "keywords": ["Фраза", "Показов в месяц", "Комментарий"],
    "negatives": ["Минус-фраза", "Почему"],
    "ads": ["Вариант", "Комментарий"],
    "extras": ["Что", "Значение"],
    "settings": ["Настройка", "Значение", "Почему"],
    "other": ["Что", "Комментарий"],
}
_ITEM_LIMITS = {"title": 56, "text": 81}


def _q_items(ctx: Context, args: dict) -> dict:
    """propose_items → таблица на согласование: строки с галочками, маркетолог снимает лишние."""
    stage = str(args.get("stage") or "other").strip().lower()
    if stage not in ITEM_STAGES:
        raise YandexError("args", f"stage — одно из {list(ITEM_STAGES)}")
    raw_columns = args.get("columns")
    columns = [_line(c, 60) for c in raw_columns if _line(c, 60)] if isinstance(raw_columns, list) else []
    raw = args.get("rows")
    if isinstance(raw, (str, dict)):
        raw = [raw]
    rows: list[dict] = []
    seen: set[tuple] = set()
    too_long: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"cells": [item]}
        if not isinstance(item, dict):
            continue
        cells = item.get("cells")
        if isinstance(cells, str):
            cells = [cells]
        if not isinstance(cells, list):
            cells = [item.get("text")]
        cells = [_line(c, 300) for c in cells[:MAX_ITEM_COLUMNS]]
        if not cells or not cells[0]:
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in ITEM_KINDS:
            if stage == "ads":
                raise YandexError("args", f"«{cells[0]}»: в таблице ads у строки kind — title или text")
            kind = _STAGE_KINDS.get(stage, "other")
        section = _line(item.get("section") or item.get("group"), 120)
        key = (section, kind, _item_key(cells[0], kind))
        if key in seen:
            continue
        seen.add(key)
        limit = _ITEM_LIMITS.get(kind)
        if limit and len(cells[0]) > limit:
            too_long.append(f"«{cells[0]}» ({len(cells[0])} > {limit})")
        rows.append(
            {"n": len(rows) + 1, "section": section, "kind": kind, "cells": cells, "checked": _bool(item, "checked", True)}
        )
    if too_long:
        raise YandexError("args", "Длиннее предела Директа, сократи: " + _preview(too_long, 5))
    if not rows:
        raise YandexError("args", "В таблице нет ни одной строки с текстом")
    if len(rows) > MAX_ITEM_ROWS:
        raise YandexError("args", f"Не больше {MAX_ITEM_ROWS} строк в таблице — раздели этап на части")
    width = max(len(r["cells"]) for r in rows)
    columns = (columns or _ITEM_COLUMNS[stage])[:width]
    columns += [""] * (width - len(columns))
    for row in rows:
        row["cells"] += [""] * (width - len(row["cells"]))
    return {
        "stage": stage,
        "title": _line(args.get("title"), 120) or ITEM_STAGES[stage],
        "intro": _para(args.get("intro"), 1500),
        "columns": columns,
        "rows": rows,
    }


ASK_TOOLS = {"ask_questions": _q_questions, "propose_plan": _q_plan, "propose_items": _q_items}


def _kind(tool: str) -> str:
    if tool in ASK_TOOLS:
        return "ask"
    return "write" if tool in WRITE_TOOLS else "read"


TOOL_LABELS = {
    "get_workspace_summary": "Сводка пространства",
    "get_analytics": "Аналитика за период",
    "list_campaigns": "Список кампаний",
    "get_campaign": "Структура кампании",
    "get_campaign_settings": "Настройки кампании",
    "get_search_queries": "Поисковые запросы",
    "refresh_search_queries": "Загрузка поисковых запросов",
    "get_structure_tops": "Топы ключей и групп",
    "list_recent_actions": "Журнал действий",
    "get_campaign_options": "Справочник для кампаний",
    "get_signals": "Сигналы пространства",
    "get_campaign_history": "Кампания по дням",
    "remember_fact": "Запоминаю ответ",
    "find_regions": "Поиск регионов",
    "get_wordstat": "Вордстат",
    "get_experiments": "Эксперименты",
    "get_headline_stats": "Статистика заголовков",
    "get_slices": "Срезы статистики",
    "check_links": "Проверка ссылок",
    "get_ad_extras": "Расширения объявления",
    "list_ad_images": "Картинки кабинета",
    "ask_questions": "Вопросы перед работой",
    "propose_plan": "План работ",
    "propose_items": "Таблица на согласование",
    "campaigns_action": "Действие с кампаниями",
    "update_campaign_settings": "Настройки кампании",
    "add_campaign_negatives": "Минус-фразы кампании",
    "create_campaign": "Новая кампания",
    "duplicate_campaign": "Копия кампании",
    "moderate_campaign": "Модерация",
    "restart_campaign_copy": "Перезапуск копией",
    "copy_campaign_geo": "Копия на новое гео",
    "start_experiment": "Запуск эксперимента",
    "create_campaign_variants": "Варианты кампании",
    "finish_experiment": "Итоги эксперимента",
    "create_adgroup": "Новая группа",
    "keywords_action": "Ключевые фразы",
    "add_keywords": "Новые ключевые фразы",
    "search_queries_action": "Поисковые запросы",
    "create_text_ad": "Новое объявление",
    "update_text_ad": "Правка объявления",
    "update_text_ads": "Правка объявлений",
    "ads_action": "Объявления",
    "fix_links": "Ссылки по шаблону",
}


# --- Описания для модели ------------------------------------------------------------------------


def _fn(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
        },
    }


_ID = {"type": "integer"}
_IDS = {"type": "array", "items": {"type": "integer"}}
_RANGE = {
    "date_from": {"type": "string", "description": "YYYY-MM-DD; по умолчанию 7 полных дней до сегодня"},
    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
}
_REASON = {
    "reason": {"type": "string", "description": "Коротко для пользователя: зачем это действие"},
    "plan_step": {"type": "integer", "description": "номер шага согласованного плана, если действие из него"},
}
_PHRASES = {"type": "array", "items": {"type": "string"}}
_EXTRAS = {
    "extras": {
        "type": "object",
        "description": "Расширения: переданный ключ заменяет текущие, пустой список — убрать",
        "properties": {
            "sitelinks": {
                "type": "array",
                "description": "быстрые ссылки, до 8: title до 30 символов, href, description до 60",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "href": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["title", "href"],
                },
            },
            "callouts": {"type": "array", "items": {"type": "string"}, "description": "уточнения, до 25 символов"},
            "image_hashes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "картинки из list_ad_images: у комбинаторного до 5, у текстового одна",
            },
        },
    },
    "extras_from_ad_row_id": {
        "type": "integer",
        "description": "взять быстрые ссылки, уточнения и картинки у этого объявления того же кабинета; ключи extras важнее",
    },
}
# Поля правки объявления: update_text_ad и записи update_text_ads.
_AD_EDIT = {
    "title": {"type": "string"},
    "title2": {"type": "string"},
    "text": {"type": "string"},
    "titles": {"type": "array", "items": {"type": "string"}},
    "texts": {"type": "array", "items": {"type": "string"}},
    "href": {"type": "string"},
    **_EXTRAS,
}

TOOLS = [
    _fn("get_workspace_summary", "Сводка: расход сегодня, проблемы («где горит»), неделя к неделе, топ кампаний."),
    _fn(
        "get_analytics",
        "Итоги пространства за период и сравнение с прошлым таким же периодом, KPI, разрез по кампаниям и кабинетам.",
        {**_RANGE, "include_days": {"type": "boolean", "description": "добавить разбивку по дням"}},
    ),
    _fn("list_campaigns", "Кампании пространства (campaign_row_id, статус, бюджет, стратегия) с метриками за период.", _RANGE),
    _fn(
        "get_campaign",
        "Структура кампании: группы (статистика, сколько в них объявлений и фраз), объявления без архивных "
        "(одинаковое — ссылкой same_as на первое такое же; sitelinks и images — есть ли расширения), фразы по расходу. "
        "Большая кампания отдаётся частями: есть not_all_shown — запроси дальше с *_offset или только нужные группы.",
        {
            "campaign_row_id": _ID,
            "adgroup_row_ids": {**_IDS, "description": "только эти группы"},
            "adgroups_offset": {"type": "integer"},
            "ads_offset": {"type": "integer"},
            "keywords_offset": {"type": "integer"},
        },
        ["campaign_row_id"],
    ),
    _fn(
        "get_campaign_settings",
        "Актуальные настройки кампании из Директа: стратегия, бюджет, минус-фразы, расписание, цели.",
        {"campaign_row_id": _ID},
        ["campaign_row_id"],
    ),
    _fn(
        "get_search_queries",
        "Загруженные поисковые запросы кампании (id для search_queries_action), с фильтрами.",
        {
            "campaign_row_id": _ID,
            "min_clicks": {"type": "integer"},
            "min_cost_rub": {"type": "number"},
            "no_conversions": {"type": "boolean", "description": "только без конверсий"},
            "with_conversions": {
                "type": "boolean",
                "description": "только с конверсиями, лучшие первыми — источник текстов и фраз для вариантов",
            },
            "search": {"type": "string"},
            "limit": {"type": "integer", "description": "до 200"},
        },
        ["campaign_row_id"],
    ),
    _fn(
        "refresh_search_queries",
        "Загрузить свежий отчёт поисковых запросов из Директа (долго, до минуты).",
        {"campaign_row_id": _ID, "days": {"type": "integer", "description": "1–90, по умолчанию 14"}},
        ["campaign_row_id"],
    ),
    _fn("get_structure_tops", "Топы ключевых фраз, групп и запросов пространства по расходу и конверсиям."),
    _fn("list_recent_actions", "Журнал последних изменений в пространстве.", {"limit": {"type": "integer"}}),
    _fn(
        "get_campaign_options",
        "Справочник для создания кампаний: типы, стратегии и их параметры, кабинеты пространства, цели Метрики.",
    ),
    _fn(
        "get_signals",
        "Выжимка за 30 дней: конверсии за 0 ₽ и их механика, затухание показов (день спада и что менялось рядом), "
        "падения логинов, серии перезапусков копиями, успешные кампании, пробелы в данных. Начинай разбор с неё.",
    ),
    _fn(
        "get_campaign_history",
        "Кампания по дням за 30 дней: показы, клики, расход, конверсии по целям, затухание и события рядом "
        "(изменения настроек, действия). Для проверки гипотезы о причине спада.",
        {"campaign_row_id": _ID},
        ["campaign_row_id"],
    ),
    _fn(
        "remember_fact",
        "Запомнить ответ маркетолога о проекте — только то, что он сам сказал или подтвердил. Тот же key "
        "заменяет прежний ответ. Механика конверсий за 0 ₽ — key zero_cost_scheme.",
        {
            "key": {"type": "string", "description": "латиницей, напр. zero_cost_scheme, success_criteria"},
            "question": {"type": "string", "description": "о чём вопрос, коротко"},
            "answer": {"type": "string", "description": "ответ маркетолога своими словами, до 1000 символов"},
        },
        ["key", "answer"],
    ),
    _fn(
        "find_regions",
        "Найти id регионов Директа по названию: город, область, страна. Для копии на новое гео и для групп; "
        "минус перед id исключает регион.",
        {"query": {"type": "string", "description": "название, напр. «Казань» или «Татарстан»"}},
        ["query"],
    ),
    _fn(
        "get_wordstat",
        "Вордстат за 30 дней: по каждой исходной фразе — запросы с ней и похожие запросы с числом показов в месяц. "
        "Для подбора и расширения ключевых фраз. Платный запрос на каждую фразу, квота 100 в час — бери 1–5 "
        "главных фраз, не перебирай всё подряд.",
        {
            "phrases": {**_PHRASES, "description": "исходные фразы, до 5"},
            "region_ids": {**_IDS, "description": "регионы (find_regions), пусто — все"},
            "device": {"type": "string", "enum": ["all", "desktop", "phone", "tablet"]},
            "limit": {"type": "integer", "description": "фраз в ответе на каждую исходную, до 100, по умолчанию 20"},
        },
        ["phrases"],
    ),
    _fn(
        "get_experiments",
        "Эксперименты пространства: гипотезы, тестовые кампании, лимиты, критерии успеха и прогресс, посчитанный "
        "кодом по загруженной статистике, и выводы прошлых экспериментов. С experiment_id — этот подробно.",
        {"experiment_id": _ID},
    ),
    _fn(
        "get_headline_stats",
        "Какие заголовки приносят конверсии ключевой цели: точные цифры заголовков (объявления с одним "
        "заголовком и загруженные отчёты Мастера, лучшие — сверху по конверсиям, enough — хватает ли данных) и "
        "комбинаторные объявления (mixed) с общими цифрами на все их заголовки. Основа для масштабирования "
        "выгодных заголовков.",
        {
            "campaign_row_ids": {**_IDS, "description": "кампании; по умолчанию все кампании пространства"},
            "days": {"type": "integer", "description": "период, дней (1–90, по умолчанию 30)"},
        },
    ),
    _fn(
        "get_slices",
        "Срез статистики кампаний: placement — площадки РСЯ, device — устройства, demography — пол и возраст, "
        "region — регионы, hour — часы (по визитам Метрики). Конверсии ключевой цели, CR, CPA, текущие и "
        "предложенные корректировки.",
        {
            "dimension": {"type": "string", "enum": ["placement", "device", "demography", "region", "hour"]},
            "campaign_row_ids": {**_IDS, "description": "кампании; по умолчанию все"},
            "days": {"type": "integer", "description": "период, дней (1–90, по умолчанию 30)"},
        },
        ["dimension"],
    ),
    _fn(
        "check_links",
        "Проверка ссылок кампаний по шаблону сайта из настроек: домен, ref и каждая utm-метка (метки — в "
        "параметрах кампании). Косяки с исправленными значениями; исправить — fix_links.",
        {"campaign_row_ids": {**_IDS, "description": "кампании; по умолчанию все"}},
    ),
    _fn(
        "get_ad_extras",
        "Быстрые ссылки, уточнения и картинки объявления — из Директа.",
        {"ad_row_id": _ID},
        ["ad_row_id"],
    ),
    _fn(
        "list_ad_images",
        "Картинки для объявлений: какие уже стоят в объявлениях кампании и сколько раз, и что ещё есть в "
        "библиотеке кабинета. Хэш — для extras.image_hashes.",
        {"campaign_row_id": _ID},
        ["campaign_row_id"],
    ),
    _fn(
        "ask_questions",
        "Вопросы маркетологу карточкой с вариантами ответа. Сначала в understanding перескажи, как понял задачу. "
        "До 5 вопросов — только о том, чего нет в данных и в прежних ответах; к каждому дай варианты, свой ответ "
        "он допишет сам. Ответы на вопросы с key запоминаются — повторно их не задавай. Ответ придёт результатом "
        "этого вызова.",
        {
            "understanding": {"type": "string", "description": "как ты понял задачу и что уже известно, 2–5 предложений"},
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "латиницей, напр. success_criteria; пусто — не запоминать"},
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}, "description": "2–6 вариантов ответа"},
                        "multiple": {"type": "boolean", "description": "можно выбрать несколько вариантов"},
                        "remember": {"type": "boolean", "description": "запомнить ответ (по умолчанию да, если есть key)"},
                    },
                    "required": ["question"],
                },
            },
        },
        ["questions"],
    ),
    _fn(
        "propose_plan",
        "План работ на согласование: шаги «было → станет → зачем». Маркетолог отметит, какие шаги делать. "
        "Согласованные шаги выполняй инструментами действий с plan_step — каждое изменение он подтвердит "
        "отдельно. Ответ придёт результатом этого вызова.",
        {
            "goal": {"type": "string", "description": "чего добиваемся планом, одним-двумя предложениями"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "что сделать, коротко"},
                        "before": {"type": "string", "description": "как сейчас"},
                        "after": {"type": "string", "description": "как станет"},
                        "why": {"type": "string", "description": "зачем, на каких данных основано"},
                    },
                    "required": ["title"],
                },
            },
        },
        ["steps"],
    ),
    _fn(
        "propose_items",
        "Таблица на согласование: строки с галочками, маркетолог снимет лишние и согласует остальное. Для всего, "
        "что выбирают списком: направления и группы, фразы, минус-фразы, заголовки, тексты, расширения, настройки. "
        "Ответ придёт результатом вызова: approved — согласованные строки, removed — снятые; используй только "
        "согласованные. До ответа следующий этап не начинай.",
        {
            "stage": {
                "type": "string",
                "enum": list(ITEM_STAGES),
                "description": "этап: goals — цели кампании (что рекламируем, оффер, гео, бюджет, цель, посадочная), "
                "analysis — направления/группы, keywords — фразы по группам, negatives — минус-фразы, ads — "
                "заголовки и тексты, extras — ссылка и расширения, settings — настройки",
            },
            "title": {"type": "string", "description": "заголовок таблицы"},
            "intro": {"type": "string", "description": "1–3 предложения: на чём основано и что согласовать"},
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"названия колонок (до {MAX_ITEM_COLUMNS}); первая — сама строка (фраза, заголовок…)",
            },
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string", "description": "раздел строки, обычно группа"},
                        "kind": {"type": "string", "enum": list(ITEM_KINDS), "description": "в stage=ads обязательно: title или text"},
                        "cells": {"type": "array", "items": {"type": "string"}, "description": "ячейки по колонкам"},
                        "checked": {"type": "boolean", "description": "отмечена сразу (по умолчанию да)"},
                    },
                    "required": ["cells"],
                },
            },
        },
        ["stage", "rows"],
    ),
    _fn(
        "campaigns_action",
        "Остановить, запустить, сменить дневной бюджет, архивировать или удалить кампании.",
        {
            "campaign_row_ids": _IDS,
            "action": {"type": "string", "enum": sorted(BULK_ACTIONS)},
            "daily_budget_rub": {"type": "number", "description": "для set_budget: новая сумма"},
            "budget_change_pct": {"type": "number", "description": "для set_budget: изменение текущего на N %"},
            **_REASON,
        },
        ["campaign_row_ids", "action"],
    ),
    _fn(
        "update_campaign_settings",
        "Изменить настройки кампании. В changes только меняемые поля: name, start_date, end_date, daily_budget_rub "
        "(0 — снять), strategy {search, network, weekly_budget_rub, bid_ceiling_rub, average_cpc_rub, "
        "average_cpa_rub, goal_id}, negative_keywords (ЗАМЕНЯЕТ список; для добавления — add_campaign_negatives), "
        "excluded_sites, schedule (always|workdays|workhours|daytime), counter_ids, priority_goals "
        "[{goal_id, value_rub}], attribution_model, settings {опция: bool}, tracking_params, client_info.",
        {"campaign_row_id": _ID, "changes": {"type": "object"}, **_REASON},
        ["campaign_row_id", "changes"],
    ),
    _fn(
        "add_campaign_negatives",
        "Добавить минус-фразы кампании к уже существующим.",
        {"campaign_row_id": _ID, "phrases": _PHRASES, **_REASON},
        ["campaign_row_id", "phrases"],
    ),
    _fn(
        "create_campaign",
        "Создать кампанию (черновик) в кабинете пространства. Сначала посмотри get_campaign_options. spec: type "
        "(TEXT_CAMPAIGN|UNIFIED_CAMPAIGN), name, start_date, end_date, strategy {search, network, параметры}, "
        "daily_budget_rub (только ручные ставки), negative_keywords [], excluded_sites [], schedule, "
        "attribution_model, priority_goals, tracking_params, groups [{name, region_ids, keywords [] (до 200), "
        "bid_rub, ad {titles (до 7), texts (до 3), href, extras или extras_from_ad_row_id}}] (до 20 групп): "
        "объявление комбинаторное, второго заголовка в нём нет; href можно не давать, если в пространстве есть "
        "шаблон ссылки. Кампания с группами создаётся только после согласованных таблиц propose_items по этапам "
        "(цели, анализ, ключи и группы, заголовки и тексты, настройки) и только из согласованных строк. "
        "Счётчик пространства подставится сам.",
        {"account_id": _ID, "spec": {"type": "object"}, **_REASON},
        ["account_id", "spec"],
    ),
    _fn(
        "duplicate_campaign",
        "Копия кампании со структурой, в тот же или другой кабинет пространства.",
        {"campaign_row_id": _ID, "target_account_id": _ID, "name": {"type": "string"}, **_REASON},
        ["campaign_row_id"],
    ),
    _fn("moderate_campaign", "Отправить кампанию на модерацию.", {"campaign_row_id": _ID, **_REASON}, ["campaign_row_id"]),
    _fn(
        "restart_campaign_copy",
        "Перезапуск затухшей кампании копией в том же кабинете: копия со структурой → модерация → оригинал на "
        "паузу (только если копия собрана целиком и ушла на модерацию). Копия — новая кампания без накопленной "
        "статистики.",
        {
            "campaign_row_id": _ID,
            "name": {"type": "string", "description": "название копии; по умолчанию «<название> (копия)»"},
            "pause_source": {
                "type": "boolean",
                "description": "остановить оригинал сразу (по умолчанию да); false — оригинал работает, пока копия "
                "на модерации",
            },
            **_REASON,
        },
        ["campaign_row_id"],
    ),
    _fn(
        "copy_campaign_geo",
        "Копия кампании на другой регион: копия со структурой → регионы во всех группах копии → модерация. "
        "Оригинал продолжает работать. id регионов — из find_regions.",
        {
            "campaign_row_id": _ID,
            "region_ids": {**_IDS, "description": "регионы копии; минус перед id — исключить"},
            "name": {"type": "string", "description": "название копии; по умолчанию «<название> — <регионы>»"},
            "target_account_id": {**_ID, "description": "другой кабинет пространства; по умолчанию тот же"},
            "moderate": {"type": "boolean", "description": "сразу на модерацию (по умолчанию да)"},
            **_REASON,
        },
        ["campaign_row_id", "region_ids"],
    ),
    _fn(
        "start_experiment",
        "Запустить эксперимент: записать гипотезу, тестовые кампании, срок и критерий успеха и поставить кампаниям "
        "лимит ₽ в неделю в Директе (недельный бюджет стратегии; у ручных ставок ТГО — дневной не больше лимита/7). "
        "Сервис ничего не останавливает сам — расход держит только этот бюджет. Тестовые кампании создай заранее "
        "(copy_campaign_geo, restart_campaign_copy, create_campaign; для вариантов одной кампании — create_campaign_variants с experiment). Срок считается с первого дня показов.",
        {
            "title": {"type": "string", "description": "коротко, напр. «Казань: копия Поиска Москва»"},
            "hypothesis": {"type": "string", "description": "что проверяем и на каких данных это основано"},
            "kind": {"type": "string", "enum": list(experiments.KINDS)},
            "campaign_row_ids": {**_IDS, "description": "тестовые кампании, до 10"},
            "source_campaign_row_id": {**_ID, "description": "кампания-образец для сравнения, если есть"},
            "weekly_budget_rub": {
                "type": "number",
                "description": "лимит ₽ в неделю на каждую тестовую кампанию — из ответа маркетолога, от 300",
            },
            "set_limit": {
                "type": "boolean",
                "description": "поставить лимит в Директе (по умолчанию да); false — маркетолог ставит сам",
            },
            "days": {"type": "integer", "description": "срок теста, 3–90 дней, по умолчанию 14"},
            "criterion": {
                "type": "string",
                "enum": list(experiments.CRITERIA),
                "description": "zero_cost — конверсии за ~0 ₽; conversions — от target конверсий за срок; cr — CR "
                "от target %; custom — свой критерий словами в criterion_text",
            },
            "target": {"type": "number", "description": "zero_cost и conversions — конверсий за весь срок; cr — CR в %"},
            "criterion_text": {"type": "string", "description": "критерий словами: для custom обязательно, иначе уточнение"},
            **_REASON,
        },
        ["title", "hypothesis", "campaign_row_ids", "weekly_budget_rub", "criterion"],
    ),
    _fn(
        "create_campaign_variants",
        "Масштабирование и тест: 1–5 копий кампании-источника с её структурой, целями и оплатой, но своими "
        "параметрами — регионы, расписание, ценность ключевых целей, недельный бюджет, тексты объявлений, ключевые "
        "фразы. В каждом варианте задай только то, что меняется; остальное — как у оригинала. Оригинал не трогает. "
        "В кампании с несколькими группами фразы и тексты задавай по группам (groups): правки групп оригинала, "
        "remove — не копировать слабую группу, name — новая группа для теста. "
        "Тест заголовков — ads.per_title: по объявлению на каждый заголовок (тексты, ссылка и расширения общие), "
        "тогда у каждого заголовка своя статистика. Копия называется «{label} · дд.мм», изменённые группы — "
        "«{группа} — {label}». Копии создаются черновиками; на модерацию — отдельно moderate_campaign после согласия. "
        "Вариант с ошибкой на любом шаге остаётся черновиком. С experiment — сразу эксперимент по вариантам с "
        "лимитом ₽/нед. и сравнением с оригиналом. Вызывай после того, как маркетолог одобрил варианты в propose_plan.",
        {
            "campaign_row_id": {**_ID, "description": "кампания-источник (успешная или исправляемая)"},
            "variants": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "название копии, у вариантов разные; по умолчанию «{label} · дд.мм»",
                        },
                        "label": {
                            "type": "string",
                            "description": "короткое имя гипотезы до 40 знаков («Скорость подключения») — для "
                            "названий копии и изменённых групп; по умолчанию — начало idea",
                        },
                        "idea": {"type": "string", "description": "гипотеза варианта одной фразой"},
                        "control": {
                            "type": "boolean",
                            "description": "контрольная копия без изменений (можно только weekly_budget_rub): "
                            "стартует вместе с вариантами, сравнивать с ней честнее, чем с оригиналом",
                        },
                        "plan_step": {"type": "integer", "description": "номер одобренного шага propose_plan"},
                        "region_ids": {**_IDS, "description": "регионы показа (find_regions); минус — исключить"},
                        "schedule": {"type": "string", "enum": ["always", "workdays", "workhours", "daytime"]},
                        "priority_goals": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"goal_id": {"type": "integer"}, "value_rub": {"type": "number"}},
                                "required": ["goal_id"],
                            },
                            "description": "новая ценность целей, ₽ (от 0,3); новые id добавятся, прочие цели оригинала останутся",
                        },
                        "weekly_budget_rub": {
                            "type": "number",
                            "description": "свой недельный бюджет варианта, от 300 (не больше лимита эксперимента)",
                        },
                        "ads": {
                            "type": "object",
                            "properties": {
                                "mode": {
                                    "type": "string",
                                    "enum": ["replace", "add"],
                                    "description": "replace — новые тексты во всех объявлениях копии (быстрые ссылки "
                                    "и картинки остаются); add — новое объявление в каждой группе рядом со старыми",
                                },
                                "per_title": {
                                    "type": "boolean",
                                    "description": "тест заголовков: по объявлению на каждый заголовок из titles "
                                    "(до 10) с общими texts, ссылкой и расширениями; mode — add",
                                },
                                "keep_old": {
                                    "type": "boolean",
                                    "description": "с per_title: оставить скопированные объявления (по умолчанию "
                                    "убираются — их смесь заголовков размывает тест)",
                                },
                                "titles": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "до 7 (с per_title — до 10), ≤56 знаков",
                                },
                                "texts": {"type": "array", "items": {"type": "string"}, "description": "до 3, ≤81 знака"},
                                "title2": {
                                    "type": "string",
                                    "description": "≤30 знаков, только mode=replace и только для старых текстовых "
                                    "объявлений: в комбинаторных и новых объявлениях второго заголовка нет",
                                },
                                "href": {"type": "string", "description": "другая ссылка; по умолчанию — как у оригинала"},
                            },
                        },
                        "keywords": {
                            "type": "object",
                            "properties": {
                                "mode": {
                                    "type": "string",
                                    "enum": ["add", "replace"],
                                    "description": "add — добавить к фразам оригинала (в первую группу); replace — "
                                    "оставить только эти (только при одной группе, иначе — groups)",
                                },
                                "phrases": {"type": "array", "items": {"type": "string"}, "description": "до 200"},
                                "bid_rub": {"type": "number"},
                            },
                            "required": ["phrases"],
                        },
                        "groups": {
                            "type": "array",
                            "maxItems": 20,
                            "description": "изменения по группам вместо ads и keywords на всю кампанию; группы "
                            "без записи копируются как есть",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "group_id": {**_ID, "description": "группа оригинала (id из get_campaign)"},
                                    "remove": {"type": "boolean", "description": "не копировать эту группу"},
                                    "rename": {
                                        "type": "string",
                                        "description": "своё название группы в копии; по умолчанию изменённая "
                                        "группа — «{группа} — {label}»",
                                    },
                                    "name": {"type": "string", "description": "новая группа (без group_id): нужны keywords и ads"},
                                    "keywords": {
                                        "type": "object",
                                        "properties": {
                                            "mode": {
                                                "type": "string",
                                                "enum": ["add", "replace"],
                                                "description": "add — к фразам группы; replace — у группы только эти",
                                            },
                                            "phrases": {"type": "array", "items": {"type": "string"}},
                                            "bid_rub": {"type": "number"},
                                        },
                                        "required": ["phrases"],
                                    },
                                    "ads": {
                                        "type": "object",
                                        "properties": {
                                            "mode": {
                                                "type": "string",
                                                "enum": ["replace", "add"],
                                                "description": "replace — новые тексты в объявлениях группы; add — "
                                                "новое объявление в группе (у новой группы — всегда)",
                                            },
                                            "per_title": {
                                                "type": "boolean",
                                                "description": "по объявлению на каждый заголовок (тест заголовков)",
                                            },
                                            "keep_old": {"type": "boolean"},
                                            "titles": {"type": "array", "items": {"type": "string"}},
                                            "texts": {"type": "array", "items": {"type": "string"}},
                                            "title2": {"type": "string"},
                                            "href": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "moderate": {
                "type": "boolean",
                "description": "сразу на модерацию (по умолчанию нет — черновики; модерацию согласуй отдельным "
                "шагом и отправь moderate_campaign)",
            },
            "experiment": {
                "type": "object",
                "description": "эксперимент по вариантам; не передавай, если маркетолог не просил",
                "properties": {
                    "title": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "weekly_budget_rub": {"type": "number", "description": "лимит ₽/нед. на каждый вариант, от 300"},
                    "days": {"type": "integer", "description": "3–90, по умолчанию 14"},
                    "criterion": {"type": "string", "enum": list(experiments.CRITERIA)},
                    "target": {"type": "number"},
                    "criterion_text": {"type": "string"},
                },
                "required": ["title", "hypothesis", "weekly_budget_rub", "criterion"],
            },
            **_REASON,
        },
        ["campaign_row_id", "variants"],
    ),
    _fn(
        "finish_experiment",
        "Записать итог эксперимента, когда маркетолог согласен с выводом: подтвердилась ли гипотеза и что дальше. "
        "Кампании не трогает — остановить или масштабировать их отдельными действиями. cancel — отменить без итога.",
        {
            "experiment_id": _ID,
            "verdict": {"type": "string", "enum": list(experiments.VERDICTS)},
            "conclusion": {"type": "string", "description": "что узнали и что делать дальше, 1–4 предложения"},
            "cancel": {"type": "boolean", "description": "отменить эксперимент; verdict тогда не нужен"},
            **_REASON,
        },
        ["experiment_id"],
    ),
    _fn(
        "create_adgroup",
        "Создать группу объявлений в кампании. Без region_ids регионы — как у первой группы кампании.",
        {"campaign_row_id": _ID, "name": {"type": "string"}, "region_ids": _IDS, **_REASON},
        ["campaign_row_id", "name"],
    ),
    _fn(
        "keywords_action",
        "Остановить, запустить, удалить ключевые фразы или задать ставку.",
        {
            "keyword_row_ids": _IDS,
            "action": {"type": "string", "enum": list(_KEYWORD_ACTIONS)},
            "bid_rub": {"type": "number"},
            **_REASON,
        },
        ["keyword_row_ids", "action"],
    ),
    _fn(
        "add_keywords",
        "Добавить ключевые фразы в группу.",
        {"adgroup_row_id": _ID, "phrases": _PHRASES, "bid_rub": {"type": "number"}, **_REASON},
        ["adgroup_row_id", "phrases"],
    ),
    _fn(
        "search_queries_action",
        "Поисковые запросы → ключевые фразы или минус-фразы группы/кампании.",
        {
            "query_row_ids": _IDS,
            "action": {"type": "string", "enum": list(_QUERY_ACTIONS)},
            "adgroup_row_id": _ID,
            "bid_rub": {"type": "number"},
            **_REASON,
        },
        ["query_row_ids", "action"],
    ),
    _fn(
        "create_text_ad",
        "Объявление в группе. В текстовых группах и ЕПК оно комбинаторное: titles — до 7 заголовков до 56 "
        "символов, texts — до 3 текстов до 81, второго заголовка нет. В остальных группах — один title, "
        "title2 до 30 и один text. Длиннее предела — ошибка, сокращай сам. Расширения — extras или "
        "extras_from_ad_row_id; без них объявление выйдет без быстрых ссылок, уточнений и картинок.",
        {
            "adgroup_row_id": _ID,
            "titles": {"type": "array", "items": {"type": "string"}},
            "texts": {"type": "array", "items": {"type": "string"}},
            "title": {"type": "string", "description": "один заголовок вместо titles"},
            "text": {"type": "string", "description": "один текст вместо texts"},
            "title2": {"type": "string", "description": "только для групп, где объявление не комбинаторное"},
            "href": {"type": "string"},
            **_EXTRAS,
            **_REASON,
        },
        ["adgroup_row_id", "href"],
    ),
    _fn(
        "update_text_ad",
        "Изменить объявление (только переданные поля). У комбинаторного titles/texts заменяют все "
        "варианты целиком, title/text меняют первый вариант, второго заголовка нет. Длиннее предела — "
        "ошибка. Несколько объявлений — одним update_text_ads.",
        {"ad_row_id": _ID, **_AD_EDIT, **_REASON},
        ["ad_row_id"],
    ),
    _fn(
        "update_text_ads",
        f"Изменить несколько объявлений одной карточкой (до {MAX_BULK_ADS}): в items по записи на объявление — "
        "ad_row_id и только меняемые поля, как в update_text_ad. Одинаковые тексты разным объявлениям не давай.",
        {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"ad_row_id": _ID, **_AD_EDIT}, "required": ["ad_row_id"]},
            },
            **_REASON,
        },
        ["items"],
    ),
    _fn(
        "ads_action",
        "Остановить, запустить, архивировать или удалить объявления.",
        {"ad_row_ids": _IDS, "action": {"type": "string", "enum": list(_AD_ACTIONS)}, **_REASON},
        ["ad_row_ids", "action"],
    ),
    _fn(
        "fix_links",
        "Привести ссылки кампаний к шаблону сайта (после check_links): метки — в параметры кампании, без "
        "модерации; ссылки объявлений с косяком — адрес и ref по шаблону, такие объявления уйдут на модерацию. "
        "Откат — в журнале.",
        {"campaign_row_ids": _IDS, **_REASON},
        ["campaign_row_ids"],
    ),
]


def _running_experiments(ctx: Context) -> list[dict]:
    """Идущие эксперименты для системного промпта, с названиями кампаний; без них ход не должен падать."""
    try:
        rows = experiments.running_experiments(ctx.user_id, ctx.ws_id)
    except Exception:
        log.warning("Идущие эксперименты пространства %s не загружены", ctx.ws_id, exc_info=True)
        return []
    ids = sorted({int(i) for e in rows for i in e["campaign_row_ids"] or []})
    names: dict[int, str] = {}
    if ids:
        try:
            with pool.connection() as conn:
                found = conn.execute(
                    "SELECT id, name FROM direct_campaigns WHERE user_id = %s AND id = ANY(%s)", (ctx.user_id, ids)
                ).fetchall()
            names = {int(r["id"]): r["name"] for r in found}
        except Exception:
            log.warning("Названия кампаний экспериментов пространства %s не загружены", ctx.ws_id, exc_info=True)
    return [{**e, "campaign_names": names} for e in rows]


def _experiment_campaigns(e: dict) -> str:
    """Кампании эксперимента для промпта: название и id для инструментов."""
    names = e.get("campaign_names") or {}
    return ", ".join(
        (f"«{names[int(i)]}» (campaign_row_id {i})" if int(i) in names else f"campaign_row_id {i}")
        for i in e["campaign_row_ids"] or []
    )


FIRST_CAMPAIGN_RULE = (
    "- В пространстве ещё нет ни одной кампании: это первая. Статистики, истории, сигналов и поисковых запросов "
    "нет — их не запрашивай и не делай вывода из их отсутствия. Если маркетолог просит «разбор» или оптимизацию, "
    "скажи, что разбирать пока нечего, и предложи собрать первую кампанию. Анализ (этап 2) строй на брифе, сайте "
    "и Вордстате; примеров своих кампаний нет — так и скажи. Бриф бери у маркетолога через ask_questions (продукт "
    "и оффер, гео, бюджет, цель, ссылка), ответы с key запомнятся. Без цели Метрики и накопленных конверсий "
    "Директу нечему учиться: стратегию на конверсии не предлагай, начни с «Максимум кликов» с недельным бюджетом "
    "или с ручных ставок, а переход на конверсии назови следующим шагом, когда конверсии накопятся. Конверсий "
    "и результата не обещай: цифры — только из Вордстата и данных пространства."
)


def _system_prompt(ctx: Context) -> str:
    ws = ctx.ws
    access = ctx.access
    if access["full_access"]:
        policy = "Полный доступ: все действия выполняются сразу, без согласования."
    else:
        auto = [CATEGORY_LABELS[c] for c, on in access["categories"].items() if on]
        policy = (
            "Без согласования разрешено: " + ("; ".join(auto) if auto else "ничего")
            + f". Бюджеты и ставки — не более чем на {access['max_change_pct']}% за раз. "
            "Остальное уходит пользователю на согласование: система сама покажет карточку с кнопками."
        )
    kpi = "не задан"
    if ws.get("kpi_type") == "cpa" and ws.get("kpi_target"):
        kpi = f"CPA не выше {_money(ws['kpi_target'])}"
    elif ws.get("kpi_type") == "drr" and ws.get("kpi_target"):
        kpi = f"ДРР не выше {ws['kpi_target']:g}%"
    accounts = "; ".join(
        f"id {a['id']}: {a['client_login']}" + (" (песочница)" if a.get("sandbox") else "")
        for a in ws.get("accounts") or []
    ) or "нет"
    lines = [
        "Ты — ИИ-ассистент интернет-маркетолога в сервисе AiMarketing (Яндекс Директ + Метрика).",
        f"Сегодня {today_msk().isoformat()} (Москва).",
        f"Рабочее пространство «{ws['name']}», сайт {ws.get('site') or '—'}, счётчик Метрики {ws['counter_id']}"
        + (f", клиент «{ws['client_name']}»" if ws.get("client_name") else "") + ".",
        f"Основная цель: {ws.get('goal_name') or ws.get('goal_id') or 'не выбрана (конверсии Директа)'}. KPI: {kpi}."
        + (f" Бюджет в месяц: {_money(ws['monthly_budget_rub'])}." if ws.get("monthly_budget_rub") else ""),
        f"Кабинеты Директа пространства: {accounts}.",
        f"Доступ: {policy}",
        "",
        "Правила:",
        "- Отвечай по-русски, кратко и по делу. Цифры бери только из инструментов, ничего не выдумывай.",
        "- Для анализа сначала собери данные инструментами, затем дай выводы и конкретные рекомендации. Данные "
        "со многими строками (кампании, группы, фразы, запросы, заголовки с цифрами) показывай markdown-таблицей, "
        "а не списком.",
        "- Всё, что маркетолог выбирает или согласует списком (направления, группы, фразы, минус-фразы, заголовки, "
        "тексты, расширения), показывай таблицей propose_items, а не перечнем в тексте: он снимет галочки с "
        "лишнего. ask_questions — для вопросов, propose_plan — для плана шагов. Списки в карточках действий тоже "
        "идут строками с галочками: снятое не выполняется, результат действия скажет, что снято.",
        "- Если ответ инструмента обрезан или в нём есть not_all_shown, ты видел не всё: дозапроси остальное или "
        "прямо скажи, что смотрел часть, — не делай вывода «всё в порядке» о том, чего не видел.",
        "- Суммы в инструментах — в рублях. Кампании, группы и объявления называй по имени или по тексту (у "
        "одинаковых названий добавляй номер кампании в Директе — campaign_id); внутренние id (campaign_row_id, "
        "adgroup_row_id, ad_row_id и т. п.) в ответах не пиши — они только для вызова инструментов.",
        "- Разбор начинай с get_signals, если выжимки ещё нет в чате: там уже посчитаны конверсии за 0 ₽, "
        "затухание, перезапуски копиями и успешные кампании. Кампанию по дням — get_campaign_history. "
        "Сырые таблицы запрашивай, только когда без них не обойтись.",
        "- Успех кампании — конверсии почти без расхода, много конверсий или высокий CR, либо критерий "
        "маркетолога из его ответов. CPA ниже KPI — не главный признак. Что работает — предлагай повторить.",
        "- Отделяй факты из данных от гипотез и говори, чем гипотезу проверить.",
        "- Чего не хватает для вывода — спроси через ask_questions: сначала перескажи, как понял задачу, затем до "
        "5 вопросов с вариантами ответа. Не спрашивай то, что видно в данных или уже есть в ответах маркетолога. "
        "Механику схемы и причины спадов не придумывай: выведи из данных и сверь вывод с маркетологом. Ответы с "
        "key запоминаются сами; remember_fact — для того, что маркетолог сказал или подтвердил обычным "
        "сообщением, а не для твоих догадок.",
        "- Предлагай действия сам, когда их подсказывают данные: вызови инструмент действия — пользователь увидит "
        "карточку «Выполнить / Отклонить». Не спрашивай разрешения текстом. В reason коротко объясни зачем. "
        "То, что по доступу выполнится без согласования, делай только по просьбе пользователя или по "
        "согласованному плану.",
        "- Перед крупной работой (несколько кампаний, перезапуски, копии на новое гео): "
        "1) ask_questions — если чего-то не хватает; 2) propose_plan — шаги «было → станет → зачем»; 3) после "
        "согласования выполняй только согласованные шаги, по одному инструменту на шаг, с plan_step. Новый шаг "
        "или изменённый план — новый propose_plan. Мелкую прямую просьбу выполняй без плана. Новую кампанию — "
        "по этапам ниже.",
        "- Новую кампанию собирай строго по этапам. Каждый этап заканчивается таблицей propose_items, и к "
        "следующему переходишь только после её согласования; без согласованных этапов create_campaign вернёт "
        "ошибку. Маркетолог видит рядом с чатом, как собирается кампания: согласованные цели, группы с фразами "
        "и объявления, поэтому называй группы (section) одинаково на всех этапах. 1) Цели кампании — сначала "
        "ask_questions, если чего-то не хватает в брифе, затем propose_items stage=goals строками «что — "
        "значение»: что рекламируем и оффер, гео, бюджет, цель (действие на сайте или цель Метрики), посадочная "
        "ссылка, ограничения. 2) Анализ: похожие кампании пространства (list_campaigns, get_campaign — какие группы, фразы и тексты "
        "приносили конверсии), get_search_queries с with_conversions, get_wordstat по 1–5 главным фразам с "
        "регионами кампании. Короткий отчёт с цифрами таблицей, затем propose_items stage=analysis — направления, "
        "они станут группами (направление, показов в месяц, откуда идея). 3) Ключи и группы — propose_items "
        "stage=keywords: фразы согласованных направлений с частотой из get_wordstat, section — группа; минус-фразы "
        "кампании — там же строками kind=negative с section «Минус-фразы» или отдельной таблицей stage=negatives. "
        "4) Заголовки и тексты — propose_items stage=ads: каждой группе до 7 заголовков (kind=title, до 56 "
        "знаков) и до 3 текстов (kind=text, до 81) под её фразы, section — группа. 5) Ссылка и расширения — "
        "propose_items stage=extras: ссылка или шаблон пространства, быстрые ссылки, уточнения, у какого "
        "объявления взять расширения. 6) Настройки — propose_items stage=settings: тип, стратегия, бюджет, "
        "регионы, расписание, цель; допустимые стратегии и цели — get_campaign_options, id регионов — "
        "find_regions. 7) create_campaign — одним вызовом черновик со всеми группами (spec.groups), "
        "только из согласованных строк: снятое галочкой не бери, новое не добавляй без новой таблицы. "
        "8) На модерацию — только после отдельного согласия (moderate_campaign). Если Вордстат не подключён или "
        "упёрся в квоту, скажи об этом, строй направления и фразы из брифа, сайта и запросов других кампаний, а "
        "частоты не выдумывай — в таблице так и пиши «нет данных».",
        "- Затухшую кампанию, если причина не в балансе, модерации или настройке, которую проще поправить, "
        "предлагай перезапустить копией — restart_campaign_copy.",
        "- Копию кампании на другой регион делай через copy_campaign_geo; id регионов бери из find_regions, "
        "не по памяти.",
        "- Масштабировать успешную кампанию или проверить несколько гипотез разом — create_campaign_variants: 1–5 "
        "копий с целями и оплатой оригинала, но своими параметрами. Порядок: 1) ask_questions — что менять "
        "(тексты, ключи, гео, расписание, ценность целей, бюджет), сколько вариантов, согласовывать все варианты "
        "одной карточкой или каждый отдельно, нужен ли эксперимент (лимит ₽/нед., срок, критерий); 2) данные — "
        "get_campaign (тексты всех объявлений), get_search_queries с with_conversions (конвертящие запросы — "
        "основа новых текстов и фраз), бриф, для новых ключей — get_wordstat; 3) propose_plan — шаг на каждый вариант: что меняется и какую "
        "гипотезу проверяет; варианты отличаются друг от друга одним-двумя параметрами, иначе не понять, что "
        "сработало; контрольная копия без изменений — вариант с control: true; 4) create_campaign_variants только с одобренными вариантами — одним вызовом или по вызову на "
        "вариант, как решил маркетолог; копии создаются черновиками (moderate=false), на модерацию — отдельным "
        "шагом после согласия (moderate_campaign по каждой копии). Тексты пиши в пределах Директа (заголовок до 56 знаков, текст до 81), "
        "без выдуманных цен и обещаний, которых нет в объявлениях оригинала или брифе. Ключи расширяй "
        "get_wordstat: 1–5 главных фраз (конвертящие запросы, ключи оригинала) с регионами варианта; бери "
        "запросы по теме бизнеса с заметной частотой, частоты называй из ответа, нецелевые слова оттуда — "
        "кандидаты в минус-фразы. Если Вордстат не подключён или упёрся в квоту, так и скажи и расширяй ключи "
        "из запросов и брифа. В кампании с несколькими группами смотри группы в get_campaign (статистика, фразы "
        "и объявления каждой) и задавай изменения по группам (groups): каждой группе — свои фразы и тексты под её "
        "тему; слабую группу (расход без конверсий) предлагай не копировать (remove), для теста — новую группу "
        "(name, фразы и объявление). Группы без записи копируются как есть. В propose_plan покажи по каждому "
        "варианту, что меняется в какой группе. Если create_campaign_variants не создался или варианты собрались "
        "с ошибками, не переделывай план сам: объясни ошибку простыми словами и спроси через ask_questions, как "
        "поступить.",
        "- Масштабировать выгодные заголовки — по шагам; каждый шаг согласуй отдельно и без ответа к следующему не "
        "переходи. 1) Максимальный анализ выбранной кампании: get_headline_stats, get_campaign, get_search_queries "
        "с with_conversions, get_campaign_history, get_slices (device, demography, region, для сетей — placement). "
        "Отчёт: лучшие заголовки по конверсиям ключевой цели с CR и CPA (enough=false — честно скажи, что выводы "
        "предварительные), что их объединяет (оффер, выгода, ключ, формулировка), где кампания теряет деньги. "
        "Если почти всё в mixed — точных цифр заголовков нет: предложи загрузить отчёт Мастера по заголовкам "
        "(экран «Объявления») или начать с теста. Затем propose_items stage=ads — какие заголовки масштабировать "
        "(строки kind=title — лучшие заголовки, в колонках конверсии, CR и CPA). 2) Новые заголовки: к каждому "
        "выбранному 2–4 близких — тот же смысл и выгода, другая формулировка или порядок слов, ключ из "
        "конвертящих запросов, до 56 знаков; покажи таблицей propose_items stage=ads (section — исходный "
        "заголовок, kind=title), лишние маркетолог снимет галочкой. 3) Структура теста — propose_plan: копии только выбранной "
        "кампании; label гипотезы, названия копии «{label} · дд.мм» и групп «{группа} — {label}»; ads.per_title с "
        "одобренными заголовками плюс исходный лучший как контроль; тексты — лучшие тексты оригинала; недельный "
        "бюджет. 4) create_campaign_variants с moderate=false — черновики. 5) Отдельно спроси, отправлять ли на "
        "модерацию, и только после согласия — moderate_campaign по каждой копии. Быстрые ссылки, уточнения и "
        "картинки копия и объявления теста берут у оригинала сами.",
        "- Объявления: в текстовых группах и ЕПК новое объявление комбинаторное — до 7 заголовков и до 3 текстов, "
        "второго заголовка нет; пределы знаков соблюдай сам, сервис не обрезает, а возвращает ошибку. Одинаковых "
        "объявлений (те же заголовки и тексты) не делай ни в одной группе, ни в разных: каждой группе — тексты под "
        "её фразы. Новому объявлению передавай расширения: extras_from_ad_row_id — как у объявления-образца "
        "(get_ad_extras покажет, что у него есть) или extras; картинки кабинета — list_ad_images. Без них "
        "объявление выйдет без быстрых ссылок, уточнений и картинок. Правку нескольких объявлений делай одним "
        "update_text_ads — маркетолог согласует её одной карточкой.",
        "- Ссылки: в настройках пространства задаётся шаблон ссылки сайта (адрес и ref — в объявлении, utm — в "
        "метках кампании). При разборе кампаний вызывай check_links; косяки (другой домен, нет ref, неверные или "
        "задвоенные utm) показывай как проблему с предложением исправить — fix_links после согласия. Новые "
        "кампании, копии и объявления сервис строит по шаблону сам.",
        "- Провальную кампанию сначала разбери: get_signals, get_campaign_history, get_campaign_settings, "
        "get_search_queries. Затем предложи, что лучше: исправить на месте (минус-фразы, ставки, настройки, тексты "
        "— если ошибка одна и понятная, а история кампании ценна) или исправленные копии через "
        "create_campaign_variants (каждая копия — своё исправление; когда кампания затухла или причин несколько). "
        "Объясни выбор.",
        "- Гипотезы проверяй экспериментом: 1) ask_questions — лимит ₽ в неделю на кампанию, срок в днях и критерий "
        "успеха (общий критерий клиента запоминай с key success_criteria); 2) propose_plan; 3) тестовые кампании — "
        "инструментами копий; 4) start_experiment. Деньги ограничивает только недельный бюджет в Директе: сервис "
        "сам ничего не останавливает. Кампанию идущего эксперимента в новый не бери.",
        "- Итоги эксперимента разбирай по get_experiments, если выжимки ещё нет в чате; у вариантов там рейтинг "
        "по критерию. Предложи план через propose_plan: проигравшие — остановить (campaigns_action), победителя — "
        "масштабировать (create_campaign_variants от победителя, копия на новое гео, другие ключи или тип); "
        "не подтвердилось — остановить тестовые кампании. Итог записывай finish_experiment, когда маркетолог "
        "согласен с выводом.",
        "- Если действие отклонено, не повторяй его без новой просьбы. Если ошибка — объясни её пользователю.",
        "- Если часть согласованного плана не выполнена (ошибка, отклонено, пропущено) или ты её убрал, прямо "
        "скажи, что именно не сделано, — не называй план выполненным.",
        "- Работай только с кампаниями и кабинетами этого пространства.",
    ]
    facts = normalize_ai_facts(ws.get("ai_facts"))
    first = not ws.get("campaigns_count")
    if first:
        lines.append(FIRST_CAMPAIGN_RULE)
    if not first and not any(f["key"] == SCHEME_FACT for f in facts):
        lines.append(
            "- Механика конверсий за 0 ₽ ещё не разобрана. Гипотеза для проверки, не факт: кампания платит за "
            "конверсии по редкой цели, а заявки по другим целям приходят бесплатно; новой кампании Директ даёт "
            "показы на обучение, а не видя оплачиваемых конверсий — режет их, и копия начинает заново. "
            f"Подтверждённый маркетологом вывод сохрани с key {SCHEME_FACT}."
        )
    brief = normalize_brief(ws.get("brief"))
    if brief_filled(brief):
        lines += ["", "Бриф клиента:"]
        if brief["product"]:
            lines.append(f"- Что продаём: {brief['product']}")
        if brief["geo"]:
            lines.append(f"- География: {brief['geo']}")
        if brief["not_ours"]:
            lines.append("- Точно не наше: " + ", ".join(brief["not_ours"]))
        if brief["notes"]:
            lines.append(f"- Заметки: {brief['notes']}")
    if facts:
        lines += ["", "Что маркетолог уже ответил (учитывай, не переспрашивай):"]
        lines += [f"- {f['question'] or f['key']}: {f['answer']}" for f in facts]
    running = _running_experiments(ctx)
    if running:
        lines += [
            "",
            "Идут эксперименты (не меняй настройки их кампаний без просьбы маркетолога — это ломает тест; "
            "прогресс и итоги — get_experiments):",
        ]
        lines += [
            f"- #{e['id']} «{e['title']}»: с {e['started_on']:%d.%m} на {_count(int(e['days']), 'день', 'дня', 'дней')}, "
            f"кампании {_experiment_campaigns(e)}"
            for e in running
        ]
    examples = examples_prompt(memory_examples(ctx.ws_id, 10))
    if examples:
        lines += ["", "Решения маркетолога по поисковым запросам (учитывай их в предложениях минус-фраз):", examples]
    if (ws.get("ai_notes") or "").strip():
        lines += ["", "Заметки маркетолога о проекте:", ws["ai_notes"].strip()]
    return "\n".join(lines)


# --- Чаты и сообщения ---------------------------------------------------------------------------


def _chat_out(row: dict) -> dict:
    status = row["status"]
    updated = row.get("updated_at")
    error = row.get("error") or ""
    if status == "running" and updated and datetime.now(timezone.utc) - updated > STALE_RUNNING:
        status, error = "error", "Ответ прервался — отправьте сообщение ещё раз"
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "title": row["title"] or "Новый чат",
        "status": status,
        "error": error,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(updated),
        "pending_actions": int(row.get("pending_actions") or 0),
    }


def _parse_args(text: str) -> dict:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _message_out(row: dict) -> dict:
    calls = row.get("tool_calls") or []
    content = row.get("content") or ""
    # Выжимки показываем целиком: пользователь видит ровно то, на чём ИИ строит выводы.
    limit = TOOL_RESULT_LIMIT if row.get("tool_name") in ("get_signals", "get_experiments") else 1500
    if row["role"] == "tool" and len(content) > limit:
        content = content[:limit] + "…"
    return {
        "id": row["id"],
        "role": row["role"],
        "content": content,
        "tool_calls": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "label": TOOL_LABELS.get(c.get("name") or "", c.get("name")),
                "args": _parse_args(c.get("arguments") or ""),
                "write": c.get("name") in WRITE_TOOLS,
                "kind": _kind(c.get("name") or ""),
            }
            for c in calls
        ],
        "tool_call_id": row.get("tool_call_id") or "",
        "tool_name": row.get("tool_name") or "",
        "created_at": _iso(row.get("created_at")),
    }


def _action_out(row: dict) -> dict:
    args = dict(row.get("args") or {})
    changes = args.pop("_diff", None) or []
    items = args.pop("_items", None) or []
    removed = args.pop("_removed", None) or []
    cats = [c for c in (row.get("category") or "").split(",") if c]
    try:
        plan_step = int(args["plan_step"]) if args.get("plan_step") not in (None, "") else None
    except (TypeError, ValueError):
        plan_step = None
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
        "tool_call_id": row["tool_call_id"],
        "tool": row["tool"],
        "kind": _kind(row["tool"]),
        "label": TOOL_LABELS.get(row["tool"], row["tool"]),
        "args": args,
        "changes": changes,
        "items": items,
        "removed": removed,
        "plan_step": plan_step,
        "reason": str(args.get("reason") or ""),
        "summary": row["summary"],
        "categories": [{"id": c, "label": CATEGORY_LABELS.get(c, c)} for c in cats],
        "status": row["status"],
        "auto": row["auto"],
        "result": row.get("result"),
        "error": row.get("error") or "",
        "created_at": _iso(row.get("created_at")),
        "decided_at": _iso(row.get("decided_at")),
    }


# Панель «Сборка кампании»: этапы новой кампании и всё согласованное по ним — группы с фразами и
# объявлениями. Собирается из таблиц propose_items чата; ждущая таблица показывается как предложение.

BUILD_STAGES = {
    "goals": "Цели",
    "analysis": "Анализ",
    "keywords": "Группы и фразы",
    "negatives": "Минус-фразы",
    "ads": "Объявления",
    "extras": "Ссылка и расширения",
    "settings": "Настройки",
}
_BUILD_OPTIONAL = {"negatives", "extras"}
_BUILD_STARTS = {"goals", "analysis"}
NO_GROUP = "Без группы"


def _build_row(row: dict, pending: bool) -> dict:
    cells = [str(c or "") for c in row.get("cells") or []]
    return {
        "text": cells[0] if cells else "",
        "value": cells[1] if len(cells) > 1 else "",
        "note": " · ".join(c for c in cells[2:] if c),
        "pending": pending,
    }


def campaign_build(actions: list[dict]) -> dict | None:
    """Как собирается новая кампания в чате; None — новую кампанию здесь не собирают.

    Сборка начинается таблицей целей или анализа и заканчивается созданным черновиком: после него
    новая таблица целей или анализа начинает следующую сборку.
    """
    episodes: list[list[dict]] = [[]]
    for action in actions:
        episodes[-1].append(action)
        if action["tool"] == "create_campaign" and action["status"] == "done":
            episodes.append([])
    episode = next(
        (
            ep
            for ep in reversed(episodes)
            if any(a["tool"] == "propose_items" and (a.get("args") or {}).get("stage") in _BUILD_STARTS for a in ep)
        ),
        None,
    )
    if episode is None:
        return None

    status: dict[str, str] = {}
    last_table: dict[str, int] = {}
    rows: dict[str, list[dict]] = {s: [] for s in BUILD_STAGES}
    seen: set[tuple] = set()
    for action in episode:
        args = action.get("args") or {}
        stage = args.get("stage")
        if action["tool"] != "propose_items" or stage not in BUILD_STAGES:
            continue
        last_table[stage] = action["id"]
        state = action["status"]
        if state == "pending":
            status[stage] = "pending"
        elif state == "done" and status.get(stage) != "pending":
            status[stage] = "done"
        elif stage not in status:
            status[stage] = "rejected"
        if state == "done":
            chosen = set((action.get("result") or {}).get("approved_rows") or [])
            picked = [r for r in args.get("rows") or [] if r.get("n") in chosen]
        elif state == "pending":
            picked = [r for r in args.get("rows") or [] if r.get("checked") is not False]
        else:
            continue
        for row in picked:
            cells = row.get("cells") or []
            key = (stage, row.get("section") or "", row.get("kind") or "", _item_key(cells[0] if cells else "", row.get("kind") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows[stage].append({**row, "_pending": state == "pending"})

    groups: dict[str, dict] = {}

    def group(name: str) -> dict:
        name = name or NO_GROUP
        return groups.setdefault(name, {"name": name, "keywords": [], "titles": [], "texts": [], "href": ""})

    negatives: list[dict] = []
    for stage in ("keywords", "negatives"):
        for row in rows[stage]:
            item = _build_row(row, row["_pending"])
            if row.get("kind") == "negative" or stage == "negatives":
                negatives.append(item)
            else:
                group(row.get("section") or "")["keywords"].append(item)
    for row in rows["ads"]:
        if row.get("kind") in ("title", "text"):
            group(row.get("section") or "")[f"{row['kind']}s"].append(_build_row(row, row["_pending"]))

    # Карточка создания — это то, что уйдёт в Директ (с учётом снятых в ней галочек): группы берём из неё,
    # частотность фраз — из таблиц.
    draft = None
    creates = [a for a in episode if a["tool"] == "create_campaign" and a["status"] != "rejected"]
    if creates:
        action = creates[-1]
        spec = (action.get("args") or {}).get("spec")
        spec = spec if isinstance(spec, dict) else {}
        values = {_item_key(k["text"], "keyword"): k["value"] for g in groups.values() for k in g["keywords"]}

        def items(values_list, kind: str, known: dict | None = None) -> list[dict]:
            out = []
            for v in values_list if isinstance(values_list, list) else []:
                text = str(v or "").strip()
                if text:
                    value = (known or {}).get(_item_key(text, kind), "")
                    out.append({"text": text, "value": value, "note": "", "pending": False})
            return out

        groups = {}
        for g, raw in enumerate(spec.get("groups") or [] if isinstance(spec.get("groups"), list) else []):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or f"Группа {g + 1}").strip()
            ad = raw.get("ad") if isinstance(raw.get("ad"), dict) else {}
            groups[name] = {
                "name": name,
                "keywords": items(raw.get("keywords"), "keyword", values),
                "titles": items(ad.get("titles"), "title"),
                "texts": items(ad.get("texts"), "text"),
                "href": str(ad.get("href") or "").strip(),
            }
        negatives = items(spec.get("negative_keywords"), "negative")
        result = action.get("result") if isinstance(action.get("result"), dict) else {}
        campaign = result.get("campaign") if isinstance(result.get("campaign"), dict) else {}
        draft = {
            "action_id": action["id"],
            "status": action["status"],
            "name": str(spec.get("name") or "").strip(),
            "summary": action.get("summary") or "",
            "campaign_row_id": campaign.get("id"),
        }

    # Минус-фразы часто приходят строками таблицы ключей — тогда этап закрыт вместе с ней.
    if "negatives" not in status and negatives and "keywords" in status:
        status["negatives"] = "pending" if any(n["pending"] for n in negatives) else "done"
        last_table["negatives"] = last_table["keywords"]
    counts = {key: len(rows[key]) for key in BUILD_STAGES}
    counts["keywords"] = sum(len(g["keywords"]) for g in groups.values())
    counts["negatives"] = len(negatives)
    counts["ads"] = sum(len(g["titles"]) + len(g["texts"]) for g in groups.values())

    stages = []
    later_started = False
    for key in reversed(BUILD_STAGES):
        state = status.get(key)
        if state is None:
            state = "skipped" if key in _BUILD_OPTIONAL and later_started else "todo"
        else:
            later_started = True
        stages.append(
            {"key": key, "label": BUILD_STAGES[key], "status": state, "count": counts[key], "action_id": last_table.get(key)}
        )
    stages.reverse()
    return {
        "stages": stages,
        "goals": [_build_row(r, r["_pending"]) for r in rows["goals"]],
        "directions": [_build_row(r, r["_pending"]) for r in rows["analysis"]],
        "groups": list(groups.values()),
        "negatives": negatives,
        "extras": [_build_row(r, r["_pending"]) for r in rows["extras"]],
        "settings": [_build_row(r, r["_pending"]) for r in rows["settings"]],
        "draft": draft,
    }


_CHAT_SELECT = """
    SELECT ch.*,
           (SELECT COUNT(*) FROM ai_actions a WHERE a.chat_id = ch.id AND a.status = 'pending') AS pending_actions
    FROM ai_chats ch
"""


def list_chats(user_id: int, workspace_id: int) -> list[dict]:
    require_workspace(user_id, workspace_id)
    with pool.connection() as conn:
        rows = conn.execute(
            _CHAT_SELECT + " WHERE ch.user_id = %s AND ch.workspace_id = %s ORDER BY ch.updated_at DESC, ch.id DESC",
            (user_id, workspace_id),
        ).fetchall()
    return [_chat_out(r) for r in rows]


def create_chat(user_id: int, workspace_id: int, title: str = "") -> dict:
    require_workspace(user_id, workspace_id)
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, %s, %s) RETURNING *",
            (user_id, workspace_id, (title or "").strip()[:120]),
        ).fetchone()
    return _chat_out(row)


def _require_chat(conn, user_id: int, chat_id: int, *, lock: bool = False) -> dict:
    row = conn.execute(
        "SELECT * FROM ai_chats WHERE id = %s AND user_id = %s" + (" FOR UPDATE" if lock else ""),
        (chat_id, user_id),
    ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return row


def get_chat(user_id: int, chat_id: int) -> dict:
    with pool.connection() as conn:
        _require_chat(conn, user_id, chat_id)
        chat = conn.execute(_CHAT_SELECT + " WHERE ch.id = %s", (chat_id,)).fetchone()
        messages = conn.execute("SELECT * FROM ai_messages WHERE chat_id = %s ORDER BY id", (chat_id,)).fetchall()
        actions = conn.execute("SELECT * FROM ai_actions WHERE chat_id = %s ORDER BY id", (chat_id,)).fetchall()
    ws = require_workspace(user_id, chat["workspace_id"])
    return {
        "chat": _chat_out(chat),
        "messages": [_message_out(m) for m in messages],
        "actions": [_action_out(a) for a in actions],
        "build": campaign_build(actions),
        "access": normalize_ai_access(ws.get("ai_access")),
        "ai": {"configured": ai.get_settings(user_id)["configured"]},
    }


def rename_chat(user_id: int, chat_id: int, title: str) -> dict:
    with pool.connection() as conn:
        _require_chat(conn, user_id, chat_id)
        conn.execute("UPDATE ai_chats SET title = %s WHERE id = %s", ((title or "").strip()[:120], chat_id))
        row = conn.execute(_CHAT_SELECT + " WHERE ch.id = %s", (chat_id,)).fetchone()
    return _chat_out(row)


def delete_chat(user_id: int, chat_id: int) -> None:
    with pool.connection() as conn:
        _require_chat(conn, user_id, chat_id)
        conn.execute("DELETE FROM ai_chats WHERE id = %s", (chat_id,))


def _insert_message(conn, chat_id: int, role: str, content: str = "", *, tool_calls=None, tool_call_id="", tool_name="") -> dict:
    return conn.execute(
        """
        INSERT INTO ai_messages (chat_id, role, content, tool_calls, tool_call_id, tool_name)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (chat_id, role, content or "", Jsonb(tool_calls) if tool_calls else None, tool_call_id, tool_name),
    ).fetchone()


_ASK_SKIPPED = {
    "ask_questions": {
        "ok": True,
        "note": "Маркетолог ответил не в карточке, а сообщением ниже — считай его ответом на вопросы",
    },
    "propose_plan": {
        "ok": False,
        "note": "Маркетолог не согласовал план в карточке, а написал сообщение ниже — учти его; "
        "шаги плана выполняй, только если он их согласовал",
    },
    "propose_items": {
        "ok": False,
        "note": "Маркетолог не согласовал таблицу в карточке, а написал сообщение ниже — учти его. К следующему "
        "этапу переходи только после согласования: покажи таблицу этого этапа заново с его правками",
    },
}


def _write_skipped(reason: str, tool: str, args: dict) -> dict:
    """Ответ модели за карточку действия, которую пропустили сообщением: её поправляют словами, и модель
    должна предложить действие заново целиком, а не только исправленную часть."""
    note = (
        "Не выполнено: маркетолог написал сообщение ниже, не нажав кнопку. Если он поправил это действие, "
        "предложи его заново целиком — с прежними аргументами и правками; если отказался — не повторяй"
    )
    if tool == "create_campaign_variants" and isinstance(args, dict) and args.get("experiment"):
        note += ". В карточке был эксперимент (experiment) — передай его снова, иначе варианты соберутся без теста"
    return {"ok": False, "error": reason, "note": note}


def _skip_open_actions(conn, chat_id: int, reason: str, *, by_message: bool = False) -> None:
    """Незакрытые действия закрываются ответом инструмента — иначе история для модели некорректна.

    by_message — пользователь написал сообщение: вопросы и план закрываются им, а не «пропуском».
    """
    ask_reason = "Ответ — в сообщении ниже" if by_message else reason
    rows = conn.execute(
        """
        UPDATE ai_actions SET status = 'rejected', decided_at = NOW(),
               error = CASE WHEN tool = ANY(%s) THEN %s ELSE %s END
        WHERE chat_id = %s AND status IN ('pending', 'approved')
        RETURNING tool_call_id, tool, args
        """,
        (list(ASK_TOOLS), ask_reason, reason, chat_id),
    ).fetchall()
    for r in rows:
        if by_message and r["tool"] in ASK_TOOLS:
            payload = _ASK_SKIPPED[r["tool"]]
        elif by_message:
            payload = _write_skipped(reason, r["tool"], r.get("args") or {})
        else:
            payload = {"ok": False, "error": reason}
        _insert_message(
            conn,
            chat_id,
            "tool",
            json.dumps(payload, ensure_ascii=False),
            tool_call_id=r["tool_call_id"],
            tool_name=r["tool"],
        )


def _submit(user_id: int, chat_id: int, marker: str) -> dict:
    from app.job_handlers import submit
    from app.jobs import PRIORITY_USER

    # marker делает ключ уникальным: новый ход не схлопнется с завершающейся задачей прошлого хода.
    return submit("ai_chat", user_id, {"chat_id": chat_id, "turn": marker}, priority=PRIORITY_USER, max_attempts=1)


def post_message(user_id: int, chat_id: int, text: str) -> dict:
    from app.jobs import public_job

    text = (text or "").strip()
    if not text:
        raise YandexError("empty", "Пустое сообщение")
    if len(text) > 8000:
        raise YandexError("too-long", "Сообщение длиннее 8000 символов")
    # Сразу понятная ошибка, а не чат в статусе «ошибка»: ИИ не подключён или лимит месяца исчерпан.
    ai.check_budget(ai.load_config(user_id, "chat"))
    with pool.connection() as conn:
        chat = _require_chat(conn, user_id, chat_id, lock=True)
        if _chat_out(chat)["status"] == "running":
            raise YandexError("busy", "ИИ ещё отвечает — дождитесь ответа")
        _skip_open_actions(conn, chat_id, "Пропущено: пользователь написал новое сообщение", by_message=True)
        message = _insert_message(conn, chat_id, "user", text)
        conn.execute(
            """
            UPDATE ai_chats SET status = 'running', error = '', updated_at = NOW(),
                   title = CASE WHEN title = '' THEN %s ELSE title END
            WHERE id = %s
            """,
            (text.splitlines()[0][:80], chat_id),
        )
    job = _submit(user_id, chat_id, f"m{message['id']}")
    return {"message": _message_out(message), "job": public_job(job)}


REVIEW_PROMPT = "Сделай разбор пространства: что работает, что затухает и почему, что стоит повторить"


def start_review(user_id: int, workspace_id: int, *, prompt: str = "", title: str = "", chat_id: int | None = None) -> dict:
    """«Разбор» по кнопке: сигналы считает код и сразу кладёт в чат ответом get_signals.

    Модель начинает с готовой выжимки — без хода на её запрос и без чтения сырой статистики.
    Без chat_id создаётся новый чат.
    """
    from app.insights import get_signals, signals_digest

    text = (prompt or "").strip()[:2000] or REVIEW_PROMPT
    title = (title or "").strip()[:120] or f"Разбор {today_msk():%d.%m}"
    ai.check_budget(ai.load_config(user_id, "chat"))
    require_workspace(user_id, workspace_id)
    digest = signals_digest(get_signals(user_id, workspace_id))
    return _start_with_digest(
        user_id, workspace_id, chat_id, text=text, title=title, tool="get_signals", args={}, digest=digest,
        skip_reason="Пропущено: пользователь запустил разбор",
    )


def start_experiment_review(user_id: int, workspace_id: int, experiment_id: int, *, chat_id: int | None = None) -> dict:
    """«Разобрать итоги» эксперимента по кнопке: прогресс считает код и кладёт в чат ответом get_experiments."""
    ai.check_budget(ai.load_config(user_id, "chat"))
    items = experiments.experiments_overview(user_id, workspace_id)["items"]
    exp = next((e for e in items if e["id"] == experiment_id), None)
    if exp is None:
        raise YandexError("not-found", "Эксперимент не найден")
    return _start_with_digest(
        user_id,
        workspace_id,
        chat_id,
        text=f"Разбери итоги эксперимента «{exp['title']}»: подтвердилась ли гипотеза и что делать дальше",
        title=f"Итоги: {exp['title']}"[:120],
        tool="get_experiments",
        args={"experiment_id": experiment_id},
        digest=experiments.digest(items, {experiment_id}),
        skip_reason="Пропущено: пользователь запустил разбор итогов эксперимента",
    )


def _start_with_digest(
    user_id: int, workspace_id: int, chat_id: int | None, *, text: str, title: str, tool: str, args: dict,
    digest: str, skip_reason: str,
) -> dict:
    """Ход по кнопке: сообщение пользователя, вызов tool и готовый ответ на него — модель начинает с данных.

    Без chat_id создаётся новый чат.
    """
    from app.jobs import public_job

    # 9 латинских букв и цифр: такой id примут все OpenAI-совместимые провайдеры.
    call = {"id": "r" + secrets.token_hex(4), "name": tool, "arguments": json.dumps(args, ensure_ascii=False)}
    with pool.connection() as conn:
        if chat_id is None:
            chat = conn.execute(
                "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, %s, %s) RETURNING *",
                (user_id, workspace_id, title),
            ).fetchone()
        else:
            chat = _require_chat(conn, user_id, chat_id, lock=True)
            if chat["workspace_id"] != workspace_id:
                raise YandexError("not-found")
            if _chat_out(chat)["status"] == "running":
                raise YandexError("busy", "ИИ ещё отвечает — дождитесь ответа")
            _skip_open_actions(conn, chat["id"], skip_reason)
        message = _insert_message(conn, chat["id"], "user", text)
        _insert_message(conn, chat["id"], "assistant", tool_calls=[call])
        _insert_message(
            conn, chat["id"], "tool", _dump({"digest": digest}), tool_call_id=call["id"], tool_name=call["name"]
        )
        conn.execute(
            """
            UPDATE ai_chats SET status = 'running', error = '', updated_at = NOW(),
                   title = CASE WHEN title = '' THEN %s ELSE title END
            WHERE id = %s
            """,
            (title, chat["id"]),
        )
        row = conn.execute(_CHAT_SELECT + " WHERE ch.id = %s", (chat["id"],)).fetchone()
    job = _submit(user_id, chat["id"], f"r{message['id']}")
    return {"chat": _chat_out(row), "message": _message_out(message), "job": public_job(job)}


def _decide(user_id: int, chat_id: int, action_ids: list[int] | None, approve: bool) -> tuple[list[dict], int]:
    with pool.connection() as conn:
        _require_chat(conn, user_id, chat_id, lock=True)
        params: list = [chat_id, list(ASK_TOOLS)]
        where = "chat_id = %s AND status = 'pending' AND tool <> ALL(%s)"
        if action_ids is not None:
            where += " AND id = ANY(%s)"
            params.append(action_ids)
        rows = conn.execute(
            f"""
            UPDATE ai_actions SET status = %s, decided_at = NOW()
            WHERE {where}
            RETURNING *
            """,
            ["approved" if approve else "rejected", *params],
        ).fetchall()
        if not approve:
            for r in rows:
                _insert_message(
                    conn,
                    chat_id,
                    "tool",
                    json.dumps({"ok": False, "error": "Пользователь отклонил действие"}, ensure_ascii=False),
                    tool_call_id=r["tool_call_id"],
                    tool_name=r["tool"],
                )
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_actions WHERE chat_id = %s AND status = 'pending'", (chat_id,)
        ).fetchone()["n"]
        if rows and not left:
            conn.execute("UPDATE ai_chats SET status = 'running', error = '', updated_at = NOW() WHERE id = %s", (chat_id,))
    return rows, int(left)


def decide_action(user_id: int, action_id: int, *, approve: bool, remember: bool = False, exclude=None) -> dict:
    """Решение по одному действию; remember — разрешить его категории без согласования впредь;
    exclude — id строк, с которых маркетолог снял галочки: их в действии не будет."""
    from app.jobs import public_job

    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM ai_actions WHERE id = %s AND user_id = %s", (action_id, user_id)).fetchone()
    if row is None:
        raise YandexError("not-found")
    if row["tool"] in ASK_TOOLS:
        raise YandexError("args", "Ответьте в карточке вопросов, плана или таблицы")
    if approve and exclude and row["status"] == "pending":
        _exclude_rows(user_id, row, exclude)
    rows, left = _decide(user_id, row["chat_id"], [action_id], approve)
    if not rows:
        raise YandexError("decided", "Решение по этому действию уже принято")
    if approve and remember:
        cats = [c for c in (row["category"] or "").split(",") if c in CATEGORY_LABELS]
        with pool.connection() as conn:
            ws_id = conn.execute("SELECT workspace_id FROM ai_chats WHERE id = %s", (row["chat_id"],)).fetchone()["workspace_id"]
        if cats:
            update_workspace(user_id, ws_id, ai_access={"categories": {c: True for c in cats}})
    job = _submit(user_id, row["chat_id"], f"a{action_id}") if not left else None
    return {"action": _action_out(rows[0]), "pending": left, "job": public_job(job) if job else None}


def decide_all(user_id: int, chat_id: int, *, approve: bool, exclude: dict | None = None) -> dict:
    """Решение по всем ждущим действиям чата; exclude — {id действия: [id снятых строк]}."""
    from app.jobs import public_job

    if approve and isinstance(exclude, dict):
        with pool.connection() as conn:
            _require_chat(conn, user_id, chat_id)
            pending = conn.execute(
                "SELECT * FROM ai_actions WHERE chat_id = %s AND status = 'pending' AND tool <> ALL(%s)",
                (chat_id, list(ASK_TOOLS)),
            ).fetchall()
        for row in pending:
            paths = exclude.get(str(row["id"])) or exclude.get(row["id"])
            if paths:
                _exclude_rows(user_id, row, paths)
    rows, left = _decide(user_id, chat_id, None, approve)
    if not rows:
        raise YandexError("decided", "Нет действий, ждущих решения")
    job = _submit(user_id, chat_id, f"all{rows[-1]['id']}") if not left else None
    return {"decided": len(rows), "pending": left, "job": public_job(job) if job else None}


# --- Ответы на вопросы и решение по плану ------------------------------------------------------


def _pending_interactive(user_id: int, action_id: int, tool: str) -> dict:
    """Карточка вопросов или плана, ждущая ответа; пока ИИ отвечает, отвечать в ней нельзя."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT a.*, ch.workspace_id FROM ai_actions a JOIN ai_chats ch ON ch.id = a.chat_id
            WHERE a.id = %s AND a.user_id = %s
            """,
            (action_id, user_id),
        ).fetchone()
        if row is None or row["tool"] != tool:
            raise YandexError("not-found")
        if row["status"] != "pending":
            raise YandexError("decided", "Решение уже принято")
        if _chat_out(_require_chat(conn, user_id, row["chat_id"]))["status"] == "running":
            raise YandexError("busy", "ИИ ещё отвечает — дождитесь ответа")
    return row


def _close_interactive(
    user_id: int, row: dict, *, status: str, result: dict | None, error: str, reply: dict
) -> tuple[dict, int]:
    """Закрывает карточку; ответ маркетолога уходит модели результатом её вызова.

    → (строка действия, сколько действий чата ещё ждёт решения).
    """
    with pool.connection() as conn:
        chat = _require_chat(conn, user_id, row["chat_id"], lock=True)
        if _chat_out(chat)["status"] == "running":
            raise YandexError("busy", "ИИ ещё отвечает — дождитесь ответа")
        updated = conn.execute(
            """
            UPDATE ai_actions SET status = %s, result = %s, error = %s, decided_at = NOW()
            WHERE id = %s AND status = 'pending'
            RETURNING *
            """,
            (status, Jsonb(result) if result is not None else None, error, row["id"]),
        ).fetchone()
        if updated is None:
            raise YandexError("decided", "Решение уже принято")
        _insert_message(
            conn, row["chat_id"], "tool", _dump(reply), tool_call_id=row["tool_call_id"], tool_name=row["tool"]
        )
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_actions WHERE chat_id = %s AND status = 'pending'", (row["chat_id"],)
        ).fetchone()["n"]
        if not left:
            conn.execute(
                "UPDATE ai_chats SET status = 'running', error = '', updated_at = NOW() WHERE id = %s",
                (row["chat_id"],),
            )
    return updated, int(left)


def _compose_answers(questions: list[dict], answers) -> tuple[list[dict], list[dict]]:
    """Ответы из карточки → ([{question, answer} | {question, skipped}], факты для памяти пространства).

    Ответ — выбранные варианты (только из предложенных) и свой текст через «; ».
    """
    by_n: dict[int, dict] = {}
    for index, item in enumerate(answers if isinstance(answers, list) else []):
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError):
            n = index + 1
        by_n.setdefault(n, item)
    out: list[dict] = []
    facts: list[dict] = []
    for question in questions:
        item = by_n.get(question.get("n")) or {}
        raw = item.get("selected")
        if isinstance(raw, str):
            raw = [raw]
        picked: list[str] = []
        for option in raw if isinstance(raw, list) else []:
            label = _line(option, 200)
            if label in (question.get("options") or []) and label not in picked:
                picked.append(label)
        if not question.get("multiple"):
            picked = picked[:1]
        text = _para(item.get("text"), 1000)
        answer = "; ".join([*picked, text] if text else picked)[:1000]
        if not answer:
            out.append({"question": question["question"], "skipped": True})
            continue
        out.append({"question": question["question"], "answer": answer})
        if question.get("key") and question.get("remember"):
            facts.append({"key": question["key"], "question": question["question"], "answer": answer})
    return out, facts


def answer_questions(user_id: int, action_id: int, *, answers=None, comment: str = "", skip: bool = False) -> dict:
    """Ответы в карточке вопросов: вопросы с key — в память пространства, всё — модели результатом вызова."""
    from app.jobs import public_job

    row = _pending_interactive(user_id, action_id, "ask_questions")
    payload = row.get("args") or {}
    comment = _para(comment, 2000)
    if skip:
        reply = {"ok": False, "error": "Пользователь не стал отвечать — работай с тем, что есть, или спроси иначе"}
        if comment:
            reply["comment"] = comment
        updated, left = _close_interactive(
            user_id,
            row,
            status="rejected",
            result={"comment": comment} if comment else None,
            error="Пропущено",
            reply=reply,
        )
    else:
        out, facts = _compose_answers(payload.get("questions") or [], answers)
        if not comment and not any("answer" in a for a in out):
            raise YandexError("args", "Ответьте хотя бы на один вопрос или напишите комментарий")
        remembered: list[str] = []
        if facts:
            try:
                remember_ai_facts(user_id, row["workspace_id"], facts)
                remembered = [f["key"] for f in facts]
            except Exception:  # ответ модели важнее памяти: не сохранилось — спросит ещё раз
                log.warning("Ответы на вопросы ИИ не сохранены в память пространства", exc_info=True)
        result = {"answers": out, "remembered": remembered, "comment": comment}
        reply = {"ok": True, **result}
        if payload.get("answered"):
            reply["already_answered"] = payload["answered"]
        updated, left = _close_interactive(user_id, row, status="done", result=result, error="", reply=reply)
    job = _submit(user_id, row["chat_id"], f"q{action_id}") if not left else None
    return {"action": _action_out(updated), "pending": left, "job": public_job(job) if job else None}


def decide_plan(user_id: int, action_id: int, *, steps=None, comment: str = "", approve: bool = True) -> dict:
    """Решение по плану: какие шаги делать. Каждое изменение по шагу потом подтверждается отдельно."""
    from app.jobs import public_job

    row = _pending_interactive(user_id, action_id, "propose_plan")
    plan = (row.get("args") or {}).get("steps") or []
    numbers = [s["n"] for s in plan]
    chosen = _numbers(steps, numbers)
    comment = _para(comment, 2000)
    if not approve:
        error = "Пользователь отклонил план"
        reply = {
            "ok": False,
            "error": error,
            "note": "Не выполняй шаги этого плана; если есть комментарий — учти его и предложи новый план",
        }
        if comment:
            reply["comment"] = comment
        updated, left = _close_interactive(
            user_id,
            row,
            status="rejected",
            result={"comment": comment} if comment else None,
            error=error,
            reply=reply,
        )
    else:
        if not chosen:
            raise YandexError("args", "Отметьте хотя бы один шаг или отклоните план")
        result = {
            "approved_steps": chosen,
            "declined_steps": [n for n in numbers if n not in chosen],
            "comment": comment,
        }
        reply = {
            "ok": True,
            **result,
            "approved": [{"n": s["n"], "title": s["title"]} for s in plan if s["n"] in chosen],
            "note": "Выполняй согласованные шаги по порядку инструментами действий с plan_step — номером шага. "
            "Отклонённые шаги не делай; новые шаги — только через новый propose_plan.",
        }
        updated, left = _close_interactive(user_id, row, status="done", result=result, error="", reply=reply)
    job = _submit(user_id, row["chat_id"], f"p{action_id}") if not left else None
    return {"action": _action_out(updated), "pending": left, "job": public_job(job) if job else None}


def _numbers(values, allowed: list[int]) -> list[int]:
    """Номера строк или шагов, отмеченные в карточке: только из allowed, без повторов, по порядку."""
    out: list[int] = []
    for value in values if isinstance(values, list) else []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n in allowed and n not in out:
            out.append(n)
    return sorted(out)


_ITEM_PREFIX = {"negative": "минус-фраза", "title": "заголовок", "text": "текст"}


def _items_brief(rows: list[dict]) -> dict[str, list[str]]:
    """Строки таблицы для модели: по разделам, фразы и тексты — как есть, остальное — ячейками через «—»."""
    out: dict[str, list[str]] = {}
    for row in rows:
        if row["kind"] in ("keyword", "negative", "title", "text"):
            text = row["cells"][0]
        else:
            text = " — ".join(c for c in row["cells"] if c)
        if row["kind"] in _ITEM_PREFIX:
            text = f"{_ITEM_PREFIX[row['kind']]}: {text}"
        out.setdefault(row["section"] or "—", []).append(text)
    return out


def decide_items(user_id: int, action_id: int, *, rows=None, comment: str = "", approve: bool = True) -> dict:
    """Решение по таблице propose_items: отмеченные строки согласованы, снятые — нет."""
    from app.jobs import public_job

    row = _pending_interactive(user_id, action_id, "propose_items")
    payload = row.get("args") or {}
    table = payload.get("rows") or []
    stage = ITEM_STAGES.get(payload.get("stage") or "other", "")
    comment = _para(comment, 2000)
    if not approve:
        error = "Пользователь не согласовал таблицу"
        reply = {
            "ok": False,
            "error": error,
            "stage": stage,
            "note": "Этап не согласован: к следующему этапу не переходи и ничего из таблицы не делай. Учти "
            "комментарий и предложи таблицу этого этапа заново",
        }
        if comment:
            reply["comment"] = comment
        updated, left = _close_interactive(
            user_id,
            row,
            status="rejected",
            result={"comment": comment} if comment else None,
            error=error,
            reply=reply,
        )
    else:
        chosen = _numbers(rows, [r["n"] for r in table])
        if not chosen:
            raise YandexError("args", "Отметьте хотя бы одну строку или отклоните таблицу")
        result = {
            "approved_rows": chosen,
            "removed_rows": [r["n"] for r in table if r["n"] not in chosen],
            "comment": comment,
        }
        removed = [r for r in table if r["n"] not in chosen]
        reply = {
            "ok": True,
            "stage": stage,
            "approved": _items_brief([r for r in table if r["n"] in chosen]),
            "comment": comment,
            "note": "Этап согласован. Дальше используй только согласованные строки (и правки из комментария); "
            "снятые галочкой не используй и не предлагай снова без новой причины. Следующий этап — новой "
            "таблицей propose_items",
        }
        if removed:
            reply["removed"] = _items_brief(removed)
        updated, left = _close_interactive(user_id, row, status="done", result=result, error="", reply=reply)
    job = _submit(user_id, row["chat_id"], f"i{action_id}") if not left else None
    return {"action": _action_out(updated), "pending": left, "job": public_job(job) if job else None}


# --- Ход агента ---------------------------------------------------------------------------------


def _history(chat_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_messages WHERE chat_id = %s ORDER BY id DESC LIMIT 400", (chat_id,)
        ).fetchall()
    rows.reverse()
    start = max(0, len(rows) - HISTORY_MESSAGES)
    user_idx = [i for i, r in enumerate(rows) if r["role"] == "user"]
    after = [i for i in user_idx if i >= start]
    start = after[0] if after else (user_idx[-1] if user_idx else len(rows))
    rows = rows[start:]

    out: list[dict] = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r["role"] == "assistant" and r.get("tool_calls"):
            calls = r["tool_calls"]
            out.append(
                {
                    "role": "assistant",
                    "content": r["content"] or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                        for c in calls
                    ],
                }
            )
            j = i + 1
            answered: dict[str, str] = {}
            while j < len(rows) and rows[j]["role"] == "tool":
                answered[rows[j]["tool_call_id"]] = rows[j]["content"]
                j += 1
            for c in calls:
                content = answered.get(c["id"], json.dumps({"ok": False, "error": "нет результата"}, ensure_ascii=False))
                out.append({"role": "tool", "tool_call_id": c["id"], "content": content})
            i = j
            continue
        if r["role"] in ("user", "assistant"):
            out.append({"role": r["role"], "content": r["content"]})
        i += 1
    return out


def _set_status(chat_id: int, status: str, error: str = "") -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE ai_chats SET status = %s, error = %s, updated_at = NOW() WHERE id = %s",
            (status, error[:500], chat_id),
        )


def _touch(chat_id: int) -> None:
    with pool.connection() as conn:
        conn.execute("UPDATE ai_chats SET updated_at = NOW() WHERE id = %s", (chat_id,))


def _run_action(ctx: Context, action: dict) -> None:
    _prepare, execute = WRITE_TOOLS[action["tool"]]
    args = dict(action["args"] or {})
    args.pop("_diff", None)
    args.pop("_items", None)
    removed = args.pop("_removed", None) or []
    result = None
    ctx.action = action
    try:
        result = execute(ctx, args)
        status, error = "done", ""
        payload = {"ok": True, "result": result}
        if removed:
            payload["removed_by_user"] = [f"{r['section']}: {r['text']}" if r.get("section") else r["text"] for r in removed]
            payload["note"] = "Маркетолог снял галочки с этих строк — они не выполнены; не предлагай их снова без новой причины"
    except YandexError as exc:
        status, error = "error", exc.message or exc.code
        payload = {"ok": False, "error": error}
    except Exception:
        log.exception("ИИ-действие %s", action["id"])
        status, error = "error", "Внутренняя ошибка при выполнении"
        payload = {"ok": False, "error": error}
    finally:
        ctx.action = None
    with pool.connection() as conn:
        conn.execute(
            "UPDATE ai_actions SET status = %s, result = %s, error = %s WHERE id = %s",
            (status, Jsonb(_jsonable(_for_ai(result))) if result is not None else None, error, action["id"]),
        )
        _insert_message(
            conn, action["chat_id"], "tool", _dump(payload), tool_call_id=action["tool_call_id"], tool_name=action["tool"]
        )


def _normalize_call(name: str, args: dict) -> dict:
    """Аргументы модели к виду, который ждут карточка и галочки: без служебных полей, списки — списками."""
    for key in ("_diff", "_items", "_removed"):  # служебные поля карточки, не от модели
        args.pop(key, None)
    if name in ("add_keywords", "add_campaign_negatives") and isinstance(args.get("phrases"), str):
        args["phrases"] = args["phrases"].splitlines()
    spec = args.get("spec")
    if name == "create_campaign" and isinstance(spec, dict):
        from app.actions import _phrases as parse_phrases

        if spec.get("group") and not spec.get("groups"):
            spec["groups"] = [spec["group"]]
        spec.pop("group", None)
        for group in spec.get("groups") if isinstance(spec.get("groups"), list) else []:
            if isinstance(group, dict) and isinstance(group.get("keywords"), str):
                group["keywords"] = parse_phrases(group["keywords"])
        if isinstance(spec.get("negative_keywords"), str):
            spec["negative_keywords"] = spec["negative_keywords"].splitlines()
    return args


def _handle_call(ctx: Context, chat_id: int, message_id: int, call: dict) -> int:
    """Выполняет вызов инструмента; возвращает 1, если действие ждёт согласования."""
    name = call["name"]

    def reply(payload: dict) -> int:
        with pool.connection() as conn:
            _insert_message(conn, chat_id, "tool", _dump(payload), tool_call_id=call["id"], tool_name=name)
        return 0

    try:
        args = json.loads(call["arguments"] or "{}")
        if not isinstance(args, dict):
            raise ValueError
    except ValueError:
        return reply({"ok": False, "error": "Аргументы должны быть JSON-объектом"})
    args = _normalize_call(name, args)

    if name in READ_TOOLS:
        try:
            return reply(READ_TOOLS[name](ctx, args))
        except YandexError as exc:
            return reply({"ok": False, "error": exc.message or exc.code})
        except Exception:
            log.exception("ИИ-инструмент %s", name)
            return reply({"ok": False, "error": "Внутренняя ошибка инструмента"})

    if name in ASK_TOOLS:
        try:
            payload = ASK_TOOLS[name](ctx, args)
        except YandexError as exc:
            return reply({"ok": False, "error": exc.message or exc.code})
        if name == "ask_questions" and not payload["questions"]:
            return reply(
                {
                    "ok": True,
                    "already_answered": payload["answered"],
                    "note": "На эти вопросы маркетолог уже ответил — используй ответы",
                }
            )
        summary = payload.get("understanding") or payload.get("goal") or payload.get("title") or TOOL_LABELS[name]
        with pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_actions (chat_id, message_id, user_id, tool_call_id, tool, args, summary, category, status, auto)
                VALUES (%s, %s, %s, %s, %s, %s, %s, '', 'pending', FALSE)
                """,
                (chat_id, message_id, ctx.user_id, call["id"], name, Jsonb(payload), _line(summary, 1000)),
            )
        return 1

    if name not in WRITE_TOOLS:
        return reply({"ok": False, "error": f"Нет инструмента {name}"})
    prepare, _execute = WRITE_TOOLS[name]
    try:
        plan = prepare(ctx, args)
    except YandexError as exc:
        return reply({"ok": False, "error": exc.message or exc.code})
    changes = _changes(ctx, name, args)
    auto = _allowed(ctx.access, plan.categories, plan.change_pct)
    stored = {**args, "_diff": changes} if changes else dict(args)
    picks = [] if auto else _picks(ctx, name, args)
    if picks:
        stored["_items"] = picks
    with pool.connection() as conn:
        action = conn.execute(
            """
            INSERT INTO ai_actions (chat_id, message_id, user_id, tool_call_id, tool, args, summary, category, status, auto, decided_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NOW() END)
            RETURNING *
            """,
            (
                chat_id,
                message_id,
                ctx.user_id,
                call["id"],
                name,
                Jsonb(stored),
                plan.summary[:1000],
                ",".join(plan.categories),
                "approved" if auto else "pending",
                auto,
                auto,
            ),
        ).fetchone()
    if not auto:
        return 1
    _run_action(ctx, action)
    return 0


def run_chat(user_id: int, chat_id: int, progress=None) -> dict:
    """Ход агента: выполнить одобренное, затем звать модель, пока она вызывает инструменты."""
    with pool.connection() as conn:
        chat = _require_chat(conn, user_id, chat_id)

    def step(n: int, text: str) -> None:
        if progress:
            progress(n, MAX_STEPS, text)

    try:
        cfg = ai.load_config(user_id, "chat", chat["workspace_id"])
        ctx = Context(user_id, chat["workspace_id"])
        ctx.chat_id = chat_id
    except YandexError as exc:
        _set_status(chat_id, "error", exc.message or exc.code)
        return {"status": "error"}

    with acting_as("ai"):
        with pool.connection() as conn:
            approved = conn.execute(
                "SELECT * FROM ai_actions WHERE chat_id = %s AND status = 'approved' ORDER BY id", (chat_id,)
            ).fetchall()
        for action in approved:
            step(0, "Выполняю одобренные действия…")
            _run_action(ctx, action)
        with pool.connection() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM ai_actions WHERE chat_id = %s AND status = 'pending'", (chat_id,)
            ).fetchone()["n"]
        if pending:
            _set_status(chat_id, "waiting")
            return {"status": "waiting"}

        system = {"role": "system", "content": _system_prompt(ctx)}
        for n in range(MAX_STEPS):
            history = _history(chat_id)
            if not history or (history[-1]["role"] == "assistant" and not history[-1].get("tool_calls")):
                break
            step(n, "ИИ думает…")
            _touch(chat_id)
            try:
                reply = ai.complete(cfg, [system, *history], TOOLS)
            except YandexError as exc:
                _set_status(chat_id, "error", exc.message or exc.code)
                return {"status": "error"}
            except Exception:
                log.exception("ИИ-чат %s", chat_id)
                _set_status(chat_id, "error", "Сбой при обращении к ИИ")
                return {"status": "error"}
            calls = [c for c in reply["tool_calls"] if c.get("name")]
            with pool.connection() as conn:
                message = _insert_message(conn, chat_id, "assistant", reply["content"], tool_calls=calls or None)
            if not calls:
                break
            waiting = 0
            for call in calls:
                step(n, TOOL_LABELS.get(call["name"], call["name"]))
                waiting += _handle_call(ctx, chat_id, message["id"], call)
            if waiting:
                _set_status(chat_id, "waiting")
                return {"status": "waiting", "pending": waiting}
        else:
            with pool.connection() as conn:
                _insert_message(
                    conn,
                    chat_id,
                    "assistant",
                    "Остановился после нескольких шагов подряд. Напишите «продолжай», если нужно продолжить.",
                )
    _set_status(chat_id, "idle")
    return {"status": "idle"}
