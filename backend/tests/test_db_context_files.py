"""Файлы контекста пространства на настоящей БД: загрузка txt/zip через API, список, выключение, удаление,
лимиты и текст файлов в системном промпте чата (модель подменена)."""

import base64
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import ai, ai_agent, context_files
from app.config import settings
from app.main import app
from app.web_auth import make_session
from tests.dbutil import make_account, make_connection, make_user, make_workspace

# Заведомо ненастоящий ключ модели.
AI_KEY = "test-ai-key-1234"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _zip(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def _seed(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, name="Кабинет", login="acc-a")
    ws = make_workspace(db, user, 555, "Окна", accounts=[acc])
    other_ws = make_workspace(db, user, 777, "Блог")
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    return user, ws, other_ws, client, auth


def _url(ws, tail=""):
    return f"/api/web/workspaces/{ws}/context-files{tail}"


def test_upload_list_replace_toggle_delete(db):
    user, ws, other_ws, client, auth = _seed(db)
    empty = client.get(_url(ws), headers=auth)
    assert empty.status_code == 200
    assert empty.json()["items"] == [] and empty.json()["usage"]["sent_chars"] == 0
    assert empty.json()["limits"]["upload_bytes"] == context_files.MAX_UPLOAD_BYTES

    r = client.post(_url(ws), json={"filename": "бриф.txt", "data": _b64("Продаём окна ПВХ в Казани".encode("cp1251"))}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == ["бриф.txt"] and body["replaced"] == [] and body["skipped"] == []
    assert [(f["filename"], f["source"], f["chars"], f["state"]) for f in body["items"]] == [("бриф.txt", "", 25, "full")]

    archive = _zip(
        [
            ("chat/1.txt", "Клиент просит не упоминать скидки".encode()),
            ("chat/2.md", b"\xef\xbb\xbf# \xd0\xa2\xd0\xbe\xd0\xbd\n\xd0\x91\xd0\xb5\xd0\xb7 \xd0\xba\xd0\xb0\xd0\xbf\xd1\x81\xd0\xb0"),
            ("../escape.txt", "Наружу".encode()),
            ("chat/screen.png", b"\x89PNG"),
        ]
    )
    r = client.post(_url(ws), json={"filename": "gpt.zip", "data": _b64(archive)}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == ["chat/1.txt", "chat/2.md", "escape.txt"]
    assert body["skipped"] == [{"name": "chat/screen.png", "reason": "не .txt и не .md"}]
    assert [(f["filename"], f["source"]) for f in body["items"]] == [
        ("бриф.txt", ""),
        ("chat/1.txt", "gpt.zip"),
        ("chat/2.md", "gpt.zip"),
        ("escape.txt", "gpt.zip"),
    ]
    assert body["usage"]["enabled"] == 4 and body["usage"]["sent_chars"] == body["usage"]["chars"]

    # Тот же архив ещё раз — файлы заменяются, а не дублируются.
    archive2 = _zip([("chat/1.txt", "Скидки можно".encode())])
    body = client.post(_url(ws), json={"filename": "gpt.zip", "data": _b64(archive2)}, headers=auth).json()
    assert body["added"] == [] and body["replaced"] == ["chat/1.txt"]
    assert len(body["items"]) == 4
    with db.connection() as conn:
        assert conn.execute(
            "SELECT content FROM workspace_context_files WHERE filename = 'chat/1.txt'"
        ).fetchone()["content"] == "Скидки можно"

    first = body["items"][1]["id"]
    body = client.patch(_url(ws, f"/{first}"), json={"enabled": False}, headers=auth).json()
    assert body["items"][1]["state"] == "off" and body["usage"]["enabled"] == 3

    body = client.delete(_url(ws, f"/{first}"), headers=auth).json()
    assert [f["filename"] for f in body["items"]] == ["бриф.txt", "chat/2.md", "escape.txt"]
    assert client.delete(_url(ws, f"/{first}"), headers=auth).status_code == 404

    # Чужое пространство и чужой файл не видны.
    stranger = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    assert client.get(_url(ws), headers=stranger).status_code == 404
    assert client.post(_url(ws), json={"filename": "a.txt", "data": _b64(b"x")}, headers=stranger).status_code == 404
    brief_id = body["items"][0]["id"]
    assert client.delete(_url(ws, f"/{brief_id}"), headers=stranger).status_code == 404
    assert client.patch(_url(other_ws, f"/{brief_id}"), json={"enabled": False}, headers=auth).status_code == 404
    assert client.delete(_url(other_ws, f"/{brief_id}"), headers=auth).status_code == 404

    body = client.delete(_url(ws), headers=auth).json()
    assert body["items"] == []


def test_upload_errors_are_readable(db):
    _, ws, _, client, auth = _seed(db)
    cases = [
        ({"filename": "photo.jpg", "data": _b64(b"\xff\xd8")}, "Подходят только .txt, .md и .zip с ними"),
        ({"filename": "a.txt", "data": "%%%"}, "Файл повреждён при загрузке"),
        ({"filename": "a.zip", "data": _b64(b"PK\x03\x04broken")}, "Архив повреждён или это не zip"),
        ({"filename": "a.zip", "data": _b64(_zip([("a.pdf", b"x")]))}, "В архиве нет текстовых файлов .txt или .md — a.pdf: не .txt и не .md"),
    ]
    for payload, message in cases:
        r = client.post(_url(ws), json=payload, headers=auth)
        assert r.status_code == 400
        assert r.json()["detail"] == {"code": "context-file", "message": message}


def test_workspace_limits(db, monkeypatch):
    user, ws, _, client, auth = _seed(db)
    monkeypatch.setattr(context_files, "MAX_FILES", 2)
    body = client.post(
        _url(ws), json={"filename": "three.zip", "data": _b64(_zip([(f"{i}.txt", b"text") for i in range(3)]))}, headers=auth
    ).json()
    assert body["added"] == ["0.txt", "1.txt"]
    assert body["skipped"] == [{"name": "2.txt", "reason": "в пространстве уже 2 файлов"}]
    # Замена существующего файла при полном пространстве разрешена.
    body = client.post(_url(ws), json={"filename": "three.zip", "data": _b64(_zip([("0.txt", b"new")]))}, headers=auth).json()
    assert body["replaced"] == ["0.txt"]

    monkeypatch.setattr(context_files, "MAX_FILES", 50)
    monkeypatch.setattr(context_files, "MAX_TOTAL_CHARS", 10)
    r = client.post(_url(ws), json={"filename": "big.txt", "data": _b64(b"x" * 20)}, headers=auth)
    assert r.status_code == 400
    assert r.json()["detail"]["message"] == "Ничего не загружено: big.txt: всего текста больше 10 символов"


def test_prompt_limit_cuts_and_reports(db, monkeypatch):
    user, ws, _, client, auth = _seed(db)
    monkeypatch.setattr(context_files, "DEFAULT_PROMPT_CHARS", 1000)
    client.post(_url(ws), json={"filename": "a.txt", "data": _b64(b"a" * 700)}, headers=auth)
    client.post(_url(ws), json={"filename": "b.txt", "data": _b64(b"b" * 700)}, headers=auth)
    body = client.post(_url(ws), json={"filename": "c.txt", "data": _b64(b"c" * 700)}, headers=auth).json()
    assert [(f["filename"], f["state"], f["sent"]) for f in body["items"]] == [
        ("a.txt", "full", 700),
        ("b.txt", "out", 0),  # остаток 300 < MIN_PIECE_CHARS
        ("c.txt", "out", 0),
    ]
    assert body["usage"] == {
        "limit": 1000, "files": 3, "enabled": 3, "chars": 2100, "sent_chars": 700, "tokens": 233,
        "cut": [], "out": ["b.txt", "c.txt"],
    }
    # Выключили первый — в лимит встают следующие.
    a_id = body["items"][0]["id"]
    body = client.patch(_url(ws, f"/{a_id}"), json={"enabled": False}, headers=auth).json()
    assert [(f["state"], f["sent"]) for f in body["items"]] == [("off", 0), ("full", 700), ("out", 0)]


def test_workspace_prompt_limit_setting(db):
    user, ws, other_ws, client, auth = _seed(db)
    assert client.get(_url(ws), headers=auth).json()["usage"]["limit"] == 20_000
    for name in ("a.txt", "b.txt", "c.txt"):
        client.post(_url(ws), json={"filename": name, "data": _b64(name[0].encode() * 3000)}, headers=auth)
    assert [f["state"] for f in client.get(_url(ws), headers=auth).json()["items"]] == ["full", "full", "full"]

    r = client.put(_url(ws, "/limit"), json={"limit": 5000}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["usage"]["limit"] == 5000 and body["usage"]["sent_chars"] == 5000
    assert [(f["state"], f["sent"]) for f in body["items"]] == [("full", 3000), ("cut", 2000), ("out", 0)]
    block = context_files.prompt_block(ws)
    assert "=== a.txt ===\n" + "a" * 3000 in block
    assert "=== b.txt — начало, 2 000 из 3 000 символов ===\n" + "b" * 2000 + "\n" in block
    assert "c" * 10 not in block and block.endswith("Не поместились в лимит и тебе не видны: c.txt.")
    # У другого пространства свой лимит.
    assert client.get(_url(other_ws), headers=auth).json()["usage"]["limit"] == 20_000

    for bad in (4999, 100_001):
        r = client.put(_url(ws, "/limit"), json={"limit": bad}, headers=auth)
        assert r.status_code == 400
        assert r.json()["detail"]["message"] == "Лимит — от 5 000 до 100 000 символов"
    stranger = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    assert client.put(_url(ws, "/limit"), json={"limit": 9000}, headers=stranger).status_code == 404
    assert client.get(_url(ws), headers=auth).json()["usage"]["limit"] == 5000


def test_chat_sees_files_in_system_prompt(db, monkeypatch):
    user, ws, other_ws, client, auth = _seed(db)
    ai.save_settings(user, provider="openai", api_key=AI_KEY, model="gpt-test")
    client.post(
        _url(ws),
        json={"filename": "gpt.zip", "data": _b64(_zip([("итоги.txt", "Оффер: замер бесплатно, монтаж за 1 день".encode())]))},
        headers=auth,
    )
    off = client.post(_url(ws), json={"filename": "old.txt", "data": _b64("Старый оффер: скидка 50%".encode())}, headers=auth)
    client.patch(_url(ws, f"/{off.json()['items'][-1]['id']}"), json={"enabled": False}, headers=auth)
    client.post(_url(other_ws), json={"filename": "blog.txt", "data": _b64("Чужой блог".encode())}, headers=auth)

    calls = []

    def fake(cfg, messages, tools=None):
        calls.append(messages)
        return {"content": "Учёл файлы.", "tool_calls": []}

    monkeypatch.setattr(ai, "complete", fake)
    chat = ai_agent.create_chat(user, ws)
    ai_agent.post_message(user, chat["id"], "Что ты знаешь о проекте?")
    assert ai_agent.run_chat(user, chat["id"])["status"] == "idle"
    system = calls[0][0]["content"]
    assert "Файлы контекста от маркетолога" in system
    assert "=== итоги.txt (из gpt.zip) ===\nОффер: замер бесплатно, монтаж за 1 день" in system
    assert "Старый оффер" not in system and "Чужой блог" not in system

    # Без файлов блока в промпте нет.
    client.delete(_url(ws), headers=auth)
    calls.clear()
    ai_agent.post_message(user, chat["id"], "А теперь?")
    ai_agent.run_chat(user, chat["id"])
    assert "Файлы контекста" not in calls[0][0]["content"]
    assert json.dumps(calls[0][0], ensure_ascii=False).count("итоги.txt") == 0


def test_files_go_with_workspace(db):
    user, ws, _, client, auth = _seed(db)
    client.post(_url(ws), json={"filename": "a.txt", "data": _b64(b"text")}, headers=auth)
    with db.connection() as conn:
        conn.execute("DELETE FROM workspaces WHERE id = %s", (ws,))
        assert conn.execute("SELECT COUNT(*) AS n FROM workspace_context_files").fetchone()["n"] == 0
