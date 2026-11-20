"""Память сборки: сервис сам помнит, что согласовано и что создано в Директе, и не даёт собирать по кругу."""

import json

import pytest

from app import actions, ai_agent
from tests.dbutil import make_campaign
from tests.test_db_ai import _call, _pending, _secret, _seed, _start, _text, _turn  # noqa: F401 — _secret: автофикстура

GOALS = [{"cells": ["Что рекламируем", "Окна ПВХ"]}, {"cells": ["Гео", "Москва"]}]
KEYWORDS = [
    {"section": "Окна ПВХ", "cells": ["купить окна пвх", "12 000"]},
    {"section": "Окна ПВХ", "cells": ["окна в рассрочку", "900"]},
    {"section": "Минус-фразы", "kind": "negative", "cells": ["бесплатно"]},
]
ADS = [
    {"section": "Окна ПВХ", "kind": "title", "cells": ["Окна от завода"]},
    {"section": "Окна ПВХ", "kind": "text", "cells": ["Замер в день заказа"]},
]
SETTINGS = [{"kind": "setting", "cells": ["Бюджет", "500 ₽/день"]}]



@pytest.fixture(autouse=True)
def _old_brief(monkeypatch):
    """Сборки здесь — без campaign в брифе (как до черновика с первого шага): черновик создаёт create_campaign.
    Запись в Директ по шагам — tests/test_db_ai_build_sync.py."""
    monkeypatch.setattr(ai_agent, "_need_campaign", lambda args, payload: None)

def _table(stage, rows, **extra):
    # Первая таблица сборки несёт требования ТЗ; в этих тестах ТЗ без цифр и запретов.
    if stage in ("goals", "analysis"):
        extra.setdefault("requirements", {})
    # Сборка одной доской — прежний режим, его теперь включают явно; мастер по шагам — by_stages=True.
    extra.setdefault("by_stages", False)
    return _call("propose_items", {"stage": stage, "rows": rows, **extra}, f"t-{stage}")


def _approve(user, chat):
    table = _pending(user, chat, "propose_items")
    ai_agent.decide_items(user, table["id"], rows=[r["n"] for r in table["args"]["rows"]])


def _reply(fake, step):
    """Ответ инструмента, который модель увидела перед шагом step."""
    return json.loads(fake.calls[step][-1]["content"])


def _cards(user, chat, tool="propose_items"):
    return [a for a in ai_agent.get_chat(user, chat)["actions"] if a["tool"] == tool]


def _spec(s, name="Окна"):
    return {"account_id": s["acc"], "spec": {"name": name, "daily_budget_rub": 500, "negative_keywords": ["бесплатно"], "groups": [
        {"name": "Окна ПВХ", "region_ids": [213], "keywords": ["купить окна пвх", "окна в рассрочку"],
         "ad": {"titles": ["Окна от завода"], "texts": ["Замер в день заказа"], "href": "https://shop.ru"}},
    ]}}


def _build(db, s, monkeypatch, upto="create"):
    """Сборка «Окна» по этапам; upto — на каком согласованном этапе остановиться (create — черновик создан)."""
    user, chat = s["user"], s["chat"]
    row = make_campaign(db, user, s["acc"], 715000001, "Окна", state="OFF", status="DRAFT")
    monkeypatch.setattr(actions, "create_campaign", lambda user_id, **kw: {"campaign": {"id": row}, "status": "ok"})
    _start(user, chat, "Сделай новую кампанию на окна", monkeypatch, _table("goals", GOALS, by_stages=True))
    _approve(user, chat)
    stages = [("analysis", [{"cells": ["Окна ПВХ", "12 000"]}]), ("keywords", KEYWORDS), ("ads", ADS), ("settings", SETTINGS)]
    for stage, rows in stages:
        if upto == "goals":
            return row
        extra = {"columns": ["Настройка", "Значение"]} if stage == "settings" else {}
        _turn(user, chat, monkeypatch, _table(stage, rows, **extra))
        _approve(user, chat)
        if upto == stage:
            return row
    _turn(user, chat, monkeypatch, _call("create_campaign", _spec(s), "create"))
    ai_agent.decide_action(user, _pending(user, chat, "create_campaign")["id"], approve=True)
    return row


def test_campaign_is_found_by_its_direct_number(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    with db.connection() as conn:
        conn.execute("UPDATE direct_campaigns SET campaign_id = 715602653 WHERE id = %s", (s["search"],))
    fake, out = _start(
        user, chat, "Останови кампанию 715602653", monkeypatch,
        _call("get_campaign", {"campaign_row_id": 715602653}),
        _call("campaigns_action", {"campaign_row_ids": ["715602653"], "action": "pause", "reason": "просьба"}, "c2"),
    )
    assert "error" not in _reply(fake, 1)
    assert out["status"] == "waiting"
    assert _pending(user, chat, "campaigns_action")["args"]["campaign_row_ids"] == [s["search"]]

    # Номер не из проекта так и остаётся ошибкой — с подсказкой, каким номером называть кампанию.
    fake, _ = _start(user, chat, "А 999?", monkeypatch, _call("get_campaign", {"campaign_row_id": 999}), _text("Такой нет."))
    assert "номером в Директе" in _reply(fake, 1)["error"]


def test_service_tells_the_model_what_is_approved(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    fake, _ = _start(user, chat, "Сделай новую кампанию на окна", monkeypatch, _table("goals", GOALS, by_stages=True))
    # Сборки ещё нет — сводки нет, запрос обычный.
    assert fake.notes == [""] and fake.cfgs[0]["volatile_tail"] == 0
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _table("analysis", [{"cells": ["Окна ПВХ", "12 000"]}]))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _table("keywords", KEYWORDS))
    _approve(user, chat)

    fake, _ = _turn(user, chat, monkeypatch, _call("get_build", {}), _text("Готовлю объявления."))
    note = fake.notes[0]
    assert note.startswith(ai_agent.BUILD_MEMORY_NOTE) and fake.cfgs[0]["volatile_tail"] == 1
    assert "- Цели: согласовано, строк: 2" in note and "- Объявления: не начато" in note
    assert "«Окна ПВХ» (фраз 2)" in note
    assert "Дальше: следующий этап — «Объявления» (перед ним, если кампании это нужно, — необязательные: «Аудитории»)" in note
    # Сводка — только в запросе: в переписке чата её нет, в сам запрос она встаёт последней.
    assert all(ai_agent.BUILD_MEMORY_NOTE not in str(m.get("content")) for m in fake.calls[1])
    assert all(ai_agent.BUILD_MEMORY_NOTE not in (m["content"] or "") for m in ai_agent.get_chat(user, chat)["messages"])

    # get_build отдаёт согласованное слово в слово — и после того, как таблицы выпали из окна переписки.
    snapshot = _reply(fake, 1)
    assert snapshot["created_campaigns"] == [] and snapshot["build"]["status"] == "собирается"
    assert snapshot["build"]["goals"] == [{"text": "Что рекламируем", "value": "Окна ПВХ"}, {"text": "Гео", "value": "Москва"}]
    assert snapshot["build"]["groups"] == [{"name": "Окна ПВХ", "keywords": ["купить окна пвх", "окна в рассрочку"]}]
    assert snapshot["build"]["negative_keywords"] == ["бесплатно"] and snapshot["build"]["groups_total"] == 1
    assert "«Объявления»" in snapshot["build"]["next"]


def test_approved_stage_is_not_shown_again(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _build(db, s, monkeypatch, upto="keywords")
    shown = len(_cards(user, chat))

    fake, out = _turn(
        user, chat, monkeypatch,
        # Те же цели другими словами — этап согласован, карточки не будет.
        _table("goals", [{"cells": ["Продукт", "Пластиковые окна"]}]),
        # Те же ключи слово в слово — сервис отвечает сам, без карточки.
        _table("keywords", KEYWORDS),
        # Новые фразы в уже согласованную группу — без revise нельзя.
        _table("keywords", [{"section": "Окна ПВХ", "cells": ["окна недорого", "500"]}]),
        _text("Перехожу к объявлениям."),
    )
    assert "уже согласовал" in _reply(fake, 1)["error"] and "revise=true" in _reply(fake, 1)["error"]
    repeated = _reply(fake, 2)
    assert repeated["already_approved"] is True and "следующий этап — «Объявления»" in repeated["note"]
    assert "«Окна ПВХ»" in _reply(fake, 3)["error"] and "уже согласованы" in _reply(fake, 3)["error"]
    assert out["status"] == "idle" and len(_cards(user, chat)) == shown

    # Маркетолог попросил дополнить — revise=true; новая группа проходит и без него.
    more = [{"section": "Окна ПВХ", "cells": ["окна недорого", "500"]}]
    _start(user, chat, "Добавь фразу «окна недорого»", monkeypatch, _table("keywords", more, revise=True))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _table("keywords", [{"section": "Балконы", "cells": ["остекление балкона", "4 000"]}]))
    _approve(user, chat)
    build = ai_agent.get_chat(user, chat)["build"]
    assert [(g["name"], [k["text"] for k in g["keywords"]]) for g in build["groups"]] == [
        ("Окна ПВХ", ["купить окна пвх", "окна в рассрочку", "окна недорого"]),
        ("Балконы", ["остекление балкона"]),
    ]


def test_created_campaign_is_edited_not_rebuilt(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    row = _build(db, s, monkeypatch)

    fake, out = _turn(
        user, chat, monkeypatch,
        # Правка после создания — не повод собирать кампанию заново и создавать вторую.
        _table("goals", GOALS),
        _call("create_campaign", _spec(s, "Окна 2"), "again"),
        _call("get_build", {}, "look"),
        _text("Черновик создан, правлю его."),
    )
    note = fake.notes[0]
    assert f"«Окна» — campaign_row_id {row}, номер в Директе 715000001" in note and "Заново её не собирай" in note
    assert "Сборка новой кампании" not in note  # черновик создан — сборки в работе нет
    assert "уже создана кампания «Окна»" in _reply(fake, 1)["error"] and "new_campaign=true" in _reply(fake, 1)["error"]
    assert "уже создана кампания «Окна»" in _reply(fake, 2)["error"]
    snapshot = _reply(fake, 3)
    assert snapshot["created_campaigns"][0]["campaign_row_id"] == row and snapshot["build"]["status"] == "черновик создан"
    assert out["status"] == "idle" and len(_cards(user, chat, "create_campaign")) == 1

    # Маркетолог прямо попросил ещё одну кампанию: новая сборка начинается с чистой доски.
    doors = _table("goals", [{"cells": ["Что рекламируем", "Двери"]}], new_campaign=True, by_stages=True)
    _start(user, chat, "Теперь сделай отдельную кампанию на Подмосковье", monkeypatch, doors)
    build = ai_agent.get_chat(user, chat)["build"]
    assert build["draft"] is None and build["groups"] == [] and build["stages"][0]["status"] == "pending"
    _approve(user, chat)
    fake, out = _turn(
        user, chat, monkeypatch,
        _call("create_campaign", _spec(s), "twin"),  # то же название — двойник
        _call("create_campaign", _spec(s, "Окна — Подмосковье"), "second"),
    )
    assert "Сборка новой кампании" in fake.notes[0] and "- Цели: согласовано" in fake.notes[0]
    assert "«Окна» в этом чате уже создана" in _reply(fake, 1)["error"]
    # Согласованное раньше переносится в новую кампанию без новых таблиц.
    assert out["status"] == "waiting" and _pending(user, chat, "create_campaign")["args"]["spec"]["name"] == "Окна — Подмосковье"

    # Удалённая из Директа кампания созданной больше не считается.
    with db.connection() as conn:
        conn.execute("UPDATE ai_actions SET status = 'rejected' WHERE id = %s", (_pending(user, chat, "create_campaign")["id"],))
        conn.execute("DELETE FROM direct_campaigns WHERE id = %s", (row,))
    fake, _ = _start(user, chat, "Что дальше?", monkeypatch, _text("Ок."))
    assert "созданные в этом чате" not in fake.notes[0]


def test_general_chat_has_no_build_memory(db, monkeypatch):
    s = _seed(db)
    chat = ai_agent.create_chat(s["user"], None)["id"]
    fake, _ = _start(s["user"], chat, "Привет", monkeypatch, _text("Здравствуйте."))
    assert fake.notes == [""] and fake.cfgs[0]["volatile_tail"] == 0
