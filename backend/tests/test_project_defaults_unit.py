"""«Для новых проектов»: шаблон ссылки без домена, проверка настроек и тексты «было → станет»."""

from decimal import Decimal

import pytest

from app import project_defaults as pd
from app.workspaces import DEFAULT_AI_ACCESS
from app.yandex import YandexError


@pytest.mark.parametrize(
    "raw, rel",
    [
        ("", ""),
        ("utm_source=yandex&utm_campaign={campaign_id}", "/?utm_source=yandex&utm_campaign={campaign_id}"),
        ("?ref=Yandex", "/?ref=Yandex"),
        ("/promo?utm_source=yandex", "/promo?utm_source=yandex"),
        ("https://old.ru/?ref=Yandex&utm_source=yandex", "/?ref=Yandex&utm_source=yandex"),
        ("old.ru/sale?utm_medium=cpc", "/sale?utm_medium=cpc"),
    ],
)
def test_relative_template(raw, rel):
    assert pd.relative_template(raw) == rel


def test_relative_template_rejects_duplicates():
    with pytest.raises(YandexError):
        pd.relative_template("utm_source=a&utm_source=b")


@pytest.mark.parametrize(
    "site, expected",
    [
        ("okna.ru", "https://okna.ru/?utm_source=yandex"),
        ("http://okna.ru/", "http://okna.ru/?utm_source=yandex"),
        ("https://www.okna.ru/catalog", "https://www.okna.ru/?utm_source=yandex"),
        ("", ""),
        ("localhost", ""),
    ],
)
def test_template_for_site(site, expected):
    assert pd.template_for_site("/?utm_source=yandex", site) == expected


def test_template_for_site_empty_template():
    assert pd.template_for_site("", "okna.ru") == ""


def test_normalize_drops_garbage():
    out = pd.normalize({"url_template": 5, "kpi": {"kpi_type": "roi"}, "ai_access": "x", "junk": 1})
    assert out == {"url_template": "", "model_class": "", "ai_access": None, "kpi": None, "budget_protection": None}
    assert pd.normalize(None)["kpi"] is None


def test_clean_kpi():
    assert pd._clean_kpi({"kpi_type": "CPA", "kpi_target": "1500"}) == {"kpi_type": "cpa", "kpi_target": 1500.0}
    assert pd._clean_kpi({"kpi_type": "drr"}) == {"kpi_type": "drr", "kpi_target": None}
    assert pd._clean_kpi({"kpi_type": ""}) is None
    with pytest.raises(YandexError):
        pd._clean_kpi({"kpi_type": "cpa", "kpi_target": -1})
    with pytest.raises(YandexError):
        pd._clean_kpi({"kpi_type": "roi"})


def test_clean_protection():
    out = pd._clean_protection(
        {
            "spend_notify": {"enabled": True, "threshold_kind": "cpa_x", "value": 2},
            "daily_budget": {"enabled": True, "threshold_kind": "rub", "value": 1},
            "unknown": {"enabled": True},
        }
    )
    # У проверки дневного бюджета порога нет — он не сохраняется.
    assert out == {"spend_notify": {"enabled": True, "threshold_kind": "cpa_x", "value": 2.0}, "daily_budget": {"enabled": True}}
    # Ничего не включено — «не задавать».
    assert pd._clean_protection({"spend_notify": {"enabled": False}}) is None
    with pytest.raises(YandexError):
        pd._clean_protection({"spend_pause": {"enabled": True, "threshold_kind": "cpa_x", "value": 100}})
    with pytest.raises(YandexError):
        pd._clean_protection({"spend_pause": {"enabled": True, "threshold_kind": "rub", "value": 10}})
    with pytest.raises(YandexError):
        pd._clean_protection({"spend_pause": {"enabled": True, "threshold_kind": "pct", "value": 10}})


def test_texts():
    assert pd.kpi_text(None) == "Не задан"
    assert pd.kpi_text({"kpi_type": "cpa", "kpi_target": 1500}) == "Цена заявки 1 500 ₽"
    assert pd.kpi_text({"kpi_type": "drr", "kpi_target": 12.5}) == "Доля расходов на рекламу 12,5 %"
    assert pd.access_text(DEFAULT_AI_ACCESS) == "Всё — после вашего «Выполнить»"
    assert pd.access_text({**DEFAULT_AI_ACCESS, "full_access": True}) == "Полный доступ"
    some = {**DEFAULT_AI_ACCESS, "categories": {**DEFAULT_AI_ACCESS["categories"], "negatives": True}, "max_change_pct": 20}
    assert pd.access_text(some) == "Без согласования: минус-фразы; предел 20 %"
    assert pd.style_text({"tone": "sell", "address": "ty"}, True) == "Продающий, на «ты»"
    assert pd.style_text({"tone": "sell", "address": "vy"}, False) == "Как в аккаунте, на «вы»"


def test_protection_text():
    prot = {
        "spend_notify": {"enabled": True, "threshold_kind": "cpa_x", "value": 2.0},
        "spend_pause": {"enabled": False, "threshold_kind": "rub", "value": 5000.0},
        "daily_budget": {"enabled": True},
    }
    text = pd.protection_text(prot, Decimal("1000")).replace("\xa0", " ")
    assert "2 цены заявки (2 000 ₽)" in text and "90 %" in text and "Остановить" not in text
    # Без цены заявки порог «в ценах» не посчитать — показываем рублёвый по умолчанию.
    assert "3 000 ₽" in pd.protection_text(prot, None).replace("\xa0", " ")
    assert pd.protection_text(None, None) == "Выключена"


def test_defaults_meta_has_no_placeholders():
    meta = pd.defaults_meta()
    assert all("{" not in p["title"] for p in meta["protection"])
    assert {c["key"] for c in meta["ai_categories"]} == set(DEFAULT_AI_ACCESS["categories"])


def _facts(prefix, n):
    return [{"key": f"{prefix}{i}", "question": "В?", "answer": "О"} for i in range(n)]


def test_merged_facts_never_drops_own_answers():
    from app.workspaces import MAX_AI_FACTS

    have = _facts("own", MAX_AI_FACTS - 5)
    merged, left_out = pd._merged_facts({"ai_facts": have}, {"ai_facts": _facts("src", 10)})
    assert len(merged) == MAX_AI_FACTS and left_out == 5
    assert [f["key"] for f in merged[: len(have)]] == [f["key"] for f in have]
    # Память полна — свои ответы на месте, ничего не добавилось.
    merged, left_out = pd._merged_facts({"ai_facts": _facts("own", MAX_AI_FACTS)}, {"ai_facts": _facts("src", 2)})
    assert [f["key"] for f in merged] == [f"own{i}" for i in range(MAX_AI_FACTS)] and left_out == 2
    # Те же ключи не считаются новыми.
    merged, left_out = pd._merged_facts({"ai_facts": _facts("a", 2)}, {"ai_facts": _facts("a", 3)})
    assert [f["key"] for f in merged] == ["a0", "a1", "a2"] and left_out == 0


def test_kpi_fields():
    assert pd._kpi_fields(None) == {"kpi_type": "", "kpi_target": None}
    assert pd._kpi_fields({"kpi_type": "cpa", "kpi_target": 1500.0}) == {"kpi_type": "cpa", "kpi_target": 1500.0}
