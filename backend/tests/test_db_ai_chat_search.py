"""Поиск по чатам ИИ: название, текст реплик, отрывок вокруг слова, чужие и служебные сообщения не ищутся."""

from fastapi.testclient import TestClient

from app import ai_agent
from app.db import pool
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_user


def _say(chat_id: int, role: str, content: str) -> int:
    with pool.connection() as conn:
        return ai_agent._insert_message(conn, chat_id, role, content)["id"]


def test_search_titles_messages_and_snippet(db):
    user = make_user(db)
    other = make_user(db, "other@example.com")
    budget = ai_agent.create_chat(user, None, "Бюджет на ноябрь")
    talk = ai_agent.create_chat(user, None, "Разбор кампаний")
    _say(talk["id"], "user", "Где **расход** растёт быстрее всего?")
    first_hit = _say(talk["id"], "assistant", "Быстрее всего растёт расход в РСЯ.")
    last_hit = _say(talk["id"], "assistant", "Итог: " + "слово " * 40 + "Расход вырос на 30 %, " + "хвост " * 40)
    tool = ai_agent.create_chat(user, None, "Служебное")
    _say(tool["id"], "tool", '{"расход": 100}')
    mine_elsewhere = ai_agent.create_chat(other, None, "Расход у соседа")

    found = ai_agent.search_chats(user, None, "  РАСХОД ")
    assert [c["id"] for c in found] == [talk["id"]]
    match = found[0]["match"]
    # Последнее найденное сообщение, отрывок — вокруг слова, обрезанный многоточием с обеих сторон.
    assert match["message_id"] == last_hit != first_hit and match["role"] == "assistant"
    assert "Расход вырос на 30 %" in match["snippet"]
    assert match["snippet"].startswith("…") and match["snippet"].endswith("…")
    assert len(match["snippet"]) <= ai_agent.SNIPPET_LEN + 2

    by_title = ai_agent.search_chats(user, None, "ноябр")
    assert [c["id"] for c in by_title] == [budget["id"]]
    assert by_title[0]["match"] == {"message_id": None, "role": "", "snippet": ""}
    assert by_title[0]["title"] == "Бюджет на ноябрь" and "pending_actions" in by_title[0]

    # Разметка из отрывка убрана.
    early = ai_agent.search_chats(user, None, "быстрее всего")
    assert early[0]["match"]["snippet"] == "Быстрее всего растёт расход в РСЯ."

    assert ai_agent.search_chats(other, None, "расход")[0]["id"] == mine_elsewhere["id"]
    assert ai_agent.search_chats(user, None, "р") == []
    assert ai_agent.search_chats(user, None, "") == []


def test_search_escapes_wildcards_and_keeps_sections_apart(db):
    from tests.test_db_ai import _seed

    s = _seed(db)
    user = s["user"]
    general = ai_agent.create_chat(user, None, "Общий")
    _say(general["id"], "user", "Скидка 50% на всё")
    project = ai_agent.create_chat(user, s["ws"], "Проектный")
    _say(project["id"], "user", "Скидка 50% в проекте")
    plain = ai_agent.create_chat(user, None, "Простой")
    _say(plain["id"], "user", "Скидка 500 рублей и a_b")

    assert [c["id"] for c in ai_agent.search_chats(user, None, "50%")] == [general["id"]]
    assert [c["id"] for c in ai_agent.search_chats(user, None, "a_b")] == [plain["id"]]
    assert ai_agent.search_chats(user, None, "%%") == []
    assert [c["id"] for c in ai_agent.search_chats(user, s["ws"], "скидка")] == [project["id"]]

    stranger = make_user(db, "stranger@example.com")
    try:
        ai_agent.search_chats(stranger, s["ws"], "скидка")
    except YandexError:
        pass
    else:
        raise AssertionError("чужой проект не должен искаться")


def test_search_api(db):
    from tests.test_db_ai import _seed

    s = _seed(db)
    user = s["user"]
    chat = ai_agent.create_chat(user, s["ws"], "Ставки")
    hit = _say(chat["id"], "user", "Подними ставки на брендовые запросы")
    ai_agent.create_chat(user, None, "Ставки в общем")

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    items = client.get(f"/api/web/workspaces/{s['ws']}/chats?q=брендов", headers=auth).json()["items"]
    assert [(c["id"], c["match"]["message_id"]) for c in items] == [(chat["id"], hit)]
    assert "брендовые" in items[0]["match"]["snippet"]
    general = client.get("/api/web/ai/chats?q=ставки", headers=auth).json()["items"]
    assert [c["title"] for c in general] == ["Ставки в общем"]
    # Пустой запрос — обычный список без match.
    plain = client.get("/api/web/ai/chats?q=%20", headers=auth).json()["items"]
    assert plain and "match" not in plain[0]
