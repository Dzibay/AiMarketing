from decimal import Decimal

from app.adgroups import _parse_phrases
from app.keywords import _rub_to_micros
from app.stats import _rub_to_kopecks
from app.telegram_notify import format_rule_alert


def test_rub_to_kopecks():
    assert _rub_to_kopecks("10.50") == 1050
    assert _rub_to_kopecks(1) == 100
    assert _rub_to_kopecks(None) == 0
    assert _rub_to_kopecks("bad") == 0


def test_keyword_bid_micros():
    assert _rub_to_micros(12.5) == 12_500_000
    assert _rub_to_micros(None) is None
    assert _rub_to_micros(0) is None
    assert _rub_to_micros(-1) is None


def test_parse_negative_phrases():
    assert _parse_phrases("a\nb\na") == ["a", "b"]
    assert _parse_phrases('["x", "y"]') == ["x", "y"]
    assert _parse_phrases(["  z  ", ""]) == ["z"]
    assert _parse_phrases(None) == []


def test_cpa_threshold_math():
    """Порог правила в рублях, cost в копейках — как в rules._matching_campaigns."""
    cost = Decimal(150000)  # 1500 ₽
    conv = Decimal(2)
    cpa = cost / conv
    threshold = Decimal("700")  # ₽
    assert cpa >= threshold * 100
    assert float(cpa / 100) == 750.0


def test_keyword_rule_combos():
    from app.rules import _validate_target_combo
    from app.yandex import YandexError

    _validate_target_combo("keyword", "cpa_above", "pause")
    _validate_target_combo("keyword", "cpc_above", "set_bid")
    _validate_target_combo("query", "spend_no_conv", "negative_adgroup")
    _validate_target_combo("query", "cpc_above", "negative_campaign")
    try:
        _validate_target_combo("keyword", "daily_budget_pct", "pause")
        assert False, "expected error"
    except YandexError as exc:
        assert exc.code == "rule-type"
    try:
        _validate_target_combo("campaign", "cpa_above", "set_bid")
        assert False, "expected error"
    except YandexError as exc:
        assert exc.code == "action"
    try:
        _validate_target_combo("query", "daily_budget_pct", "notify")
        assert False, "expected error"
    except YandexError as exc:
        assert exc.code == "rule-type"
    try:
        _validate_target_combo("query", "spend_no_conv", "pause")
        assert False, "expected error"
    except YandexError as exc:
        assert exc.code == "action"


def test_format_rule_alert():
    text = format_rule_alert(
        rule_name="CPA",
        action="pause",
        dry_run=False,
        matched=[{"name": "Camp A", "reason": "CPA 900 >= 500"}],
        applied=1,
    )
    assert "CPA" in text
    assert "Camp A" in text
    assert "pause" in text
