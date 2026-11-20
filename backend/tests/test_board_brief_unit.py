"""Бриф формой (docs/board-plan.md, пункт 6): какие строки брифа — поля формы, стратегия по недельному бюджету."""

from app import ai_agent


def _row(name, value="", n=1, **extra):
    return {"n": n, "section": "", "kind": "goal", "cells": [name, value], "checked": True, **extra}


def test_row_kinds():
    kind = ai_agent._brief_row_kind
    assert kind(_row("Что рекламируем")) == "product"
    assert kind(_row("Гео")) == "region"
    assert kind(_row("Регион показа")) == "region"
    assert kind(_row("Бюджет")) == "budget"
    assert kind(_row("Цель рекламы")) == "goal"
    assert kind(_row("Конверсия")) == "goal"
    assert kind(_row("Сайт")) == "href"
    assert kind(_row("Запрещённые слова")) == "limits"
    assert kind(_row("Название кампании")) == "name"
    assert kind(_row("УТП")) == ""
    assert kind(_row("Оффер")) == ""
    # Строка, которую уже записала форма, — по метке, а не по названию.
    assert kind(_row("Что угодно", form="budget")) == "budget"


def test_rows_keep_numbers_and_ai_rows():
    old = [_row("Что рекламируем", "окна", 1), _row("УТП", "замер бесплатно", 2), _row("Гео", "Москва", 3)]
    rows = ai_agent._brief_rows(old, 3, {"product": "Окна ПВХ", "region": "Москва", "budget": "7 000 ₽ в неделю"})
    assert [(r["n"], r["cells"][0], r.get("form")) for r in rows] == [
        (1, "Что рекламируем", "product"),
        (2, "УТП", None),
        (3, "Регион", "region"),
        (4, "Бюджет в неделю", "budget"),
    ]
    assert rows[3]["cells"] == ["Бюджет в неделю", "7 000 ₽ в неделю", ""]
    # Поле очистили — строки нет; строки ИИ про другое остаются.
    rows = ai_agent._brief_rows(rows, 3, {"product": "Окна ПВХ"})
    assert [r["cells"][0] for r in rows] == ["Что рекламируем", "УТП"]


def test_strategy_from_weekly_budget_and_goal():
    strategy = ai_agent._brief_strategy
    assert strategy({}, None, None) is None
    assert strategy({}, 7000, None) == {"network": "SERVING_OFF", "search": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 7000}
    assert strategy({"search": "WB_MAXIMUM_CLICKS", "network": "NETWORK_DEFAULT"}, 7000, 42) == {
        "search": "WB_MAXIMUM_CONVERSION_RATE", "network": "NETWORK_DEFAULT", "weekly_budget_rub": 7000, "goal_id": 42,
    }
    # Цель убрали — снова клики.
    assert strategy({"search": "WB_MAXIMUM_CONVERSION_RATE", "goal_id": 42, "weekly_budget_rub": 1}, 5000, None) == {
        "network": "SERVING_OFF", "search": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 5000,
    }
    # Свою стратегию ИИ не меняем — только её бюджет и цель.
    assert strategy({"search": "AVERAGE_CPA", "average_cpa_rub": 500}, 9000, 7) == {
        "search": "AVERAGE_CPA", "average_cpa_rub": 500, "weekly_budget_rub": 9000, "goal_id": 7,
    }


def test_missing_fields():
    full = {"account_id": 1, "name": "Окна", "region_ids": [213], "href": "https://shop.ru"}
    assert ai_agent._brief_missing(full, link_template=False) == []
    assert ai_agent._brief_missing({**full, "href": ""}, link_template=True) == []
    assert ai_agent._brief_missing({"region_ids": [-213]}, link_template=False) == [
        "кабинет", "название кампании", "регион", "посадочная ссылка",
    ]
