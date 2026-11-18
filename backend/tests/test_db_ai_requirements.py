"""Требования ТЗ: фиксируются первой таблицей сборки, видны на доске и не пускают черновик, пока не выполнены."""

import copy

from app import ai_agent
from tests.test_db_ai import _call, _secret, _seed, _start, _text, _turn  # noqa: F401 — _secret: автофикстура
from tests.test_db_ai_board import ANALYSIS, BRIEF, _fake_direct, _held
from tests.test_db_ai_build_memory import ADS, GOALS, KEYWORDS, SETTINGS, _reply, _spec, _table

REQ = {"titles": 2, "banned_words": ["бесплатн"], "other": ["Только Москва"]}


def _stages(req=REQ):
    return (
        _table("goals", GOALS, requirements=req), _table("analysis", ANALYSIS), _table("keywords", KEYWORDS),
        _table("ads", ADS), _table("settings", SETTINGS, columns=["Настройка", "Значение"]),
        _text("Кампания на доске — проверьте и согласуйте."),
    )


def _checks(user, chat):
    return {c["key"]: c for c in ai_agent.get_chat(user, chat)["build"]["checks"] if c["key"].startswith("req_")}


def test_first_table_must_fix_requirements(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    bare = _call("propose_items", {"stage": "goals", "rows": GOALS}, "bare")
    fake, _out = _start(user, chat, BRIEF, monkeypatch, bare, _table("goals", GOALS), _text("Цели на доске."))
    assert "Первая таблица сборки фиксирует требования ТЗ" in _reply(fake, 1)["error"]
    assert _reply(fake, 2)["on_board"] is True
    # Пустые требования — ТЗ без цифр и запретов: проверок ТЗ на доске нет.
    assert _checks(user, chat) == {}


def test_requirements_block_the_draft_until_met(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _row, created = _fake_direct(db, s, monkeypatch)

    fake, _out = _start(user, chat, BRIEF, monkeypatch, *_stages())
    # Сводка помнит требования и что на доске их пока не хватает.
    assert "Требования ТЗ (сервис считает их сам" in fake.notes[5] and "Только Москва" in fake.notes[5]
    assert "На доске пока не выполнено: в каждой группе не меньше 2 заголовков — не хватает: «Окна ПВХ» — 1" in fake.notes[5]
    checks = _checks(user, chat)
    assert checks["req_titles"]["state"] == "bad" and checks["req_banned"]["state"] == "ok"
    build = ai_agent.get_chat(user, chat)["build"]
    assert build["requirements"] == REQ and build["ready"] is False
    # Проверки ТЗ — сразу под этапами.
    assert [c["key"] for c in build["checks"]][:3] == ["stages", "req_titles", "req_banned"]

    ai_agent.decide_build(user, chat)
    fake, _out = _turn(
        user, chat, monkeypatch,
        _call("get_build", {}, "look"),
        _call("create_campaign", _spec(s), "short"),
        _text("Не хватает заголовка — дополню."),
    )
    assert _reply(fake, 1)["build"]["requirements_unmet"] == [
        "в каждой группе не меньше 2 заголовков — не хватает: «Окна ПВХ» — 1"
    ]
    short = _reply(fake, 2)["error"]
    assert short.startswith("Черновик не создан: не выполнены требования ТЗ") and "«Окна ПВХ» — 1" in short
    assert not created

    # Дополненный раздел ложится на доску; маркетолог согласовал его, не заметив запрещённого слова.
    def titles(second):
        return [
            {"section": "Окна ПВХ", "kind": "title", "cells": ["Окна от завода"]},
            {"section": "Окна ПВХ", "kind": "title", "cells": [second]},
        ]

    _start(user, chat, "Добавь заголовок", monkeypatch, _table("ads", titles("Бесплатный замер окон"), revise=True),
           _text("Добавил."))
    checks = _checks(user, chat)
    assert checks["req_titles"]["state"] == "ok" and checks["req_banned"]["state"] == "bad"
    ai_agent.decide_build(user, chat)
    banned = copy.deepcopy(_spec(s))
    banned["spec"]["groups"][0]["ad"]["titles"] = ["Окна от завода", "Бесплатный замер окон"]
    fake, _out = _turn(user, chat, monkeypatch, _call("create_campaign", banned, "banned"), _text("Уберу слово."))
    # Минус-фраза «бесплатно» — не нарушение: запрет касается текстов объявления.
    error = _reply(fake, 1)["error"]
    assert "без слов «бесплатн» — найдено: «бесплатн» в «Бесплатный замер окон»" in error and not created

    # Согласованный заголовок с запрещённым словом убирается строкой без галочки.
    fixed = [*titles("Окна за 1 день"), {"section": "Окна ПВХ", "kind": "title", "cells": ["Бесплатный замер окон"],
                                         "checked": False}]
    _start(user, chat, "Перепиши", monkeypatch, _table("ads", fixed, revise=True), _text("Готово."))
    assert _held(user, chat) and all(c["state"] == "ok" for c in _checks(user, chat).values())
    ai_agent.decide_build(user, chat)
    good = copy.deepcopy(_spec(s))
    good["spec"]["groups"][0]["ad"]["titles"] = ["Окна от завода", "Окна за 1 день"]
    fake, out = _turn(user, chat, monkeypatch, _call("create_campaign", good, "go"), _text("Черновик создан."))
    assert _reply(fake, 1)["ok"] is True and out["status"] == "idle"
    [kw] = created
    assert kw["spec"]["groups"][0]["ad"]["titles"] == ["Окна от завода", "Окна за 1 день"]


def test_marketer_can_change_requirements(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _start(user, chat, BRIEF, monkeypatch, *_stages())
    assert _checks(user, chat)["req_titles"]["state"] == "bad"
    # Маркетолог передумал: хватит одного заголовка — новая таблица этапа с requirements.
    goals = _table("goals", GOALS, requirements={"titles": 1}, revise=True)
    _start(user, chat, "Хватит одного заголовка", monkeypatch, goals, _text("Требования обновил."))
    checks = _checks(user, chat)
    assert set(checks) == {"req_titles"} and checks["req_titles"]["state"] == "ok"
