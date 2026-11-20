"""Чат ИИ в кабинете — без технического текста провайдера: ошибки инструментов, генерация картинок, ошибки хода
и старые строки ленты. Без БД, модели и сети: pool и вызовы подменены."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import ai, ai_agent, audiences, billing, image_gen, plans
from app.yandex import YandexError

BANNED = ("Cloudflare", "мост", "провайдер", "Провайдер", "модел", "квот", "токен", "ключ", "gpt", "(502)", "Error")


def _exc(code: str, message: str | None = None, transient: bool = False) -> YandexError:
    exc = YandexError(code, message)
    exc.transient = transient
    return exc


def _plain(text: str) -> bool:
    return not any(word in text for word in BANNED)


class FakeConn:
    """pool.connection() без БД: любой запрос — пустой ответ, действие — «ещё одобрено»."""

    def __init__(self):
        self.sql = []

    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: {"id": 1, "status": "approved", "n": 0})


@pytest.fixture
def feed(monkeypatch):
    """Что ушло бы в ленту (ai_messages) и в статус чата: [(role, content, meta)] и [(status, error)]."""
    out = SimpleNamespace(messages=[], status=[], conn=FakeConn())
    monkeypatch.setattr(ai_agent, "pool", out.conn)
    monkeypatch.setattr(
        ai_agent, "_insert_message",
        lambda conn, chat_id, role, content="", meta=None, **kw: out.messages.append((role, content, meta)) or {"id": 50},
    )
    monkeypatch.setattr(ai_agent, "_set_status", lambda chat_id, status, error="": out.status.append((status, error)))
    return out


# --- Ошибки инструментов ------------------------------------------------------------------------------


def test_tool_error_hides_provider_text(caplog):
    with caplog.at_level("WARNING", logger=ai_agent.log.name):
        text = ai_agent._tool_error(_exc("image-auth", "Генератор картинок отклонил ключ: invalid api key"), "generate_images")
    assert text == ai.ERROR_DOWN
    # Технический текст — в лог сервера.
    assert "image-auth" in caplog.text and "invalid api key" in caplog.text
    assert ai_agent._tool_error(_exc("ai-error", "Ошибка ИИ через мост Cloudflare (502): Bad gateway", True)) == ai.ERROR_BUSY
    assert ai_agent._tool_error(_exc("bridge-gateway", "Шлюз моста не найден")) == ai.ERROR_DOWN
    # «Слишком длинно» внутри инструмента — не про длину разговора: новый чат там не поможет.
    assert ai_agent._tool_error(_exc("ai-error", "Ошибка ИИ (400): prompt is too long")) == ai.ERROR_TOO_MUCH


def test_tool_error_keeps_direct_and_argument_errors():
    # По ним модель исправляет вызов — текст как есть, без текста — код.
    assert ai_agent._tool_error(_exc("args", "count — число картинок")) == "count — число картинок"
    assert ai_agent._tool_error(_exc("api", "Неверная ключевая фраза (8000)")) == "Неверная ключевая фраза (8000)"
    assert ai_agent._tool_error(_exc("not-found")) == "not-found"
    assert ai_agent._tool_error(_exc("image-error", "Картинка больше 5 МБ — Директ её не примет")) == (
        "Картинка больше 5 МБ — Директ её не примет"
    )


def test_handle_call_stores_human_text_and_loop_guard_quotes_it(monkeypatch, feed):
    def boom(ctx, args):
        raise _exc("image-balance", "Генератор картинок: у провайдера кончилась квота (gpt-image-1)")

    monkeypatch.setitem(ai_agent.READ_TOOLS, "generate_images", boom)
    ctx = SimpleNamespace(general=False, ws_id=None, user_id=1)
    call = {"id": "c1", "name": "generate_images", "arguments": json.dumps({"prompt": "окно"})}
    assert ai_agent._handle_call(ctx, 7, 50, call) == 0
    role, content, _meta = feed.messages[-1]
    payload = json.loads(content)
    assert role == "tool" and payload == {"ok": False, "error": ai.ERROR_DOWN}
    # Остановка «не проходит несколько раз» (она же — в колокольчик) цитирует понятный текст.
    guard, stop = ai_agent._LoopGuard(), ""
    for _ in range(ai_agent.MAX_SAME_ERROR):
        stop = guard.stuck([call], {"c1": payload["error"]})
    assert stop and "Генерация картинок" in stop and _plain(stop)


def test_handle_call_keeps_tool_validation_error(monkeypatch, feed):
    def boom(ctx, args):
        raise _exc("args", "Укажите prompt")

    monkeypatch.setitem(ai_agent.READ_TOOLS, "generate_images", boom)
    ctx = SimpleNamespace(general=False, ws_id=None, user_id=1)
    ai_agent._handle_call(ctx, 7, 50, {"id": "c1", "name": "generate_images", "arguments": "{}"})
    assert json.loads(feed.messages[-1][1]) == {"ok": False, "error": "Укажите prompt"}


def test_run_action_stores_human_text(monkeypatch, feed):
    def execute(ctx, args):
        raise _exc("ai-auth", "Провайдер ИИ отклонил ключ: invalid x-api-key")

    monkeypatch.setitem(ai_agent.WRITE_TOOLS, "test_write", (None, execute))
    action = {"id": 3, "tool": "test_write", "args": {}, "chat_id": 7, "tool_call_id": "c1", "detached_at": None}
    ai_agent._run_action(SimpleNamespace(general=False, action=None), action)
    update = next(params for sql, params in feed.conn.sql if sql.startswith("UPDATE ai_actions SET status"))
    assert update[0] == "error" and update[2] == ai.ERROR_DOWN
    assert json.loads(feed.messages[-1][1]) == {"ok": False, "error": ai.ERROR_DOWN}


# --- Генерация картинок -------------------------------------------------------------------------------


def test_generate_into_has_no_model_and_dedupes_errors(monkeypatch):
    monkeypatch.setattr(audiences, "account_access", lambda user_id, account_id: ("t", "acc"))
    monkeypatch.setattr(image_gen, "generate", lambda user_id, prompt, count, workspace_id: {
        "images": [b"1", b"2"], "errors": [ai.ERROR_DOWN, ai.ERROR_DOWN], "model": "gpt-image-1"})
    monkeypatch.setattr(image_gen, "upload", lambda token, login, images, name: (
        [{"hash": "h1", "preview_url": "u1"}], ["Директ не принял картинку 2"]))
    out = ai_agent._generate_into(1, 2, 4, "окно", 4, name="ИИ")
    assert out == {
        "images": [{"hash": "h1", "preview_url": "u1", "account_id": 4}],
        "errors": [ai.ERROR_DOWN, "Директ не принял картинку 2"],
    }
    # Директ не принял ни одной — причина из его ответа, а не из сбоя генератора.
    monkeypatch.setattr(image_gen, "upload", lambda token, login, images, name: ([], ["Слишком маленькая"]))
    with pytest.raises(YandexError) as err:
        ai_agent._generate_into(1, 2, 4, "окно", 4, name="ИИ")
    assert err.value.message == "Директ не принял сгенерированные картинки: Слишком маленькая"


# --- Ошибка хода --------------------------------------------------------------------------------------


@pytest.fixture
def turn(monkeypatch, feed):
    """run_chat без БД и модели до вызова ai.complete."""
    monkeypatch.setattr(ai_agent, "_require_chat", lambda conn, user_id, chat_id: {"workspace_id": 2, "model_class": None})
    monkeypatch.setattr(ai_agent, "_wants_vision", lambda chat_id: False)
    monkeypatch.setattr(ai, "load_config", lambda *a, **k: {"class_name": "Стандарт"})
    monkeypatch.setattr(ai_agent, "Context", lambda user_id, ws_id: SimpleNamespace(general=False, ws_id=ws_id, user_id=user_id))
    monkeypatch.setattr(ai_agent, "_recover_stale_actions", lambda conn, chat_id: 0)
    monkeypatch.setattr(ai_agent, "_system_prompt", lambda ctx: "система")
    monkeypatch.setattr(ai_agent, "_history", lambda *a, **k: [{"role": "user", "content": "Привет"}])
    monkeypatch.setattr(ai_agent, "_touch", lambda chat_id: None)
    monkeypatch.setattr(ai_agent, "_build_memory", lambda ctx: "")
    monkeypatch.setattr(ai_agent, "sync_build", lambda ctx: None)
    return feed


@pytest.mark.parametrize("where", ["load_config", "complete"])
def test_turn_error_without_message_is_not_a_bare_code(monkeypatch, turn, where):
    def boom(*a, **k):
        raise YandexError("not-found")

    monkeypatch.setattr(ai, where, boom)
    assert ai_agent.run_chat(1, 7) == {"status": "error"}
    assert turn.status[-1] == ("error", ai.ERROR_DOWN)
    assert turn.messages[-1] == ("error", ai.ERROR_DOWN, {"error_kind": "down"})


def test_turn_error_keeps_user_facing_text(monkeypatch, turn):
    def boom(*a, **k):
        raise YandexError("units-exhausted", billing.NOT_ENOUGH)

    monkeypatch.setattr(ai, "complete", boom)
    ai_agent.run_chat(1, 7)
    assert turn.messages[-1] == ("error", billing.NOT_ENOUGH, {"error_kind": "plan"})


# --- Старые строки ленты (до ai.human_error) ----------------------------------------------------------

SIZE = " (запрос ≈ 95000 токенов, сообщений: 40)"


@pytest.mark.parametrize("stored", [
    "Ошибка ИИ через мост Cloudflare (502): Bad gateway",
    "Ошибка ИИ (503): overloaded",
    "Лимит запросов к ИИ: Rate limit reached for requests",
    "Не удалось связаться с ИИ: ConnectError",
    "Провайдер ИИ или мост Cloudflare (ключ провайдера или токен моста) отклонил ключ: invalid x-api-key",
    "У провайдера ИИ кончились деньги или квота (модель gpt-4o): insufficient_quota",
    "Провайдер ИИ не отдаёт модель deepseek-v3: model not found",
    "ИИ вернул ответ не в том формате — попробуйте ещё раз или смените модель",
    "Стандарт: укажите модель или выключите класс",
])
def test_legacy_technical_error_becomes_down(stored):
    assert ai_agent._shown_error(stored + SIZE) == (ai.ERROR_DOWN, "down")
    assert ai_agent._shown_error(stored) == (ai.ERROR_DOWN, "down")


def test_legacy_too_long_error_offers_new_chat():
    stored = "Ошибка ИИ (400): prompt is too long: 210000 tokens > 200000 maximum"
    assert ai_agent._shown_error(stored + SIZE) == (ai.ERROR_LONG, "long")
    # Провайдер отклонил историю — тоже «Новый чат», но без слов о длине.
    broken = "Ошибка ИИ (400): messages with role 'tool' must be a response to a preceding message with 'tool_calls'"
    assert ai_agent._shown_error(broken + SIZE) == (ai.ERROR_RESTART, "long")


@pytest.mark.parametrize("text", [
    "Сбой при обращении к ИИ",
    "ИИ не ответил за отведённое время",
    "Непонятный ответ ИИ",
    "Ответ прервался — повторите запрос",
    ai.NOT_CONFIGURED,
    ai.NO_VISION,
    ai.ERROR_BUSY,
    ai.ERROR_LONG,
    ai.ERROR_DOWN,
    ai.ERROR_TOO_MUCH,
    ai.ERROR_FORMAT,
    plans.EXPIRED,
    plans.PROJECT_LOCKED,
    billing.NOT_ENOUGH,
    billing.exhausted_text(datetime(2026, 12, 1, tzinfo=timezone.utc)),
    "Решите самостоятельно, какие ключевые фразы оставить",
])
def test_plain_error_stays_as_is(text):
    assert ai_agent._shown_error(text) == (text, "")
    # Размер запроса из старой строки — не для пользователя.
    assert ai_agent._shown_error(text + SIZE) == (text, "")


def _message(content: str, meta: dict | None = None) -> dict:
    return {"id": 1, "role": "error", "content": content, "meta": meta}


def test_message_out_converts_only_legacy_rows():
    legacy = ai_agent._message_out(_message("Ошибка ИИ через мост Cloudflare (502): Bad gateway" + SIZE))
    assert (legacy["content"], legacy["error_kind"]) == (ai.ERROR_DOWN, "down")
    plain = ai_agent._message_out(_message("Сбой при обращении к ИИ" + SIZE))
    assert (plain["content"], plain["error_kind"]) == ("Сбой при обращении к ИИ", "")
    # Строка с видом уже записана понятной — как есть.
    fresh = ai_agent._message_out(_message(ai.ERROR_LONG, {"error_kind": "long"}))
    assert (fresh["content"], fresh["error_kind"]) == (ai.ERROR_LONG, "long")
    # Ответы ИИ и результаты инструментов не трогаем.
    assistant = ai_agent._message_out({**_message("Модель gpt-4o через Cloudflare"), "role": "assistant"})
    assert assistant["content"] == "Модель gpt-4o через Cloudflare" and "error_kind" not in assistant


def _tool(name: str, payload) -> dict:
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"id": 2, "role": "tool", "content": content, "tool_name": name, "tool_call_id": "c1", "meta": None}


def test_message_out_hides_provider_text_in_legacy_tool_rows():
    # Строка до ai.user_error: ответ генератора с ключом и модель генератора — в экспертном режиме не видны.
    legacy = _tool("generate_images", {
        "account_id": 4, "images": [{"hash": "h1", "account_id": 4}],
        "errors": ["Генератор картинок отклонил ключ: invalid_api_key sk-…", "Генератор картинок отклонил ключ: invalid_api_key sk-…",
                   "Директ не принял картинку 2"],
        "model": "gpt-image-1",
    })
    out = json.loads(ai_agent._message_out(legacy)["content"])
    assert out == {"account_id": 4, "images": [{"hash": "h1", "account_id": 4}],
                   "errors": [ai.ERROR_DOWN, "Директ не принял картинку 2"]}
    for stored, shown in [
        ("Ошибка ИИ через мост Cloudflare (502): Bad gateway", ai.ERROR_DOWN),
        ("Провайдер ИИ не отдаёт модель deepseek-v3: model not found", ai.ERROR_DOWN),
        ("Лимит запросов к генератору: Rate limit reached for gpt-image-1", ai.ERROR_DOWN),
        # Внутри инструмента «слишком длинно» — не про разговор (как ai.user_error).
        ("Ошибка ИИ (400): prompt is too long: 210000 tokens > 200000 maximum", ai.ERROR_TOO_MUCH),
    ]:
        content = ai_agent._message_out(_tool("classify", {"ok": False, "error": stored}))["content"]
        assert json.loads(content) == {"ok": False, "error": shown} and _plain(content)


def test_message_out_keeps_other_tool_rows():
    # Ошибки Директа и аргументов, новые строки и обычные результаты — байт в байт.
    for payload in [
        {"ok": False, "error": "Неверная ключевая фраза (8000)"},
        {"ok": False, "error": ai.ERROR_DOWN},
        {"account_id": 4, "images": [], "errors": [ai.ERROR_BUSY, ai.ERROR_BUSY]},
        {"rows": [{"placement": "cloudflare.com", "clicks": 3}]},
        "не JSON: Ошибка ИИ (502)",
    ]:
        row = _tool("generate_images", payload)
        assert ai_agent._message_out(row)["content"] == row["content"]


def test_message_out_cuts_long_tool_rows_after_cleaning():
    rows = [{"text": "x" * 40} for _ in range(60)]
    row = _tool("get_campaign", {"ok": False, "error": "Ошибка ИИ (502): Bad gateway", "rows": rows})
    content = ai_agent._message_out(row)["content"]
    assert content.endswith("…") and len(content) == 1501 and _plain(content)


def _action(error: str) -> dict:
    return {"id": 3, "chat_id": 7, "message_id": 5, "tool_call_id": "c1", "tool": "test_write", "args": {},
            "category": "", "summary": "", "status": "error", "auto": False, "error": error}


def test_action_out_hides_legacy_provider_error():
    legacy = "Провайдер ИИ или мост Cloudflare (ключ провайдера или токен моста) отклонил ключ: invalid x-api-key"
    assert ai_agent._action_out(_action(legacy))["error"] == ai.ERROR_DOWN
    # Директ и прочие — как есть.
    assert ai_agent._action_out(_action("Кампания не найдена"))["error"] == "Кампания не найдена"
    assert ai_agent._action_out(_action(ai_agent.STALE_ACTION_ERROR))["error"] == ai_agent.STALE_ACTION_ERROR
    assert ai_agent._action_out(_action(""))["error"] == ""


def _chat(status: str, error: str) -> dict:
    now = datetime.now(timezone.utc)
    return {"id": 7, "workspace_id": 2, "title": "Чат", "status": status, "error": error,
            "created_at": now, "updated_at": now}


def test_chat_out_converts_legacy_error_like_the_feed():
    stored = "Ошибка ИИ через мост Cloudflare (502): Bad gateway"
    chat = ai_agent._chat_out(_chat("error", stored))
    last = ai_agent._message_out(_message(stored + SIZE))
    assert chat["error"] == ai.ERROR_DOWN
    # Плашка чата и строка ленты совпадают — интерфейс не показывает ошибку дважды (errorInFeed).
    assert last["content"].startswith(chat["error"])
    assert ai_agent._chat_out(_chat("error", ai.ERROR_BUSY))["error"] == ai.ERROR_BUSY
    assert ai_agent._chat_out(_chat("idle", ""))["error"] == ""
