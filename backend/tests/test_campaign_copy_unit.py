"""Копия кампании: стратегия и ключевые цели из campaigns.get приводятся к полям campaigns.add."""

import copy

import pytest

from app import actions
from app.actions import _add_copy, _copy_body, _strategy_for_add
from app.yandex import YandexError

METRICA_TAG = {"Option": "ADD_METRICA_TAG", "Value": "YES"}
KEY_GOALS = {
    "BiddingStrategyType": "WB_MAXIMUM_CONVERSION_RATE",
    "WbMaximumConversionRate": {"GoalId": 13, "WeeklySpendLimit": 5_000_000_000},
}
OPTION_ERROR = {"Code": 4000, "Message": "Параметры запроса указаны неверно", "Details": "Опция недоступна"}


def test_pay_for_conversion_multiple_goals_drops_budget_type():
    """get отдаёт BudgetType, add его не знает — из-за этого падало создание копии."""
    raw = {
        "Search": {
            "BiddingStrategyType": "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
            "PayForConversionMultipleGoals": {
                "WeeklySpendLimit": 30_000_000_000,
                "BudgetType": "WEEKLY_BUDGET",
                "CustomPeriodBudget": None,
            },
        },
        "Network": {"BiddingStrategyType": "SERVING_OFF"},
    }
    out = _strategy_for_add(raw)
    block = out["Search"]["PayForConversionMultipleGoals"]
    assert "BudgetType" not in block
    assert "CustomPeriodBudget" not in block
    assert block["WeeklySpendLimit"] == 30_000_000_000
    assert out["Network"] == {"BiddingStrategyType": "SERVING_OFF"}


def test_custom_period_keeps_period_budget_only():
    raw = {
        "Search": {
            "BiddingStrategyType": "PAY_FOR_CONVERSION",
            "PayForConversion": {
                "Cpa": 500_000_000,
                "GoalId": 42,
                "WeeklySpendLimit": 10_000_000_000,
                "BudgetType": "CUSTOM_PERIOD_BUDGET",
                "CustomPeriodBudget": {
                    "SpendLimit": 50_000_000_000,
                    "StartDate": "2026-11-01",
                    "EndDate": "2026-11-30",
                    "AutoContinue": "NO",
                },
            },
        },
        "Network": {"BiddingStrategyType": "NETWORK_DEFAULT"},
    }
    block = _strategy_for_add(raw)["Search"]["PayForConversion"]
    assert "BudgetType" not in block
    assert "WeeklySpendLimit" not in block
    assert block["CustomPeriodBudget"]["SpendLimit"] == 50_000_000_000
    assert block["Cpa"] == 500_000_000


def test_default_exploration_budget_is_omitted():
    raw = {
        "Search": {"BiddingStrategyType": "SERVING_OFF"},
        "Network": {
            "BiddingStrategyType": "AVERAGE_CPA",
            "AverageCpa": {
                "AverageCpa": 400_000_000,
                "GoalId": 7,
                "WeeklySpendLimit": 8_000_000_000,
                "BudgetType": "WEEKLY_BUDGET",
                "ExplorationBudget": {
                    "MinimumExplorationBudget": 0,
                    "IsMinimumExplorationBudgetCustom": "NO",
                },
            },
        },
    }
    block = _strategy_for_add(raw)["Network"]["AverageCpa"]
    assert "BudgetType" not in block
    assert "ExplorationBudget" not in block
    assert block["GoalId"] == 7


def test_custom_exploration_budget_is_kept():
    raw = {
        "Search": {
            "BiddingStrategyType": "MAX_PROFIT",
            "MaxProfit": {
                "WeeklySpendLimit": 12_000_000_000,
                "BudgetType": "WEEKLY_BUDGET",
                "ExplorationBudget": {
                    "MinimumExplorationBudget": 1_000_000_000,
                    "IsMinimumExplorationBudgetCustom": "YES",
                },
            },
        },
        "Network": {"BiddingStrategyType": "SERVING_OFF"},
    }
    block = _strategy_for_add(raw)["Search"]["MaxProfit"]
    assert block["ExplorationBudget"]["IsMinimumExplorationBudgetCustom"] == "YES"
    assert "BudgetType" not in block


def test_copy_body_sends_strategy_without_budget_type():
    item = {
        "StartDate": "2020-01-01",
        "TimeZone": "Europe/Moscow",
        "TextCampaign": {
            "BiddingStrategy": {
                "Search": {
                    "BiddingStrategyType": "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
                    "PlacementTypes": {"SearchResults": "YES", "ProductGallery": "NO", "DynamicPlaces": "YES"},
                    "PayForConversionMultipleGoals": {
                        "WeeklySpendLimit": 30_000_000_000,
                        "BudgetType": "WEEKLY_BUDGET",
                    },
                },
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            },
            "Settings": [{"Option": "ADD_METRICA_TAG", "Value": "YES"}, {"Option": "UNKNOWN_OPTION", "Value": "YES"}],
        },
    }
    body, manual = _copy_body(item, "TEXT_CAMPAIGN", name="Поиск+РСЯ (РФ) (копия тест)", counter_id=None)
    block = body["TextCampaign"]["BiddingStrategy"]["Search"]["PayForConversionMultipleGoals"]
    assert "BudgetType" not in block
    assert block["WeeklySpendLimit"] == 30_000_000_000
    assert body["TextCampaign"]["BiddingStrategy"]["Search"]["PlacementTypes"]["SearchResults"] == "YES"
    assert manual is False
    assert body["Name"] == "Поиск+РСЯ (РФ) (копия тест)"


def test_copy_body_keeps_key_goals_without_value():
    """Цели с нулевой ценностью отсеивались — и стратегия по ключевым целям уходила в Директ без целей."""
    item = {
        "StartDate": "2020-01-01",
        "TextCampaign": {
            "BiddingStrategy": {"Search": KEY_GOALS, "Network": {"BiddingStrategyType": "SERVING_OFF"}},
            "PriorityGoals": {
                "Items": [
                    {"GoalId": 12, "Value": 0, "IsMetrikaSourceOfValue": "NO"},
                    {"GoalId": 98765432, "Value": 0, "IsMetrikaSourceOfValue": "NO"},
                    {"GoalId": 55555555, "Value": 300_000_000, "IsMetrikaSourceOfValue": "NO"},
                ]
            },
        },
    }
    body, _ = _copy_body(item, "TEXT_CAMPAIGN", name="Копия", counter_id=None)
    # Признак «ценность из Метрики» не у стратегии с долей расходов не передаём.
    assert body["TextCampaign"]["PriorityGoals"]["Items"] == [
        {"GoalId": 12, "Value": 0},
        {"GoalId": 98765432, "Value": 0},
        {"GoalId": 55555555, "Value": 300_000_000},
    ]


def test_copy_body_crr_goals_carry_metrika_source():
    """У стратегий с долей расходов add требует признак «ценность из Метрики» у каждой цели."""
    crr = {"BiddingStrategyType": "AVERAGE_CRR", "AverageCrr": {"Crr": 20, "GoalId": 13, "WeeklySpendLimit": 5_000_000_000}}
    item = {
        "TextCampaign": {
            "BiddingStrategy": {"Search": crr, "Network": {"BiddingStrategyType": "SERVING_OFF"}},
            "PriorityGoals": {
                "Items": [{"GoalId": 111111, "Value": 0, "IsMetrikaSourceOfValue": "YES"}, {"GoalId": 222222, "Value": 500_000_000}]
            },
        },
    }
    body, _ = _copy_body(item, "TEXT_CAMPAIGN", name="Копия", counter_id=None)
    assert body["TextCampaign"]["PriorityGoals"]["Items"] == [
        {"GoalId": 111111, "Value": 0, "IsMetrikaSourceOfValue": "YES"},
        {"GoalId": 222222, "Value": 500_000_000, "IsMetrikaSourceOfValue": "NO"},
    ]


def _body(search: dict, settings: list[dict], goals: list[dict] | None = None) -> dict:
    block = {"BiddingStrategy": {"Search": search, "Network": {"BiddingStrategyType": "SERVING_OFF"}}, "Settings": settings}
    if goals:
        block["PriorityGoals"] = {"Items": goals}
    return {"Name": "Копия", "TextCampaign": block}


def _fake_add(monkeypatch, *answers) -> list[dict]:
    """campaigns.add отдаёт ответы по очереди; возвращает, что ушло в Директ при каждом вызове."""
    sent: list[dict] = []
    queue = list(answers)

    def call(token, client_login, method, params):
        sent.append(copy.deepcopy(params["Campaigns"][0]))
        return queue.pop(0)

    monkeypatch.setattr(actions, "_call_campaigns", call)
    return sent


def _failed(error: dict = OPTION_ERROR) -> dict:
    return {"AddResults": [{"Errors": [error]}]}


def test_add_copy_retry_keeps_goals_the_strategy_needs(monkeypatch):
    """Запасная попытка снимала и ключевые цели — Директ отвечал, что для стратегии по ключевым целям их нет."""
    goals = [{"GoalId": 55555555, "Value": 300_000_000}]
    body = _body(KEY_GOALS, [METRICA_TAG, {"Option": "ENABLE_SITE_MONITORING", "Value": "YES"}], goals)
    sent = _fake_add(monkeypatch, _failed(), {"AddResults": [{"Id": 777}]})
    warnings: list[str] = []
    assert _add_copy("token", "login", body, "TextCampaign", warnings) == 777
    assert sent[1]["TextCampaign"]["PriorityGoals"] == {"Items": goals}
    assert sent[1]["TextCampaign"]["Settings"] == [METRICA_TAG]
    assert warnings == ["Часть настроек не перенесена (Параметры запроса указаны неверно: Опция недоступна)"]


def test_add_copy_shows_direct_error_when_nothing_to_simplify(monkeypatch):
    body = _body(KEY_GOALS, [METRICA_TAG], [{"GoalId": 55555555, "Value": 300_000_000}])
    sent = _fake_add(monkeypatch, _failed())
    warnings: list[str] = []
    with pytest.raises(YandexError) as err:
        _add_copy("token", "login", body, "TextCampaign", warnings)
    assert err.value.message == "Параметры запроса указаны неверно: Опция недоступна"
    assert len(sent) == 1
    assert warnings == []


def test_add_copy_drops_goals_the_strategy_does_not_use(monkeypatch):
    clicks = {"BiddingStrategyType": "WB_MAXIMUM_CLICKS", "WbMaximumClicks": {"WeeklySpendLimit": 5_000_000_000}}
    body = _body(clicks, [METRICA_TAG], [{"GoalId": 55555555, "Value": 300_000_000}])
    sent = _fake_add(monkeypatch, _failed(), {"AddResults": [{"Id": 778}]})
    warnings: list[str] = []
    assert _add_copy("token", "login", body, "TextCampaign", warnings) == 778
    assert "PriorityGoals" not in sent[1]["TextCampaign"]
    assert len(warnings) == 1
