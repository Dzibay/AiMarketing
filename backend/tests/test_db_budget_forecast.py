"""Бюджет месяца на главной проекта (I27): прогноз по дням недели и поле бюджета на настоящем Postgres."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import stats
from app.analytics import workspace_summary
from app.main import app
from app.overview import budget_pace, build_overview, weekday_profile
from app.web_auth import make_session
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

TODAY = date(2026, 11, 19)  # четверг


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr("app.overview.today_msk", lambda: TODAY)
    monkeypatch.setattr("app.analytics.today_msk", lambda: TODAY)


def _stat(user, account, campaign, day, cost_kopecks):
    stats.upsert_direct_stat(
        user_id=user,
        day=day,
        account_id=account,
        campaign_id=campaign,
        impressions=100,
        clicks=10,
        cost_kopecks=cost_kopecks,
        conversions=0,
        revenue_kopecks=0,
    )


def _seed(db, *, history_days: int, budget_rub: float | None = 1000):
    """Проект с одной кампанией: будни по 50 ₽, выходные по 10 ₽ за history_days дней, сегодня 20 ₽."""
    user = make_user(db)
    connection = make_connection(db, user, "owner")
    acc = make_account(db, user, connection, name="Директ", login="owner")
    camp = make_campaign(db, user, acc, 7, "Поиск")
    daily = {}
    for i in range(1, history_days + 1):
        d = TODAY - timedelta(days=i)
        daily[d] = 1000 if d.weekday() >= 5 else 5000
        _stat(user, acc, 7, d, daily[d])
    _stat(user, acc, 7, TODAY, 2000)
    ws = make_workspace(db, user, 501, "Сайт", accounts=[acc], campaigns=[camp], monthly_budget_rub=budget_rub)
    return user, ws, daily


def test_summary_forecast_counts_weekdays(db, today):
    user, ws, daily = _seed(db, history_days=50)
    budget = workspace_summary(user, ws)["row"]["budget"]
    before = sum(c for d, c in daily.items() if d >= TODAY.replace(day=1))
    expected = budget_pace(100_000, before + 2000, before, TODAY, profile=weekday_profile(daily, TODAY))
    assert budget["forecast_method"] == "weekdays"
    assert budget["forecast_kopecks"] == expected["forecast_kopecks"] == 114_000
    assert budget["spent_kopecks"] == before + 2000
    assert budget["status"] == "over"
    assert (budget["day"], budget["days_in_month"]) == (19, 30)


def test_budget_signal_shows_the_same_forecast(db, today):
    user, ws, _ = _seed(db, history_days=50)
    summary = workspace_summary(user, ws)
    signal = next(p for p in summary["problems"] if p["kind"] == "budget")
    # Поровну было бы ~1 200 ₽, по дням недели — ~1 100 ₽: сигнал и блок говорят одно.
    assert "~1 100 ₽" in signal["title"].replace(" ", " ")


def test_short_history_splits_evenly(db, today):
    user, ws, _ = _seed(db, history_days=20)
    budget = workspace_summary(user, ws)["row"]["budget"]
    assert budget["forecast_method"] == "even"
    assert budget["forecast_kopecks"] == round(70_000 / 18 * 30)


def test_no_budget_then_set_it_from_the_block(db, today):
    user, ws, _ = _seed(db, history_days=50, budget_rub=None)
    assert workspace_summary(user, ws)["row"]["budget"] is None
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    saved = client.patch(f"/api/web/workspaces/{ws}", json={"monthly_budget_rub": 1200}, headers=auth)
    assert saved.status_code == 200 and saved.json()["monthly_budget_rub"] == 1200
    budget = client.get(f"/api/web/workspaces/{ws}/summary", headers=auth).json()["row"]["budget"]
    assert budget["budget_kopecks"] == 120_000 and budget["forecast_method"] == "weekdays"
    assert budget["status"] == "ok"


def test_other_projects_spend_is_not_counted(db, today):
    user, ws, daily = _seed(db, history_days=50)
    with db.connection() as conn:
        connection = conn.execute("SELECT id FROM yandex_connections WHERE user_id = %s", (user,)).fetchone()["id"]
    other_acc = make_account(db, user, connection, name="Чужой", login="other")
    other = make_campaign(db, user, other_acc, 8, "Другое")
    make_workspace(db, user, 502, "Другой сайт", accounts=[other_acc], campaigns=[other])
    for d in daily:
        _stat(user, other_acc, 8, d, 900_000)
    budget = workspace_summary(user, ws)["row"]["budget"]
    assert budget["forecast_kopecks"] == 114_000


def test_one_forecast_when_even_split_would_warn(db, today):
    """Поровну вышло бы ~1 167 ₽ при бюджете 1 050 ₽ (перерасход), по дням недели — ~1 140 ₽ (в норме).
    Сигнал «Где горит», сводка проекта и обзор (а с ним строка «как дела» и срочные оповещения) берут
    один прогноз — по дням недели: сигнала о перерасходе нет нигде."""
    user, ws, _ = _seed(db, history_days=50, budget_rub=1050)
    overview = build_overview(user, TODAY)
    row = next(r for r in overview["workspaces"] if r["id"] == ws)
    assert row["budget"]["forecast_method"] == "weekdays"
    assert row["budget"]["forecast_kopecks"] == 114_000
    assert row["budget"]["status"] == "ok"
    assert not [p for p in overview["problems"] if p["kind"] == "budget"]
    summary = workspace_summary(user, ws)
    assert summary["row"]["budget"] == row["budget"]
    assert not [p for p in summary["problems"] if p["kind"] == "budget"]


def test_overview_signal_uses_weekday_forecast(db, today):
    user, ws, _ = _seed(db, history_days=50)
    overview = build_overview(user, TODAY)
    signal = next(p for p in overview["problems"] if p["kind"] == "budget")
    assert "~1 100 ₽" in signal["title"].replace(" ", " ")
    assert signal["metric"] == {"reasons": ["over"]}
