"""Проверка сайта на лендинге: своя модель по общему ключу, лимиты, кэш по домену, открытая часть результата."""

import json

import pytest
from fastapi.testclient import TestClient

from app import ai, ai_agent, brief, site_check, workspaces
from app.config import settings
from app.main import app
from app.settings_store import invalidate_settings_cache
from app.yandex import YandexError
from app.web_auth import make_session
from tests.dbutil import admin_headers, make_connection, make_counter, make_user, make_workspace, setup_ai

PAGE = "<html><head><title>Окна ПВХ в Москве</title></head><body><h1>Окна под ключ</h1><p>Замер бесплатно</p></body></html>"
REPLY = {
    "niche": "пластиковые окна в Москве",
    "offer": "Окна ПВХ под ключ с бесплатным замером.",
    "headlines": ["Окна ПВХ под ключ в Москве", "Пластиковые окна с бесплатным замером и установкой за один день без грязи", "Окна от производителя", "Замер окон бесплатно"],
    "minus_words": [f"слово{i}" for i in range(14)] + ["бесплатно", "Бесплатно"],
    "groups": [{"name": "Окна ПВХ", "phrases": ["окна пвх", "пластиковые окна"]}, {"name": "", "phrases": ["x"]}],
}


@pytest.fixture(autouse=True)
def _clean(db, monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-site-check-secret")
    with db.connection() as conn:
        conn.execute("TRUNCATE site_checks CASCADE")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'site_check_%'")
        conn.execute("DELETE FROM ai_usage WHERE task IN ('site_check', 'test')")
    invalidate_settings_cache()
    yield
    invalidate_settings_cache()


class FakeModel:
    def __init__(self, reply):
        self.reply = reply
        self.cfgs: list[dict] = []

    def __call__(self, cfg, messages, tools=None):
        self.cfgs.append(cfg)
        return {"content": json.dumps(self.reply, ensure_ascii=False), "tool_calls": [], "usage": {}, "model": cfg["model_name"]}


def _fake(monkeypatch, reply=REPLY):
    fake = FakeModel(reply)
    monkeypatch.setattr(ai, "complete", fake)
    monkeypatch.setattr(brief, "fetch_site", lambda url: (url, PAGE))
    return fake


def test_normalize_url():
    assert site_check.normalize_url("WWW.Okna-Msk.ru/catalog?x=1") == ("https://www.okna-msk.ru/catalog", "okna-msk.ru")
    assert site_check.normalize_url("http://окна.рф") == ("http://xn--80atjc.xn--p1ai/", "окна.рф")
    for bad in ("", "просто текст", "ftp://a.ru", "https://user:pw@a.ru", "localhost", "a" * 301 + ".ru"):
        with pytest.raises(YandexError):
            site_check.normalize_url(bad)


def test_normalize_result_cuts_and_dedupes():
    out = site_check.normalize_result(REPLY)
    assert all(len(h) <= site_check.HEADLINE_MAX for h in out["headlines"])
    assert len(out["headlines"]) == 4
    assert out["minus_words"].count("бесплатно") == 1 and "Бесплатно" not in out["minus_words"]
    assert out["groups"] == [{"name": "Окна ПВХ", "phrases": ["окна пвх", "пластиковые окна"]}]
    assert site_check.normalize_result("мусор") == {"niche": "", "offer": "", "headlines": [], "minus_words": [], "groups": []}


def test_own_model_on_shared_key(db, monkeypatch):
    setup_ai(model="gpt-std", fast="gpt-fast")
    fake = _fake(monkeypatch)
    assert site_check.admin_overview()["effective_model"] == "gpt-fast"

    site_check.save_settings(enabled=True, model="  gpt-cheap ")
    overview = site_check.admin_overview()
    assert (overview["model"], overview["effective_model"], overview["class_model"]) == ("gpt-cheap", "gpt-cheap", "gpt-fast")

    started = site_check.start("okna-msk.ru", "1.2.3.4")
    site_check.run(started["id"])
    cfg = fake.cfgs[0]
    # Модель своя, адрес и ключ — общие, цены — класса задачи.
    assert cfg["model"] == "gpt-cheap" and cfg["task"] == "site_check" and cfg["model_class"] == "fast"
    assert cfg["base_url"] == ai.load_config(None, "site_check")["base_url"]
    assert cfg["headers"]["Authorization"] == "Bearer sk-test-1234abcd"

    result = site_check.public(started["id"])
    assert result["status"] == "done" and result["domain"] == "okna-msk.ru"
    r = result["result"]
    assert len(r["headlines"]) == 3 and len(r["minus_words"]) == 10
    assert r["more"] == {"minus_words": 5, "headlines": 1, "groups": 1}
    with db.connection() as conn:
        assert conn.execute("SELECT model FROM site_checks").fetchone()["model"] == "gpt-cheap"

    assert site_check.test_model()["model"] == "gpt-cheap"


def test_cache_and_limits(db, monkeypatch):
    setup_ai()
    _fake(monkeypatch)
    site_check.save_settings(enabled=True, ip_daily=2, daily=3, cache_days=7)

    first = site_check.start("https://okna-msk.ru", "1.1.1.1")
    # Пока идёт — тот же домен получает ту же проверку.
    assert site_check.start("www.okna-msk.ru/x", "9.9.9.9") == {"id": first["id"], "cached": True}
    site_check.run(first["id"])
    assert site_check.start("okna-msk.ru", "1.1.1.1") == {"id": first["id"], "cached": True}

    site_check.start("b.ru", "1.1.1.1")
    with pytest.raises(YandexError) as exc:
        site_check.start("c.ru", "1.1.1.1")
    assert exc.value.code == "too-soon"
    site_check.start("c.ru", "2.2.2.2")
    with pytest.raises(YandexError):
        site_check.start("d.ru", "3.3.3.3")
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM site_checks WHERE ip_hash LIKE '1.1%'").fetchone()["n"] == 0

    site_check.save_settings(enabled=False)
    with pytest.raises(YandexError) as exc:
        site_check.start("e.ru", "4.4.4.4")
    assert exc.value.code == "site-check-off"
    with pytest.raises(YandexError):
        site_check.save_settings(enabled=True, ip_daily=0)


def test_errors_are_readable(db, monkeypatch):
    setup_ai()
    _fake(monkeypatch)

    def broken(url):
        raise YandexError("site", "Сайт не открылся")

    monkeypatch.setattr(brief, "fetch_site", broken)
    started = site_check.start("okna-msk.ru", "1.1.1.1")
    site_check.run(started["id"])
    assert site_check.public(started["id"]) == {"id": started["id"], "domain": "okna-msk.ru", "status": "error", "error": "Сайт не открылся"}

    _fake(monkeypatch, reply={"niche": "x"})
    started = site_check.start("b.ru", "2.2.2.2")
    site_check.run(started["id"])
    assert site_check.public(started["id"])["error"] == site_check.FAILED_TEXT

    with pytest.raises(YandexError) as exc:
        site_check.public("nope")
    assert exc.value.code == "not-found"


def test_http_endpoints(db, monkeypatch):
    setup_ai()
    _fake(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/site/check", json={"url": "okna-msk.ru"}, headers={"X-Forwarded-For": "5.5.5.5"})
    assert res.status_code == 200, res.text
    check_id = res.json()["id"]
    # TestClient выполняет фоновые задачи до возврата ответа.
    body = client.get(f"/api/site/check/{check_id}").json()
    assert body["status"] == "done" and body["result"]["niche"] == "пластиковые окна в Москве"
    assert client.post("/api/site/check", json={"url": "не сайт"}).status_code == 400
    assert client.get("/api/site/check/nope").status_code == 404

    headers = admin_headers()
    assert client.put("/api/admin/site-check", json={"enabled": True, "model": "gpt-x", "ip_daily": 5}, headers=headers).json()["model"] == "gpt-x"
    got = client.get("/api/admin/site-check", headers=headers).json()
    assert got["effective_model"] == "gpt-x" and got["ip_daily"] == 5 and got["stats"]["today"] == 1
    assert client.get("/api/admin/site-check").status_code in (401, 403)


def _done_check(monkeypatch, url="okna-msk.ru", ip="7.7.7.7") -> str:
    _fake(monkeypatch)
    started = site_check.start(url, ip)
    site_check.run(started["id"])
    return started["id"]


def test_claim_to_general_chat_then_carry_to_project(db, monkeypatch):
    setup_ai()
    check_id = _done_check(monkeypatch)
    user = make_user(db)

    got = site_check.claim(user, check_id)
    assert got["status"] == "done" and got["workspace_id"] is None
    chat = ai_agent.get_chat(user, got["chat_id"])
    assert chat["chat"]["title"] == "Проверка сайта okna-msk.ru"
    texts = [m["content"] for m in chat["messages"]]
    assert texts[0] == "Проверь сайт okna-msk.ru"
    report = texts[1]
    # Полный набор, а не открытая часть: все заголовки, все минус-слова, группы.
    assert "Окна от производителя" in report and "Замер окон бесплатно" in report
    assert "слово13" in report and "**Окна ПВХ:** окна пвх, пластиковые окна" in report
    assert "Подключите Яндекс Директ" in report

    # Повторный перенос не плодит чаты.
    assert site_check.claim(user, check_id)["chat_id"] == got["chat_id"]
    assert len(ai_agent.list_chats(user, None)) == 1

    # Появился проект этого сайта — проверка переезжает в его чат, один раз.
    conn_id = make_connection(db, user)
    make_counter(db, user, conn_id, 501, site="https://www.okna-msk.ru/")
    make_counter(db, user, conn_id, 502, site="https://other.ru")
    created = workspaces.ensure_workspaces(user)
    assert len(created) == 2
    ws = next(w for w in workspaces.list_workspaces(user) if w["counter_id"] == 501)
    project_chats = ai_agent.list_chats(user, ws["id"])
    assert [c["title"] for c in project_chats] == ["Проверка сайта okna-msk.ru"]
    project_msgs = ai_agent.get_chat(user, project_chats[0]["id"])["messages"]
    assert "Собери черновик" in project_msgs[1]["content"]
    assert site_check.carry_to_projects(user) == []
    # Теперь кабинет открывает чат проекта.
    assert site_check.claim(user, check_id) == {"status": "done", "chat_id": project_chats[0]["id"], "workspace_id": ws["id"]}


def test_claim_straight_to_project_and_states(db, monkeypatch):
    setup_ai()
    user = make_user(db)
    ws = make_workspace(db, user, 601)
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET site = 'okna-msk.ru' WHERE id = %s", (ws,))
    check_id = _done_check(monkeypatch, "https://okna-msk.ru/catalog")
    got = site_check.claim(user, check_id)
    assert got["workspace_id"] == ws
    with db.connection() as conn:
        row = conn.execute("SELECT chat_id, project_chat_id FROM site_check_claims").fetchone()
    assert row["chat_id"] == row["project_chat_id"] == got["chat_id"]

    # Та же проверка (кэш по домену) у другого пользователя — свой чат.
    other = make_user(db, "o@example.com")
    assert site_check.claim(other, check_id)["chat_id"] != got["chat_id"]

    running = site_check.start("b.ru", "8.8.8.8")["id"]
    assert site_check.claim(user, running)["status"] == "running"
    site_check.run(running)  # fetch подменён на страницу окон, ИИ — тот же ответ: проверка готова
    assert site_check.claim(user, running)["status"] == "done"
    with pytest.raises(YandexError) as exc:
        site_check.claim(user, "nope")
    assert exc.value.code == "not-found"


def test_claim_endpoint(db, monkeypatch):
    setup_ai()
    check_id = _done_check(monkeypatch)
    user = make_user(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    res = client.post("/api/web/ai/site-check/claim", json={"id": check_id}, headers=auth)
    assert res.status_code == 200, res.text
    assert client.get(f"/api/web/ai/chats/{res.json()['chat_id']}", headers=auth).status_code == 200
    assert client.post("/api/web/ai/site-check/claim", json={"id": check_id}).status_code == 401
    assert client.post("/api/web/ai/site-check/claim", json={"id": "nope"}, headers=auth).status_code == 404
