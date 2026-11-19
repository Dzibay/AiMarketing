"""Сбои ИИ и генератора картинок в кабинете — словами пользователя, технический текст — в журнал и лог.
Без БД и сети: вызовы ИИ и запись ai_errors подменены."""

import contextlib
from types import SimpleNamespace

import httpx
import pytest

from app import ad_stats, ai, ai_bridge, image_gen, query_cleaning, slices
from app.jobs import INTERNAL_ERROR, public_job
from app.yandex import YandexError

BANNED = ("Cloudflare", "ключ", "модел", "квот", "токен", "мост", "gpt", "OpenAI", "Yandex")
JPEG = b"\xff\xd8\xff" + b"0" * 64
CFG = {"model": "gpt-4o", "model_name": "gpt-4o", "model_class": "standard", "base_url": "https://api.example.com/v1",
       "headers": {}, "user_id": 1, "task": "classify", "workspace_id": 2}


def _exc(code: str, message: str, transient: bool = False) -> YandexError:
    exc = YandexError(code, message)
    exc.transient = transient
    return exc


@pytest.fixture
def saved(monkeypatch):
    """Что ушло бы в ai_errors: [(cfg, attempts, answered_by)]."""
    out = []
    monkeypatch.setattr(ai, "_save_attempts", lambda cfg, attempts, answered_by: out.append((cfg, attempts, answered_by)))
    monkeypatch.setattr(ai, "_down", {})
    monkeypatch.setattr(ai, "check_budget", lambda cfg, need=0.0: None)
    return out


# --- Тексты -------------------------------------------------------------------------------------


def test_new_texts_are_plain():
    for text in (ai.ERROR_TOO_MUCH, ai.ERROR_FORMAT, ai.ERROR_RESTART, INTERNAL_ERROR):
        assert not any(word.lower() in text.lower() for word in BANNED), text


@pytest.mark.parametrize("code, message, transient, kind", [
    # Лимит в минуту — перегрузка, хотя в тексте «tokens» и «limit».
    ("ai-limit", "Лимит запросов к ИИ: Rate limit reached for gpt-4o on tokens per min (TPM): Limit 30000", True, "busy"),
    ("ai-error", "Ошибка ИИ (429): превышен лимит токенов в минуту", True, "busy"),
    ("ai-error", "Ошибка ИИ (400): token limit per minute (TPM) exceeded", False, "busy"),
    # Один запрос больше минутного лимита — его надо сократить: это длина.
    ("ai-limit", "Request too large for gpt-4o on tokens per min (TPM): Limit 30000, Requested 50000. "
                 "The input or output tokens must be reduced in order to run successfully.", True, "long"),
    ("ai-error", "Ошибка ИИ (400): This model's maximum context length is 128000 tokens", False, "long"),
    # Gemini за мостом — свои слова о длине.
    ("ai-error", "Ошибка ИИ (400): The input token count (1200000) exceeds the maximum number of tokens allowed "
                 "(1048576).", False, "long"),
    ("ai-error", "Ошибка ИИ через мост Cloudflare (400): Request payload size exceeds the limit: 20971520 bytes.",
     False, "long"),
    # Отказ в самом запросе (4xx) — повтор через минуту упрётся в то же: «недоступен», а не «перегружен».
    ("ai-error", "Ошибка ИИ (422): Unsupported value: 'temperature' does not support 0.3", False, "down"),
    ("ai-error", "Ошибка ИИ через мост Cloudflare (404): Not Found", False, "down"),
    # 5xx и таймаут шлюза — перегрузка, даже без флага transient (в задачах его нет).
    ("ai-error", "Ошибка ИИ (502): bad gateway (попыток: 3)", False, "busy"),
    ("ai-error", "Ошибка ИИ (524): шлюз провайдера (обычно Cloudflare) оборвал ожидание", False, "busy"),
    ("ai-error", "Ошибка ИИ: model overloaded", True, "busy"),
    # Шлюз моста — настройка, сама не пройдёт.
    ("bridge-gateway", "Имя шлюза — латиница, цифры, дефис и подчёркивание", False, "down"),
    ("bridge-network", "Не удалось связаться с Worker'ом: ConnectError", False, "busy"),
    # Ответ не по схеме — повторить.
    ("ai-json", "ИИ вернул ответ не в том формате — попробуйте ещё раз или смените модель", False, "busy"),
])
def test_human_error_kinds(code, message, transient, kind):
    text, got = ai.human_error(code, message, transient=transient)
    assert got == kind
    want = {"busy": ai.ERROR_BUSY, "long": ai.ERROR_LONG, "down": ai.ERROR_DOWN}[kind]
    assert text == (ai.ERROR_FORMAT if code == "ai-json" else want)
    assert ai.is_provider_error(code)


def test_long_outside_chat_has_no_new_chat_advice():
    message = "Ошибка ИИ (400): prompt is too long: 210000 tokens > 200000 maximum"
    assert ai.human_error("ai-error", message) == (ai.ERROR_LONG, "long")
    assert ai.human_error("ai-error", message, chat=False) == (ai.ERROR_TOO_MUCH, "long")
    assert ai.user_error(YandexError("ai-error", message)) == ai.ERROR_TOO_MUCH
    assert ai.user_error(YandexError("ai-error", message), chat=True) == ai.ERROR_LONG
    assert "чат" not in ai.ERROR_TOO_MUCH


BROKEN = ("Ошибка ИИ (400): Invalid parameter: messages with role 'tool' must be a response to a preceding message "
          "with 'tool_calls'")


@pytest.mark.parametrize("message", [
    BROKEN,
    "Ошибка ИИ через мост Cloudflare (400): messages.3: `tool_use` ids were found without `tool_result` blocks",
])
def test_broken_history_offers_new_chat(message):
    # Та же история упадёт и на повторе — в чате «Новый чат», но без слов о длине разговора.
    assert ai.human_error("ai-error", message) == (ai.ERROR_RESTART, "long")
    assert "длинн" not in ai.ERROR_RESTART and not any(w.lower() in ai.ERROR_RESTART.lower() for w in BANNED)
    # Вне чата истории нет — «недоступен»; временный сбой с теми же словами — перегрузка.
    assert ai.human_error("ai-error", message, chat=False) == (ai.ERROR_DOWN, "down")
    assert ai.human_error("ai-error", message, transient=True) == (ai.ERROR_BUSY, "busy")


def test_public_job_rejected_request():
    assert public_job(_job("ai_chat", "ai-error", BROKEN))["error"] == ai.ERROR_RESTART
    assert public_job(_job("classify_queries", "ai-error", BROKEN))["error"] == ai.ERROR_DOWN
    # 5xx после всех повторов — перегрузка: флага transient в задаче нет, статус — из текста.
    assert public_job(_job("ads_ai", "ai-error", "Ошибка ИИ (503): unavailable (попыток: 3)"))["error"] == ai.ERROR_BUSY


def test_user_error():
    assert ai.user_error(_exc("ai-limit", "Лимит запросов к ИИ: rate limit", transient=True)) == ai.ERROR_BUSY
    assert ai.user_error(YandexError("ai-limit", "Лимит запросов к ИИ: insufficient_quota billing")) == ai.ERROR_DOWN
    assert ai.user_error(YandexError("ai-balance", "У провайдера ИИ кончились деньги (модель gpt-4o)")) == ai.ERROR_DOWN
    assert ai.user_error(YandexError("ai-json", "…смените модель")) == ai.ERROR_FORMAT
    # Не провайдер — как есть.
    assert ai.user_error(YandexError("ai-not-configured", ai.NOT_CONFIGURED)) == ai.NOT_CONFIGURED
    assert ai.user_error(YandexError("units-exhausted", "Единицы закончились")) == "Единицы закончились"
    assert ai.user_error(YandexError("crash")) == ai.ERROR_DOWN


@pytest.mark.parametrize("code, kind", [
    ("image-limit", "busy"), ("image-network", "busy"), ("image-timeout", "busy"),
    ("image-auth", "down"), ("image-balance", "down"), ("image-api", "down"),
])
def test_image_provider_codes(code, kind):
    # «Request too large» от генератора — не длина разговора.
    text, got = ai.human_error(code, "Ошибка генератора (413): request too large, ключ gpt-image-1")
    assert ai.is_provider_error(code) and got == kind
    assert text == {"busy": ai.ERROR_BUSY, "down": ai.ERROR_DOWN}[kind]


@pytest.mark.parametrize("code", ["image-error", "image-prompt", "image-not-configured"])
def test_image_local_codes_stay_as_is(code):
    assert not ai.is_provider_error(code)
    assert ai.user_error(YandexError(code, "Генератор вернул не JPG/PNG/GIF — Директ такую не примет")).startswith(
        "Генератор вернул не JPG"
    )


# --- Задачи -------------------------------------------------------------------------------------


def _job(kind: str, code: str, error: str) -> dict:
    return {
        "id": 1, "kind": kind, "status": "error", "attempts": 1, "max_attempts": 1,
        "progress_done": 0, "progress_total": 0, "progress_text": "", "error_code": code, "error": error,
    }


def test_public_job_long_and_internal():
    long = "Ошибка ИИ (400): This model's maximum context length is 128000 tokens"
    assert public_job(_job("classify_queries", "ai-error", long))["error"] == ai.ERROR_TOO_MUCH
    assert public_job(_job("ai_chat", "ai-error", long))["error"] == ai.ERROR_LONG
    assert public_job(_job("placements_ai", "ai-json", "…смените модель"))["error"] == ai.ERROR_FORMAT
    # Сбой кода — без str(исключения).
    crash = public_job(_job("ads_ai", "internal", "KeyError: 'verdict' at /app/slices.py"))
    assert crash["error"] == INTERNAL_ERROR and crash["error_code"] == "internal"
    assert public_job(_job("ads_ai", "token-expired", "Токен истёк"))["error"] == "Токен истёк"


# --- Частичные сбои: разметка, площадки, объявления ---------------------------------------------


class FakePool:
    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        return SimpleNamespace(fetchone=lambda: {"n": 1})


def _classify(monkeypatch, labels_side):
    rows = [{"norm": f"q{i}", "query": f"запрос {i}"} for i in range(3)]
    plan = {"ws": {}, "workspace_id": 2, "rows": rows, "labels": [], "rest": rows, "campaign_ids": []}
    monkeypatch.setattr(query_cleaning, "_classify_plan", lambda user_id, payload: plan)
    monkeypatch.setattr(query_cleaning, "suggest_labels", lambda *a: 0)
    monkeypatch.setattr(query_cleaning, "_classify_system", lambda user_id, plan: "sys")
    monkeypatch.setattr(query_cleaning, "_batch_tokens", lambda *a: (10, 10))
    monkeypatch.setattr(query_cleaning, "AI_BATCH", 1)
    monkeypatch.setattr(query_cleaning, "pool", FakePool())
    # Замок проекта и сверка пачки с уже размеченным — запросы к базе: здесь их нет.
    monkeypatch.setattr(query_cleaning, "_classify_lock", lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(query_cleaning, "_unlabelled", lambda workspace_id, batch, relabel: batch)
    monkeypatch.setattr(query_cleaning, "ai_labels", labels_side)
    monkeypatch.setattr(ai, "chars_per_token", lambda cfg: 3.0)
    monkeypatch.setattr(ai, "out_tokens_per_unit", lambda cfg, task, default: 10)
    monkeypatch.setattr(ai, "units_for", lambda cfg, *a: 1.0)
    monkeypatch.setattr(ai, "check_budget", lambda cfg, need=0.0: None)
    monkeypatch.setattr(ai, "usage_out", lambda acc, user_id: None)


def test_classify_partial_failure_is_human(monkeypatch):
    monkeypatch.setattr(ai, "load_config", lambda *a, **k: CFG)
    calls = []

    def labels(cfg, system, batch):
        calls.append(batch)
        if len(calls) == 2:
            raise YandexError("ai-balance", "У провайдера ИИ кончились деньги или квота (модель gpt-4o): insufficient_quota")
        return [{"query_norm": batch[0]["norm"], "query": batch[0]["query"], "verdict": "negative"}]

    _classify(monkeypatch, labels)
    out = query_cleaning.classify_queries(1, {"workspace_id": 2})
    assert (out["labeled"], out["ai"], out["ai_error"]) == (1, True, ai.ERROR_DOWN)


def test_classify_config_failure_is_human(monkeypatch):
    _classify(monkeypatch, lambda *a: [])

    def broken(*a, **k):
        raise YandexError("bridge-provider", "Мост Cloudflare не знает модель gpt-x")

    monkeypatch.setattr(ai, "load_config", broken)
    out = query_cleaning.classify_queries(1, {"workspace_id": 2})
    assert (out["ai"], out["ai_error"]) == (False, ai.ERROR_DOWN)
    # Оценка до запуска — тот же текст; «ИИ не подключён» — как есть.
    assert query_cleaning.estimate_classify(1, {"workspace_id": 2})["ai_error"] == ai.ERROR_DOWN

    def not_configured(*a, **k):
        raise YandexError("ai-not-configured", ai.NOT_CONFIGURED)

    monkeypatch.setattr(ai, "load_config", not_configured)
    assert query_cleaning.estimate_classify(1, {"workspace_id": 2})["ai_error"] == ai.NOT_CONFIGURED


def _json_side(first: dict, then: YandexError):
    calls = []

    def complete_json(cfg, messages):
        calls.append(cfg)
        if len(calls) > 1:
            raise then
        return first

    return complete_json


def test_placements_partial_failure_is_human(monkeypatch):
    monkeypatch.setattr(slices, "require_workspace", lambda user_id, ws_id: {"brief": {}})
    monkeypatch.setattr(slices, "_target_cpa_kopecks", lambda ws: None)
    monkeypatch.setattr(slices, "brief_prompt", lambda ws: "")
    monkeypatch.setattr(slices, "AI_BATCH", 1)
    monkeypatch.setattr(ai, "load_config", lambda *a, **k: CFG)
    monkeypatch.setattr(ai, "usage_out", lambda acc, user_id: None)
    busy = _exc("ai-limit", "Лимит запросов к ИИ: Rate limit reached for gpt-4o on tokens per min (TPM)", transient=True)
    monkeypatch.setattr(ai, "complete_json", _json_side({"items": [{"p": "a.ru", "v": "exclude", "r": "мусор"}]}, busy))
    items = [{"key": "a.ru", "label": "a.ru"}, {"key": "b.ru", "label": "b.ru"}]
    out = slices.ai_placements(1, {"workspace_id": 2, "items": items})
    assert out["verdicts"] == {"a.ru": {"verdict": "exclude", "reason": "мусор"}}
    assert out["error"] == ai.ERROR_BUSY


def test_rewrite_ads_partial_failure_is_human(monkeypatch):
    ads = {1: {"type": "TEXT_AD", "adgroup_row_id": 7}, 2: {"type": "TEXT_AD", "adgroup_row_id": 7}}
    monkeypatch.setattr(ad_stats, "require_workspace", lambda user_id, ws_id: {})
    monkeypatch.setattr(ad_stats, "_workspace_ads", lambda user_id, ws_id, ids: ads)
    monkeypatch.setattr(ad_stats, "_group_keywords", lambda user_id, ids: {})
    monkeypatch.setattr(ad_stats, "brief_prompt", lambda ws: "")
    monkeypatch.setattr(ad_stats.ai_memory, "account_prefs", lambda user_id: {})
    monkeypatch.setattr(ad_stats, "_ad_block", lambda *a: "объявление")
    monkeypatch.setattr(ad_stats, "clean_suggestion", lambda ad, got: {"titles": ["Окна"]})
    monkeypatch.setattr(ad_stats, "AI_BATCH", 1)
    monkeypatch.setattr(ai, "load_config", lambda *a, **k: CFG)
    monkeypatch.setattr(ai, "usage_out", lambda acc, user_id: None)
    off = YandexError("ai-json", "ИИ вернул ответ не в том формате — попробуйте ещё раз или смените модель")
    monkeypatch.setattr(ai, "complete_json", _json_side({"ads": [{"id": 1}]}, off))
    out = ad_stats.ai_rewrite_ads(1, {"workspace_id": 2, "items": [{"ad_row_id": 1}, {"ad_row_id": 2}]})
    assert out["suggestions"] == {1: {"titles": ["Окна"]}}
    assert out["error"] == ai.ERROR_FORMAT


# --- Журнал ai_errors: сбои мимо повторов, без задвоений ----------------------------------------


def test_failure_before_send_is_journaled(monkeypatch, saved):
    def once(cfg, messages, tools=None):
        raise YandexError("ai-url", "Адрес API указывает во внутреннюю сеть")

    monkeypatch.setattr(ai, "_complete_once", once)
    with pytest.raises(YandexError):
        ai.complete({**CFG, "fallbacks": []}, [{"role": "user", "content": "x"}])
    assert len(saved) == 1
    _, attempts, answered_by = saved[0]
    assert [(a["model"], a["code"], a["retried"], a["fallback"]) for a in attempts] == [("gpt-4o", "ai-url", False, False)]
    assert answered_by == ""


def test_send_failure_is_not_journaled_twice(monkeypatch, saved):
    def send(cfg, url, body, timeout, native=False):
        raise YandexError("ai-auth", "Провайдер ИИ отклонил ключ: 401")

    monkeypatch.setattr(ai, "_send", send)
    with pytest.raises(YandexError):
        ai.complete({**CFG, "fallbacks": []}, [{"role": "user", "content": "x"}])
    assert [a["code"] for a in saved[0][1]] == ["ai-auth"]


def test_fallback_bridge_failure_is_journaled(monkeypatch, saved):
    def send(cfg, url, body, timeout, native=False):
        raise YandexError("ai-model", "Провайдер ИИ не отдаёт модель gpt-4o")

    def fallback(cfg, entry, **kw):
        raise YandexError("bridge-provider", "Мост не знает модель claude-x")

    monkeypatch.setattr(ai, "_send", send)
    monkeypatch.setattr(ai, "_fallback_cfg", fallback)
    with pytest.raises(YandexError) as err:
        ai.complete({**CFG, "fallbacks": [{"id": "standard", "model": "claude-x"}]}, [{"role": "user", "content": "x"}])
    assert err.value.code == "ai-model"
    rows = [(a["model"], a["code"], a["fallback"]) for a in saved[0][1]]
    assert rows == [("gpt-4o", "ai-model", True), ("claude-x", "bridge-provider", False)]


def test_complete_json_format_failure_is_journaled(monkeypatch, saved):
    monkeypatch.setattr(ai, "complete", lambda cfg, messages, tools=None: {"content": "не JSON"})
    with pytest.raises(YandexError) as err:
        ai.complete_json(CFG, [{"role": "user", "content": "x"}])
    assert err.value.code == "ai-json" and ai.user_error(err.value) == ai.ERROR_FORMAT
    assert [(a["code"], a["retried"]) for a in saved[0][1]] == [("ai-json", False)] and saved[0][2] == ""
    assert len(saved) == 1


def _config_mocks(monkeypatch):
    cls = {"id": "standard", "model": "claude-x"}
    monkeypatch.setattr(ai, "_service", lambda: {"provider": "openai", "api_key_enc": "enc", "temperature": None})
    monkeypatch.setattr(ai, "_has_key", lambda row: True)
    monkeypatch.setattr(ai, "decrypt_token", lambda value: "key")
    monkeypatch.setattr(ai, "model_classes", lambda: [cls])
    monkeypatch.setattr(ai, "_resolve_class", lambda *a, **k: cls)
    monkeypatch.setattr(ai, "_fallback_chain", lambda *a: [])

    def target(kind, model):
        raise YandexError("bridge-provider", "Мост не знает модель claude-x")

    monkeypatch.setattr(ai_bridge, "target", target)


def test_load_config_bridge_failure_is_journaled_for_users_only(monkeypatch, saved):
    _config_mocks(monkeypatch)
    with pytest.raises(YandexError):
        ai.load_config(1, "classify", 2)
    assert len(saved) == 1
    cfg, attempts, answered_by = saved[0]
    assert (cfg["user_id"], cfg["task"], attempts[0]["model"], attempts[0]["code"]) == (1, "classify", "claude-x", "bridge-provider")
    # Проверки из админки — без журнала.
    with pytest.raises(YandexError):
        ai.load_config(None, "test")
    assert len(saved) == 1


# --- Картинки -----------------------------------------------------------------------------------


def _img_cfg():
    return {"provider": "openai", "base_url": image_gen.OPENAI_BASE, "model": "gpt-image-1", "model_name": "gpt-image-1",
            "headers": {}, "check_host": False, "bridge": False, "user_id": 1, "workspace_id": 2, "task": "images",
            "units": 0.0, "cost": None}


@pytest.mark.parametrize("response, code", [
    (httpx.Response(500, json={"error": {"message": "upstream"}}), "image-api"),
    (httpx.Response(200, text="<html>oops</html>"), "image-api"),
    (httpx.Response(402, json={"error": {"message": "insufficient_quota"}}), "image-balance"),
    (httpx.Response(429, json={"error": {"message": "You exceeded your current quota, check your plan and billing"}}),
     "image-balance"),
    (httpx.Response(429, json={"error": {"message": "Rate limit reached"}}), "image-limit"),
    (httpx.Response(403, json={"error": {"message": "bad key"}}), "image-auth"),
    (httpx.Response(200, json={"data": [{}]}), "image-error"),
])
def test_image_request_codes(monkeypatch, response, code):
    monkeypatch.setattr(image_gen, "_post", lambda cfg, body: response)
    with pytest.raises(YandexError) as err:
        image_gen._request(_img_cfg(), "окно")
    assert err.value.code == code


class _Stream:
    def __init__(self, status=200, error=None):
        self.status_code, self.error = status, error

    def __enter__(self):
        if self.error:
            raise self.error
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        yield JPEG


@pytest.mark.parametrize("stream, code", [
    (_Stream(404), "image-api"),
    (_Stream(error=httpx.ReadTimeout("slow")), "image-timeout"),
    (_Stream(error=httpx.ConnectError("refused")), "image-network"),
])
def test_image_download_codes(monkeypatch, stream, code):
    monkeypatch.setattr(image_gen.ai, "_check_url_resolves_public", lambda url: None)
    monkeypatch.setattr(image_gen.httpx, "stream", lambda *a, **k: stream)
    with pytest.raises(YandexError) as err:
        image_gen._download("https://cdn.example.com/a.jpg")
    assert err.value.code == code


def test_image_download_ok(monkeypatch):
    monkeypatch.setattr(image_gen.ai, "_check_url_resolves_public", lambda url: None)
    monkeypatch.setattr(image_gen.httpx, "stream", lambda *a, **k: _Stream())
    assert image_gen._download("https://cdn.example.com/a.jpg") == JPEG


def test_generate_errors_are_human_and_logged(monkeypatch, caplog):
    monkeypatch.setattr(image_gen, "load_config", lambda user_id, workspace_id=None: _img_cfg())
    monkeypatch.setattr(image_gen.billing, "check", lambda user_id, need=0.0: None)
    monkeypatch.setattr(image_gen.billing, "units_left", lambda user_id: None)
    monkeypatch.setattr(image_gen, "_record", lambda c, prompt: None)
    fails = iter([
        YandexError("image-api", "Ошибка генератора (500): upstream gpt-image-1"),
        YandexError("image-error", "Генератор вернул не JPG/PNG/GIF — Директ такую не примет"),
        _exc("image-limit", "Лимит запросов к генератору: Rate limit reached"),
    ])
    calls = []

    def request(cfg, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return JPEG
        raise next(fails)

    monkeypatch.setattr(image_gen, "_request", request)
    with caplog.at_level("WARNING", logger="app.image_gen"):
        out = image_gen.generate(1, "окно", count=4)
    assert out["images"] == [JPEG]
    assert out["errors"] == [ai.ERROR_DOWN, "Генератор вернул не JPG/PNG/GIF — Директ такую не примет", ai.ERROR_BUSY]
    # Технический текст — в лог: в ai_errors картинки не пишутся.
    assert "image-api" in caplog.text and "upstream gpt-image-1" in caplog.text


def test_cabinet_generate_hides_model(monkeypatch):
    from app.api import ai as ai_api

    out = {"images": [{"hash": "h1", "account_id": 3}], "errors": [], "model": "gpt-image-1"}
    monkeypatch.setattr(ai_api.ai_agent, "generate_images", lambda *a, **k: out)
    body = ai_api.ImageGenerateBody(account_id=3, prompt="окно")
    assert ai_api.workspace_generate_images(2, body, user_id=1) == {"images": out["images"], "errors": []}
