"""Claude через родной Messages API (кэш начала запроса) и prompt_cache_key для OpenAI."""

import json
from contextlib import contextmanager

import httpx

from app import ai, ai_bridge

CFG = {"base_url": "https://bridge.example/anthropic/v1", "model": "claude-opus-5-5", "headers": {}, "check_host": False,
       "user_id": None, "stream": True,
       "anthropic": {"url": "https://bridge.example/anthropic/v1/messages", "model": "claude-opus-5-5",
                     "headers": {"x-api-key": "k", "anthropic-version": ai.ANTHROPIC_VERSION}}}
TOOLS = [
    {"type": "function", "function": {"name": "get_campaign", "description": "Кампания", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "list_ads", "description": "Объявления", "parameters": {"type": "object", "properties": {}}}},
]
HISTORY = [
    {"role": "system", "content": "Ты помощник."},
    {"role": "user", "content": [{"type": "text", "text": "Что с кампанией?"},
                                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call.1", "type": "function", "function": {"name": "get_campaign", "arguments": '{"id": 5}'}},
        {"id": "call_2", "type": "function", "function": {"name": "list_ads", "arguments": "не json"}},
    ]},
    {"role": "tool", "tool_call_id": "call.1", "content": '{"ok": true}'},
    {"role": "tool", "tool_call_id": "call_2", "content": ""},
]


def _sse(*events) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


ANSWER = _sse(
    {"type": "message_start", "message": {"model": "claude-opus-5-5-20261101", "usage": {
        "input_tokens": 120, "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 300, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Смотрю "}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "кампанию."}},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_campaign", "input": {}}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"id"'}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ": 5}"}},
    {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_2", "name": "list_ads", "input": {}}},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 40}},
    {"type": "message_stop"},
)


def _stream(monkeypatch, *replies):
    """Подделка httpx.stream: ответы по очереди — байты SSE или (статус, json). → список (url, тело, заголовки)."""
    calls = []
    queue = list(replies)

    @contextmanager
    def stream(method, url, json=None, headers=None, **kw):
        calls.append((url, json, headers))
        reply = queue.pop(0)
        request = httpx.Request(method, url)
        if isinstance(reply, bytes):
            yield httpx.Response(200, headers={"content-type": "text/event-stream"}, content=reply, request=request)
        else:
            yield httpx.Response(reply[0], json=reply[1], request=request)

    monkeypatch.setattr(ai.httpx, "stream", stream)
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    return calls


def test_body_has_cache_points_and_claude_shapes():
    body = ai.anthropic_body(HISTORY, TOOLS, "claude-opus-5-5")
    assert body["system"] == [{"type": "text", "text": "Ты помощник.", "cache_control": {"type": "ephemeral"}}]
    assert "temperature" not in body and body["max_tokens"] == ai.ANTHROPIC_MAX_TOKENS
    tools = body["tools"]
    assert [t["name"] for t in tools] == ["get_campaign", "list_ads"]
    assert tools[0]["input_schema"]["properties"]["id"] == {"type": "integer"} and "cache_control" not in tools[0]
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    user, assistant, results = body["messages"]
    assert user["role"] == "user" and user["content"][1]["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}
    # Пустой текст ассистента не отправляется; id — без точки; кривые аргументы — пустой объект.
    assert [b["type"] for b in assistant["content"]] == ["tool_use", "tool_use"]
    assert assistant["content"][0]["id"] == "call_1" and assistant["content"][0]["input"] == {"id": 5}
    assert assistant["content"][1]["input"] == {}
    # Ответы инструментов шага — одно сообщение user; точка кэша — на последнем блоке.
    assert results["role"] == "user" and [b["tool_use_id"] for b in results["content"]] == ["call_1", "call_2"]
    assert results["content"][1]["content"] == "(пусто)"
    assert results["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in results["content"][0]


def test_cache_point_stays_before_the_service_note(monkeypatch):
    # Сводка сервиса меняется от шага к шагу: точка кэша — на последнем блоке до неё, иначе кэш не попадал бы.
    note = {"role": "user", "content": "[Сводка сервиса] Цели согласованы."}
    body = ai.anthropic_body([*HISTORY, note], TOOLS, "claude-opus-5-5", volatile_tail=1)
    results = body["messages"][-1]
    assert [b["type"] for b in results["content"]] == ["tool_result", "tool_result", "text"]
    assert results["content"][1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in results["content"][2] and results["content"][2]["text"].startswith("[Сводка")
    # Без пометки точка кэша — как раньше, на самом последнем блоке.
    plain = ai.anthropic_body([*HISTORY, note], TOOLS, "claude-opus-5-5")["messages"][-1]["content"]
    assert "cache_control" in plain[2] and "cache_control" not in plain[1]

    # Пометка доезжает от хода агента до запроса в настройках вызова.
    calls = _stream(monkeypatch, ANSWER)
    ai.complete({**CFG, "volatile_tail": 1}, [*HISTORY, note], TOOLS)
    sent = calls[0][1]["messages"][-1]["content"]
    assert "cache_control" in sent[1] and "cache_control" not in sent[2]


def test_stream_is_read_into_the_usual_reply_with_cached_tokens(monkeypatch):
    calls = _stream(monkeypatch, ANSWER)
    reply = ai.complete(CFG, HISTORY, TOOLS)
    url, body, headers = calls[0]
    assert url.endswith("/anthropic/v1/messages") and body["stream"] is True and headers["x-api-key"] == "k"
    assert reply["content"] == "Смотрю кампанию."
    assert reply["tool_calls"] == [
        {"id": "toolu_1", "name": "get_campaign", "arguments": '{"id": 5}'},
        {"id": "toolu_2", "name": "list_ads", "arguments": "{}"},
    ]
    usage = reply["usage"]
    assert usage["tokens_in"] == 9420 and usage["tokens_cached"] == 9000 and usage["tokens_out"] == 40
    assert not usage["estimated"] and reply["model"] == "claude-opus-5-5-20261101"


def test_rejected_native_request_goes_the_old_way(monkeypatch):
    old = b'data: {"choices": [{"delta": {"content": "\\u0433\\u043e\\u0442\\u043e\\u0432"}}]}\n\ndata: [DONE]\n\n'
    calls = _stream(monkeypatch, (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}), old)
    assert ai.complete(CFG, HISTORY, TOOLS)["content"] == "готов"
    assert [c[0] for c in calls] == [CFG["anthropic"]["url"], "https://bridge.example/anthropic/v1/chat/completions"]
    # Старый путь получает запрос в виде OpenAI.
    assert calls[1][1]["messages"] == HISTORY


def test_overload_mid_native_stream_is_retried(monkeypatch):
    calls = _stream(monkeypatch, _sse({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}), ANSWER)
    assert ai.complete(CFG, HISTORY, TOOLS)["content"] == "Смотрю кампанию."
    assert len(calls) == 2 and all(c[0] == CFG["anthropic"]["url"] for c in calls)


def test_native_json_without_stream_and_refusal(monkeypatch):
    def post(url, json=None, headers=None, **kw):
        body = {"model": "claude-opus-5-5", "content": [], "stop_reason": "refusal",
                "usage": {"input_tokens": 10, "output_tokens": 0}}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", post)
    reply = ai.complete({**CFG, "stream": False}, [{"role": "user", "content": "привет"}])
    assert reply["content"] == ai.REFUSAL_TEXT and reply["usage"]["tokens_in"] == 10


def test_prompt_cache_key_only_for_openai(monkeypatch):
    sent = []

    def post(url, json=None, **kw):
        sent.append(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", post)
    plain = {"base_url": "https://api.openai.com/v1", "model": "gpt-6-luna", "headers": {}, "check_host": False,
             "user_id": None, "cache_key": "tolk-chat-7"}
    ai.complete({**plain, "openai_cache": True}, [{"role": "user", "content": "привет"}])
    ai.complete(plain, [{"role": "user", "content": "привет"}])
    assert sent[0]["prompt_cache_key"] == "tolk-chat-7" and "prompt_cache_key" not in sent[1]


def test_config_routes_claude_natively_and_marks_openai(monkeypatch):
    cls = {"id": "standard", "name": "Стандартная", "model": "claude-opus-5-5", "units_in": 1, "units_out": 1,
           "cost_in": 1, "cost_out": 1, "enabled": True, "vision": False}
    monkeypatch.setattr(ai, "decrypt_token", lambda cipher: "sk-test")
    monkeypatch.setattr(ai, "model_classes", lambda: [cls])
    monkeypatch.setattr(ai, "_resolve_class", lambda *a, **kw: cls)
    monkeypatch.setattr(ai, "_has_key", lambda row: True)
    monkeypatch.setattr(ai_bridge, "target", lambda use, model: None)
    service = {"provider": "openai", "base_url": "https://api.anthropic.com/v1", "api_key_enc": "x", "temperature": None}
    monkeypatch.setattr(ai, "_service", lambda: service)
    cfg = ai.load_config(None)
    native = cfg["anthropic"]
    assert native["url"] == "https://api.anthropic.com/v1/messages" and native["model"] == "claude-opus-5-5"
    assert native["headers"]["x-api-key"] == "sk-test" and "Authorization" not in native["headers"]
    assert not cfg["openai_cache"]

    cls["model"] = "gpt-6-luna"
    service["base_url"] = ai.OPENAI_BASE
    cfg = ai.load_config(None)
    assert cfg["anthropic"] is None and cfg["openai_cache"]


def test_bridge_gives_native_address(monkeypatch):
    monkeypatch.setattr(ai_bridge, "_decrypt", lambda value, what: "secret")
    worker = {"enabled": True, "kind": "worker", "worker_url": "https://w.example", "worker_token_enc": "x", "use_chat": True}
    monkeypatch.setattr(ai_bridge, "_row", lambda: worker)
    assert ai_bridge.target("chat", "claude-opus-5-5")["messages_url"] == "https://w.example/anthropic/v1/messages"
    assert ai_bridge.target("chat", "gpt-6-luna")["messages_url"] == ""

    gateway = {"enabled": True, "kind": "gateway", "account_id": "acc", "gateway_id": "gw", "use_chat": True}
    monkeypatch.setattr(ai_bridge, "_row", lambda: gateway)
    target = ai_bridge.target("chat", "anthropic/claude-opus-5-5")
    assert target["messages_url"] == f"{ai_bridge.GATEWAY_HOST}/acc/gw/anthropic/v1/messages"
    assert target["native_model"] == "claude-opus-5-5"
