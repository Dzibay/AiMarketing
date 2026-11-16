"""Дашборд админки: период и прошлый отрезок, карточки, удержание, ряды графиков, срез «сейчас», полоса состояния."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import admin_dashboard, admin_users, plans
from app.clock import MSK, now_msk
from app.main import app
from tests.dbutil import admin_headers, make_plan, make_user


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _pay(pool, uid, plan, amount, paid_at, last_day, *, refunded=0, test=False):
    _sql(
        pool,
        """
        INSERT INTO payments (user_id, plan_id, plan_name, days, kind, amount_rub, status, refunded_rub, test, paid_at, last_day)
        VALUES (%s, %s, %s, 30, 'renew', %s, 'succeeded', %s, %s, %s, %s)
        """,
        (uid, plan["id"], plan["name"], amount, refunded, test, paid_at, last_day),
    )


@pytest.fixture
def world(db):
    admin_dashboard.clear_cache()
    now = now_msk()
    today = now.date()
    pro = make_plan(name="Про", price_rub=990, days=30)
    trial = make_plan(name="Пробный", price_rub=0, days=7, is_trial=True)

    a = make_user(db, "a@example.com")
    b = make_user(db, "b@example.com")
    c = make_user(db, "c@example.com")
    d = make_user(db, "d@example.com")
    for uid, ago in ((a, 2), (b, 40), (c, 0), (d, 10)):
        _sql(db, "UPDATE users SET created_at = %s WHERE id = %s", (now - timedelta(days=ago, minutes=1), uid))

    _pay(db, a, pro, 990, now - timedelta(days=1), today + timedelta(days=29))
    _pay(db, a, pro, 990, now - timedelta(days=1), today + timedelta(days=29), refunded=990)  # возвращён — не считается
    _pay(db, a, pro, 500, now - timedelta(days=1), today, test=True)  # тестовый — не считается
    _pay(db, b, pro, 990, now - timedelta(days=35), today - timedelta(days=5))
    _pay(db, b, pro, 990, now - timedelta(days=6), today + timedelta(days=2))  # раньше окончания прошлой — досрочно

    plans.set_subscription(a, plan_id=pro["id"], until=(today + timedelta(days=29)).isoformat())
    plans.set_subscription(b, plan_id=pro["id"], until=(today + timedelta(days=2)).isoformat())
    plans.set_subscription(c, plan_id=trial["id"], until=(today + timedelta(days=2)).isoformat())
    plans.set_subscription(d, plan_id=None)

    _sql(
        db,
        "INSERT INTO user_activity_days (user_id, day, visits, actions) VALUES (%s, %s, 1, 1), (%s, %s, 2, 0), (%s, %s, 1, 0)",
        (a, today, c, today, b, today - timedelta(days=3)),
    )
    asked = now - timedelta(seconds=30)
    _sql(db, "INSERT INTO user_events (user_id, kind, created_at) VALUES (%s, 'ai_question', %s), (%s, 'ai_question', %s)", (a, asked, a, asked))
    _sql(
        db,
        "INSERT INTO site_visits (visitor_id, day, started_at) VALUES ('v1', %s, %s), ('v1', %s, %s), ('v2', %s, %s)",
        (today, asked, today, asked, today, asked),
    )
    _sql(db, "INSERT INTO support_threads (user_id) VALUES (%s), (%s)", (a, b))
    _sql(
        db,
        """
        INSERT INTO support_messages (thread_id, author, body)
        SELECT id, 'user', 'вопрос' FROM support_threads WHERE user_id = %s
        """,
        (a,),
    )
    _sql(
        db,
        """
        INSERT INTO support_messages (thread_id, author, body, created_at)
        SELECT id, 'user', 'вопрос', NOW() - INTERVAL '1 hour' FROM support_threads WHERE user_id = %s
        UNION ALL
        SELECT id, 'admin', 'ответ', NOW() FROM support_threads WHERE user_id = %s
        """,
        (b, b),
    )
    yield {"now": now, "today": today, "ids": (a, b, c, d), "pro": pro}
    admin_dashboard.clear_cache()


def test_period_bounds():
    now = now_msk().replace(hour=15, minute=30)
    today = now.date()
    day = admin_dashboard.period("day", now=now)
    assert (day["from"], day["to"], day["days"]) == (today.isoformat(), today.isoformat(), 1)
    assert day["end"] == now and day["prev"]["start"] == day["start"] - timedelta(days=1)
    assert day["prev"]["end"] == now - timedelta(days=1)  # вчера до того же часа

    week = admin_dashboard.period("7d", now=now)
    assert week["from"] == (today - timedelta(days=6)).isoformat() and week["step"] == "day"
    assert week["prev"]["to"] == (today - timedelta(days=7)).isoformat()
    assert admin_dashboard.period("bogus", now=now)["preset"] == "30d"
    assert admin_dashboard.period("180d", now=now)["step"] == "week"
    assert admin_dashboard.period("180d", step="day", now=now)["step"] == "day"

    custom = admin_dashboard.period(date_from="2026-11-10", date_to="2026-11-01", now=now.replace(year=2026, month=11, day=16))
    assert (custom["preset"], custom["from"], custom["to"], custom["days"]) == ("custom", "2026-11-01", "2026-11-10", 10)
    assert custom["prev"]["from"] == "2026-10-22" and custom["prev"]["to"] == "2026-10-31"
    future = admin_dashboard.period(date_from="2026-11-10", date_to="2027-01-01", now=now.replace(year=2026, month=11, day=16))
    assert future["to"] == "2026-11-16"


def test_buckets():
    assert admin_dashboard._buckets(date(2026, 11, 1), date(2026, 11, 16), "week") == [
        date(2026, 10, 26), date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)
    ]
    assert admin_dashboard._buckets(date(2026, 9, 30), date(2026, 11, 2), "month") == [
        date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)
    ]


def test_week_numbers(world):
    out = admin_dashboard.build("7d", now=world["now"])
    cur, prev, ch = out["current"], out["previous"], out["changes"]
    assert cur["new_users"] == 2 and prev["new_users"] == 1 and ch["new_users"] == 100.0
    assert (cur["revenue"], cur["payments"], cur["payers"]) == (1980.0, 2, 2)
    assert cur["avg_check"] == 990.0 and cur["per_payer"] == 990.0
    assert out["conversion_window_days"] == 14
    assert cur["conversion"] == {"paid": 1, "registered": 2, "rate": 50.0, "growing": True}
    assert prev["conversion"]["rate"] == 0.0 and ch["conversion"] is None  # с нуля процент не считается
    assert cur["retention"] == {"endings": 1, "early": 1, "on_day": 0, "returned": 0, "rate": 100.0, "return_rate": 100.0}
    assert cur["active_users"] == 3 and cur["active_avg"] == round(3 / 7, 1)
    assert cur["questions"] == 2 and cur["questions_per_active"] == round(2 / 3, 1)
    assert (cur["visits"], cur["visitors"], cur["site_conversion"]) == (3, 2, 100.0)

    series = out["series"]
    assert len(series) == 7 and series[-1]["day"] == world["today"].isoformat()
    assert sum(p["users"] for p in series) == 2 and series[-1]["users"] == 1 and series[-3]["users"] == 1
    assert sum(p["revenue"] for p in series) == 1980.0 and series[-2]["revenue"] == 990.0
    assert (series[-1]["visited"], series[-1]["acted"], series[-4]["visited"]) == (2, 1, 1)
    assert (series[-1]["visits"], series[-1]["visitors"], series[-1]["questions"]) == (3, 2, 2)
    assert len(out["prev_series"]) == 7 and sum(p["users"] for p in out["prev_series"]) == 1


def test_now_and_status(world):
    out = admin_dashboard.build("30d", now=world["now"])
    snap = out["now"]
    assert snap["users_total"] == 4
    assert snap["subscriptions"]["paying"] == 2 and snap["subscriptions"]["trial"] == 1 and snap["subscriptions"]["none"] == 1
    assert snap["subscriptions"]["paying_pct"] == 50.0
    assert snap["subscriptions"]["by_plan"] == [{"plan_id": world["pro"]["id"], "name": "Про", "trial": False, "users": 2}]
    assert snap["ending"] == {"total": 2, "paid": 1}
    assert snap["all_time"]["revenue"] == 2970.0 and snap["all_time"]["payments_per_payer"] == 1.5
    # B оплатил через 5 дней после регистрации — тоже в окне 14 дней.
    assert snap["all_time"]["conversion"] == {"paid": 2, "registered": 4, "rate": 50.0}
    assert snap["active"] == {"today_visited": 2, "today_acted": 1, "week": 3, "month": 3}
    st = out["status"]
    assert st["support_open"] == 1 and st["no_receipt"] == 3
    keys = {s["key"]: s for s in st["services"]}
    assert keys["queue"]["ok"] and keys["bridge"]["ok"] is None


def test_all_time_and_ending_filter(world):
    out = admin_dashboard.build("all", now=world["now"])
    assert out["period"]["prev"] is None and out["previous"] is None and out["changes"] == {}
    assert out["period"]["from"] == (world["now"] - timedelta(days=40, minutes=1)).astimezone(MSK).date().isoformat()
    assert out["current"]["revenue"] == 2970.0

    a, b, c, d = world["ids"]
    ending = sorted(i["id"] for i in admin_users.list_page({"sub": "ending"})["items"])
    assert ending == sorted([b, c])
    assert [i["id"] for i in admin_users.list_page({"sub": "ending_paid"})["items"]] == [b]


def test_api_and_cache(db, world):
    client = TestClient(app)
    assert client.get("/api/admin/dashboard").status_code == 401
    h = admin_headers()
    first = client.get("/api/admin/dashboard?p=7d", headers=h).json()
    assert first["period"]["preset"] == "7d" and first["current"]["new_users"] == 2
    make_user(db, "late@example.com")
    again = client.get("/api/admin/dashboard?p=7d", headers=h).json()
    assert again["now"]["users_total"] == 4  # минуту отдаётся из памяти
    admin_dashboard.clear_cache()
    assert client.get("/api/admin/dashboard?p=7d", headers=h).json()["now"]["users_total"] == 5
    custom = client.get("/api/admin/dashboard?from=2026-01-01&to=2026-01-31&step=week", headers=h).json()
    assert custom["period"]["preset"] == "custom" and custom["period"]["step"] == "week"
