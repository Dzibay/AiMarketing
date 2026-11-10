"""Тарифы, подписки и доступ (фаза 2): лимиты по периоду, закрытие по сроку, функции, проекты, почта."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import billing, email_verify, events, mailer, plans, workspaces, yandex
from app.clock import MSK, now_msk
from app.main import app
from app.users import get_user
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import admin_headers, make_connection, make_counter, make_plan, make_user, make_workspace


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {make_session(user_id)}"}


@pytest.fixture
def no_smtp(monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: False)


@pytest.fixture
def smtp(monkeypatch):
    """SMTP «настроен», письма копятся в списке."""
    sent: list[dict] = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(
        mailer, "send", lambda to, subject, body, link="": sent.append({"to": to, "link": link}) or True
    )
    return sent


# --- Тарифы ------------------------------------------------------------------------------------


def test_plan_crud_trial_is_single_and_validated(db, no_smtp):
    trial = make_plan(name="Пробный", price_rub=0, days=7, units_limit=100, is_trial=True)
    pro = make_plan(name="Про", price_rub=2990, days=30, units_limit=5000, projects_limit=3, features={"images": False})
    assert trial["is_trial"] and pro["features"]["images"] is False and pro["features"]["wordstat"] is True

    # Второй пробный снимает отметку с первого.
    trial2 = make_plan(name="Пробный 2", days=14, is_trial=True)
    assert plans.get_plan(trial["id"])["is_trial"] is False and trial2["is_trial"]

    # Пользователю — только включённые платные, пробный не продаётся.
    # Прежний пробный стал обычным тарифом.
    assert [p["name"] for p in plans.list_plans()] == ["Пробный", "Про"]
    assert [p["name"] for p in plans.list_plans(for_admin=True)][0] == "Пробный 2"

    for bad in ({"name": ""}, {"days": 0}, {"price_rub": "дорого"}, {"projects_limit": 1.5}, {"units_limit": -1}):
        with pytest.raises(YandexError) as exc:
            plans.save_plan(None, {"name": "X", "price_rub": 1, "days": 30, **bad})
        assert exc.value.code == "plan-field"

    # Тариф с пользователями не удаляется.
    user = make_user(db)
    plans.set_subscription(user, plan_id=pro["id"])
    with pytest.raises(YandexError) as exc:
        plans.delete_plan(pro["id"])
    assert exc.value.code == "plan-in-use"
    plans.delete_plan(trial["id"])
    assert plans.get_plan(trial["id"]) is None


def test_upgrade_discounts_and_offers(db, no_smtp):
    base = make_plan(name="Старт", price_rub=1000, units_limit=1000, projects_limit=1)
    pro = make_plan(name="Про", price_rub=3000, units_limit=3000, projects_limit=5)
    unlimited = make_plan(name="Агентство", price_rub=9000, units_limit=None, projects_limit=None)
    plans.set_upgrade(base["id"], pro["id"], 20)
    assert plans.list_upgrades() == [{"from_plan_id": base["id"], "to_plan_id": pro["id"], "discount_pct": 20.0}]
    with pytest.raises(YandexError):
        plans.set_upgrade(base["id"], base["id"], 10)
    with pytest.raises(YandexError):
        plans.set_upgrade(base["id"], pro["id"], 150)

    user = make_user(db)
    plans.set_subscription(user, plan_id=base["id"])
    offers = {o["name"]: o for o in plans.offers(user)}
    assert offers["Про"]["price_with_discount"] == 2400.0 and offers["Про"]["discount_pct"] == 20.0
    assert offers["Про"]["units_factor"] == 3.0 and offers["Старт"]["current"]
    assert offers["Агентство"]["units_unlimited"] and offers["Агентство"]["price_with_discount"] == 9000.0

    # 0 — скидка снята.
    plans.set_upgrade(base["id"], pro["id"], 0)
    assert plans.list_upgrades() == []


# --- Период и сроки ----------------------------------------------------------------------------


def test_period_is_the_paid_window_and_month_without_plan():
    start = datetime(2026, 11, 1, 12, 0, tzinfo=MSK)
    # 30 дней от начала: на 45-й день идёт второй период.
    p = plans.period_of(start, start + timedelta(days=60), 30, start + timedelta(days=45))
    assert p == (start + timedelta(days=30), start + timedelta(days=60))
    # Срок кончился — показываем последний период.
    p = plans.period_of(start, start + timedelta(days=30), 30, start + timedelta(days=40))
    assert p == (start, start + timedelta(days=30))
    # Без тарифа — календарный месяц.
    now = datetime(2026, 12, 15, tzinfo=MSK)
    assert plans.period_of(start, None, None, now) == (
        datetime(2026, 12, 1, tzinfo=MSK),
        datetime(2027, 1, 1, tzinfo=MSK),
    )
    assert plans.last_day(datetime(2026, 12, 16, tzinfo=MSK)) == "2026-12-15"


def test_set_subscription_plan_until_renew_and_overrides(db, no_smtp):
    plan = make_plan(name="Про", days=30, units_limit=1000, projects_limit=2)
    user = make_user(db)

    # Без подписки — ограничений нет.
    sub = plans.subscription(user)
    assert sub["plan"] is None and sub["units_limit"] is None and sub["state"] == "ok"

    view = plans.set_subscription(user, plan_id=plan["id"])
    assert view["plan"]["name"] == "Про" and view["units_limit"] == 1000.0 and view["projects_limit"] == 2
    ends = plans.subscription(user)["ends_at"]
    assert timedelta(days=29) < ends - now_msk() <= timedelta(days=30)

    # Продление — с конца текущего срока.
    plans.set_subscription(user, renew=True)
    assert plans.subscription(user)["ends_at"] - ends == timedelta(days=30)

    # Правки поверх тарифа: лимит, проекты, функция; null — без лимита.
    plans.set_subscription(user, overrides={"units_limit": None, "projects_limit": 5, "features": {"images": False}})
    sub = plans.subscription(user)
    assert sub["units_limit"] is None and sub["projects_limit"] == 5 and sub["features"]["images"] is False
    assert sub["features"]["wordstat"] is True

    # Срок задним числом закрывает доступ; без срока — открывает.
    plans.set_subscription(user, until=(now_msk() - timedelta(days=3)).date().isoformat())
    assert plans.subscription(user)["state"] == "expired"
    plans.set_subscription(user, until=None)
    assert plans.subscription(user)["state"] == "ok" and plans.subscription(user)["ends_at"] is None

    with pytest.raises(YandexError):
        plans.set_subscription(user, until="15.12.2026")
    with pytest.raises(YandexError):
        plans.set_subscription(user, plan_id=999999)
    with pytest.raises(YandexError):
        plans.set_subscription(999999, overrides={})

    # Каждая правка — в журнале.
    assert [e["kind"] for e in events.list_events(user)].count("plan_changed") == 5


def test_renew_after_expiry_starts_today_and_resumes_sync(db, no_smtp, monkeypatch):
    plan = make_plan(days=30)
    user = make_user(db)
    make_connection(db, user, "anna")
    plans.set_subscription(user, plan_id=plan["id"], until=(now_msk() - timedelta(days=1)).date().isoformat())
    assert plans.subscription(user)["expired"]

    submitted = []
    monkeypatch.setattr("app.job_handlers.submit", lambda kind, uid, payload, **kw: submitted.append((kind, payload)))
    plans.set_subscription(user, renew=True)
    sub = plans.subscription(user)
    assert not sub["expired"] and timedelta(days=29) < sub["ends_at"] - now_msk() <= timedelta(days=30)
    # Догрузка данных за время простоя.
    assert [k for k, _ in submitted] == ["sync_connection"] and submitted[0][1]["deep"] is True


def test_units_limit_is_per_period_and_burns(db, no_smtp):
    plan = make_plan(days=30, units_limit=10)
    user = make_user(db)
    plans.set_subscription(user, plan_id=plan["id"])
    sub = plans.subscription(user)
    _sql(
        db,
        "INSERT INTO ai_usage (user_id, task, units, charged, created_at) VALUES (%s, 'chat', 10, 10, %s)",
        (user, sub["period_start"] + timedelta(hours=1)),
    )
    with pytest.raises(YandexError) as exc:
        billing.check(user)
    assert exc.value.code == "units-exhausted"
    # Событие «лимит исчерпан» — один раз за период.
    with pytest.raises(YandexError):
        billing.check(user)
    assert [e["kind"] for e in events.list_events(user)].count("limit_exhausted") == 1

    # Расход прошлого периода не переносится: новый период — лимит снова полный.
    _sql(
        db,
        "UPDATE subscriptions SET starts_at = starts_at - interval '30 days', ends_at = ends_at + interval '30 days' WHERE user_id = %s",
        (user,),
    )
    _sql(db, "UPDATE ai_usage SET created_at = created_at - interval '30 days' WHERE user_id = %s", (user,))
    billing.check(user)
    assert billing.units_left(user) == 10.0


# --- Доступ к кабинету -------------------------------------------------------------------------


def test_expired_subscription_closes_cabinet_but_not_profile(db, no_smtp):
    plan = make_plan(name="Про", days=30)
    user = make_user(db)
    plans.set_subscription(user, plan_id=plan["id"], until=(now_msk() - timedelta(days=1)).date().isoformat())
    client = TestClient(app)
    auth = _auth(user)

    me = client.get("/api/web/me", headers=auth)
    assert me.status_code == 200
    body = me.json()
    assert body["subscription"]["state"] == "expired" and body["workspaces"] == [] and body["connections"] == []
    assert client.get("/api/web/plan", headers=auth).json()["usage"] is None
    assert client.get("/api/web/workspaces", headers=auth).status_code == 402
    assert client.get("/api/web/workspaces", headers=auth).json()["detail"] == "subscription-expired"

    # Продлили в админке — доступ вернулся.
    put = client.put(f"/api/admin/users/{user}/subscription", headers=admin_headers(), json={"renew": True})
    assert put.status_code == 200 and put.json()["state"] == "ok"
    assert client.get("/api/web/workspaces", headers=auth).status_code == 200


def test_trial_on_register_and_email_confirmation(db, smtp):
    make_plan(name="Пробный", price_rub=0, days=7, units_limit=50, is_trial=True)
    client = TestClient(app)
    reg = client.post("/api/web/register", json={"email": "new@example.com", "password": "secret-pass-1"})
    assert reg.status_code == 200
    user = reg.json()["user"]["id"]
    auth = {"Authorization": f"Bearer {reg.json()['token']}"}

    sub = plans.subscription(user)
    assert sub["plan"]["is_trial"] and sub["units_limit"] == 50.0 and sub["state"] == "unverified"
    assert timedelta(days=6) < sub["ends_at"] - now_msk() <= timedelta(days=7)
    assert [e["kind"] for e in events.list_events(user)] == ["register"]

    # Пока почта не подтверждена — профиль есть, кабинета нет.
    assert client.get("/api/web/me", headers=auth).json()["subscription"]["state"] == "unverified"
    assert client.get("/api/web/workspaces", headers=auth).status_code == 403
    assert len(smtp) == 1 and "/verify-email?token=" in smtp[0]["link"]

    # Повтор письма — не чаще раза в минуту.
    assert client.post("/api/web/verify-email/resend", headers=auth).status_code == 429
    _sql(db, "UPDATE email_verify_tokens SET created_at = created_at - interval '2 minutes'")
    assert client.post("/api/web/verify-email/resend", headers=auth).json() == {"ok": True, "sent": True}
    assert len(smtp) == 2

    # Старая ссылка уже недействительна, новая подтверждает (и без входа).
    old = smtp[0]["link"].split("token=")[1]
    new = smtp[1]["link"].split("token=")[1]
    assert client.post("/api/web/verify-email", json={"token": old}).status_code == 400
    assert client.post("/api/web/verify-email", json={"token": new}).json() == {"ok": True}
    assert get_user(user)["email_verified_at"] is not None
    assert client.get("/api/web/workspaces", headers=auth).status_code == 200
    assert client.post("/api/web/verify-email", json={"token": new}).status_code == 400
    assert "email_verified" in [e["kind"] for e in events.list_events(user)]


def test_expired_verify_link_and_admin_verify(db, smtp):
    user = make_user(db, "late@example.com")
    _sql(db, "UPDATE users SET email_verified_at = NULL WHERE id = %s", (user,))
    assert email_verify.send(user)
    token = smtp[0]["link"].split("token=")[1]
    _sql(db, "UPDATE email_verify_tokens SET expires_at = NOW() - interval '1 minute'")
    with pytest.raises(Exception) as exc:
        email_verify.confirm(token)
    assert getattr(exc.value, "code", "") == "token-expired"

    client = TestClient(app)
    assert client.post(f"/api/admin/users/{user}/verify-email", headers=admin_headers()).json() == {"ok": True}
    assert plans.subscription(user)["state"] == "ok"
    # Подтверждённому письмо не уходит.
    assert email_verify.send(user) is False


def test_without_smtp_email_is_not_required(db, no_smtp):
    client = TestClient(app)
    reg = client.post("/api/web/register", json={"email": "plain@example.com", "password": "secret-pass-1"})
    user = reg.json()["user"]["id"]
    # Пробного тарифа нет — без ограничений, кабинет открыт сразу.
    sub = plans.subscription(user)
    assert sub["plan"] is None and sub["state"] == "ok" and sub["ends_at"] is None
    assert client.get("/api/web/workspaces", headers={"Authorization": f"Bearer {reg.json()['token']}"}).status_code == 200


def test_active_users_for_background_jobs(db, monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: False)
    ok = make_user(db, "ok@example.com")
    expired = make_user(db, "expired@example.com")
    blocked = make_user(db, "blocked@example.com")
    unverified = make_user(db, "unverified@example.com")
    plan = make_plan(days=30, features={"digest": False})
    plans.set_subscription(expired, plan_id=plan["id"], until=(now_msk() - timedelta(days=1)).date().isoformat())
    _sql(db, "UPDATE users SET blocked_at = NOW() WHERE id = %s", (blocked,))
    _sql(db, "UPDATE users SET email_verified_at = NULL WHERE id = %s", (unverified,))
    assert plans.active_user_ids() == {ok, unverified}
    monkeypatch.setattr(mailer, "configured", lambda: True)
    _sql(db, "UPDATE users SET email_verified_at = NOW() WHERE id = %s", (ok,))
    assert plans.active_user_ids() == {ok}

    plans.set_subscription(ok, plan_id=plan["id"])
    assert plans.users_with_feature({ok, unverified}, "digest") == {unverified}


# --- Функции тарифа ----------------------------------------------------------------------------


def test_locked_features_answer_402(db, no_smtp):
    plan = make_plan(features={"wordstat": False, "images": False, "autopilot": False, "digest": False})
    user = make_user(db)
    plans.set_subscription(user, plan_id=plan["id"])
    with pytest.raises(YandexError) as exc:
        plans.require_feature(user, "wordstat")
    assert exc.value.code == "feature-locked" and "на тарифе выше" in exc.value.message
    plans.require_feature(user, "experiments")

    client = TestClient(app)
    auth = _auth(user)
    rule = client.post("/api/web/rules", headers=auth, json={"name": "Правило", "rule_type": "cpa_above"})
    assert rule.status_code == 402 and rule.json()["detail"]["code"] == "feature-locked"
    me = client.get("/api/web/plan", headers=auth).json()
    assert me["subscription"]["features"]["wordstat"] is False and me["features"]["wordstat"] == "Вордстат"

    # Админ открыл функцию пользователю поверх тарифа.
    plans.set_subscription(user, overrides={"features": {"wordstat": True}})
    plans.require_feature(user, "wordstat")


# --- Проекты в пределах тарифа -----------------------------------------------------------------


def test_projects_limit_auto_creation_and_counter_choice(db, no_smtp):
    user = make_user(db)
    conn = make_connection(db, user, "anna")
    for counter_id in (101, 102, 103):
        make_counter(db, user, conn, counter_id, site=f"site{counter_id}.ru")
    plan = make_plan(days=30, projects_limit=2)
    plans.set_subscription(user, plan_id=plan["id"])

    # Три счётчика в тариф на два проекта не помещаются — проекты не создаются автоматически.
    assert workspaces.ensure_workspaces(user) == []
    choices = workspaces.counter_choices(user)
    assert choices["projects_limit"] == 2 and choices["projects_used"] == 0
    assert {i["state"] for i in choices["items"]} == {"available"}

    client = TestClient(app)
    auth = _auth(user)
    first = client.post("/api/web/workspaces/from-counter", headers=auth, json={"counter_id": 101})
    assert first.status_code == 200 and first.json()["counter_id"] == 101
    assert client.post("/api/web/workspaces/from-counter", headers=auth, json={"counter_id": 101}).json()["id"] == first.json()["id"]
    assert client.post("/api/web/workspaces/from-counter", headers=auth, json={"counter_id": 102}).status_code == 200
    third = client.post("/api/web/workspaces/from-counter", headers=auth, json={"counter_id": 103})
    assert third.status_code == 402 and third.json()["detail"]["code"] == "projects-limit"
    states = {i["counter_id"]: i["state"] for i in client.get("/api/web/workspaces/counters", headers=auth).json()["items"]}
    assert states == {101: "project", 102: "project", 103: "locked"}
    assert client.post("/api/web/workspaces/from-counter", headers=auth, json={"counter_id": 999}).status_code == 404
    assert [e["meta"]["auto"] for e in events.list_events(user) if e["kind"] == "project_created"] == [False, False]

    # В архив — место освободилось; вернуть из архива, когда места нет, нельзя.
    ws_first = first.json()["id"]
    assert client.patch(f"/api/web/workspaces/{ws_first}", headers=auth, json={"archived": True}).status_code == 200
    assert client.post("/api/web/workspaces/from-counter", headers=auth, json={"counter_id": 103}).status_code == 200
    back = client.patch(f"/api/web/workspaces/{ws_first}", headers=auth, json={"archived": False})
    assert back.status_code == 402 and back.json()["detail"]["code"] == "projects-limit"


def test_projects_over_limit_are_locked(db, no_smtp):
    user = make_user(db)
    old = make_workspace(db, user, 201, "Первый")
    new = make_workspace(db, user, 202, "Второй")
    plan = make_plan(days=30, projects_limit=1)
    plans.set_subscription(user, plan_id=plan["id"])

    items = {w["id"]: w["locked"] for w in workspaces.list_workspaces(user)}
    assert items == {old: False, new: True}
    client = TestClient(app)
    auth = _auth(user)
    assert client.get(f"/api/web/workspaces/{old}", headers=auth).status_code == 200
    locked = client.get(f"/api/web/workspaces/{new}", headers=auth)
    assert locked.status_code == 402 and locked.json()["detail"]["code"] == "project-locked"
    # Закрытый проект можно только отправить в архив.
    assert client.patch(f"/api/web/workspaces/{new}", headers=auth, json={"name": "Х"}).status_code == 402
    # Архив первого открывает второй.
    assert client.patch(f"/api/web/workspaces/{old}", headers=auth, json={"archived": True}).status_code == 200
    assert client.get(f"/api/web/workspaces/{new}", headers=auth).status_code == 200


def test_without_limit_all_counters_become_projects(db, no_smtp):
    user = make_user(db)
    conn = make_connection(db, user, "anna")
    for counter_id in (301, 302, 303):
        make_counter(db, user, conn, counter_id)
    assert len(workspaces.ensure_workspaces(user)) == 3
    assert [e["meta"]["auto"] for e in events.list_events(user) if e["kind"] == "project_created"] == [True] * 3


# --- Один Яндекс — в одном аккаунте -------------------------------------------------------------


def test_counter_taken_by_other_account(db, no_smtp):
    owner = make_user(db, "owner@example.com")
    other = make_user(db, "other@example.com")
    make_workspace(db, owner, 401, "Сайт владельца")
    conn = make_connection(db, other, "shared")
    make_counter(db, other, conn, 401)
    make_counter(db, other, conn, 402)

    created = workspaces.ensure_workspaces(other)
    assert len(created) == 1  # только 402
    states = {i["counter_id"]: (i["state"], i["note"]) for i in workspaces.counter_choices(other)["items"]}
    assert states[401][0] == "taken" and "напишите в поддержку" in states[401][1]
    with pytest.raises(YandexError) as exc:
        workspaces.create_project_for_counter(other, 401)
    assert exc.value.code == "counter-taken"


def test_yandex_login_and_direct_account_unique(db, no_smtp):
    owner = make_user(db, "owner@example.com", yandex_uid="uid-anna")
    make_connection(db, owner, "anna")  # yandex_uid = uid-anna
    stranger = make_user(db, "stranger@example.com")
    profile = {"uid": "uid-anna", "login": "anna", "email": "anna@yandex.ru"}

    # Подключить к чужому аккаунту нельзя; войти владельцу — можно.
    with pytest.raises(YandexError) as exc:
        yandex._check_login_free(profile, stranger)
    assert exc.value.code == "yandex-taken" and "anna" in exc.value.message
    yandex._check_login_free(profile, owner)
    yandex._check_login_free(profile, None)  # вход: найден владелец по uid
    yandex._check_login_free({"uid": "uid-free", "login": "free"}, stranger)

    # Кабинет Директа, подключённый у владельца, второму аккаунту не записывается.
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO direct_accounts (user_id, connection_id, client_login) "
            "SELECT %s, id, 'shop' FROM yandex_connections WHERE user_id = %s",
            (owner, owner),
        )
        assert yandex.login_taken(conn, stranger, "SHOP") is True
        assert yandex.login_taken(conn, owner, "shop") is False
    stranger_conn = make_connection(db, stranger, "boris")
    count, _ = yandex.upsert_direct(stranger, stranger_conn, [{"client_login": "shop", "name": "Магазин"}])
    assert count == 0
