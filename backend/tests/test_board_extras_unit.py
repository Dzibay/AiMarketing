"""Быстрые ссылки и уточнения на доске сборки: правки, пределы Директа, ждущая таблица и карточка создания."""

import pytest

from app import ai_agent
from app.yandex import YandexError
from tests.test_ai_agent_unit import _done_campaign, _edits, _table


def _extras(status="done"):
    return _table(4, "extras", [
        {"section": "Быстрые ссылки", "cells": ["Цены", "https://okna.ru/price", "Прайс на все окна"]},
        {"section": "Быстрые ссылки", "cells": ["Доставка", "https://okna.ru/d", ""]},
        {"cells": ["Уточнения", "Гарантия 5 лет, Монтаж за день"]},
    ], status=status)


def _build(*ops, status="done"):
    actions = _done_campaign() + [_extras(status)]
    return ai_agent.campaign_build(actions + ([_edits(5, *ops)] if ops else []))


def _op(**raw):
    return ai_agent._board_op({"group": "", **raw})


def _error(build, **raw):
    with pytest.raises(YandexError) as err:
        ai_agent._board_validate(build, _op(**raw))
    return err.value.message


def test_board_op_links():
    op = ai_agent._board_op({"op": "add", "kind": "sitelink", "group": "Окна", "text": " Отзывы ",
                             "href": " okna.ru/r ", "description": "Что говорят"})
    assert op == {"op": "add", "group": "", "kind": "sitelink", "href": "okna.ru/r", "description": "Что говорят",
                  "text": "Отзывы"}
    # Адрес и описание не передали — остаются как были; у уточнения их нет вовсе.
    assert "href" not in _op(op="edit", kind="sitelink", text="Цены", to="Цены на окна")
    assert _op(op="add", kind="callout", text="Рассрочка", href="x") == {
        "op": "add", "group": "", "kind": "callout", "text": "Рассрочка",
    }
    with pytest.raises(YandexError):
        _op(op="move", kind="sitelink", text="Цены", to_group="Окна")


def test_board_links_validate():
    build = _build()
    assert "нужен адрес страницы" in _error(build, op="add", kind="sitelink", text="Отзывы")
    assert "не похоже на адрес страницы" in _error(build, op="add", kind="sitelink", text="Отзывы", href="отзывы")
    assert "длиннее 30 знаков" in _error(build, op="add", kind="sitelink", text="Отзывы наших клиентов о нашей работе",
                                         href="okna.ru/r")
    assert _error(build, op="add", kind="sitelink", text="Отзывы", href="okna.ru/r", description="д" * 61) == (
        f"Описание «{'д' * 61}»: длиннее 60 знаков (61) — Директ так не примет"
    )
    assert "эмодзи" in _error(build, op="add", kind="callout", text="Гарантия 🔥")
    assert "длиннее 25 знаков" in _error(build, op="add", kind="callout", text="Гарантия на всё пять лет!!")
    assert "«цены» уже есть" in _error(build, op="add", kind="sitelink", text="цены", href="okna.ru/p")
    assert "«Монтаж за день» уже есть" in _error(build, op="add", kind="callout", text="Монтаж за день")
    assert _error(build, op="edit", kind="sitelink", text="Цены", to="Цены", href="https://okna.ru/price") == (
        "Ничего не изменилось"
    )
    assert "«Доставка» уже есть" in _error(build, op="edit", kind="sitelink", text="Цены", to="Доставка")
    assert "нужен адрес страницы" in _error(build, op="edit", kind="sitelink", text="Цены", to="Цены", href="")
    assert "уже нет на доске" in _error(build, op="remove", kind="callout", text="Нет такого")

    # Адрес без протокола дополняется https://; поменять одно описание — тоже правка.
    op = _op(op="add", kind="sitelink", text="Отзывы", href="okna.ru/reviews")
    assert ai_agent._board_validate(build, op) is None and op["href"] == "https://okna.ru/reviews"
    op = _op(op="edit", kind="sitelink", text="Цены", to="Цены", description="")
    assert ai_agent._board_validate(build, op)["text"] == "Цены"


def test_board_links_limits():
    build = _build(*[
        {"op": "add", "kind": "sitelink", "group": "", "text": f"Ссылка {i}", "href": f"https://okna.ru/{i}"}
        for i in range(6)
    ], *[{"op": "add", "kind": "callout", "group": "", "text": f"Уточнение {i}"} for i in range(48)])
    assert len(build["sitelinks"]) == 8 and len(build["callouts"]) == 50
    assert _error(build, op="add", kind="sitelink", text="Ещё", href="okna.ru/x") == (
        "Уже 8 быстрых ссылок — больше Директ не примет"
    )
    assert _error(build, op="add", kind="callout", text="Ещё") == "Уже 50 уточнений — больше Директ не примет"
    # Снятая галочка освобождает место.
    unchecked = _build(*[
        {"op": "add", "kind": "sitelink", "group": "", "text": f"Ссылка {i}", "href": f"https://okna.ru/{i}"}
        for i in range(6)
    ])
    unchecked["sitelinks"][0]["checked"] = False
    assert ai_agent._board_validate(unchecked, _op(op="add", kind="sitelink", text="Ещё", href="okna.ru/x")) is None


def test_campaign_build_applies_link_edits():
    build = _build(
        {"op": "add", "kind": "sitelink", "group": "", "text": "Отзывы", "href": "https://okna.ru/r",
         "description": "Что говорят"},
        {"op": "edit", "kind": "sitelink", "group": "", "text": "Цены", "to": "Цены на окна", "href": "https://okna.ru/p2"},
        {"op": "remove", "kind": "sitelink", "group": "", "text": "Доставка"},
        {"op": "add", "kind": "callout", "group": "", "text": "Рассрочка"},
        {"op": "remove", "kind": "callout", "group": "", "text": "Гарантия 5 лет"},
        {"op": "edit", "kind": "callout", "group": "", "text": "Монтаж за день", "to": "Монтаж за 1 день"},
    )
    assert [(s["text"], s["href"], s["description"], s["own"]) for s in build["sitelinks"]] == [
        ("Цены на окна", "https://okna.ru/p2", "Прайс на все окна", True),
        ("Отзывы", "https://okna.ru/r", "Что говорят", True),
    ]
    assert [(c["text"], c["own"]) for c in build["callouts"]] == [("Монтаж за 1 день", True), ("Рассрочка", True)]


def test_campaign_build_marks_shared_links():
    build = _build()
    # Уточнения из одной строки «A, B»: галочка снимет оба — убирают их правкой.
    assert [(c["text"], c["shared"]) for c in build["callouts"]] == [("Гарантия 5 лет", True), ("Монтаж за день", True)]
    assert [s["shared"] for s in build["sitelinks"]] == [False, False]


def test_board_links_rules_check():
    build = _build({"op": "add", "kind": "callout", "group": "", "text": "САМЫЕ НИЗКИЕ ЦЕНЫ"})
    checks = {c["key"]: c for c in build["checks"]}
    assert checks["rules"]["state"] == "warn"
    assert build["callouts"][-1]["rule"]["label"] == "модерация"


def test_board_note_links():
    note = ai_agent._board_note
    assert note(_op(op="add", kind="sitelink", text="Отзывы", href="https://okna.ru/r", description="Что говорят")) == (
        "добавил быструю ссылку «Отзывы» (адрес https://okna.ru/r, описание «Что говорят»)"
    )
    assert note(_op(op="edit", kind="sitelink", text="Цены", to="Цены на окна")) == (
        "поправил быструю ссылку: «Цены» → «Цены на окна»"
    )
    assert note(_op(op="edit", kind="sitelink", text="Цены", to="Цены", description="")) == (
        "поправил быструю ссылку «Цены» (без описания)"
    )
    assert note(_op(op="remove", kind="callout", text="Рассрочка")) == "убрал уточнение «Рассрочка»"


def test_link_patches_pending_table():
    table = _extras(status="pending")
    rows = table["args"]["rows"]
    build = _build(status="pending")
    price, delivery = build["sitelinks"]
    assert price["pending"] and price["action_id"] == 4

    op = _op(op="edit", kind="sitelink", text="Цены", to="Цены на окна", href="okna.ru/p2")
    ai_agent._board_validate(build, op)
    assert ai_agent._table_row_patch(table, op, price, build)
    assert rows[0]["cells"] == ["Цены на окна", "https://okna.ru/p2", "Прайс на все окна"]
    assert rows[0]["edited_from"] == "Цены — https://okna.ru/price — Прайс на все окна"
    assert rows[0]["links"] == [{"text": "Цены на окна", "href": "https://okna.ru/p2", "description": "Прайс на все окна"}]

    # Предложенную ИИ одиночную ссылку снимают галочкой.
    with pytest.raises(YandexError) as err:
        ai_agent._table_row_patch(table, _op(op="remove", kind="sitelink", text="Доставка"), delivery, build)
    assert "снимите с неё галочку" in err.value.message

    # Новая ссылка — строкой в том же разделе; свою можно убрать.
    add = _op(op="add", kind="sitelink", text="Отзывы", href="https://okna.ru/r")
    assert ai_agent._table_row_patch(table, add, None, build)
    assert rows[3] == {
        "n": 4, "section": "Быстрые ссылки", "kind": "extra", "cells": ["Отзывы", "https://okna.ru/r", ""],
        "checked": True, "added": True, "links": [{"text": "Отзывы", "href": "https://okna.ru/r", "description": ""}],
        "link_kind": "sitelink",
    }
    ai_agent._table_row_patch(table, _op(op="remove", kind="sitelink", text="Отзывы"), {"n": 4}, build)
    assert len(rows) == 3

    # Из общей строки «A, B» уточнение убирается правкой строки.
    callout = build["callouts"][0]
    ai_agent._table_row_patch(table, _op(op="remove", kind="callout", text="Гарантия 5 лет"), callout, build)
    assert rows[2]["cells"] == ["Уточнения", "Монтаж за день"] and rows[2]["edited_from"] == "Гарантия 5 лет, Монтаж за день"
    ai_agent._table_row_patch(
        table, _op(op="edit", kind="callout", text="Монтаж за день", to="Монтаж за 1 день"), callout, build
    )
    assert rows[2]["cells"] == ["Уточнения", "Монтаж за 1 день"]
    assert rows[2]["edited_from"] == "Гарантия 5 лет, Монтаж за день"  # первоначальный вид — один раз

    # Доска после правок таблицы видит их через row["links"].
    rebuilt = ai_agent.campaign_build(_done_campaign() + [table])
    assert [s["text"] for s in rebuilt["sitelinks"]] == ["Цены на окна", "Доставка"]
    assert [c["text"] for c in rebuilt["callouts"]] == ["Монтаж за 1 день"]
    assert ai_agent._edited_rows(rows) == [
        "поправил быстрые ссылки: «Цены — https://okna.ru/price — Прайс на все окна» → "
        "«Цены на окна — https://okna.ru/p2 — Прайс на все окна»",
        "поправил уточнения: «Гарантия 5 лет, Монтаж за день» → «Монтаж за 1 день»",
    ]


def test_link_patch_missing_row():
    table = _extras(status="pending")
    with pytest.raises(YandexError) as err:
        ai_agent._table_row_patch(table, _op(op="remove", kind="callout", text="Нет такого"), {"n": 3}, {})
    assert "обновите страницу" in err.value.message


def test_spec_patch_links():
    spec = {"groups": [
        {"name": "Окна", "ad": {"title": "Окна", "extras": {
            "sitelinks": [{"title": "Цены", "href": "https://okna.ru/p", "description": "Прайс"}],
            "callouts": ["Гарантия"],
        }}},
        {"name": "Балконы", "ad": {"title": "Балконы"}},
        # Расширения другого объявления — своего набора нет, новые ссылки сюда не добавляются.
        {"name": "Двери", "ad": {"title": "Двери", "extras_from_ad_row_id": 15}},
    ]}
    ai_agent._spec_patch(spec, _op(op="add", kind="sitelink", text="Отзывы", href="https://okna.ru/r"))
    ai_agent._spec_patch(spec, _op(op="add", kind="callout", text="Рассрочка"))
    ai_agent._spec_patch(spec, _op(op="edit", kind="sitelink", text="цены", to="Цены на окна", description=""))
    ai_agent._spec_patch(spec, _op(op="remove", kind="callout", text="Гарантия"))
    okna, balcony, doors = (g["ad"] for g in spec["groups"])
    assert okna["extras"] == {
        "sitelinks": [{"title": "Цены на окна", "href": "https://okna.ru/p"}, {"title": "Отзывы", "href": "https://okna.ru/r"}],
        "callouts": ["Рассрочка"],
    }
    assert balcony["extras"] == {"sitelinks": [{"title": "Отзывы", "href": "https://okna.ru/r"}], "callouts": ["Рассрочка"]}
    assert "extras" not in doors
    ai_agent._spec_patch(spec, _op(op="remove", kind="callout", text="Рассрочка"))
    assert balcony["extras"] == {"sitelinks": [{"title": "Отзывы", "href": "https://okna.ru/r"}]}


def test_spec_board_links_validate():
    """Без таблицы расширений ссылки доски — из карточки создания, без повторов по группам."""
    create = {
        "id": 9, "tool": "create_campaign", "status": "pending", "summary": "Окна", "result": None,
        "args": {"spec": {"name": "Окна", "groups": [
            {"name": "Окна", "keywords": ["купить окна"], "ad": {"title": "Окна", "text": "Замер", "extras": {
                "sitelinks": [{"title": "Цены", "href": "https://okna.ru/p"}], "callouts": ["Гарантия"]}}},
            {"name": "Балконы", "keywords": ["балкон"], "ad": {"title": "Балконы", "text": "Монтаж", "extras": {
                "sitelinks": [{"title": "Цены", "href": "https://okna.ru/p"}], "callouts": ["Гарантия"]}}},
        ]}},
    }
    build = ai_agent.campaign_build(_done_campaign() + [create])
    assert [s["text"] for s in ai_agent._board_links(build, "sitelink")] == ["Цены"]
    assert ai_agent._board_validate(build, _op(op="remove", kind="callout", text="Гарантия"))["text"] == "Гарантия"
    assert "уже есть" in _error(build, op="add", kind="sitelink", text="Цены", href="okna.ru/p")


def test_board_link_href_scheme_case():
    # «Https://» ставит клавиатура телефона: схема — строчными, иначе при создании выйдет https://Https://…
    for raw, href in (("Https://okna.ru/otzyvy", "https://okna.ru/otzyvy"), ("HTTP://okna.ru/Otzyvy", "http://okna.ru/Otzyvy")):
        op = _op(op="add", kind="sitelink", text="Отзывы", href=raw)
        assert ai_agent._board_validate(_build(), op) is None and op["href"] == href


def _pending_extras(sitelinks, callouts=(), off=()):
    rows = [{"section": "Быстрые ссылки", "cells": [text, href, ""]} for text, href in sitelinks]
    rows += [{"cells": ["Уточнения", text]} for text in callouts]
    table = _table(4, "extras", rows, status="pending")
    for row in table["args"]["rows"]:
        row["checked"] = row["n"] not in off
    return ai_agent.campaign_build(_done_campaign() + [table])


def test_board_extras_over_limit_and_no_href_are_red():
    links = [(f"Ссылка {i}", f"https://okna.ru/{i}") for i in range(9)]
    build = _pending_extras(links, [f"Уточнение {i}" for i in range(51)])
    checks = {c["key"]: c for c in build["checks"]}
    assert checks["extras"]["state"] == "bad" and not build["ready"]
    assert checks["extras"]["note"] == "больше 8 быстрых ссылок и больше 50 уточнений — снимите лишние"
    # Снятые галочки — в пределах: проверка зелёная.
    build = _pending_extras(links, [f"Уточнение {i}" for i in range(51)], off=(1, 10))
    assert {c["key"]: c["state"] for c in build["checks"]}["extras"] == "ok"
    # Быстрая ссылка без адреса — Директ не примет.
    build = _pending_extras([("Цены", "https://okna.ru/p"), ("Отзывы", "")])
    rules = {c["key"]: c for c in build["checks"]}["rules"]
    assert rules["state"] == "bad" and "«Отзывы» — нет адреса страницы" in rules["note"]
    assert build["sitelinks"][1]["rule"]["note"] == "нет адреса страницы"


def test_board_checks_op():
    assert ai_agent._board_off({"op": "checks", "off": {"4": [1, "3"], 7: []}}) == {4: {1, 3}, 7: set()}
    for bad in ({}, {"off": []}, {"off": {"4": 1}}, {"off": {"x": [1]}}, {"off": {"4": ["a"]}},
                {"off": {"4": list(range(ai_agent.MAX_BOARD_OFF + 1))}}):
        with pytest.raises(YandexError):
            ai_agent._board_off(bad)
    table = _extras(status="pending")
    assert ai_agent._board_set_checks(table, {2, 9}) is True
    assert [r["checked"] for r in table["args"]["rows"]] == [True, False, True]
    assert ai_agent._board_set_checks(table, {2}) is False
    assert ai_agent._board_set_checks(table, set()) is True and all(r["checked"] for r in table["args"]["rows"])
    # Решённую таблицу галочки не трогают.
    done = _extras()
    assert ai_agent._board_set_checks(done, {1}) is False and done["args"]["rows"][0]["checked"] is True


def test_row_same_keeps_board_unchecks():
    old = {"section": "Окна", "kind": "", "cells": ["купить окна"], "checked": False}
    # Та же строка от ИИ не возвращает галочку, снятую на доске; снятая самим ИИ — уже другая строка.
    assert ai_agent._row_same({**old, "checked": True}, old)
    assert not ai_agent._row_same(old, {**old, "checked": True})


def test_snapshot_rules_for_ai():
    build = _pending_extras([("Цены", "https://okna.ru/p"), ("Отзывы", "")], ["Лучшие цены!!"])
    lines = ai_agent._snapshot_rules(build, ai_agent._on_board_items)
    assert lines == [
        "быстрая ссылка «Отзывы»: Директ не примет — нет адреса страницы",
        "уточнение «Лучшие цены!!»: модерация — «Лучшие» — модерация попросит доказательства; больше одного «!»",
    ]
    # Снятое на доске ИИ не показываем; длинный список — с хвостом «и ещё N».
    build = _pending_extras([("Цены", "https://okna.ru/p"), ("Отзывы", "")], ["Лучшие цены!!"], off=(2, 3))
    assert ai_agent._snapshot_rules(build, ai_agent._on_board_items) == []
    build = _pending_extras([(f"Ссылка {i}", "") for i in range(8)], [f"Лучшее {i}" for i in range(10)])
    lines = ai_agent._snapshot_rules(build, ai_agent._on_board_items)
    assert len(lines) == ai_agent.MAX_SNAPSHOT_RULES + 1 and lines[-1] == "и ещё 3"
    assert all("Директ не примет" in line for line in lines[:8])
