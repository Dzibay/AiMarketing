"""ИИ-ассистент: настройки BYOK, ход агента с инструментами, согласование действий — с фейковой моделью."""

import json

import pytest
from fastapi.testclient import TestClient

from app import actions, ai, ai_agent, workspaces
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_counter, make_user, make_workspace


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")


def _seed(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, name="Кабинет", login="acc-a")
    other_acc = make_account(db, user, conn_id, name="Чужой", login="acc-x")
    counter = make_counter(db, user, conn_id, 555, site="shop.ru")
    other_counter = make_counter(db, user, conn_id, 777, site="blog.ru")
    search = make_campaign(db, user, acc, 11, "Поиск")
    network = make_campaign(db, user, acc, 12, "РСЯ")
    foreign = make_campaign(db, user, other_acc, 21, "Блог")
    with db.connection() as conn:
        conn.execute("UPDATE direct_campaigns SET daily_budget_kopecks = 100000 WHERE id = %s", (search,))
    ws = make_workspace(db, user, 555, "Магазин", counter_row_id=counter, accounts=[acc], campaigns=[search, network])
    make_workspace(db, user, 777, "Блог", counter_row_id=other_counter, accounts=[other_acc], campaigns=[foreign])
    ai.save_settings(user, provider="openai", api_key="sk-test-1234abcd", model="gpt-test")
    chat = ai_agent.create_chat(user, ws)
    return {"user": user, "acc": acc, "ws": ws, "search": search, "network": network, "foreign": foreign, "chat": chat["id"]}


class FakeModel:
    """Отвечает заранее заданными ответами и запоминает, что ему отправили."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, cfg, messages, tools=None):
        self.calls.append(messages)
        return self.replies.pop(0)


def _call(name, args, call_id="c1"):
    return {"content": "", "tool_calls": [{"id": call_id, "name": name, "arguments": json.dumps(args)}]}


def _text(text):
    return {"content": text, "tool_calls": []}


def _start(user, chat, text, monkeypatch, *replies):
    fake = FakeModel(*replies)
    monkeypatch.setattr(ai, "complete", fake)
    ai_agent.post_message(user, chat, text)
    return fake, ai_agent.run_chat(user, chat)


def test_settings_validation_and_yandex_config(db):
    user = make_user(db)
    saved = ai.save_settings(user, provider="openai", api_key="sk-abcdef123456")
    assert saved["has_key"] and saved["configured"] and saved["key_hint"] == "…3456"
    kept = ai.save_settings(user, provider="openai", model="gpt-4o", api_key=None)
    assert kept["has_key"] and kept["model"] == "gpt-4o"
    for url in ("http://api.example.com/v1", "https://10.1.2.3/v1", "https://localhost/v1"):
        with pytest.raises(YandexError):
            ai.save_settings(user, provider="openai", base_url=url)
    with pytest.raises(YandexError):
        ai.save_settings(user, provider="yandexgpt")
    ai.save_settings(user, provider="yandexgpt", folder_id="b1gfolder", model="yandexgpt/latest")
    cfg = ai.load_config(user)
    assert cfg["model"] == "gpt://b1gfolder/yandexgpt/latest"
    assert cfg["headers"]["OpenAI-Project"] == "b1gfolder"
    assert cfg["base_url"] == ai.YANDEX_BASE
    cleared = ai.save_settings(user, provider="openai", api_key="")
    assert not cleared["has_key"] and not cleared["configured"]
    with pytest.raises(YandexError):
        ai.load_config(user)


def test_read_tools_then_answer(db, monkeypatch):
    s = _seed(db)
    fake, out = _start(
        s["user"],
        s["chat"],
        "Проанализируй кампании",
        monkeypatch,
        _call("list_campaigns", {}),
        _text("Обе кампании без расхода."),
    )
    assert out["status"] == "idle"
    system = fake.calls[0][0]["content"]
    assert "Магазин" in system and "555" in system and "Без согласования разрешено: ничего" in system
    tool_result = json.loads(fake.calls[1][-1]["content"])
    assert {c["name"] for c in tool_result["campaigns"]} == {"Поиск", "РСЯ"}
    chat = ai_agent.get_chat(s["user"], s["chat"])
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert chat["chat"]["title"] == "Проанализируй кампании" and chat["chat"]["status"] == "idle"
    assert chat["messages"][1]["tool_calls"][0]["label"] == "Список кампаний"


def test_action_waits_for_approval_then_runs(db, monkeypatch):
    s = _seed(db)
    done = []
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: done.append(kw) or {"ok": 1})
    _fake, out = _start(
        s["user"],
        s["chat"],
        "Останови Поиск",
        monkeypatch,
        _call("campaigns_action", {"campaign_row_ids": [s["search"]], "action": "pause", "reason": "дорого"}),
    )
    assert out["status"] == "waiting" and not done
    chat = ai_agent.get_chat(s["user"], s["chat"])
    action = chat["actions"][0]
    assert chat["chat"]["status"] == "waiting" and chat["chat"]["pending_actions"] == 1
    assert action["status"] == "pending" and action["summary"] == "Остановить: «Поиск»" and action["reason"] == "дорого"
    assert action["categories"][0]["id"] == "pause_resume"

    decided = ai_agent.decide_action(s["user"], action["id"], approve=True, remember=True)
    assert decided["pending"] == 0 and decided["job"]["kind"] == "ai_chat"
    with pytest.raises(YandexError):
        ai_agent.decide_action(s["user"], action["id"], approve=False)
    # «Запомнить» включает категорию в доступах пространства.
    assert workspaces.get_workspace(s["user"], s["ws"])["ai_access"]["categories"]["pause_resume"] is True

    fake = FakeModel(_text("Остановил."))
    monkeypatch.setattr(ai, "complete", fake)
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"
    assert done == [
        {"campaign_ids": [s["search"]], "action": "pause", "daily_budget_rub": None, "budget_change_pct": None, "workspace_id": s["ws"]}
    ]
    tool_msg = fake.calls[0][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["ok"] is True
    final = ai_agent.get_chat(s["user"], s["chat"])
    assert final["actions"][0]["status"] == "done" and final["messages"][-1]["content"] == "Остановил."


def test_access_rules_and_scope(db, monkeypatch):
    s = _seed(db)
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: {"ok": 1})
    workspaces.update_workspace(s["user"], s["ws"], ai_access={"categories": {"pause_resume": True, "budget": True}})

    # Чужая кампания — ошибка инструмента, без действия.
    fake, _ = _start(
        s["user"],
        s["chat"],
        "Останови Блог",
        monkeypatch,
        _call("campaigns_action", {"campaign_row_ids": [s["foreign"]], "action": "pause"}),
        _text("Не могу."),
    )
    assert "не входят" in json.loads(fake.calls[1][-1]["content"])["error"]
    assert ai_agent.get_chat(s["user"], s["chat"])["actions"] == []

    # Разрешённая категория выполняется сама; +50% бюджета больше лимита 30% — на согласование.
    _fake, out = _start(
        s["user"],
        s["chat"],
        "Запусти РСЯ и подними бюджет Поиска",
        monkeypatch,
        {
            "content": "",
            "tool_calls": [
                {"id": "a", "name": "campaigns_action", "arguments": json.dumps({"campaign_row_ids": [s["network"]], "action": "resume"})},
                {
                    "id": "b",
                    "name": "campaigns_action",
                    "arguments": json.dumps({"campaign_row_ids": [s["search"]], "action": "set_budget", "daily_budget_rub": 1500}),
                },
            ],
        },
    )
    assert out["status"] == "waiting"
    acts = ai_agent.get_chat(s["user"], s["chat"])["actions"]
    assert [(a["status"], a["auto"]) for a in acts] == [("done", True), ("pending", False)]
    assert acts[1]["summary"] == "Изменить дневной бюджет: «Поиск» — 1 000 ₽ → 1 500 ₽"

    # Новое сообщение закрывает ожидающее действие, история для модели остаётся корректной.
    fake, _ = _start(s["user"], s["chat"], "Не надо", monkeypatch, _text("Хорошо."))
    acts = ai_agent.get_chat(s["user"], s["chat"])["actions"]
    assert acts[1]["status"] == "rejected" and "Пропущено" in acts[1]["error"]
    sent = fake.calls[0]
    ids = [m.get("tool_call_id") for m in sent if m["role"] == "tool"]
    assert "a" in ids and "b" in ids

    # Полный доступ: тот же +50% без согласования.
    workspaces.update_workspace(s["user"], s["ws"], ai_access={"full_access": True})
    _fake, out = _start(
        s["user"],
        s["chat"],
        "Подними бюджет",
        monkeypatch,
        _call("campaigns_action", {"campaign_row_ids": [s["search"]], "action": "set_budget", "budget_change_pct": 50}, "z"),
        _text("Готово."),
    )
    assert out["status"] == "idle"
    assert ai_agent.get_chat(s["user"], s["chat"])["actions"][-1]["status"] == "done"


def test_reject_all_lets_model_continue(db, monkeypatch):
    s = _seed(db)
    _start(
        s["user"],
        s["chat"],
        "Удали РСЯ",
        monkeypatch,
        _call("campaigns_action", {"campaign_row_ids": [s["network"]], "action": "delete"}),
    )
    assert ai_agent.get_chat(s["user"], s["chat"])["actions"][0]["categories"][0]["id"] == "destructive"
    out = ai_agent.decide_all(s["user"], s["chat"], approve=False)
    assert out["decided"] == 1 and out["job"]
    fake = FakeModel(_text("Ок, не удаляю."))
    monkeypatch.setattr(ai, "complete", fake)
    ai_agent.run_chat(s["user"], s["chat"])
    assert "отклонил" in fake.calls[0][-1]["content"]
    assert ai_agent.get_chat(s["user"], s["chat"])["actions"][0]["status"] == "rejected"


def test_model_error_marks_chat(db, monkeypatch):
    s = _seed(db)

    def boom(cfg, messages, tools=None):
        raise YandexError("ai-auth", "Провайдер ИИ отклонил ключ")

    monkeypatch.setattr(ai, "complete", boom)
    ai_agent.post_message(s["user"], s["chat"], "Привет")
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "error"
    chat = ai_agent.get_chat(s["user"], s["chat"])["chat"]
    assert chat["status"] == "error" and "ключ" in chat["error"]


def test_ai_api(db, monkeypatch):
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    other = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}

    got = client.get("/api/web/ai/settings", headers=auth).json()
    assert got["configured"] and got["key_hint"] == "…abcd" and "api_key_enc" not in got
    put = client.put("/api/web/ai/settings", headers=auth, json={"provider": "openai", "model": "gpt-x"})
    assert put.json()["has_key"] and put.json()["model"] == "gpt-x"
    bad = client.put("/api/web/ai/settings", headers=auth, json={"provider": "openai", "base_url": "http://x"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "ai-url"

    created = client.post(f"/api/web/workspaces/{s['ws']}/chats", headers=auth, json={}).json()
    assert created["title"] == "Новый чат"
    items = client.get(f"/api/web/workspaces/{s['ws']}/chats", headers=auth).json()["items"]
    assert len(items) == 2
    assert client.get(f"/api/web/ai/chats/{created['id']}", headers=other).status_code == 404

    sent = client.post(f"/api/web/ai/chats/{created['id']}/messages", headers=auth, json={"text": "Сводка?"}).json()
    assert sent["job"]["kind"] == "ai_chat" and sent["message"]["role"] == "user"
    busy = client.post(f"/api/web/ai/chats/{created['id']}/messages", headers=auth, json={"text": "Ещё"})
    assert busy.status_code == 400 and busy.json()["detail"]["code"] == "busy"

    monkeypatch.setattr(ai, "complete", FakeModel(_call("moderate_campaign", {"campaign_row_id": s["search"]})))
    ai_agent.run_chat(s["user"], created["id"])
    chat = client.get(f"/api/web/ai/chats/{created['id']}", headers=auth).json()
    action = chat["actions"][0]
    assert chat["chat"]["status"] == "waiting" and action["summary"] == "Отправить на модерацию «Поиск»"
    assert client.post(f"/api/web/ai/actions/{action['id']}/decide", headers=other, json={"approve": True}).status_code == 404
    decided = client.post(f"/api/web/ai/actions/{action['id']}/decide", headers=auth, json={"approve": False}).json()
    assert decided["action"]["status"] == "rejected" and decided["job"]

    assert client.patch(f"/api/web/ai/chats/{created['id']}", headers=auth, json={"title": "Модерация"}).json()["title"] == "Модерация"
    assert client.delete(f"/api/web/ai/chats/{created['id']}", headers=auth).json() == {"ok": True}
    assert client.get(f"/api/web/ai/chats/{created['id']}", headers=auth).status_code == 404


def test_read_tools_work_on_local_data(db):
    s = _seed(db)
    ctx = ai_agent.Context(s["user"], s["ws"])
    for name in ("get_workspace_summary", "get_analytics", "list_campaigns", "get_structure_tops", "list_recent_actions", "get_campaign_options"):
        assert isinstance(ai_agent.READ_TOOLS[name](ctx, {}), dict), name
    camp = ai_agent.READ_TOOLS["get_campaign"](ctx, {"campaign_row_id": s["search"]})
    assert camp["campaign"]["name"] == "Поиск" and camp["keywords"] == []
    queries = ai_agent.READ_TOOLS["get_search_queries"](ctx, {"campaign_row_id": s["search"]})
    assert queries["total"] == 0 and "refresh_search_queries" in queries["hint"]
    with pytest.raises(YandexError):
        ai_agent.READ_TOOLS["get_campaign"](ctx, {"campaign_row_id": s["foreign"]})
    # Копейки модель видит рублями.
    assert ai_agent._for_ai({"cost_kopecks": 12345, "bid_micros": 2_500_000}) == {"cost_rub": 123.45, "bid_rub": 2.5}


def test_same_named_campaigns_get_direct_number(db):
    s = _seed(db)
    ctx = ai_agent.Context(s["user"], s["ws"])
    make_campaign(db, s["user"], s["acc"], 31, "Поиск")  # одноимённая, но вне пространства — номер не нужен
    assert ai_agent._cnames(ctx.campaigns([s["search"], s["network"]])) == "«Поиск», «РСЯ»"

    twin = make_campaign(db, s["user"], s["acc"], 13, "Поиск")
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id) VALUES (%s, %s, %s)",
            (s["ws"], twin, s["user"]),
        )
    assert ai_agent._cnames(ctx.campaigns([s["search"], s["network"], twin])) == "«Поиск» №11, «Поиск» №13, «РСЯ»"
    assert ai_agent._cname(ctx.campaign(s["search"])) == "«Поиск» №11"


def _pending(user, chat, tool):
    [action] = [a for a in ai_agent.get_chat(user, chat)["actions"] if a["status"] == "pending" and a["tool"] == tool]
    return action


def _turn(user, chat, monkeypatch, *replies):
    fake = FakeModel(*replies)
    monkeypatch.setattr(ai, "complete", fake)
    return fake, ai_agent.run_chat(user, chat)


def test_new_campaign_goes_by_stages(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    created = []
    monkeypatch.setattr(
        actions, "create_campaign", lambda user_id, **kw: created.append(kw) or {"campaign": {"id": 99}, "status": "ok"}
    )

    def spec(keywords, titles):
        return {"account_id": s["acc"], "spec": {"name": "Окна", "negative_keywords": ["бесплатно"], "groups": [
            {"name": "Окна ПВХ", "keywords": keywords,
             "ad": {"titles": titles, "texts": ["Замер в день заказа"], "href": "https://shop.ru"}},
        ]}}

    # Сразу кампанию — нельзя: ни один этап не согласован.
    fake, out = _start(
        user, chat, "Сделай новую кампанию на окна", monkeypatch,
        _call("create_campaign", spec(["купить окна пвх"], ["Окна от завода"])),
        _call("propose_items", {"stage": "goals", "rows": [
            {"cells": ["Что рекламируем", "Окна ПВХ"]}, {"cells": ["Гео", "Москва"]},
        ]}, "c2"),
    )
    error = json.loads(fake.calls[1][-1]["content"])["error"]
    assert "Ещё не согласовано: Цели кампании, Анализ, Ключи и группы, Заголовки и тексты, Настройки" in error
    assert out["status"] == "waiting"
    table = _pending(user, chat, "propose_items")
    assert table["kind"] == "ask" and table["summary"] == "Цели кампании" and len(table["args"]["rows"]) == 2
    assert table["args"]["rows"][0]["kind"] == "goal"
    with pytest.raises(YandexError):  # таблица решается только своей кнопкой
        ai_agent.decide_action(user, table["id"], approve=True)
    build = ai_agent.get_chat(user, chat)["build"]
    assert build["stages"][0] == {"key": "goals", "label": "Цели", "status": "pending", "count": 2, "action_id": table["id"]}
    assert [g["pending"] for g in build["goals"]] == [True, True] and build["groups"] == []

    # Цели согласованы, анализ: «Двери» сняты галочкой.
    ai_agent.decide_items(user, table["id"], rows=[1, 2])
    _turn(user, chat, monkeypatch, _call("propose_items", {"stage": "analysis", "rows": [
        {"cells": ["Окна ПВХ", "12 000"]}, {"cells": ["Двери", "5 000"]},
    ]}, "c2a"))
    table = _pending(user, chat, "propose_items")
    decided = ai_agent.decide_items(user, table["id"], rows=[1])
    assert decided["action"]["result"]["removed_rows"] == [2] and decided["job"]["kind"] == "ai_chat"
    fake, out = _turn(user, chat, monkeypatch, _call("propose_items", {"stage": "keywords", "rows": [
        {"section": "Окна ПВХ", "cells": ["купить окна пвх", "12 000"]},
        {"section": "Окна ПВХ", "cells": ["окна дешево", "900"]},
        {"section": "Минус-фразы", "kind": "negative", "cells": ["бесплатно"]},
    ]}, "c3"))
    reply = json.loads(fake.calls[0][-1]["content"])
    assert reply["approved"] == {"—": ["Окна ПВХ — 12 000"]} and reply["removed"] == {"—": ["Двери — 5 000"]}

    ai_agent.decide_items(user, _pending(user, chat, "propose_items")["id"], rows=[1, 3], comment="Добавь «окна в рассрочку»")
    _turn(user, chat, monkeypatch, _call("propose_items", {"stage": "ads", "rows": [
        {"section": "Окна ПВХ", "kind": "title", "cells": ["Окна от завода"]},
        {"section": "Окна ПВХ", "kind": "title", "cells": ["Окна за 1 день"]},
        {"section": "Окна ПВХ", "kind": "text", "cells": ["Замер в день заказа"]},
        {"section": "Окна ПВХ", "kind": "text", "cells": ["Скидка 10%"], "checked": False},
    ]}, "c4"))

    # Панель сборки: согласованное по этапам, предложенные заголовки — пока со своей пометкой.
    build = ai_agent.get_chat(user, chat)["build"]
    assert [(st["key"], st["status"]) for st in build["stages"]] == [
        ("goals", "done"), ("analysis", "done"), ("keywords", "done"), ("negatives", "done"),
        ("ads", "pending"), ("extras", "todo"), ("settings", "todo"),
    ]
    assert [st["count"] for st in build["stages"][2:5]] == [1, 1, 3]  # минус-фраза из таблицы ключей — своим этапом
    assert [d["text"] for d in build["directions"]] == ["Окна ПВХ"]
    [group] = build["groups"]
    assert group["name"] == "Окна ПВХ" and [(k["text"], k["value"]) for k in group["keywords"]] == [("купить окна пвх", "12 000")]
    assert [(t["text"], t["pending"]) for t in group["titles"]] == [("Окна от завода", True), ("Окна за 1 день", True)]
    assert [t["text"] for t in group["texts"]] == ["Замер в день заказа"]  # снятое галочкой не показываем
    assert [n["text"] for n in build["negatives"]] == ["бесплатно"] and build["draft"] is None

    ai_agent.decide_items(user, _pending(user, chat, "propose_items")["id"], rows=[1, 2, 3])
    _turn(user, chat, monkeypatch, _call("propose_items", {"stage": "settings", "columns": ["Настройка", "Значение"], "rows": [
        {"kind": "setting", "cells": ["Бюджет", "500 ₽/день"]},
    ]}, "c5"))
    ai_agent.decide_items(user, _pending(user, chat, "propose_items")["id"], rows=[1])

    # Снятую фразу взять нельзя; добавленную комментарием — можно.
    fake, out = _turn(
        user, chat, monkeypatch,
        _call("create_campaign", spec(["купить окна пвх", "окна дешево"], ["Окна от завода", "Окна за 1 день"]), "c6"),
        _call("create_campaign", spec(["купить окна пвх", "окна в рассрочку"], ["Окна от завода", "Окна за 1 день"]), "c7"),
    )
    error = json.loads(fake.calls[1][-1]["content"])["error"]
    assert "снял галочку" in error and "«окна дешево»" in error
    assert out["status"] == "waiting" and not created
    card = _pending(user, chat, "create_campaign")
    assert [(i["id"], i["kind"]) for i in card["items"]] == [
        ("spec.groups.0.keywords.0", "keyword"),
        ("spec.groups.0.keywords.1", "keyword"),
        ("spec.groups.0.ad.titles.0", "title"),
        ("spec.groups.0.ad.titles.1", "title"),
        ("spec.groups.0.ad.texts.0", "text"),
        ("spec.negative_keywords.0", "negative"),
    ]

    # Галочки: без единственного текста объявление не собрать — действие остаётся как было.
    with pytest.raises(YandexError) as err:
        ai_agent.decide_action(user, card["id"], approve=True, exclude=["spec.groups.0.ad.texts.0"])
    assert "Без снятых строк" in err.value.message
    assert _pending(user, chat, "create_campaign")["args"] == card["args"]

    # Сняли фразу и заголовок (и чужой путь — он не учитывается).
    ai_agent.decide_action(
        user, card["id"], approve=True, exclude=["spec.groups.0.keywords.1", "spec.groups.0.ad.titles.1", "spec.name"]
    )
    fake, out = _turn(user, chat, monkeypatch, _text("Черновик создан."))
    [kw] = created
    group = kw["spec"]["groups"][0]
    assert group["keywords"] == ["купить окна пвх"] and group["ad"]["titles"] == ["Окна от завода"]
    result = json.loads(fake.calls[0][-1]["content"])
    assert result["ok"] and result["removed_by_user"] == ["Окна ПВХ: окна в рассрочку", "Окна ПВХ: Окна за 1 день"]
    done = [a for a in ai_agent.get_chat(user, chat)["actions"] if a["id"] == card["id"]][0]
    assert done["status"] == "done" and [r["text"] for r in done["removed"]] == ["окна в рассрочку", "Окна за 1 день"]
    assert "1 фраза" in done["summary"]

    # После создания панель показывает то, что ушло в Директ, без снятого в карточке.
    build = ai_agent.get_chat(user, chat)["build"]
    assert build["draft"]["status"] == "done" and build["draft"]["campaign_row_id"] == 99 and build["draft"]["name"] == "Окна"
    [group] = build["groups"]
    assert [(k["text"], k["value"]) for k in group["keywords"]] == [("купить окна пвх", "12 000")]
    assert [t["text"] for t in group["titles"]] == ["Окна от завода"] and group["href"] == "https://shop.ru"
    assert all(st["status"] in ("done", "skipped") for st in build["stages"])

    # В созданную здесь кампанию — тоже только согласованное.
    with db.connection() as conn:
        conn.execute("UPDATE ai_actions SET result = %s WHERE id = %s", (json.dumps({"campaign": {"id": s["search"]}}), card["id"]))
    ctx = ai_agent.Context(user, s["ws"])
    ctx.chat_id = chat
    with pytest.raises(YandexError) as err:
        ai_agent._p_add_negatives(ctx, {"campaign_row_id": s["search"], "phrases": ["бесплатно", "своими руками"]})
    assert "«своими руками»" in err.value.message


def test_items_table_rejected_and_decide_all_with_exclude(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _start(user, chat, "Предложи заголовки", monkeypatch, _call("propose_items", {"stage": "ads", "rows": [
        {"kind": "title", "cells": ["Окна от завода"]},
    ]}))
    table = _pending(user, chat, "propose_items")
    ai_agent.decide_items(user, table["id"], approve=False, comment="Скучно")
    fake, _out = _turn(user, chat, monkeypatch, _text("Переделаю"))
    reply = json.loads(fake.calls[0][-1]["content"])
    assert reply["ok"] is False and reply["comment"] == "Скучно" and "не переходи" in reply["note"]
    with pytest.raises(YandexError):
        ai_agent.decide_items(user, table["id"], rows=[1])  # уже решено

    # «Одобрить все» тоже учитывает снятые галочки.
    done = []
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: done.append(kw) or {"ok": 1})
    _start(user, chat, "Останови обе", monkeypatch, _call("campaigns_action", {
        "campaign_row_ids": [s["search"], s["network"]], "action": "pause", "reason": "дорого",
    }, "c3"))
    card = _pending(user, chat, "campaigns_action")
    assert [i["text"] for i in card["items"]] == ["Поиск", "РСЯ"]
    ai_agent.decide_all(user, chat, approve=True, exclude={str(card["id"]): ["campaign_row_ids.1"]})
    _turn(user, chat, monkeypatch, _text("Остановил."))
    assert done[0]["campaign_ids"] == [s["search"]]


def _empty_workspace(db):
    """Первое пространство нового пользователя: кабинет и счётчик есть, кампаний нет."""
    user = make_user(db, "first@example.com")
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, name="Новый кабинет", login="new-acc")
    counter = make_counter(db, user, conn_id, 888, site="sofa.ru")
    ws = make_workspace(db, user, 888, "Диваны", counter_row_id=counter, accounts=[acc], campaigns=[])
    ai.save_settings(user, provider="openai", api_key="sk-test-1234abcd", model="gpt-test")
    chat = ai_agent.create_chat(user, ws)
    return {"user": user, "acc": acc, "ws": ws, "chat": chat["id"]}


def test_first_campaign_in_empty_workspace(db, monkeypatch):
    s = _empty_workspace(db)
    user, chat = s["user"], s["chat"]
    created = []
    monkeypatch.setattr(
        actions, "create_campaign", lambda user_id, **kw: created.append(kw) or {"campaign": {"id": 99}, "status": "ok"}
    )

    # Первая кампания: модель знает, что истории нет, и не выдвигает гипотезу про 0 ₽ на пустом месте.
    fake, _out = _start(
        user, chat, "Хочу первую кампанию на диваны", monkeypatch,
        _call("list_campaigns", {}),
        _call("get_signals", {}, "c2"),
        _call("get_wordstat", {"phrases": ["купить диван"], "region_ids": [213]}, "c3"),
        _call("propose_items", {"stage": "goals", "rows": [
            {"cells": ["Что рекламируем", "Прямые и угловые диваны"]}, {"cells": ["Бюджет", "3500 ₽/неделя"]},
        ]}, "c4"),
    )
    prompt = fake.calls[0][0]["content"]
    assert "ещё нет ни одной кампании" in prompt and "Максимум кликов" in prompt
    assert "Механика конверсий за 0" not in prompt

    # Пустое пространство отдаёт пустые данные, а не падает.
    assert json.loads(fake.calls[1][-1]["content"])["campaigns"] == []
    assert "Итого: 0 кампаний" in json.loads(fake.calls[2][-1]["content"])["digest"]
    assert "Вордстат не подключён" in json.loads(fake.calls[3][-1]["content"])["error"]

    # Дальше — те же согласуемые этапы, что и для любой новой кампании.
    ai_agent.decide_items(user, _pending(user, chat, "propose_items")["id"], rows=[1, 2])
    stages = [
        ("analysis", [{"cells": ["Диваны прямые", "—"]}, {"cells": ["Угловые диваны", "—"]}]),
        ("keywords", [
            {"section": "Прямые", "cells": ["купить диван", "—"]},
            {"section": "Прямые", "cells": ["диван прямой цена", "—"]},
        ]),
        ("negatives", [{"kind": "negative", "cells": ["бесплатно"]}, {"kind": "negative", "cells": ["б у"]}]),
        ("ads", [
            {"section": "Прямые", "kind": "title", "cells": ["Диваны от производителя"]},
            {"section": "Прямые", "kind": "title", "cells": ["Прямые диваны со скидкой"]},
            {"section": "Прямые", "kind": "title", "cells": ["Доставка по городу"]},
            {"section": "Прямые", "kind": "text", "cells": ["Гарантия 2 года, примерка в салоне"]},
        ]),
        ("settings", [
            {"kind": "setting", "cells": ["Стратегия", "Максимум кликов"]},
            {"kind": "setting", "cells": ["Бюджет", "3500 ₽/неделя"]},
        ]),
    ]
    for stage, rows in stages:
        _turn(user, chat, monkeypatch, _call("propose_items", {"stage": stage, "rows": rows}, f"s-{stage}"))
        ai_agent.decide_items(user, _pending(user, chat, "propose_items")["id"], rows=list(range(1, len(rows) + 1)))

    spec = {
        "name": "Диваны — поиск",
        "strategy": {"search": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 3500},
        "negative_keywords": ["бесплатно", "б у"],
        "groups": [{
            "name": "Прямые",
            "keywords": ["купить диван", "диван прямой цена"],
            "ad": {"titles": ["Диваны от производителя", "Прямые диваны со скидкой", "Доставка по городу"],
                   "texts": ["Гарантия 2 года, примерка в салоне"], "href": "https://sofa.ru"},
        }],
    }
    _turn(user, chat, monkeypatch, _call("create_campaign", {"account_id": s["acc"], "spec": spec}, "c-create"))
    card = _pending(user, chat, "create_campaign")
    assert "WB_MAXIMUM_CLICKS" in card["summary"] and not created
    ai_agent.decide_action(user, card["id"], approve=True)
    _turn(user, chat, monkeypatch, _text("Черновик создан."))
    assert [kw["spec"]["name"] for kw in created] == ["Диваны — поиск"]


def test_prompt_with_campaigns_has_no_first_campaign_rule(db, monkeypatch):
    s = _seed(db)
    fake, _out = _start(s["user"], s["chat"], "Привет", monkeypatch, _text("Здравствуйте"))
    prompt = fake.calls[0][0]["content"]
    assert "ещё нет ни одной кампании" not in prompt and "Механика конверсий за 0" in prompt
