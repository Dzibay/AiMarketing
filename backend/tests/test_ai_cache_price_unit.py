"""Цена кэшированного входа: разбор usage провайдера и расчёт единиц и себестоимости."""

import pytest

from app import ai

CFG = {"units_in": 200, "units_out": 1000, "units_cache": 10, "cost_in": 20, "cost_out": 100, "cost_cache": 1}


def test_cached_tokens_openai_format():
    assert ai.cached_tokens({"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 800}}) == 800


def test_cached_tokens_missing_or_bad():
    assert ai.cached_tokens({"prompt_tokens": 1000}) == 0
    assert ai.cached_tokens({"prompt_tokens_details": None}) == 0
    assert ai.cached_tokens({"prompt_tokens_details": {"cached_tokens": "x"}}) == 0
    assert ai.cached_tokens({"cached_tokens": 5}) == 5


def test_cache_priced_separately():
    # 1 млн входа, из них 800 тыс. из кэша, 10 тыс. выхода.
    assert ai.units_for(CFG, 1_000_000, 10_000, 800_000) == pytest.approx(200 * 0.2 + 10 * 0.8 + 1000 * 0.01)
    assert ai.cost_rub(CFG, 1_000_000, 10_000, 800_000) == pytest.approx(20 * 0.2 + 1 * 0.8 + 100 * 0.01)


def test_no_cache_price_means_full_input():
    cfg = {**CFG, "units_cache": None, "cost_cache": None}
    assert ai.units_for(cfg, 1_000_000, 0, 800_000) == pytest.approx(200)
    assert ai.cost_rub(cfg, 1_000_000, 0, 800_000) == pytest.approx(20)


def test_cached_clamped_to_input_and_no_price():
    assert ai.units_for(CFG, 1000, 0, 5000) == pytest.approx(1000 * 10 / 1_000_000)
    assert ai.units_for({}, 1000, 1000, 500) == 0.0
    assert ai.cost_rub({}, 1000, 1000, 500) is None


def test_usage_reads_cache():
    data = {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 900_000}}}
    usage = ai._usage(CFG, data, [{"role": "user", "content": "x"}], None, "ok", [])
    assert usage["tokens_cached"] == 900_000
    assert usage["charged"] == pytest.approx(200 * 0.1 + 10 * 0.9)
    assert usage["cost_rub"] == pytest.approx(20 * 0.1 + 1 * 0.9)


def test_usage_estimated_has_no_cache():
    usage = ai._usage(CFG, {}, [{"role": "user", "content": "x"}], None, "ok", [])
    assert usage["estimated"] and usage["tokens_cached"] == 0
