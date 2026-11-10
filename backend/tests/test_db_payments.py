"""Фаза 4: оплата через ЮKassa, промокоды, реф-ссылки и напоминания об окончании тарифа."""

import copy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import events, mailer, payments, plans, promos, referrals, reminders
from app.clock import now_msk
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import admin_headers, make_plan, make_user


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {make_session(user_id)}"}


class FakeKassa:
    """ЮKassa в памяти: создаёт платежи, отдаёт их по id, умеет «оплатить», «отменить» и «вернуть»."""

    def __init__(self):
        self.payments: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.down = False
        self.test_shop = True

    def __call__(self, method, path, *, body=None, idempotence_key="", creds=None):
        self.calls.append((method, path, idempotence_key))
        if self.down:
            raise YandexError("payments-remote", "ЮKassa не ответила — повторите позже")
        if path == "/me":
            return {"account_id": "100500", "test": self.test_shop, "status": "enabled"}
        if method == "POST" and path == "/payments":
            # Тот же ключ идемпотентности — тот же платёж.
            for p in self.payments.values():
                if p["_key"] == idempotence_key:
                    return copy.deepcopy(p)
            pid = f"2f{len(self.payments):06d}-000f-5000-9000-1a2b3c4d5e6f"
            self.payments[pid] = {
                "_key": idempotence_key,
                "id": pid,
                "status": "pending",
                "paid": False,
                "test": True,
                "amount": body["amount"],
                "metadata": body["metadata"],
                "description": body["description"],
                "confirmation": {"type": "redirect", "confirmation_url": f"https://yoomoney.ru/checkout?orderId={pid}"},
            }
            return copy.deepcopy(self.payments[pid])
        if method == "GET" and path.startswith("/payments/"):
            pid = path.rsplit("/", 1)[1]
            if pid not in self.payments:
                raise YandexError("payments-remote", "Платёж не найден в ЮKassa")
            return copy.deepcopy(self.payments[pid])
        raise AssertionError(f"неожиданный запрос {method} {path}")

    def pay(self, pid):
        self.payments[pid].update(status="succeeded", paid=True, captured_at="2026-11-10T09:00:00.000Z")
        self.payments[pid].pop("confirmation", None)

    def cancel(self, pid, reason="insufficient_funds"):
        self.payments[pid].update(status="canceled", cancellation_details={"party": "yoo_money", "reason": reason})

    def refund(self, pid, value):
        self.payments[pid]["refunded_amount"] = {"value": f"{value:.2f}", "currency": "RUB"}


@pytest.fixture
def kassa(db, monkeypatch):
    fake = FakeKassa()
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-pay-secret")
    monkeypatch.setattr(payments, "_request", fake)
    monkeypatch.setattr(payments, "public_base_url", lambda: "https://aim.example")
    monkeypatch.setattr(mailer, "configured", lambda: False)
    payments.save_settings(shop_id="123456", secret_key="test_secret_key_1234", test_mode=True, enabled=True)
    return fake


@pytest.fixture
def client():
    return TestClient(app)


def _notify(client, event, obj):
    return client.post("/api/yookassa/webhook", json={"type": "notification", "event": event, "object": obj})


def _sub_row(pool, user_id):
    return _sql(pool, "SELECT * FROM subscriptions WHERE user_id = %s", (user_id,)).fetchone()


# --- Подключение --------------------------------------------------------------------------------


def test_settings_keep_key_validate_and_check(db, monkeypatch, client):
    fake = FakeKassa()
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-pay-secret")
    monkeypatch.setattr(payments, "_request", fake)
    s = payments.get_settings()
    assert not s["configured"] and not s["ready"] and s["test_mode"] is True

    with pytest.raises(YandexError) as exc:
        payments.save_settings(shop_id="abc", secret_key="k")
    assert exc.value.code == "payments-field"
    with pytest.raises(YandexError):
        # Без ключа оплату не включить.
        payments.save_settings(shop_id="123456", enabled=True)

    s = payments.save_settings(shop_id="123456", secret_key="test_AbCdEf123456", test_mode=True, enabled=False)
    assert s["configured"] and s["has_key"] and s["key_hint"].endswith("3456") and not s["ready"]
    # Ключ не передан — остаётся прежний.
    s = payments.save_settings(shop_id="123456", secret_key=None, test_mode=True, enabled=True)
    assert s["ready"] and s["has_key"]

    check = payments.check_connection()
    assert check["ok"] and check["test"] and check["warning"] == ""
    assert payments.get_settings()["check_test"] is True
    # Боевой ключ при включённом тестовом режиме — предупреждение.
    fake.test_shop = False
    assert "боевого" in payments.check_connection()["warning"]

    fake.down = True
    with pytest.raises(YandexError):
        payments.check_connection()
    assert payments.get_settings()["check_error"]

    # Админка: ключ в ответе не светится, адрес уведомлений — от адреса сайта.
    res = client.get("/api/admin/payments/settings", headers=admin_headers())
    assert res.status_code == 200 and "secret" not in str(res.json()).lower().replace("secret_key", "")
    assert res.json()["webhook_events"] == ["payment.succeeded", "payment.canceled", "refund.succeeded"]
    # Пустой ключ — удалить; тогда и оплата выключается.
    res = client.put(
        "/api/admin/payments/settings", headers=admin_headers(), json={"shop_id": "123456", "secret_key": "", "enabled": False}
    )
    assert res.status_code == 200 and not res.json()["has_key"]


# --- Платёж -------------------------------------------------------------------------------------


def test_payment_disabled_shows_support(db, client):
    user = make_user(db)
    plan = make_plan(name="Про", price_rub=2990)
    res = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": plan["id"]})
    assert res.status_code == 400 and res.json()["detail"]["code"] == "payments-off"
    assert client.get("/api/web/plan", headers=_auth(user)).json()["payments_enabled"] is False


def test_renew_pays_full_price_and_extends_once(db, kassa, client):
    user = make_user(db)
    pro = make_plan(name="Про", price_rub=2990, days=30, units_limit=1000)
    plans.set_subscription(user, plan_id=pro["id"])
    before = plans.subscription(user)["ends_at"]

    q = client.get(f"/api/web/payments/quote?plan_id={pro['id']}", headers=_auth(user)).json()
    assert q["kind"] == "renew" and q["amount_rub"] == 2990 and q["extends"] is True

    res = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": pro["id"]})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["confirmation_url"].startswith("https://yoomoney.ru/")
    pay = data["payment"]
    assert pay["status"] == "pending" and pay["amount_rub"] == 2990 and pay["test"]
    pid = next(iter(kassa.payments))
    sent = kassa.payments[pid]
    assert sent["amount"] == {"value": "2990.00", "currency": "RUB"}
    assert sent["metadata"]["payment_id"] == str(pay["id"])
    assert kassa.calls[-1][2] == f"aim-{pay['id']}"

    # Уведомление об оплате: платёж перечитан из API, подписка продлена от конца срока.
    kassa.pay(pid)
    res = _notify(client, "payment.succeeded", {"id": pid, "status": "succeeded"})
    assert res.status_code == 200 and res.json()["result"] == "ok"
    sub = plans.subscription(user)
    assert sub["ends_at"] == before + timedelta(days=30) and sub["plan"]["id"] == pro["id"]

    # Повторное уведомление и проверка после возврата на сайт ничего не продлевают.
    _notify(client, "payment.succeeded", {"id": pid, "status": "succeeded"})
    check = client.post(f"/api/web/payments/{pay['id']}/check", headers=_auth(user)).json()["payment"]
    assert check["status"] == "succeeded" and check["applied"] and check["last_day"]
    payments.apply(pay["id"])
    assert plans.subscription(user)["ends_at"] == before + timedelta(days=30)

    evs = [e for e in events.list_events(user) if e["kind"] == "payment"]
    assert len(evs) == 1 and evs[0]["meta"]["amount_rub"] == 2990 and evs[0]["meta"]["renew"] is True
    plan_page = client.get("/api/web/plan", headers=_auth(user)).json()
    assert plan_page["payments_enabled"] and plan_page["payments_test"]
    assert [p["status"] for p in plan_page["payments"]] == ["succeeded"]
    assert plan_page["history"][0]["kind"] == "payment"


def test_change_uses_discount_and_restarts_now(db, kassa, client):
    pool = db
    user = make_user(pool)
    base = make_plan(name="Старт", price_rub=1000, days=30, units_limit=1000)
    pro = make_plan(name="Про", price_rub=3000, days=30, units_limit=3000)
    plans.set_upgrade(base["id"], pro["id"], 20)
    plans.set_subscription(user, plan_id=base["id"], overrides={"units_limit": 50})

    res = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": pro["id"]})
    assert res.json()["payment"]["amount_rub"] == 2400 and res.json()["payment"]["kind"] == "change"
    pid = next(iter(kassa.payments))
    kassa.pay(pid)
    _notify(client, "payment.succeeded", {"id": pid})
    sub = plans.subscription(user)
    # Переход: новый тариф с сегодняшнего дня, правки админа сброшены.
    assert sub["plan"]["id"] == pro["id"] and sub["units_limit"] == 3000 and sub["overrides"] == {}
    assert abs((sub["ends_at"] - now_msk()) - timedelta(days=30)) < timedelta(minutes=1)

    # Тариф, которого нет в продаже (пробный), оплатить нельзя.
    trial = make_plan(name="Пробный", price_rub=0, days=7, is_trial=True)
    res = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": trial["id"]})
    assert res.status_code == 404


def test_expired_subscription_resumes_after_payment(db, kassa, client, monkeypatch):
    pool = db
    user = make_user(pool)
    pro = make_plan(name="Про", price_rub=990, days=30)
    plans.set_subscription(user, plan_id=pro["id"], until=(now_msk() - timedelta(days=3)).date().isoformat())
    assert plans.subscription(user)["expired"]
    resumed = []
    monkeypatch.setattr(plans, "resume", lambda uid: resumed.append(uid))

    res = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": pro["id"]})
    assert res.json()["payment"]["kind"] == "renew"
    pid = next(iter(kassa.payments))
    kassa.pay(pid)
    client.post(f"/api/web/payments/{res.json()['payment']['id']}/check", headers=_auth(user))
    sub = plans.subscription(user)
    assert not sub["expired"] and abs((sub["ends_at"] - now_msk()) - timedelta(days=30)) < timedelta(minutes=1)
    assert resumed == [user]


def test_cancel_refund_mismatch_and_unknown(db, kassa, client):
    pool = db
    user = make_user(pool)
    pro = make_plan(name="Про", price_rub=1500, days=30)
    plans.set_subscription(user, plan_id=pro["id"])
    ends = plans.subscription(user)["ends_at"]

    first = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": pro["id"]}).json()["payment"]
    pid1 = list(kassa.payments)[0]
    kassa.cancel(pid1)
    _notify(client, "payment.canceled", {"id": pid1})
    row = payments._get(first["id"])
    assert row["status"] == "canceled" and payments._public(row)["cancel_reason"] == "Недостаточно средств"
    assert plans.subscription(user)["ends_at"] == ends

    # Уведомление о чужом платеже и мусор — 200, ничего не делаем.
    assert _notify(client, "payment.succeeded", {"id": "2fffffff-000f-5000-9000-1a2b3c4d5e6f"}).json()["result"] == "unknown"
    assert _notify(client, "payment.succeeded", {"id": "../../me"}).json()["result"] == "ignored"
    assert _notify(client, "deal.closed", {"id": pid1}).json()["result"] == "ignored"

    # Оплата и возврат: сумма возврата подтягивается, событие пишется, подписка не трогается.
    second = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": pro["id"]}).json()["payment"]
    pid2 = list(kassa.payments)[1]
    kassa.pay(pid2)
    _notify(client, "payment.succeeded", {"id": pid2})
    extended = plans.subscription(user)["ends_at"]
    kassa.refund(pid2, 500)
    _notify(client, "refund.succeeded", {"id": "refund-1", "payment_id": pid2})
    row = payments._get(second["id"])
    assert float(row["refunded_rub"]) == 500 and plans.subscription(user)["ends_at"] == extended
    assert [e["meta"]["amount_rub"] for e in events.list_events(user) if e["kind"] == "refund"] == [500]

    # ЮKassa недоступна при уведомлении — 500, чтобы она прислала его снова.
    kassa.down = True
    assert _notify(client, "payment.succeeded", {"id": pid2}).status_code == 500
    kassa.down = False

    # Сумма в ЮKassa не та — подписку не продлеваем.
    third = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": pro["id"]}).json()["payment"]
    pid3 = list(kassa.payments)[2]
    kassa.payments[pid3]["amount"] = {"value": "1.00", "currency": "RUB"}
    kassa.pay(pid3)
    _notify(client, "payment.succeeded", {"id": pid3})
    row = payments._get(third["id"])
    assert row["status"] == "error" and row["applied_at"] is None
    assert plans.subscription(user)["ends_at"] == extended


def test_admin_payments_list_revenue_receipt_and_sync(db, kassa, client):
    pool = db
    user = make_user(pool, "payer@example.com")
    pro = make_plan(name="Про", price_rub=2000, days=30)
    plans.set_subscription(user, plan_id=pro["id"])
    pay = client.post("/api/web/payments", headers=_auth(user), json={"plan_id": pro["id"]}).json()["payment"]
    pid = next(iter(kassa.payments))
    kassa.pay(pid)
    # Без уведомления: фоновая сверка находит оплату.
    assert payments.sync_open()["checked"] == 1
    assert payments._get(pay["id"])["applied_at"] is not None

    data = client.get("/api/admin/payments", headers=admin_headers()).json()
    item = data["items"][0]
    assert item["email"] == "payer@example.com" and item["status"] == "succeeded" and not item["receipt_done"]
    # Тестовые платежи в выручку не идут.
    assert data["summary"]["total_rub"] == 0
    _sql(pool, "UPDATE payments SET test = FALSE")
    data = client.get("/api/admin/payments?status=no_receipt", headers=admin_headers()).json()
    assert data["summary"]["month_rub"] == 2000 and data["summary"]["no_receipt"] == 1 and len(data["items"]) == 1

    res = client.put(f"/api/admin/payments/{pay['id']}/receipt", headers=admin_headers(), json={"done": True})
    assert res.json()["receipt_done"] is True
    kassa.refund(pid, 2000)
    res = client.post(f"/api/admin/payments/{pay['id']}/sync", headers=admin_headers())
    assert res.json()["refunded_rub"] == 2000
    me = client.get("/api/admin/me", headers=admin_headers()).json()
    assert me["payments_ready"] and me["payments"]["month_rub"] == 0


# --- Промокоды ----------------------------------------------------------------------------------


def test_promo_admin_crud_and_validation(db, client):
    pro = make_plan(name="Про", price_rub=2000, days=30)
    h = admin_headers()
    res = client.post("/api/admin/promos", headers=h, json={"code": " start-30 ", "kind": "days", "days": 30, "max_uses": 2})
    assert res.status_code == 200 and res.json()["code"] == "START-30"
    assert client.post("/api/admin/promos", headers=h, json={"code": "start-30", "kind": "days", "days": 5}).status_code == 409
    for bad in (
        {"code": "x", "kind": "days", "days": 5},
        {"code": "UNITS", "kind": "units"},
        {"code": "PLAN", "kind": "plan", "days": 30},
        {"code": "DAYS", "kind": "days", "days": 0},
        {"code": "DATE", "kind": "days", "days": 1, "valid_until": "завтра"},
    ):
        assert client.post("/api/admin/promos", headers=h, json=bad).status_code == 400, bad
    plan_promo = client.post(
        "/api/admin/promos", headers=h, json={"code": "PRO2W", "kind": "plan", "plan_id": pro["id"], "days": 14}
    ).json()
    assert plan_promo["plan_name"] == "Про"
    items = client.get("/api/admin/promos", headers=h).json()["items"]
    assert {i["code"] for i in items} == {"START-30", "PRO2W"} and all(i["uses"] == 0 for i in items)
    res = client.put(f"/api/admin/promos/{plan_promo['id']}", headers=h, json={**plan_promo, "active": False})
    assert res.json()["active"] is False
    # Тариф, который выдаёт промокод, не удаляется — понятная ошибка вместо 500 от внешнего ключа.
    res = client.delete(f"/api/admin/plans/{pro['id']}", headers=h)
    assert res.status_code == 409 and "PRO2W" in res.json()["detail"]["message"]
    assert client.delete(f"/api/admin/promos/{plan_promo['id']}", headers=h).status_code == 200
    assert client.delete(f"/api/admin/plans/{pro['id']}", headers=h).status_code == 200


def test_promo_days_limits_and_once_per_user(db, client):
    pro = make_plan(name="Про", price_rub=2000, days=30)
    a, b, c = make_user(db, "a@example.com"), make_user(db, "b@example.com"), make_user(db, "c@example.com")
    for u in (a, b, c):
        plans.set_subscription(u, plan_id=pro["id"])
    promos.save_promo(None, {"code": "PLUS7", "kind": "days", "days": 7, "max_uses": 2})
    ends = plans.subscription(a)["ends_at"]

    res = client.post("/api/web/promo", headers=_auth(a), json={"code": "plus7"})
    assert res.status_code == 200 and "7 дн." in res.json()["message"]
    assert plans.subscription(a)["ends_at"] == ends + timedelta(days=7)
    res = client.post("/api/web/promo", headers=_auth(a), json={"code": "PLUS7"})
    assert res.status_code == 400 and "уже активировали" in res.json()["detail"]["message"]
    assert client.post("/api/web/promo", headers=_auth(b), json={"code": "PLUS7"}).status_code == 200
    res = client.post("/api/web/promo", headers=_auth(c), json={"code": "PLUS7"})
    assert "максимальное" in res.json()["detail"]["message"]

    promos.save_promo(None, {"code": "OLD", "kind": "days", "days": 7, "valid_until": "2020-01-01"})
    assert "закончился" in client.post("/api/web/promo", headers=_auth(c), json={"code": "OLD"}).json()["detail"]["message"]
    assert client.post("/api/web/promo", headers=_auth(c), json={"code": "NOPE"}).json()["detail"]["message"] == promos.NOT_FOUND
    # Подписка без срока — дни добавлять не к чему.
    legacy = make_user(db, "legacy@example.com")
    promos.save_promo(None, {"code": "MORE", "kind": "days", "days": 3})
    assert client.post("/api/web/promo", headers=_auth(legacy), json={"code": "MORE"}).status_code == 400
    assert [e["meta"]["code"] for e in events.list_events(a) if e["kind"] == "promo"] == ["PLUS7"]


def test_promo_units_only_in_current_period(db):
    pro = make_plan(name="Про", price_rub=2000, days=30, units_limit=1000)
    user = make_user(db)
    plans.set_subscription(user, plan_id=pro["id"])
    promos.save_promo(None, {"code": "BOOST", "kind": "units", "units": 250})
    promos.save_promo(None, {"code": "BOOST2", "kind": "units", "units": 100})
    res = promos.redeem(user, "boost")
    assert res["message"].startswith("+25% к лимиту ИИ")
    assert plans.subscription(user)["units_limit"] == 1250
    promos.redeem(user, "BOOST2")
    assert plans.subscription(user)["units_limit"] == 1350
    # Оффер «во сколько раз больше» считается от лимита тарифа, без бонуса.
    assert plans.offers(user)[0]["units_factor"] == 1.0
    # Следующий период — бонуса нет.
    later = plans.subscription(user)["period_end"] + timedelta(days=1)
    _sql(db, "UPDATE subscriptions SET ends_at = ends_at + INTERVAL '30 days' WHERE user_id = %s", (user,))
    assert plans.subscription(user, now=later)["units_limit"] == 1000

    unlimited = make_user(db, "unl@example.com")
    plans.set_subscription(unlimited, plan_id=make_plan(name="Безлимит", units_limit=None)["id"])
    promos.save_promo(None, {"code": "BOOST3", "kind": "units", "units": 10})
    with pytest.raises(YandexError) as exc:
        promos.redeem(unlimited, "BOOST3")
    assert "без ограничений" in exc.value.message


def test_promo_plan_switches_trial_but_not_paid_plan(db):
    trial = make_plan(name="Пробный", price_rub=0, days=7, units_limit=100, is_trial=True)
    pro = make_plan(name="Про", price_rub=2000, days=30, units_limit=1000)
    other = make_plan(name="Старт", price_rub=900, days=30, units_limit=500)
    promos.save_promo(None, {"code": "PRO14", "kind": "plan", "plan_id": pro["id"], "days": 14})

    on_trial = make_user(db, "t@example.com")
    plans.start_trial(on_trial)
    res = promos.redeem(on_trial, "PRO14")
    sub = plans.subscription(on_trial)
    assert sub["plan"]["id"] == pro["id"] and "Подключён тариф «Про» на 14 дн." in res["message"]
    assert abs((sub["ends_at"] - now_msk()) - timedelta(days=14)) < timedelta(minutes=1)

    same = make_user(db, "same@example.com")
    plans.set_subscription(same, plan_id=pro["id"])
    ends = plans.subscription(same)["ends_at"]
    assert "продлён" in promos.redeem(same, "PRO14")["message"]
    assert plans.subscription(same)["ends_at"] == ends + timedelta(days=14)

    paid = make_user(db, "paid@example.com")
    plans.set_subscription(paid, plan_id=other["id"])
    with pytest.raises(YandexError) as exc:
        promos.redeem(paid, "PRO14")
    assert "«Старт»" in exc.value.message
    assert plans.subscription(paid)["plan"]["id"] == other["id"]
    # Отказ не тратит активацию.
    assert promos.list_promos()[0]["uses"] == 2
    assert trial["is_trial"]


def test_register_with_promo_and_ref(db, client, monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: False)
    make_plan(name="Пробный", price_rub=0, days=7, is_trial=True)
    promos.save_promo(None, {"code": "WELCOME", "kind": "days", "days": 7, "max_uses": 1})
    link = referrals.save_link(None, {"code": "Blog", "name": "Блог"})
    assert link["code"] == "blog"

    # Неверный промокод — аккаунт не создаётся.
    res = client.post(
        "/api/web/register", json={"email": "bad@example.com", "password": "secret-pass-1", "promo": "nope"}
    )
    assert res.status_code == 400
    assert _sql(db, "SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0

    res = client.post(
        "/api/web/register",
        json={"email": "new@example.com", "password": "secret-pass-1", "promo": "welcome", "ref": "BLOG"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "7 дн." in body["promo"]["message"]
    uid = body["user"]["id"]
    sub = plans.subscription(uid)
    assert abs((sub["ends_at"] - now_msk()) - timedelta(days=14)) < timedelta(minutes=1)
    assert _sql(db, "SELECT ref_link_id FROM users WHERE id = %s", (uid,)).fetchone()["ref_link_id"] == link["id"]

    # Неизвестный реф-код просто не запоминается.
    res = client.post("/api/web/register", json={"email": "x@example.com", "password": "secret-pass-1", "ref": "zzz"})
    assert res.status_code == 200 and res.json()["promo"] is None
    assert _sql(db, "SELECT ref_link_id FROM users WHERE email = 'x@example.com'").fetchone()["ref_link_id"] is None


# --- Реф-ссылки ---------------------------------------------------------------------------------


def test_ref_visits_and_stats(db, client):
    h = admin_headers()
    link = client.post("/api/admin/refs", headers=h, json={"code": "vk-ads", "name": "VK"}).json()
    assert link["url"].endswith("/?ref=vk-ads")
    assert client.post("/api/admin/refs", headers=h, json={"code": "VK-ADS"}).status_code == 409
    assert client.post("/api/admin/refs", headers=h, json={"code": "плохой"}).status_code == 400

    assert client.post("/api/site/ref", json={"code": "VK-ADS"}).status_code == 200
    assert client.post("/api/site/ref", json={"code": "vk-ads"}).status_code == 200
    assert client.post("/api/site/ref", json={"code": "nope"}).status_code == 404

    u1, u2, u3 = make_user(db, "1@example.com"), make_user(db, "2@example.com"), make_user(db, "3@example.com")
    for u in (u1, u2, u3):
        assert referrals.attach(u, "vk-ads")
    # Первая ссылка не перебивается.
    other = referrals.save_link(None, {"code": "other"})
    assert not referrals.attach(u1, "other")
    _sql(db, "UPDATE users SET email_verified_at = NOW() WHERE id IN (%s, %s)", (u1, u2))
    pro = make_plan(name="Про", price_rub=1000)
    for uid, amount, refunded, test in ((u1, 1000, 0, False), (u1, 1000, 300, False), (u2, 1000, 0, True)):
        _sql(
            db,
            """
            INSERT INTO payments (user_id, plan_id, plan_name, days, kind, amount_rub, status, refunded_rub, test)
            VALUES (%s, %s, 'Про', 30, 'renew', %s, 'succeeded', %s, %s)
            """,
            (uid, pro["id"], amount, refunded, test),
        )
    items = {i["code"]: i for i in client.get("/api/admin/refs", headers=h).json()["items"]}
    vk = items["vk-ads"]
    assert (vk["visits"], vk["registrations"], vk["verified"], vk["payers"], vk["payments"], vk["revenue_rub"]) == (
        2, 3, 2, 1, 2, 1700.0,
    )
    assert items["other"]["registrations"] == 0

    # Выключенная ссылка не считает переходы и не привязывает новых.
    client.put(f"/api/admin/refs/{other['id']}", headers=h, json={"code": "other", "active": False})
    assert client.post("/api/site/ref", json={"code": "other"}).status_code == 404
    assert client.delete(f"/api/admin/refs/{link['id']}", headers=h).status_code == 200
    assert _sql(db, "SELECT COUNT(*) AS n FROM users WHERE ref_link_id IS NOT NULL").fetchone()["n"] == 0


def test_yandex_signup_keeps_ref(db, monkeypatch):
    from app import yandex

    referrals.save_link(None, {"code": "tg"})
    monkeypatch.setattr(yandex, "build_authorize_url", lambda state, **kw: f"https://oauth/?state={state}")
    url = yandex.start_oauth(purpose="login", ref="TG")
    state = url.split("state=")[1]
    row = _sql(db, "SELECT signup FROM oauth_states WHERE state = %s", (state,)).fetchone()
    assert row["signup"] == {"ref": "TG"}
    assert yandex.consume_oauth_state(state)["signup"] == {"ref": "TG"}


# --- Напоминания --------------------------------------------------------------------------------


def test_reminders_3_and_1_days_once(db, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, link="": sent.append({"to": to, "subject": subject}) or True)
    trial = make_plan(name="Пробный", price_rub=0, days=7, is_trial=True)
    pro = make_plan(name="Про", price_rub=1000, days=30)
    user = make_user(db, "r@example.com")
    _sql(db, "UPDATE users SET email_verified_at = NOW() WHERE id = %s", (user,))
    plans.start_trial(user)
    far = make_user(db, "far@example.com")
    plans.set_subscription(far, plan_id=pro["id"])
    now = now_msk()

    # 6 дней до конца — рано.
    assert reminders.due(now) == []
    later = plans.subscription(user)["ends_at"] - timedelta(days=2, hours=12)
    due = reminders.due(later)
    assert [(d["user_id"], d["days"]) for d in due] == [(user, 3)]
    res = reminders.send(user, due[0]["ends_at"], 3, now=later)
    assert res["sent"] and "пробный период" in sent[0]["subject"].lower()
    # Второй раз не уходит.
    assert reminders.due(later) == []
    assert reminders.send(user, due[0]["ends_at"], 3, now=later)["reason"] == "already"

    last_day = plans.subscription(user)["ends_at"] - timedelta(hours=10)
    due = reminders.due(last_day)
    assert [(d["user_id"], d["days"]) for d in due] == [(user, 1)]
    assert reminders.send(user, due[0]["ends_at"], 1, now=last_day)["sent"]
    assert "завтра" in sent[1]["subject"]
    assert reminders.due(last_day) == []

    # Продлили — напоминание к старому сроку молчит.
    stale = due[0]["ends_at"]
    plans.set_subscription(user, plan_id=pro["id"])
    assert reminders.send(user, stale, 1)["reason"] == "changed"
    assert [e["meta"]["days"] for e in events.list_events(user) if e["kind"] == "plan_reminder"] == [1, 3]
    assert trial["is_trial"]


def test_reminder_job_scheduled(db, monkeypatch):
    from app import workers

    pro = make_plan(name="Про", price_rub=1000, days=30)
    user = make_user(db, "soon@example.com")
    plans.set_subscription(user, plan_id=pro["id"], until=(now_msk() + timedelta(days=1)).date().isoformat())
    workers._enqueue_reminders()
    jobs = _sql(db, "SELECT kind, payload FROM jobs WHERE user_id = %s", (user,)).fetchall()
    assert [j["kind"] for j in jobs] == ["plan_reminder"]
    # Повторная постановка не дублирует задачу.
    workers._enqueue_reminders()
    assert _sql(db, "SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1
