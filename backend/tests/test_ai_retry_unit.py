"""Автоповтор временных сбоев ИИ в ai.complete и повтор оборвавшегося хода чата кнопкой."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app import ai, ai_agent
from app.yandex import YandexError

CFG = {"base_url": "https://ai.example", "model": "m", "headers": {}, "check_host": False, "limit_rub": None,
       "user_id": None}
WORKER_CFG = {**CFG, "bridge": True, "bridge_kind": "worker"}
OK = {"choices": [{"message": {"content": "готов"}}]}
PING = [{"role": "user", "content": "привет"}]
REGION = {"error": {"type": "forbidden", "message": "Request not allowed"}}


def _provider(monkeypatch, *replies):
    """Подделка httpx.post: отдаёт ответы по очереди (последний — дальше без конца); исключение — бросает.

    → список вызовов и список пауз между попытками.
    """
    calls, sleeps = [], []
    queue = list(replies)

    def post(url, **kw):
        calls.append(url)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        status, body, headers = reply if len(reply) == 3 else (*reply, {})
        return httpx.Response(status, json=body, headers=headers, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", post)
    monkeypatch.setattr(ai, "RETRY_PAUSES", (2, 5))
    monkeypatch.setattr(ai.time, "sleep", sleeps.append)
    return calls, sleeps


def test_transient_errors_are_retried_until_success(monkeypatch):
    calls, sleeps = _provider(monkeypatch, (503, {"error": {"message": "unavailable"}}), (529, {"error": {"message": "overloaded"}}), (200, OK))
    assert ai.complete(CFG, PING)["content"] == "готов"
    assert len(calls) == 3 and sleeps == [2, 5]

    calls, sleeps = _provider(monkeypatch, httpx.ConnectError("refused"), (200, OK))
    assert ai.complete(CFG, PING)["content"] == "готов"
    assert len(calls) == 2 and sleeps == [2]


def test_transient_error_after_all_attempts_says_how_many(monkeypatch):
    calls, sleeps = _provider(monkeypatch, (502, {"error": {"message": "bad gateway"}}))
    with pytest.raises(YandexError) as err:
        ai.complete(CFG, PING)
    assert len(calls) == 3 and sleeps == [2, 5]
    assert err.value.code == "ai-error" and err.value.message.endswith("(попыток: 3)")


@pytest.mark.parametrize(
    "reply, code",
    [
        ((401, {"error": {"message": "invalid key"}}), "ai-auth"),
        ((400, {"error": {"message": "bad request"}}), "ai-error"),
        # 524 — шлюз не дождался модели: это таймаут, его повторяют только кнопкой.
        ((524, {"error": {"message": "timeout"}}), "ai-error"),
        ((429, {"error": {"message": "You exceeded your current quota, check billing"}}), "ai-limit"),
        ((429, {"error": {"message": "rate limit"}}, {"retry-after": "60"}), "ai-limit"),
        (httpx.ReadTimeout("slow"), "ai-timeout"),
    ],
)
def test_lasting_errors_and_timeouts_are_not_retried(monkeypatch, reply, code):
    calls, sleeps = _provider(monkeypatch, reply)
    with pytest.raises(YandexError) as err:
        ai.complete(CFG, PING)
    assert len(calls) == 1 and sleeps == []
    assert err.value.code == code and "попыток" not in err.value.message


def test_rate_limit_waits_for_retry_after(monkeypatch):
    calls, sleeps = _provider(monkeypatch, (429, {"error": {"message": "rate limit"}}, {"retry-after": "7"}), (200, OK))
    assert ai.complete(CFG, PING)["content"] == "готов"
    assert len(calls) == 2 and sleeps == [7]


def test_worker_region_error_is_retried_and_explained(monkeypatch):
    # Cloudflare изредка выполняет Worker не в США — со второго раза запрос проходит.
    calls, sleeps = _provider(monkeypatch, (403, REGION), (200, OK))
    assert ai.complete(WORKER_CFG, PING)["content"] == "готов"
    assert len(calls) == 2

    calls, sleeps = _provider(monkeypatch, (403, REGION, {"cf-placement": "local-DME"}))
    with pytest.raises(YandexError) as err:
        ai.complete(WORKER_CFG, PING)
    assert len(calls) == 3 and err.value.code == "ai-region"
    message = err.value.message
    assert "Worker моста не в США" in message and "local-DME" in message and "Проверить мост" in message
    # Совет «переключитесь на свой Worker» — только для AI Gateway: Worker и так включён.
    assert "Переключите" not in message and message.endswith("(попыток: 3)")

    _provider(monkeypatch, (403, REGION))
    with pytest.raises(YandexError) as err:
        ai.complete({**CFG, "bridge": True, "bridge_kind": "gateway"}, PING)
    assert err.value.code == "ai-region" and "Переключите мост" in err.value.message


def test_stream_overload_mid_answer_is_retried(monkeypatch):
    from contextlib import contextmanager

    answers = ['data: {"error": {"type": "overloaded_error", "message": "Overloaded"}}\n\n',
               'data: {"choices": [{"delta": {"content": "готов"}}]}\n\ndata: [DONE]\n\n']

    @contextmanager
    def stream(method, url, **kw):
        sse = answers.pop(0)
        yield httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode(),
                             request=httpx.Request(method, url))

    monkeypatch.setattr(ai.httpx, "stream", stream)
    assert ai.complete({**CFG, "stream": True}, PING)["content"] == "готов"
    assert answers == []


# --- Кнопка «Повторить» в чате -------------------------------------------------------------------


class FakeConn:
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
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])


def _chat_row(status: str, minutes_ago: int = 0, error: str = "") -> dict:
    updated = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {"id": 7, "workspace_id": 3, "title": "Чат", "status": status, "error": error, "created_at": updated,
            "updated_at": updated}


def _retry_env(monkeypatch, row: dict):
    from app import jobs

    conn = FakeConn()
    submitted = []
    monkeypatch.setattr(ai_agent, "pool", conn)
    monkeypatch.setattr(ai_agent.ai, "load_config", lambda user_id, task: {"limit_rub": None})
    monkeypatch.setattr(ai_agent, "_require_chat", lambda c, user_id, chat_id, lock=False: row)
    monkeypatch.setattr(ai_agent, "_submit", lambda user_id, chat_id, marker: submitted.append(marker) or {"id": 1})
    monkeypatch.setattr(jobs, "public_job", lambda job: {"id": job["id"]})
    return conn, submitted


def test_retry_restarts_the_failed_turn_without_a_new_message(monkeypatch):
    conn, submitted = _retry_env(monkeypatch, _chat_row("error", error="Ошибка ИИ (502)"))
    assert ai_agent.retry_chat(1, 7) == {"job": {"id": 1}}
    assert any("status = 'running'" in sql for sql, _ in conn.sql)
    assert not any("ai_messages" in sql for sql, _ in conn.sql)
    # Каждый повтор — своя задача: зависшая прошлая попытка его не поглотит.
    ai_agent.retry_chat(1, 7)
    assert len(submitted) == 2 and submitted[0] != submitted[1] and submitted[0].startswith("r")


def test_retry_after_a_stalled_turn(monkeypatch):
    # «Ответ прервался»: ход висит в running дольше STALE_RUNNING — его тоже можно повторить.
    stale = int(ai_agent.STALE_RUNNING.total_seconds() // 60) + 1
    _conn, submitted = _retry_env(monkeypatch, _chat_row("running", minutes_ago=stale))
    ai_agent.retry_chat(1, 7)
    assert len(submitted) == 1


@pytest.mark.parametrize("row, code", [(_chat_row("running"), "busy"), (_chat_row("idle"), "nothing-to-retry")])
def test_retry_refuses_when_nothing_failed(monkeypatch, row, code):
    _conn, submitted = _retry_env(monkeypatch, row)
    with pytest.raises(YandexError) as err:
        ai_agent.retry_chat(1, 7)
    assert err.value.code == code and submitted == []
