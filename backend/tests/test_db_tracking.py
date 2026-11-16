"""Учёт для админки (этап 2): заходы и действия пользователей, визиты сайта, переходы по реф-ссылкам по дням,
расход ИИ лендинга, ошибки ИИ."""

from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import activity, ai, events, journal, referrals, site_visits
from app.clock import today_msk
from app.config import settings
from app.main import app
from app.web_auth import make_session
from tests.dbutil import make_user, setup_ai

PRICES = {
    "standard": {"units_in": 100, "units_out": 400, "cost_in": 50, "cost_out": 200},
    "fast": {"units_in": 10, "units_out": 40, "cost_in": 5, "cost_out": 20},
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
VISITOR = "a" * 32


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")
    activity.forget()
    site_visits._hits.clear()
    yield
    activity.forget()


def _day(db, user):
    with db.connection() as c:
        return c.execute("SELECT * FROM user_activity_days WHERE user_id = %s AND day = %s", (user, today_msk())).fetchone()


# --- заходы и действия ---


def test_visit_counted_once_per_five_minutes(db):
    user = make_user(db)
    activity.touch(user)
    activity.touch(user)
    activity.forget()  # другой процесс: память пуста, но в БД заход свежий
    activity.touch(user)
    row = _day(db, user)
    assert (row["visits"], row["actions"]) == (1, 0)

    with db.connection() as c:
        c.execute("UPDATE users SET last_seen_at = NOW() - INTERVAL '6 minutes' WHERE id = %s", (user,))
    activity.forget()
    activity.touch(user)
    assert _day(db, user)["visits"] == 2
    with db.connection() as c:
        seen = c.execute("SELECT last_seen_at > NOW() - INTERVAL '1 minute' AS fresh FROM users WHERE id = %s", (user,)).fetchone()
    assert seen["fresh"]


def test_cabinet_request_is_a_visit(db):
    user = make_user(db)
    client = TestClient(app)
    assert client.get("/api/web/me", headers={"Authorization": f"Bearer {make_session(user)}"}).status_code == 200
    assert _day(db, user)["visits"] == 1


def test_actions_from_events_and_user_edits_only(db):
    user = make_user(db)
    events.record(user, "ai_question")
    events.record(user, "project_created")
    events.record(user, "project_created", auto=True)  # создан сам — не действие человека
    events.record(user, "login")  # не действие
    assert _day(db, user)["actions"] == 2

    journal.log_action(user, "campaign_edit", {}, {}, "done")
    with journal.acting_as("ai"):
        journal.log_action(user, "campaign_edit", {}, {}, "done")
    with journal.acting_as("rule"):
        journal.log_action(user, "campaign_edit", {}, {}, "done")
    journal.log_action(user, "campaign_edit", {}, {}, "error")
    row = _day(db, user)
    assert (row["visits"], row["actions"]) == (0, 3)


def test_history_backfilled_from_events_chats_and_edits(db):
    """0052 на живой базе: дни с событиями, вопросами в чатах и правками человека — заходы и действия."""
    from alembic import command
    from alembic.config import Config

    from app.db import ALEMBIC_INI, run_migrations

    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["configure_logging"] = False
    command.downgrade(cfg, "0051_ai_reserves")
    try:
        user = make_user(db)
        with db.connection() as c:
            c.execute(
                """
                INSERT INTO user_events (user_id, kind, created_at) VALUES
                    (%(u)s, 'login', '2026-09-01 10:00+03'),
                    (%(u)s, 'ai_question', '2026-09-01 12:00+03'),
                    (%(u)s, 'project_created', '2026-09-03 09:00+03')
                """,
                {"u": user},
            )
            c.execute(
                """
                INSERT INTO action_logs (user_id, action, payload, result, status, source, created_at)
                VALUES (%s, 'campaign_edit', '{}', '{}', 'done', 'user', '2026-09-03 18:00+03'),
                       (%s, 'campaign_edit', '{}', '{}', 'done', 'rule', '2026-09-04 18:00+03')
                """,
                (user, user),
            )
    finally:
        run_migrations()
    with db.connection() as c:
        days = c.execute(
            "SELECT day::text, visits, actions FROM user_activity_days WHERE user_id = %s ORDER BY day", (user,)
        ).fetchall()
        seen = c.execute("SELECT last_seen_at FROM users WHERE id = %s", (user,)).fetchone()["last_seen_at"]
    assert [(d["day"], d["visits"], d["actions"]) for d in days] == [("2026-09-01", 1, 1), ("2026-09-03", 1, 2)]
    assert seen == datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)  # последняя активность — правка человека


def test_tracking_boundary_is_stored(db):
    with db.connection() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key = 'activity_tracked_since'").fetchone()
    assert row is not None and str(row["value"]).strip('"')[:4].isdigit()


# --- визиты сайта ---


def _visits(db):
    with db.connection() as c:
        return c.execute("SELECT * FROM site_visits ORDER BY id").fetchall()


def test_site_visit_pages_and_new_visit_after_silence(db):
    kw = {"visitor_id": VISITOR, "user_agent": UA, "own_host": "tolk-ads.ru"}
    assert site_visits.record(
        path="/price?x=1#top", referrer="https://www.yandex.ru/search?q=1",
        utm={"utm_source": "direct", "utm_campaign": "kitchens", "junk": "x"}, ref="Partner-1", **kw,
    ) == "new"
    assert site_visits.record(path="/about", referrer="https://tolk-ads.ru/price", **kw) == "page"
    rows = _visits(db)
    assert len(rows) == 1
    first = rows[0]
    assert (first["landing_page"], first["referrer_host"], first["utm_source"], first["utm_campaign"]) == (
        "/price", "yandex.ru", "direct", "kitchens")
    assert (first["ref_code"], first["device"], first["pages"], first["day"]) == ("partner-1", "desktop", 2, today_msk())

    with db.connection() as c:
        c.execute("UPDATE site_visits SET last_at = NOW() - INTERVAL '31 minutes'")
    assert site_visits.record(path="/", **kw) == "new"
    assert len(_visits(db)) == 2


def test_site_visit_skips_bots_bad_ids_and_floods(db, monkeypatch):
    assert site_visits.record(visitor_id=VISITOR, path="/", user_agent="Googlebot/2.1") == "skip"
    assert site_visits.record(visitor_id=VISITOR, path="/", user_agent="") == "skip"
    assert site_visits.record(visitor_id="short", path="/", user_agent=UA) == "skip"
    monkeypatch.setattr(site_visits, "IP_PER_MINUTE", 2)
    results = [site_visits.record(visitor_id=VISITOR, path="/", user_agent=UA, ip="1.2.3.4") for _ in range(3)]
    assert results == ["new", "page", "skip"]
    assert len(_visits(db)) == 1


def test_attach_user_saves_first_source(db):
    kw = {"visitor_id": VISITOR, "user_agent": UA, "own_host": "tolk-ads.ru"}
    site_visits.record(path="/blog/minus-slova", referrer="https://vc.ru/x", utm={"utm_source": "vc"}, **kw)
    with db.connection() as c:
        c.execute("UPDATE site_visits SET last_at = NOW() - INTERVAL '2 hours', started_at = NOW() - INTERVAL '2 hours'")
    site_visits.record(path="/register", **kw)
    user = make_user(db)
    assert site_visits.attach_user(user, VISITOR)
    assert not site_visits.attach_user(user, "b" * 32)  # первый источник не перебивается
    with db.connection() as c:
        u = c.execute("SELECT visitor_id, first_landing, first_referrer, first_utm FROM users WHERE id = %s", (user,)).fetchone()
    assert (u["visitor_id"], u["first_landing"], u["first_referrer"], u["first_utm"]) == (
        VISITOR, "/blog/minus-slova", "vc.ru", {"utm_source": "vc"})
    assert {r["user_id"] for r in _visits(db)} == {user}
    # Следующий визит этого браузера — сразу с пользователем.
    with db.connection() as c:
        c.execute("UPDATE site_visits SET last_at = NOW() - INTERVAL '1 hour'")
    site_visits.record(path="/", **kw)
    assert _visits(db)[-1]["user_id"] == user


def test_visit_endpoint_always_ok(db):
    client = TestClient(app)
    body = {"visitor": VISITOR, "path": "/login", "referrer": "", "utm": {}, "ref": ""}
    assert client.post("/api/site/visit", json=body, headers={"User-Agent": UA}).json() == {"ok": True}
    assert client.post("/api/site/visit", json={**body, "visitor": "?"}, headers={"User-Agent": UA}).json() == {"ok": True}
    assert [r["landing_page"] for r in _visits(db)] == ["/login"]


def test_ua_and_referrer_helpers():
    assert site_visits.device("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile/15E148") == "mobile"
    assert site_visits.device("Mozilla/5.0 (Linux; Android 14; SM-X710) Safari") == "tablet"
    assert site_visits.device("Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari") == "mobile"
    assert site_visits.device(UA) == "desktop"
    assert site_visits.is_bot("curl/8.4") and site_visits.is_bot("YandexBot/3.0") and not site_visits.is_bot(UA)
    assert site_visits._referrer_host("https://www.tolk-ads.ru/a", "tolk-ads.ru:443") == ""
    assert site_visits._referrer_host("not a url", "x") == ""


# --- реф-ссылки по дням ---


def test_ref_clicks_per_day(db):
    link = referrals.save_link(None, {"code": "promo", "name": "Промо"})
    assert referrals.visit("promo") and referrals.visit("PROMO")
    assert not referrals.visit("nope")
    with db.connection() as c:
        rows = c.execute("SELECT day, clicks FROM ref_link_clicks_daily WHERE link_id = %s", (link["id"],)).fetchall()
    assert [(r["day"], r["clicks"]) for r in rows] == [(today_msk(), 2)]


# --- ИИ: расход лендинга, ссылка на чат, длительность, ошибки ---


def _ok(url, **kw):
    data = {"choices": [{"message": {"content": "готов"}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
    return httpx.Response(200, json=data, request=httpx.Request("POST", url))


def _usage(db):
    with db.connection() as c:
        return c.execute("SELECT * FROM ai_usage ORDER BY id").fetchall()


def test_landing_ai_spend_is_recorded_without_user(db, monkeypatch):
    setup_ai(model="gpt-smart", fast="gpt-fast", prices=PRICES)
    monkeypatch.setattr(ai.httpx, "post", _ok)
    for task in ("site_check", "site_tools"):
        ai.complete(ai.load_config(None, task), [{"role": "user", "content": "проверь"}])
    rows = _usage(db)
    assert [(r["user_id"], r["source"], r["task"]) for r in rows] == [(None, "landing", "site_check"), (None, "landing", "site_tools")]
    assert all(r["duration_ms"] is not None and r["duration_ms"] >= 0 for r in rows)

    # Проверка модели из админки — не расход сервиса.
    ai.complete(ai.load_config(None, "test"), [{"role": "user", "content": "ping"}])
    assert len(_usage(db)) == 2


def test_cabinet_usage_keeps_chat_link(db, monkeypatch):
    user = make_user(db)
    setup_ai(model="gpt-smart", fast="gpt-fast", prices=PRICES)
    monkeypatch.setattr(ai.httpx, "post", _ok)
    with db.connection() as c:
        chat = c.execute("INSERT INTO ai_chats (user_id) VALUES (%s) RETURNING id", (user,)).fetchone()["id"]
    cfg = {**ai.load_config(user, "chat"), "chat_id": chat}
    ai.complete(cfg, [{"role": "user", "content": "привет"}])
    row = _usage(db)[-1]
    assert (row["user_id"], row["source"], row["chat_id"]) == (user, "cabinet", chat)


def test_ai_errors_logged_through_retries_and_fallback(db, monkeypatch):
    user = make_user(db)
    setup_ai(model="gpt-smart", fast="gpt-fast", prices=PRICES)
    monkeypatch.setattr(ai, "_down", {})

    smart = []

    def post(url, **kw):
        if kw["json"]["model"] != "gpt-smart":
            return _ok(url, **kw)
        smart.append(1)
        if len(smart) == 1:  # временный сбой — повтор той же модели
            return httpx.Response(503, json={"error": {"message": "перегружен"}}, request=httpx.Request("POST", url))
        # модели нет — переход на запасную
        return httpx.Response(404, json={"error": {"code": "model_not_found", "message": "нет модели"}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", post)
    assert ai.complete(ai.load_config(user, "chat"), [{"role": "user", "content": "привет"}])["content"] == "готов"
    with db.connection() as c:
        rows = c.execute("SELECT * FROM ai_errors ORDER BY id").fetchall()
    assert rows and all(r["user_id"] == user and r["model"] == "gpt-smart" and r["source"] == "cabinet" for r in rows)
    assert all(r["answered"] and r["answered_by"] == "gpt-fast" for r in rows)
    assert [(r["code"], r["retried"], r["fallback"]) for r in rows] == [("ai-error", True, False), ("ai-model", False, True)]

    with db.connection() as c:
        c.execute("UPDATE ai_errors SET created_at = NOW() - INTERVAL '91 days' WHERE id = %s", (rows[0]["id"],))
    assert ai.purge_errors() == 1


def test_ai_error_without_answer(db, monkeypatch):
    user = make_user(db)
    setup_ai(model="gpt-smart", prices=PRICES)
    monkeypatch.setattr(ai, "_down", {})
    monkeypatch.setattr(
        ai.httpx, "post",
        lambda url, **kw: httpx.Response(400, json={"error": {"message": "плохой запрос"}}, request=httpx.Request("POST", url)),
    )
    with pytest.raises(Exception):
        ai.complete(ai.load_config(user, "chat"), [{"role": "user", "content": "привет"}])
    with db.connection() as c:
        rows = c.execute("SELECT answered, answered_by, message FROM ai_errors").fetchall()
    assert rows and not rows[-1]["answered"] and rows[-1]["answered_by"] == "" and "плохой запрос" in rows[-1]["message"]
