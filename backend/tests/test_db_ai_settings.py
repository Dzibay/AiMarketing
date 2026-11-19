"""Настройки черновика: регион, стратегия и бюджет — как согласовано; без региона черновик не создаётся."""

import copy

from app import ai_agent
from tests.test_db_ai import _call, _secret, _seed, _start, _text, _turn  # noqa: F401 — _secret: автофикстура
from tests.test_db_ai_board import ANALYSIS, BRIEF, _fake_direct
from tests.test_db_ai_build_memory import ADS, GOALS, KEYWORDS, _reply, _spec, _table

KAZAN = [
    {"kind": "setting", "cells": ["Регион", "Казань"]},
    {"kind": "setting", "cells": ["Бюджет", "500 ₽/день"]},
]


def _stages(settings):
    return (
        _table("goals", GOALS), _table("analysis", ANALYSIS), _table("keywords", KEYWORDS), _table("ads", ADS),
        _table("settings", settings, columns=["Настройка", "Значение"]),
        _text("Кампания на доске — проверьте и согласуйте."),
    )


def _settings_check(user, chat):
    build = ai_agent.get_chat(user, chat)["build"]
    return next(c for c in build["checks"] if c["key"] == "settings"), build["ready"]


def _with_regions(s, region_ids):
    spec = copy.deepcopy(_spec(s))
    for group in spec["spec"]["groups"]:
        group["region_ids"] = region_ids
    return spec


def test_draft_follows_agreed_region(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _row, created = _fake_direct(db, s, monkeypatch)
    _start(user, chat, BRIEF, monkeypatch, *_stages(KAZAN))
    # Бриф просил Москву, а в настройках Казань — «Готовность» предупреждает, но согласовать можно.
    check, ready = _settings_check(user, chat)
    assert (check["state"], check["note"]) == ("warn", "в брифе «Москва», в настройках «Казань»")
    assert ready is True
    ai_agent.decide_build(user, chat)

    fake, _out = _turn(
        user, chat, monkeypatch,
        _call("create_campaign", _with_regions(s, []), "none"),
        _call("create_campaign", _with_regions(s, [225]), "russia"),
        _call("create_campaign", _with_regions(s, [43]), "kazan"),
        _text("Черновик создан."),
    )
    assert _reply(fake, 1)["error"].startswith("Группа «Окна ПВХ»: укажи region_ids")
    assert _reply(fake, 2)["error"].startswith(
        "Черновик расходится с согласованными настройками — регион: в настройках «Казань», в черновике «Россия»"
    )
    assert _reply(fake, 3)["ok"] is True
    [kw] = created
    assert kw["spec"]["groups"][0]["region_ids"] == [43]


def test_board_without_region_is_not_ready(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _start(user, chat, BRIEF, monkeypatch, *_stages([{"kind": "setting", "cells": ["Бюджет", "500 ₽/день"]}]))
    check, ready = _settings_check(user, chat)
    assert check["state"] == "bad" and check["note"].startswith("не указан регион") and ready is False
