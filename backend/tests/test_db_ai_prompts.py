"""Готовые задачи ИИ: встроенный набор и свои задачи пользователя."""

import pytest
from fastapi.testclient import TestClient

from app import ai_prompts
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_user


def test_builtin_set_is_consistent():
    cats = {c["key"] for c in ai_prompts.CATEGORIES}
    places = {"general", "project", "first", "campaigns", "campaigns_picked"}
    items = ai_prompts.builtin()
    keys = [p["key"] for p in items]
    assert len(keys) == len(set(keys))
    for p in items:
        assert p["category"] in cats and p["title"] and p["text"]
        assert p["places"] and set(p["places"]) <= places
        assert not p["home"] or p["icon"]
        assert p["roles"] and set(p["roles"]) <= set(ai_prompts.ROLES)
        # На главной задача стоит только у тех, кому она видна.
        assert set(p["home"]) <= set(p["roles"])
    # Каждое место, где показываются задачи, не пустое; на главных — по 4 быстрых вопроса у каждой роли.
    for place in places:
        assert any(place in p["places"] for p in items)
    for place in ("general", "project"):
        for role in ai_prompts.ROLES:
            assert sum(1 for p in items if role in p["home"] and place in p["places"]) == 4, (place, role)
    # Владельцу на главной — простыми словами, без терминов.
    jargon = ("CPA", "CTR", "ДРР", "KPI", "CR ")
    for p in items:
        if "owner" in p["home"]:
            assert not any(word in p["title"] for word in jargon), p["title"]


def test_own_prompts_crud(db):
    user = make_user(db)
    stranger = make_user(db, "stranger@example.com")

    p = ai_prompts.create(user, "", "  Найди   фразы\nбез конверсий за 30 дней  ")
    assert p["title"] == "Найди фразы без конверсий за 30 дней"
    assert p["text"] == "Найди   фразы\nбез конверсий за 30 дней"
    # Тот же текст — та же задача, без копии.
    assert ai_prompts.create(user, "Другое", p["text"])["id"] == p["id"]
    q = ai_prompts.create(user, "x" * 200, "Отчёт")
    assert len(q["title"]) == ai_prompts.TITLE_LEN and q["title"].endswith("…")
    assert [x["id"] for x in ai_prompts.own(user)] == [p["id"], q["id"]]

    with pytest.raises(YandexError) as err:
        ai_prompts.create(user, "t", "   ")
    assert err.value.code == "args"

    assert ai_prompts.update(user, q["id"], title="Отчёт за неделю", sort=-1)["title"] == "Отчёт за неделю"
    assert [x["id"] for x in ai_prompts.own(user)] == [q["id"], p["id"]]

    # Чужие задачи не видны, не меняются и не удаляются.
    assert ai_prompts.own(stranger) == []
    for call in (lambda: ai_prompts.update(stranger, p["id"], title="t"), lambda: ai_prompts.delete(stranger, p["id"])):
        with pytest.raises(YandexError) as err:
            call()
        assert err.value.code == "not-found"

    ai_prompts.delete(user, p["id"])
    assert [x["id"] for x in ai_prompts.own(user)] == [q["id"]]


def test_own_prompts_limit(db, monkeypatch):
    user = make_user(db)
    monkeypatch.setattr(ai_prompts, "OWN_LIMIT", 2)
    ai_prompts.create(user, "", "один")
    ai_prompts.create(user, "", "два")
    with pytest.raises(YandexError) as err:
        ai_prompts.create(user, "", "три")
    assert err.value.code == "args"
    # Повтор уже сохранённого текста лимит не трогает.
    assert ai_prompts.create(user, "", "два")["text"] == "два"


def test_prompts_api(db):
    user = make_user(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}

    data = client.get("/api/web/ai/prompts", headers=auth).json()
    assert [c["key"] for c in data["categories"]] == ["review", "save", "growth", "ads", "report"]
    assert data["own"] == [] and len(data["builtin"]) == len(ai_prompts.BUILTIN)

    made = client.post("/api/web/ai/prompts", json={"text": "Проверь ставки"}, headers=auth).json()["prompt"]
    assert made["title"] == "Проверь ставки"
    patched = client.patch(f"/api/web/ai/prompts/{made['id']}", json={"title": "Ставки"}, headers=auth).json()
    assert patched["prompt"]["title"] == "Ставки" and patched["prompt"]["text"] == "Проверь ставки"
    assert client.post("/api/web/ai/prompts", json={"text": ""}, headers=auth).status_code == 400
    assert client.delete(f"/api/web/ai/prompts/{made['id']}", headers=auth).json() == {"ok": True}
    assert client.delete(f"/api/web/ai/prompts/{made['id']}", headers=auth).status_code == 404
    assert client.get("/api/web/ai/prompts").status_code == 401
