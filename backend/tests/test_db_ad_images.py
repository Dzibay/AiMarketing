"""Картинки объявлений: подключение генерации (BYOK), учёт расхода, вопрос об источнике в чате и картинки
кампаний пространства — на настоящей БД; провайдер и Директ подменены."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ad_extras, ads, ai, ai_agent, image_gen
from app import yandex as yandex_module
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

JPEG = b"\xff\xd8\xff" + b"0" * 64
# Заведомо ненастоящий ключ генератора; заголовки собираем из него, а не пишем строкой.
IMAGE_KEY = "test-image-1234"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")


def _seed(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, name="Кабинет", login="acc-a")
    other = make_account(db, user, conn_id, name="Чужой", login="acc-x")
    summer = make_campaign(db, user, acc, 11, "Окна лето")
    winter = make_campaign(db, user, acc, 12, "Окна зима")
    foreign = make_campaign(db, user, other, 21, "Блог")
    ws = make_workspace(db, user, 555, "Окна", accounts=[acc], campaigns=[summer, winter])
    make_workspace(db, user, 777, "Блог", accounts=[other], campaigns=[foreign])
    ai.save_settings(user, provider="openai", api_key="sk-test-1234abcd", model="gpt-test")
    return SimpleNamespace(user=user, acc=acc, other=other, ws=ws, summer=summer, winter=winter, foreign=foreign)


def _ad(db, user, account_id, campaign_row_id, ad_id, hashes):
    with db.connection() as conn:
        group = conn.execute(
            """
            INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name)
            VALUES (%s, %s, %s, %s, 'Группа') RETURNING id
            """,
            (user, account_id, campaign_row_id, ad_id * 10),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO direct_ads (user_id, account_id, adgroup_row_id, ad_id, type, image_hashes)
            VALUES (%s, %s, %s, %s, 'TEXT_AD', %s::jsonb)
            """,
            (user, account_id, group, ad_id, json.dumps(hashes)),
        )


def test_settings_keep_key_reset_check_and_yandexart(db):
    user = make_user(db)
    assert image_gen.get_settings(user)["configured"] is False and not image_gen.is_configured(user)
    saved = image_gen.save_settings(user, provider="openai", api_key=IMAGE_KEY, price_rub=4.5)
    assert saved["configured"] and saved["key_hint"] == "…1234" and saved["price_rub"] == 4.5
    assert "api_key" not in saved and "api_key_enc" not in saved
    image_gen._remember_check(user, "")
    # Та же модель и ключ — итог проверки остаётся; новая модель его сбрасывает; цена не передана — как была.
    assert image_gen.save_settings(user, provider="openai", api_key=None)["checked_at"]
    kept = image_gen.save_settings(user, provider="openai", model="dall-e-3")
    assert kept["checked_at"] is None and kept["has_key"] and kept["price_rub"] == 4.5
    cfg = image_gen.load_config(user, 7)
    assert (cfg["base_url"], cfg["model"], cfg["headers"]["Authorization"]) == (image_gen.OPENAI_BASE, "dall-e-3", f"Bearer {IMAGE_KEY}")
    assert (cfg["task"], cfg["workspace_id"], cfg["price"], cfg["check_host"]) == ("images", 7, 4.5, False)

    with pytest.raises(YandexError) as err:
        image_gen.save_settings(user, provider="yandexart")
    assert err.value.code == "image-folder"
    with pytest.raises(YandexError):
        image_gen.save_settings(user, provider="openai", base_url="http://10.0.0.1/v1")
    with pytest.raises(YandexError):
        image_gen.save_settings(user, provider="midjourney")
    art = image_gen.save_settings(user, provider="yandexart", folder_id="b1gfolder", model="")
    assert art["configured"] and art["base_url"] == ""
    cfg = image_gen.load_config(user)
    assert cfg["model"] == "art://b1gfolder/aliceai-image-art-3.0" and cfg["model_name"] == "aliceai-image-art-3.0"
    assert cfg["headers"]["Authorization"] == f"Api-Key {IMAGE_KEY}" and cfg["headers"]["OpenAI-Project"] == "b1gfolder"

    assert image_gen.save_settings(user, provider="yandexart", folder_id="b1gfolder", api_key="")["configured"] is False
    with pytest.raises(YandexError) as err:
        image_gen.load_config(user)
    assert err.value.code == "image-not-configured"


def test_generated_images_count_in_ai_spend(db, monkeypatch):
    s = _seed(db)
    ai.save_settings(s.user, provider="openai", api_key="sk-test-1234abcd", model="gpt-test", price_in=100, price_out=400,
                     monthly_limit_rub=100)
    image_gen.save_settings(s.user, provider="openai", api_key=IMAGE_KEY, price_rub=40)
    sent = []

    def post(url, **kw):
        sent.append((url, kw["json"]))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(JPEG).decode()}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(image_gen.httpx, "post", post)
    # Лимит 100 ₽ при 40 ₽ за картинку — нарисуем две из четырёх.
    out = image_gen.generate(s.user, "окно в квартире", count=4, workspace_id=s.ws)
    assert len(out["images"]) == 2 and len(sent) == 2
    assert sent[0][0] == f"{image_gen.OPENAI_BASE}/images/generations" and sent[0][1]["output_format"] == "jpeg"
    with db.connection() as conn:
        rows = conn.execute("SELECT task, workspace_id, units, cost_rub FROM ai_usage WHERE user_id = %s", (s.user,)).fetchall()
    assert [(r["task"], r["workspace_id"], r["units"], float(r["cost_rub"])) for r in rows] == [("images", s.ws, 1, 40.0)] * 2
    assert ai.spent_this_month(s.user) == 80.0
    out = image_gen.generate(s.user, "окно", count=4)
    assert len(out["images"]) == 1  # осталось 20 ₽ — хотя бы одна
    with pytest.raises(YandexError) as err:
        image_gen.generate(s.user, "окно")
    assert err.value.code == "ai-budget"

    # Проверка подключения — одна картинка; ошибка запоминается.
    monkeypatch.setattr(image_gen.httpx, "post", lambda url, **kw: httpx.Response(
        401, json={"error": {"message": "bad key"}}, request=httpx.Request("POST", url)))
    ai.save_settings(s.user, provider="openai", model="gpt-test", monthly_limit_rub=None)
    with pytest.raises(YandexError):
        image_gen.check_connection(s.user)
    assert image_gen.get_settings(s.user)["check_error"]


def test_image_source_answer_needs_connected_generation(db, monkeypatch):
    s = _seed(db)
    fake = lambda cfg, messages, tools=None: {  # noqa: E731
        "content": "", "tool_calls": [{"id": "c1", "name": "ask_questions", "arguments": json.dumps({"questions": [
            {"question": "Как поступим с картинками?", "key": "image_source"}]})}]}
    monkeypatch.setattr(ai, "complete", fake)
    chat = ai_agent.create_chat(s.user, s.ws)["id"]
    ai_agent.post_message(s.user, chat, "Собери новую кампанию на окна")
    ai_agent.run_chat(s.user, chat)
    data = ai_agent.get_chat(s.user, chat)
    assert data["image_gen"] == {"configured": False}
    [card] = [a for a in data["actions"] if a["tool"] == "ask_questions"]
    [question] = card["args"]["questions"]
    assert question["needs"] == {"Сгенерировать по описанию": "image_gen"} and question["multiple"]

    pick = [{"n": 1, "selected": ["Сгенерировать по описанию"]}]
    with pytest.raises(YandexError) as err:
        ai_agent.answer_questions(s.user, card["id"], answers=pick)
    assert "недоступно" in err.value.message
    ctx = SimpleNamespace(chat_id=chat)
    assert ai_agent._wants_generation(ctx) is False

    image_gen.save_settings(s.user, provider="openai", api_key=IMAGE_KEY)
    assert ai_agent.get_chat(s.user, chat)["image_gen"] == {"configured": True}
    monkeypatch.setattr(ai, "complete", lambda cfg, messages, tools=None: {"content": "Опишите товар", "tool_calls": []})
    ai_agent.answer_questions(s.user, card["id"], answers=pick)
    assert ai_agent._wants_generation(ctx) is True
    # Ответ — на эту кампанию: в память пространства не попадает.
    assert not [f for f in ai_agent.get_workspace(s.user, s.ws).get("ai_facts") or [] if f["key"] == "image_source"]


def test_workspace_images_from_campaign_ads(db, monkeypatch):
    s = _seed(db)
    _ad(db, s.user, s.acc, s.summer, 1, ["h1", "h2"])
    _ad(db, s.user, s.acc, s.winter, 2, ["h1", "gone"])
    _ad(db, s.user, s.other, s.foreign, 3, ["x1"])  # чужое пространство
    asked = []
    monkeypatch.setattr(ads, "get_connection_access_token", lambda conn_id, user_id: "tok")

    def previews(token, login, hashes):
        asked.append((login, sorted(hashes)))
        return {h: f"https://img/{h}" for h in hashes if h != "gone"}

    monkeypatch.setattr(ad_extras, "image_previews", previews)
    found = ai_agent.workspace_images(s.user, s.ws)
    assert [(i["hash"], i["account_id"], i["campaigns"], i["ads"]) for i in found["items"]] == [
        ("h1", s.acc, ["Окна зима", "Окна лето"], 2), ("h2", s.acc, ["Окна лето"], 1),
    ]
    assert found["items"][0]["preview_url"] == "https://img/h1" and asked == [("acc-a", ["gone", "h1", "h2"])]
    assert ai_agent.workspace_images(s.user, s.ws, account_id=s.acc)["items"][0]["hash"] == "h1"
    with pytest.raises(YandexError) as err:
        ai_agent.workspace_images(s.user, s.ws, account_id=s.other)
    assert err.value.code == "scope"

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s.user)}"}
    got = client.get(f"/api/web/workspaces/{s.ws}/ad-images", headers=auth).json()
    assert [i["hash"] for i in got["items"]] == ["h1", "h2"] and got["more"] is False
    stranger = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    assert client.get(f"/api/web/workspaces/{s.ws}/ad-images", headers=stranger).status_code == 404


def test_api_settings_and_board_generation(db, monkeypatch):
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s.user)}"}
    body = {"account_id": s.acc, "prompt": "окно", "count": 2}
    off = client.post(f"/api/web/workspaces/{s.ws}/ad-images/generate", headers=auth, json=body)
    assert off.status_code == 400 and off.json()["detail"]["code"] == "image-not-configured"

    put = client.put("/api/web/ai/image-gen", headers=auth, json={"provider": "openai", "api_key": IMAGE_KEY, "price_rub": 3})
    assert put.status_code == 200 and put.json()["configured"] and put.json()["price_rub"] == 3.0
    # PUT без цены её не стирает, null — стирает.
    assert client.put("/api/web/ai/image-gen", headers=auth, json={"provider": "openai"}).json()["price_rub"] == 3.0
    assert client.put("/api/web/ai/image-gen", headers=auth, json={"provider": "openai", "price_rub": None}).json()["price_rub"] is None
    got = client.get("/api/web/ai/image-gen", headers=auth).json()
    assert got["has_key"] and got["key_hint"] == "…1234" and [p["id"] for p in got["providers"]] == ["openai", "yandexart"]

    monkeypatch.setattr(yandex_module, "get_connection_access_token", lambda conn_id, user_id: "tok")
    monkeypatch.setattr(image_gen, "_request", lambda cfg, prompt: JPEG)
    uploaded = []
    monkeypatch.setattr(ad_extras, "upload_image", lambda token, login, *, name, data_b64: uploaded.append((login, name)) or {
        "hash": f"g{len(uploaded)}", "preview_url": f"https://img/g{len(uploaded)}"})
    made = client.post(f"/api/web/workspaces/{s.ws}/ad-images/generate", headers=auth, json=body)
    assert made.status_code == 200, made.text
    assert [(i["hash"], i["account_id"]) for i in made.json()["images"]] == [("g1", s.acc), ("g2", s.acc)]
    assert uploaded == [("acc-a", "ИИ 1"), ("acc-a", "ИИ 2")]
    # Чужой кабинет — нельзя, и ничего не рисуем.
    bad = client.post(f"/api/web/workspaces/{s.ws}/ad-images/generate", headers=auth, json={**body, "account_id": s.other})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "scope" and len(uploaded) == 2
