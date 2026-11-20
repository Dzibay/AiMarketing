"""Быстрые ссылки и уточнения правят прямо на доске; правила Директа — в «Готовности» и у строк."""

import pytest

from app import ai_agent
from app.yandex import YandexError
from tests.test_db_ai import _secret, _seed, _start  # noqa: F401 — _secret: автофикстура
from tests.test_db_ai_board import _fake_direct, _held, _stages
from tests.test_db_ai_build_memory import _table

EXTRAS = [
    {"section": "Быстрые ссылки", "cells": ["Цены", "https://shop.ru/price", "Прайс на все окна"]},
    {"section": "Быстрые ссылки", "cells": ["Доставка", "https://shop.ru/d", ""]},
    {"cells": ["Уточнения", "Гарантия 5 лет, Монтаж за день"]},
]


def test_links_are_edited_on_the_board(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _fake_direct(db, s, monkeypatch)
    _start(user, chat, "Собери кампанию на окна", monkeypatch, *_stages(_table("extras", EXTRAS)))
    extras = next(t for t in _held(user, chat) if t["args"]["stage"] == "extras")

    build = ai_agent.get_chat(user, chat)["build"]
    checks = {c["key"]: c["state"] for c in build["checks"]}
    assert checks["rules"] == "ok" and checks["overlap"] == "ok"  # минус-фраза «бесплатно» фраз не режет
    assert [(c["text"], c["shared"]) for c in build["callouts"]] == [("Гарантия 5 лет", True), ("Монтаж за день", True)]

    out = ai_agent.edit_board(user, chat, [
        {"op": "edit", "kind": "sitelink", "group": "", "text": "Цены", "to": "Цены на окна", "href": "shop.ru/prices",
         "description": "Прайс на все окна"},
        {"op": "add", "kind": "sitelink", "group": "", "text": "Отзывы", "href": "https://shop.ru/r"},
        {"op": "remove", "kind": "callout", "group": "", "text": "Гарантия 5 лет"},
        {"op": "add", "kind": "callout", "group": "", "text": "Рассрочка"},
    ])
    build = out["build"]
    assert [(l["text"], l["href"]) for l in build["sitelinks"]] == [
        ("Цены на окна", "https://shop.ru/prices"), ("Доставка", "https://shop.ru/d"), ("Отзывы", "https://shop.ru/r"),
    ]
    assert [c["text"] for c in build["callouts"]] == ["Монтаж за день", "Рассрочка"]
    # Правки легли в ту же ждущую таблицу: одно согласование доски, без отдельной записи правок.
    assert not [a for a in out["actions"] if a["tool"] == "board_edit"]
    rows = next(a for a in out["actions"] if a["id"] == extras["id"])["args"]["rows"]
    assert rows[0]["edited_from"] == "Цены — https://shop.ru/price — Прайс на все окна"
    assert [r["cells"][:2] for r in rows if r.get("added")] == [["Отзывы", "https://shop.ru/r"], ["Рассрочка", ""]]
    assert "поправил уточнения: «Гарантия 5 лет, Монтаж за день» → «Монтаж за день»" in build["held_edits"]

    # Чего Директ не примет — правка не проходит целиком.
    with pytest.raises(YandexError) as err:
        ai_agent.edit_board(user, chat, [
            {"op": "add", "kind": "callout", "group": "", "text": "Скидка"},
            {"op": "add", "kind": "sitelink", "group": "", "text": "Отзывы наших клиентов о монтаже", "href": "shop.ru/x"},
        ])
    assert "длиннее 30 знаков" in err.value.message
    assert [c["text"] for c in ai_agent.get_chat(user, chat)["build"]["callouts"]] == ["Монтаж за день", "Рассрочка"]

    # Риск модерации — предупреждение у строки и в «Готовности», согласовать можно.
    build = ai_agent.edit_board(user, chat, [{"op": "add", "kind": "callout", "group": "", "text": "Лучшие цены!!"}])["build"]
    rules = next(c for c in build["checks"] if c["key"] == "rules")
    assert rules["state"] == "warn" and "«Лучшие цены!!»" in rules["note"]
    assert build["callouts"][-1]["rule"]["label"] == "модерация"


def test_board_checks_follow_the_board(db, monkeypatch):
    """Галочки доски уходят на сервер: пределы, «Готовность» и снимок для ИИ считают те же строки."""
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _fake_direct(db, s, monkeypatch)
    links = [{"section": "Быстрые ссылки", "cells": [f"Раздел {i}", f"https://shop.ru/{i}", ""]} for i in range(1, 9)]
    _start(user, chat, "Собери кампанию на окна", monkeypatch,
           *_stages(_table("extras", links + [{"cells": ["Уточнения", "Лучшие цены!!"]}])))
    extras = next(t for t in _held(user, chat) if t["args"]["stage"] == "extras")
    table = str(extras["id"])
    add = {"op": "add", "kind": "sitelink", "group": "", "text": "Отзывы", "href": "shop.ru/r"}

    # Без галочек доски сервер считает все восемь ссылок — девятую не примет.
    with pytest.raises(YandexError) as err:
        ai_agent.edit_board(user, chat, [add])
    assert err.value.message == "Уже 8 быстрых ссылок — больше Директ не примет"

    # Снятая на доске ссылка освобождает место; снятое уточнение больше не тянет «Правила Директа».
    out = ai_agent.edit_board(user, chat, [{"op": "checks", "off": {table: [1, 9]}}, add])
    rows = next(a for a in out["actions"] if a["id"] == extras["id"])["args"]["rows"]
    assert [r["n"] for r in rows if r["checked"] is False] == [1, 9]
    assert rows[-1]["cells"][:2] == ["Отзывы", "https://shop.ru/r"] and rows[-1]["checked"] is True
    checks = {c["key"]: c["state"] for c in out["build"]["checks"]}
    assert checks["rules"] == "ok" and checks["extras"] == "ok"
    assert not [a for a in out["actions"] if a["tool"] == "board_edit"]

    # Одни галочки, без правок: уточнение вернули — предупреждение тоже вернулось.
    out = ai_agent.edit_board(user, chat, [{"op": "checks", "off": {table: [1]}}])
    rules = next(c for c in out["build"]["checks"] if c["key"] == "rules")
    assert rules["state"] == "warn" and "«Лучшие цены!!»" in rules["note"]
    with pytest.raises(YandexError):
        ai_agent.edit_board(user, chat, [{"op": "checks", "off": {table: "1"}}])
    rows = next(a for a in ai_agent.get_chat(user, chat)["actions"] if a["id"] == extras["id"])["args"]["rows"]
    assert [r["n"] for r in rows if r["checked"] is False] == [1]
