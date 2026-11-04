"""Мост Cloudflare AI Gateway: настройки, адрес и заголовки чата и картинок, выключение без потери данных."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai, ai_bridge, image_gen
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_user

ACCOUNT = "0123456789abcdef0123456789abcdef"
GATEWAY = f"https://gateway.ai.cloudflare.com/v1/{ACCOUNT}/aimarketing"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-bridge-secret")


def test_gateway_model_prefix():
    assert ai_bridge.gateway_model("claude-sonnet-5-5") == "anthropic/claude-sonnet-5-5"
    assert ai_bridge.gateway_model("gemini-2.5-flash") == "google-ai-studio/gemini-2.5-flash"
    assert ai_bridge.gateway_model("gpt-4o-mini") == "openai/gpt-4o-mini"
    assert ai_bridge.gateway_model("anthropic/claude-opus-5-5") == "anthropic/claude-opus-5-5"


def test_settings_validation(db):
    user = make_user(db)
    empty = ai_bridge.get_settings(user)
    assert not empty["enabled"] and not empty["active"] and empty["use_chat"] and empty["use_images"]
    with pytest.raises(YandexError):
        ai_bridge.save_settings(user, enabled=True, gateway_id="aimarketing")
    with pytest.raises(YandexError):
        ai_bridge.save_settings(user, account_id="not-an-id", gateway_id="aimarketing")
    with pytest.raises(YandexError):
        ai_bridge.save_settings(user, account_id=ACCOUNT, gateway_id="bad id/../x")
    saved = ai_bridge.save_settings(
        user, enabled=True, account_id=ACCOUNT.upper(), gateway_id="aimarketing", token="cf-token-123456"
    )
    assert saved["active"] and saved["account_id"] == ACCOUNT and saved["token_hint"] == "…3456"
    assert saved["chat_url"] == f"{GATEWAY}/compat"
    kept = ai_bridge.save_settings(user, enabled=False, account_id=ACCOUNT, gateway_id="aimarketing", token=None)
    assert kept["has_token"] and not kept["active"] and kept["account_id"] == ACCOUNT


def test_chat_and_images_go_through_bridge(db):
    user = make_user(db)
    ai.save_settings(user, provider="openai", api_key="sk-ant-test-key", model="claude-sonnet-5-5", base_url="https://api.example.com/v1")
    image_gen.save_settings(user, provider="openai", api_key="sk-openai-test", model="gpt-image-1")
    direct = ai.load_config(user)
    assert direct["base_url"] == "https://api.example.com/v1" and not direct["bridge"]

    ai_bridge.save_settings(user, enabled=True, account_id=ACCOUNT, gateway_id="aimarketing", token="cf-token-123456")
    cfg = ai.load_config(user)
    assert cfg["bridge"] and cfg["base_url"] == f"{GATEWAY}/compat"
    assert cfg["model"] == "anthropic/claude-sonnet-5-5" and cfg["model_name"] == "claude-sonnet-5-5"
    assert cfg["headers"]["Authorization"] == "Bearer sk-ant-test-key"
    assert cfg["headers"]["cf-aig-authorization"] == "Bearer cf-token-123456"
    img = image_gen.load_config(user)
    assert img["bridge"] and img["base_url"] == f"{GATEWAY}/openai"
    assert img["headers"]["cf-aig-authorization"] == "Bearer cf-token-123456"

    # Только для чата: картинки идут напрямую.
    ai_bridge.save_settings(user, enabled=True, account_id=ACCOUNT, gateway_id="aimarketing", use_images=False)
    assert ai.load_config(user)["bridge"] and not image_gen.load_config(user)["bridge"]

    # Выключен — всё напрямую, данные моста на месте.
    ai_bridge.save_settings(user, enabled=False, account_id=ACCOUNT, gateway_id="aimarketing")
    assert ai.load_config(user)["base_url"] == "https://api.example.com/v1"
    assert ai_bridge.get_settings(user)["has_token"]

    # YandexGPT мост не трогает.
    ai_bridge.save_settings(user, enabled=True, account_id=ACCOUNT, gateway_id="aimarketing")
    ai.save_settings(user, provider="yandexgpt", folder_id="b1gfolder", model="yandexgpt/latest")
    assert not ai.load_config(user)["bridge"]


def test_gateway_errors_are_readable(db, monkeypatch):
    user = make_user(db)
    ai.save_settings(user, provider="openai", api_key="sk-ant-test-key", model="claude-sonnet-5-5")
    ai_bridge.save_settings(user, enabled=True, account_id=ACCOUNT, gateway_id="aimarketing")
    monkeypatch.setattr(ai, "_check_url_resolves_public", lambda url: None)
    sent = {}

    def post(url, json=None, headers=None, **kwargs):
        sent.update(url=url, model=json["model"])
        return httpx.Response(
            401, json={"success": False, "errors": [{"code": 2009, "message": "Unauthorized"}]}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(ai.httpx, "post", post)
    with pytest.raises(YandexError) as exc:
        ai.complete(ai.load_config(user), [{"role": "user", "content": "привет"}])
    assert sent == {"url": f"{GATEWAY}/compat/chat/completions", "model": "anthropic/claude-sonnet-5-5"}
    assert exc.value.code == "ai-auth" and "Cloudflare" in exc.value.message and "Unauthorized" in exc.value.message


def test_bridge_api(db):
    user = make_user(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    assert client.get("/api/web/ai/bridge", headers=auth).json()["enabled"] is False
    res = client.put(
        "/api/web/ai/bridge", headers=auth, json={"enabled": True, "account_id": ACCOUNT, "gateway_id": "aimarketing", "token": "cf-token-123456"}
    )
    assert res.status_code == 200 and res.json()["active"] and "token" not in res.json()
    bad = client.put("/api/web/ai/bridge", headers=auth, json={"enabled": True, "account_id": "", "gateway_id": ""})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bridge-incomplete"
