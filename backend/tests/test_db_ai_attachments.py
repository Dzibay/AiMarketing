"""Картинки в чате с ИИ: загрузка из поля ввода, отправка с сообщением, модель, которая их видит,
картинки для объявлений и их загрузка в библиотеку кабинета только при выполнении."""

import pytest
from fastapi.testclient import TestClient

from app import ad_extras, ai, ai_agent, ai_attachments
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_user, setup_ai
from tests.test_ai_attachments_unit import data_url, png
from tests.test_db_ai import _seed, _secret, _text  # noqa: F401 — _secret: автофикстура ключа шифрования


def _last_user(messages):
    return [m for m in messages if m["role"] == "user"][-1]


def test_pictures_go_to_a_model_that_sees_them(db, monkeypatch):
    s = _seed(db)
    setup_ai(api_key=None, model="gpt-test", fast="gpt-fast", vision=("standard",))
    shown = ai_attachments.upload(s["user"], data_url(png(800, 600)), name="  Скриншот   кабинета ")
    assert shown["purpose"] == "ai" and shown["name"] == "Скриншот кабинета" and shown["direct_problem"] == ""
    banner = ai_attachments.upload(s["user"], data_url(png(1080, 607)), purpose="ads", name="Баннер")
    # Не по требованиям Директа — понятная ошибка сразу, «Показать ИИ» можно.
    with pytest.raises(YandexError) as err:
        ai_attachments.upload(s["user"], data_url(png(1000, 300)), purpose="ads")
    assert err.value.code == "attachment-direct" and "обрежьте" in err.value.message
    narrow = ai_attachments.upload(s["user"], data_url(png(1000, 300)))
    assert "обрежьте" in narrow["direct_problem"]
    with pytest.raises(YandexError):
        ai_attachments.set_purpose(s["user"], narrow["id"], "ads")
    assert ai_attachments.set_purpose(s["user"], banner["id"], "ai")["purpose"] == "ai"
    assert ai_attachments.set_purpose(s["user"], banner["id"], "ads")["purpose"] == "ads"
    # Чужую картинку не переключить и не отправить.
    stranger = make_user(db, "x@example.com")
    with pytest.raises(YandexError):
        ai_attachments.set_purpose(stranger, banner["id"], "ai")

    with pytest.raises(YandexError) as err:
        ai_agent.post_message(s["user"], s["chat"], "", attachments=[1, 2, 3, 4, 5])
    assert err.value.code == "too-many"
    # Сообщение может быть одной картинкой; выбранная «Быстрая» картинок не видит — ход ведёт «Стандартная».
    ai_agent.set_chat_model(s["user"], s["chat"], "fast")
    ai_agent.post_message(s["user"], s["chat"], "", attachments=[shown["id"], banner["id"]])

    calls = []

    def complete(cfg, messages, tools=None):
        calls.append((cfg["model"], messages))
        return _text("На скриншоте падает CTR.")

    monkeypatch.setattr(ai, "complete", complete)
    ai_agent.run_chat(s["user"], s["chat"])
    model, messages = calls[-1]
    assert model == "gpt-test"
    with pytest.raises(YandexError) as err:
        ai_agent.post_message(s["user"], s["chat"], "ещё", attachments=[shown["id"]])  # уже отправлена
    assert err.value.code == "not-found"
    content = _last_user(messages)["content"]
    text, *pictures = content
    assert [p["type"] for p in pictures] == ["image_url", "image_url"]
    assert pictures[0]["image_url"]["url"].startswith("data:image/png;base64,iVBOR")
    assert "«Скриншот кабинета» 800×600" in text["text"] and f"hash tolk-{banner['id']}" in text["text"]

    chat = ai_agent.get_chat(s["user"], s["chat"])
    user_msg = _last_user(chat["messages"])
    assert chat["chat"]["title"] == "Картинка"
    assert [a["id"] for a in user_msg["attachments"]] == [shown["id"], banner["id"]]
    assert user_msg["attachments"][1]["url"] == ai_attachments.url(banner["id"])
    # Для объявлений — только банер и только в этом проекте.
    assert [i["hash"] for i in ai_attachments.project_images(s["user"], s["ws"])] == [f"tolk-{banner['id']}"]
    assert ai_attachments.project_images(s["user"], None) == []
    assert set(ai_attachments.for_ads(s["user"], [shown["id"], banner["id"]], s["ws"])) == {banner["id"]}

    # Старые картинки в истории — подписью: модели уходят только последние сообщения с картинками.
    for n in range(ai_agent.HISTORY_IMAGE_MESSAGES):
        extra = ai_attachments.upload(s["user"], data_url(png(500, 500)), name=f"Ещё {n}")
        ai_agent.post_message(s["user"], s["chat"], f"и это {n}", attachments=[extra["id"]])
        ai_agent.run_chat(s["user"], s["chat"])
    messages = calls[-1][1]
    first = next(m for m in messages if m["role"] == "user" and "Скриншот" in str(m["content"]))
    assert isinstance(first["content"], str) and "сейчас ты их не видишь" in first["content"]
    assert sum(isinstance(m["content"], list) for m in messages if m["role"] == "user") == ai_agent.HISTORY_IMAGE_MESSAGES

    # Модели картинок не видят: «Показать ИИ» — ошибка, «В объявления» — можно, модель получит подпись.
    setup_ai(api_key=None, model="gpt-test", fast="gpt-fast")
    assert ai.chat_classes()["vision"] is False
    blind = ai_attachments.upload(s["user"], data_url(png(800, 600)))
    with pytest.raises(YandexError) as err:
        ai_agent.post_message(s["user"], s["chat"], "смотри", attachments=[blind["id"]])
    assert err.value.code == "ai-vision"
    ads = ai_attachments.upload(s["user"], data_url(png(800, 800)), purpose="ads")
    ai_agent.post_message(s["user"], s["chat"], "в объявления", attachments=[ads["id"]])
    ai_agent.run_chat(s["user"], s["chat"])
    model, messages = calls[-1]
    assert model == "gpt-fast" and all(isinstance(m["content"], (str, type(None))) for m in messages)
    assert f"tolk-{ads['id']}" in _last_user(messages)["content"]


def test_own_pictures_go_to_direct_only_on_execution(db, monkeypatch):
    s = _seed(db)
    setup_ai(vision=("standard",))
    banner = ai_attachments.upload(s["user"], data_url(png(1080, 607)), purpose="ads", name="Баннер")
    note = ai_attachments.upload(s["user"], data_url(png(800, 600)), purpose="ai")
    ai_agent.post_message(s["user"], s["chat"], "Поставь в объявления", attachments=[banner["id"], note["id"]])
    uploads = []

    def upload_image(token, login, *, name, data_b64):
        uploads.append((login, name))
        if name == "сломана":
            raise YandexError("direct", "Неверный формат")
        return {"hash": f"real-{len(uploads)}", "preview_url": ""}

    monkeypatch.setattr(ad_extras, "upload_image", upload_image)
    pseudo = f"tolk-{banner['id']}"
    warnings = []
    got = ai_attachments.resolve_hashes(s["user"], [pseudo, "h-direct", f"tolk-{note['id']}"], "t", "acc-a", s["acc"], warnings)
    # «Показать ИИ» в объявления не уходит; обычные хэши Директа не трогаем.
    assert got == {pseudo: "real-1"} and uploads == [("acc-a", "Баннер")]
    assert warnings == [f"Картинка tolk-{note['id']} не найдена — объявление без неё"]
    # Второй раз в тот же кабинет — без загрузки; в другой — загрузка.
    assert ai_attachments.resolve_hashes(s["user"], [pseudo], "t", "acc-a", s["acc"], []) == {pseudo: "real-1"}
    assert ai_attachments.resolve_hashes(s["user"], [pseudo], "t", "acc-x", 999, []) == {pseudo: "real-2"}
    assert len(uploads) == 2
    # Директ не принял — предупреждение, хэша нет.
    with db.connection() as conn:
        conn.execute("UPDATE ai_attachments SET name = 'сломана', direct_hashes = '{}' WHERE id = %s", (banner["id"],))
    warnings = []
    assert ai_attachments.resolve_hashes(s["user"], [pseudo], "t", "acc-a", s["acc"], warnings) == {}
    assert "Неверный формат" in warnings[0]


def test_attachments_api(db):
    s = _seed(db)
    setup_ai(vision=("standard",))
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    assert client.get("/api/web/ai/models", headers=auth).json()["vision"] is True

    res = client.post("/api/web/ai/attachments", json={"data": data_url(png(900, 900)), "name": "Фото"}, headers=auth)
    assert res.status_code == 200
    item = res.json()
    assert item["purpose"] == "ai" and item["width"] == 900 and item["url"] == ai_attachments.url(item["id"])
    bad = client.post("/api/web/ai/attachments", json={"data": "data:text/plain;base64,aGk="}, headers=auth)
    assert bad.status_code == 400 and "JPEG или PNG" in bad.text
    assert client.post("/api/web/ai/attachments", json={"data": data_url(png(9, 9))}).status_code == 401
    res = client.patch(f"/api/web/ai/attachments/{item['id']}", json={"purpose": "ads"}, headers=auth)
    assert res.status_code == 200 and res.json()["purpose"] == "ads"

    # Картинка открывается обычным <img> по подписанной ссылке; без подписи — нет.
    image = client.get(item["url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png" and image.content.startswith(b"\x89PNG")
    assert client.get(f"/api/web/ai/attachments/{item['id']}/{'0' * 32}").status_code == 404

    sent = client.post(
        f"/api/web/ai/chats/{s['chat']}/messages", json={"text": "", "attachments": [item["id"]]}, headers=auth
    )
    assert sent.status_code == 200, sent.text
    chat = client.get(f"/api/web/ai/chats/{s['chat']}", headers=auth).json()
    assert _last_user(chat["messages"])["attachments"][0]["id"] == item["id"]
