"""Бюджет одной кампании на настоящем Postgres: расход за неделю / сегодня и правка через API (Директ подменён)."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import actions, campaign_budget, stats
from app.campaign_budget import budget_info
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

WEDNESDAY = date(2026, 11, 18)
MONDAY = date(2026, 11, 16)
MICRO = 1_000_000


def _stat(user, account, campaign, day, cost_rub):
    stats.upsert_direct_stat(
        user_id=user,
        day=day,
        account_id=account,
        campaign_id=campaign,
        impressions=10,
        clicks=1,
        cost_kopecks=cost_rub * 100,
        conversions=0,
    )


def _strategy(db, row_id, bidding, ctype="TEXT_CAMPAIGN", daily_kopecks=None):
    with db.connection() as conn:
        conn.execute(
            """
            UPDATE direct_campaigns SET bidding_strategy = %s, campaign_type = %s, daily_budget_kopecks = %s
            WHERE id = %s
            """,
            (Jsonb(bidding), ctype, daily_kopecks, row_id),
        )


AUTO = {
    "Search": {"BiddingStrategyType": "WB_MAXIMUM_CLICKS", "WbMaximumClicks": {"WeeklySpendLimit": 14000 * MICRO}},
    "Network": {"BiddingStrategyType": "SERVING_OFF"},
}
MANUAL = {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}, "Network": {"BiddingStrategyType": "SERVING_OFF"}}


def _seed(db):
    user = make_user(db)
    connection = make_connection(db, user)
    acc = make_account(db, user, connection, login="shop")
    other_acc = make_account(db, user, connection, login="other")
    weekly = make_campaign(db, user, acc, 501, "Поиск")
    daily = make_campaign(db, user, acc, 502, "Ручные")
    quiet = make_campaign(db, user, other_acc, 601, "Тихая")
    _strategy(db, weekly, AUTO)
    _strategy(db, daily, MANUAL, daily_kopecks=200_000)
    _strategy(db, quiet, AUTO)
    ws = make_workspace(db, user, 901, "Магазин", accounts=[acc, other_acc], campaigns=[weekly, daily, quiet])
    return {"user": user, "acc": acc, "other_acc": other_acc, "weekly": weekly, "daily": daily, "quiet": quiet, "ws": ws}


def test_weekly_spend_counts_from_monday(db):
    s = _seed(db)
    _stat(s["user"], s["acc"], 501, MONDAY - timedelta(days=1), 9000)  # прошлое воскресенье — не в счёт
    _stat(s["user"], s["acc"], 501, MONDAY, 3000)
    _stat(s["user"], s["acc"], 501, WEDNESDAY, 1500)
    _stat(s["user"], s["acc"], 502, WEDNESDAY, 700)  # другая кампания того же кабинета

    info = budget_info(s["user"], s["weekly"], today=WEDNESDAY)
    assert info["kind"] == "weekly" and info["period"] == "week"
    assert info["amount_rub"] == 14000
    assert (info["date_from"], info["date_to"]) == ("2026-11-16", "2026-11-18")
    assert info["spent_kopecks"] == 450_000
    assert info["has_data"] is True
    assert info["editable"] is True
    assert info["updated_at"]

    day = budget_info(s["user"], s["daily"], today=WEDNESDAY)
    assert day["kind"] == "daily" and day["period"] == "day"
    assert day["amount_rub"] == 2000
    assert (day["date_from"], day["date_to"]) == ("2026-11-18", "2026-11-18")
    assert day["spent_kopecks"] == 70_000


def test_no_stats_means_no_percent(db):
    s = _seed(db)
    _stat(s["user"], s["acc"], 501, WEDNESDAY, 1500)  # статистика есть, но у другого кабинета
    info = budget_info(s["user"], s["quiet"], today=WEDNESDAY)
    assert info["has_data"] is False
    assert info["spent_kopecks"] is None
    assert info["updated_at"] is None

    # Кабинет загружен, а кампания за неделю не тратила — честные 0 ₽.
    _stat(s["user"], s["other_acc"], 602, MONDAY, 100)
    info = budget_info(s["user"], s["quiet"], today=WEDNESDAY)
    assert info["has_data"] is True
    assert info["spent_kopecks"] == 0
    assert info["spent_net_kopecks"] == 0


def test_fresh_sync_without_rows_is_zero(db):
    """Весь кабинет стоял: отчёт Директа пустой. После сегодняшней загрузки это 0 ₽, а не «не загрузился»."""
    s = _seed(db)
    with db.connection() as conn:
        # Загрузка была позавчера — неделю она не покрывает, процента нет.
        conn.execute(
            "UPDATE direct_accounts SET stats_synced_at = '2026-11-16 12:00+03' WHERE id = %s", (s["other_acc"],)
        )
    stale = budget_info(s["user"], s["quiet"], today=WEDNESDAY)
    assert stale["has_data"] is False and stale["spent_kopecks"] is None

    with db.connection() as conn:
        conn.execute(
            "UPDATE direct_accounts SET stats_synced_at = '2026-11-18 09:30+03' WHERE id = %s", (s["other_acc"],)
        )
    fresh = budget_info(s["user"], s["quiet"], today=WEDNESDAY)
    assert fresh["has_data"] is True
    assert fresh["spent_kopecks"] == 0 and fresh["spent_net_kopecks"] == 0
    assert fresh["updated_at"].startswith("2026-11-18")


def test_spend_without_vat_for_the_limit(db):
    """Расход в отчётах с НДС, лимит Директа — без: долю бюджета считаем по сумме без НДС."""
    s = _seed(db)
    _stat(s["user"], s["acc"], 501, MONDAY, 17080)  # весь лимит 14 000 ₽ без НДС = 17 080 ₽ с НДС 22 %
    info = budget_info(s["user"], s["weekly"], today=WEDNESDAY)
    assert info["spent_kopecks"] == 1_708_000
    assert info["spent_net_kopecks"] == 1_400_000


def test_foreign_campaign_is_not_found(db):
    s = _seed(db)
    stranger = make_user(db, email="x@example.com")
    try:
        budget_info(stranger, s["weekly"], today=WEDNESDAY)
    except YandexError as exc:
        assert exc.code == "not-found"
    else:
        raise AssertionError("чужая кампания открылась")


def test_api_reads_and_changes_budget_with_undo(db, monkeypatch):
    s = _seed(db)
    monkeypatch.setattr(campaign_budget, "today_msk", lambda: WEDNESDAY)
    sent = []

    def settings(user_id, row_id):
        return {
            "type": "TEXT_CAMPAIGN",
            "name": "Поиск",
            "daily_budget_rub": None,
            "strategy": actions.strategy_from_api(AUTO),
        }

    def update(user_id, row_id, changes, **kwargs):
        sent.append((row_id, changes))
        return {"status": "ok"}

    monkeypatch.setattr(actions, "get_campaign_settings", settings)
    monkeypatch.setattr(actions, "update_campaign_settings", update)

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    url = f"/api/web/workspaces/{s['ws']}/campaigns/{s['weekly']}/budget"

    info = client.get(url, headers=auth).json()
    assert info["kind"] == "weekly" and info["amount_rub"] == 14000

    too_small = client.post(url, headers=auth, json={"weekly_budget_rub": 200})
    assert too_small.status_code == 400
    assert "меньше 300" in too_small.json()["detail"]["message"]
    assert client.post(url, headers=auth, json={}).status_code == 400
    assert sent == []

    res = client.post(url, headers=auth, json={"weekly_budget_rub": 20000})
    assert res.status_code == 200
    body = res.json()
    assert body["changed"] is True
    assert sent[0][0] == s["weekly"]
    assert sent[0][1]["strategy"]["weekly_budget_rub"] == 20000
    assert res.headers.get("x-undo-log")
    with db.connection() as conn:
        log = conn.execute(
            "SELECT action, workspace_id, undo FROM action_logs WHERE id = %s", (int(res.headers["x-undo-log"]),)
        ).fetchone()
    assert log["action"] == "budget"
    assert log["workspace_id"] == s["ws"]
    assert log["undo"]["steps"][0]["args"]["changes"]["strategy"]["weekly_budget_rub"] == 14000

    other = make_user(db, email="y@example.com")
    foreign = client.get(url, headers={"Authorization": f"Bearer {make_session(other)}"})
    assert foreign.status_code == 404
