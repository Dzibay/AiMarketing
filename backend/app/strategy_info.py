"""Стратегия кампании Директа в коротком виде: тип, оплачиваемая цель, цена конверсии, бюджеты.

Нужна синку (что поменялось в стратегии), сигналам («конверсии за 0 ₽», затухание) и ИИ.
Деньги в ответах campaigns.get — микроединицы: 1 000 000 = 1 ₽.
"""

from __future__ import annotations

STRATEGY_LABELS = {
    "HIGHEST_POSITION": "Ручные ставки",
    "MAXIMUM_COVERAGE": "Ручные ставки",
    "SERVING_OFF": "Не показывать",
    "NETWORK_DEFAULT": "Как на поиске",
    "WB_MAXIMUM_CLICKS": "Максимум кликов",
    "WB_MAXIMUM_CONVERSION_RATE": "Максимум конверсий",
    "WB_MAXIMUM_APP_INSTALLS": "Максимум установок",
    "WEEKLY_CLICK_PACKAGE": "Недельный пакет кликов",
    "AVERAGE_CPC": "Средняя цена клика",
    "AVERAGE_CPA": "Средняя цена конверсии",
    "AVERAGE_CPA_MULTIPLE_GOALS": "Средняя цена конверсии по нескольким целям",
    "AVERAGE_CPA_PER_CAMPAIGN": "Средняя цена конверсии на кампанию",
    "AVERAGE_CPA_PER_FILTER": "Средняя цена конверсии на фильтр",
    "AVERAGE_CPI": "Средняя цена установки",
    "AVERAGE_CRR": "Доля рекламных расходов",
    "AVERAGE_ROI": "Рентабельность",
    "PAY_FOR_CONVERSION": "Оплата за конверсии",
    "PAY_FOR_CONVERSION_CRR": "Оплата за конверсии по доле расходов",
    "PAY_FOR_CONVERSION_MULTIPLE_GOALS": "Оплата за конверсии по нескольким целям",
    "PAY_FOR_CONVERSION_PER_CAMPAIGN": "Оплата за конверсии на кампанию",
    "PAY_FOR_CONVERSION_PER_FILTER": "Оплата за конверсии на фильтр",
    "PAY_FOR_INSTALL": "Оплата за установки",
}

# Служебные цели стратегий Директа — не цели Метрики: в отчёт по целям их не передать.
SPECIAL_GOALS = {0: "все цели", 12: "вовлечённые сессии", 13: "ключевые цели кампании"}
KEY_GOALS_GOAL_ID = 13
# Стратегии, которые оптимизируют сразу по набору ключевых целей кампании (PriorityGoals).
MULTI_GOAL_STRATEGIES = frozenset({"AVERAGE_CPA_MULTIPLE_GOALS", "PAY_FOR_CONVERSION_MULTIPLE_GOALS", "MAX_PROFIT"})
# У целей Метрики номера длинные; короткие — служебные цели Директа.
_MIN_METRIKA_GOAL_ID = 1000

# Поля блока стратегии → ключ короткого вида. Деньги — в микроединицах.
_MONEY_FIELDS = {
    "Cpa": "cpa_rub",
    "AverageCpa": "cpa_rub",
    "AverageCpc": "avg_cpc_rub",
    "AverageCpi": "cpi_rub",
    "WeeklySpendLimit": "weekly_rub",
    "SpendLimit": "period_rub",
    "BidCeiling": "bid_ceiling_rub",
}
_PLAIN_FIELDS = {
    "GoalId": "goal_id",
    "Crr": "crr",
    "LimitPercent": "limit_percent",
    "StartDate": "start_date",
    "EndDate": "end_date",
}


def micros_to_rub(raw) -> float | None:
    try:
        return round(int(raw) / 1_000_000, 2)
    except (TypeError, ValueError):
        return None


def is_pay_for_conversion(kind: str) -> bool:
    return str(kind or "").startswith(("PAY_FOR_CONVERSION", "PAY_FOR_INSTALL"))


def is_metrika_goal(goal_id) -> bool:
    try:
        return int(goal_id) >= _MIN_METRIKA_GOAL_ID
    except (TypeError, ValueError):
        return False


def _read_block(block: dict, out: dict) -> None:
    for field, value in block.items():
        if value is None:
            continue
        if isinstance(value, dict):
            _read_block(value, out)  # CustomPeriodBudget: {SpendLimit, StartDate, EndDate}
        elif field in _MONEY_FIELDS:
            out[_MONEY_FIELDS[field]] = micros_to_rub(value)
        elif field == "GoalId":
            try:
                out["goal_id"] = int(value)
            except (TypeError, ValueError):
                continue
        elif field in _PLAIN_FIELDS:
            out[_PLAIN_FIELDS[field]] = value


def _side(part: dict) -> dict:
    kind = str(part.get("BiddingStrategyType") or "")
    out: dict = {"type": kind, "label": STRATEGY_LABELS.get(kind, kind)}
    for key, block in part.items():
        if key != "BiddingStrategyType" and isinstance(block, dict):
            _read_block(block, out)
    return out


def strategy_brief(bidding: dict | None) -> dict:
    """BiddingStrategy из Директа → {search, network, label, pay_for_conversion, paid_goal_ids}.

    search/network — {type, label, goal_id, cpa_rub, weekly_rub, …}: только заданные поля.
    paid_goal_ids — цели, за конверсии по которым кампания платит.
    """
    bidding = bidding or {}
    out: dict = {}
    paid: list[int] = []
    for side in ("Search", "Network"):
        part = bidding.get(side) or {}
        if not part.get("BiddingStrategyType"):
            continue
        info = _side(part)
        out[side.lower()] = info
        goal_id = info.get("goal_id")
        if is_pay_for_conversion(info["type"]) and goal_id is not None and goal_id not in paid:
            paid.append(goal_id)
    if not out:
        return {}
    out["label"] = brief_label(out)
    out["pay_for_conversion"] = any(is_pay_for_conversion((out.get(s) or {}).get("type")) for s in ("search", "network"))
    out["paid_goal_ids"] = paid
    return out


def budget_brief(bidding: dict | None) -> dict:
    """Для списка кампаний: подпись стратегии, недельный бюджет и цена цели (None — не заданы)."""
    brief = strategy_brief(bidding)
    out: dict = {"strategy_label": brief.get("label") or "", "weekly_budget_rub": None,
                 "target_cpa_rub": None, "target_cpc_rub": None}
    for side in ("search", "network"):
        info = brief.get(side) or {}
        for key, field in (("weekly_budget_rub", "weekly_rub"), ("target_cpa_rub", "cpa_rub"), ("target_cpc_rub", "avg_cpc_rub")):
            if out[key] is None and info.get(field):
                out[key] = info[field]
    return out


def brief_label(brief: dict) -> str:
    search = brief.get("search") or {}
    network = brief.get("network") or {}
    if search.get("type") == "SERVING_OFF":
        return f"Только сети: {network.get('label') or '—'}"
    name = search.get("label") or network.get("label") or ""
    if network.get("type") in (None, "", "SERVING_OFF"):
        return name
    if network.get("type") == "NETWORK_DEFAULT":
        return f"{name} + сети"
    return f"{name} + сети: {network.get('label')}"


def priority_goals_from_api(raw) -> list[dict]:
    """PriorityGoals из Директа → [{goal_id, value_rub}] по возрастанию goal_id."""
    items = raw.get("Items") if isinstance(raw, dict) else raw
    out = []
    for item in items or []:
        try:
            goal_id = int(item.get("GoalId"))
        except (TypeError, ValueError, AttributeError):
            continue
        out.append({"goal_id": goal_id, "value_rub": micros_to_rub(item.get("Value"))})
    out.sort(key=lambda g: g["goal_id"])
    return out


def uses_priority_goals(bidding: dict | None) -> bool:
    """Стратегия держится на ключевых целях кампании: GoalId 13 или стратегия по нескольким целям.

    Без PriorityGoals с целью, отличной от вовлечённых сессий, Директ такую стратегию не примет.
    """
    for side in ("Search", "Network"):
        part = (bidding or {}).get(side) or {}
        if part.get("BiddingStrategyType") in MULTI_GOAL_STRATEGIES:
            return True
        for block in part.values():
            if isinstance(block, dict) and str(block.get("GoalId")) == str(KEY_GOALS_GOAL_ID):
                return True
    return False


def campaign_goal_ids(bidding: dict | None, priority_goals: list[dict] | None) -> list[int]:
    """Цели Метрики кампании: оплачиваемые в стратегии и ключевые."""
    brief = strategy_brief(bidding)
    out: list[int] = []
    for goal_id in [*brief.get("paid_goal_ids", []), *[g.get("goal_id") for g in priority_goals or []]]:
        if is_metrika_goal(goal_id) and int(goal_id) not in out:
            out.append(int(goal_id))
    return out


def goal_label(goal_id, names: dict[int, str] | None = None) -> str:
    try:
        goal_id = int(goal_id)
    except (TypeError, ValueError):
        return str(goal_id)
    if goal_id in SPECIAL_GOALS:
        return f"{goal_id} ({SPECIAL_GOALS[goal_id]})"
    name = (names or {}).get(goal_id)
    return f"{goal_id} «{name}»" if name else str(goal_id)
