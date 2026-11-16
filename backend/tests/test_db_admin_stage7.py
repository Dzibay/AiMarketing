"""Этап 7 админки: платежи, обращения, «Кому написать», финансы, сайт и воронка, качество, проекты, система, уведомления."""

import pytest
from fastapi.testclient import TestClient

from app import admin_alerts, admin_finance, admin_outreach, admin_payments, admin_site, admin_support
from app import telegram_notify as tg
from app.main import app
from tests.dbutil import admin_headers, make_plan, make_user

client = TestClient(app)


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _pay(pool, uid, plan, amount, *, months=1, refunded=0, test=False, status="succeeded", ago=0, receipt=False):
    return _sql(
        pool,
        """
        INSERT INTO payments (user_id, plan_id, plan_name, days, months, kind, amount_rub, status, refunded_rub, test,
                              receipt_done, paid_at, created_at, applied_at)
        VALUES (%s, %s, %s, 30, %s, 'renew', %s, %s, %s, %s, %s, NOW() - %s * INTERVAL '1 day',
                NOW() - %s * INTERVAL '1 day', CASE WHEN %s = 'succeeded' THEN NOW() END)
        RETURNING id
        """,
        (uid, plan["id"], plan["name"], months, amount, status, refunded, test, receipt, ago, ago, status),
    ).fetchone()["id"]


def _msg(pool, uid, author, body, minutes_ago):
    thread = _sql(
        pool,
        """
        INSERT INTO support_threads (user_id) VALUES (%s)
        ON CONFLICT (user_id) DO UPDATE SET updated_at = NOW() RETURNING id
        """,
        (uid,),
    ).fetchone()["id"]
    _sql(
        pool,
        "INSERT INTO support_messages (thread_id, author, body, created_at) VALUES (%s, %s, %s, NOW() - %s * INTERVAL '1 minute')",
        (thread, author, body, minutes_ago),
    )


@pytest.fixture
def world(db):
    pro = make_plan(name="Про", price_rub=990, days=30)
    anna = make_user(db, "anna@example.com")
    boris = make_user(db, "boris@example.com")
    first = _pay(db, anna, pro, 990, ago=2, receipt=True)
    _pay(db, anna, pro, 2970, months=3, ago=1)
    _pay(db, boris, pro, 990, refunded=990)
    _pay(db, boris, pro, 500, test=True)
    _pay(db, boris, pro, 990, status="canceled")
    return {"pro": pro, "anna": anna, "boris": boris, "first": first}


def test_payments_search_filters_and_totals(world):
    out = admin_payments.build()
    # Тестовый скрыт по умолчанию; итоги — по выборке: возврат вычитается.
    assert out["total"]["count"] == 4
    assert {p["email"] for p in out["items"]} == {"anna@example.com", "boris@example.com"}
    assert not any(p["test"] for p in out["items"])

    assert {p["user_id"] for p in admin_payments.build(q="anna")["items"]} == {world["anna"]}
    assert [p["id"] for p in admin_payments.build(q=str(world["first"]))["items"]] == [world["first"]]
    assert len(admin_payments.build(test="only")["items"]) == 1
    assert {p["months"] for p in admin_payments.build(months=3)["items"]} == {3}

    no_receipt = admin_payments.build(no_receipt=True)["items"]
    assert world["first"] not in {p["id"] for p in no_receipt}
    assert all(p["status"] == "succeeded" for p in no_receipt)

    r = client.get("/api/admin/payments/list", params={"q": "boris", "status": "refunded"}, headers=admin_headers())
    assert r.status_code == 200
    assert [p["refunded_rub"] for p in r.json()["items"]] == [990]


def test_support_inbox_puts_waiting_first(world, db):
    _msg(db, world["anna"], "user", "Привет", 60)
    _msg(db, world["anna"], "admin", "Здравствуйте", 30)
    _msg(db, world["boris"], "user", "Не работает", 10)

    out = admin_support.inbox(filter="all")
    assert [t["user_id"] for t in out["items"]][0] == world["boris"]
    assert out["items"][0]["waiting"] is True
    assert admin_support.waiting_count() == 1
    assert [t["user_id"] for t in admin_support.inbox()["items"]] == [world["boris"]]

    h = admin_headers()
    assert client.get("/api/admin/me", headers=h).json()["support_waiting"] == 1
    card = client.get(f"/api/admin/support/user/{world['anna']}", headers=h).json()
    assert [m["body"] for m in card["thread"]["messages"]] == ["Привет", "Здравствуйте"]


def test_outreach_lists(world):
    lists = admin_outreach.counts()
    assert len(lists) == 7
    for item in lists:
        r = client.get(f"/api/admin/outreach/{item['key']}", headers=admin_headers())
        assert r.status_code == 200, item["key"]
        assert r.json()["total"] == item["count"]
    assert client.get("/api/admin/outreach/nope", headers=admin_headers()).status_code == 404


def test_finance_months(world, db):
    _sql(
        db,
        "INSERT INTO ai_usage (user_id, kind, task, model, model_class, charged, cost_rub) VALUES (%s, 'model', 'chat', 'gpt', 'standard', 1, 100)",
        (world["anna"],),
    )
    out = admin_finance.build("6")
    assert len(out["months"]) == 6
    t = out["total"]
    # 990 + 2970 + возвращённые 990; тестовый и отменённый не считаются.
    assert t["revenue_rub"] == 4950
    assert t["refunded_rub"] == 990
    assert t["net_rub"] == 3960
    assert t["ai_rub"] == 100
    assert t["margin_rub"] == 3860
    assert {r["months"] for r in out["by_term"]} == {1, 3}
    assert out["by_plan"][0]["plan"] == "Про"


def test_site_and_funnel(world, db):
    _sql(
        db,
        "INSERT INTO site_visits (visitor_id, day, user_id, landing_page, device) VALUES ('v1', CURRENT_DATE, %s, '/', 'desktop'), ('v2', CURRENT_DATE, NULL, '/price', 'mobile')",
        (world["anna"],),
    )
    site = admin_site.site("30d")
    assert site["numbers"]["visitors"] == 2
    assert site["numbers"]["from_site"] == 1
    assert {r["key"] for r in site["pages"]} == {"/", "/price"}

    steps = {s["key"]: s for s in admin_site.funnel("30d")["steps"]}
    assert steps["visit"]["count"] == 2
    assert steps["register"]["count"] == 2
    assert steps["payment"]["count"] == 2  # возврат не отменяет того, что человек дошёл до оплаты
    assert steps["register"]["of_prev"] == 100.0


def test_other_pages_answer(world):
    h = admin_headers()
    for path in ("/api/admin/site/tools", "/api/admin/quality", "/api/admin/projects", "/api/admin/system", "/api/admin/finance?range=all"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, path
    system = client.get("/api/admin/system", headers=h).json()
    assert {s["key"] for s in system["services"]} >= {"queue", "telegram", "database"}
    assert any(s["name"] == "alerts" for s in system["schedules"])


@pytest.fixture
def telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "support_configured", lambda: True)
    monkeypatch.setattr(tg, "telegram_configured", lambda: True)
    monkeypatch.setattr(tg, "create_support_topic", lambda name: 77 if name == "Оплаты" else 88)
    monkeypatch.setattr(tg, "send_to_support_topic", lambda topic, text, **kw: sent.append((topic, text)) or 1)
    return sent


def test_alert_settings_and_test_send(world, telegram):
    h = admin_headers()
    s = client.get("/api/admin/alerts/settings", headers=h).json()
    assert s["configured"] is True
    assert all(k["enabled"] for k in s["kinds"])

    s = client.put("/api/admin/alerts/settings", json={"kinds": {"ai_spend": False, "junk": True}}, headers=h).json()
    assert {k["key"]: k["enabled"] for k in s["kinds"]}["ai_spend"] is False
    assert admin_alerts.send("ai_spend", "x") is False

    assert client.post("/api/admin/alerts/test", json={"kind": "payment"}, headers=h).json() == {"ok": True}
    assert client.post("/api/admin/alerts/test", json={"kind": "payment"}, headers=h).json() == {"ok": True}
    assert [t for t, _ in telegram] == [77, 77]
    assert client.post("/api/admin/alerts/test", json={"kind": "nope"}, headers=h).status_code == 400


def test_alerts_throttle_and_checks(world, db, telegram):
    assert admin_alerts.send("yookassa", "a") is True
    assert admin_alerts.send("yookassa", "b") is False  # то же правило — не чаще раза в час
    assert admin_alerts.send("yookassa", "c", rule="other") is True

    _sql(db, "UPDATE payments SET paid_at = NOW() - INTERVAL '2 days', receipt_done = FALSE WHERE id = %s", (world["first"],))
    _sql(db, "INSERT INTO jobs (kind, status, run_after) VALUES ('sync_account', 'queued', NOW() - INTERVAL '30 minutes')")
    assert set(admin_alerts.check_all()) == {"no_receipt", "stuck_job"}
    assert admin_alerts.check_all() == []  # повтор — через час
    texts = [t for _, t in telegram]
    assert any("без чека" in t for t in texts)
    assert all(topic == 88 for topic, _ in telegram)


def test_payment_alert_first_and_repeat(world, db, telegram):
    pay = dict(_sql(db, "SELECT * FROM payments WHERE id = %s", (world["first"],)).fetchone())
    admin_alerts.payment(pay)
    later = _sql(db, "SELECT * FROM payments WHERE user_id = %s AND months = 3", (world["anna"],)).fetchone()
    admin_alerts.payment(dict(later))
    test_pay = _sql(db, "SELECT * FROM payments WHERE test").fetchone()
    admin_alerts.payment(dict(test_pay))
    assert len(telegram) == 2
    assert "первая" in telegram[0][1] and "повторная" in telegram[1][1]
    assert "3 месяца" in telegram[1][1]
