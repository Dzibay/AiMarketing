"""Общий чат ИИ по всем проектам и очередь «Ждёт решения» — с фейковой моделью."""

import json

import pytest
from fastapi.testclient import TestClient

from app import actions, ai, ai_agent, workspaces
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.test_db_ai import _call, _seed, _text


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")


class ToolsModel:
    """Как FakeModel, но запоминает и список инструментов, который получила модель."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.tools: list[list[dict]] = []

    def __call__(self, cfg, messages, tools=None):
        self.calls.append(messages)
        self.tools.append(tools or [])
        return self.replies.pop(0)


def _blog(user):
    return next(w["id"] for w in workspaces.list_workspaces(user) if w["name"] == "Блог")


def test_general_chat_reads_any_project_and_every_write_waits(db, monkeypatch):
    s = _seed(db)
    user, blog = s["user"], _blog(s["user"])
    # Полный доступ в проектах не действует в общем чате: там всё на согласовании.
    workspaces.update_workspace(user, blog, ai_access={"full_access": True})
    done = []
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: done.append(kw) or {"ok": 1})

    chat = ai_agent.create_chat(user, None)
    assert chat["workspace_id"] is None
    assert [c["id"] for c in ai_agent.list_chats(user, None)] == [chat["id"]]
    assert chat["id"] not in [c["id"] for c in ai_agent.list_chats(user, s["ws"])]

    fake = ToolsModel(
        _call("get_workspace_summary", {}, "c1"),
        _call("list_campaigns", {"project_id": blog}, "c2"),
        _call("create_campaign", {"project_id": blog, "spec": {}}, "c3"),
        _call("campaigns_action", {"project_id": s["ws"], "campaign_row_ids": [s["foreign"]], "action": "pause"}, "c4"),
        _call("campaigns_action", {"campaign_row_ids": [s["foreign"]], "action": "pause"}, "c5"),
        _call("campaigns_action", {"project_id": blog, "campaign_row_ids": [s["foreign"]], "action": "pause", "reason": "дорого"}, "c6"),
    )
    monkeypatch.setattr(ai, "complete", fake)
    ai_agent.post_message(user, chat["id"], "Останови дорогое в блоге")
    assert ai_agent.run_chat(user, chat["id"])["status"] == "waiting"

    names = {t["function"]["name"] for t in fake.tools[0]}
    assert names == set(ai_agent.GENERAL_TOOL_NAMES) and "create_campaign" not in names
    summary_tool = next(t for t in fake.tools[0] if t["function"]["name"] == "campaigns_action")
    assert summary_tool["function"]["parameters"]["required"][0] == "project_id"
    system = fake.calls[0][0]["content"]
    assert "общий чат" in system and f"project_id {blog}: «Блог»" in system and "«Магазин»" in system

    def result(n: int) -> dict:
        return json.loads(fake.calls[n][-1]["content"])

    assert "Укажи project_id" in result(1)["error"]
    assert result(2)["project"] == {"project_id": blog, "name": "Блог"}
    assert "нет инструмента create_campaign" in result(3)["error"]
    assert "не входят" in result(4)["error"]
    assert "Укажи project_id" in result(5)["error"]

    got = ai_agent.get_chat(user, chat["id"])
    assert got["build"] is None and got["access"]["full_access"] is False
    assert got["projects"] == {str(blog): "Блог"}
    action = got["actions"][0]
    assert action["status"] == "pending" and action["args"]["project_id"] == blog and not done

    assert ai_agent.pending_count(user) == {"count": 1}
    queue = ai_agent.pending_actions(user)["items"]
    assert queue[0]["id"] == action["id"] and queue[0]["project"] == {"id": blog, "name": "Блог"}
    assert queue[0]["chat"]["general"] is True
    assert ai_agent.pending_actions(user, s["ws"])["items"] == []

    # «Запомнить» в общем чате ничего не включает: прав ИИ у него нет.
    before = workspaces.get_workspace(user, s["ws"])["ai_access"]
    decided = ai_agent.decide_action(user, action["id"], approve=True, remember=True)
    assert decided["pending"] == 0 and decided["job"]["kind"] == "ai_chat"
    assert workspaces.get_workspace(user, s["ws"])["ai_access"] == before

    monkeypatch.setattr(ai, "complete", ToolsModel(_text("Остановил.")))
    assert ai_agent.run_chat(user, chat["id"])["status"] == "idle"
    assert done == [
        {"campaign_ids": [s["foreign"]], "action": "pause", "daily_budget_rub": None, "budget_change_pct": None, "workspace_id": blog}
    ]
    assert ai_agent.get_chat(user, chat["id"])["actions"][0]["status"] == "done"
    assert ai_agent.pending_count(user) == {"count": 0}


def test_general_chat_questions_are_not_remembered(db, monkeypatch):
    s = _seed(db)
    user = s["user"]
    chat = ai_agent.create_chat(user, None)
    ask = _call(
        "ask_questions",
        {"understanding": "Сравнить проекты", "questions": [{"question": "Критерий успеха?", "key": "success_criteria", "options": ["CPA", "CR"]}]},
    )
    monkeypatch.setattr(ai, "complete", ToolsModel(ask))
    ai_agent.post_message(user, chat["id"], "Сравни проекты")
    assert ai_agent.run_chat(user, chat["id"])["status"] == "waiting"
    card = ai_agent.get_chat(user, chat["id"])["actions"][0]
    assert ai_agent.pending_actions(user)["items"][0]["project"] is None

    out = ai_agent.answer_questions(user, card["id"], answers=[{"n": 1, "selected": ["CR"]}])
    assert out["action"]["status"] == "done" and out["action"]["result"]["remembered"] == []
    assert workspaces.get_workspace(user, s["ws"])["ai_facts"] == []
    with pytest.raises(YandexError):
        ai_agent.edit_board(user, chat["id"], [{"op": "add", "kind": "keyword", "group": "g", "text": "x"}])


def test_pending_queue_skips_archived_projects_and_api(db, monkeypatch):
    s = _seed(db)
    user = s["user"]
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: {"ok": 1})
    monkeypatch.setattr(
        ai, "complete", ToolsModel(_call("campaigns_action", {"campaign_row_ids": [s["search"]], "action": "pause"}))
    )
    ai_agent.post_message(user, s["chat"], "Останови Поиск")
    ai_agent.run_chat(user, s["chat"])

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    assert client.get("/api/web/ai/pending/count", headers=auth).json() == {"count": 1}
    items = client.get("/api/web/ai/pending", headers=auth).json()["items"]
    assert items[0]["project"] == {"id": s["ws"], "name": "Магазин"} and items[0]["chat"]["general"] is False
    assert client.get(f"/api/web/ai/pending?workspace_id={_blog(user)}", headers=auth).json()["items"] == []

    created = client.post("/api/web/ai/chats", headers=auth, json={"title": "Все проекты"}).json()
    assert created["workspace_id"] is None and created["title"] == "Все проекты"
    assert [c["id"] for c in client.get("/api/web/ai/chats", headers=auth).json()["items"]] == [created["id"]]

    workspaces.update_workspace(user, s["ws"], archived=True)
    assert client.get("/api/web/ai/pending/count", headers=auth).json() == {"count": 0}
