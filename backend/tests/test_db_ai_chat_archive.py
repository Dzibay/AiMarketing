"""Чаты с ИИ: закрепить и архив. Закреплённые — первыми, архив не виден в списке и поиске, чат с ждущими
предложениями в архив без ответа «что с ними делать» не уходит, новое сообщение возвращает чат из архива."""

import json

import pytest
from fastapi.testclient import TestClient

from app import actions, ai, ai_agent
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.test_db_ai import FakeModel, _call, _seed, _secret, _start, _text  # noqa: F401 — _secret: автофикстура


def _ids(items):
    return [c["id"] for c in items]


def test_pin_and_archive_order_and_search(db, monkeypatch):
    s = _seed(db)
    user, ws = s["user"], s["ws"]
    old, mid, new = s["chat"], ai_agent.create_chat(user, ws, "Про бюджет")["id"], ai_agent.create_chat(user, ws)["id"]
    with db.connection() as conn:
        for n, chat in enumerate((old, mid, new)):
            conn.execute("UPDATE ai_chats SET updated_at = NOW() - make_interval(days => %s) WHERE id = %s", (3 - n, chat))
    assert _ids(ai_agent.list_chats(user, ws)) == [new, mid, old]

    # Закреплённые — первыми, позже закреплённый выше; закрепление не меняет «давность» чата.
    assert ai_agent.pin_chat(user, old, True)["pinned"] is True
    ai_agent.pin_chat(user, mid, True)
    assert _ids(ai_agent.list_chats(user, ws)) == [mid, old, new]
    ai_agent.pin_chat(user, mid, True)  # повторно — порядок тот же
    assert _ids(ai_agent.list_chats(user, ws)) == [mid, old, new]
    assert ai_agent.pin_chat(user, mid, False)["pinned"] is False
    assert _ids(ai_agent.list_chats(user, ws)) == [old, new, mid]

    # Архив: нет в списке и в поиске; закрепление снимается; закрепить из архива нельзя.
    archived = ai_agent.archive_chat(user, old, True)
    assert archived["archived"] is True and archived["pinned"] is False
    assert _ids(ai_agent.list_chats(user, ws)) == [new, mid]
    assert _ids(ai_agent.list_chats(user, ws, archived=True)) == [old]
    assert ai_agent.archived_count(user, ws) == 1 and ai_agent.archived_count(user, None) == 0
    ai_agent.rename_chat(user, old, "Старый бюджет")
    assert _ids(ai_agent.search_chats(user, ws, "бюджет")) == [mid]
    assert _ids(ai_agent.search_chats(user, ws, "бюджет", archived=True)) == [old]
    with pytest.raises(YandexError) as err:
        ai_agent.pin_chat(user, old, True)
    assert err.value.code == "archived"
    assert ai_agent.archive_chat(user, old, False)["archived"] is False
    assert ai_agent.archived_count(user, ws) == 0

    # Новое сообщение возвращает чат из архива.
    ai_agent.archive_chat(user, mid, True)
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Готово.")))
    ai_agent.post_message(user, mid, "Вернись")
    assert ai_agent.get_chat(user, mid)["chat"]["archived"] is False

    # Чужой чат не трогаем.
    from tests.dbutil import make_user

    stranger = make_user(db, "x@example.com")
    for fn in (lambda: ai_agent.pin_chat(stranger, new, True), lambda: ai_agent.archive_chat(stranger, new, True)):
        with pytest.raises(YandexError) as err:
            fn()
        assert err.value.code == "not-found"


def test_archive_asks_about_pending_proposals(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: {"ok": 1})
    pause = {"campaign_row_ids": [s["search"]], "action": "pause", "reason": "дорого"}
    _fake, out = _start(user, chat, "Останови Поиск", monkeypatch, _call("campaigns_action", pause))
    assert out["status"] == "waiting"

    with pytest.raises(YandexError) as err:
        ai_agent.archive_chat(user, chat, True)
    assert err.value.code == "chat-pending" and "(1)" in err.value.message
    with pytest.raises(YandexError):
        ai_agent.archive_chat(user, chat, True, "maybe")

    # «Оставить ждать»: предложение в «Ждёт решения», чат в архиве.
    kept = ai_agent.archive_chat(user, chat, True, "keep")
    assert kept["archived"] and kept["pending_actions"] == 1
    assert ai_agent.pending_count(user)["count"] == 1
    ai_agent.archive_chat(user, chat, False)

    # «Отклонить»: карточка закрыта, модель получает отказ, нового ответа ИИ нет.
    calls = []
    monkeypatch.setattr(ai, "complete", lambda *a, **kw: calls.append(a) or _text("?"))
    rejected = ai_agent.archive_chat(user, chat, True, "reject")
    assert rejected["archived"] and rejected["pending_actions"] == 0 and rejected["status"] != "running"
    assert ai_agent.pending_count(user)["count"] == 0
    full = ai_agent.get_chat(user, chat)
    assert full["actions"][0]["status"] == "rejected"
    tool = [m for m in full["messages"] if m["role"] == "tool"][-1]
    assert "архив" in json.loads(tool["content"])["error"] and not calls


def test_chat_patch_api(db):
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    url = f"/api/web/ai/chats/{s['chat']}"
    assert client.patch(url, json={"title": "Окна"}, headers=auth).json()["title"] == "Окна"
    res = client.patch(url, json={"pinned": True}, headers=auth).json()
    assert res["pinned"] is True and res["title"] == "Окна"
    res = client.patch(url, json={"archived": True}, headers=auth).json()
    assert res["archived"] is True and res["pinned"] is False

    listed = client.get(f"/api/web/workspaces/{s['ws']}/chats", headers=auth).json()
    assert listed == {"items": [], "archived_count": 1}
    archive = client.get(f"/api/web/workspaces/{s['ws']}/chats?archived=true", headers=auth).json()
    assert _ids(archive["items"]) == [s["chat"]]
    found = client.get(f"/api/web/workspaces/{s['ws']}/chats?q=Окна&archived=true", headers=auth).json()
    assert _ids(found["items"]) == [s["chat"]]
    assert client.get("/api/web/ai/chats", headers=auth).json() == {"items": [], "archived_count": 0}

    # Вернуть и сразу закрепить — одним запросом.
    res = client.patch(url, json={"archived": False, "pinned": True}, headers=auth).json()
    assert res["archived"] is False and res["pinned"] is True
    assert client.patch(url, json={}, headers=auth).json()["id"] == s["chat"]
