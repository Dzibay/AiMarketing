"""Действия над кампаниями Директа: создание, копия, настройки, массовые операции и журнал."""

from __future__ import annotations

import base64
import json
import logging
from collections import defaultdict
from datetime import date
from urllib.parse import urlsplit

import httpx

from app import direct_api
from app.db import pool
from app.direct_sync import (
    _money_to_kopecks,
    get_campaign_rows,
    insert_campaign_local,
    remove_campaign_local,
    update_campaign_local_state,
)
from app.journal import ACTION_TITLES, _workspace_for, journal_quiet, log_action
from app.strategy_info import priority_goals_from_api
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.actions")

BULK_ACTIONS = {"pause", "resume", "set_budget", "archive", "unarchive", "delete"}
BULK_LIMIT = 500
UPDATE_BATCH = 10  # campaigns.update — не более 10 кампаний за вызов
ADD_BATCH = 1000  # adgroups/keywords/ads.add — до 1000 объектов за вызов

# --- Доступ к API ------------------------------------------------------------------------------


def _call_campaigns(access_token: str, client_login: str, method: str, params: dict) -> dict:
    return direct_api.call(access_token, client_login, "campaigns", method, params)


def _account_creds(user_id: int, account_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, connection_id, client_login, enabled
            FROM direct_accounts
            WHERE id = %s AND user_id = %s
            """,
            (account_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def _campaign_full(user_id: int, campaign_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.campaign_type, c.state, c.status,
                   c.daily_budget_kopecks, c.strategy, a.connection_id, a.client_login, a.enabled
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.id = %s AND c.user_id = %s
            """,
            (campaign_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row["enabled"]:
        raise YandexError("disabled")
    return row


def _get_all(token, client_login: str, service: str, params: dict, key: str) -> list[dict]:
    """get с постраничной выборкой (LimitedBy)."""
    out: list[dict] = []
    offset = 0
    while True:
        result = direct_api.call(token, client_login, service, "get", {**params, "Page": {"Limit": 10_000, "Offset": offset}})
        out.extend(result.get(key) or [])
        if result.get("LimitedBy") is None:
            return out
        offset = int(result["LimitedBy"])


def _added_id(result: dict, key: str = "AddResults") -> int:
    errors = direct_api.item_errors(result, key)
    if errors:
        raise YandexError("direct", errors[0][1])
    items = result.get(key) or []
    if not items or not items[0].get("Id"):
        raise YandexError("direct")
    return int(items[0]["Id"])


def _clean(value):
    """Директ не принимает null во вложенных структурах add — выбрасываем пустое."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value if v is not None]
    return value


def _micros(rub) -> int:
    return int(round(float(rub) * 1_000_000))


def _rub(micros) -> float | None:
    kopecks = _money_to_kopecks(micros)
    return None if kopecks is None else kopecks / 100


# --- Справочники настроек ----------------------------------------------------------------------

CAMPAIGN_TYPES = {"TEXT_CAMPAIGN": "Текстово-графическая", "UNIFIED_CAMPAIGN": "Единая перфоманс-кампания (ЕПК)"}
_TYPE_BLOCK = {"TEXT_CAMPAIGN": "TextCampaign", "UNIFIED_CAMPAIGN": "UnifiedCampaign"}

STRATEGY_PARAMS = {
    "weekly_budget_rub": "Недельный бюджет, ₽",
    "bid_ceiling_rub": "Максимальная ставка, ₽",
    "average_cpc_rub": "Средняя цена клика, ₽",
    "average_cpa_rub": "Цена конверсии, ₽",
    "goal_id": "Цель Метрики",
}

# Автостратегии: подпись, структура в API, {параметр: (поле API, обязателен)}.
AUTO_STRATEGIES = {
    "WB_MAXIMUM_CLICKS": (
        "Максимум кликов",
        "WbMaximumClicks",
        {"weekly_budget_rub": ("WeeklySpendLimit", True), "bid_ceiling_rub": ("BidCeiling", False)},
    ),
    "WB_MAXIMUM_CONVERSION_RATE": (
        "Максимум конверсий",
        "WbMaximumConversionRate",
        {
            "weekly_budget_rub": ("WeeklySpendLimit", True),
            "goal_id": ("GoalId", True),
            "bid_ceiling_rub": ("BidCeiling", False),
        },
    ),
    "AVERAGE_CPC": (
        "Средняя цена клика",
        "AverageCpc",
        {"average_cpc_rub": ("AverageCpc", True), "weekly_budget_rub": ("WeeklySpendLimit", False)},
    ),
    "AVERAGE_CPA": (
        "Средняя цена конверсии",
        "AverageCpa",
        {
            "average_cpa_rub": ("AverageCpa", True),
            "goal_id": ("GoalId", True),
            "weekly_budget_rub": ("WeeklySpendLimit", False),
            "bid_ceiling_rub": ("BidCeiling", False),
        },
    ),
    "PAY_FOR_CONVERSION": (
        "Оплата за конверсии",
        "PayForConversion",
        {
            "average_cpa_rub": ("Cpa", True),
            "goal_id": ("GoalId", True),
            "weekly_budget_rub": ("WeeklySpendLimit", False),
        },
    ),
}
MANUAL_LABELS = {
    "HIGHEST_POSITION": "Ручные ставки",
    "MAXIMUM_COVERAGE": "Ручные ставки",
    "SERVING_OFF": "Не показывать",
    "NETWORK_DEFAULT": "Как на поиске",
}

CAMPAIGN_SETTINGS = {
    "ADD_METRICA_TAG": ("Размечать ссылки для Метрики (yclid)", True),
    "ENABLE_SITE_MONITORING": ("Останавливать показы, если сайт недоступен", True),
}

ATTRIBUTION_MODELS = {
    "AUTO": "Автоматическая",
    "LYDC": "Последний переход из Директа",
    "LSC": "Последний значимый переход",
    "FC": "Первый переход",
    "LC": "Последний переход",
    "LYDCCD": "Последний переход из Директа (кросс-девайс)",
    "LSCCD": "Последний значимый переход (кросс-девайс)",
    "FCCD": "Первый переход (кросс-девайс)",
}
# С 21.04.2026 LSC, FC, LYDC и LYDCCD выбрать нельзя: Директ переводит их в ближайшие действующие.
ATTRIBUTION_SELECTABLE = ("AUTO", "LC", "LSCCD", "FCCD")
_ATTRIBUTION_REPLACED = {"LSC": "LSCCD", "FC": "FCCD", "LYDC": "AUTO", "LYDCCD": "AUTO"}


def attribution_for_api(value) -> str | None:
    """Модель атрибуции из формы, ИИ или исходной кампании → действующая в Директе (None — неизвестная)."""
    model = str(value or "").strip().upper()
    model = _ATTRIBUTION_REPLACED.get(model, model)
    return model if model in ATTRIBUTION_SELECTABLE else None

SCHEDULE_PRESETS = {
    "always": "Круглосуточно, без выходных",
    "workdays": "Только будни, круглосуточно",
    "workhours": "Будни, 9:00–19:00",
    "daytime": "Каждый день, 8:00–23:00",
}


def campaign_options() -> dict:
    """Справочник для формы создания/настроек и для ИИ-агента."""

    def params(kind: str) -> list[dict]:
        return [
            {"key": key, "label": STRATEGY_PARAMS[key], "required": required}
            for key, (_field, required) in AUTO_STRATEGIES[kind][2].items()
        ]

    auto = [{"id": k, "label": v[0], "manual": False, "params": params(k)} for k, v in AUTO_STRATEGIES.items()]
    manual = {"id": "HIGHEST_POSITION", "label": "Ручные ставки", "manual": True, "params": []}
    off = {"id": "SERVING_OFF", "label": "Не показывать на поиске", "manual": False, "params": []}
    no_network = {"id": "SERVING_OFF", "label": "Не показывать в сетях"}
    network_auto = [{"id": "NETWORK_DEFAULT", "label": "Показывать в сетях по стратегии поиска"}, no_network]
    text = {
        "search_strategies": [manual, *auto, off],
        "network_by_search": {
            "HIGHEST_POSITION": [no_network, {"id": "MAXIMUM_COVERAGE", "label": "Ручные ставки в сетях"}],
            "AUTO": network_auto,
            "SERVING_OFF": [
                {"id": "MAXIMUM_COVERAGE", "label": "Ручные ставки в сетях", "manual": True, "params": []},
                *auto,
            ],
        },
    }
    # В ЕПК ручных ставок в сетях нет, а у ручных ставок на поиске бюджет недельный (с 29.06.2026).
    weekly = {"key": "weekly_budget_rub", "label": STRATEGY_PARAMS["weekly_budget_rub"], "required": False}
    unified = {
        "search_strategies": [{**manual, "params": [weekly]}, *auto, off],
        "network_by_search": {"HIGHEST_POSITION": [no_network], "AUTO": network_auto, "SERVING_OFF": auto},
    }
    return {
        "types": [{"id": k, "label": v} for k, v in CAMPAIGN_TYPES.items()],
        **text,
        "by_type": {"TEXT_CAMPAIGN": text, "UNIFIED_CAMPAIGN": unified},
        "settings": [{"id": k, "label": v[0], "default": v[1]} for k, v in CAMPAIGN_SETTINGS.items()],
        "attribution_models": [{"id": k, "label": ATTRIBUTION_MODELS[k]} for k in ATTRIBUTION_SELECTABLE],
        "schedule_presets": [{"id": k, "label": v} for k, v in SCHEDULE_PRESETS.items()],
        "min_daily_budget_rub": 300,
        "min_weekly_budget_rub": 300,
    }


def _auto_block(kind: str, strategy: dict) -> dict:
    label, struct, fields = AUTO_STRATEGIES[kind]
    block: dict = {}
    for key, (field, required) in fields.items():
        value = strategy.get(key)
        if value in (None, "", 0, "0"):
            if required:
                raise YandexError("strategy", f"{label}: заполните «{STRATEGY_PARAMS[key]}»")
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise YandexError("strategy", f"{label}: «{STRATEGY_PARAMS[key]}» — не число") from exc
        if number <= 0:
            raise YandexError("strategy", f"{label}: «{STRATEGY_PARAMS[key]}» должно быть больше нуля")
        block[field] = int(number) if key == "goal_id" else _micros(number)
    return {"BiddingStrategyType": kind, struct: block}


def build_bidding_strategy(strategy: dict | None, ctype: str = "TEXT_CAMPAIGN") -> tuple[dict, bool]:
    """Стратегия из формы → BiddingStrategy API и признак ручного управления ставками.

    В ЕПК в сетях ручных ставок (MAXIMUM_COVERAGE) нет, а недельный бюджет ручных ставок
    задаётся в HighestPosition.WeeklySpendLimit.
    """
    strategy = strategy or {}
    unified = ctype == "UNIFIED_CAMPAIGN"
    search = str(strategy.get("search") or "HIGHEST_POSITION").upper()
    network = str(strategy.get("network") or "").upper()
    if unified and network == "MAXIMUM_COVERAGE":
        raise YandexError("strategy", "В ЕПК ручных ставок в сетях нет — в сетях работает только автостратегия")
    if search == "HIGHEST_POSITION":
        network = network or "SERVING_OFF"
        if network not in ("SERVING_OFF", "MAXIMUM_COVERAGE"):
            raise YandexError("strategy", "При ручных ставках на поиске в сетях — ручные ставки или без показов")
        manual: dict = {"BiddingStrategyType": "HIGHEST_POSITION"}
        if unified and strategy.get("weekly_budget_rub") not in (None, "", 0, "0"):
            try:
                weekly = float(strategy["weekly_budget_rub"])
            except (TypeError, ValueError) as exc:
                raise YandexError("strategy", "Ручные ставки: недельный бюджет — не число") from exc
            if weekly <= 0:
                raise YandexError("strategy", "Ручные ставки: недельный бюджет должен быть больше нуля")
            manual["HighestPosition"] = {"WeeklySpendLimit": _micros(weekly)}
        return {"Search": manual, "Network": {"BiddingStrategyType": network}}, True
    if search in AUTO_STRATEGIES:
        network = network or "NETWORK_DEFAULT"
        if network not in ("NETWORK_DEFAULT", "SERVING_OFF"):
            raise YandexError("strategy", "При автостратегии на поиске сети работают по ней же или выключены")
        return {"Search": _auto_block(search, strategy), "Network": {"BiddingStrategyType": network}}, False
    if search == "SERVING_OFF":
        network = network or ("" if unified else "MAXIMUM_COVERAGE")
        if network == "MAXIMUM_COVERAGE":
            return {
                "Search": {"BiddingStrategyType": "SERVING_OFF"},
                "Network": {"BiddingStrategyType": "MAXIMUM_COVERAGE"},
            }, True
        if network in AUTO_STRATEGIES:
            return {"Search": {"BiddingStrategyType": "SERVING_OFF"}, "Network": _auto_block(network, strategy)}, False
        raise YandexError("strategy", "Кампания должна показываться хотя бы на поиске или в сетях")
    raise YandexError("strategy", "Неизвестная стратегия")


def strategy_from_api(bidding: dict | None) -> dict:
    """BiddingStrategy из Директа → плоский вид формы."""
    bidding = bidding or {}
    search = bidding.get("Search") or {}
    network = bidding.get("Network") or {}
    out: dict = {
        "search": search.get("BiddingStrategyType") or "",
        "network": network.get("BiddingStrategyType") or "",
    }
    for part in (search, network):
        kind = part.get("BiddingStrategyType")
        if kind not in AUTO_STRATEGIES:
            continue
        _label, struct, fields = AUTO_STRATEGIES[kind]
        block = part.get(struct) or {}
        for key, (field, _required) in fields.items():
            value = block.get(field)
            if value is None:
                continue
            out[key] = int(value) if key == "goal_id" else _rub(value)
    weekly = manual_weekly_limit(bidding)
    if weekly:
        out["weekly_budget_rub"] = _rub(weekly)
    out["manual"] = out["search"] == "HIGHEST_POSITION" or (
        out["search"] == "SERVING_OFF" and out["network"] == "MAXIMUM_COVERAGE"
    )
    return out


def manual_weekly_limit(bidding: dict | None) -> int | None:
    """Недельный бюджет ручных ставок ЕПК в микроединицах; None — не задан (тогда работает дневной)."""
    search = (bidding or {}).get("Search") or {}
    if search.get("BiddingStrategyType") != "HIGHEST_POSITION":
        return None
    value = (search.get("HighestPosition") or {}).get("WeeklySpendLimit")
    return int(value) if value else None


def strategy_label(strategy: dict) -> str:
    search = strategy.get("search") or ""
    network = strategy.get("network") or ""
    if search == "SERVING_OFF":
        name = AUTO_STRATEGIES[network][0] if network in AUTO_STRATEGIES else MANUAL_LABELS.get(network, network)
        return f"Только сети: {name}"
    name = AUTO_STRATEGIES[search][0] if search in AUTO_STRATEGIES else MANUAL_LABELS.get(search, search)
    return name if network in ("SERVING_OFF", "") else f"{name} + сети"


def _schedule_pct(value) -> int:
    try:
        pct = int(value)
    except (TypeError, ValueError):
        return 0
    if pct <= 0:
        return 0
    return max(10, min(200, round(pct / 10) * 10))


def schedule_grid(value) -> list[list[int]] | None:
    """None — показывать всегда; иначе 7×24 процента ставки (0 — не показывать)."""
    if value in (None, "", "always"):
        return None
    if value == "workdays":
        return [[100] * 24 if day < 5 else [0] * 24 for day in range(7)]
    if value == "workhours":
        return [[100 if 9 <= h < 19 else 0 for h in range(24)] if day < 5 else [0] * 24 for day in range(7)]
    if value == "daytime":
        return [[100 if 8 <= h < 23 else 0 for h in range(24)] for _day in range(7)]
    if isinstance(value, list) and len(value) == 7 and all(isinstance(r, list) and len(r) == 24 for r in value):
        grid = [[_schedule_pct(v) for v in row] for row in value]
        if all(v == 100 for row in grid for v in row):
            return None
        weekday_hours = sum(1 for row in grid[:5] for v in row if v)
        if weekday_hours < 40:
            raise YandexError("schedule", "Директ требует не меньше 40 часов показов в будни")
        return grid
    raise YandexError("schedule", "Неверное расписание показов")


def _time_targeting(grid: list[list[int]], consider_working_weekends: bool = True) -> dict:
    return {
        "Schedule": {"Items": [",".join(str(v) for v in [day + 1, *row]) for day, row in enumerate(grid)]},
        "ConsiderWorkingWeekends": "YES" if consider_working_weekends else "NO",
    }


def _grid_from_api(time_targeting: dict | None) -> list[list[int]] | None:
    items = ((time_targeting or {}).get("Schedule") or {}).get("Items") or []
    if not items:
        return None
    grid = [[100] * 24 for _ in range(7)]
    for item in items:
        parts = [p.strip() for p in str(item).split(",")]
        try:
            day = int(parts[0]) - 1
            values = [int(p) for p in parts[1:25]]
        except (ValueError, IndexError):
            continue
        if 0 <= day < 7 and len(values) == 24:
            grid[day] = values
    return None if all(v == 100 for row in grid for v in row) else grid


def _phrases(value) -> list[str]:
    from app.adgroups import _parse_phrases

    return _parse_phrases(value)


def _negatives(value) -> list[str]:
    from app.negative_lists import CAMPAIGN_CHARS_LIMIT, negatives_length

    phrases = _phrases(value)
    if negatives_length(phrases) > CAMPAIGN_CHARS_LIMIT:
        raise YandexError("negatives-limit")
    return phrases


def _sites(value) -> list[str]:
    raw = value.replace(",", "\n").splitlines() if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for item in raw:
        site = str(item).strip().lower()
        for prefix in ("https://", "http://", "www."):
            if site.startswith(prefix):
                site = site[len(prefix) :]
        site = site.split("/")[0]
        if site and site not in out:
            out.append(site)
    if len(out) > 1000:
        raise YandexError("limit", "Не больше 1000 запрещённых площадок")
    return out


def _settings_items(values: dict | None, *, defaults: bool) -> list[dict]:
    values = values or {}
    out = []
    for option, (_label, default) in CAMPAIGN_SETTINGS.items():
        if option in values:
            enabled = bool(values[option])
        elif defaults:
            enabled = default
        else:
            continue
        out.append({"Option": option, "Value": "YES" if enabled else "NO"})
    return out


def _date(value, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise YandexError("date", f"Неверная дата: {field}") from exc


def _name(value) -> str:
    name = str(value or "").strip()[:255]
    if not name:
        raise YandexError("empty", "Укажите название кампании")
    return name


def _counter_list(values) -> list[int]:
    out: list[int] = []
    for value in values or []:
        try:
            counter_id = int(value)
        except (TypeError, ValueError):
            continue
        if counter_id > 0 and counter_id not in out:
            out.append(counter_id)
    return out


def _priority_goals(values) -> list[dict]:
    out = []
    for item in values or []:
        try:
            goal_id = int(item.get("goal_id"))
            value = float(item.get("value_rub") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if goal_id > 0 and value > 0:
            out.append({"GoalId": goal_id, "Value": _micros(value)})
    return out


# --- Создание ----------------------------------------------------------------------------------


def create_campaign(user_id: int, *, account_id: int, spec: dict, workspace_id: int | None = None) -> dict:
    """
    Новая кампания в выбранном кабинете (черновик). spec — см. campaign_options():
    type, name, start_date, end_date, strategy{search, network, параметры}, daily_budget_rub,
    negative_keywords, excluded_sites, schedule, settings{}, attribution_model, counter_ids,
    priority_goals[{goal_id, value_rub}], tracking_params и необязательная первая группа
    group{name, region_ids, keywords, bid_rub, ad{title, title2, text, href}}.
    """
    from app.workspaces import attach_campaign, require_workspace

    ctype = str(spec.get("type") or "TEXT_CAMPAIGN").upper()
    if ctype not in CAMPAIGN_TYPES:
        raise YandexError("type", "Поддерживаются текстово-графические кампании и ЕПК")
    name = _name(spec.get("name"))
    account = _account_creds(user_id, account_id)
    ws = require_workspace(user_id, workspace_id) if workspace_id else None

    today = date.today()
    start = max(_date(spec.get("start_date"), "дата начала") or today, today)
    end = _date(spec.get("end_date"), "дата окончания")
    if end and end < start:
        raise YandexError("date", "Дата окончания раньше даты начала")

    strategy = dict(spec.get("strategy") or {})
    if ws and ws.get("goal_id") and not strategy.get("goal_id"):
        strategy["goal_id"] = ws["goal_id"]
    bidding, manual = build_bidding_strategy(strategy, ctype)

    body: dict = {"Name": name, "StartDate": start.isoformat(), "TimeZone": "Europe/Moscow"}
    if end:
        body["EndDate"] = end.isoformat()
    daily = spec.get("daily_budget_rub")
    daily_kopecks = None
    if daily not in (None, "", 0):
        if not manual:
            raise YandexError("budget", "Дневной бюджет — только для ручных ставок; у автостратегии задайте недельный")
        if manual_weekly_limit(bidding):
            raise YandexError("budget", "Задан недельный бюджет ручных ставок — дневной не нужен")
        if float(daily) <= 0:
            raise YandexError("budget")
        body["DailyBudget"] = {"Amount": _micros(daily), "Mode": "STANDARD"}
        daily_kopecks = int(round(float(daily) * 100))
    negatives = _negatives(spec.get("negative_keywords"))
    if negatives:
        body["NegativeKeywords"] = {"Items": negatives}
    sites = _sites(spec.get("excluded_sites"))
    if sites:
        body["ExcludedSites"] = {"Items": sites}
    grid = schedule_grid(spec.get("schedule"))
    if grid:
        body["TimeTargeting"] = _time_targeting(grid, spec.get("consider_working_weekends", True) is not False)

    counters = _counter_list(spec.get("counter_ids"))
    if ws and int(ws["counter_id"]) not in counters:
        counters.insert(0, int(ws["counter_id"]))
    block: dict = {"BiddingStrategy": bidding, "Settings": _settings_items(spec.get("settings"), defaults=True)}
    if counters:
        block["CounterIds"] = {"Items": counters}
    goals = _priority_goals(spec.get("priority_goals"))
    if goals:
        block["PriorityGoals"] = {"Items": goals}
    attribution = attribution_for_api(spec.get("attribution_model"))
    if attribution:
        block["AttributionModel"] = attribution
    tracking = str(spec.get("tracking_params") or "").strip().lstrip("?")
    if tracking:
        block["TrackingParams"] = tracking[:1024]
    body[_TYPE_BLOCK[ctype]] = block

    token = get_connection_access_token(account["connection_id"], user_id)
    result = _call_campaigns(token, account["client_login"], "add", {"Campaigns": [body]})
    campaign_id = _added_id(result)
    warnings = [text for _i, text in direct_api.item_warnings(result, "AddResults")]

    flat = strategy_from_api(bidding)
    row = insert_campaign_local(
        user_id,
        account_id,
        campaign_id=campaign_id,
        name=name,
        campaign_type=ctype,
        state="OFF",
        status="DRAFT",
        strategy=flat["search"] if flat["search"] != "SERVING_OFF" else flat["network"],
        daily_budget_kopecks=daily_kopecks,
        negative_keywords=negatives,
        counter_ids=counters,
        start_date=start.isoformat(),
        bidding_strategy=bidding,
        priority_goals=priority_goals_from_api(goals),
        attribution_model=block.get("AttributionModel") or "",
    )
    if ws:
        attach_campaign(user_id, int(ws["id"]), row["id"])

    group_out = None
    if spec.get("group"):
        try:
            # Группа, фразы и объявление — часть создания кампании, отдельных записей в журнале не нужно.
            with journal_quiet():
                group_out = _create_first_group(user_id, row["id"], spec["group"], manual=manual)
            warnings.extend(group_out.pop("warnings", []))
        except YandexError as exc:
            warnings.append(f"Группа не создана: {exc.message or exc.code}")

    out = {"campaign": row, "group": group_out, "warnings": warnings, "status": "ok"}
    log_action(
        user_id,
        "create",
        {"account_id": account_id, "campaign_row_id": row["id"], "name": name, "type": ctype, "strategy": strategy},
        {"campaign_id": campaign_id, "group": group_out, "warnings": warnings},
        "ok",
        workspace_id=int(ws["id"]) if ws else None,
        title=f"Создание кампании «{name}»",
        undo=_delete_undo(row["id"], "Удалить созданную кампанию"),
    )
    return out


def _delete_undo(campaign_row_id: int, label: str) -> dict:
    return {
        "label": label,
        "danger": True,
        "steps": [{"op": "campaigns_action", "args": {"campaign_ids": [campaign_row_id], "action": "delete"}}],
    }


def _create_first_group(user_id: int, campaign_row_id: int, group: dict, *, manual: bool) -> dict:
    from app.adgroups import create_adgroup
    from app.ads import create_text_ad
    from app.keywords import create_keywords

    warnings: list[str] = []
    regions = [int(r) for r in group.get("region_ids") or [] if str(r).lstrip("-").isdigit()] or [225]
    adgroup = create_adgroup(
        user_id,
        campaign_row_id=campaign_row_id,
        name=str(group.get("name") or "Группа 1").strip()[:255] or "Группа 1",
        region_ids=regions,
    )
    out: dict = {"adgroup": adgroup, "keywords": 0, "ad": None}
    phrases = _phrases(group.get("keywords"))[:200]
    for part in direct_api.chunks(phrases, 100):
        try:
            res = create_keywords(
                user_id,
                adgroup_row_id=adgroup["id"],
                phrases=part,
                bid_rub=group.get("bid_rub") if manual else None,
            )
            out["keywords"] += int(res.get("created") or 0)
            warnings.extend(str(e.get("message") or e.get("error") or e) for e in res.get("errors") or [])
        except YandexError as exc:
            warnings.append(f"Фразы не добавлены: {exc.message or exc.code}")
    ad = group.get("ad") or {}
    if ad.get("title") and ad.get("text") and ad.get("href"):
        try:
            out["ad"] = create_text_ad(
                user_id,
                adgroup_row_id=adgroup["id"],
                title=ad["title"],
                title2=ad.get("title2") or "",
                text=ad["text"],
                href=ad["href"],
                titles=[str(t) for t in ad.get("titles") or []] or None,
                texts=[str(t) for t in ad.get("texts") or []] or None,
            )
        except YandexError as exc:
            warnings.append(f"Объявление не создано: {exc.message or exc.code}")
    out["warnings"] = warnings
    return out


def create_text_campaign(
    user_id: int,
    *,
    account_id: int,
    name: str,
    daily_budget_rub: float,
    start_date: str | None = None,
) -> dict:
    """Простая поисковая кампания с ручными ставками (старый быстрый сценарий)."""
    if daily_budget_rub is None or float(daily_budget_rub) <= 0:
        raise YandexError("budget")
    return create_campaign(
        user_id,
        account_id=account_id,
        spec={
            "type": "TEXT_CAMPAIGN",
            "name": name,
            "start_date": start_date,
            "daily_budget_rub": daily_budget_rub,
            "strategy": {"search": "HIGHEST_POSITION", "network": "SERVING_OFF"},
        },
    )


# --- Настройки ---------------------------------------------------------------------------------

_SETTINGS_FIELDS = [
    "Id",
    "Name",
    "Type",
    "State",
    "Status",
    "StartDate",
    "EndDate",
    "DailyBudget",
    "NegativeKeywords",
    "ExcludedSites",
    "TimeTargeting",
    "TimeZone",
    "ClientInfo",
]
_BLOCK_FIELDS = [
    "BiddingStrategy",
    "Settings",
    "CounterIds",
    "PriorityGoals",
    "AttributionModel",
    "TrackingParams",
    "NegativeKeywordSharedSetIds",
]


def _supported(row: dict) -> str:
    ctype = str(row.get("campaign_type") or "")
    if ctype not in _TYPE_BLOCK:
        raise YandexError("unsupported", "Настройки этого типа кампаний пока меняются только в интерфейсе Директа")
    return ctype


def _fetch_campaign(token, client_login: str, campaign_id: int, ctype: str) -> dict:
    block = _TYPE_BLOCK[ctype]
    result = _call_campaigns(
        token,
        client_login,
        "get",
        {
            "SelectionCriteria": {"Ids": [int(campaign_id)]},
            "FieldNames": _SETTINGS_FIELDS,
            f"{block}FieldNames": _BLOCK_FIELDS,
        },
    )
    items = result.get("Campaigns") or []
    if not items:
        raise YandexError("not-found", "Кампания не найдена в Директе")
    return items[0]


def _settings_out(row: dict, item: dict, ctype: str) -> dict:
    block = item.get(_TYPE_BLOCK[ctype]) or {}
    strategy = strategy_from_api(block.get("BiddingStrategy"))
    options = {
        str(s.get("Option")): s.get("Value") == "YES"
        for s in block.get("Settings") or []
        if s.get("Option") in CAMPAIGN_SETTINGS
    }
    tt = item.get("TimeTargeting") or {}
    return {
        "id": row["id"],
        "campaign_id": int(item.get("Id") or row["campaign_id"]),
        "account_id": row["account_id"],
        "client_login": row["client_login"],
        "type": ctype,
        "name": item.get("Name") or row["name"],
        "state": item.get("State") or row.get("state"),
        "status": item.get("Status") or row.get("status"),
        "start_date": item.get("StartDate"),
        "end_date": item.get("EndDate"),
        "time_zone": item.get("TimeZone"),
        "daily_budget_rub": _rub((item.get("DailyBudget") or {}).get("Amount")),
        "strategy": strategy,
        "strategy_label": strategy_label(strategy),
        "negative_keywords": _phrases(((item.get("NegativeKeywords") or {}).get("Items")) or []),
        "excluded_sites": list(((item.get("ExcludedSites") or {}).get("Items")) or []),
        "schedule": _grid_from_api(tt),
        "consider_working_weekends": tt.get("ConsiderWorkingWeekends", "YES") == "YES",
        "counter_ids": _counter_list(((block.get("CounterIds") or {}).get("Items")) or []),
        "priority_goals": [
            {"goal_id": int(g["GoalId"]), "value_rub": _rub(g.get("Value"))}
            for g in ((block.get("PriorityGoals") or {}).get("Items")) or []
            if g.get("GoalId")
        ],
        "attribution_model": block.get("AttributionModel") or "",
        "settings": options,
        "tracking_params": block.get("TrackingParams") or "",
        "client_info": item.get("ClientInfo") or "",
    }


def get_campaign_settings(user_id: int, campaign_row_id: int) -> dict:
    row = _campaign_full(user_id, campaign_row_id)
    ctype = _supported(row)
    token = get_connection_access_token(row["connection_id"], user_id)
    item = _fetch_campaign(token, row["client_login"], row["campaign_id"], ctype)
    return _settings_out(row, item, ctype)


def update_campaign_settings(user_id: int, campaign_row_id: int, changes: dict) -> dict:
    """
    Меняет только переданные поля: name, start_date, end_date ("" — снять), daily_budget_rub
    (0 — снять), strategy, negative_keywords, excluded_sites, schedule, consider_working_weekends,
    counter_ids, priority_goals, attribution_model, settings{}, tracking_params, client_info.
    """
    row = _campaign_full(user_id, campaign_row_id)
    ctype = _supported(row)
    block_name = _TYPE_BLOCK[ctype]
    patch: dict = {"Id": int(row["campaign_id"])}
    block: dict = {}
    local: dict = {}
    changed: list[str] = []

    if "name" in changes:
        patch["Name"] = local["name"] = _name(changes["name"])
        changed.append("название")
    if "start_date" in changes:
        start = _date(changes["start_date"], "дата начала")
        if start:
            patch["StartDate"] = start.isoformat()
            local["start_date"] = start.isoformat()
            changed.append("дата начала")
    if "end_date" in changes:
        end = _date(changes["end_date"], "дата окончания")
        patch["EndDate"] = end.isoformat() if end else None
        changed.append("дата окончания")
    manual = None
    weekly_manual = False
    if "strategy" in changes and changes["strategy"]:
        bidding, manual = build_bidding_strategy(changes["strategy"], ctype)
        block["BiddingStrategy"] = bidding
        flat = strategy_from_api(bidding)
        local["strategy"] = flat["search"] if flat["search"] != "SERVING_OFF" else flat["network"]
        changed.append("стратегия")
        weekly_manual = bool(manual_weekly_limit(bidding))
        if not manual or weekly_manual:
            # У автостратегий и у ручных ставок с недельным бюджетом дневного нет — Директ откажет, если он останется.
            patch["DailyBudget"] = None
            local["daily_budget_kopecks"] = None
    if "daily_budget_rub" in changes:
        daily = changes["daily_budget_rub"]
        if daily in (None, "", 0):
            patch["DailyBudget"] = None
            local["daily_budget_kopecks"] = None
        else:
            if manual is False:
                raise YandexError("budget", "Дневной бюджет — только для ручных ставок")
            if weekly_manual:
                raise YandexError("budget", "Задан недельный бюджет ручных ставок — дневной не нужен")
            if float(daily) <= 0:
                raise YandexError("budget")
            patch["DailyBudget"] = {"Amount": _micros(daily), "Mode": "STANDARD"}
            local["daily_budget_kopecks"] = int(round(float(daily) * 100))
        changed.append("дневной бюджет")
    if "negative_keywords" in changes:
        negatives = _negatives(changes["negative_keywords"])
        patch["NegativeKeywords"] = {"Items": negatives} if negatives else None
        local["negative_keywords"] = negatives
        changed.append("минус-фразы")
    if "excluded_sites" in changes:
        sites = _sites(changes["excluded_sites"])
        patch["ExcludedSites"] = {"Items": sites} if sites else None
        changed.append("запрещённые площадки")
    if "schedule" in changes:
        grid = schedule_grid(changes["schedule"])
        weekends = changes.get("consider_working_weekends", True) is not False
        patch["TimeTargeting"] = _time_targeting(grid or [[100] * 24 for _ in range(7)], weekends)
        changed.append("расписание")
    if "counter_ids" in changes:
        counters = _counter_list(changes["counter_ids"])
        if not counters:
            raise YandexError("counter", "Нужен хотя бы один счётчик Метрики")
        block["CounterIds"] = {"Items": counters}
        local["counter_ids"] = counters
        changed.append("счётчики")
    if "priority_goals" in changes:
        goals = _priority_goals(changes["priority_goals"])
        block["PriorityGoals"] = {"Items": [{**g, "Operation": "SET"} for g in goals]} if goals else None
        changed.append("ключевые цели")
    if "attribution_model" in changes:
        model = attribution_for_api(changes["attribution_model"])
        if not model:
            raise YandexError("attribution", "Неизвестная модель атрибуции")
        block["AttributionModel"] = model
        changed.append("атрибуция")
    if "settings" in changes:
        items = _settings_items(changes["settings"], defaults=False)
        if items:
            block["Settings"] = items
            changed.append("опции")
    if "tracking_params" in changes:
        tracking = str(changes["tracking_params"] or "").strip().lstrip("?")
        block["TrackingParams"] = tracking[:1024] if tracking else None
        changed.append("параметры ссылок")
    if "client_info" in changes:
        patch["ClientInfo"] = str(changes["client_info"] or "").strip()[:255] or None
        changed.append("информация о клиенте")
    if block:
        patch[block_name] = block
    if len(patch) == 1:
        raise YandexError("empty", "Нет изменений")

    token = get_connection_access_token(row["connection_id"], user_id)
    previous = _previous_settings(token, row, ctype, changes)
    result = _call_campaigns(token, row["client_login"], "update", {"Campaigns": [patch]})
    errors = direct_api.item_errors(result, "UpdateResults")
    if errors:
        raise YandexError("direct", errors[0][1])
    warnings = [text for _i, text in direct_api.item_warnings(result, "UpdateResults")]
    _update_local(campaign_row_id, local)
    out = {"id": campaign_row_id, "changed": changed, "warnings": warnings, "status": "ok"}
    log_action(
        user_id,
        "settings",
        {"campaign_row_id": campaign_row_id, "changes": changes},
        out,
        "ok",
        title=f"Настройки «{row['name']}»: {', '.join(changed)}",
        undo={
            "label": "Вернуть прежние настройки",
            "steps": [{"op": "campaign_settings", "args": {"campaign_row_id": campaign_row_id, "changes": previous}}]
            if previous
            else [],
        },
    )
    return out


def _previous_settings(token, row: dict, ctype: str, changes: dict) -> dict:
    """Прежние значения изменяемых полей — для отката. Пусто, если прочитать не удалось."""
    try:
        before = _settings_out(row, _fetch_campaign(token, row["client_login"], row["campaign_id"], ctype), ctype)
    except Exception:
        log.warning("settings snapshot failed for campaign %s", row["id"], exc_info=True)
        return {}
    previous: dict = {}
    for key in changes:
        if key == "start_date":
            # Дату начала в прошлом Директ не примет — её не возвращаем.
            if before.get("start_date") and before["start_date"] >= date.today().isoformat():
                previous["start_date"] = before["start_date"]
        elif key == "end_date":
            previous["end_date"] = before.get("end_date") or ""
        elif key == "daily_budget_rub":
            previous["daily_budget_rub"] = before.get("daily_budget_rub") or 0
        elif key == "strategy":
            previous["strategy"] = before["strategy"]
            if before.get("daily_budget_rub"):
                previous["daily_budget_rub"] = before["daily_budget_rub"]
        elif key == "schedule":
            previous["schedule"] = before.get("schedule")
            previous["consider_working_weekends"] = before.get("consider_working_weekends", True)
        elif key == "settings":
            wanted = changes.get("settings") or {}
            old = {k: before["settings"][k] for k in wanted if k in before["settings"]}
            if old:
                previous["settings"] = old
        elif key == "counter_ids":
            if before.get("counter_ids"):
                previous["counter_ids"] = before["counter_ids"]
        elif key == "attribution_model":
            if before.get("attribution_model"):
                previous["attribution_model"] = before["attribution_model"]
        elif key in ("name", "negative_keywords", "excluded_sites", "priority_goals", "tracking_params", "client_info"):
            previous[key] = before.get(key)
    return previous


def _update_local(campaign_row_id: int, local: dict) -> None:
    if not local:
        return
    sets, params = [], []
    for column, value in local.items():
        if column == "negative_keywords":
            value = json.dumps(value, ensure_ascii=False)
        elif column == "counter_ids":
            value = json.dumps(value)
        sets.append(f"{column} = %s")
        params.append(value)
    params.append(campaign_row_id)
    with pool.connection() as conn:
        conn.execute(f"UPDATE direct_campaigns SET {', '.join(sets)}, synced_at = NOW() WHERE id = %s", params)


def update_campaign(
    user_id: int,
    campaign_row_id: int,
    *,
    name: str | None = None,
    daily_budget_rub: float | None = None,
    negative_keywords: list[str] | str | None = None,
) -> dict:
    """Быстрая правка названия, дневного бюджета и минус-фраз (работает для любых типов)."""
    rows = get_campaign_rows(user_id, [campaign_row_id])
    if not rows:
        raise YandexError("not-found")
    row = rows[0]
    patch: dict = {"Id": int(row["campaign_id"])}
    local: dict = {}
    if name is not None:
        patch["Name"] = local["name"] = _name(name)
    if daily_budget_rub is not None:
        if float(daily_budget_rub) <= 0:
            raise YandexError("budget")
        patch["DailyBudget"] = {"Amount": _micros(daily_budget_rub), "Mode": "STANDARD"}
        local["daily_budget_kopecks"] = int(round(float(daily_budget_rub) * 100))
    negatives = None
    if negative_keywords is not None:
        negatives = _negatives(negative_keywords)
        # Пустой список Директ принимает только как null.
        patch["NegativeKeywords"] = {"Items": negatives} if negatives else None
        local["negative_keywords"] = negatives
    if len(patch) == 1:
        raise YandexError("empty")

    token = get_connection_access_token(row["connection_id"], user_id)
    before_negatives = None
    if negatives is not None:
        from app.negative_lists import fetch_campaign_negatives

        before_negatives = fetch_campaign_negatives(token, row["client_login"], [int(row["campaign_id"])]).get(
            int(row["campaign_id"])
        )
    result = _call_campaigns(token, row["client_login"], "update", {"Campaigns": [patch]})
    errors = direct_api.item_errors(result, "UpdateResults")
    if errors:
        raise YandexError("direct", errors[0][1])
    _update_local(campaign_row_id, local)
    out = {
        "id": campaign_row_id,
        "name": local.get("name"),
        "daily_budget_kopecks": local.get("daily_budget_kopecks"),
        "negative_keywords": negatives,
        "status": "ok",
    }
    steps: list[dict] = []
    if name is not None and row["name"] != local["name"]:
        steps.append({"op": "campaign_update", "args": {"campaign_row_id": campaign_row_id, "name": row["name"]}})
    if daily_budget_rub is not None:
        steps.append({"op": "campaign_budgets", "args": {"budgets": {str(campaign_row_id): row.get("daily_budget_kopecks")}}})
    if before_negatives is not None:
        from app.negative_lists import phrase_key

        old_keys = {phrase_key(p) for p in before_negatives}
        new_keys = {phrase_key(p) for p in negatives or []}
        added = [p for p in negatives or [] if phrase_key(p) not in old_keys]
        removed = [p for p in before_negatives if phrase_key(p) not in new_keys]
        if added or removed:
            steps.append({"op": "negatives", "args": {"campaigns": {str(campaign_row_id): {"add": removed, "remove": added}}}})
    log_action(
        user_id,
        "update",
        {
            "campaign_row_id": campaign_row_id,
            "name": local.get("name"),
            "daily_budget_rub": daily_budget_rub,
            "negative_count": len(negatives) if negatives is not None else None,
        },
        out,
        "ok",
        title=f"Изменение кампании «{row['name']}»",
        undo={"label": "Вернуть как было", "steps": steps},
    )
    return out


def set_campaign_budgets(user_id: int, budgets: dict) -> dict:
    """Дневные бюджеты кампаний как есть: {row_id: копейки | None (снять)}. Для отката."""
    wanted = {int(k): (int(v) if v else None) for k, v in budgets.items()}
    rows = get_campaign_rows(user_id, list(wanted))
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["connection_id"], row["client_login"])].append(row)
    applied, errors, done = 0, [], set()
    for (connection_id, client_login), group in grouped.items():
        try:
            token = get_connection_access_token(connection_id, user_id)
            for chunk in direct_api.chunks(group, UPDATE_BATCH):
                body = [
                    {
                        "Id": int(r["campaign_id"]),
                        "DailyBudget": {"Amount": wanted[r["id"]] * 10_000, "Mode": "STANDARD"} if wanted[r["id"]] else None,
                    }
                    for r in chunk
                ]
                result = _call_campaigns(token, client_login, "update", {"Campaigns": body})
                item_errs = dict(direct_api.item_errors(result, "UpdateResults"))
                for index, r in enumerate(chunk):
                    if index in item_errs:
                        errors.append({"campaign_row_id": r["id"], "message": item_errs[index]})
                        continue
                    with pool.connection() as conn:
                        conn.execute(
                            "UPDATE direct_campaigns SET daily_budget_kopecks = %s, synced_at = NOW() WHERE id = %s",
                            (wanted[r["id"]], r["id"]),
                        )
                    done.add(r["id"])
                    applied += 1
        except YandexError as exc:
            errors.extend({"campaign_row_id": r["id"], "message": exc.message or exc.code} for r in group if r["id"] not in done)
    missing = set(wanted) - {r["id"] for r in rows}
    errors.extend({"campaign_row_id": i, "message": "кампания не найдена"} for i in missing)
    return {"applied": applied, "errors": errors}


# --- Массовые действия -------------------------------------------------------------------------

_SELECT_METHODS = {
    "pause": ("suspend", "SuspendResults", "SUSPENDED"),
    "resume": ("resume", "ResumeResults", "ON"),
    "archive": ("archive", "ArchiveResults", "ARCHIVED"),
    "unarchive": ("unarchive", "UnarchiveResults", "SUSPENDED"),
    "delete": ("delete", "DeleteResults", None),
}


def _new_budget(row: dict, daily_budget_rub, budget_change_pct) -> int:
    """Новый дневной бюджет в копейках: абсолютный или изменение текущего на N %."""
    if daily_budget_rub is not None:
        return int(round(float(daily_budget_rub) * 100))
    current = row.get("daily_budget_kopecks")
    if not current:
        raise YandexError("budget", "У кампании нет дневного бюджета — задайте сумму")
    return int(round(int(current) * (1 + float(budget_change_pct) / 100)))


def bulk_campaign_action(
    user_id: int,
    *,
    campaign_ids: list[int],
    action: str,
    daily_budget_rub: float | None = None,
    budget_change_pct: float | None = None,
    workspace_id: int | None = None,
) -> dict:
    """action: pause | resume | set_budget | archive | unarchive | delete. campaign_ids — id строк."""
    if action not in BULK_ACTIONS:
        raise YandexError("action")
    if not campaign_ids:
        raise YandexError("empty")
    if len(campaign_ids) > BULK_LIMIT:
        raise YandexError("limit")
    if action == "set_budget":
        if daily_budget_rub is None and budget_change_pct is None:
            raise YandexError("budget")
        if daily_budget_rub is not None and float(daily_budget_rub) <= 0:
            raise YandexError("budget")

    rows = get_campaign_rows(user_id, campaign_ids)
    if len(rows) != len(set(campaign_ids)):
        raise YandexError("not-found")
    if workspace_id is None:
        workspace_id = _workspace_for(user_id, {"campaign_ids": campaign_ids})

    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["connection_id"], row["client_login"])].append(row)

    applied = 0
    errors: list[dict] = []
    details: list[dict] = []
    done: list[dict] = []  # строки до изменения — для отката

    def fail(row: dict, login: str, code: str, message: str = "") -> None:
        errors.append(
            {"campaign_row_id": row["id"], "name": row["name"], "client_login": login, "error": code, "message": message}
        )

    for (connection_id, client_login), group in grouped.items():
        done_before = applied
        try:
            token = get_connection_access_token(connection_id, user_id)
            if action == "set_budget":
                planned = []
                for r in group:
                    try:
                        planned.append((r, _new_budget(r, daily_budget_rub, budget_change_pct)))
                    except YandexError as exc:
                        fail(r, client_login, exc.code, exc.message or "")
                for chunk in direct_api.chunks(planned, UPDATE_BATCH):
                    body = [
                        {"Id": int(r["campaign_id"]), "DailyBudget": {"Amount": kopecks * 10_000, "Mode": "STANDARD"}}
                        for r, kopecks in chunk
                    ]
                    result = _call_campaigns(token, client_login, "update", {"Campaigns": body})
                    item_errs = dict(direct_api.item_errors(result, "UpdateResults"))
                    for index, (r, kopecks) in enumerate(chunk):
                        if index in item_errs:
                            fail(r, client_login, "direct", item_errs[index])
                        else:
                            update_campaign_local_state(r["id"], daily_budget_kopecks=kopecks)
                            done.append(r)
                            applied += 1
            else:
                method, key, state = _SELECT_METHODS[action]
                for chunk in direct_api.chunks(group, 1000):
                    ids = [int(r["campaign_id"]) for r in chunk]
                    result = _call_campaigns(token, client_login, method, {"SelectionCriteria": {"Ids": ids}})
                    item_errs = dict(direct_api.item_errors(result, key))
                    for index, r in enumerate(chunk):
                        if index in item_errs:
                            fail(r, client_login, "direct", item_errs[index])
                        elif action == "delete":
                            remove_campaign_local(r["id"])
                            applied += 1
                        else:
                            update_campaign_local_state(r["id"], state=state)
                            done.append(r)
                            applied += 1
        except YandexError as exc:
            for r in group:
                if not any(e["campaign_row_id"] == r["id"] for e in errors):
                    fail(r, client_login, exc.code, exc.message or "")
        except Exception:
            log.exception("bulk action %s", action)
            for r in group:
                if not any(e["campaign_row_id"] == r["id"] for e in errors):
                    fail(r, client_login, "direct")
        count = applied - done_before
        details.append({"client_login": client_login, "count": count, "ok": count == len(group)})

    status = "ok" if not errors else ("partial" if applied else "error")
    result = {"applied": applied, "errors": errors, "details": details, "status": status}
    names = ", ".join(f"«{r['name']}»" for r in rows[:3]) + (f" и ещё {len(rows) - 3}" if len(rows) > 3 else "")
    title = f"{ACTION_TITLES[action]}: {names}"
    if action == "set_budget":
        what = f"{daily_budget_rub:g} ₽" if daily_budget_rub is not None else f"{budget_change_pct:+g} %"
        title = f"Дневной бюджет {what}: {names}"
    log_action(
        user_id,
        action,
        {
            "campaign_ids": campaign_ids,
            "daily_budget_rub": daily_budget_rub,
            "budget_change_pct": budget_change_pct,
        },
        result,
        status,
        workspace_id=workspace_id,
        title=title,
        undo=_bulk_undo(action, done),
    )
    if status == "error":
        first = errors[0] if errors else {"error": "direct", "message": ""}
        raise YandexError(first["error"], first.get("message") or None)
    return result


def _bulk_undo(action: str, done: list[dict]) -> dict | None:
    """Обратное массовое действие только для тех кампаний, что реально изменились."""

    def step(ids: list[int], what: str) -> list[dict]:
        return [{"op": "campaigns_action", "args": {"campaign_ids": ids, "action": what}}] if ids else []

    every = [r["id"] for r in done]
    was_on = [r["id"] for r in done if r.get("state") == "ON"]
    if action == "set_budget":
        budgets = {str(r["id"]): r.get("daily_budget_kopecks") for r in done}
        return {"label": "Вернуть прежние бюджеты", "steps": [{"op": "campaign_budgets", "args": {"budgets": budgets}}]}
    if action == "pause":
        return {"label": "Запустить снова", "steps": step(was_on, "resume")}
    if action == "resume":
        return {"label": "Остановить снова", "steps": step([r["id"] for r in done if r.get("state") == "SUSPENDED"], "pause")}
    if action == "archive":
        return {"label": "Вернуть из архива", "steps": step(every, "unarchive") + step(was_on, "resume")}
    if action == "unarchive":
        return {"label": "Вернуть в архив", "steps": step(every, "archive")}
    return None  # удаление не откатывается


# --- Модерация ---------------------------------------------------------------------------------


def moderate_campaign(user_id: int, campaign_row_id: int) -> dict:
    """Отправляет на модерацию все объявления-черновики кампании."""
    row = _campaign_full(user_id, campaign_row_id)
    token = get_connection_access_token(row["connection_id"], user_id)
    drafts = _get_all(
        token,
        row["client_login"],
        "ads",
        {"SelectionCriteria": {"CampaignIds": [int(row["campaign_id"])], "Statuses": ["DRAFT"]}, "FieldNames": ["Id"]},
        "Ads",
    )
    ids = [int(a["Id"]) for a in drafts if a.get("Id")]
    sent = 0
    errors: list[str] = []
    for chunk in direct_api.chunks(ids, 10_000):
        result = direct_api.call(token, row["client_login"], "ads", "moderate", {"SelectionCriteria": {"Ids": chunk}})
        item_errs = direct_api.item_errors(result, "ModerateResults")
        sent += len(chunk) - len(item_errs)
        errors.extend(text for _i, text in item_errs)
    status = "ok" if not errors else ("partial" if sent else "error")
    out = {"drafts": len(ids), "sent": sent, "errors": errors[:20], "status": status}
    log_action(
        user_id,
        "moderate",
        {"campaign_row_id": campaign_row_id},
        out,
        status,
        title=f"На модерацию: «{row['name']}» — {sent} объявл.",
    )
    if status == "error":
        raise YandexError("direct", errors[0])
    return out


# --- Копия кампании ----------------------------------------------------------------------------

# Опции, которые можно передать в add; остальные Директ отдаёт только на чтение.
_COPYABLE_OPTIONS = {
    "ADD_METRICA_TAG",
    "ADD_TO_FAVORITES",
    "ENABLE_COMPANY_INFO",
    "ENABLE_SITE_MONITORING",
    "EXCLUDE_PAUSED_COMPETING_ADS",
    "ENABLE_EXTENDED_AD_TITLE",
    "CAMPAIGN_EXACT_PHRASE_MATCHING_ENABLED",
    "ENABLE_CURRENT_AREA_TARGETING",
    "ENABLE_REGULAR_AREA_TARGETING",
}
_GROUP_TYPES = {"TEXT_AD_GROUP", "UNIFIED_AD_GROUP"}


def _strategy_for_add(strategy: dict | None) -> dict:
    """campaigns.get отдаёт поля, которые campaigns.add не принимает.

    BudgetType в ответе get говорит, недельный бюджет или бюджет периода.
    В add этого поля нет: тип задаётся тем, какое поле передано — WeeklySpendLimit
    или CustomPeriodBudget, но не оба. ExplorationBudget с признаком «не свой»
    add отклоняет: бюджет по умолчанию Директ проставит сам.
    """
    out = _clean(strategy or {})
    for side in ("Search", "Network"):
        part = out.get(side)
        if not isinstance(part, dict):
            continue
        for block in part.values():
            if not isinstance(block, dict):
                continue
            kind = block.pop("BudgetType", None)
            if kind == "WEEKLY_BUDGET":
                block.pop("CustomPeriodBudget", None)
            elif kind == "CUSTOM_PERIOD_BUDGET":
                block.pop("WeeklySpendLimit", None)
            explore = block.get("ExplorationBudget")
            if isinstance(explore, dict) and explore.get("IsMinimumExplorationBudgetCustom") != "YES":
                block.pop("ExplorationBudget", None)
    return _clean(out)


def _copy_body(
    item: dict, ctype: str, *, name: str, counter_id: int | None, same_account: bool = False
) -> tuple[dict, bool]:
    block_name = _TYPE_BLOCK[ctype]
    src = item.get(block_name) or {}
    strategy = _strategy_for_add(src.get("BiddingStrategy"))
    flat = strategy_from_api(strategy)
    today = date.today()
    start = max(_date(item.get("StartDate"), "дата начала") or today, today)
    body: dict = {"Name": name, "StartDate": start.isoformat(), "TimeZone": item.get("TimeZone") or "Europe/Moscow"}
    end = _date(item.get("EndDate"), "дата окончания")
    if end and end >= start:
        body["EndDate"] = end.isoformat()
    daily = (item.get("DailyBudget") or {}).get("Amount")
    # Недельный бюджет ручных ставок ЕПК и дневной вместе Директ не принимает.
    if daily and flat["manual"] and not manual_weekly_limit(strategy):
        body["DailyBudget"] = {"Amount": int(daily), "Mode": (item.get("DailyBudget") or {}).get("Mode") or "STANDARD"}
    for key in ("NegativeKeywords", "ExcludedSites"):
        items = ((item.get(key) or {}).get("Items")) or []
        if items:
            body[key] = {"Items": items}
    tt = item.get("TimeTargeting") or {}
    if (tt.get("Schedule") or {}).get("Items"):
        body["TimeTargeting"] = _clean(
            {
                "Schedule": {"Items": tt["Schedule"]["Items"]},
                "ConsiderWorkingWeekends": tt.get("ConsiderWorkingWeekends") or "YES",
                "HolidaysSchedule": tt.get("HolidaysSchedule"),
            }
        )
    if item.get("ClientInfo"):
        body["ClientInfo"] = item["ClientInfo"]

    counters = _counter_list(((src.get("CounterIds") or {}).get("Items")) or [])
    if counter_id and counter_id not in counters:
        counters.insert(0, counter_id)
    block: dict = {
        "BiddingStrategy": strategy,
        "Settings": [
            {"Option": s["Option"], "Value": s["Value"]}
            for s in src.get("Settings") or []
            if s.get("Option") in _COPYABLE_OPTIONS and s.get("Value") in ("YES", "NO")
        ],
    }
    if counters:
        block["CounterIds"] = {"Items": counters}
    goals = [
        {"GoalId": int(g["GoalId"]), "Value": int(g["Value"])}
        for g in ((src.get("PriorityGoals") or {}).get("Items")) or []
        if g.get("GoalId") and g.get("Value")
    ]
    if goals:
        block["PriorityGoals"] = {"Items": goals}
    attribution = attribution_for_api(src.get("AttributionModel"))
    if attribution:
        block["AttributionModel"] = attribution
    if src.get("TrackingParams"):
        block["TrackingParams"] = src["TrackingParams"]
    shared = ((src.get("NegativeKeywordSharedSetIds") or {}).get("Items")) or []
    if same_account and shared:
        # Наборы минус-фраз — объекты кабинета: в своём кабинете подключаем те же.
        block["NegativeKeywordSharedSetIds"] = {"Items": shared}
    body[block_name] = block
    return body, flat["manual"]


def _add_copy(token, client_login: str, body: dict, block_name: str, warnings: list[str]) -> int:
    try:
        return _added_id(_call_campaigns(token, client_login, "add", {"Campaigns": [body]}))
    except YandexError as exc:
        # Частая причина — опция или ключевые цели, недоступные в другом кабинете: пробуем проще.
        block = body[block_name]
        if not (block.get("Settings") or block.get("PriorityGoals")):
            raise
        warnings.append(f"Часть настроек не перенесена ({exc.message or exc.code})")
        block["Settings"] = [s for s in block.get("Settings") or [] if s["Option"] == "ADD_METRICA_TAG"]
        block.pop("PriorityGoals", None)
        return _added_id(_call_campaigns(token, client_login, "add", {"Campaigns": [body]}))


def _add_batches(token, client_login: str, service: str, key: str, objects: list[dict]) -> tuple[list[int | None], list[str]]:
    """Добавляет пачками; возвращает новые id по порядку (None — не создан) и ошибки."""
    ids: list[int | None] = []
    errors: list[str] = []
    for chunk in direct_api.chunks(objects, ADD_BATCH):
        result = direct_api.call(token, client_login, service, "add", {key: chunk})
        item_errs = dict(direct_api.item_errors(result, "AddResults"))
        for index, res in enumerate(result.get("AddResults") or []):
            if index in item_errs or not res.get("Id"):
                ids.append(None)
                if index in item_errs:
                    errors.append(item_errs[index])
            else:
                ids.append(int(res["Id"]))
        ids.extend([None] * (len(chunk) - len(result.get("AddResults") or [])))
    return ids, errors


def _copy_sitelinks(src_token, src_login, dst_token, dst_login, set_ids: set[int], warnings: list[str]) -> dict[int, int]:
    if not set_ids:
        return {}
    try:
        sets = _get_all(
            src_token, src_login, "sitelinks", {"SelectionCriteria": {"Ids": sorted(set_ids)}, "FieldNames": ["Id", "Sitelinks"]}, "SitelinksSets"
        )
        payload = [
            {"Sitelinks": [_clean({k: link.get(k) for k in ("Title", "Href", "Description")}) for link in s.get("Sitelinks") or []]}
            for s in sets
        ]
        new_ids, errors = _add_batches(dst_token, dst_login, "sitelinks", "SitelinksSets", payload)
        warnings.extend(f"Быстрые ссылки: {e}" for e in errors[:3])
        return {int(s["Id"]): new for s, new in zip(sets, new_ids) if new}
    except YandexError as exc:
        warnings.append(f"Быстрые ссылки не скопированы: {exc.message or exc.code}")
        return {}


def _copy_callouts(src_token, src_login, dst_token, dst_login, ext_ids: set[int], warnings: list[str]) -> dict[int, int]:
    if not ext_ids:
        return {}
    try:
        exts = _get_all(
            src_token,
            src_login,
            "adextensions",
            {"SelectionCriteria": {"Ids": sorted(ext_ids)}, "FieldNames": ["Id", "Type"], "CalloutFieldNames": ["CalloutText"]},
            "AdExtensions",
        )
        exts = [e for e in exts if (e.get("Callout") or {}).get("CalloutText")]
        payload = [{"Callout": {"CalloutText": e["Callout"]["CalloutText"]}} for e in exts]
        new_ids, errors = _add_batches(dst_token, dst_login, "adextensions", "AdExtensions", payload)
        warnings.extend(f"Уточнения: {e}" for e in errors[:3])
        return {int(e["Id"]): new for e, new in zip(exts, new_ids) if new}
    except YandexError as exc:
        warnings.append(f"Уточнения не скопированы: {exc.message or exc.code}")
        return {}


# Поля для копии: без ResponsiveAdFieldNames Директ отдаёт комбинаторное объявление
# как TextAd с одним заголовком, текстом и картинкой.
_AD_COPY_FIELDS = {
    "FieldNames": ["Id", "AdGroupId", "Type", "State"],
    "TextAdFieldNames": [
        "Title",
        "Title2",
        "Text",
        "Href",
        "DisplayUrlPath",
        "Mobile",
        "SitelinkSetId",
        "AdImageHash",
        "VCardId",
        "AdExtensions",
        "TurboPageId",
        "VideoExtension",
        "BusinessId",
        "ErirAdDescription",
    ],
    "TextAdPriceExtensionFieldNames": ["Price", "OldPrice", "PriceQualifier", "PriceCurrency"],
    "ResponsiveAdFieldNames": [
        "Titles",
        "Texts",
        "Href",
        "DisplayUrlPath",
        "SitelinkSetId",
        "AdImages",
        "VideoExtensions",
        "AdExtensions",
        "PriceExtension",
        "BusinessId",
        "ErirAdDescription",
    ],
    "TextImageAdFieldNames": ["Href", "AdImageHash", "TurboPageId"],
}
_KEYWORD_COPY_FIELDS = {
    "FieldNames": [
        "Id",
        "AdGroupId",
        "Keyword",
        "State",
        "Bid",
        "ContextBid",
        "StrategyPriority",
        "UserParam1",
        "UserParam2",
        "AutotargetingSearchBidIsAuto",
    ],
    "AutotargetingSettingsCategoriesFieldNames": ["Exact", "Narrow", "Alternative", "Accessory", "Broader"],
    "AutotargetingSettingsBrandOptionsFieldNames": ["WithoutBrands", "WithAdvertiserBrand", "WithCompetitorsBrand"],
}
AUTOTARGETING = "---autotargeting"
# Картинки качаем только с серверов Яндекса и не больше 10 МБ; Директ советует грузить по 3 за вызов.
_IMAGE_HOSTS = (".yandex.net", ".yandex.ru", ".yandex.com", ".yastatic.net")
_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_BATCH = 3


def _group_for_add(group: dict, campaign_id: int, *, unified: bool) -> dict:
    entry = {
        "Name": group.get("Name") or "Группа",
        "CampaignId": campaign_id,
        "RegionIds": group.get("RegionIds") or [225],
        "NegativeKeywords": {"Items": group["NegativeKeywords"]["Items"]}
        if (group.get("NegativeKeywords") or {}).get("Items")
        else None,
        "TrackingParams": group.get("TrackingParams") or None,
    }
    if unified:
        # Группа ЕПК без UnifiedAdGroup.OfferRetargeting не создаётся.
        offer = (group.get("UnifiedAdGroup") or {}).get("OfferRetargeting")
        entry["UnifiedAdGroup"] = {"OfferRetargeting": offer if offer in ("YES", "NO") else "NO"}
    return _clean(entry)


def _is_autotargeting(keyword: dict) -> bool:
    return str(keyword.get("Keyword") or "").strip().lower() == AUTOTARGETING


def _keyword_for_add(keyword: dict, group_id: int, *, manual: bool, unified: bool) -> dict:
    auto = _is_autotargeting(keyword)
    entry = {
        "AdGroupId": group_id,
        "Keyword": AUTOTARGETING if auto else keyword["Keyword"],
        "UserParam1": keyword.get("UserParam1") or None,
        "UserParam2": keyword.get("UserParam2") or None,
    }
    if manual:
        entry["Bid"] = keyword.get("Bid") or None
        if not unified:
            # В ЕПК при ручных ставках сетей нет — ставка в сетях не нужна.
            entry["ContextBid"] = keyword.get("ContextBid") or None
    elif keyword.get("StrategyPriority") in ("LOW", "NORMAL", "HIGH"):
        entry["StrategyPriority"] = keyword["StrategyPriority"]
    if auto:
        if keyword.get("AutotargetingSearchBidIsAuto") in ("YES", "NO"):
            entry["AutotargetingSearchBidIsAuto"] = keyword["AutotargetingSearchBidIsAuto"]
        if keyword.get("AutotargetingSettings"):
            entry["AutotargetingSettings"] = keyword["AutotargetingSettings"]
    return _clean(entry)


def _ad_body(ad: dict) -> dict:
    return ad.get("ResponsiveAd") or ad.get("TextAd") or ad.get("TextImageAd") or {}


def _ad_image_hashes(ad: dict) -> list[str]:
    if ad.get("ResponsiveAd"):
        items = ((ad["ResponsiveAd"].get("AdImages") or {}).get("Items")) or []
        return [str(i["ImageHash"]) for i in items if i.get("ImageHash")]
    body = ad.get("TextAd") or ad.get("TextImageAd") or {}
    return [str(body["AdImageHash"])] if body.get("AdImageHash") else []


def _source_only(body: dict) -> bool:
    """Визитка, турбо-страница, видео или организация — их в другой кабинет не перенести."""
    return bool(
        body.get("VCardId")
        or body.get("TurboPageId")
        or body.get("BusinessId")
        or (body.get("VideoExtension") or {}).get("CreativeId")
        or ((body.get("VideoExtensions") or {}).get("Items"))
    )


def _price_extension(value: dict | None) -> dict | None:
    value = value or {}
    if not (value.get("Price") and value.get("PriceQualifier") and value.get("PriceCurrency")):
        return None
    return _clean({k: value.get(k) for k in ("Price", "OldPrice", "PriceQualifier", "PriceCurrency")})


def _ad_for_add(
    ad: dict,
    *,
    unified: bool,
    same: bool,
    sitelinks: dict[int, int],
    callouts: dict[int, int],
    images: dict[str, str],
) -> dict | None:
    """Объявление из ads.get → элемент ads.add без AdGroupId; None — такой вид не копируем."""

    def image(value) -> str | None:
        if not value:
            return None
        return str(value) if same else images.get(str(value))

    def sitelink(body: dict) -> int | None:
        set_id = int(body.get("SitelinkSetId") or 0)
        return (set_id or None) if same else sitelinks.get(set_id)

    def extensions(body: dict) -> list[int] | None:
        ids = [
            int(e["AdExtensionId"])
            for e in body.get("AdExtensions") or []
            if e.get("AdExtensionId") and e.get("Type") in (None, "CALLOUT")
        ]
        return (ids if same else [callouts[i] for i in ids if i in callouts]) or None

    if ad.get("ResponsiveAd"):
        r = ad["ResponsiveAd"]
        entry = {
            "Titles": [str(t["Title"]) for t in r.get("Titles") or [] if t.get("Title")],
            "Texts": [str(t["Text"]) for t in r.get("Texts") or [] if t.get("Text")],
            "Href": r.get("Href") or None,
            "DisplayUrlPath": r.get("DisplayUrlPath") or None,
            "SitelinkSetId": sitelink(r),
            "AdExtensionIds": extensions(r),
            "AdImageHashes": [h for h in (image(x) for x in _ad_image_hashes(ad)) if h] or None,
            "PriceExtension": _price_extension(r.get("PriceExtension")),
            "ErirAdDescription": r.get("ErirAdDescription") or None,
        }
        if same:
            videos = ((r.get("VideoExtensions") or {}).get("Items")) or []
            entry["VideoExtensionIds"] = [int(v["CreativeId"]) for v in videos if v.get("CreativeId")] or None
            entry["BusinessId"] = r.get("BusinessId") or None
        if not entry["Titles"] or not entry["Texts"]:
            return None
        return {"ResponsiveAd": _clean(entry)}
    if ad.get("TextAd"):
        t = ad["TextAd"]
        if not t.get("Title") or not t.get("Text"):
            return None
        # В ЕПК Директ сам превратит TextAd в комбинаторное; Mobile=YES, визитку и турбо-страницу группа ЕПК не примет.
        entry = {
            "Title": t["Title"],
            "Title2": t.get("Title2") or None,
            "Text": t["Text"],
            "Href": t.get("Href") or None,
            "Mobile": "NO" if unified else (t.get("Mobile") or "NO"),
            "DisplayUrlPath": t.get("DisplayUrlPath") or None,
            "SitelinkSetId": sitelink(t),
            "AdExtensionIds": extensions(t),
            "AdImageHash": image(t.get("AdImageHash")),
            "PriceExtension": _price_extension(t.get("PriceExtension")),
            "ErirAdDescription": t.get("ErirAdDescription") or None,
        }
        if same:
            creative = (t.get("VideoExtension") or {}).get("CreativeId")
            entry["VideoExtension"] = {"CreativeId": int(creative)} if creative else None
            entry["BusinessId"] = t.get("BusinessId") or None
            if not unified:
                entry["VCardId"] = t.get("VCardId") or None
                entry["TurboPageId"] = t.get("TurboPageId") or None
        return {"TextAd": _clean(entry)}
    if ad.get("TextImageAd"):
        t = ad["TextImageAd"]
        picture = image(t.get("AdImageHash"))
        if not picture:
            return None
        entry = {"AdImageHash": picture, "Href": t.get("Href") or None}
        if same and not unified:
            entry["TurboPageId"] = t.get("TurboPageId") or None
        if not entry["Href"] and not entry.get("TurboPageId"):
            return None
        return {"TextImageAd": _clean(entry)}
    return None


def _download_image(url) -> bytes | None:
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host.endswith(_IMAGE_HOSTS):
        return None
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            response = client.get(str(url))
    except httpx.HTTPError as exc:
        log.warning("image download %s: %s", host, exc)
        return None
    if response.status_code != 200 or not response.content or len(response.content) > _IMAGE_MAX_BYTES:
        return None
    return response.content


def _copy_images(src_token, src_login, dst_token, dst_login, hashes: set[str], warnings: list[str]) -> dict[str, str]:
    """Картинки живут в библиотеке кабинета: для другого кабинета скачиваем оригиналы и загружаем заново."""
    if not hashes:
        return {}
    try:
        found = _get_all(
            src_token,
            src_login,
            "adimages",
            {"SelectionCriteria": {"AdImageHashes": sorted(hashes)}, "FieldNames": ["AdImageHash", "OriginalUrl", "Name"]},
            "AdImages",
        )
    except YandexError as exc:
        warnings.append(f"Картинки не скопированы: {exc.message or exc.code}")
        return {}
    out: dict[str, str] = {}
    errors: list[str] = []
    for chunk in direct_api.chunks([i for i in found if i.get("AdImageHash")], _IMAGE_BATCH):
        items, sources = [], []
        for img in chunk:
            data = _download_image(img.get("OriginalUrl"))
            if data is None:
                continue
            name = str(img.get("Name") or img["AdImageHash"])[:255]
            items.append({"ImageData": base64.b64encode(data).decode("ascii"), "Name": name})
            sources.append(str(img["AdImageHash"]))
        if not items:
            continue
        try:
            result = direct_api.call(dst_token, dst_login, "adimages", "add", {"AdImages": items})
        except YandexError as exc:
            errors.append(exc.message or exc.code)
            continue
        item_errs = dict(direct_api.item_errors(result, "AddResults"))
        errors.extend(item_errs.values())
        for index, (source, res) in enumerate(zip(sources, result.get("AddResults") or [])):
            if index not in item_errs and res.get("AdImageHash"):
                out[source] = str(res["AdImageHash"])
    lost = len(hashes) - len(out)
    if lost:
        reason = f" ({errors[0]})" if errors else ""
        warnings.append(f"Не скопировано картинок: {lost}{reason} — объявления созданы без них")
    return out


def duplicate_campaign(
    user_id: int,
    campaign_row_id: int,
    *,
    target_account_id: int | None = None,
    name: str | None = None,
    workspace_id: int | None = None,
    progress=None,
) -> dict:
    """
    Копия кампании со структурой: настройки, группы, фразы и автотаргетинг (со ставками при ручной
    стратегии), объявления — комбинаторные со всеми заголовками, текстами и картинками, текстовые
    и графические. В другой кабинет быстрые ссылки, уточнения и картинки пересоздаются; визитки,
    турбо-страницы, видео, организации и наборы минус-фраз — нет (они живут в кабинете-источнике).
    """
    from app.adgroups import sync_campaign_adgroups
    from app.ads import sync_campaign_ads
    from app.keywords import sync_campaign_keywords
    from app.workspaces import attach_campaign, campaign_workspace_id, require_workspace

    def step(n: int, text: str) -> None:
        if progress:
            progress(n, 7, text)

    src = _campaign_full(user_id, campaign_row_id)
    ctype = _supported(src)
    block_name = _TYPE_BLOCK[ctype]
    target_id = int(target_account_id or src["account_id"])
    dst = _account_creds(user_id, target_id)
    same = target_id == int(src["account_id"])
    src_token = get_connection_access_token(src["connection_id"], user_id)
    dst_token = get_connection_access_token(dst["connection_id"], user_id)
    if direct_api.is_sandbox(src_token) != direct_api.is_sandbox(dst_token):
        raise YandexError("sandbox", "Нельзя копировать между песочницей и боевым Директом")
    ws_id = workspace_id or campaign_workspace_id(user_id, campaign_row_id)
    ws = require_workspace(user_id, ws_id) if ws_id else None
    new_name = _name(name or f"{src['name']} (копия)")
    warnings: list[str] = []

    step(0, "Читаю настройки кампании")
    item = _fetch_campaign(src_token, src["client_login"], src["campaign_id"], ctype)
    body, manual = _copy_body(
        item, ctype, name=new_name, counter_id=int(ws["counter_id"]) if ws else None, same_account=same
    )
    if not same and ((item.get(block_name) or {}).get("NegativeKeywordSharedSetIds") or {}).get("Items"):
        warnings.append("Наборы минус-фраз живут в исходном кабинете — подключите их к копии вручную")

    step(1, "Создаю кампанию")
    new_campaign_id = _add_copy(dst_token, dst["client_login"], body, block_name, warnings)
    flat = strategy_from_api(body[block_name]["BiddingStrategy"])
    row = insert_campaign_local(
        user_id,
        target_id,
        campaign_id=new_campaign_id,
        name=new_name,
        campaign_type=ctype,
        state="OFF",
        status="DRAFT",
        strategy=flat["search"] if flat["search"] != "SERVING_OFF" else flat["network"],
        daily_budget_kopecks=_money_to_kopecks((body.get("DailyBudget") or {}).get("Amount")),
        negative_keywords=((body.get("NegativeKeywords") or {}).get("Items")) or [],
        counter_ids=((body[block_name].get("CounterIds") or {}).get("Items")) or [],
        start_date=body["StartDate"],
        bidding_strategy=body[block_name]["BiddingStrategy"],
        priority_goals=priority_goals_from_api(body[block_name].get("PriorityGoals")),
        attribution_model=body[block_name].get("AttributionModel") or "",
        source_row_id=campaign_row_id,
    )
    if ws:
        attach_campaign(user_id, int(ws["id"]), row["id"])
    out = {"campaign": row, "groups": 0, "keywords": 0, "ads": 0, "warnings": warnings, "status": "ok"}

    try:
        step(2, "Копирую группы")
        unified = ctype == "UNIFIED_CAMPAIGN"
        group_fields: dict = {"FieldNames": ["Id", "Name", "RegionIds", "NegativeKeywords", "TrackingParams", "Type"]}
        if unified:
            group_fields["UnifiedAdGroupFieldNames"] = ["OfferRetargeting"]
        groups = _get_all(
            src_token,
            src["client_login"],
            "adgroups",
            {"SelectionCriteria": {"CampaignIds": [int(src["campaign_id"])]}, **group_fields},
            "AdGroups",
        )
        skipped = [g for g in groups if g.get("Type") not in _GROUP_TYPES]
        if skipped:
            warnings.append(f"Пропущено групп особых типов: {len(skipped)}")
        groups = [g for g in groups if g.get("Type") in _GROUP_TYPES]
        payload = [_group_for_add(g, new_campaign_id, unified=unified) for g in groups]
        new_group_ids, errors = _add_batches(dst_token, dst["client_login"], "adgroups", "AdGroups", payload)
        warnings.extend(f"Группа: {e}" for e in errors[:5])
        group_map = {int(g["Id"]): new for g, new in zip(groups, new_group_ids) if new}
        out["groups"] = len(group_map)

        step(3, "Копирую ключевые фразы и автотаргетинг")
        keywords = []
        if group_map:
            keywords = _get_all(
                src_token,
                src["client_login"],
                "keywords",
                {"SelectionCriteria": {"CampaignIds": [int(src["campaign_id"])]}, **_KEYWORD_COPY_FIELDS},
                "Keywords",
            )
        keywords = [k for k in keywords if int(k.get("AdGroupId") or 0) in group_map]
        phrases = [k for k in keywords if not _is_autotargeting(k)]
        autos = [k for k in keywords if _is_autotargeting(k)]
        new_kw_ids, errors = _add_batches(
            dst_token,
            dst["client_login"],
            "keywords",
            "Keywords",
            [_keyword_for_add(k, group_map[int(k["AdGroupId"])], manual=manual, unified=unified) for k in phrases],
        )
        warnings.extend(f"Фраза: {e}" for e in errors[:5])
        auto_ids, errors = _add_batches(
            dst_token,
            dst["client_login"],
            "keywords",
            "Keywords",
            [_keyword_for_add(k, group_map[int(k["AdGroupId"])], manual=manual, unified=unified) for k in autos],
        )
        warnings.extend(f"Автотаргетинг: {e}" for e in errors[:3])
        out["keywords"] = sum(1 for x in new_kw_ids if x)
        out["autotargeting"] = sum(1 for x in auto_ids if x)
        suspended = [
            new for k, new in zip(phrases + autos, new_kw_ids + auto_ids) if new and k.get("State") == "SUSPENDED"
        ]
        for chunk in direct_api.chunks(suspended, 10_000):
            try:
                direct_api.call(dst_token, dst["client_login"], "keywords", "suspend", {"SelectionCriteria": {"Ids": chunk}})
            except YandexError as exc:
                warnings.append(f"Не удалось остановить фразы, выключенные в оригинале: {exc.message or exc.code}")

        step(4, "Копирую объявления")
        ads = []
        if group_map:
            ads = _get_all(
                src_token,
                src["client_login"],
                "ads",
                {"SelectionCriteria": {"CampaignIds": [int(src["campaign_id"])]}, **_AD_COPY_FIELDS},
                "Ads",
            )
        ads = [a for a in ads if int(a.get("AdGroupId") or 0) in group_map and a.get("State") != "ARCHIVED"]
        sitelinks: dict[int, int] = {}
        callouts: dict[int, int] = {}
        images: dict[str, str] = {}
        if not same:
            bodies = [_ad_body(a) for a in ads]
            sitelinks = _copy_sitelinks(
                src_token,
                src["client_login"],
                dst_token,
                dst["client_login"],
                {int(b["SitelinkSetId"]) for b in bodies if b.get("SitelinkSetId")},
                warnings,
            )
            callouts = _copy_callouts(
                src_token,
                src["client_login"],
                dst_token,
                dst["client_login"],
                {
                    int(e["AdExtensionId"])
                    for b in bodies
                    for e in b.get("AdExtensions") or []
                    if e.get("AdExtensionId") and e.get("Type") in (None, "CALLOUT")
                },
                warnings,
            )
            step(4, "Копирую картинки объявлений")
            images = _copy_images(
                src_token,
                src["client_login"],
                dst_token,
                dst["client_login"],
                {h for a in ads for h in _ad_image_hashes(a)},
                warnings,
            )
            if any(_source_only(b) for b in bodies):
                warnings.append("Визитки, турбо-страницы, видео и организации живут в исходном кабинете — добавьте их в копии вручную")
        payload = []
        other = 0
        for a in ads:
            entry = _ad_for_add(a, unified=unified, same=same, sitelinks=sitelinks, callouts=callouts, images=images)
            if entry is None:
                other += 1
                continue
            payload.append({"AdGroupId": group_map[int(a["AdGroupId"])], **entry})
        if other:
            warnings.append(f"Не скопировано объявлений других видов: {other}")
        new_ad_ids, errors = _add_batches(dst_token, dst["client_login"], "ads", "Ads", payload)
        warnings.extend(f"Объявление: {e}" for e in errors[:5])
        out["ads"] = sum(1 for x in new_ad_ids if x)
        warnings.append("Корректировки ставок не копируются — проверьте их в копии")
    except YandexError as exc:
        warnings.append(f"Структура скопирована не полностью: {exc.message or exc.code}")
        out["status"] = "partial"

    step(5, "Обновляю данные копии")
    for sync in (sync_campaign_adgroups, sync_campaign_keywords, sync_campaign_ads):
        try:
            sync(user_id, row["id"])
        except Exception:
            log.exception("sync after duplicate %s", row["id"])
    step(6, "Готово")
    log_action(
        user_id,
        "duplicate",
        {"campaign_row_id": campaign_row_id, "target_account_id": target_id, "new_campaign_row_id": row["id"]},
        {k: v for k, v in out.items() if k != "campaign"},
        out["status"],
        workspace_id=int(ws["id"]) if ws else None,
        title=f"Копия «{src['name']}» → «{new_name}» ({dst['client_login']})",
        undo=_delete_undo(row["id"], "Удалить копию"),
        targets=[f"campaign:{campaign_row_id}", f"campaign:{row['id']}"],
    )
    return out
