"""«Защита бюджета»: шаблоны правил, порог в ценах заявки и его пересчёт в рубли — без базы."""

from decimal import Decimal

import pytest

from app import rules, rules_templates as rt
from app.yandex import YandexError


def test_templates_use_existing_rule_types_and_actions():
    keys = [t["key"] for t in rt.TEMPLATES]
    assert keys == ["spend_notify", "spend_pause", "daily_budget"]
    for t in rt.TEMPLATES:
        assert t["rule_type"] in rules.RULE_TYPES and t["action"] in rules.RULE_ACTIONS
    pause = rt.BY_KEY["spend_pause"]
    assert pause["rule_type"] == "spend_no_conv" and pause["action"] == "pause"
    assert pause["caption"].startswith("Сервис сам остановит кампанию")
    assert not pause["default_on"]
    assert rt.BY_KEY["daily_budget"]["threshold"] == Decimal("90")


def _text(*args):
    # В суммах — неразрывные пробелы.
    return rt.threshold_text(*args).replace(" ", " ")


def test_threshold_text():
    spend = rt.BY_KEY["spend_notify"]
    assert _text(spend, "cpa_x", Decimal("2"), Decimal("3000")) == "2 цены заявки (6 000 ₽)"
    assert _text(spend, "cpa_x", Decimal("1"), None) == "1 цена заявки"
    assert _text(spend, "cpa_x", Decimal("1.5"), Decimal("1000")) == "1,5 цены заявки (1 500 ₽)"
    assert _text(spend, "cpa_x", Decimal("5"), None) == "5 цен заявки"
    assert _text(spend, "rub", Decimal("3000"), Decimal("1")) == "3 000 ₽"
    assert _text(rt.BY_KEY["daily_budget"], "rub", Decimal("90"), None) == "90 %"


def test_price_word_russian_plural():
    cases = {
        "1": "цена", "2": "цены", "4": "цены", "5": "цен", "11": "цен", "12": "цен", "14": "цен",
        "20": "цен", "21": "цена", "22": "цены", "24": "цены", "25": "цен", "31": "цена", "41": "цена",
        "44": "цены", "50": "цен", "0.5": "цены", "21.5": "цены", "21.0": "цена",
    }
    for value, word in cases.items():
        assert rt.price_word(Decimal(value)) == f"{word} заявки", value
    assert _text(rt.BY_KEY["spend_notify"], "cpa_x", Decimal("21"), None) == "21 цена заявки"


def test_default_threshold_prefers_target_cpa():
    spend = rt.BY_KEY["spend_notify"]
    assert rt.default_threshold(spend, Decimal("500")) == ("cpa_x", Decimal("2"))
    assert rt.default_threshold(spend, None) == ("rub", Decimal("3000"))
    assert rt.default_threshold(rt.BY_KEY["daily_budget"], Decimal("500")) == ("rub", Decimal("90"))


def test_clean_threshold():
    spend = rt.BY_KEY["spend_pause"]
    assert rt.clean_threshold(spend, "cpa_x", "2.55", Decimal("100")) == ("cpa_x", Decimal("2.6"))
    assert rt.clean_threshold(spend, "rub", 4000.4, None) == ("rub", Decimal("4000"))
    with pytest.raises(YandexError) as exc:
        rt.clean_threshold(spend, "cpa_x", 2, None)
    assert exc.value.code == "no-target-cpa"
    for kind, value in (("cpa_x", 0.1), ("cpa_x", 100), ("rub", 50), ("rub", "много")):
        with pytest.raises(YandexError) as exc:
            rt.clean_threshold(spend, kind, value, Decimal("100"))
        assert exc.value.code == "threshold"
    with pytest.raises(YandexError):
        rt.clean_threshold(spend, "pct", 2, Decimal("100"))
    # NaN и бесконечность из запроса — понятный отказ, а не падение на сравнении.
    for value in (float("nan"), float("inf"), "-Infinity"):
        for kind in ("cpa_x", "rub"):
            with pytest.raises(YandexError) as exc:
                rt.clean_threshold(spend, kind, value, Decimal("100"))
            assert exc.value.code == "threshold"
    # У дневного бюджета порог фиксированный.
    assert rt.clean_threshold(rt.BY_KEY["daily_budget"], "cpa_x", 5, None) == ("rub", Decimal("90"))


def test_target_cpa_of_project_beats_client():
    assert rules.target_cpa_of({"kpi_type": "cpa", "kpi_target": 800, "client_kpi_type": "cpa", "client_kpi_target": 100}) == Decimal("800")
    assert rules.target_cpa_of({"kpi_type": "", "kpi_target": None, "client_kpi_type": "cpa", "client_kpi_target": 100}) == Decimal("100")
    # У проекта своя цель — ДРР: цену заявки клиента не берём.
    assert rules.target_cpa_of({"kpi_type": "drr", "kpi_target": 20, "client_kpi_type": "cpa", "client_kpi_target": 100}) is None
    assert rules.target_cpa_of({"kpi_type": "cpa", "kpi_target": 0}) is None
    assert rules.target_cpa_of({}) is None


def test_validate_threshold_kind():
    assert rules._validate_threshold_kind(None, "spend_no_conv", None) == "rub"
    assert rules._validate_threshold_kind("cpa_x", "spend_no_conv", 5) == "cpa_x"
    with pytest.raises(YandexError):
        rules._validate_threshold_kind("cpa_x", "daily_budget_pct", 5)
    with pytest.raises(YandexError):
        rules._validate_threshold_kind("cpa_x", "spend_no_conv", None)
    with pytest.raises(YandexError):
        rules._validate_threshold_kind("x", "spend_no_conv", 5)


def test_rule_threshold_rub(monkeypatch):
    monkeypatch.setattr(rules, "project_target_cpa_rub", lambda user_id, ws: Decimal("1200") if ws == 7 else None)
    assert rules.rule_threshold_rub(1, {"threshold_value": 2.5, "threshold_kind": "cpa_x", "scope_workspace_id": 7}) == Decimal("3000.00")
    assert rules.rule_threshold_rub(1, {"threshold_value": 2, "threshold_kind": "cpa_x", "scope_workspace_id": 8}) is None
    assert rules.rule_threshold_rub(1, {"threshold_value": 500, "threshold_kind": "rub", "scope_workspace_id": 8}) == Decimal("500")
    assert rules.rule_threshold_rub(1, {"threshold_value": 500}) == Decimal("500")


def test_rule_link():
    tpl = {"template": "spend_pause", "scope_workspace_id": 4}
    assert rules._rule_link(tpl, "pause", False) == "/w/4/history"
    assert rules._rule_link(tpl, "pause", True) == "/w/4/campaigns"
    assert rules._rule_link({**tpl, "template": "spend_notify"}, "notify", False) == "/w/4/campaigns"
    assert rules._rule_link({"template": None, "scope_workspace_id": 4}, "pause", False) == "/cabinet/autopilot"
