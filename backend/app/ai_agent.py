"""ИИ-агент пространства: чат с инструментами (данные и действия в Директе) и согласованием.

Чтение выполняется сразу. Каждое изменение — запись ai_actions: выполняется само, если это
разрешают настройки доступа пространства, иначе ждёт решения пользователя в чате.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import ai
from app.actions import BULK_ACTIONS, campaign_options
from app.brief import brief_filled, normalize_brief
from app.clock import today_msk
from app.db import pool
from app.journal import acting_as, list_action_logs
from app.query_cleaning import examples_prompt, memory_examples
from app.workspaces import (
    AI_CATEGORIES,
    get_workspace,
    normalize_ai_access,
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
        text = text[:limit] + "… [обрезано]"
    return text


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _money(rub) -> str:
    if rub is None:
        return "—"
    return f"{float(rub):,.0f}".replace(",", " ") + " ₽"


def _names(rows: list[dict], limit: int = 3) -> str:
    names = [f"«{r.get('name') or r.get('keyword') or r.get('title') or r.get('query') or r['id']}»" for r in rows]
    text = ", ".join(names[:limit])
    return text + (f" и ещё {len(names) - limit}" if len(names) > limit else "")


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


# --- Контекст пространства --------------------------------------------------------------------


class Context:
    def __init__(self, user_id: int, workspace_id: int):
        self.user_id = user_id
        self.ws_id = workspace_id
        self.ws = get_workspace(user_id, workspace_id)
        self.access = normalize_ai_access(self.ws.get("ai_access"))

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
                       c.daily_budget_kopecks, c.account_id
                FROM direct_campaigns c
                WHERE c.user_id = %s AND c.id = ANY(%s)
                ORDER BY c.name
                """,
                (self.user_id, ids),
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
            SELECT ad.id, ad.title, g.campaign_row_id
            FROM direct_ads ad JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            WHERE ad.user_id = %s AND ad.id = ANY(%s)
            """,
            ids,
            "Объявления",
        )

    def adgroup(self, adgroup_row_id: int) -> dict:
        return self._scoped(
            "SELECT g.id, g.name, g.campaign_row_id FROM direct_adgroups g WHERE g.user_id = %s AND g.id = ANY(%s)",
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
    return out


def _t_analytics(ctx: Context, args: dict) -> dict:
    from app.analytics import workspace_analytics

    out = workspace_analytics(ctx.user_id, ctx.ws_id, **_range_args(args), with_tops=False)
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


def _t_campaign(ctx: Context, args: dict) -> dict:
    from app.adgroups import list_adgroups
    from app.ads import list_ads
    from app.direct_sync import get_campaign
    from app.keywords import list_keywords

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    keywords = list_keywords(ctx.user_id, campaign_row_id=cid)
    keywords.sort(key=lambda k: (k.get("period_cost_kopecks") or 0, k.get("period_clicks") or 0), reverse=True)
    ads = list_ads(ctx.user_id, campaign_row_id=cid)
    return {
        "campaign": get_campaign(ctx.user_id, cid),
        "adgroups": list_adgroups(ctx.user_id, cid)[:100],
        "ads": [
            {k: a.get(k) for k in ("id", "adgroup_row_id", "type", "state", "status", "title", "title2", "text", "href")}
            for a in ads[:100]
        ],
        "keywords_total": len(keywords),
        "keywords": [
            {
                k: kw.get(k)
                for k in (
                    "id", "adgroup_row_id", "adgroup_name", "keyword", "state", "status", "bid_rub",
                    "period_impressions", "period_clicks", "period_cost_kopecks", "period_conversions",
                    "period_cpa_rub", "stats_from", "stats_to",
                )
            }
            for kw in keywords[:150]
        ],
    }


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
    summary = f"{_CAMPAIGN_VERBS[action]}: {_names(rows)}"
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


_SETTINGS_LABELS = {
    "name": "название",
    "start_date": "дата начала",
    "end_date": "дата окончания",
    "daily_budget_rub": "дневной бюджет",
    "strategy": "стратегия",
    "negative_keywords": "минус-фразы (замена списка)",
    "excluded_sites": "запрещённые площадки",
    "schedule": "расписание",
    "consider_working_weekends": "рабочие выходные",
    "counter_ids": "счётчики",
    "priority_goals": "ключевые цели",
    "attribution_model": "атрибуция",
    "settings": "опции",
    "tracking_params": "параметры ссылок",
    "client_info": "информация о клиенте",
}


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
    return Plan(f"Настройки «{row['name']}»: " + ", ".join(parts), cats, change)


def _x_update_settings(ctx: Context, args: dict) -> dict:
    from app.actions import update_campaign_settings

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    return update_campaign_settings(ctx.user_id, cid, dict(args["changes"]))


def _p_add_negatives(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    phrases = _phrases(args)
    preview = ", ".join(phrases[:6]) + (f" и ещё {len(phrases) - 6}" if len(phrases) > 6 else "")
    return Plan(f"Минус-фразы кампании «{row['name']}»: +{len(phrases)} ({preview})", ["negatives"])


def _x_add_negatives(ctx: Context, args: dict) -> dict:
    """Добавляет только новые фразы; откат из журнала уберёт ровно их."""
    from app.negatives import add_negatives_logged

    cid = _int(args, "campaign_row_id")
    row = ctx.campaign(cid)
    phrases = _phrases(args)
    result = add_negatives_logged(
        ctx.user_id,
        campaigns={cid: {"add": phrases}},
        title=f"Минус-фразы кампании «{row['name']}»",
        payload={"phrases": phrases[:50]},
    )
    added = (result["campaigns"].get(cid) or {}).get("added") or []
    if not added:
        return {"added": [], "note": "все фразы уже есть"}
    return {"added": added, "skipped": [s["reason"] for s in result["skipped"]][:10]}


def _p_create_campaign(ctx: Context, args: dict) -> Plan:
    account = ctx.require_account(_int(args, "account_id"))
    spec = args.get("spec")
    if not isinstance(spec, dict) or not str(spec.get("name") or "").strip():
        raise YandexError("args", "spec — объект кампании, нужен хотя бы name")
    details = []
    strategy = spec.get("strategy") or {}
    if isinstance(strategy, dict) and strategy.get("search"):
        details.append(f"стратегия {strategy.get('search')}")
    if spec.get("daily_budget_rub"):
        details.append(f"бюджет {_money(spec['daily_budget_rub'])}/день")
    if isinstance(strategy, dict) and strategy.get("weekly_budget_rub"):
        details.append(f"бюджет {_money(strategy['weekly_budget_rub'])}/нед.")
    group = spec.get("group") if isinstance(spec.get("group"), dict) else None
    if group:
        details.append(f"группа с {len(group.get('keywords') or [])} фразами")
    extra = f" ({', '.join(details)})" if details else ""
    return Plan(
        f"Создать кампанию «{str(spec['name']).strip()}» в кабинете {account['client_login']}{extra}",
        ["campaigns"],
    )


def _x_create_campaign(ctx: Context, args: dict) -> dict:
    from app.actions import create_campaign

    account_id = _int(args, "account_id")
    ctx.require_account(account_id)
    return create_campaign(ctx.user_id, account_id=account_id, spec=dict(args["spec"]), workspace_id=ctx.ws_id)


def _p_duplicate(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    target = args.get("target_account_id")
    where = ""
    if target not in (None, ""):
        account = ctx.require_account(_int(args, "target_account_id"))
        where = f" в кабинет {account['client_login']}"
    name = str(args.get("name") or "").strip()
    return Plan(f"Копия кампании «{row['name']}»{where}" + (f" под именем «{name}»" if name else ""), ["campaigns"])


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
    return Plan(f"Отправить на модерацию «{row['name']}»", ["campaigns"])


def _x_moderate(ctx: Context, args: dict) -> dict:
    from app.actions import moderate_campaign

    cid = _int(args, "campaign_row_id")
    ctx.campaign(cid)
    return moderate_campaign(ctx.user_id, cid)


def _p_create_adgroup(ctx: Context, args: dict) -> Plan:
    row = ctx.campaign(_int(args, "campaign_row_id"))
    name = str(args.get("name") or "").strip()
    if not name:
        raise YandexError("args", "Укажите name группы")
    return Plan(f"Новая группа «{name}» в кампании «{row['name']}»", ["campaigns"])


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


def _p_add_keywords(ctx: Context, args: dict) -> Plan:
    group = ctx.adgroup(_int(args, "adgroup_row_id"))
    phrases = _phrases(args)
    preview = ", ".join(phrases[:5]) + (f" и ещё {len(phrases) - 5}" if len(phrases) > 5 else "")
    bid = _float(args, "bid_rub")
    return Plan(
        f"Добавить {len(phrases)} фраз в группу «{group['name']}»: {preview}" + (f" (ставка {bid:g} ₽)" if bid else ""),
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


def _p_create_ad(ctx: Context, args: dict) -> Plan:
    group = ctx.adgroup(_int(args, "adgroup_row_id"))
    for key in ("title", "text", "href"):
        if not str(args.get(key) or "").strip():
            raise YandexError("args", f"Укажите {key}")
    return Plan(f"Объявление в группу «{group['name']}»: «{str(args['title']).strip()}» — {str(args['text']).strip()}", ["ads"])


def _x_create_ad(ctx: Context, args: dict) -> dict:
    from app.ads import create_text_ad

    group_id = _int(args, "adgroup_row_id")
    ctx.adgroup(group_id)
    return create_text_ad(
        ctx.user_id,
        adgroup_row_id=group_id,
        title=str(args["title"]),
        title2=str(args.get("title2") or ""),
        text=str(args["text"]),
        href=str(args["href"]),
    )


def _p_update_ad(ctx: Context, args: dict) -> Plan:
    row = ctx.ads([_int(args, "ad_row_id")])[0]
    fields = [k for k in ("title", "title2", "text", "href") if args.get(k) is not None]
    if not fields:
        raise YandexError("args", "Укажите, что поменять: title, title2, text или href")
    return Plan(f"Правка объявления «{row['title']}»: {', '.join(fields)}", ["ads"])


def _x_update_ad(ctx: Context, args: dict) -> dict:
    from app.ads import update_text_ad

    ad_id = _int(args, "ad_row_id")
    ctx.ads([ad_id])
    return update_text_ad(
        ctx.user_id,
        ad_id,
        **{k: str(args[k]) for k in ("title", "title2", "text", "href") if args.get(k) is not None},
    )


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


WRITE_TOOLS = {
    "campaigns_action": (_p_campaigns_action, _x_campaigns_action),
    "update_campaign_settings": (_p_update_settings, _x_update_settings),
    "add_campaign_negatives": (_p_add_negatives, _x_add_negatives),
    "create_campaign": (_p_create_campaign, _x_create_campaign),
    "duplicate_campaign": (_p_duplicate, _x_duplicate),
    "moderate_campaign": (_p_moderate, _x_moderate),
    "create_adgroup": (_p_create_adgroup, _x_create_adgroup),
    "keywords_action": (_p_keywords_action, _x_keywords_action),
    "add_keywords": (_p_add_keywords, _x_add_keywords),
    "search_queries_action": (_p_queries_action, _x_queries_action),
    "create_text_ad": (_p_create_ad, _x_create_ad),
    "update_text_ad": (_p_update_ad, _x_update_ad),
    "ads_action": (_p_ads_action, _x_ads_action),
}

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
    "campaigns_action": "Действие с кампаниями",
    "update_campaign_settings": "Настройки кампании",
    "add_campaign_negatives": "Минус-фразы кампании",
    "create_campaign": "Новая кампания",
    "duplicate_campaign": "Копия кампании",
    "moderate_campaign": "Модерация",
    "create_adgroup": "Новая группа",
    "keywords_action": "Ключевые фразы",
    "add_keywords": "Новые ключевые фразы",
    "search_queries_action": "Поисковые запросы",
    "create_text_ad": "Новое объявление",
    "update_text_ad": "Правка объявления",
    "ads_action": "Объявления",
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
_REASON = {"reason": {"type": "string", "description": "Коротко для пользователя: зачем это действие"}}
_PHRASES = {"type": "array", "items": {"type": "string"}}

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
        "Структура кампании: группы, объявления, ключевые фразы со ставками и статистикой.",
        {"campaign_row_id": _ID},
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
        "attribution_model, priority_goals, tracking_params, group {name, region_ids, keywords [], bid_rub, "
        "ad {title, title2, text, href}}. Счётчик пространства подставится сам.",
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
        "create_adgroup",
        "Создать группу объявлений в кампании.",
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
        "Текстовое объявление в группе: title до 56 символов, title2 до 30, text до 81, href.",
        {
            "adgroup_row_id": _ID,
            "title": {"type": "string"},
            "title2": {"type": "string"},
            "text": {"type": "string"},
            "href": {"type": "string"},
            **_REASON,
        },
        ["adgroup_row_id", "title", "text", "href"],
    ),
    _fn(
        "update_text_ad",
        "Изменить текстовое объявление (только переданные поля).",
        {
            "ad_row_id": _ID,
            "title": {"type": "string"},
            "title2": {"type": "string"},
            "text": {"type": "string"},
            "href": {"type": "string"},
            **_REASON,
        },
        ["ad_row_id"],
    ),
    _fn(
        "ads_action",
        "Остановить, запустить, архивировать или удалить объявления.",
        {"ad_row_ids": _IDS, "action": {"type": "string", "enum": list(_AD_ACTIONS)}, **_REASON},
        ["ad_row_ids", "action"],
    ),
]


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
        "- Для анализа сначала собери данные инструментами, затем дай выводы и конкретные рекомендации списком.",
        "- Суммы в инструментах — в рублях. Кампании называй по имени; id нужны только для вызова инструментов.",
        "- Меняй что-либо только инструментами действий и только когда пользователь просит действовать. "
        "Не спрашивай подтверждения текстом: согласование покажет система. В reason коротко объясни зачем.",
        "- Если действие отклонено, не повторяй его без новой просьбы. Если ошибка — объясни её пользователю.",
        "- Работай только с кампаниями и кабинетами этого пространства.",
    ]
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
    if row["role"] == "tool" and len(content) > 1500:
        content = content[:1500] + "…"
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
            }
            for c in calls
        ],
        "tool_call_id": row.get("tool_call_id") or "",
        "tool_name": row.get("tool_name") or "",
        "created_at": _iso(row.get("created_at")),
    }


def _action_out(row: dict) -> dict:
    args = row.get("args") or {}
    cats = [c for c in (row.get("category") or "").split(",") if c]
    return {
        "id": row["id"],
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
        "tool_call_id": row["tool_call_id"],
        "tool": row["tool"],
        "label": TOOL_LABELS.get(row["tool"], row["tool"]),
        "args": args,
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


def _skip_open_actions(conn, chat_id: int, reason: str) -> None:
    """Незакрытые действия закрываются ответом инструмента — иначе история для модели некорректна."""
    rows = conn.execute(
        """
        UPDATE ai_actions SET status = 'rejected', error = %s, decided_at = NOW()
        WHERE chat_id = %s AND status IN ('pending', 'approved')
        RETURNING tool_call_id, tool
        """,
        (reason, chat_id),
    ).fetchall()
    for r in rows:
        _insert_message(
            conn,
            chat_id,
            "tool",
            json.dumps({"ok": False, "error": reason}, ensure_ascii=False),
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
        _skip_open_actions(conn, chat_id, "Пропущено: пользователь написал новое сообщение")
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


def _decide(user_id: int, chat_id: int, action_ids: list[int] | None, approve: bool) -> tuple[list[dict], int]:
    with pool.connection() as conn:
        _require_chat(conn, user_id, chat_id, lock=True)
        params: list = [chat_id]
        where = "chat_id = %s AND status = 'pending'"
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


def decide_action(user_id: int, action_id: int, *, approve: bool, remember: bool = False) -> dict:
    """Решение по одному действию; remember — разрешить его категории без согласования впредь."""
    from app.jobs import public_job

    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM ai_actions WHERE id = %s AND user_id = %s", (action_id, user_id)).fetchone()
    if row is None:
        raise YandexError("not-found")
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


def decide_all(user_id: int, chat_id: int, *, approve: bool) -> dict:
    from app.jobs import public_job

    rows, left = _decide(user_id, chat_id, None, approve)
    if not rows:
        raise YandexError("decided", "Нет действий, ждущих решения")
    job = _submit(user_id, chat_id, f"all{rows[-1]['id']}") if not left else None
    return {"decided": len(rows), "pending": left, "job": public_job(job) if job else None}


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
    result = None
    try:
        result = execute(ctx, action["args"] or {})
        status, error = "done", ""
        payload = {"ok": True, "result": result}
    except YandexError as exc:
        status, error = "error", exc.message or exc.code
        payload = {"ok": False, "error": error}
    except Exception:
        log.exception("ИИ-действие %s", action["id"])
        status, error = "error", "Внутренняя ошибка при выполнении"
        payload = {"ok": False, "error": error}
    with pool.connection() as conn:
        conn.execute(
            "UPDATE ai_actions SET status = %s, result = %s, error = %s WHERE id = %s",
            (status, Jsonb(_jsonable(_for_ai(result))) if result is not None else None, error, action["id"]),
        )
        _insert_message(
            conn, action["chat_id"], "tool", _dump(payload), tool_call_id=action["tool_call_id"], tool_name=action["tool"]
        )


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

    if name in READ_TOOLS:
        try:
            return reply(READ_TOOLS[name](ctx, args))
        except YandexError as exc:
            return reply({"ok": False, "error": exc.message or exc.code})
        except Exception:
            log.exception("ИИ-инструмент %s", name)
            return reply({"ok": False, "error": "Внутренняя ошибка инструмента"})

    if name not in WRITE_TOOLS:
        return reply({"ok": False, "error": f"Нет инструмента {name}"})
    prepare, _execute = WRITE_TOOLS[name]
    try:
        plan = prepare(ctx, args)
    except YandexError as exc:
        return reply({"ok": False, "error": exc.message or exc.code})
    auto = _allowed(ctx.access, plan.categories, plan.change_pct)
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
                Jsonb(args),
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
