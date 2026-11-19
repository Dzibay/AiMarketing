"""Бюджет одной кампании: вид и сумма по сохранённой стратегии, минимумы Директа и что уходит в правку.

Директ не вызываем: чтение и запись настроек кампании подменены.
"""

from datetime import date

import pytest

from app import actions, budgets, campaign_budget
from app.campaign_budget import budget_change_args, budget_period, budget_state, change_budget
from app.yandex import YandexError

MICRO = 1_000_000


def _auto_row(weekly_rub=14000, ctype="TEXT_CAMPAIGN", state="ON"):
    block = {"WeeklySpendLimit": weekly_rub * MICRO} if weekly_rub else {}
    return {
        "id": 7,
        "account_id": 1,
        "campaign_id": 701,
        "name": "Поиск",
        "campaign_type": ctype,
        "state": state,
        "daily_budget_kopecks": None,
        "bidding_strategy": {
            "Search": {"BiddingStrategyType": "WB_MAXIMUM_CLICKS", "WbMaximumClicks": block},
            "Network": {"BiddingStrategyType": "SERVING_OFF"},
        },
    }


def _manual_row(daily_rub=2000, weekly_rub=None, ctype="TEXT_CAMPAIGN"):
    search = {"BiddingStrategyType": "HIGHEST_POSITION"}
    if weekly_rub:
        search["HighestPosition"] = {"WeeklySpendLimit": weekly_rub * MICRO}
    return {
        "id": 8,
        "account_id": 1,
        "campaign_id": 801,
        "name": "Ручные",
        "campaign_type": ctype,
        "state": "ON",
        "daily_budget_kopecks": daily_rub * 100 if daily_rub else None,
        "bidding_strategy": {"Search": search, "Network": {"BiddingStrategyType": "SERVING_OFF"}},
    }


def test_auto_strategy_has_weekly_amount():
    state = budget_state(_auto_row(14000))
    assert state["kind"] == "weekly"
    assert state["amount_rub"] == 14000
    assert state["editable"] is True
    assert state["min_rub"] == budgets.MIN_WEEKLY_RUB
    assert state["strategy_label"] == "Максимум кликов"


def test_auto_strategy_without_limit_has_no_amount():
    state = budget_state(_auto_row(None))
    assert state["kind"] == "weekly"
    assert state["amount_rub"] is None
    assert state["editable"] is True


def test_manual_text_campaign_is_daily():
    state = budget_state(_manual_row(daily_rub=2000))
    assert state["kind"] == "daily"
    assert state["amount_rub"] == 2000
    assert state["min_rub"] == budgets.MIN_DAILY_RUB


def test_manual_unified_campaign_with_weekly_limit():
    state = budget_state(_manual_row(daily_rub=None, weekly_rub=7000, ctype="UNIFIED_CAMPAIGN"))
    assert state["kind"] == "weekly"
    assert state["amount_rub"] == 7000


def test_unsupported_type_and_archived_are_not_editable():
    assert budget_state(_auto_row(ctype="MOBILE_APP_CAMPAIGN"))["editable"] is False
    assert budget_state(_auto_row(state="ARCHIVED"))["editable"] is False
    empty = budget_state({"campaign_type": "TEXT_CAMPAIGN", "bidding_strategy": None})
    assert empty["kind"] is None and empty["editable"] is False and empty["amount_rub"] is None


def test_period_week_starts_monday():
    wednesday = date(2026, 11, 18)
    assert budget_period("weekly", wednesday) == (date(2026, 11, 16), wednesday)
    assert budget_period("daily", wednesday) == (wednesday, wednesday)
    monday = date(2026, 11, 16)
    assert budget_period("weekly", monday) == (monday, monday)


def test_change_args_respect_direct_minimums():
    assert budget_change_args("weekly", 14000) == {"weekly_budget_rub": 14000.0}
    assert budget_change_args("daily", "2000") == {"daily_budget_rub": 2000.0}
    with pytest.raises(YandexError) as exc:
        budget_change_args("weekly", 299)
    assert "недельный бюджет меньше 300" in exc.value.message
    with pytest.raises(YandexError) as exc:
        budget_change_args("daily", 100)
    assert "дневной бюджет меньше 300" in exc.value.message
    for bad in (None, "abc", float("nan"), float("inf")):
        with pytest.raises(YandexError) as exc:
            budget_change_args("weekly", bad)
        assert exc.value.message == "Укажите сумму"
    with pytest.raises(YandexError) as exc:
        budget_change_args(None, 1000)
    assert exc.value.code == "strategy"


@pytest.fixture
def direct(monkeypatch):
    """Подмена Директа и журнала: настройки кампании — из строки, записи — в список."""
    state = {"row": _auto_row(14000), "updates": [], "logs": []}

    def settings_of(row):
        flat = actions.strategy_from_api(row["bidding_strategy"])
        daily = row["daily_budget_kopecks"] / 100 if row["daily_budget_kopecks"] else None
        return {"type": row["campaign_type"], "name": row["name"], "daily_budget_rub": daily, "strategy": flat}

    monkeypatch.setattr(campaign_budget, "_campaign_row", lambda user_id, cid: state["row"])
    monkeypatch.setattr(
        campaign_budget, "budget_info", lambda user_id, cid, today=None: {"campaign_row_id": cid, "reloaded": True}
    )
    monkeypatch.setattr(actions, "get_campaign_settings", lambda user_id, cid: settings_of(state["row"]))

    def update(user_id, cid, changes, **kwargs):
        state["updates"].append((cid, changes))
        return {"status": "ok"}

    monkeypatch.setattr(actions, "update_campaign_settings", update)

    def log(*args, **kwargs):
        state["logs"].append((args, kwargs))
        return 501

    monkeypatch.setattr(budgets, "log_action", log)
    return state


def test_change_weekly_budget_payload(direct):
    res = change_budget(1, 3, 7, weekly_budget_rub=20000)
    [(cid, changes)] = direct["updates"]
    assert cid == 7
    assert list(changes) == ["strategy"]
    assert changes["strategy"]["weekly_budget_rub"] == 20000.0
    assert res["changed"] is True
    assert res["info"] == {"campaign_row_id": 7, "reloaded": True}
    # Одна запись в «Истории» с откатом к прежней сумме, в нужном проекте.
    [(args, kwargs)] = direct["logs"]
    assert args[1] == "budget"
    assert kwargs["workspace_id"] == 3
    [step] = kwargs["undo"]["steps"]
    assert step["op"] == "campaign_settings"
    assert step["args"]["campaign_row_id"] == 7
    assert step["args"]["changes"]["strategy"]["weekly_budget_rub"] == 14000


def test_change_daily_budget_payload(direct):
    direct["row"] = _manual_row(daily_rub=2000)
    res = change_budget(1, 3, 8, daily_budget_rub=3500)
    assert direct["updates"] == [(8, {"daily_budget_rub": 3500.0})]
    assert res["changed"] is True


def test_same_amount_changes_nothing(direct):
    res = change_budget(1, 3, 7, weekly_budget_rub=14000)
    assert direct["updates"] == []
    assert res["changed"] is False


def test_change_rejects_bad_requests(direct):
    with pytest.raises(YandexError):
        change_budget(1, 3, 7)
    with pytest.raises(YandexError):
        change_budget(1, 3, 7, weekly_budget_rub=1000, daily_budget_rub=500)
    with pytest.raises(YandexError) as exc:
        change_budget(1, 3, 7, weekly_budget_rub=200)
    assert "меньше 300" in exc.value.message
    direct["row"] = _auto_row(ctype="MOBILE_APP_CAMPAIGN")
    with pytest.raises(YandexError) as exc:
        change_budget(1, 3, 7, weekly_budget_rub=5000)
    assert exc.value.code == "unsupported"
    assert direct["updates"] == []


def test_spend_without_vat():
    # Директ держит лимит без НДС, отчёты — с НДС 22 %: 17 080 ₽ с НДС — это ровно лимит 14 000 ₽.
    assert campaign_budget.VAT_RATE == 0.22
    assert campaign_budget.net_of_vat(1_708_000) == 1_400_000
    assert campaign_budget.net_of_vat(0) == 0
    assert campaign_budget.net_of_vat(None) is None
