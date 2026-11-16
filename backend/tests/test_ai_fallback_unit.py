"""Ошибки настройки моделей: понятная причина, без лишних повторов, переход на запасную модель."""

from contextlib import contextmanager

import httpx
import pytest

from app import ai
from app.yandex import YandexError

OK = {"choices": [{"message": {"content": "готов"}}], "model": "f"}
PING = [{"role": "user", "content": "привет"}]
CFG = {"base_url": "https://ai.example", "model": "m", "model_name": "m", "class_name": "Стандартная",
       "model_class": "standard", "headers": {}, "check_host": False, "user_id": None, "task": "chat",
       "workspace_id": None, "fallbacks": [{"id": "fast", "model": "f"}]}
NOT_SERVED = {"error": {"code": "model_not_found", "type": "server_error", "message":
              "Модель m сейчас не обслуживается выбранным источником Claude Kiro. Её обслуживают источники: Claude Max"}}
WRONG_ID = {"error": {"code": "model_not_found", "type": "invalid_request_error", "message":
            "Модель claude-opus-5.5 недоступна. Получите список доступных моделей через GET /v1/models."}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(ai, "_down", {})
    monkeypatch.setattr(ai, "RETRY_PAUSES", (2, 5))
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    monkeypatch.setattr(ai, "load_config", lambda user_id, task, ws, model_class=None, vision=False: {
        **CFG, "model": "f", "model_name": "f", "class_name": "Быстрая", "model_class": model_class, "fallbacks": []})


def _provider(monkeypatch, replies: dict):
    """Ответ по модели запроса: {модель: (статус, тело)}. → список моделей по порядку вызовов."""
    calls = []

    def post(url, json=None, **kw):
        calls.append(json["model"])
        status, body = replies[json["model"]]
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", post)
    return calls


@pytest.mark.parametrize(
    "reply, code, text",
    [
        ((503, NOT_SERVED), "ai-model", "не отдаёт модель m"),
        ((404, WRONG_ID), "ai-model", "claude-opus-5.5 недоступна"),
        ((404, {"error": {"message": "The model `gpt-9` does not exist"}}), "ai-model", "gpt-9"),
        ((400, {"error": {"message": "Your credit balance is too low to access the Anthropic API."}}), "ai-balance", "кончились деньги"),
        # Раньше это показывалось как «отклонил ключ».
        ((403, {"error": {"message": "token quota is not enough, token remain quota: $1.00"}}), "ai-balance", "квота"),
    ],
)
def test_setup_errors_are_named_and_not_retried(monkeypatch, reply, code, text):
    calls = _provider(monkeypatch, {"m": reply})
    with pytest.raises(YandexError) as err:
        ai.complete({**CFG, "fallbacks": []}, PING)
    assert calls == ["m"] and err.value.code == code and text in err.value.message


def test_stream_error_about_model_is_named(monkeypatch):
    @contextmanager
    def stream(method, url, **kw):
        sse = 'data: {"error": {"message": "model m is not available for this key"}}\n\n'
        yield httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode(),
                             request=httpx.Request(method, url))

    monkeypatch.setattr(ai.httpx, "stream", stream)
    with pytest.raises(YandexError) as err:
        ai.complete({**CFG, "stream": True, "fallbacks": []}, PING)
    assert err.value.code == "ai-model"


def test_unavailable_model_falls_back_and_is_skipped_next_time(monkeypatch):
    calls = _provider(monkeypatch, {"m": (503, NOT_SERVED), "f": (200, OK)})
    reply = ai.complete(CFG, PING)
    assert reply["content"] == "готов" and calls == ["m", "f"]
    assert reply["fallback"]["model_name"] == "f" and reply["fallback"]["class_name"] == "Быстрая"
    assert reply["fallback_from"] == "Стандартная" and reply["fallback"]["fallbacks"] == []
    # Отказавшую модель следующие минуты не зовут первой.
    calls.clear()
    assert ai.complete(CFG, PING)["fallback"]["model_name"] == "f" and calls == ["f"]


def test_all_models_fail_with_both_reasons(monkeypatch):
    _provider(monkeypatch, {"m": (404, WRONG_ID), "f": (400, {"error": {"message": "Your credit balance is too low"}})})
    with pytest.raises(YandexError) as err:
        ai.complete(CFG, PING)
    assert err.value.code == "ai-model"
    assert "claude-opus-5.5 недоступна" in err.value.message and "Запасная модель f тоже не ответила" in err.value.message


def test_key_errors_do_not_fall_back(monkeypatch):
    calls = _provider(monkeypatch, {"m": (401, {"error": {"message": "invalid key"}}), "f": (200, OK)})
    with pytest.raises(YandexError) as err:
        ai.complete(CFG, PING)
    assert err.value.code == "ai-auth" and calls == ["m"]


def test_config_lists_fallbacks(monkeypatch):
    monkeypatch.undo()
    classes = [
        {"id": "standard", "name": "Стандартная", "model": "m1", "enabled": True, "vision": True},
        {"id": "fast", "name": "Быстрая", "model": "m2", "enabled": True, "vision": False},
        {"id": "thinking", "name": "Думающая", "model": "m1", "enabled": True, "vision": True},
    ]
    for c in classes:
        c.update(units_in=1, units_out=1, cost_in=1, cost_out=1)
    monkeypatch.setattr(ai, "decrypt_token", lambda cipher: "sk")
    monkeypatch.setattr(ai, "model_classes", lambda: classes)
    monkeypatch.setattr(ai, "_service", lambda: {"provider": "openai", "base_url": "https://ai.example/v1",
                                                 "api_key_enc": "x", "temperature": None})
    monkeypatch.setattr(ai.ai_bridge, "target", lambda use, model: None)
    # Та же модель в другом классе — не запасная.
    assert ai.load_config(None, "chat")["fallbacks"] == [{"id": "fast", "model": "m2"}]
    assert ai.load_config(None, "chat", vision=True)["fallbacks"] == []
    assert ai.load_config(None, "test", model_class="fast")["fallbacks"] == []
    # Своя модель задачи (проверка сайта) — запасная — модель её класса.
    assert ai.load_config(None, "site_check", model="x")["fallbacks"][0] == {"id": "fast", "model": "m2"}


def test_similar_models_for_a_wrong_id(monkeypatch):
    ids = ["claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "gpt-6-sol"]

    def get(url, headers=None, **kw):
        assert url == "https://ai.example/models"
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(ai.httpx, "get", get)
    cfg = {**CFG, "provider": "openai"}
    assert ai.similar_models(cfg, "claude-opus-5.5")[0] == "claude-opus-5-5"
    assert ai.similar_models(cfg, "gpt-6-sol") == []  # такая есть — подсказывать нечего
    assert ai.similar_models({**cfg, "provider": "yandexgpt"}, "x") == []
