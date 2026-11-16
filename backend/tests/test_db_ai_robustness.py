"""Надёжность ИИ-чата: прерванные действия не выполняются повторно, старые шаги долгого хода укорачиваются,
большой ответ инструмента режется без поломки JSON, html-страница шлюза не попадает в чат."""

import json

import httpx

from app import actions, ai, ai_agent
from tests.test_db_ai import FakeModel, _call, _secret, _seed, _start, _text  # noqa: F401 — _secret: автофикстура


def _approved(db, s, monkeypatch, done):
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: done.append(kw) or {"ok": 1})
    pause = {"campaign_row_ids": [s["search"]], "action": "pause", "reason": "дорого"}
    _start(s["user"], s["chat"], "Останови Поиск", monkeypatch, _call("campaigns_action", pause))
    action = ai_agent.get_chat(s["user"], s["chat"])["actions"][0]
    ai_agent.decide_action(s["user"], action["id"], approve=True)
    return action["id"]


def test_interrupted_action_is_not_repeated(db, monkeypatch):
    s = _seed(db)
    done = []
    action_id = _approved(db, s, monkeypatch, done)
    # Процесс упал посреди выполнения: начато час назад, статус так и остался «approved».
    with db.connection() as conn:
        conn.execute("UPDATE ai_actions SET started_at = NOW() - INTERVAL '1 hour' WHERE id = %s", (action_id,))

    fake = FakeModel(_text("Проверю кампанию."))
    monkeypatch.setattr(ai, "complete", fake)
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"
    assert not done  # в Директ второй раз не пошли
    action = ai_agent.get_chat(s["user"], s["chat"])["actions"][0]
    assert action["status"] == "error" and "прервалось" in action["error"]
    tool = fake.calls[0][-1]
    assert tool["role"] == "tool" and "проверь" in json.loads(tool["content"])["error"].lower()


def test_long_approved_but_never_started_closes_on_open(db, monkeypatch):
    s = _seed(db)
    done = []
    action_id = _approved(db, s, monkeypatch, done)
    with db.connection() as conn:
        conn.execute("UPDATE ai_actions SET decided_at = NOW() - INTERVAL '1 day' WHERE id = %s", (action_id,))
    # Открыли чат — карточка больше не «выполняется».
    assert ai_agent.get_chat(s["user"], s["chat"])["actions"][0]["status"] == "error"
    assert not done


def test_fresh_approved_runs_once(db, monkeypatch):
    s = _seed(db)
    done = []
    action_id = _approved(db, s, monkeypatch, done)
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Остановил."), _text("Ещё раз?")))
    ai_agent.run_chat(s["user"], s["chat"])
    assert len(done) == 1
    with db.connection() as conn:
        row = conn.execute("SELECT status, started_at FROM ai_actions WHERE id = %s", (action_id,)).fetchone()
    assert row["status"] == "done" and row["started_at"] is not None
    # Уже начатое не берётся вторым ходом.
    with db.connection() as conn:
        row = conn.execute("UPDATE ai_actions SET status = 'approved' WHERE id = %s RETURNING *", (action_id,)).fetchone()
    ai_agent._run_action(ai_agent.Context(s["user"], s["ws"]), row)
    assert len(done) == 1


def test_old_steps_of_long_turn_are_shortened(db):
    s = _seed(db)
    chat = s["chat"]
    big = json.dumps({"ok": True, "result": "x" * 5000})
    with db.connection() as conn:
        ai_agent._insert_message(conn, chat, "user", "Разберись со всеми кампаниями")
        for n in range(7):
            call = {"id": f"r{n}", "name": "get_campaign", "arguments": "{}"}
            ai_agent._insert_message(conn, chat, "assistant", "", tool_calls=[call])
            ai_agent._insert_message(conn, chat, "tool", big, tool_call_id=f"r{n}", tool_name="get_campaign")
    history = ai_agent._history(chat)
    sizes = [len(m["content"]) for m in history if m["role"] == "tool"]
    # 7 шагов: срезаны первые 3 (пачкой), полными — 4 последних.
    assert [size < 2000 for size in sizes] == [True, True, True, False, False, False, False]
    # Восьмой шаг границу не двигает — начало запроса то же, кэш провайдера работает.
    with db.connection() as conn:
        ai_agent._insert_message(conn, chat, "assistant", "", tool_calls=[{"id": "r7", "name": "get_campaign", "arguments": "{}"}])
        ai_agent._insert_message(conn, chat, "tool", big, tool_call_id="r7", tool_name="get_campaign")
    again = ai_agent._history(chat)
    assert again[: len(history)] == history


def test_big_tool_result_stays_valid_json():
    rows = [{"name": f"Кампания {i}", "phrases": [f"фраза {j}" for j in range(30)]} for i in range(200)]
    text = ai_agent._dump({"ok": True, "result": {"rows": rows, "total": 200}})
    data = json.loads(text)
    assert len(text) <= ai_agent.TOOL_RESULT_LIMIT
    assert data["ok"] is True and data["result"]["total"] == 200 and "_сокращено" in data
    shown = data["result"]["rows"]
    assert shown[-1].startswith("… ещё ") and len(shown) > 10
    # Справочник кампаний теперь влезает целиком.
    assert len(json.dumps(ai_agent._options_for_ai(), ensure_ascii=False)) < ai_agent.TOOL_RESULT_LIMIT // 2


def test_gateway_html_inside_json_is_hidden():
    html = "<!DOCTYPE html><html><body>524: A timeout occurred</body></html>"
    res = httpx.Response(524, json={"error": {"message": html}})
    assert "<" not in ai._error_text(res) and "шлюз" in ai._error_text(res)
    assert ai._error_text(httpx.Response(400, json={"error": {"message": "Неверная модель"}})) == "Неверная модель"
