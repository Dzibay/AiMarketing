"""Копия кампании: стратегия из campaigns.get приводится к полям campaigns.add."""

from app.actions import _copy_body, _strategy_for_add


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
