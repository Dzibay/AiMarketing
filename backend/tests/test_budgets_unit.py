import pytest

from app.budgets import _previous, budget_kind, budget_plan, validate_args
from app.yandex import YandexError


def _auto(weekly=None, cpa=None, search="AVERAGE_CPA", ctype="TEXT_CAMPAIGN"):
    strategy = {"search": search, "network": "SERVING_OFF", "weekly_budget_rub": weekly, "average_cpa_rub": cpa}
    return {"type": ctype, "name": "A", "daily_budget_rub": None, "strategy": strategy}


def _manual(daily=None, weekly=None, ctype="TEXT_CAMPAIGN"):
    strategy = {"search": "HIGHEST_POSITION", "network": "SERVING_OFF", "weekly_budget_rub": weekly, "manual": True}
    return {"type": ctype, "name": "M", "daily_budget_rub": daily, "strategy": strategy}


def test_budget_kind():
    assert budget_kind(_auto(weekly=5000)) == "weekly"
    assert budget_kind(_manual(daily=1000)) == "daily"
    assert budget_kind(_manual(weekly=7000, ctype="UNIFIED_CAMPAIGN")) == "weekly"
    assert budget_kind(_manual(daily=1000, ctype="UNIFIED_CAMPAIGN")) == "daily"
    assert budget_kind({"strategy": {"search": "SOMETHING_ELSE"}}) == ""


def test_weekly_set_and_daily_converted():
    plan = budget_plan(_auto(weekly=5000), weekly_rub=7000)
    assert plan["changes"]["strategy"]["weekly_budget_rub"] == 7000
    assert "manual" not in plan["changes"]["strategy"]

    plan = budget_plan(_auto(weekly=5000), daily_rub=1000)
    assert plan["changes"]["strategy"]["weekly_budget_rub"] == 7000
    assert "× 7" in plan["note"]

    plan = budget_plan(_manual(daily=1000), weekly_rub=14000)
    assert plan["changes"] == {"daily_budget_rub": 2000}
    assert "÷ 7" in plan["note"]


def test_pct_with_cap():
    plan = budget_plan(_auto(weekly=10000), budget_pct=50, cap_weekly_rub=12000)
    assert plan["changes"]["strategy"]["weekly_budget_rub"] == 12000
    plan = budget_plan(_auto(weekly=10000), budget_pct=-50, cap_weekly_rub=8000)
    assert plan["changes"]["strategy"]["weekly_budget_rub"] == 8000
    # Дневной бюджет: потолок недели делится на 7.
    plan = budget_plan(_manual(daily=1000), budget_pct=100, cap_weekly_rub=10500)
    assert plan["changes"] == {"daily_budget_rub": 1500}


def test_unchanged_when_same():
    plan = budget_plan(_auto(weekly=5000), weekly_rub=5000)
    assert plan["changes"] == {}


def test_minimums_and_missing_budget():
    with pytest.raises(YandexError):
        budget_plan(_auto(weekly=5000), weekly_rub=200)
    with pytest.raises(YandexError):
        budget_plan(_manual(daily=1000), weekly_rub=1400)  # 200 ₽/день
    with pytest.raises(YandexError):
        budget_plan(_auto(weekly=None), budget_pct=10)


def test_target_pct():
    plan = budget_plan(_auto(weekly=5000, cpa=1000), target_pct=-10)
    assert plan["changes"]["strategy"]["average_cpa_rub"] == 900
    assert plan["changes"]["strategy"]["weekly_budget_rub"] == 5000
    assert "CPA" in plan["after"]
    with pytest.raises(YandexError):
        budget_plan(_auto(weekly=5000, search="WB_MAXIMUM_CLICKS"), target_pct=10)


def test_small_target_keeps_kopecks():
    """CPC 0,90 + 20 % — «0,90 ₽ → 1,08 ₽», а не «1 ₽ → 1 ₽»."""
    settings = {"type": "TEXT_CAMPAIGN", "strategy": {"search": "AVERAGE_CPC", "weekly_budget_rub": 5000, "average_cpc_rub": 0.9}}
    plan = budget_plan(settings, target_pct=20)
    assert plan["changes"]["strategy"]["average_cpc_rub"] == 1.08
    assert plan["before"] == "CPC 0,90 ₽" and plan["after"] == "CPC 1,08 ₽"


def test_previous_values_for_undo():
    settings = _auto(weekly=5000, cpa=1000)
    plan = budget_plan(settings, weekly_rub=7000)
    prev = _previous(settings, plan["changes"])
    assert prev["strategy"]["weekly_budget_rub"] == 5000
    settings = _manual(daily=1000)
    prev = _previous(settings, budget_plan(settings, daily_rub=1500)["changes"])
    assert prev == {"daily_budget_rub": 1000}


def test_validate_args():
    validate_args(5000, None, None, None)
    validate_args(None, None, None, -10)
    with pytest.raises(YandexError):
        validate_args(5000, 1000, None, None)
    with pytest.raises(YandexError):
        validate_args(None, None, None, None)
    with pytest.raises(YandexError):
        validate_args(None, None, 900, None)
