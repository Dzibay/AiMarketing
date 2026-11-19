"""Помогла ли правка: вердикт, подписи, окна и оценка отсечённых запросов — без БД."""

from datetime import date

from app import effects
from app.effects import added_phrases, cut_cost, describe, judge, phrase_matches, week_text, windows_for


def plain(text):
    return text.replace(" ", " ")


def side(cost_rub=0, clicks=0, conv=0):
    return {"cost_kopecks": int(cost_rub * 100), "clicks": clicks, "conversions": conv}


def test_cpa_better_worse_neutral():
    before = side(10_000, 200, 10)  # 1 000 ₽ за заявку
    assert judge(before, side(8_200, 200, 10)) == {"verdict": "better", "metric": "cpa", "change_pct": -18.0}
    assert judge(before, side(12_000, 200, 10))["verdict"] == "worse"
    assert judge(before, side(11_000, 200, 10))["verdict"] == "neutral"


def test_low_data_when_few_conversions_or_no_spend_after():
    assert judge(side(500, 10, 1), side(400, 8, 2))["verdict"] == "low_data"
    # Кампанию остановили — после правки ни расхода, ни кликов.
    assert judge(side(10_000, 200, 10), side())["verdict"] == "low_data"


def test_conversions_appeared_or_vanished():
    assert judge(side(5_000, 100, 0), side(5_000, 100, 4)) == {
        "verdict": "better",
        "metric": "conversions",
        "change_pct": None,
    }
    # Было 5 заявок по 1 000 ₽, после — ни одной за 3 000 ₽.
    assert judge(side(5_000, 100, 5), side(3_000, 80, 0))["verdict"] == "worse"
    # Потратили меньше прежней цены заявки — судить рано.
    assert judge(side(5_000, 100, 5), side(500, 10, 0))["verdict"] == "low_data"


def test_zero_cost_cpa_does_not_crash():
    # Заявки до правки без расхода (бонусы, поздняя атрибуция) — цену заявки не сравнить.
    assert judge(side(0, 0, 3), side(500, 100, 2))["verdict"] == "low_data"
    assert judge(side(5_000, 100, 4), side(0, 10, 3))["verdict"] == "low_data"
    # Было 3+ заявки бесплатно, после — ни одной: прежней цены заявки нет — не «хуже».
    assert judge(side(0, 10, 3), side(3_000, 80, 0))["verdict"] == "low_data"


def test_conversions_from_empty_baseline_need_traffic_before():
    # Кампании до правки не было (запуск на днях): «0 → 4» — не заслуга правки.
    assert judge(side(), side(5_000, 100, 4))["verdict"] == "low_data"
    # У запуска пустое «до» ожидаемо.
    assert judge(side(), side(5_000, 100, 4), zero_baseline_ok=True)["verdict"] == "better"


def test_negatives_without_conversions_judged_by_cost():
    before, after = side(3_000, 150, 0), side(2_000, 100, 0)
    assert judge(before, after)["verdict"] == "low_data"
    res = judge(before, after, negatives=True)
    assert res["verdict"] == "better" and res["metric"] == "cost" and res["change_pct"] == -33.3
    assert judge(before, side(4_000, 200, 0), negatives=True)["verdict"] == "worse"
    # Меньше 1 000 ₽ до правки — мало данных и для минус-фраз.
    assert judge(side(900, 50, 0), side(100, 5, 0), negatives=True)["verdict"] == "low_data"
    # Расход упал вместе с заявками — не заслуга минус-фраз.
    assert judge(side(3_000, 150, 2), side(2_000, 100, 1), negatives=True)["verdict"] == "neutral"


def _row(verdict, metric="cpa", pct=None, window=7, **kw):
    base = {
        "window_days": window,
        "before_cost_kopecks": 1_000_000,
        "before_clicks": 200,
        "before_conversions": 10,
        "after_cost_kopecks": 820_000,
        "after_clicks": 210,
        "after_conversions": 10,
        "verdict": verdict,
        "metric": metric,
        "change_pct": pct,
        "cut_cost_kopecks": None,
        "others": 0,
    }
    return {**base, **kw}


def test_describe_texts():
    out = describe(_row("better", pct=-18.0))
    assert plain(out["text"]) == "Цена заявки −18 % за 7 дней"
    assert out["tone"] == "ok"
    assert "день правки не считаем" in out["hint"] and "совпал по времени" in out["hint"]
    assert plain(describe(_row("worse", pct=32.4, window=14))["text"]) == "Цена заявки +32 % за 14 дней"
    assert describe(_row("worse", pct=32.4))["tone"] == "warn"
    low = describe(_row("low_data", metric="none"))
    assert low["text"] == "Мало данных за 7 дней" and low["tone"] == "muted"
    assert plain(describe(_row("better", metric="cost", pct=-40.0))["text"]) == "Расход −40 % за 7 дней"
    conv = describe(_row("better", metric="conversions", before_conversions=0, after_conversions=4))
    assert conv["text"] == "Заявки: 0 → 4 за 7 дней"
    note = plain(describe(_row("neutral", pct=3.0, others=2, cut_cost_kopecks=120_000))["hint"])
    assert "ещё 2 правки" in note and "1 200" in note


def test_windows_skip_change_day():
    before, after = windows_for(date(2026, 11, 10), 7)
    assert before == (date(2026, 11, 3), date(2026, 11, 9))
    assert after == (date(2026, 11, 11), date(2026, 11, 17))


def test_phrase_matching_and_cut_cost():
    assert phrase_matches("бесплатно", "окна бесплатно москва")
    assert phrase_matches("!своими руками", "пластиковые окна своими руками")
    assert phrase_matches("пластиковые", "пластиковых окон цена")
    assert not phrase_matches("ремонт", "окна цена")
    queries = [
        # 10 дней отчёта до правки (с 1 по 10 ноября, правка 11-го).
        {"query": "окна бесплатно", "cost_kopecks": 50_000, "date_from": date(2026, 11, 1), "date_to": date(2026, 11, 14)},
        {"query": "окна цена", "cost_kopecks": 90_000, "date_from": date(2026, 11, 1), "date_to": date(2026, 11, 14)},
    ]
    assert cut_cost(["бесплатно"], queries, date(2026, 11, 11), 7) == 35_000
    assert cut_cost([], queries, date(2026, 11, 11), 7) is None
    assert cut_cost(["ремонт"], queries, date(2026, 11, 11), 7) is None


def test_added_phrases_from_undo_steps():
    undo = {
        "steps": [
            {"op": "negatives", "args": {"campaigns": {"5": {"remove": ["бесплатно", "своими руками"]}}}},
            {"op": "negatives", "args": {"adgroups": {"9": {"remove": ["ремонт"], "add": ["старое"]}}}},
            {"op": "campaign_budgets", "args": {}},
        ]
    }
    assert added_phrases(undo) == {"campaigns": ["бесплатно", "своими руками"], "adgroups": ["ремонт"]}
    assert added_phrases(None) == {"campaigns": [], "adgroups": []}


def test_week_text():
    assert week_text(5, 1) == "За неделю правки помогли у 5 проектов, хуже — у 1"
    assert week_text(1, 0) == "За неделю правки помогли у 1 проекта"
    assert week_text(0, 2) == "За неделю после правок стало хуже у 2 проектов"
    assert week_text(0, 0) == ""


def test_qualifying_actions_are_changes_not_reads():
    assert "undo" not in effects.QUALIFYING and "duplicate" not in effects.QUALIFYING
    for action in ("set_budget", "bid_adjust", "settings", "negatives", "pause", "resume", "ads", "adgroups"):
        assert action in effects.QUALIFYING


def test_attach_without_ids_does_not_touch_db():
    items = [{"id": None}]
    assert effects.attach(1, items) == [{"id": None, "effect": None}]
