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

from app import ai, audiences, context_files, experiments, image_gen
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
    list_workspace_accounts,
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
# Ответы чтения из прошлых ходов в запрос идут укороченными: полный вызов с ~100 тыс. токенов не укладывался во
# время ответа. Нужны данные — модель читает заново; согласования, ответы на вопросы и записи не сокращаются.
OLD_RESULT_CHARS = 1500
# Один вызов модели в чате: запросы большие, на обычные 90 с хватает не всегда. Задача шлёт heartbeat — 5 минут запаса.
CHAT_TIMEOUT_SECONDS = 150
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
        # Правки с доски, которые сейчас сохраняются: согласованы, как и строки таблиц.
        self.board_ops: list[dict] = []

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

    if args.get("campaign_row_id") in (None, ""):
        return _workspace_images(ctx, args)
    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    out = campaign_images(ctx.user_id, cid)
    out["hint"] = (
        "Хэш картинки — в extras.image_hashes объявления. in_campaign — картинки, что уже стоят в объявлениях "
        "кампании (ads — в скольких); library — остальные картинки кабинета"
    )
    return out


# Картинки новой кампании: откуда их брать, маркетолог выбирает в ask_questions с key image_source.
IMAGE_SOURCE_KEY = "image_source"
IMAGE_SOURCES = {
    "campaigns": "Взять из других кампаний",
    "upload": "Загружу свои",
    "generate": "Сгенерировать по описанию",
    "none": "Без картинок",
}
# Вариант ответа → что для него должно быть подключено; без этого в карточке его не выбрать.
IMAGE_SOURCE_NEEDS = {IMAGE_SOURCES["generate"]: "image_gen"}
_NEED_TEXT = {"image_gen": image_gen.NOT_CONFIGURED}
MAX_GROUP_IMAGES = 5
_IMAGE_HASH_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_GENERATE_RE = re.compile(r"сгенер|генерац|генерир|нарис|нейросет", re.I)


def _workspace_images(ctx: Context, args: dict) -> dict:
    from app.ads import workspace_images as found_images

    account_id = None
    if args.get("account_id") not in (None, ""):
        account_id = int(ctx.require_account(_int(args, "account_id"))["id"])
    found = found_images(ctx.user_id, workspace_campaign_row_ids(ctx.user_id, ctx.ws_id), account_id=account_id)
    return {
        "images": [{k: v for k, v in i.items() if k != "preview_url"} for i in found["items"]],
        "more": found["more"],
        "hint": (
            "Картинки из объявлений кампаний пространства, чаще используемые — первыми. Самих картинок ты не "
            "видишь — выбирай по кампаниям, где они стоят. Предлагай таблицей propose_items stage=images: строка — "
            "картинка, image {hash, account_id}, section — группа, cells [название, откуда, почему]; в группе до "
            f"{MAX_GROUP_IMAGES}. Картинку из другого кабинета сервис скопирует сам"
        ),
    }


def _wants_generation(ctx: Context) -> bool:
    """Генерация платная: только если маркетолог сам её выбрал — ответом на вопрос или словами в чате."""
    if ctx.chat_id is None:
        return True
    with pool.connection() as conn:
        said = [
            m["content"] or ""
            for m in conn.execute(
                "SELECT content FROM ai_messages WHERE chat_id = %s AND role = 'user'", (ctx.chat_id,)
            ).fetchall()
        ]
        cards = conn.execute(
            "SELECT result FROM ai_actions WHERE chat_id = %s AND tool = 'ask_questions' AND status = 'done'",
            (ctx.chat_id,),
        ).fetchall()
    for card in cards:
        result = card["result"] or {}
        said.append(str(result.get("comment") or ""))
        said += [str(a.get("answer") or "") for a in result.get("answers") or [] if isinstance(a, dict)]
    return any(_GENERATE_RE.search(text) for text in said)


def _generate_into(user_id: int, workspace_id: int, account_id: int, prompt, count, *, name: str) -> dict:
    """Картинки по описанию → библиотека кабинета: {images: [{hash, preview_url, account_id}], errors, model}."""
    try:
        count = int(count) if count not in (None, "") else 1
    except (TypeError, ValueError):
        raise YandexError("args", "count — число картинок") from None
    # Доступ к кабинету — до генерации: картинки платные, загрузить их должно быть куда.
    token, login = audiences.account_access(user_id, account_id)
    result = image_gen.generate(user_id, str(prompt or ""), count=count, workspace_id=workspace_id)
    uploaded, errors = image_gen.upload(token, login, result["images"], name=name)
    errors = result["errors"] + errors
    if not uploaded:
        raise YandexError("direct", "Директ не принял сгенерированные картинки: " + (errors[0] if errors else "без ответа"))
    return {"images": [{**i, "account_id": account_id} for i in uploaded], "errors": errors, "model": result["model"]}


def _t_generate_images(ctx: Context, args: dict) -> dict:
    if not image_gen.is_configured(ctx.user_id):
        raise YandexError(
            "image-not-configured",
            image_gen.NOT_CONFIGURED + " — предложи взять картинки из других кампаний или загрузить свои",
        )
    if not _wants_generation(ctx):
        raise YandexError(
            "args",
            "Генерация платная — сначала спроси маркетолога, откуда брать картинки (ask_questions с key "
            f"{IMAGE_SOURCE_KEY}), и генерируй, только если он выбрал генерацию",
        )
    account_id = int(_audience_account(ctx, args)["id"])
    group = _line(args.get("group"), 120)
    count = args.get("count") if args.get("count") not in (None, "") else 2
    out = _generate_into(ctx.user_id, ctx.ws_id, account_id, args.get("prompt"), count, name=group or "ИИ")
    return {
        "account_id": account_id,
        "images": [{"hash": i["hash"], "account_id": account_id} for i in out["images"]],
        "errors": out["errors"],
        "hint": (
            "Картинки уже в библиотеке кабинета. Покажи их маркетологу таблицей propose_items stage=images "
            "(image {hash, account_id}, section — группа, cells [название, «сгенерирована», почему]) — он увидит "
            "превью и отберёт"
        ),
    }


def _workspace_account(user_id: int, workspace_id: int, account_id) -> int:
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        raise YandexError("args", "Укажите кабинет") from None
    if account_id not in {int(a["id"]) for a in list_workspace_accounts(user_id, workspace_id)}:
        raise YandexError("scope", "Кабинет не подключён к этому пространству")
    return account_id


def workspace_images(user_id: int, workspace_id: int, *, account_id=None) -> dict:
    """Картинки кампаний пространства для доски: {items: [{hash, account_id, preview_url, campaigns, ads}], more}."""
    from app.ads import workspace_images as found_images

    require_workspace(user_id, workspace_id)
    if account_id not in (None, ""):
        account_id = _workspace_account(user_id, workspace_id, account_id)
    else:
        account_id = None
    return found_images(user_id, workspace_campaign_row_ids(user_id, workspace_id), account_id=account_id)


def generate_images(user_id: int, workspace_id: int, *, account_id, prompt, count=1) -> dict:
    """Кнопка «Сгенерировать» на доске: картинки по описанию маркетолога — сразу в библиотеку кабинета."""
    require_workspace(user_id, workspace_id)
    account_id = _workspace_account(user_id, workspace_id, account_id)
    return _generate_into(user_id, workspace_id, account_id, prompt, count, name="ИИ")


AUDIENCE_LISTS_MAX = 40


def _audience_account(ctx: Context, args: dict) -> dict:
    """Кабинет для аудиторий: account_id или единственный кабинет пространства."""
    if args.get("account_id") not in (None, ""):
        return ctx.require_account(_int(args, "account_id"))
    accounts = ctx.ws.get("accounts") or []
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise YandexError("args", "К пространству не подключён кабинет Директа")
    raise YandexError("args", "Укажи account_id — кабинет, в котором будет кампания")


def _t_audiences(ctx: Context, args: dict) -> dict:
    account = _audience_account(ctx, args)
    token, login = audiences.account_access(ctx.user_id, int(account["id"]))
    goals = _goal_names(ctx)
    query = str(args.get("query") or "").strip()
    catalog = audiences.interest_catalog(token)
    interest_names = {k: v["name"] for k, v in catalog.items()}
    out: dict = {
        "account_id": int(account["id"]),
        "goals": [{"goal_id": g, "name": n} for g, n in goals.items()],
    }
    try:
        lists = [i for i in audiences.account_lists(token, login) if audiences.can_target(i)]
    except YandexError as exc:
        lists, out["lists_error"] = [], exc.message or exc.code
    if query:
        words = audiences._norm(query).split()
        lists = [i for i in lists if all(w in audiences._norm(i["name"]) for w in words)]
    lists.sort(key=lambda i: -i["id"])
    out["lists_total"] = len(lists)
    out["lists"] = [
        {
            "list_id": i["id"],
            "name": i["name"],
            "kind": "интересы" if i["type"] == "AUDIENCE" else "LAL" if i["lal"] else "ретаргетинг и сегменты",
            "rules": audiences.list_text(i, goals=goals, interests=interest_names),
        }
        for i in lists[:AUDIENCE_LISTS_MAX]
    ]
    if not catalog:
        out["interests_error"] = "Справочник интересов Директа сейчас недоступен — попробуй позже"
    elif query:
        out["interests"] = [
            {"interest_id": i["id"], "name": i["name"], "path": i["path"], "description": i["description"][:200]}
            for i in audiences.find_interests(catalog, query)
        ]
    else:
        out["interest_sections"] = audiences.interest_roots(catalog)
    rights = audiences.account_rights(ctx.user_id, int(account["id"]))
    # Приложение Аудиторий не задано на сервере — новых LAL нет вовсе, только готовые условия.
    if rights["enabled"]:
        out["lookalike"] = {"available": rights["rights"]}
        if rights["rights"]:
            try:
                segments = audiences.list_segments(audiences.audience_token(rights))
            except YandexError as exc:
                if exc.code in ("audience-scope", "audience-config"):
                    out["lookalike"] = {"available": False, "why": exc.message or audiences.rights_text(rights["login"])}
                else:
                    out["lookalike"]["segments_error"] = exc.message or exc.code
            else:
                segments.sort(key=lambda s: -int(s["id"]))
                out["lookalike"]["segments"] = [
                    {"segment_id": int(s["id"]), "name": str(s.get("name") or ""), "what": audiences.segment_text(s)}
                    for s in segments[:AUDIENCE_LISTS_MAX]
                ]
        else:
            out["lookalike"]["why"] = audiences.rights_text(rights["login"])
    new_lal = "lookalike" in out
    notes = [
        "Аудитории предлагай таблицей propose_items stage=audiences: строка — аудитория, section — группа, "
        "audience — объект: {type: retargeting, goal_ids, exclude_goal_ids, days} (или rules [{operator ANY|ALL|NONE, "
        "goal_ids, days}]) — цели из goals; {type: list, list_id} — готовое условие из lists; {type: interests, "
        "interest_ids} — интересы (только в сетях)" + ("; {type: lookalike, source, similarity} — новый LAL." if new_lal else "."),
        (
            "LAL (похожие на покупателей): готовое условие кабинета с kind LAL или новый LAL {type: lookalike, source: "
            "{goal_id} — похожие на выполнивших цель из goals | {segment_id} — на свой сегмент Аудиторий из "
            "lookalike.segments (например, список клиентов из CRM) | {counter_id} — на посетителей сайта, similarity 1–5 "
            "(по умолчанию 3: больше — ближе к источнику, но меньше охват)}. Новый LAL создастся в Яндекс Аудиториях "
            "вместе с кампанией и будет готов через несколько часов — до этого Директ может не дать нацелить на него "
            "группу. "
            if new_lal
            else "LAL (похожие на покупателей): только готовое условие кабинета с kind LAL из lists — новые LAL "
            "на сервисе не подключены, type lookalike не предлагай. "
        )
        + audiences.LAL_NOTE
        + ".",
        "Интересы ищи по query: «путешествия», «ремонт», «автомобили».",
    ]
    if new_lal and not out["lookalike"]["available"]:
        notes.insert(2, "Новый LAL сейчас не создать: " + out["lookalike"]["why"] + ". Скажи об этом маркетологу, если LAL нужен")
    if not goals:
        notes.insert(0, "У проекта нет целей Метрики — ретаргетинг по целям недоступен, только готовые условия и интересы")
    if out["lists_total"] > AUDIENCE_LISTS_MAX:
        notes.append(f"Условий больше {AUDIENCE_LISTS_MAX} — показаны новые; остальные ищи по query")
    out["notes"] = notes
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
    "get_audiences": _t_audiences,
    "get_wordstat": _t_wordstat,
    "get_experiments": _t_experiments,
    "get_headline_stats": _t_headlines,
    "get_slices": _t_slices,
    "check_links": _t_links,
    "get_ad_extras": _t_ad_extras,
    "list_ad_images": _t_ad_images,
    "generate_images": _t_generate_images,
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
# цели → анализ → ключи и группы → (аудитории) → заголовки и тексты → (картинки) → настройки. create_campaign с
# группами не пройдёт, пока этапы не согласованы, а фразы, минус-фразы, заголовки, тексты и аудитории в
# ней — только из согласованных строк (фразы и тексты — ещё и из слов самого маркетолога). То же для фраз и объявлений, которые потом
# добавляются в кампанию, созданную в этом чате.

ITEM_STAGES = {
    "goals": "Цели кампании",
    "analysis": "Анализ",
    "keywords": "Ключи и группы",
    "negatives": "Минус-фразы",
    "audiences": "Аудитории",
    "ads": "Заголовки и тексты",
    "images": "Картинки",
    "extras": "Ссылка и расширения",
    "settings": "Настройки",
    "other": "На согласование",
}
ITEM_KINDS = (
    "goal", "direction", "keyword", "negative", "audience", "title", "text", "image", "extra", "setting", "other",
)
_STAGE_KINDS = {
    "goals": "goal",
    "analysis": "direction",
    "keywords": "keyword",
    "negatives": "negative",
    "audiences": "audience",
    "images": "image",
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


_AD_PARTS = re.compile(r"\s*[;|\n]\s*")
_AD_SENTENCES = re.compile(r"(?<=[.!?…])\s+")


def _ad_row_keys(cells) -> set[str]:
    """Ключи заголовков и текстов, согласованных строкой этапа «ads». Строка — один заголовок или текст, но
    модель часто кладёт в неё комплект объявления: заголовки и описания в ячейках через «;», «|» или
    предложениями подряд. Согласованы и ячейка целиком, и каждая её часть, и предложения подряд."""
    out: set[str] = set()
    for cell in cells or []:
        cell = str(cell or "")
        out.add(_item_key(cell))
        for part in _AD_PARTS.split(cell):
            sentences = [s for s in _AD_SENTENCES.split(part) if s.strip()]
            for i in range(len(sentences)):
                for j in range(i + 1, min(len(sentences), i + 4) + 1):
                    out.add(_item_key(" ".join(sentences[i:j])))
    out.discard("")
    return out


def _stage_approvals(ctx: Context) -> tuple[set[str], set[str], str]:
    """Что маркетолог согласовал в этом чате: (этапы таблиц, ключи согласованных строк, его собственные слова)."""
    stages: set[str] = set()
    approved: set[str] = set()
    said: list[str] = []
    with pool.connection() as conn:
        cards = conn.execute(
            """
            SELECT tool, args, result FROM ai_actions
            WHERE chat_id = %s AND (tool = ANY(%s) AND status = 'done' OR tool = 'board_edit')
            ORDER BY id
            """,
            (ctx.chat_id, list(ASK_TOOLS)),
        ).fetchall()
        said += [
            m["content"] or ""
            for m in conn.execute(
                "SELECT content FROM ai_messages WHERE chat_id = %s AND role = 'user'", (ctx.chat_id,)
            ).fetchall()
        ]
    edits = list(getattr(ctx, "board_ops", None) or [])
    for card in cards:
        if card["tool"] == "board_edit":
            edits += (card["args"] or {}).get("ops") or []
            continue
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
                if args.get("stage") == "ads":
                    approved |= _ad_row_keys(row["cells"])
    # Что маркетолог сам вписал на доске — согласовано им.
    for op in edits:
        text = op.get("to") if op.get("op") == "edit" else op.get("text") if op.get("op") == "add" else None
        if text:
            approved.update({_item_key(text, "keyword"), _item_key(text, op.get("kind") or ""), _item_key(text)})
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
            "Этого маркетолог не согласовывал, снял галочку или убрал на доске: " + "; ".join(bad) + ". Убери это или покажи "
            "новой таблицей propose_items и добавляй только после согласования",
        )


def _item_cards(ctx: Context) -> list[dict]:
    """Решённые таблицы и правки доски этого чата — по порядку."""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT tool, args, result FROM ai_actions
            WHERE chat_id = %s AND (tool = 'propose_items' AND status = 'done' OR tool = 'board_edit')
            ORDER BY id
            """,
            (ctx.chat_id,),
        ).fetchall()


def _audience_approvals(ctx: Context) -> list[dict]:
    """Аудитории, согласованные в этом чате: [{sign, name, group}] — по таблицам этапа «Аудитории» и
    правкам на доске."""
    return _approved_audiences(_item_cards(ctx), getattr(ctx, "board_ops", None) or [])


def _approved_audiences(cards: list[dict], board_ops: list[dict]) -> list[dict]:
    """Таблицы и правки доски по порядку (board_ops — правка, которая применяется сейчас) → [{sign, name,
    group}]. Более поздняя строка той же аудитории в той же группе заменяет прежнюю."""
    out: list[dict] = []

    def drop(name: str, group: str) -> None:
        out[:] = [a for a in out if not (a["name"] == name and a["group"] == group)]

    def edit(op: dict) -> None:
        if op.get("op") == "rename_group" and op.get("to"):
            for item in out:
                if item["group"] == op.get("group"):
                    item["group"] = op["to"]
        elif op.get("op") == "remove" and op.get("kind") == "audience":
            drop(_item_key(op.get("text")), op.get("group") or "")

    for card in cards:
        args = card["args"] or {}
        if card["tool"] == "board_edit":
            for op in args.get("ops") or []:
                edit(op)
            continue
        if args.get("stage") != "audiences":
            continue
        chosen = set((card["result"] or {}).get("approved_rows") or [])
        for row in args.get("rows") or []:
            if not isinstance(row.get("audience"), dict) or not row.get("cells"):
                continue
            name, group = _item_key(row["cells"][0]), row.get("section") or ""
            drop(name, group)
            if row.get("n") in chosen:
                out.append({"sign": audiences.audience_signature(row["audience"]), "name": name, "group": group})
    for op in board_ops:
        edit(op)
    return out


def _require_audiences(ctx: Context, items: list[tuple[str, dict]]) -> None:
    """Аудитории новой кампании — только согласованные таблицей этапа «Аудитории» и в своих группах."""
    if ctx.chat_id is None or not items:
        return
    approved = _audience_approvals(ctx)
    bad: list[str] = []
    for group, aud in items:
        same = [a for a in approved if a["sign"] == audiences.audience_signature(aud)]
        if any(a["group"] == group for a in same):
            continue
        if same:
            bad.append(f"«{aud['name']}» согласована для группы «{same[0]['group']}», а не «{group}»")
        else:
            bad.append(f"«{aud['name']}» (группа «{group}»)")
    if bad:
        raise YandexError(
            "args",
            "Этих аудиторий маркетолог не согласовывал, снял галочку или убрал на доске: " + "; ".join(bad[:5])
            + (f" и ещё {len(bad) - 5}" if len(bad) > 5 else "") + ". Бери объекты audience из согласованной таблицы stage=audiences или покажи новую таблицу",
        )


def _image_approvals(ctx: Context) -> list[dict]:
    """Картинки, согласованные в этом чате: [{hash, account_id, group}]."""
    return _approved_images(_item_cards(ctx), getattr(ctx, "board_ops", None) or [])


def _approved_images(cards: list[dict], board_ops: list[dict]) -> list[dict]:
    """Таблицы этапа «Картинки» и правки доски по порядку → [{hash, account_id, group}]. Картинку, которую
    маркетолог сам добавил на доске (загрузил, взял из кампаний, сгенерировал), он этим и согласовал."""
    out: list[dict] = []

    def drop(image_hash: str, group: str) -> None:
        out[:] = [i for i in out if not (i["hash"] == image_hash and i["group"] == group)]

    def edit(op: dict) -> None:
        if op.get("op") == "rename_group" and op.get("to"):
            for item in out:
                if item["group"] == op.get("group"):
                    item["group"] = op["to"]
        elif op.get("kind") == "image" and op.get("op") in ("add", "remove"):
            drop(op.get("text") or "", op.get("group") or "")
            if op["op"] == "add":
                image = op.get("image") if isinstance(op.get("image"), dict) else {}
                out.append({"hash": op.get("text") or "", "account_id": image.get("account_id"), "group": op.get("group") or ""})

    for card in cards:
        args = card["args"] or {}
        if card["tool"] == "board_edit":
            for op in args.get("ops") or []:
                edit(op)
            continue
        if args.get("stage") != "images":
            continue
        chosen = set((card["result"] or {}).get("approved_rows") or [])
        for row in args.get("rows") or []:
            image = row.get("image")
            if not isinstance(image, dict) or not image.get("hash"):
                continue
            group = row.get("section") or ""
            drop(image["hash"], group)
            if row.get("n") in chosen:
                out.append({"hash": image["hash"], "account_id": image.get("account_id"), "group": group})
    for op in board_ops:
        edit(op)
    return out


def _require_images(ctx: Context, items: list[tuple[str, str]]) -> None:
    """Картинки новой кампании — только согласованные (таблицей этапа «Картинки» или на доске) и в своих группах."""
    if ctx.chat_id is None or not items:
        return
    approved = _image_approvals(ctx)
    bad: list[str] = []
    for group, image_hash in items:
        groups = [i["group"] for i in approved if i["hash"] == image_hash]
        if group in groups:
            continue
        bad.append(
            f"{image_hash} согласована для группы «{groups[0]}», а не «{group}»" if groups else f"{image_hash} (группа «{group}»)"
        )
    if bad:
        raise YandexError(
            "args",
            "Этих картинок маркетолог не согласовывал, снял галочку или убрал на доске: " + "; ".join(bad[:5])
            + (f" и ещё {len(bad) - 5}" if len(bad) > 5 else "") + ". Бери хэши из согласованной таблицы "
            "stage=images и правок на доске или покажи новую таблицу",
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


def _spec_audiences(group: dict, name: str) -> list[dict]:
    """Аудитории группы новой кампании — проверенные, без повторов."""
    raw = group.get("audiences")
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise YandexError("args", f"Группа «{name}»: audiences — список аудиторий")
    if len(raw) > audiences.MAX_PER_GROUP:
        raise YandexError("args", f"Группа «{name}»: не больше {audiences.MAX_PER_GROUP} аудиторий")
    out: list[dict] = []
    for item in raw:
        aud = audiences.clean_audience(item)
        if any(audiences.audience_signature(a) == audiences.audience_signature(aud) for a in out):
            raise YandexError("args", f"Группа «{name}»: аудитория «{aud['name']}» повторяется")
        out.append(aud)
    return out


def _spec_network(ctx: Context, spec: dict) -> bool | None:
    """Показывается ли новая кампания в сетях; None — стратегию не разобрать (её проверит создание)."""
    from app import actions

    strategy = dict(spec.get("strategy") or {}) if isinstance(spec.get("strategy"), dict) else {}
    if ctx.ws.get("goal_id") and not strategy.get("goal_id"):
        strategy["goal_id"] = ctx.ws["goal_id"]
    try:
        bidding, _manual = actions.build_bidding_strategy(strategy, str(spec.get("type") or "TEXT_CAMPAIGN").upper())
    except YandexError:
        return None
    return actions.strategy_from_api(bidding)["network"] != "SERVING_OFF"


def _check_spec_money(ctx: Context, spec: dict) -> None:
    """Стратегия и ценность целей — теми же проверками, что у создания, но до карточки: ИИ исправит ошибку сразу,
    а маркетолог не согласует то, что всё равно упадёт."""
    from app import actions

    raw = spec.get("strategy")
    if raw is not None and not isinstance(raw, dict):
        raise YandexError("args", "strategy — объект {search, network, weekly_budget_rub, ...}")
    strategy = dict(raw or {})
    if ctx.ws.get("goal_id") and not strategy.get("goal_id"):
        strategy["goal_id"] = ctx.ws["goal_id"]
    actions.build_bidding_strategy(strategy, str(spec.get("type") or "TEXT_CAMPAIGN").upper())
    actions._priority_goals(spec.get("priority_goals"))


def _p_create_campaign(ctx: Context, args: dict) -> Plan:
    from app import actions

    account = ctx.require_account(_int(args, "account_id"))
    spec = args.get("spec")
    if not isinstance(spec, dict) or not str(spec.get("name") or "").strip():
        raise YandexError("args", "spec — объект кампании, нужен хотя бы name")
    _check_spec_money(ctx, spec)
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
    group_audiences: list[tuple[str, dict]] = []
    group_images: list[tuple[str, str]] = []
    bare: list[str] = []
    href_default = bool(actions._ws_link_template(ctx.ws))
    for index, group in enumerate(groups):
        name = str(group.get("name") or f"Группа {index + 1}").strip()
        if name in names:
            raise YandexError("args", f"Две группы называются «{name}» — назови их по-разному")
        names.append(name)
        phrases = []
        if group.get("keywords") not in (None, "", []):
            phrases = actions._phrases(group.get("keywords"))
            if len(phrases) > 200:
                raise YandexError("args", f"Группа «{name}»: больше 200 фраз — раздели её на несколько групп")
            content["keyword"] += phrases
        mine = _spec_audiences(group, name)
        group_audiences += [(name, aud) for aud in mine]
        if mine and not phrases:
            bare.append(name)
        variants = _group_ad(ctx, group.get("ad"), name, int(account["id"]))
        if variants is None:
            continue
        extras = group["ad"].get("extras")
        if isinstance(extras, dict) and isinstance(extras.get("image_hashes"), list):
            group_images += [(name, str(h).strip()) for h in extras["image_hashes"] if str(h or "").strip()]
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
    if group_audiences and _spec_network(ctx, spec) is False:
        interests = [f"«{a['name']}»" for _, a in group_audiences if a["type"] == "interests"]
        if interests:
            raise YandexError(
                "args", f"Интересы работают только в сетях, а сети в стратегии выключены: {_preview(interests, 4)} — "
                "включи сети или убери интересы",
            )
        if bare:
            raise YandexError(
                "args", f"Группы без фраз показываются только в сетях по аудитории, а сети выключены: "
                f"{_preview([f'«{n}»' for n in bare], 4)} — включи сети или добавь фразы",
            )
    if groups:
        stages = [
            "goals", "analysis", *(["keywords"] if content["keyword"] else []),
            *(["audiences"] if group_audiences else []), *(["ads"] if ads else []), "settings",
        ]
        _require_approved(ctx, {k: v for k, v in content.items() if v}, stages=stages)
        _require_audiences(ctx, group_audiences)
        _require_images(ctx, group_images)

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
            + (f", {_count(len(group_audiences), 'аудитория', 'аудитории', 'аудиторий')}" if group_audiences else "")
            + (f", {_count(len(group_images), 'картинка', 'картинки', 'картинок')}" if group_images else "")
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
    warnings = _bring_images(ctx, groups, account_id)
    out = create_campaign(ctx.user_id, account_id=account_id, spec=spec, workspace_id=ctx.ws_id)
    if warnings:
        out["warnings"] = warnings + list(out.get("warnings") or [])
    return out


def _bring_images(ctx: Context, groups: list[dict], account_id: int) -> list[str]:
    """Картинки групп — в библиотеку кабинета новой кампании: взятые из другого кабинета пространства
    копируются, хэши заменяются; каких нет нигде — убираются. → предупреждения."""
    extras_list = []
    for group in groups:
        ad = group.get("ad")
        if isinstance(ad, dict) and isinstance(ad.get("extras"), dict):
            extras_list.append(ad["extras"])
    return _bring_extras_images(ctx, extras_list, account_id, "объявления выйдут без них")


def _image_homes(ctx: Context, hashes: set[str]) -> dict[str, set[int]]:
    """В каких кабинетах пространства картинка стоит в объявлениях: хэш → {account_id}."""
    if not hashes:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT h.value AS hash, ad.account_id
            FROM direct_ads ad
            JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            CROSS JOIN LATERAL jsonb_array_elements_text(ad.image_hashes) AS h(value)
            WHERE ad.user_id = %s AND g.campaign_row_id = ANY(%s) AND h.value = ANY(%s)
            """,
            (ctx.user_id, workspace_campaign_row_ids(ctx.user_id, ctx.ws_id), sorted(hashes)),
        ).fetchall()
    homes: dict[str, set[int]] = {}
    for row in rows:
        homes.setdefault(row["hash"], set()).add(int(row["account_id"]))
    return homes


def _bring_extras_images(ctx: Context, extras_list: list[dict], account_id: int, lost_note: str) -> list[str]:
    """image_hashes в extras — в библиотеку кабинета account_id: каких там нет, копируются из другого кабинета
    пространства (согласованные в чате или стоящие в его объявлениях), хэши заменяются на новые; каких нет
    нигде — убираются (extras без image_hashes картинок объявления не трогает). → предупреждения."""
    from app import ad_extras
    from app.actions import _copy_images

    lists = [e for e in extras_list if isinstance(e, dict) and isinstance(e.get("image_hashes"), list) and e["image_hashes"]]
    wanted = {str(h) for extras in lists for h in extras["image_hashes"]}
    if not wanted:
        return []
    token, login = audiences.account_access(ctx.user_id, account_id)
    try:
        here = set(ad_extras.image_previews(token, login, sorted(wanted)))
    except YandexError:
        return []  # библиотека не ответила — картинки проверит Директ при сохранении объявления
    missing = wanted - here
    if not missing:
        return []
    sources: dict[int, set[str]] = {}

    def source(src, image_hash: str) -> None:
        try:
            src = int(src)
        except (TypeError, ValueError):
            return
        if image_hash in missing and src != account_id and src in ctx.account_ids():
            sources.setdefault(src, set()).add(image_hash)

    if ctx.chat_id is not None:
        for item in _image_approvals(ctx):
            source(item.get("account_id"), item["hash"])
    # Картинку могли взять из объявления другого кабинета, не согласовывая в чате таблицей.
    unknown = missing - {h for hashes in sources.values() for h in hashes}
    for image_hash, accounts in _image_homes(ctx, unknown).items():
        for src in sorted(accounts):
            source(src, image_hash)
    warnings: list[str] = []
    mapping: dict[str, str] = {}
    for src, hashes in sources.items():
        if hashes <= set(mapping):
            continue
        src_token, src_login = audiences.account_access(ctx.user_id, src)
        mapping.update(_copy_images(src_token, src_login, token, login, hashes - set(mapping), warnings))
    lost = missing - set(mapping)
    for extras in lists:
        extras["image_hashes"] = [mapping.get(str(h), h) for h in extras["image_hashes"] if str(h) not in lost]
        if not extras["image_hashes"]:
            extras.pop("image_hashes")
    orphans = lost - {h for hashes in sources.values() for h in hashes}
    if orphans:
        warnings.append(
            f"Нет в библиотеке кабинета, {lost_note}: " + _count(len(orphans), "картинка", "картинки", "картинок")
        )
    return warnings


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
    extras = _ad_extras_args(ctx, args, ad_type, group["account_id"], fetch=True)
    warnings = _bring_extras_images(ctx, [extras] if extras else [], int(group["account_id"]), "объявление выйдет без них")
    out = create_text_ad(
        ctx.user_id,
        adgroup_row_id=int(group["id"]),
        title2=str(args.get("title2") or ""),
        href=str(args["href"]),
        titles=titles,
        texts=texts,
        extras=extras,
    )
    return {**out, "warnings": warnings} if warnings else out


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
    extras = _ad_extras_args(ctx, args, row["type"] or "TEXT_AD", row["account_id"], fetch=True)
    warnings = _bring_extras_images(ctx, [extras] if extras else [], int(row["account_id"]), "картинки объявления не менялись")
    out = update_text_ad(
        ctx.user_id,
        int(row["id"]),
        **{k: str(args[k]) for k in ("title", "title2", "text", "href") if args.get(k) is not None},
        titles=_variants(args, "titles"),
        texts=_variants(args, "texts"),
        extras=extras,
    )
    return {**out, "warnings": warnings} if warnings else out


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
    done, failed, warnings = [], [], []
    for item, row in _ad_items(ctx, args):
        try:
            out = _x_update_ad(ctx, item)
            done.append(_ad_name(row))
            warnings += [f"{_ad_name(row)}: {w}" for w in out.get("warnings") or []]
        except YandexError as exc:
            failed.append(f"{_ad_name(row)} — {exc.message or exc.code}")
    if failed:
        raise YandexError("ads", (f"Изменены: {', '.join(done)}; " if done else "") + "не изменены: " + "; ".join(failed))
    return {"updated": len(done), "ads": done, **({"warnings": warnings} if warnings else {})}


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
    manual, weekly, flat = None, False, {}
    try:
        bidding, manual = actions.build_bidding_strategy(strategy, ctype)
        weekly = bool(actions.manual_weekly_limit(bidding))
        flat = actions.strategy_from_api(bidding)
        out.append(_ch("Стратегия", "—", _strategy_text(flat, names)))
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
        try:
            mine = _spec_audiences(group, name)
        except YandexError:
            mine = []
        reach = "без фраз — показ в сетях по аудитории" if mine and not phrases else _count(len(phrases), *_PHRASE_WORDS)
        parts = [reach, geo.region_label(regions, regions_all)]
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
        if mine:
            out.append(_ch(f"Аудитории «{name}»", "нет", _audiences_text(mine, phrases, flat)))
        if titles and texts:
            out += [dict(r, what=f"{r['what']} «{name}»") for r in _extras_rows(ctx, ad)]
    return out


def _audiences_text(items: list[dict], phrases: list[str], flat: dict) -> str:
    """Аудитории группы для карточки создания: что это и как они меняют показы."""
    text = "; ".join(f"{a['name']} ({audiences.TYPE_LABELS[a['type']]})" for a in items)
    narrowing = [a for a in items if a["type"] != "interests"]
    if phrases and narrowing and flat.get("search") not in (None, "", "SERVING_OFF"):
        text += " · на поиске фразы группы покажутся только этой аудитории"
    if phrases and flat.get("network") not in (None, "", "SERVING_OFF"):
        text += " · в сетях — и по фразам, и по аудитории"
    if any(a["type"] == "lookalike" for a in items):
        text += " · новый LAL создастся в Яндекс Аудиториях, Директ примет его, когда сегмент будет готов (от двух часов)"
    return text


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
        for i, aud in enumerate(group.get("audiences") if isinstance(group.get("audiences"), list) else []):
            if isinstance(aud, dict) and str(aud.get("name") or "").strip():
                label = audiences.TYPE_LABELS.get(str(aud.get("type") or ""), "аудитория")
                out.append(_pick(f"{base}.audiences.{i}", aud["name"], name, "audience", label))
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
        if key == IMAGE_SOURCE_KEY:
            # Варианты — наши: генерацию нельзя выбрать, пока она не подключена (needs). Ответ — на эту кампанию.
            keys.add(key)
            questions.append(
                {
                    "n": len(questions) + 1,
                    "key": key,
                    "question": text,
                    "options": list(IMAGE_SOURCES.values()),
                    "multiple": True,
                    "remember": False,
                    "needs": dict(IMAGE_SOURCE_NEEDS),
                }
            )
            continue
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
    "audiences": ["Аудитория", "Что внутри", "Почему"],
    "ads": ["Вариант", "Комментарий"],
    "images": ["Картинка", "Откуда", "Почему"],
    "extras": ["Что", "Значение"],
    "settings": ["Настройка", "Значение", "Почему"],
    "other": ["Что", "Комментарий"],
}
_ITEM_LIMITS = {"title": 56, "text": 81}


def _audience_rows(ctx: Context, args: dict, rows: list[dict]) -> None:
    """Строки этапа «Аудитории»: проверка по целям проекта, условиям кабинета и справочнику интересов;
    «Что внутри» пишет код — по сути аудитории, а не со слов модели."""
    goals = _goal_names(ctx)
    seen: set[tuple] = set()
    for row in rows:
        aud = row["audience"]
        sign = (row["section"], audiences.audience_signature(aud))
        if sign in seen:
            raise YandexError("args", f"«{aud['name']}»: такая аудитория в группе «{row['section']}» уже есть")
        seen.add(sign)
        stray = [g for g in audiences.goal_ids(aud) if g not in goals]
        if stray:
            raise YandexError(
                "args",
                f"«{aud['name']}»: целей {', '.join(map(str, stray))} нет в проекте — цели бери из get_audiences"
                if goals else "У проекта нет целей Метрики — ретаргетинг по целям не настроить",
            )
    kinds = {r["audience"]["type"] for r in rows}
    lists: dict[int, dict] = {}
    catalog: dict[int, dict] = {}
    segments: dict[int, dict] = {}
    segments_known = False  # список сегментов Аудиторий получен — пустой тоже ответ
    if kinds & {"list", "interests", "lookalike"}:
        account_id = int(_audience_account(ctx, args)["id"])
        token, login = audiences.account_access(ctx.user_id, account_id)
        if "list" in kinds:
            try:
                lists = {i["id"]: i for i in audiences.account_lists(token, login)}
            except YandexError:
                lists = {}  # Директ не ответил — условия проверятся при создании кампании
        if "interests" in kinds:
            catalog = audiences.interest_catalog(token)
        if "lookalike" in kinds:
            # Без Аудиторий у логина новый LAL не создать — не предлагаем то, что не выйдет.
            rights = audiences.account_rights(ctx.user_id, account_id)
            if not rights["enabled"]:
                raise YandexError("args", audiences.DISABLED_TEXT)
            if not rights["rights"]:
                raise YandexError("audience-scope", audiences.rights_text(rights["login"]))
            if any(r["audience"]["type"] == "lookalike" and r["audience"]["source"]["kind"] == "segment" for r in rows):
                try:
                    segments = {int(s["id"]): s for s in audiences.list_segments(audiences.audience_token(rights))}
                    segments_known = True
                except YandexError as exc:
                    if exc.code == "audience-config":
                        raise
                    if exc.code == "audience-scope":
                        raise YandexError("audience-scope", audiences.rights_text(rights["login"])) from exc
                    segments = {}  # Аудитории не ответили — сегмент проверится при создании кампании
    names = {k: v["name"] for k, v in catalog.items()}
    segment_names = {k: str(v.get("name") or k) for k, v in segments.items()}
    for row in rows:
        aud = row["audience"]
        if aud["type"] == "list" and lists:
            item = lists.get(aud["list_id"])
            if item is None:
                raise YandexError("args", f"«{aud['name']}»: условия №{aud['list_id']} нет в кабинете — бери из get_audiences")
            if not audiences.can_target(item):
                raise YandexError("args", f"«{aud['name']}»: условие «{item['name']}» не годится для показа")
        if aud["type"] == "interests" and catalog:
            unknown = [k for k in aud["interest_ids"] if k not in catalog]
            if unknown:
                raise YandexError("args", f"«{aud['name']}»: интересов {unknown} нет в справочнике — ищи в get_audiences")
        if aud["type"] == "lookalike" and aud["source"]["kind"] == "segment" and segments_known:
            seg = segments.get(aud["source"]["id"])
            if seg is None:
                raise YandexError(
                    "args", f"«{aud['name']}»: сегмента Аудиторий №{aud['source']['id']} нет — бери из get_audiences lookalike.segments"
                )
            if seg.get("status") in audiences.SEGMENT_FAILED:
                raise YandexError("args", f"«{aud['name']}»: сегмент «{seg.get('name')}» не обработан — LAL из него не построить")
        text = audiences.summary(aud, goals=goals, interests=names, lists=lists, segments=segment_names)
        cells = row["cells"]
        if len(cells) >= 3:
            cells[1] = text
        else:
            cells.insert(1, text)
        if lists.get(aud.get("list_id"), {}).get("lal"):
            row["lal"] = True
        if aud["type"] == "lookalike":
            row["lal"] = True
            row["lookalike"] = {
                "source": audiences.source_text(aud["source"], goals=goals, segments=segment_names),
                "similarity": aud["similarity"],
            }


def _clean_image_ref(raw, name: str = "") -> dict:
    """image строки этапа «Картинки» → {hash, account_id}; account_id None — кабинет кампании."""
    if isinstance(raw, str):
        raw = {"hash": raw}
    if not isinstance(raw, dict):
        raise YandexError("args", f"«{name}»: image — объект {{hash, account_id}} из list_ad_images или generate_images")
    image_hash = str(raw.get("hash") or "").strip()
    if not _IMAGE_HASH_RE.fullmatch(image_hash):
        raise YandexError("args", f"«{name}»: image.hash — хэш картинки из list_ad_images или generate_images")
    try:
        account_id = int(raw["account_id"]) if raw.get("account_id") not in (None, "") else None
    except (TypeError, ValueError):
        raise YandexError("args", f"«{name}»: image.account_id — число") from None
    return {"hash": image_hash, "account_id": account_id}


def _image_rows(ctx: Context, args: dict, rows: list[dict]) -> int:
    """Строки этапа «Картинки»: картинка есть в библиотеке своего кабинета (превью — таблице и доске), в
    группе не больше 5 отмеченных. → кабинет кампании."""
    from app import ad_extras

    default = None
    for row in rows:
        image = row["image"]
        if image["account_id"] is None:
            if default is None:
                default = int(_audience_account(ctx, args)["id"])
            image["account_id"] = default
        ctx.require_account(image["account_id"])
    previews: dict[int, dict[str, str]] = {}
    for account_id in sorted({r["image"]["account_id"] for r in rows}):
        hashes = sorted({r["image"]["hash"] for r in rows if r["image"]["account_id"] == account_id})
        try:
            token, login = audiences.account_access(ctx.user_id, account_id)
            previews[account_id] = ad_extras.image_previews(token, login, hashes)
        except YandexError:
            continue  # Директ не ответил — картинки проверит создание кампании
        missing = [h for h in hashes if h not in previews[account_id]]
        if missing:
            raise YandexError(
                "args",
                f"Картинок {_preview(missing, 5)} нет в библиотеке кабинета {account_id} — бери хэши из "
                "list_ad_images или generate_images",
            )
    per_group: dict[str, int] = {}
    for row in rows:
        url = previews.get(row["image"]["account_id"], {}).get(row["image"]["hash"])
        if url:
            row["image"]["preview_url"] = url
        if row["checked"]:
            per_group[row["section"]] = per_group.get(row["section"], 0) + 1
    over = [f"«{g}»" for g, n in per_group.items() if n > MAX_GROUP_IMAGES]
    if over:
        raise YandexError(
            "args", f"В группе до {MAX_GROUP_IMAGES} картинок: {_preview(over, 4)} — лишние отметь checked: false"
        )
    return default if default is not None else rows[0]["image"]["account_id"]


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
    glued = False
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
        if (
            kind not in ITEM_KINDS
            or (kind == "audience") != (stage == "audiences")
            or (kind == "image") != (stage == "images")
        ):
            if stage == "ads":
                raise YandexError("args", f"«{cells[0]}»: в таблице ads у строки kind — title или text")
            kind = _STAGE_KINDS.get(stage, "other")
        section = _line(item.get("section") or item.get("group"), 120)
        image = None
        if kind == "image":
            if not section:
                raise YandexError("args", f"«{cells[0]}»: section — группа, в объявление которой картинка")
            image = _clean_image_ref(item.get("image"), cells[0])
        key = (section, kind, image["hash"] if image else _item_key(cells[0], kind))
        if key in seen:
            continue
        seen.add(key)
        audience = None
        if kind == "audience":
            if not section:
                raise YandexError(
                    "args", f"«{cells[0]}»: section — группа аудитории (для отдельной группы — её новое название)"
                )
            audience = audiences.clean_audience(item.get("audience"), name=cells[0])
            cells[0] = audience["name"]
        limit = _ITEM_LIMITS.get(kind)
        if stage == "extras" and kind == "extra":
            where = section.lower()
            limit = SITELINK_TITLE_LEN if "быстр" in where else CALLOUT_LEN if "уточнен" in where else None
        if limit and len(cells[0]) > limit:
            too_long.append(f"«{cells[0]}» ({len(cells[0])} > {limit})")
            glued = glued or (stage == "ads" and bool(re.search(r"\s\|\s|;\s", cells[0])))
        rows.append(
            {"n": len(rows) + 1, "section": section, "kind": kind, "cells": cells, "checked": _bool(item, "checked", True)}
        )
        if audience is not None:
            rows[-1]["audience"] = audience
        if image is not None:
            rows[-1]["image"] = image
    if stage == "audiences" and rows:
        _audience_rows(ctx, args, rows)
    image_account = _image_rows(ctx, args, rows) if stage == "images" and rows else None
    if too_long:
        hint = (
            ". В ячейке склеено несколько текстов: одна строка — один заголовок или текст, остальные отдельными строками"
            if glued
            else ""
        )
        raise YandexError("args", "Длиннее предела Директа, сократи: " + _preview(too_long, 5) + hint)
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
        **({"account_id": image_account} if image_account else {}),
    }


ASK_TOOLS = {"ask_questions": _q_questions, "propose_plan": _q_plan, "propose_items": _q_items}


def _kind(tool: str) -> str:
    if tool in ASK_TOOLS:
        return "ask"
    return "write" if tool in WRITE_TOOLS else "read"


TOOL_LABELS = {
    "board_edit": "Правки на доске",
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
    "get_audiences": "Аудитории и интересы",
    "get_wordstat": "Вордстат",
    "get_experiments": "Эксперименты",
    "get_headline_stats": "Статистика заголовков",
    "get_slices": "Срезы статистики",
    "check_links": "Проверка ссылок",
    "get_ad_extras": "Расширения объявления",
    "list_ad_images": "Картинки объявлений",
    "generate_images": "Генерация картинок",
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
                "description": "картинки из list_ad_images: у комбинаторного до 5, у текстового одна. Картинку из другого кабинета "
                "пространства сервис скопирует в кабинет объявления сам",
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
        "get_audiences",
        "Из чего собрать аудитории групп новой кампании: цели проекта для ретаргетинга, готовые условия кабинета "
        "(в том числе LAL — похожие на покупателей), интересы Директа («поведение и привычки») и источники нового "
        "LAL из Яндекс Аудиторий (lookalike: можно ли и свои сегменты; нет ключа — новые LAL на сервисе не "
        "подключены). С query — поиск интересов и условий по словам.",
        {
            "account_id": {**_ID, "description": "кабинет кампании; можно не давать, если кабинет один"},
            "query": {"type": "string", "description": "слова для поиска интересов и условий, напр. «путешествия»"},
        },
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
        "Картинки для объявлений. С campaign_row_id — какие уже стоят в объявлениях кампании и сколько раз, и "
        "что ещё есть в библиотеке кабинета. Без него — картинки из объявлений всех кампаний пространства (для "
        "новой кампании). Хэш — для extras.image_hashes.",
        {
            "campaign_row_id": {**_ID, "description": "кампания; без неё — все кампании пространства"},
            "account_id": {**_ID, "description": "без campaign_row_id: только этот кабинет"},
        },
    ),
    _fn(
        "generate_images",
        "Сгенерировать картинки для объявлений по описанию — через API генерации маркетолога, платно. Только если "
        f"он сам выбрал генерацию (ask_questions с key {IMAGE_SOURCE_KEY}). Картинки сразу ложатся в библиотеку "
        "кабинета; дальше покажи их таблицей propose_items stage=images.",
        {
            "prompt": {
                "type": "string",
                "description": "что на картинке: предмет, сцена, стиль, свет — под оффер группы; без надписей, "
                "логотипов и рамок",
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": image_gen.COUNT_MAX,
                "description": "сколько вариантов, по умолчанию 2",
            },
            "group": {"type": "string", "description": "группа, для которой картинки"},
            "account_id": {**_ID, "description": "кабинет кампании, если их несколько"},
        },
        ["prompt"],
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
        "что выбирают списком: направления и группы, фразы, минус-фразы, заголовки, тексты, картинки, "
        "расширения, настройки. "
        "Ответ придёт результатом вызова: approved — согласованные строки, removed — снятые; используй только "
        "согласованные. До ответа следующий этап не начинай.",
        {
            "stage": {
                "type": "string",
                "enum": list(ITEM_STAGES),
                "description": "этап: goals — цели кампании (что рекламируем, оффер, гео, бюджет, цель, посадочная), "
                "analysis — направления/группы, keywords — фразы по группам, negatives — минус-фразы, audiences — "
                "аудитории групп (ретаргетинг, LAL, интересы), ads — заголовки и тексты, images — картинки "
                "объявлений, extras — ссылка и расширения, settings — настройки",
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
                        "audience": {
                            "type": "object",
                            "description": "только в stage=audiences: {type: retargeting, goal_ids, exclude_goal_ids, "
                            "days} | {type: list, list_id} | {type: interests, interest_ids} | {type: lookalike, "
                            "source: {goal_id} | {segment_id} | {counter_id}, similarity 1–5} (только если "
                            "get_audiences вернул lookalike); cells — [название, "
                            "почему], «Что внутри» код напишет сам",
                        },
                        "image": {
                            "type": "object",
                            "description": "только в stage=images: {hash, account_id} из list_ad_images или "
                            "generate_images; section — группа, cells — [название, откуда, почему]",
                        },
                    },
                    "required": ["cells"],
                },
            },
            "account_id": {**_ID, "description": "stage=audiences и images: кабинет кампании, если их несколько"},
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
        "(TEXT_CAMPAIGN|UNIFIED_CAMPAIGN), name, start_date, end_date, strategy {search, network, параметры} — "
        "search строкой: HIGHEST_POSITION (ручные ставки) или автостратегия из get_campaign_options (например "
        "PAY_FOR_CONVERSION_MULTIPLE_GOALS), network: NETWORK_DEFAULT|SERVING_OFF, "
        "daily_budget_rub (только ручные ставки), negative_keywords [], excluded_sites [], schedule, "
        "attribution_model, priority_goals [{goal_id, value_rub — ценность цели в ₽, от 0,3}], tracking_params, "
        "groups [{name, region_ids, keywords [] (до 200), "
        "bid_rub, ad {titles (до 7), texts (до 3), href, extras {sitelinks, callouts, image_hashes — согласованные "
        "картинки группы, до 5} или extras_from_ad_row_id}, audiences [] — "
        "согласованные аудитории группы объектами audience из таблицы stage=audiences}] (до 20 групп; группа без "
        "фраз — только по аудитории, показы лишь в сетях): "
        "объявление комбинаторное, второго заголовка в нём нет; href можно не давать, если в пространстве есть "
        "шаблон ссылки. Кампания с группами создаётся только после согласованных таблиц propose_items по этапам "
        "(цели, анализ, ключи и группы, аудитории, если они есть, заголовки и тексты, настройки) и только из "
        "согласованных строк. "
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


def _lal_prompt() -> str:
    """LAL в этапе аудиторий: новый — только когда на сервере задано приложение Яндекс Аудиторий."""
    if not audiences.enabled():
        return (
            "LAL — только готовое условие кабинета с kind LAL из get_audiences: создавать новые LAL на сервисе не "
            "подключено, type lookalike не предлагай. Директ сейчас может не дать добавить LAL в группу."
        )
    return (
        "LAL — готовое условие кабинета с kind LAL или новый LAL {type: lookalike, source "
        "{goal_id | segment_id | counter_id}, similarity 1–5}, например «Похожие на тех, кто оставил заявку» от цели "
        "заявки; новый можно, только если get_audiences вернул lookalike.available, иначе передай маркетологу, что "
        "логину надо подключить Яндекс Аудитории («Аккаунты» → «Подключить Аудитории»). Предупреди, что новый LAL "
        "создастся вместе с кампанией и будет готов через несколько часов, а Директ сейчас может не дать добавить "
        "LAL в группу."
    )


def _image_gen_ready(ctx: Context) -> bool:
    return image_gen.is_configured(ctx.user_id)


def _context_files_block(ctx: Context) -> str:
    """Файлы контекста пространства для промпта; без них ход не должен падать."""
    try:
        return context_files.prompt_block(ctx.ws_id)
    except Exception:
        log.warning("Файлы контекста пространства %s не загружены", ctx.ws_id, exc_info=True)
        return ""


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
        "ошибку. Маркетолог видит рядом с чатом доску сборки — цели, группы с фразами, объявлениями и "
        "картинками, расширения — и согласует этапы на ней, поэтому называй группы (section) одинаково на всех "
        "этапах. На доске он может сам поправить фразы, заголовки, тексты и группы, убрать аудитории, добавить и "
        "убрать картинки: такие правки приходят сообщением "
        "«[Правки на доске]» и полем edited_by_user в ответе таблицы — дальше бери их в таком виде. 1) Цели кампании — сначала "
        "ask_questions, если чего-то не хватает в брифе, затем propose_items stage=goals строками «что — "
        "значение»: что рекламируем и оффер, гео, бюджет, цель (действие на сайте или цель Метрики), посадочная "
        "ссылка, ограничения. 2) Анализ: похожие кампании пространства (list_campaigns, get_campaign — какие группы, фразы и тексты "
        "приносили конверсии), get_search_queries с with_conversions, get_wordstat по 1–5 главным фразам с "
        "регионами кампании. Короткий отчёт с цифрами таблицей, затем propose_items stage=analysis — направления, "
        "они станут группами (направление, показов в месяц, откуда идея). 3) Ключи и группы — propose_items "
        "stage=keywords: фразы согласованных направлений с частотой из get_wordstat, section — группа; минус-фразы "
        "кампании — там же строками kind=negative с section «Минус-фразы» или отдельной таблицей stage=negatives. "
        "4) Аудитории — когда они помогут цели: get_audiences (цели проекта, готовые условия кабинета, "
        "интересы), затем propose_items stage=audiences: строка — аудитория, cells [название, почему], audience — "
        "объект («Что внутри» код напишет сам), section — группа. Ретаргетинг — по целям проекта: кто выполнил "
        "цель (goal_ids) и, если нужно, кто не выполнил другую (exclude_goal_ids), например «оставили заявку, но "
        "не оплатили» за 30 дней. " + _lal_prompt() + " Интересы («поведение и привычки») "
        "работают только в сетях — обычно в группы с фразами, расширяют охват в сетях. Где разместить, решай сам "
        "и объясни в «Почему»: если кампания в сетях, ретаргетинг и LAL — отдельной группой без фраз (section — "
        "её новое название, объявление для неё — на этапе заголовков); на поиске условие в группе с фразами "
        "сужает показы этих фраз до аудитории — так делай, только если это и нужно. Если сети выключены, "
        "интересы и группы без фраз не предлагай. В create_campaign аудитории — в groups[].audiences объектами "
        "audience из согласованных строк. "
        "5) Заголовки и тексты — propose_items stage=ads: каждой группе до 7 заголовков (kind=title, до 56 "
        "знаков) и до 3 текстов (kind=text, до 81) под её фразы, section — группа. Одна строка — один заголовок или текст: не склеивай несколько через «;» "
        "или «|». Несколько объявлений в группе — section «Группа / Объявление 2». 6) Картинки — сначала "
        f"ask_questions с key {IMAGE_SOURCE_KEY}: откуда брать картинки (варианты подставит сервис, можно выбрать "
        "несколько). «Взять из других кампаний» — list_ad_images без campaign_row_id, затем propose_items "
        "stage=images: строка — картинка, image {hash, account_id}, section — группа, cells [название, откуда, "
        "почему]; самих картинок ты не видишь — выбирай по кампаниям, где они стоят, маркетолог увидит превью. "
        "«Загружу свои» — попроси загрузить их кнопкой «Загрузить» в группах на доске и написать, когда готово: "
        "загруженное приходит правками на доске и уже согласовано, таблица не нужна. «Сгенерировать по "
        "описанию» — generate_images по каждой группе (сцена под её оффер, без надписей и логотипов), затем "
        "таблица stage=images. «Без картинок» — этап пропусти. В группе до 5 картинок; в create_campaign "
        "согласованные хэши — в ad.extras.image_hashes своей группы. 7) Ссылка и расширения — "
        "propose_items stage=extras: ссылка или шаблон пространства; быстрые ссылки — строка на ссылку с "
        "section «Быстрые ссылки» (текст до 30 знаков, ссылка, описание до 60); уточнения — строка на уточнение "
        "с section «Уточнения» (до 25 знаков); или у какого объявления взять расширения. В create_campaign "
        "согласованные быстрые ссылки и уточнения — в ad.extras каждой группы. 8) Настройки — propose_items stage=settings: тип, стратегия, бюджет, "
        "регионы, расписание, цель; допустимые стратегии и цели — get_campaign_options, id регионов — "
        "find_regions. 9) create_campaign — одним вызовом черновик со всеми группами (spec.groups), "
        "только из согласованных строк: снятое галочкой не бери, новое не добавляй без новой таблицы. "
        "10) На модерацию — только после отдельного согласия (moderate_campaign). Если Вордстат не подключён или "
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
    if not _image_gen_ready(ctx):
        lines.append(
            "- Генерация картинок не подключена: generate_images не вызывай; в вопросе image_source этот вариант "
            "маркетолог выбрать не сможет (подключают его в Настройки → Генерация картинок)."
        )
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
    files = _context_files_block(ctx)
    if files:
        lines += ["", files]
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


# Доска «Сборка кампании»: этапы новой кампании и всё по ним — цели, спрос, группы с фразами и
# объявлениями, расширения, настройки и проверка готовности. Собирается по порядку из таблиц
# propose_items чата (ждущая таблица — предложение со всеми строками и галочками) и правок
# маркетолога на доске (board_edit): более поздняя строка или правка заменяет прежнюю.

BUILD_STAGES = {
    "goals": "Цели",
    "analysis": "Анализ",
    "keywords": "Группы и фразы",
    "negatives": "Минус-фразы",
    "audiences": "Аудитории",
    "ads": "Объявления",
    "images": "Картинки",
    "extras": "Ссылка и расширения",
    "settings": "Настройки",
}
_BUILD_OPTIONAL = {"negatives", "audiences", "images", "extras"}
_BUILD_STARTS = {"goals", "analysis"}
_BUILD_REQUIRED = ("goals", "analysis", "keywords", "ads", "settings")
_BUILD_FACTS = {"goals": "goals", "analysis": "directions", "extras": "extras", "settings": "settings"}
NO_GROUP = "Без группы"
SITELINK_TITLE_LEN = 30
CALLOUT_LEN = 25
_LINK_RE = re.compile(r"(https?://|www\.)\S+", re.I)


def _build_item(text: str, value: str = "", note: str = "", **extra) -> dict:
    return {
        "text": text,
        "value": value,
        "note": note,
        "pending": False,
        "checked": True,
        "action_id": None,
        "n": None,
        "own": False,
        **extra,
    }


def _row_item(row: dict, action: dict, pending: bool) -> dict:
    cells = [str(c or "") for c in row.get("cells") or []]
    return _build_item(
        cells[0] if cells else "",
        cells[1] if len(cells) > 1 else "",
        " · ".join(c for c in cells[2:] if c),
        pending=pending,
        checked=row.get("checked") is not False if pending else True,
        action_id=action["id"],
        n=row.get("n"),
        own=bool(row.get("added") or row.get("edited_from") or row.get("moved_from")),
        # Вписана маркетологом в ждущую таблицу — её можно убрать; предложенное ИИ снимают галочкой.
        added=bool(row.get("added")),
    )


def _extra_links(section: str, row: dict) -> tuple[str, list[dict]]:
    """Строка этапа «Ссылка и расширения» → ("sitelink" | "callout", [{text, href, description}]); "" — обычная.

    Раздел «Быстрые ссылки» / «Уточнения» — строка на ссылку или уточнение; иначе строка «Быстрые
    ссылки — Доставка, Гарантия…» делится по запятым.
    """
    cells = [str(c or "").strip() for c in row.get("cells") or []]
    head = (cells[0] if cells else "").lower()
    where = section.lower()
    kind = "sitelink" if "быстр" in where else "callout" if "уточнен" in where else ""
    if kind:
        rest = [c for c in cells[1:] if c]
        href = next((c for c in rest if re.match(r"(https?://|www\.|/)", c, re.I)), "")
        description = next((c for c in rest if c != href), "") if kind == "sitelink" else ""
        return kind, [{"text": cells[0], "href": href, "description": description}] if cells and cells[0] else []
    kind = "sitelink" if "быстр" in head else "callout" if "уточнен" in head else ""
    if not kind or len(cells) < 2:
        return "", []
    limit = SITELINK_TITLE_LEN if kind == "sitelink" else CALLOUT_LEN
    parts = [p.strip() for p in re.split(r"[,;]", cells[1]) if p.strip()]
    if not parts or any(len(p) > limit for p in parts):
        return "", []
    return kind, [{"text": p, "href": "", "description": ""} for p in parts]


class _Board:
    """Состояние доски по ходу чата: строки таблиц и правки маркетолога применяются по порядку."""

    def __init__(self):
        self.facts: dict[str, dict] = {stage: {} for stage in _BUILD_FACTS}
        self.groups: dict[str, dict] = {}
        self.negatives: dict[str, dict] = {}
        self.links: dict[str, dict] = {"sitelink": {}, "callout": {}}
        # Частотность фраз из всех таблиц — для карточки создания, даже если фразу потом перенесли.
        self.values: dict[str, str] = {}

    def group(self, name: str) -> dict:
        name = name or NO_GROUP
        if name not in self.groups:
            self.groups[name] = {
                "name": name, "keywords": {}, "titles": {}, "texts": {}, "audiences": {}, "images": {}, "href": "",
            }
        return self.groups[name]

    def _drop_keyword(self, key: str, keep: str | None = None) -> None:
        for name, g in self.groups.items():
            if name != keep:
                g["keywords"].pop(key, None)

    def row(self, stage: str, row: dict, action: dict, pending: bool, chosen: bool = True) -> None:
        """Строка таблицы: согласованная или ждущая — на доску, снятая в решённой таблице — с доски."""
        cells = row.get("cells") or []
        text = str(cells[0] if cells else "").strip()
        if not text:
            return
        kind = row.get("kind") or ""
        section = str(row.get("section") or "").strip()
        item = _row_item(row, action, pending)
        if stage in ("keywords", "negatives"):
            if kind == "negative" or stage == "negatives":
                key = _item_key(text, "negative")
                if chosen:
                    self.negatives[key] = item
                else:
                    self.negatives.pop(key, None)
                return
            key = _item_key(text, "keyword")
            if item["value"]:
                self.values[key] = item["value"]
            if not chosen:
                self._drop_keyword(key)
                return
            name = section or NO_GROUP
            self._drop_keyword(key, keep=name)
            self.group(name)["keywords"][key] = item
        elif stage == "audiences":
            if not isinstance(row.get("audience"), dict):
                return
            key = _item_key(text)
            if chosen:
                self.group(section)["audiences"][key] = {
                    **item,
                    "type": row["audience"].get("type") or "",
                    "lal": bool(row.get("lal")),
                    "lookalike": row.get("lookalike"),
                }
            elif (section or NO_GROUP) in self.groups:
                self.groups[section or NO_GROUP]["audiences"].pop(key, None)
        elif stage == "ads":
            if kind not in ("title", "text"):
                return
            if chosen:
                self.group(section)[f"{kind}s"][_item_key(text)] = item
            elif (section or NO_GROUP) in self.groups:
                self.groups[section or NO_GROUP][f"{kind}s"].pop(_item_key(text), None)
        elif stage == "images":
            image = row.get("image")
            if not isinstance(image, dict) or not image.get("hash"):
                return
            if chosen:
                self.group(section)["images"][image["hash"]] = {
                    **item,
                    "hash": image["hash"],
                    "account_id": image.get("account_id"),
                    "preview_url": image.get("preview_url") or "",
                }
            elif (section or NO_GROUP) in self.groups:
                self.groups[section or NO_GROUP]["images"].pop(image["hash"], None)
        elif stage in self.facts:
            link_kind, links = _extra_links(section, row) if stage == "extras" else ("", [])
            target = self.links[link_kind] if link_kind else self.facts[stage]
            for link in links or [{"text": text}]:
                key = _item_key(link["text"]) if link_kind else f"{_item_key(section)}|{_item_key(text)}"
                if chosen:
                    target[key] = {**item, "value": "", "note": "", **link} if link_kind else item
                else:
                    target.pop(key, None)

    def _bucket(self, op: dict) -> dict | None:
        kind = op.get("kind")
        if kind == "negative":
            return self.negatives
        group = self.groups.get(op.get("group") or "")
        if group is None:
            return None
        return group[f"{kind}s"] if kind in ("title", "text", "audience", "image") else group["keywords"]

    def op(self, op: dict) -> None:
        """Правка с доски; чего уже нет (убрали новой таблицей) — пропускается."""
        if op["op"] == "rename_group":
            self.rename(op["group"], op["to"])
            return
        kind = op["kind"]
        bucket = self._bucket(op)
        if bucket is None:
            return
        key = op["text"] if kind == "image" else _item_key(op["text"], kind)
        if op["op"] == "add" and kind == "image":
            image = op.get("image") if isinstance(op.get("image"), dict) else {}
            bucket.setdefault(key, _build_item(
                op.get("name") or "Картинка", "добавил маркетолог", own=True, hash=key,
                account_id=image.get("account_id"), preview_url=image.get("preview_url") or "",
            ))
        elif op["op"] == "add":
            if kind == "keyword":
                self._drop_keyword(key, keep=op["group"])
            bucket.setdefault(key, _build_item(op["text"], own=True))
        elif op["op"] == "remove":
            bucket.pop(key, None)
        elif op["op"] == "edit" and key in bucket:
            new_key = _item_key(op["to"], kind)
            items = list(bucket.items())
            bucket.clear()
            for k, item in items:
                if k == key:
                    bucket[new_key] = {**item, "text": op["to"], "own": True}
                elif k != new_key:
                    bucket[k] = item
        elif op["op"] == "move" and key in bucket:
            target = self.groups.get(op.get("to_group") or "")
            if target is not None:
                target["keywords"][key] = {**bucket.pop(key), "own": True}

    def rename(self, old: str, new: str) -> None:
        source = self.groups.get(old)
        if source is None or not new or old == new:
            return
        target = self.groups.get(new)
        if target is None:
            source["name"] = new
            self.groups = {(new if name == old else name): g for name, g in self.groups.items()}
            return
        for key, item in source["keywords"].items():
            target["keywords"].setdefault(key, item)
        for key, item in source["audiences"].items():
            target["audiences"].setdefault(key, item)
        for key, item in source["images"].items():
            target["images"].setdefault(key, item)
        if not target["titles"] and not target["texts"]:
            target["titles"], target["texts"] = source["titles"], source["texts"]
        target["href"] = target["href"] or source["href"]
        del self.groups[old]


def _spec_board(
    spec: dict, values: dict[str, str], known: dict[str, dict] | None = None, known_images: dict[str, dict] | None = None
) -> tuple[list[dict], list[dict]]:
    """Группы и минус-фразы из карточки создания: это то, что уйдёт в Директ. Частотность фраз, суть
    аудиторий (known — строки таблиц по названию) и превью картинок (known_images — по хэшу) — из таблиц."""
    known = known or {}
    known_images = known_images or {}

    def group_images(raw) -> list[dict]:
        out = []
        for value in raw if isinstance(raw, list) else []:
            image_hash = str(value or "").strip()
            if not image_hash or any(i["hash"] == image_hash for i in out):
                continue
            seen = known_images.get(image_hash) or {}
            out.append(
                _build_item(
                    seen.get("text") or "Картинка",
                    seen.get("value") or "",
                    seen.get("note") or "",
                    hash=image_hash,
                    account_id=seen.get("account_id"),
                    preview_url=seen.get("preview_url") or "",
                )
            )
        return out

    def group_audiences(raw) -> list[dict]:
        out = []
        for aud in raw if isinstance(raw, list) else []:
            try:
                clean = audiences.clean_audience(aud)
            except YandexError:
                continue
            seen = known.get(_item_key(clean["name"])) or {}
            value = seen.get("value") or audiences.describe(clean)
            lookalike = seen.get("lookalike")
            if clean["type"] == "lookalike" and not lookalike:
                lookalike = {"source": audiences.source_text(clean["source"]), "similarity": clean["similarity"]}
            out.append(
                _build_item(
                    clean["name"],
                    value,
                    seen.get("note") or "",
                    type=clean["type"],
                    lal=bool(seen.get("lal")) or clean["type"] == "lookalike",
                    lookalike=lookalike,
                )
            )
        return out

    def items(raw, kind: str) -> list[dict]:
        out = []
        for v in raw if isinstance(raw, list) else []:
            text = str(v or "").strip()
            if text:
                out.append(_build_item(text, values.get(_item_key(text, kind), "") if kind == "keyword" else ""))
        return out

    groups = []
    raw_groups = spec.get("groups") if isinstance(spec.get("groups"), list) else []
    for g, raw in enumerate(raw_groups):
        if not isinstance(raw, dict):
            continue
        ad = raw.get("ad") if isinstance(raw.get("ad"), dict) else {}
        extras = ad.get("extras") if isinstance(ad.get("extras"), dict) else {}
        sitelinks = [
            {**_build_item(str(s.get("title") or "").strip()), "href": str(s.get("href") or ""),
             "description": str(s.get("description") or "")}
            for s in extras.get("sitelinks") or [] if isinstance(s, dict) and str(s.get("title") or "").strip()
        ]
        groups.append({
            "name": str(raw.get("name") or f"Группа {g + 1}").strip(),
            "keywords": items(raw.get("keywords"), "keyword"),
            "titles": items(ad.get("titles") if isinstance(ad.get("titles"), list) else [ad.get("title")], "title"),
            "texts": items(ad.get("texts") if isinstance(ad.get("texts"), list) else [ad.get("text")], "text"),
            "audiences": group_audiences(raw.get("audiences")),
            "images": group_images(extras.get("image_hashes")),
            "href": str(ad.get("href") or "").strip(),
            "sitelinks": sitelinks,
            "callouts": items(extras.get("callouts"), "callout"),
            "extras_from_ad": ad.get("extras_from_ad_row_id") not in (None, ""),
        })
    return groups, items(spec.get("negative_keywords"), "negative")


def _checked(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("checked", True)]


def _build_checks(build: dict, *, link_template: bool) -> list[dict]:
    """«Проверка готовности» перед созданием черновика: ok — всё в порядке, warn — можно, но стоит
    посмотреть, bad — Директ или проверка черновика не пропустят."""
    checks: list[dict] = []

    def add(key: str, label: str, state: str, note: str = "") -> None:
        checks.append({"key": key, "label": label, "state": state, "note": note})

    status = {s["key"]: s["status"] for s in build["stages"]}
    todo = [BUILD_STAGES[k] for k in _BUILD_REQUIRED if status.get(k) not in ("done", "pending")]
    waiting = [BUILD_STAGES[k] for k in BUILD_STAGES if status.get(k) == "pending"]
    if todo:
        add("stages", "Этапы согласованы", "bad", "не пройдены: " + ", ".join(todo))
    elif waiting:
        add("stages", "Этапы согласованы", "warn", "ждут решения: " + ", ".join(waiting))
    else:
        add("stages", "Этапы согласованы", "ok")

    groups = build["groups"]
    keywords = {g["name"]: _checked(g["keywords"]) for g in groups}
    targeted = {g["name"]: _checked(g.get("audiences") or []) for g in groups}
    if not groups:
        add("keywords", "Фразы в группах", "bad", "групп с фразами пока нет")
    else:
        # Группа без фраз, но с аудиторией — показы в сетях по аудитории, это не ошибка.
        empty = [name for name, kws in keywords.items() if not kws and not targeted[name]]
        by_audience = [name for name, kws in keywords.items() if not kws and targeted[name]]
        big = [name for name, kws in keywords.items() if len(kws) > 200]
        total = sum(len(kws) for kws in keywords.values())
        if empty:
            add("keywords", "Фразы в группах", "bad", "без фраз: " + _preview([f"«{n}»" for n in empty], 4))
        elif big:
            add("keywords", "Фразы в группах", "bad", "больше 200 фраз: " + _preview([f"«{n}»" for n in big], 4))
        else:
            note = f"{_count(len(groups), 'группа', 'группы', 'групп')}, {_count(total, *_PHRASE_WORDS)}"
            if by_audience:
                note += f"; по аудитории без фраз: {_preview([f'«{n}»' for n in by_audience], 3)}"
            add("keywords", "Фразы в группах", "ok", note)

        ads = {g["name"]: ([t["text"] for t in _checked(g["titles"])], [t["text"] for t in _checked(g["texts"])]) for g in groups}
        no_ad = [name for name, (titles, texts) in ads.items() if not titles or not texts]
        if no_ad:
            add("ads", "Объявление у каждой группы", "bad",
                "нужны заголовок и текст: " + _preview([f"«{n}»" for n in no_ad], 4))
        else:
            add("ads", "Объявление у каждой группы", "ok")
        seen: dict[tuple, str] = {}
        twins: list[str] = []
        for name, (titles, texts) in ads.items():
            if not titles or not texts:
                continue
            key = (tuple(sorted(_item_key(t) for t in titles)), tuple(sorted(_item_key(t) for t in texts)))
            if key in seen:
                twins.append(f"«{seen[key]}» и «{name}»")
            seen.setdefault(key, name)
        if twins:
            add("unique", "Объявления групп разные", "bad", "одинаковые у " + "; ".join(twins[:3]))
        elif len(seen) > 1:
            add("unique", "Объявления групп разные", "ok")

    facts = " ".join(f"{f['text']} {f['value']} {f['note']}" for f in _checked(build["goals"] + build["extras"]))
    if groups and all(g["href"] for g in groups):
        add("link", "Ссылка на сайт", "ok", "у каждого объявления")
    elif link_template:
        add("link", "Ссылка на сайт", "ok", "шаблон ссылки пространства")
    elif build["draft"] and build["draft"]["status"] in ("pending", "approved", "done"):
        add("link", "Ссылка на сайт", "bad", "не у всех объявлений есть ссылка")
    elif _LINK_RE.search(facts):
        add("link", "Ссылка на сайт", "ok", _LINK_RE.search(facts).group(0))
    else:
        add("link", "Ссылка на сайт", "warn", "посадочную ссылку ещё не указали")

    chosen = [a for g in groups for a in _checked(g.get("audiences") or [])]
    if chosen:
        note = (
            f"{_count(len(chosen), 'аудитория', 'аудитории', 'аудиторий')} в "
            f"{_count(sum(1 for items in targeted.values() if _checked(items)), 'группе', 'группах', 'группах')}"
        )
        lal = [f"«{a['text']}»" for a in chosen if a.get("lal")]
        if lal:
            add("audiences", "Аудитории", "warn", f"{note}; LAL {_preview(lal, 3)} — {audiences.LAL_NOTE}")
        else:
            add("audiences", "Аудитории", "ok", note)

    images = {g["name"]: _checked(g.get("images") or []) for g in groups}
    if groups and status.get("images") != "skipped":
        crowded = [f"«{name}»" for name, items in images.items() if len(items) > MAX_GROUP_IMAGES]
        plain = [f"«{g['name']}»" for g in groups if not images[g["name"]] and not g.get("extras_from_ad")]
        total = sum(len(items) for items in images.values())
        note = _count(total, "картинка", "картинки", "картинок")
        if crowded:
            add("images", "Картинки", "bad", f"больше {MAX_GROUP_IMAGES} в группе: " + _preview(crowded, 3))
        elif not total and plain:
            add("images", "Картинки", "warn", "картинок нет — в сетях объявление без картинки получает меньше показов")
        elif plain:
            add("images", "Картинки", "warn", f"{note}; без картинок: {_preview(plain, 3)}")
        else:
            add("images", "Картинки", "ok", note if total else "как у объявления кампании")

    negatives = _checked(build["negatives"])
    if negatives:
        add("negatives", "Минус-фразы", "ok", _count(len(negatives), "минус-фраза", "минус-фразы", "минус-фраз"))
    else:
        add("negatives", "Минус-фразы", "warn", "минус-фраз нет — будут показы по нецелевым запросам")

    sitelinks = _checked(build["sitelinks"]) or [s for g in groups for s in g.get("sitelinks") or []]
    callouts = _checked(build["callouts"]) or [c for g in groups for c in g.get("callouts") or []]
    if sitelinks or callouts or any(g.get("extras_from_ad") for g in groups):
        parts = [_count(len(sitelinks), "быстрая ссылка", "быстрые ссылки", "быстрых ссылок")] if sitelinks else []
        parts += [_count(len(callouts), "уточнение", "уточнения", "уточнений")] if callouts else []
        add("extras", "Быстрые ссылки и уточнения", "ok", ", ".join(parts) or "как у объявления кампании")
    else:
        add("extras", "Быстрые ссылки и уточнения", "warn", "без них объявление ниже и заметно хуже")
    return checks


def campaign_build(actions: list[dict], *, link_template: bool = False) -> dict | None:
    """Как собирается новая кампания в чате; None — новую кампанию здесь не собирают.

    Сборка начинается таблицей целей или анализа и заканчивается созданным черновиком: после него
    новая таблица целей или анализа начинает следующую сборку. link_template — у пространства есть
    шаблон ссылки (для проверки готовности).
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
    board = _Board()
    for action in episode:
        args = action.get("args") or {}
        if action["tool"] == "board_edit":
            for op in args.get("ops") or []:
                board.op(op)
            continue
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
            for row in args.get("rows") or []:
                board.row(stage, row, action, False, row.get("n") in chosen)
        elif state == "pending":
            for row in args.get("rows") or []:
                board.row(stage, row, action, True)

    def listed(g: dict) -> dict:
        return {
            "name": g["name"],
            "keywords": list(g["keywords"].values()),
            "titles": list(g["titles"].values()),
            "texts": list(g["texts"].values()),
            "audiences": list(g["audiences"].values()),
            "images": list(g["images"].values()),
            "href": g["href"],
            "sitelinks": None,
            "callouts": None,
            "extras_from_ad": False,
        }

    # Группа, из которой всё перенесли или убрали, с доски уходит.
    groups = [
        listed(g) for g in board.groups.values()
        if g["keywords"] or g["titles"] or g["texts"] or g["audiences"] or g["images"]
    ]
    negatives = list(board.negatives.values())

    # Карточка создания (ждёт решения, создаётся или создана) — это то, что уйдёт в Директ: группы
    # берём из неё. Не прошла в Директ — на доске снова согласованное в таблицах и правки.
    draft = None
    creates = [a for a in episode if a["tool"] == "create_campaign" and a["status"] != "rejected"]
    if creates:
        action = creates[-1]
        spec = (action.get("args") or {}).get("spec")
        spec = spec if isinstance(spec, dict) else {}
        if action["status"] in ("pending", "approved", "done"):
            known = {k: i for g in board.groups.values() for k, i in g["audiences"].items()}
            known_images = {k: i for g in board.groups.values() for k, i in g["images"].items()}
            groups, negatives = _spec_board(spec, board.values, known, known_images)
        result = action.get("result") if isinstance(action.get("result"), dict) else {}
        campaign = result.get("campaign") if isinstance(result.get("campaign"), dict) else {}
        draft = {
            "action_id": action["id"],
            "status": action["status"],
            "name": str(spec.get("name") or "").strip(),
            "summary": action.get("summary") or "",
            "error": action.get("error") or "",
            "campaign_row_id": campaign.get("id"),
        }

    # Минус-фразы часто приходят строками таблицы ключей — тогда этап закрыт вместе с ней.
    if "negatives" not in status and negatives and "keywords" in status:
        status["negatives"] = "pending" if any(n["pending"] for n in negatives) else "done"
        last_table["negatives"] = last_table["keywords"]
    # Картинки, которые маркетолог сам добавил на доске, — этап пройден и без таблицы.
    if "images" not in status and any(g.get("images") for g in groups):
        status["images"] = "done"
    facts = {out: list(board.facts[stage].values()) for stage, out in _BUILD_FACTS.items()}
    counts = {stage: len(_checked(facts[out])) for stage, out in _BUILD_FACTS.items()}
    counts["keywords"] = sum(len(_checked(g["keywords"])) for g in groups)
    counts["negatives"] = len(_checked(negatives))
    counts["ads"] = sum(len(_checked(g["titles"])) + len(_checked(g["texts"])) for g in groups)
    counts["audiences"] = sum(len(_checked(g["audiences"])) for g in groups)
    counts["images"] = sum(len(_checked(g.get("images") or [])) for g in groups)

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
    # Таблицы этапов и карточка создания этой сборки решаются на доске: в чате вместо них — плашка.
    action_ids = [
        a["id"] for a in episode
        if a["tool"] == "propose_items" and (a.get("args") or {}).get("stage") in BUILD_STAGES
    ] + ([draft["action_id"]] if draft else [])
    build = {
        "stages": stages,
        "action_ids": action_ids,
        **facts,
        "groups": groups,
        "negatives": negatives,
        "sitelinks": list(board.links["sitelink"].values()),
        "callouts": list(board.links["callout"].values()),
        "draft": draft,
        "account_id": _build_account(episode, groups),
    }
    build["checks"] = _build_checks(build, link_template=link_template)
    build["ready"] = all(c["state"] != "bad" for c in build["checks"])
    return build


def _build_account(episode: list[dict], groups: list[dict]) -> int | None:
    """Кабинет новой кампании — для картинок, которые маркетолог добавляет на доске."""
    found = None
    for action in episode:
        args = action.get("args") or {}
        if action.get("status") == "rejected":
            continue
        if action["tool"] in ("propose_items", "create_campaign") and args.get("account_id") not in (None, ""):
            try:
                found = int(args["account_id"])
            except (TypeError, ValueError):
                continue
    if found is None:
        found = next((int(i["account_id"]) for g in groups for i in g.get("images") or [] if i.get("account_id")), None)
    return found


# --- Правки маркетолога на доске ----------------------------------------------------------------
#
# Строка ждущей таблицы правится в самой таблице (маркетолог согласует уже поправленное); правка
# согласованного — записью board_edit, её модель получает заметкой «[Правки на доске]». Если карточка
# создания ждёт решения, правка меняет и её, и черновик заново проходит проверку.

BOARD_OPS = ("edit", "add", "remove", "move", "rename_group")
MAX_BOARD_OPS = 50
_BOARD_KINDS = ("keyword", "negative", "title", "text", "audience", "image")
_BOARD_MAX = {"keyword": 200, "title": 7, "text": 3, "image": MAX_GROUP_IMAGES}
_BOARD_STAGE = {
    "keyword": "keywords", "negative": "keywords", "title": "ads", "text": "ads", "audience": "audiences",
    "image": "images",
}
_BOARD_WHAT = {
    "keyword": "фразу", "negative": "минус-фразу", "title": "заголовок", "text": "текст", "audience": "аудиторию",
    "image": "картинку",
}
_BOARD_LIST = {"keyword": "фраз", "title": "заголовков", "text": "текстов", "image": "картинок"}


def _board_op(raw) -> dict:
    """Правка с доски → {op, kind, group, text, to, to_group}; ошибка — непонятная правка."""
    if not isinstance(raw, dict):
        raise YandexError("args", "Правка — объект {op, kind, group, text}")
    op = str(raw.get("op") or "").strip()
    if op not in BOARD_OPS:
        raise YandexError("args", f"op — одно из {list(BOARD_OPS)}")
    out = {"op": op, "group": _line(raw.get("group"), 120)}
    if op == "rename_group":
        out["to"] = _line(raw.get("to"), 120)
        if not out["group"] or not out["to"]:
            raise YandexError("args", "Укажите группу и новое название")
        return out
    kind = str(raw.get("kind") or "").strip()
    if kind not in _BOARD_KINDS:
        raise YandexError("args", f"kind — одно из {list(_BOARD_KINDS)}")
    if op == "move" and kind != "keyword":
        raise YandexError("args", "Переносить между группами можно только фразы")
    if kind == "audience" and op != "remove":
        raise YandexError("args", "Аудиторию на доске можно только убрать — новую предложит ИИ в чате")
    if kind == "image":
        # Картинка — по хэшу в библиотеке кабинета; name — подпись на доске и в заметке для ИИ.
        if op not in ("add", "remove"):
            raise YandexError("args", "Картинку на доске можно только добавить или убрать")
        out["kind"] = kind
        out["text"] = str(raw.get("text") or raw.get("hash") or "").strip()
        if not _IMAGE_HASH_RE.fullmatch(out["text"]):
            raise YandexError("args", "Нет хэша картинки")
        out["name"] = _line(raw.get("name"), 120) or "Картинка"
        if op == "add":
            try:
                out["image"] = {"account_id": int(raw.get("account_id"))}
            except (TypeError, ValueError):
                raise YandexError("args", "Укажите кабинет картинки") from None
        return out
    out["kind"] = kind
    if kind == "negative":
        out["group"] = ""
    out["text"] = _line(raw.get("text"), 4096)
    if not out["text"]:
        raise YandexError("args", "Пустой текст")
    if op == "edit":
        out["to"] = _line(raw.get("to"), 4096)
        if not out["to"]:
            raise YandexError("args", "Пустой текст — уберите строку, а не стирайте её")
    if op == "move":
        out["to_group"] = _line(raw.get("to_group"), 120)
    return out


def _board_text_problem(kind: str, text: str) -> str:
    limit = _ITEM_LIMITS.get(kind)
    if limit and len(text) > limit:
        return f"«{text}» длиннее {limit} знаков ({len(text)})"
    if kind in ("keyword", "negative"):
        words = [w for w in text.split() if not w.startswith("-")] if kind == "keyword" else text.split()
        if len(words) > 7:
            return f"«{text}»: больше 7 слов — Директ такую фразу не примет"
        if not _item_key(text, kind):
            return f"«{text}»: нет слов"
    return ""


def _board_note(op: dict) -> str:
    q = lambda s: f"«{s}»"  # noqa: E731
    if op["op"] == "rename_group":
        return f"объединил группу {q(op['group'])} с группой {q(op['to'])}" if op.get("merge") else (
            f"переименовал группу {q(op['group'])} в {q(op['to'])}"
        )
    if op["kind"] == "image":
        verb, where = ("добавил", "в группу") if op["op"] == "add" else ("убрал", "из группы")
        return f"{verb} картинку {q(op.get('name') or 'Картинка')} {where} {q(op['group'])}: image_hash {op['text']}"
    what = _BOARD_WHAT[op["kind"]]
    where = f" в группе {q(op['group'])}" if op["kind"] != "negative" else ""
    if op["op"] == "edit":
        return f"поправил {what}{where}: {q(op['text'])} → {q(op['to'])}"
    if op["op"] == "add":
        return f"добавил {what} {q(op['text'])}" + (f" в группу {q(op['group'])}" if op["kind"] != "negative" else "")
    if op["op"] == "remove":
        return f"убрал {what} {q(op['text'])}" + (f" из группы {q(op['group'])}" if op["kind"] != "negative" else "")
    return f"перенёс фразу {q(op['text'])} из группы {q(op['group'])} в {q(op['to_group'])}"


def _board_find(build: dict, op: dict) -> tuple[list[dict], dict | None]:
    """→ (список, где лежит строка правки; сама строка или None)."""
    if op["kind"] == "negative":
        bucket = build["negatives"]
    else:
        group = next((g for g in build["groups"] if g["name"] == op["group"]), None)
        if group is None:
            raise YandexError("args", f"Группы «{op['group']}» уже нет на доске — обновите страницу")
        bucket = group[f"{op['kind']}s"] if op["kind"] in ("title", "text", "audience", "image") else group["keywords"]
    if op["kind"] == "image":
        return bucket, next((i for i in bucket if i.get("hash") == op["text"]), None)
    key = _item_key(op["text"], op["kind"])
    return bucket, next((i for i in bucket if _item_key(i["text"], op["kind"]) == key), None)


def _board_keyword_elsewhere(build: dict, op: dict, text: str) -> None:
    """Фраза одна на кампанию: в другой группе её быть не должно."""
    if op["kind"] != "keyword":
        return
    key = _item_key(text, "keyword")
    other = next(
        (g["name"] for g in build["groups"] if g["name"] != op["group"]
         for k in g["keywords"] if _item_key(k["text"], "keyword") == key),
        None,
    )
    if other:
        raise YandexError("args", f"Фраза «{text}» уже есть в группе «{other}» — перенесите её")


def _board_validate(build: dict, op: dict) -> dict | None:
    """Правка допустима на текущей доске → строка, которую она меняет (None для add и rename_group)."""
    names = [g["name"] for g in build["groups"]]
    if op["op"] == "rename_group":
        if op["group"] not in names:
            raise YandexError("args", f"Группы «{op['group']}» уже нет на доске — обновите страницу")
        if op["to"] == op["group"]:
            raise YandexError("args", "Название не изменилось")
        op["merge"] = op["to"] in names
        return None
    kind = op["kind"]
    bucket, item = _board_find(build, op)
    if op["op"] == "add":
        problem = _board_text_problem(kind, op["text"])
        if problem:
            raise YandexError("args", problem)
        if item is not None:
            raise YandexError("args", "Эта картинка в группе уже есть" if kind == "image" else f"«{op['text']}» уже есть")
        _board_keyword_elsewhere(build, op, op["text"])
        top = _BOARD_MAX.get(kind)
        if top and len(_checked(bucket)) >= top:
            raise YandexError("args", f"В группе уже {top} {_BOARD_LIST[kind]} — больше Директ не примет")
        return None
    if item is None:
        raise YandexError("args", f"«{op['text']}» уже нет на доске — обновите страницу")
    if op["op"] == "edit":
        problem = _board_text_problem(kind, op["to"])
        if problem:
            raise YandexError("args", problem)
        if op["to"] == item["text"]:
            raise YandexError("args", "Текст не изменился")
        key = _item_key(op["to"], kind)
        if key != _item_key(op["text"], kind):
            if any(_item_key(i["text"], kind) == key for i in bucket):
                raise YandexError("args", f"«{op['to']}» уже есть")
            _board_keyword_elsewhere(build, op, op["to"])
    if op["op"] == "move":
        if op["to_group"] not in names or op["to_group"] == op["group"]:
            raise YandexError("args", "Выберите другую группу с доски")
        target = next(g for g in build["groups"] if g["name"] == op["to_group"])
        if len(_checked(target["keywords"])) >= 200:
            raise YandexError("args", f"В группе «{op['to_group']}» уже 200 фраз")
    return item


def _table_row_patch(table: dict, op: dict, item: dict | None, build: dict) -> bool:
    """Правка строки ждущей таблицы — в самой таблице. → False, если таблица тут ни при чём."""
    rows = table["args"]["rows"]
    if op["op"] == "rename_group":
        hit = False
        for row in rows:
            if (row.get("section") or "") == op["group"]:
                row["section"] = op["to"]
                hit = True
        return hit
    if op["op"] == "add":
        same = [
            r for r in rows
            if (r.get("kind") == "negative") == (op["kind"] == "negative")
            and (op["kind"] == "negative" or (r.get("section") or "") == op["group"])
            and (op["kind"] not in ("title", "text") or r.get("kind") in ("title", "text"))
        ]
        if not same:
            return False
        width = max(len(r.get("cells") or []) for r in rows) or 1
        cells = ([op["name"], "добавил маркетолог"] if op["kind"] == "image" else [op["text"]])[:width]
        rows.append({
            "n": max(int(r.get("n") or 0) for r in rows) + 1,
            "section": same[0].get("section") or "" if op["kind"] == "negative" else op["group"],
            "kind": op["kind"],
            "cells": cells + [""] * (width - len(cells)),
            "checked": True,
            "added": True,
        })
        if op["kind"] == "image":
            rows[-1]["image"] = {"hash": op["text"], **op["image"]}
        return True
    row = next((r for r in rows if r.get("n") == item["n"]), None)
    if row is None:
        raise YandexError("args", "Строки уже нет в таблице — обновите страницу")
    if op["op"] == "edit":
        if not row.get("added") and "edited_from" not in row:
            row["edited_from"] = row["cells"][0]
        row["cells"][0] = op["to"]
    elif op["op"] == "move":
        if "moved_from" not in row and not row.get("added"):
            row["moved_from"] = row.get("section") or ""
        row["section"] = op["to_group"]
    elif op["op"] == "remove":
        if not row.get("added"):
            what = f"Картинку «{op.get('name') or row['cells'][0]}»" if op.get("kind") == "image" else f"«{op['text']}»"
            raise YandexError("args", f"{what} предложил ИИ — снимите с неё галочку, и она не войдёт в этап")
        rows.remove(row)
    return True


def _spec_patch(spec: dict, op: dict) -> None:
    """Правка с доски — в карточке создания, которая ждёт решения."""
    groups = spec.get("groups") if isinstance(spec.get("groups"), list) else []

    def find(name: str) -> dict:
        for g, raw in enumerate(groups):
            if isinstance(raw, dict) and str(raw.get("name") or f"Группа {g + 1}").strip() == name:
                return raw
        raise YandexError("args", f"Группы «{name}» нет в карточке создания — обновите страницу")

    def phrases(group: dict) -> list:
        if not isinstance(group.get("keywords"), list):
            group["keywords"] = []
        return group["keywords"]

    if op["op"] == "rename_group":
        source = find(op["group"])
        if not op.get("merge"):
            source["name"] = op["to"]
            return
        target = find(op["to"])
        have = {_item_key(p, "keyword") for p in phrases(target)}
        phrases(target).extend(p for p in phrases(source) if _item_key(p, "keyword") not in have)
        if not isinstance(target.get("ad"), dict) or not target["ad"]:
            target["ad"] = source.get("ad")
        groups.remove(source)
        return
    kind = op["kind"]
    if kind == "image":
        group = find(op["group"])
        if not isinstance(group.get("ad"), dict):
            group["ad"] = {}
        if not isinstance(group["ad"].get("extras"), dict):
            group["ad"]["extras"] = {}
        extras = group["ad"]["extras"]
        hashes = [str(h) for h in extras.get("image_hashes") or []] if isinstance(extras.get("image_hashes"), list) else []
        if op["op"] == "add":
            extras["image_hashes"] = [*hashes, op["text"]]
            return
        left = [h for h in hashes if h != op["text"]]
        if len(left) == len(hashes):
            raise YandexError("args", "Этой картинки нет в карточке создания — обновите страницу")
        if left:
            extras["image_hashes"] = left
        else:
            extras.pop("image_hashes", None)
        return
    if kind == "audience":
        group = find(op["group"])
        items = group.get("audiences") if isinstance(group.get("audiences"), list) else []
        key = _item_key(op["text"])
        left = [a for a in items if not (isinstance(a, dict) and _item_key(a.get("name")) == key)]
        if len(left) == len(items):
            raise YandexError("args", f"«{op['text']}» нет в карточке создания — обновите страницу")
        group["audiences"] = left
        return
    if kind == "negative":
        if not isinstance(spec.get("negative_keywords"), list):
            spec["negative_keywords"] = []
        values = spec["negative_keywords"]
    elif kind == "keyword":
        values = phrases(find(op["group"]))
    else:
        group = find(op["group"])
        if not isinstance(group.get("ad"), dict):
            group["ad"] = {}
        ad = group["ad"]
        if not isinstance(ad.get(f"{kind}s"), list):
            ad[f"{kind}s"] = [ad[kind]] if str(ad.get(kind) or "").strip() else []
        ad.pop(kind, None)
        values = ad[f"{kind}s"]
    key = _item_key(op["text"], kind)
    index = next((i for i, v in enumerate(values) if _item_key(v, kind) == key), None)
    if op["op"] == "add":
        values.append(op["text"])
        return
    if index is None:
        raise YandexError("args", f"«{op['text']}» нет в карточке создания — обновите страницу")
    if op["op"] == "edit":
        values[index] = op["to"]
    elif op["op"] == "remove":
        del values[index]
    elif op["op"] == "move":
        phrases(find(op["to_group"])).append(values.pop(index))


def edit_board(user_id: int, chat_id: int, ops) -> dict:
    """Правки маркетолога на доске сборки: всё или ничего. → чат, как get_chat."""
    if not isinstance(ops, list) or not ops:
        raise YandexError("args", "Нет правок")
    if len(ops) > MAX_BOARD_OPS:
        raise YandexError("args", f"Не больше {MAX_BOARD_OPS} правок за раз")
    ops = [_board_op(raw) for raw in ops]
    with pool.connection() as conn:
        chat = _require_chat(conn, user_id, chat_id)
        if _chat_out(chat)["status"] == "running":
            raise YandexError("busy", "ИИ ещё отвечает — дождитесь ответа")
        actions = conn.execute("SELECT * FROM ai_actions WHERE chat_id = %s ORDER BY id", (chat_id,)).fetchall()
    ws = require_workspace(user_id, chat["workspace_id"])
    _board_images(user_id, chat["workspace_id"], ops)
    from app.actions import _ws_link_template

    link_template = bool(_ws_link_template(ws))
    work = copy.deepcopy([dict(a) for a in actions])
    original = {a["id"]: a["args"] for a in actions}
    recorded: list[dict] = []
    edits = {"id": (actions[-1]["id"] if actions else 0) + 1, "tool": "board_edit", "status": "done", "args": {"ops": recorded}}
    work.append(edits)
    patched: set[int] = set()
    for op in ops:
        build = campaign_build(work, link_template=link_template)
        if build is None:
            raise YandexError("args", "В этом чате не собирают новую кампанию")
        draft = build["draft"]
        if draft and draft["status"] in ("approved", "done"):
            raise YandexError("decided", "Черновик уже создаётся или создан — правьте кампанию в разделе кампаний")
        item = _board_validate(build, op)
        op["note"] = _board_note(op)
        by_id = {a["id"]: a for a in work}
        record = True
        if item is not None and item.get("pending"):
            _table_row_patch(by_id[item["action_id"]], op, item, build)
            patched.add(item["action_id"])
            record = False
        elif op["op"] in ("add", "rename_group"):
            for table in reversed(work):
                if table["tool"] != "propose_items" or table["status"] != "pending":
                    continue
                stage = table["args"].get("stage")
                fits = op["op"] == "rename_group" or stage == _BOARD_STAGE[op["kind"]] or (
                    op["kind"] == "negative" and stage == "negatives"
                )
                if fits and _table_row_patch(table, op, None, build):
                    patched.add(table["id"])
                    record = op["op"] == "rename_group"
                    if op["op"] == "add":
                        break
        if record and draft and draft["status"] == "pending":
            create = by_id[draft["action_id"]]
            create["args"] = dict(create["args"])
            create["args"]["spec"] = copy.deepcopy(create["args"].get("spec") or {})
            _spec_patch(create["args"]["spec"], op)
            patched.add(create["id"])
        if record:
            recorded.append(op)

    updates: list[tuple[int, dict, str | None]] = []
    for action in work:
        if action["id"] not in patched:
            continue
        args, summary = action["args"], None
        if action["tool"] == "create_campaign":
            args, summary = _replan_create(user_id, chat, action, recorded)
        updates.append((action["id"], args, summary))

    with pool.connection() as conn:
        chat = _require_chat(conn, user_id, chat_id, lock=True)
        if _chat_out(chat)["status"] == "running":
            raise YandexError("busy", "ИИ ещё отвечает — дождитесь ответа")
        for action_id, args, summary in updates:
            done = conn.execute(
                """
                UPDATE ai_actions SET args = %s, summary = COALESCE(%s, summary)
                WHERE id = %s AND status = 'pending' AND args = %s
                RETURNING id
                """,
                (Jsonb(args), summary, action_id, Jsonb(original[action_id])),
            ).fetchone()
            if done is None:
                raise YandexError("decided", "Пока вы правили, этап уже решён — обновите страницу")
        if recorded:
            anchor = conn.execute(
                "SELECT MAX(id) AS id FROM ai_messages WHERE chat_id = %s", (chat_id,)
            ).fetchone()["id"]
            last = conn.execute(
                "SELECT * FROM ai_actions WHERE chat_id = %s ORDER BY id DESC LIMIT 1", (chat_id,)
            ).fetchone()
            if last and last["tool"] == "board_edit" and last["message_id"] == anchor:
                all_ops = list((last["args"] or {}).get("ops") or []) + recorded
                conn.execute(
                    "UPDATE ai_actions SET args = %s, summary = %s, decided_at = NOW() WHERE id = %s",
                    (Jsonb({"ops": all_ops}), _board_summary(all_ops), last["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO ai_actions
                        (chat_id, message_id, user_id, tool_call_id, tool, args, summary, category, status, auto, decided_at)
                    VALUES (%s, %s, %s, '', 'board_edit', %s, %s, '', 'done', FALSE, NOW())
                    """,
                    (chat_id, anchor, user_id, Jsonb({"ops": recorded}), _board_summary(recorded)),
                )
        conn.execute("UPDATE ai_chats SET updated_at = NOW() WHERE id = %s", (chat_id,))
    return get_chat(user_id, chat_id)


def _board_images(user_id: int, workspace_id: int, ops: list[dict]) -> None:
    """Картинки, добавленные на доске: кабинет — из пространства, картинка — в его библиотеке; превью — доске."""
    from app import ad_extras

    adds = [op for op in ops if op.get("kind") == "image" and op["op"] == "add"]
    if not adds:
        return
    allowed = {int(a["id"]) for a in list_workspace_accounts(user_id, workspace_id)}
    by_account: dict[int, list[dict]] = {}
    for op in adds:
        if op["image"]["account_id"] not in allowed:
            raise YandexError("scope", "Кабинет картинки не подключён к этому пространству")
        by_account.setdefault(op["image"]["account_id"], []).append(op)
    for account_id, items in by_account.items():
        token, login = audiences.account_access(user_id, account_id)
        found = ad_extras.image_previews(token, login, sorted({op["text"] for op in items}))
        for op in items:
            if op["text"] not in found:
                raise YandexError("args", f"Картинки «{op['name']}» нет в библиотеке кабинета — загрузите её заново")
            op["image"]["preview_url"] = found[op["text"]]


def _board_summary(ops: list[dict]) -> str:
    notes = [op.get("note") or "" for op in ops]
    text = "; ".join(n for n in notes if n)
    return (text[:997] + "…") if len(text) > 1000 else text


def _replan_create(user_id: int, chat: dict, action: dict, recorded: list[dict]) -> tuple[dict, str]:
    """Карточка создания после правок с доски: заново проверка и строки карточки."""
    args = action["args"]
    clean = {k: v for k, v in args.items() if k not in ("_diff", "_items", "_removed")}
    ctx = Context(user_id, chat["workspace_id"])
    ctx.chat_id = chat["id"]
    ctx.board_ops = recorded
    try:
        plan = _p_create_campaign(ctx, clean)
    except YandexError as exc:
        raise YandexError("args", f"Правка не прошла проверку черновика: {exc.message or exc.code}") from exc
    stored = dict(clean)
    changes = _changes(ctx, "create_campaign", clean)
    if changes:
        stored["_diff"] = changes
    picks = _picks(ctx, "create_campaign", clean)
    if picks:
        stored["_items"] = picks
    if args.get("_removed"):
        stored["_removed"] = args["_removed"]
    return stored, plan.summary[:1000]


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
    from app.actions import _ws_link_template

    build = campaign_build(actions, link_template=bool(_ws_link_template(ws)))
    if build is not None:
        # Кабинеты — чтобы доска знала, куда загружать картинки, если кабинет кампании ещё не выбран.
        accounts = list_workspace_accounts(user_id, chat["workspace_id"])
        build["accounts"] = [
            {"id": int(a["id"]), "client_login": a["client_login"], "name": a.get("name") or a["client_login"]}
            for a in accounts
        ]
        if build["account_id"] is None and len(accounts) == 1:
            build["account_id"] = int(accounts[0]["id"])
    return {
        "chat": _chat_out(chat),
        "messages": [_message_out(m) for m in messages],
        "actions": [_action_out(a) for a in actions],
        "build": build,
        "access": normalize_ai_access(ws.get("ai_access")),
        "ai": {"configured": ai.get_settings(user_id)["configured"]},
        "image_gen": {"configured": image_gen.is_configured(user_id)},
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


def _compose_answers(questions: list[dict], answers, caps: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Ответы из карточки → ([{question, answer} | {question, skipped}], факты для памяти пространства).

    Ответ — выбранные варианты (только из предложенных) и свой текст через «; ». Вариант с needs выбирается,
    только если нужное подключено (caps).
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
                need = (question.get("needs") or {}).get(label)
                if need and need not in (caps or set()):
                    raise YandexError("args", f"«{label}» недоступно: {_NEED_TEXT.get(need, 'не подключено')}")
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
        questions = payload.get("questions") or []
        needs = {n for q in questions for n in (q.get("needs") or {}).values()}
        caps = {"image_gen"} if "image_gen" in needs and image_gen.is_configured(user_id) else set()
        out, facts = _compose_answers(questions, answers, caps)
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
_EDITED_NOTE = "Маркетолог сам поправил эти строки на доске — бери их в таком виде"


def _edited_rows(rows: list[dict]) -> list[str]:
    """Строки таблицы, которые маркетолог поправил на доске, — для ответа модели."""
    out: list[str] = []
    for row in rows:
        what = _ITEM_PREFIX.get(row.get("kind") or "", "фраза" if row.get("kind") == "keyword" else "строка")
        text = row["cells"][0]
        where = f" (группа «{row['section']}»)" if row.get("section") else ""
        if row.get("added"):
            out.append(f"добавил {what} «{text}»{where}")
            continue
        if row.get("edited_from"):
            out.append(f"поправил {what}{where}: «{row['edited_from']}» → «{text}»")
        if "moved_from" in row:
            out.append(
                f"перенёс {what} «{text}» из группы «{row['moved_from'] or NO_GROUP}» в «{row.get('section') or NO_GROUP}»"
            )
    return out


def _items_brief(rows: list[dict]) -> dict[str, list[str]]:
    """Строки таблицы для модели: по разделам, фразы и тексты — как есть, остальное — ячейками через «—»."""
    out: dict[str, list[str]] = {}
    for row in rows:
        if row["kind"] in ("keyword", "negative", "title", "text"):
            text = row["cells"][0]
        elif row.get("audience"):
            # Аудиторию модель переносит в create_campaign как есть — объект целиком.
            text = f"аудитория «{row['cells'][0]}» ({row['cells'][1]}): audience={json.dumps(row['audience'], ensure_ascii=False)}"
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
        edited = _edited_rows(table)
        if edited:
            reply["edited_by_user"] = edited
            reply["note"] += f". {_EDITED_NOTE}"
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
        edited = _edited_rows(table)
        if edited:
            reply["edited_by_user"] = edited
            reply["note"] += f". {_EDITED_NOTE}"
        updated, left = _close_interactive(user_id, row, status="done", result=result, error="", reply=reply)
    job = _submit(user_id, row["chat_id"], f"i{action_id}") if not left else None
    return {"action": _action_out(updated), "pending": left, "job": public_job(job) if job else None}


# --- Ход агента ---------------------------------------------------------------------------------


_BOARD_HISTORY = (
    "[Правки на доске] Маркетолог сам поправил сборку кампании на доске:\n{notes}\nДальше бери группы, фразы и "
    "тексты с этими правками; убранное не возвращай без новой причины."
)


def _shorten_result(content: str, limit: int = OLD_RESULT_CHARS) -> str:
    if len(content) <= limit:
        return content
    return f"{content[:limit]}… [сокращено: было {len(content)} символов; нужны данные — вызови инструмент снова]"


def _history(chat_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_messages WHERE chat_id = %s ORDER BY id DESC LIMIT 400", (chat_id,)
        ).fetchall()
        edits = conn.execute(
            "SELECT message_id, args FROM ai_actions WHERE chat_id = %s AND tool = 'board_edit' ORDER BY id",
            (chat_id,),
        ).fetchall()
    rows.reverse()
    # Правки с доски — сообщением после того, на котором маркетолог их сделал.
    notes = [
        (e["message_id"], [op.get("note") or "" for op in (e["args"] or {}).get("ops") or [] if op.get("note")])
        for e in edits
    ]
    notes = [(anchor, lines) for anchor, lines in notes if lines]

    def board_notes(upto: int | None) -> list[dict]:
        lines: list[str] = []
        while notes and (upto is None or notes[0][0] <= upto):
            lines += notes.pop(0)[1]
        if not lines:
            return []
        return [{"role": "user", "content": _BOARD_HISTORY.format(notes="\n".join(f"- {n}" for n in lines))}]

    start = max(0, len(rows) - HISTORY_MESSAGES)
    user_idx = [i for i, r in enumerate(rows) if r["role"] == "user"]
    after = [i for i in user_idx if i >= start]
    start = after[0] if after else (user_idx[-1] if user_idx else len(rows))
    rows = rows[start:]
    # Что было до последней реплики маркетолога — прошлый ход: чтения оттуда укорачиваем.
    last_user = max((i for i, r in enumerate(rows) if r["role"] == "user"), default=-1)

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
                if i < last_user and c["name"] in READ_TOOLS:
                    content = _shorten_result(content)
                out.append({"role": "tool", "tool_call_id": c["id"], "content": content})
            out += board_notes(rows[j - 1]["id"])
            i = j
            continue
        if r["role"] in ("user", "assistant"):
            out.append({"role": r["role"], "content": r["content"]})
        out += board_notes(r["id"])
        i += 1
    return out + board_notes(None)


def _set_status(chat_id: int, status: str, error: str = "") -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE ai_chats SET status = %s, error = %s, updated_at = NOW() WHERE id = %s",
            (status, error[:500], chat_id),
        )


def _end_with_error(chat_id: int, error: str, request: tuple[int, int] | None = None) -> dict:
    """Ход оборвался. Плашку чата следующая отправка сбросит, а строка в ленте (role «error») остаётся —
    по ней видно, что и когда не получилось. request — (токенов в запросе, сообщений в нём)."""
    _set_status(chat_id, "error", error)
    note = f"{error} (запрос ≈ {request[0]} токенов, сообщений: {request[1]})" if request else error
    with pool.connection() as conn:
        _insert_message(conn, chat_id, "error", note)
    return {"status": "error"}


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
        cfg = {**ai.load_config(user_id, "chat", chat["workspace_id"]), "timeout": CHAT_TIMEOUT_SECONDS}
        ctx = Context(user_id, chat["workspace_id"])
        ctx.chat_id = chat_id
    except YandexError as exc:
        return _end_with_error(chat_id, exc.message or exc.code)

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
            request = (ai.estimate_tokens([system, *history], TOOLS), len(history))
            try:
                reply = ai.complete(cfg, [system, *history], TOOLS)
            except YandexError as exc:
                return _end_with_error(chat_id, exc.message or exc.code, request)
            except Exception:
                log.exception("ИИ-чат %s", chat_id)
                return _end_with_error(chat_id, "Сбой при обращении к ИИ", request)
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
