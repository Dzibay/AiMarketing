"""Чистые данные доски (пункт 1 docs/board-plan.md): подписи вместо текста, «/ Объявление N», группа
вместо частотности, пределы 7 заголовков и 3 текстов — без БД."""

import pytest

from app import ai_agent
from app.yandex import YandexError
from tests.test_ai_items_unit import FakeContext


def _table(action_id, stage, rows, status="done", approved=None):
    rows = [{"n": i + 1, "section": "", "kind": "", "checked": True, **r} for i, r in enumerate(rows)]
    result = {"approved_rows": approved if approved is not None else [r["n"] for r in rows]} if status == "done" else None
    return {"id": action_id, "tool": "propose_items", "status": status, "args": {"stage": stage, "rows": rows}, "result": result}


def _goals_and_keywords():
    return [
        _table(1, "goals", [{"cells": ["Что", "Станки"]}]),
        _table(2, "keywords", [
            {"section": "Станки", "cells": ["станок чпу купить", "Станки"]},
            {"section": "Металлопрокат", "cells": ["металлопрокат купить", "57"]},
        ]),
    ]


@pytest.mark.parametrize("text", ["Заголовок 1", "текст 3", "Быстрая ссылка 2", "Уточнение 10.", "Заголовок №4"])
def test_items_table_refuses_placeholders(text):
    with pytest.raises(YandexError) as err:
        ai_agent._q_items(FakeContext(), {"stage": "ads", "rows": [
            {"section": "Станки", "kind": "title", "cells": [text, "Станки с ЧПУ в наличии"]},
        ]})
    assert "подпись вместо текста" in err.value.message and f"«{text}»" in err.value.message


def test_items_table_keeps_real_texts_with_numbers():
    ok = ai_agent._q_items(FakeContext(), {"stage": "ads", "rows": [
        {"section": "Станки", "kind": "title", "cells": ["Заголовок 1 в выдаче за 24 часа"]},
        {"section": "Станки", "kind": "text", "cells": ["Текст 2026 года"]},
    ]})
    assert len(ok["rows"]) == 2
    # Цели и настройки — «подпись: значение», там «Текст 1» не проверяется.
    goals = ai_agent._q_items(FakeContext(), {"stage": "goals", "rows": [{"cells": ["Текст 1", "из ТЗ"]}]})
    assert goals["rows"][0]["cells"][0] == "Текст 1"


def test_items_table_drops_group_name_from_frequency():
    ok = ai_agent._q_items(FakeContext(), {"stage": "keywords", "rows": [
        {"section": "Производство — станки", "cells": ["станок чпу купить", "Производство — станки"]},
        {"section": "Производство — станки", "cells": ["токарный станок", "423"]},
    ]})
    assert [r["cells"][1] for r in ok["rows"]] == ["", "423"]


def test_board_hides_placeholders_and_group_as_frequency():
    build = ai_agent.campaign_build(_goals_and_keywords() + [
        _table(3, "ads", [
            {"section": "Станки", "kind": "title", "cells": ["Станки с ЧПУ"]},
            {"section": "Станки", "kind": "title", "cells": ["Заголовок 1", "Станки с ЧПУ"]},
            {"section": "Станки", "kind": "text", "cells": ["Текст 1"]},
            {"section": "Станки", "kind": "text", "cells": ["Поставим станок за неделю"]},
        ]),
    ])
    machines = next(g for g in build["groups"] if g["name"] == "Станки")
    assert [t["text"] for t in machines["titles"]] == ["Станки с ЧПУ"]
    assert [t["text"] for t in machines["texts"]] == ["Поставим станок за неделю"]
    assert [k["value"] for k in machines["keywords"]] == [""]


def test_board_merges_second_ad_into_its_group():
    build = ai_agent.campaign_build(_goals_and_keywords() + [
        _table(3, "ads", [
            {"section": "Станки", "kind": "title", "cells": ["Станки с ЧПУ"]},
            {"section": "Станки / Объявление 2", "kind": "title", "cells": ["Токарные станки"]},
            {"section": "Станки / Объявление 2", "kind": "text", "cells": ["Поставим станок за неделю"]},
        ]),
        {"id": 4, "tool": "board_edit", "status": "done",
         "args": {"ops": [{"op": "remove", "kind": "title", "group": "Станки / Объявление 2", "text": "Станки с ЧПУ"}]}},
    ])
    assert [g["name"] for g in build["groups"]] == ["Станки", "Металлопрокат"]
    machines = build["groups"][0]
    assert [t["text"] for t in machines["titles"]] == ["Токарные станки"]
    assert [t["text"] for t in machines["texts"]] == ["Поставим станок за неделю"]


def test_checks_block_more_than_seven_titles():
    titles = [{"section": "Станки", "kind": "title", "cells": [f"Станки модель {i}"]} for i in range(9)]
    build = ai_agent.campaign_build(_goals_and_keywords() + [
        _table(3, "ads", titles + [{"section": "Станки", "kind": "text", "cells": ["Поставим за неделю"]}]),
    ])
    over = [c for c in build["checks"] if c["key"] == "limits"]
    assert over and over[0]["state"] == "bad" and "«Станки» — 9 заголовков" in over[0]["note"]
    seven = ai_agent.campaign_build(_goals_and_keywords() + [_table(3, "ads", titles[:7])])
    assert not [c for c in seven["checks"] if c["key"] == "limits"]


def _ads_rows(section, titles, checked=True):
    return [{"section": section, "kind": "title", "cells": [t], "checked": checked} for t in titles]


def test_ad_sections_must_be_board_groups():
    build = ai_agent.campaign_build(_goals_and_keywords())
    with pytest.raises(YandexError) as err:
        ai_agent._check_ad_sections(build, _ads_rows("Станки / Объявление 2", ["Станки"]), "ads")
    assert "одно объявление" in err.value.message
    with pytest.raises(YandexError) as err:
        ai_agent._check_ad_sections(build, _ads_rows("Станки ЧПУ", ["Станки"]), "ads")
    assert "«Станки ЧПУ»" in err.value.message and "«Станки»" in err.value.message
    ai_agent._check_ad_sections(build, _ads_rows("Станки", ["Станки"]), "ads")


def test_ad_limits_count_approved_rows():
    done = [f"Станки {i}" for i in range(7)]
    build = ai_agent.campaign_build(_goals_and_keywords() + [_table(3, "ads", _ads_rows("Станки", done))])
    # Ещё 7 к семи согласованным — 14 из 7, как на скриншоте.
    with pytest.raises(YandexError) as err:
        ai_agent._check_ad_sections(build, _ads_rows("Станки", [f"Новые {i}" for i in range(7)]), "ads")
    assert "14 заголовков из 7" in err.value.message
    # Замена: старые сняты checked=false — в пределе.
    rows = _ads_rows("Станки", done[:3], checked=False) + _ads_rows("Станки", ["Новые 1", "Новые 2", "Новые 3"])
    ai_agent._check_ad_sections(build, rows, "ads")
