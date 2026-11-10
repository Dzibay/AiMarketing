"""Мост Cloudflare: AI Gateway и свой Worker — настройки, адрес и заголовки чата и картинок, развёртывание Worker'а."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai, ai_bridge, image_gen
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import admin_headers, make_user, setup_ai

ACCOUNT = "0123456789abcdef0123456789abcdef"
GATEWAY = f"https://gateway.ai.cloudflare.com/v1/{ACCOUNT}/aimarketing"
WORKER = "https://aimarketing-bridge.acme.workers.dev"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-bridge-secret")


def _gateway(**kwargs):
    return ai_bridge.save_settings(kind="gateway", account_id=ACCOUNT, gateway_id="aimarketing", **kwargs)


def _worker(**kwargs):
    return ai_bridge.save_settings(kind="worker", worker_url=WORKER, **kwargs)


def test_model_routing():
    assert ai_bridge.gateway_model("claude-sonnet-5-5") == "anthropic/claude-sonnet-5-5"
    assert ai_bridge.gateway_model("gemini-2.5-flash") == "google-ai-studio/gemini-2.5-flash"
    assert ai_bridge.gateway_model("gpt-4o-mini") == "openai/gpt-4o-mini"
    assert ai_bridge.gateway_model("anthropic/claude-opus-5-5") == "anthropic/claude-opus-5-5"
    assert ai_bridge.model_provider("anthropic/claude-opus-5-5") == "anthropic"
    assert "BRIDGE_TOKEN" in ai_bridge.WORKER_SCRIPT and "api.anthropic.com" in ai_bridge.WORKER_SCRIPT


def test_settings_validation(db):
    empty = ai_bridge.get_settings()
    assert not empty["enabled"] and not empty["active"] and empty["kind"] == "worker" and empty["use_chat"]
    with pytest.raises(YandexError):
        ai_bridge.save_settings(kind="gateway", enabled=True, gateway_id="aimarketing")
    with pytest.raises(YandexError):
        ai_bridge.save_settings(kind="gateway", account_id="not-an-id", gateway_id="aimarketing")
    with pytest.raises(YandexError):
        ai_bridge.save_settings(kind="gateway", account_id=ACCOUNT, gateway_id="bad id/../x")
    with pytest.raises(YandexError):
        ai_bridge.save_settings(kind="worker", enabled=True, worker_url=WORKER)  # без секрета
    with pytest.raises(YandexError):
        ai_bridge.save_settings(kind="worker", worker_url="http://10.0.0.1")
    saved = ai_bridge.save_settings(
        kind="gateway", enabled=True, account_id=ACCOUNT.upper(), gateway_id="aimarketing", token="cf-token-123456"
    )
    assert saved["active"] and saved["account_id"] == ACCOUNT and saved["token_hint"] == "…3456"
    assert saved["chat_url"] == f"{GATEWAY}/compat"
    kept = _gateway(enabled=False, token=None)
    assert kept["has_token"] and not kept["active"] and kept["account_id"] == ACCOUNT
    # Переключение на Worker не теряет токен шлюза.
    worker = _worker(enabled=True, worker_token="worker-secret-abcdef")
    assert worker["active"] and worker["kind"] == "worker" and worker["has_token"] and worker["worker_token_hint"] == "…cdef"


def test_chat_and_images_go_through_gateway(db):
    user = make_user(db)
    setup_ai(api_key="sk-ant-test-key", model="claude-sonnet-5-5", base_url="https://api.example.com/v1")
    image_gen.save_settings(provider="openai", api_key="sk-openai-test", model="gpt-image-1")
    direct = ai.load_config(user)
    assert direct["base_url"] == "https://api.example.com/v1" and not direct["bridge"] and direct["temperature"] == 0.3

    _gateway(enabled=True, token="cf-token-123456")
    cfg = ai.load_config(user)
    assert cfg["bridge"] and cfg["base_url"] == f"{GATEWAY}/compat"
    assert cfg["model"] == "anthropic/claude-sonnet-5-5" and cfg["model_name"] == "claude-sonnet-5-5"
    assert cfg["headers"]["Authorization"] == "Bearer sk-ant-test-key"
    assert cfg["headers"]["cf-aig-authorization"] == "Bearer cf-token-123456"
    assert cfg["temperature"] is None
    img = image_gen.load_config(user)
    assert img["bridge"] and img["base_url"] == f"{GATEWAY}/openai"
    assert img["headers"]["cf-aig-authorization"] == "Bearer cf-token-123456"

    # Только для чата: картинки идут напрямую.
    _gateway(enabled=True, use_images=False)
    assert ai.load_config(user)["bridge"] and not image_gen.load_config(user)["bridge"]

    # Выключен — всё напрямую, данные моста на месте.
    _gateway(enabled=False)
    assert ai.load_config(user)["base_url"] == "https://api.example.com/v1"
    assert ai_bridge.get_settings()["has_token"]

    # YandexGPT мост не трогает.
    _gateway(enabled=True)
    setup_ai(provider="yandexgpt", api_key=None, folder_id="b1gfolder", model="yandexgpt/latest")
    assert not ai.load_config(user)["bridge"]


def test_chat_and_images_go_through_worker(db):
    user = make_user(db)
    setup_ai(api_key="sk-ant-test-key", model="anthropic/claude-sonnet-5-5")
    image_gen.save_settings(provider="openai", api_key="sk-openai-test", model="gpt-image-1")
    _worker(enabled=True, worker_token="worker-secret-abcdef")
    cfg = ai.load_config(user)
    assert cfg["bridge"] and cfg["base_url"] == f"{WORKER}/anthropic/v1" and cfg["model"] == "claude-sonnet-5-5"
    assert cfg["headers"]["x-bridge-token"] == "worker-secret-abcdef" and cfg["temperature"] is None
    setup_ai(api_key=None, model="gpt-4o-mini")
    cfg = ai.load_config(user)
    assert cfg["base_url"] == f"{WORKER}/openai/v1" and cfg["model"] == "gpt-4o-mini" and cfg["temperature"] == 0.3
    setup_ai(api_key=None, model="gemini-2.5-flash")
    assert ai.load_config(user)["base_url"] == f"{WORKER}/google/v1beta/openai"
    img = image_gen.load_config(user)
    assert img["base_url"] == f"{WORKER}/openai/v1" and img["headers"]["x-bridge-token"] == "worker-secret-abcdef"
    setup_ai(api_key=None, model="mistral-large")
    with pytest.raises(YandexError):
        ai.load_config(user)


def test_claude_request_has_no_temperature_and_region_error(db, monkeypatch):
    user = make_user(db)
    setup_ai(api_key="sk-ant-test-key", model="claude-opus-5-5")
    _gateway(enabled=True)
    monkeypatch.setattr(ai, "_check_url_resolves_public", lambda url: None)
    sent = {}

    def post(url, json=None, headers=None, **kwargs):
        sent.update(url=url, body=json)
        return httpx.Response(
            403, json={"error": {"type": "forbidden", "message": "Request not allowed"}}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(ai.httpx, "post", post)
    with pytest.raises(YandexError) as exc:
        ai.complete(ai.load_config(user), [{"role": "user", "content": "привет"}])
    assert sent["url"] == f"{GATEWAY}/compat/chat/completions" and sent["body"]["model"] == "anthropic/claude-opus-5-5"
    assert "temperature" not in sent["body"]
    assert exc.value.code == "ai-region" and "Worker" in exc.value.message


def test_gateway_auth_error_is_readable(db, monkeypatch):
    user = make_user(db)
    setup_ai(api_key="sk-test-key", model="gpt-4o-mini")
    _gateway(enabled=True)
    monkeypatch.setattr(ai, "_check_url_resolves_public", lambda url: None)

    def post(url, json=None, headers=None, **kwargs):
        return httpx.Response(
            401, json={"success": False, "errors": [{"code": 2009, "message": "Unauthorized"}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(ai.httpx, "post", post)
    with pytest.raises(YandexError) as exc:
        ai.complete(ai.load_config(user), [{"role": "user", "content": "привет"}])
    assert exc.value.code == "ai-auth" and "Cloudflare" in exc.value.message and "Unauthorized" in exc.value.message


def test_deploy_and_check_worker(db, monkeypatch):
    _gateway(token="cf-token-123456")
    calls = []

    def request(method, url, headers=None, **kwargs):
        calls.append((method, url, headers, kwargs))
        req = httpx.Request(method, url)
        if url.endswith("/workers/subdomain"):
            return httpx.Response(200, json={"success": True, "result": {"subdomain": "acme"}}, request=req)
        return httpx.Response(200, json={"success": True, "result": {}}, request=req)

    monkeypatch.setattr(ai_bridge.httpx, "request", request)
    saved = ai_bridge.deploy_worker(account_id=ACCOUNT, api_token="cf-api-token")
    assert saved["active"] and saved["kind"] == "worker" and saved["worker_url"] == WORKER and saved["has_token"]
    upload = calls[0]
    assert upload[0] == "PUT" and upload[1].endswith(f"/accounts/{ACCOUNT}/workers/scripts/aimarketing-bridge")
    assert upload[2]["Authorization"] == "Bearer cf-api-token"
    metadata = json.loads(upload[3]["files"]["metadata"][1])
    assert metadata["placement"] == {"region": "aws:us-east-1"} and metadata["main_module"] == "worker.js"
    secret = metadata["bindings"][0]["text"]
    assert ai_bridge.target("chat", "claude-sonnet-5-5")["headers"]["x-bridge-token"] == secret

    def get(url, headers=None, **kwargs):
        assert url == f"{WORKER}/trace" and headers["x-bridge-token"] == secret
        return httpx.Response(200, text="fl=1\nloc=US\ncolo=IAD\n", request=httpx.Request("GET", url))

    monkeypatch.setattr(ai_bridge.httpx, "get", get)
    assert ai_bridge.check_worker() == {"ok": True, "country": "US", "colo": "IAD", "blocked": False}

    def denied(method, url, headers=None, **kwargs):
        return httpx.Response(403, json={"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}, request=httpx.Request(method, url))

    monkeypatch.setattr(ai_bridge.httpx, "request", denied)
    with pytest.raises(YandexError) as exc:
        ai_bridge.deploy_worker(account_id=ACCOUNT, api_token="bad")
    assert exc.value.code == "bridge-cf-auth" and "Edit Cloudflare Workers" in exc.value.message


def test_bridge_api(db, monkeypatch):
    user = make_user(db, email="owner@example.com")
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    # Мост — только в админке.
    assert client.get("/api/web/ai/bridge", headers=auth).status_code == 404
    assert client.get("/api/admin/bridge", headers=auth).status_code == 401
    admin = admin_headers()
    assert client.get("/api/admin/bridge", headers=admin).json()["enabled"] is False
    res = client.put(
        "/api/admin/bridge",
        headers=admin,
        json={"enabled": True, "kind": "gateway", "account_id": ACCOUNT, "gateway_id": "aimarketing", "token": "cf-token-123456"},
    )
    assert res.status_code == 200 and res.json()["active"] and "token" not in res.json()
    bad = client.put("/api/admin/bridge", headers=admin, json={"enabled": True, "kind": "worker"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bridge-incomplete"
    missing = client.post("/api/admin/bridge/test", headers=admin)
    assert missing.status_code == 400


def test_check_connection_reports_each_model(db, monkeypatch):
    setup_ai(api_key="sk-ant-test-key", model="claude-opus-5-5", fast="claude-haiku-5-5")
    _worker(enabled=True, worker_token="worker-secret-abcdef")

    def complete(cfg, messages, tools=None):
        if cfg["model_class"] == "standard":
            raise YandexError("ai-error", "Ошибка ИИ через мост Cloudflare (404): model not found")
        return {"content": "готов", "tool_calls": [], "usage": {}, "model": "claude-haiku-5-5-20261001"}

    monkeypatch.setattr(ai, "complete", complete)
    checked = ai.check_connection()
    assert not checked["ok"] and checked["bridge"]
    fast, smart = checked["models"]
    assert (smart["label"], smart["model"]) == ("Стандартная", "claude-opus-5-5") and "404" in smart["error"]
    assert (fast["label"], fast["model"], fast["reply"]) == ("Быстрая", "claude-haiku-5-5", "готов")
    assert fast["answered_by"] == "claude-haiku-5-5-20261001"
