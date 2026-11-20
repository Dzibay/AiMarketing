"""Бриф формой (docs/board-plan.md, пункт 6): ИИ присылает бриф сразу с тем, что знает, маркетолог дописывает форму
на доске; без кабинета, названия, регионов и ссылки «Дальше» не пройдёт; «ИИ заполнит по сайту» — один лёгкий
вызов модели, форму он не сохраняет."""

import json

import pytest

from app import ai, ai_agent, billing, brief, geo
from app.yandex import YandexError
from tests.test_db_ai import FakeModel, _seed, _start, _text, _turn
from tests.test_db_ai import _secret  # noqa: F401 — автофикстура
from tests.test_db_ai_build_memory import _approve, _pending
from tests.test_db_ai_build_sync import FakeDirect, _wizard

ROWS = [
    {"cells": ["Что рекламируем", "Окна ПВХ"]},
    {"cells": ["Гео", "Москва"]},
    {"cells": ["УТП", "Замер бесплатно"]},
]
FORM = {
    "name": "Окна — поиск",
    "href": "https://shop.ru/okna",
    "product": "Окна ПВХ с монтажом",
    "region_ids": [213],
    "weekly_budget_rub": 7000,
    "banned_words": "дешев, бесплатно",
    "titles": 5,
    "other": "Без цен в заголовках",
}


def _brief(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", ROWS, campaign={}))
    return user, chat


def test_incomplete_brief_is_a_form_and_blocks_next(db, monkeypatch):
    user, chat = _brief(db, monkeypatch)
    form = ai_agent.get_chat(user, chat)["build"]["brief"]
    assert form["missing"] == ["название кампании", "регион", "посадочная ссылка"]
    # Кабинет у проекта один — выбран сам; регион словами от ИИ — подсказкой.
    assert form["form"]["account_id"] and form["form"]["product"] == "Окна ПВХ"
    assert form["region_text"] == "Москва"
    assert form["extra"] == [{"text": "УТП", "value": "Замер бесплатно"}]
    table = _pending(user, chat, "propose_items")
    with pytest.raises(YandexError) as err:
        ai_agent.decide_items(user, table["id"], rows=[r["n"] for r in table["args"]["rows"]])
    assert "Заполните в брифе: название кампании, регион, посадочная ссылка" in err.value.message


def test_save_fills_the_brief_table_and_next_creates_the_draft(db, monkeypatch):
    user, chat = _brief(db, monkeypatch)
    out = ai_agent.save_brief(user, chat, FORM)
    form = out["build"]["brief"]
    assert form["missing"] == []
    assert form["form"]["weekly_budget_rub"] == 7000 and form["form"]["banned_words"] == ["дешев", "бесплатно"]
    args = _pending(user, chat, "propose_items")["args"]
    camp = args["campaign"]
    assert camp["name"] == "Окна — поиск" and camp["region_ids"] == [213] and camp["href"] == "https://shop.ru/okna"
    assert camp["strategy"] == {"network": "SERVING_OFF", "search": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 7000}
    assert args["requirements"] == {"titles": 5, "banned_words": ["дешев", "бесплатно"], "other": ["Без цен в заголовках"]}
    rows = [(r["n"], r["cells"][0], r["cells"][1]) for r in args["rows"]]
    # Строки ИИ тех же полей заменены формой на своих местах, УТП осталось.
    assert rows[:3] == [(1, "Что рекламируем", "Окна ПВХ с монтажом"), (2, "Регион", "Москва"), (3, "УТП", "Замер бесплатно")]
    assert [r[1] for r in rows[3:]] == ["Кампания", "Бюджет в неделю", "Посадочная ссылка", "Ограничения"]
    _approve(user, chat)
    build = ai_agent.get_chat(user, chat)["build"]
    assert "brief" not in build
    assert build["campaign"]["name"] == "Окна — поиск"
    # Бриф согласован — форма больше не сохраняется.
    _turn(user, chat, monkeypatch, _text("Черновик создан."))
    with pytest.raises(YandexError) as err:
        ai_agent.save_brief(user, chat, FORM)
    assert err.value.code == "decided"


def test_save_rejects_bad_values(db, monkeypatch):
    user, chat = _brief(db, monkeypatch)
    before = _pending(user, chat, "propose_items")["args"]
    for form, message in (
        ({**FORM, "href": "shop.ru"}, "campaign.href"),
        ({**FORM, "weekly_budget_rub": "много"}, "Бюджет в неделю — число"),
        ({**FORM, "titles": 9}, "requirements.titles"),
    ):
        with pytest.raises(YandexError) as err:
            ai_agent.save_brief(user, chat, form)
        assert message in err.value.message
    # Ничего не сохранилось.
    assert _pending(user, chat, "propose_items")["args"] == before


def test_fill_from_site_is_one_light_call_and_saves_nothing(db, monkeypatch):
    user, chat = _brief(db, monkeypatch)
    before = _pending(user, chat, "propose_items")["args"]
    pages = []

    def read_page(url, limit):
        pages.append((url, limit))
        return {"url": "https://shop.ru/", "title": "Окна в Казани", "description": "", "headings": ["Окна ПВХ"],
                "prices": ["от 9 900 ₽"], "text": "Пластиковые окна с установкой в Казани"}

    monkeypatch.setattr(brief, "read_page", read_page)
    monkeypatch.setattr(
        geo, "search_regions",
        lambda user_id, q, limit=10: {"regions": [{"id": 43, "name": "Казань", "path": "Россия"}] if "Казан" in q else []},
    )
    answer = {"name": "Поиск — Окна ПВХ — Казань", "product": "Пластиковые окна с установкой", "regions": ["Казань", "Марс"],
              "weekly_budget_rub": None, "banned_words": [], "other": []}
    fake = FakeModel({"content": json.dumps(answer, ensure_ascii=False), "tool_calls": []})
    monkeypatch.setattr(ai, "complete", fake)
    out = ai_agent.fill_brief(user, chat, "https://shop.ru")
    assert out["form"] == {
        "href": "https://shop.ru/", "name": "Поиск — Окна ПВХ — Казань", "product": "Пластиковые окна с установкой",
        "region_ids": [43],
    }
    assert out["regions"] == [{"id": 43, "name": "Казань", "path": "Россия"}]
    assert pages == [("https://shop.ru", ai_agent.BRIEF_FILL_PAGE)]
    assert len(fake.calls) == 1 and fake.cfgs[0]["task"] == "board_brief"
    assert [m["role"] for m in fake.calls[0]] == ["system", "user"]
    assert "Пластиковые окна с установкой в Казани" in fake.calls[0][1]["content"]
    assert _pending(user, chat, "propose_items")["args"] == before


def test_fill_needs_budget_and_open_brief(db, monkeypatch):
    user, chat = _brief(db, monkeypatch)
    fake = FakeModel()
    monkeypatch.setattr(ai, "complete", fake)
    monkeypatch.setattr(brief, "read_page", lambda url, limit: pytest.fail("сайт не читаем без лимита"))

    def exhausted(user_id, need=0.0):
        raise YandexError("units-exhausted", "Лимит исчерпан")

    monkeypatch.setattr(billing, "check", exhausted)
    with pytest.raises(YandexError) as err:
        ai_agent.fill_brief(user, chat, "https://shop.ru")
    assert err.value.code == "units-exhausted"
    assert fake.calls == []
