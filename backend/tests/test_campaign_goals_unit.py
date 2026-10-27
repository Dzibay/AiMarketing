"""Цели и стратегии кампаний: короткий вид стратегии, отчёт по целям, изменения между синками."""

from datetime import date

from app.direct_sync import _changes
from app.stats import goal_columns, parse_goal_day_rows
from app.strategy_info import (
    brief_label,
    campaign_goal_ids,
    goal_label,
    micros_to_rub,
    priority_goals_from_api,
    strategy_brief,
)

ORDER, CALL = 5001, 5002


def pay_for(goal_id: int, cpa_micros: int = 500_000_000) -> dict:
    return {
        "Search": {
            "BiddingStrategyType": "PAY_FOR_CONVERSION",
            "PayForConversion": {"Cpa": cpa_micros, "GoalId": goal_id, "WeeklySpendLimit": 10_000_000_000},
        },
        "Network": {"BiddingStrategyType": "SERVING_OFF"},
    }


def test_micros_to_rub():
    assert micros_to_rub(1_500_000) == 1.5
    assert micros_to_rub("500000000") == 500.0
    assert micros_to_rub(None) is None
    assert micros_to_rub("abc") is None


def test_strategy_brief_pay_for_conversion():
    brief = strategy_brief(pay_for(ORDER))
    assert brief["search"] == {"type": "PAY_FOR_CONVERSION", "label": "Оплата за конверсии",
                               "cpa_rub": 500.0, "goal_id": ORDER, "weekly_rub": 10_000.0}
    assert brief["network"] == {"type": "SERVING_OFF", "label": "Не показывать"}
    assert brief["label"] == "Оплата за конверсии"
    assert brief["pay_for_conversion"] is True
    assert brief["paid_goal_ids"] == [ORDER]


def test_strategy_brief_labels():
    max_conv = {
        "BiddingStrategyType": "WB_MAXIMUM_CONVERSION_RATE",
        "WbMaximumConversionRate": {"GoalId": 13, "WeeklySpendLimit": 3_000_000_000, "BidCeiling": None},
    }
    both = strategy_brief({"Search": max_conv, "Network": {"BiddingStrategyType": "NETWORK_DEFAULT"}})
    assert both["label"] == "Максимум конверсий + сети"
    assert both["search"]["weekly_rub"] == 3000.0
    assert "bid_ceiling_rub" not in both["search"]  # пустые поля не переносим
    assert both["pay_for_conversion"] is False
    assert both["paid_goal_ids"] == []  # цель есть, но платим за клики

    network = strategy_brief({"Search": {"BiddingStrategyType": "SERVING_OFF"}, "Network": max_conv})
    assert network["label"] == "Только сети: Максимум конверсий"
    manual = {"search": {"label": "Ручные ставки"}, "network": {"type": "MAXIMUM_COVERAGE", "label": "Ручные ставки"}}
    assert brief_label(manual) == "Ручные ставки + сети: Ручные ставки"
    assert strategy_brief({"Search": {"BiddingStrategyType": "NEW_TYPE"}})["label"] == "NEW_TYPE"
    assert strategy_brief(None) == {}
    assert strategy_brief({"Search": {}}) == {}


def test_priority_goals_and_campaign_goal_ids():
    raw = {"Items": [{"GoalId": CALL, "Value": 1_000_000}, {"GoalId": "плохая"}, {"GoalId": ORDER, "Value": None}]}
    goals = priority_goals_from_api(raw)
    assert goals == [{"goal_id": ORDER, "value_rub": None}, {"goal_id": CALL, "value_rub": 1.0}]
    assert priority_goals_from_api(raw["Items"]) == goals
    assert priority_goals_from_api(None) == []
    # Оплачиваемая цель первой, повторы убраны.
    assert campaign_goal_ids(pay_for(CALL), goals) == [CALL, ORDER]
    # Служебные цели Директа (0, 12, 13) — не цели Метрики: в отчёт по целям их не передать.
    assert campaign_goal_ids(pay_for(13), [{"goal_id": 12}]) == []


def test_goal_label():
    assert goal_label(13) == "13 (ключевые цели кампании)"
    assert goal_label(ORDER, {ORDER: "Заказ"}) == "5001 «Заказ»"
    assert goal_label(CALL, {ORDER: "Заказ"}) == "5002"
    assert goal_label("нет") == "нет"


def test_goal_columns_take_first_attribution_model():
    raw = [{"Date": "2026-10-25", "CampaignId": "11", "Cost": "10", "Conversions_5001_LSC": "1",
            "Revenue_5001_LSC": "0", "Conversions_5001_FC": "3", "Conversions_5002_LSC": "--"}]
    assert goal_columns(raw) == {
        ORDER: {"model": "LSC", "Conversions": "Conversions_5001_LSC", "Revenue": "Revenue_5001_LSC"},
        CALL: {"model": "LSC", "Conversions": "Conversions_5002_LSC"},
    }
    assert goal_columns([]) == {}


def test_goal_day_rows_merge_and_skip_zeros():
    raw = [
        {"Date": "2026-10-25", "CampaignId": "11", "Conversions_5001_LSC": "2", "Revenue_5001_LSC": "1500.5",
         "Conversions_5002_LSC": "--", "Revenue_5002_LSC": "--"},
        {"Date": "2026-10-25", "CampaignId": "11", "Conversions_5001_LSC": "1", "Revenue_5001_LSC": "0",
         "Conversions_5002_LSC": "0", "Revenue_5002_LSC": "0"},
        {"Date": "2026-10-26", "CampaignId": "12", "Conversions_5001_LSC": "0", "Revenue_5001_LSC": "0",
         "Conversions_5002_LSC": "4", "Revenue_5002_LSC": "--"},
        {"Date": "плохая дата", "CampaignId": "11", "Conversions_5001_LSC": "9"},
        {"Date": "2026-10-26", "CampaignId": "0", "Conversions_5001_LSC": "9"},
    ]
    assert parse_goal_day_rows(raw) == [
        {"day": date(2026, 10, 25), "campaign_id": 11, "goal_id": ORDER, "conversions": 3, "revenue_kopecks": 150050},
        {"day": date(2026, 10, 26), "campaign_id": 12, "goal_id": CALL, "conversions": 4, "revenue_kopecks": 0},
    ]


def test_changes_between_syncs():
    old = {"name": "Поиск", "state": "ON", "status": "ACCEPTED", "daily_budget_kopecks": 0,
           "bidding_strategy": pay_for(ORDER), "priority_goals": [{"goal_id": ORDER, "value_rub": None}],
           "attribution_model": "LSC"}
    new = {**old, "state": "SUSPENDED", "daily_budget_kopecks": None, "bidding_strategy": pay_for(ORDER, 300_000_000),
           "priority_goals": [{"goal_id": CALL, "value_rub": None}], "attribution_model": "AUTO"}
    changes = {field: (before, after) for field, before, after in _changes(old, new)}
    assert set(changes) == {"state", "strategy", "priority_goals", "attribution_model"}  # бюджет 0 → нет — не изменение
    assert changes["state"] == ("ON", "SUSPENDED")
    assert (changes["strategy"][0]["search"]["cpa_rub"], changes["strategy"][1]["search"]["cpa_rub"]) == (500.0, 300.0)
    assert changes["priority_goals"] == ([{"goal_id": ORDER, "value_rub": None}], [{"goal_id": CALL, "value_rub": None}])
    assert changes["attribution_model"] == ("LSC", "AUTO")
    assert _changes(old, dict(old)) == []


def test_first_sync_with_strategy_and_goals_is_not_a_change():
    """Стратегию и цели раньше не хранили: первый синк после обновления их только записывает."""
    old = {"name": "Поиск", "state": "ON", "status": "ACCEPTED", "daily_budget_kopecks": 100_000,
           "bidding_strategy": {}, "priority_goals": [], "attribution_model": ""}
    new = {**old, "bidding_strategy": pay_for(ORDER), "priority_goals": [{"goal_id": ORDER, "value_rub": None}],
           "attribution_model": "LSC"}
    assert _changes(old, new) == []
    # Директ не отдал поля целей (None): записанные цели не считаем изменившимися.
    assert _changes(new, {**new, "priority_goals": None, "attribution_model": None}) == []
