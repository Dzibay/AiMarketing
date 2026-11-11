"""Сайт: контакты из админки, SmartCaptcha, лист ожидания, ИИ-режим генераторов."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai, site_extras
from app.config import settings
from app.main import app
from app.settings_store import invalidate_settings_cache
from app.yandex import YandexError
from tests.dbutil import admin_headers, setup_ai

KEYS = (
    "legal_email",
    "site_legal_line",
    "site_support_telegram",
    "smartcaptcha_client_key",
    "smartcaptcha_server_key_enc",
    "site_tools_enabled",
    "site_tools_ip_daily",
    "site_tools_daily",
    "site_check_model",
)


@pytest.fixture(autouse=True)
def _clean(db, monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-site-extras-secret")
    with db.connection() as conn:
        conn.execute("TRUNCATE site_waitlist, site_tool_runs")
        conn.execute("DELETE FROM app_settings WHERE key = ANY(%s)", (list(KEYS),))
        conn.execute("DELETE FROM ai_usage WHERE task = 'site_tools'")
    invalidate_settings_cache()
    yield
    invalidate_settings_cache()


class FakeModel:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
        self.cfgs: list[dict] = []

    def __call__(self, cfg, messages, tools=None):
        self.calls += 1
        self.cfgs.append(cfg)
        return {"content": json.dumps(self.reply, ensure_ascii=False), "tool_calls": [], "usage": {}, "model": cfg["model_name"]}


def test_contacts_saved_and_public(db):
    client = TestClient(app)
    assert client.get("/api/site/public").json() == {"email": "", "legal": "", "telegram": "", "captcha_key": "", "ai_tools": True}

    r = client.put(
        "/api/admin/site",
        headers=admin_headers(),
        json={"email": " help@tolk-ads.ru ", "legal": "ИП  Иванов\nИ. И., ИНН 1", "telegram": "https://t.me/tolk_help"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["contacts"] == {"email": "help@tolk-ads.ru", "legal": "ИП Иванов И. И., ИНН 1", "telegram": "tolk_help"}
    pub = client.get("/api/site/public").json()
    assert (pub["email"], pub["telegram"]) == ("help@tolk-ads.ru", "tolk_help")
    assert "phone" not in pub

    bad = client.put("/api/admin/site", headers=admin_headers(), json={"email": "не почта"})
    assert bad.status_code == 400
    assert client.put("/api/admin/site", json={}).status_code == 401


def test_captcha_keys_and_verify(db, monkeypatch):
    client = TestClient(app)
    # Только ключ клиента — капча выключена: проверять нечем.
    site_extras.save_captcha(client_key="ck-1")
    assert site_extras.public()["captcha_key"] == ""
    site_extras.verify_captcha("", "1.1.1.1")

    r = client.put("/api/admin/site", headers=admin_headers(), json={"captcha_client_key": "ck-1", "captcha_server_key": "sk-secret"})
    body = r.json()["captcha"]
    assert body == {"client_key": "ck-1", "server_key_set": True, "enabled": True}
    assert "sk-secret" not in r.text
    with db.connection() as conn:
        stored = conn.execute("SELECT value FROM app_settings WHERE key = 'smartcaptcha_server_key_enc'").fetchone()
    assert stored and "sk-secret" not in stored["value"]
    assert client.get("/api/site/public").json()["captcha_key"] == "ck-1"

    # Без токена — отказ, не трогая сервис капчи.
    with pytest.raises(YandexError) as exc:
        site_extras.verify_captcha("", "1.1.1.1")
    assert exc.value.code == "captcha"

    sent = {}

    def fake_post(url, data, timeout):
        sent.update(data)
        return httpx.Response(200, json={"status": "ok" if data["token"] == "good" else "failed"})

    monkeypatch.setattr(httpx, "post", fake_post)
    site_extras.verify_captcha("good", "1.1.1.1")
    assert sent == {"secret": "sk-secret", "token": "good", "ip": "1.1.1.1"}
    with pytest.raises(YandexError):
        site_extras.verify_captcha("bad", "1.1.1.1")

    # Сервис капчи лёг — посетителя пропускаем.
    def broken(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", broken)
    site_extras.verify_captcha("any", "1.1.1.1")

    # Ключ сервера не меняется, если его не прислали; "" — стирает.
    client.put("/api/admin/site", headers=admin_headers(), json={"captcha_client_key": "ck-2"})
    assert site_extras.captcha_settings()["server_key_set"] is True
    client.put("/api/admin/site", headers=admin_headers(), json={"captcha_client_key": "ck-2", "captcha_server_key": ""})
    assert site_extras.captcha_settings() == {"client_key": "ck-2", "server_key_set": False, "enabled": False}


def test_site_check_requires_captcha_when_on(db):
    site_extras.save_captcha(client_key="ck", server_key="sk")
    r = TestClient(app).post("/api/site/check", json={"url": "okna.ru"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "captcha"


def test_waitlist(db):
    client = TestClient(app)
    ok = {"channel": "ozon", "email": "Shop@Example.ru", "consent": True}
    assert client.post("/api/site/waitlist", json=ok).status_code == 200
    assert client.post("/api/site/waitlist", json=ok).status_code == 200  # повтор — без дубля
    assert client.post("/api/site/waitlist", json={**ok, "channel": "vk-reklama"}).status_code == 200
    assert client.post("/api/site/waitlist", json={**ok, "consent": False}).status_code == 400
    assert client.post("/api/site/waitlist", json={**ok, "channel": "avito"}).status_code == 400
    assert client.post("/api/site/waitlist", json={**ok, "email": "shop@"}).status_code == 400

    data = client.get("/api/admin/site/waitlist", headers=admin_headers()).json()
    assert {c["id"]: c["count"] for c in data["channels"]} == {"vk-reklama": 1, "ozon": 1, "wildberries": 0}
    assert {i["email"] for i in data["items"]} == {"shop@example.ru"}
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM site_waitlist WHERE ip_hash = 'testclient'").fetchone()["n"] == 0

    first = data["items"][0]["id"]
    after = client.delete(f"/api/admin/site/waitlist/{first}", headers=admin_headers()).json()
    assert len(after["items"]) == 1

    for i in range(site_extras.WAITLIST_IP_DAILY):
        client.post("/api/site/waitlist", json={**ok, "email": f"u{i}@example.ru"})
    r = client.post("/api/site/waitlist", json={**ok, "email": "last@example.ru"})
    assert r.status_code == 429


def test_normalizers():
    long = "Очень длинный заголовок про пластиковые окна с установкой под ключ и гарантией на десять лет"
    out = site_extras.normalize_ads({"titles": ["Окна ПВХ", "Окна ПВХ", long], "titles2": ["Замер бесплатно"], "texts": [long + long]})
    assert out["titles"][0] == "Окна ПВХ" and len(out["titles"]) == 2
    assert all(len(t) <= site_extras.TITLE_MAX for t in out["titles"])
    assert all(len(t) <= site_extras.TEXT_MAX for t in out["texts"])
    assert site_extras.normalize_ads("мусор") == {"titles": [], "titles2": [], "texts": []}

    m = site_extras.normalize_minus(
        {"groups": [{"name": "Не покупают", "words": ["Бесплатно", "бесплатно", "очень длинная фраза тут"]}, {"name": "", "words": ["x"]}, "y"]}
    )
    assert m == {"groups": [{"name": "Не покупают", "words": ["бесплатно"]}]}


def test_generate_limits_and_off(db, monkeypatch):
    setup_ai(model="gpt-std", fast="gpt-fast")
    fake = FakeModel({"titles": ["Окна ПВХ в Казани"], "titles2": ["Замер бесплатно"], "texts": ["Окна под ключ. Звоните"]})
    monkeypatch.setattr(ai, "complete", fake)
    client = TestClient(app)

    r = client.post("/api/site/generate", json={"tool": "ads", "fields": {"product": "Окна ПВХ", "where": "в Казани"}})
    assert r.status_code == 200, r.text
    assert r.json()["titles"] == ["Окна ПВХ в Казани"]
    assert fake.cfgs[0]["model_name"] == "gpt-fast"

    # Пустой ввод — отказ до модели и без списания лимита.
    assert client.post("/api/site/generate", json={"tool": "minus", "fields": {"about": ""}}).status_code == 400
    assert client.post("/api/site/generate", json={"tool": "spam", "fields": {}}).status_code == 404
    assert fake.calls == 1

    site_extras.save_tools(enabled=True, ip_daily=2, daily=100)
    assert client.post("/api/site/generate", json={"tool": "ads", "fields": {"product": "Окна"}}).status_code == 200
    r = client.post("/api/site/generate", json={"tool": "ads", "fields": {"product": "Окна"}})
    assert r.status_code == 429
    assert site_extras.tools_settings()["stats"]["today"] == 2

    site_extras.save_tools(enabled=False, ip_daily=50, daily=100)
    assert client.get("/api/site/public").json()["ai_tools"] is False
    r = client.post("/api/site/generate", json={"tool": "ads", "fields": {"product": "Окна"}})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "site-tools-off"

    with pytest.raises(YandexError):
        site_extras.save_tools(enabled=True, ip_daily=0, daily=100)


def test_generate_model_failure(db, monkeypatch):
    setup_ai(model="gpt-std", fast="gpt-fast")
    monkeypatch.setattr(ai, "complete", FakeModel({"groups": []}))
    r = TestClient(app).post("/api/site/generate", json={"tool": "minus", "fields": {"about": "окна в Казани"}})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "site-tools-failed"
