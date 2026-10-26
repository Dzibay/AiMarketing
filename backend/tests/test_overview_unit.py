"""Расчёты сводки без БД: KPI, темп бюджета, баланс, аномалии, текст дайджеста."""

from datetime import date, timedelta

import pytest

from app.digest import format_digest
from app.overview import budget_pace, campaign_anomalies, days_on_balance, kpi_state, rub

DAY = date(2026, 10, 25)


def _history(base_cost: int, base_conv: int, day_cost: int, day_conv: int, days: int = 14) -> dict:
    history = {DAY - timedelta(days=i): (base_cost, base_conv) for i in range(1, days + 1)}
    history[DAY] = (day_cost, day_conv)
    return history


def _kinds(found: list[dict]) -> set[str]:
    return {a["kind"] for a in found}


def test_rub_keeps_number_on_one_line():
    assert rub(1234567) == "12 346 ₽"
    assert rub(None) == "—"


@pytest.mark.parametrize(
    ("cost", "conv", "status", "ratio"),
    [
        (90_000, 1, "ok", 0.9),  # CPA 900 ₽ при цели 1000 ₽
        (120_000, 1, "warn", 1.2),
        (300_000, 2, "bad", 1.5),
        (50_000, 0, "none", None),  # конверсий нет, но и цель ещё не потрачена
        (250_000, 0, "bad", 2.5),  # 2500 ₽ без конверсий — CPA точно не лучше
    ],
)
def test_kpi_cpa(cost, conv, status, ratio):
    kpi = kpi_state("cpa", 1000, cost, conv, 0)
    assert (kpi["status"], kpi["ratio"]) == (status, ratio)


def test_kpi_drr_and_missing_target():
    assert kpi_state("drr", 20, 10_000, 3, 100_000) == {
        "type": "drr",
        "target": 20,
        "value": 10.0,
        "ratio": 0.5,
        "status": "ok",
    }
    assert kpi_state("drr", 20, 10_000, 3, 0)["status"] == "none"  # дохода нет — оценивать не по чему
    assert kpi_state("cpa", None, 10_000, 1, 0)["value"] == 10_000
    assert kpi_state("", None, 10_000, 1, 0)["status"] == "none"


def test_budget_pace():
    # 10 полных дней по 4000 ₽ в 31-дневном месяце → прогноз 124 000 ₽ при бюджете 100 000 ₽.
    pace = budget_pace(10_000_000, 4_100_000, 4_000_000, date(2026, 10, 11))
    assert pace["forecast_kopecks"] == 12_400_000
    assert pace["pace_pct"] == 124
    assert pace["status"] == "over"
    assert pace["daily_allowed_kopecks"] == round(5_900_000 / 21)

    assert budget_pace(10_000_000, 0, 0, date(2026, 10, 1))["status"] == "ok"  # первый день: прогноза нет
    assert budget_pace(10_000_000, 1_000_000, 1_000_000, date(2026, 10, 4))["status"] == "ok"  # рано судить
    assert budget_pace(10_000_000, 1_000_000, 1_000_000, date(2026, 10, 15))["status"] == "under"
    assert budget_pace(10_000_000, 10_000_000, 9_000_000, date(2026, 10, 20))["status"] == "exhausted"
    assert budget_pace(None, 1, 1, DAY) is None


def test_days_on_balance():
    assert days_on_balance(700_000, 1_400_000) == 3.5
    assert days_on_balance(-5, 700) == 0
    assert days_on_balance(700_000, 0) is None
    assert days_on_balance(None, 700) is None


def test_spend_spike_and_drop():
    assert _kinds(campaign_anomalies(_history(100_000, 2, 250_000, 5), DAY, day_loaded=True)) == {"spend_spike"}
    # Рост в 3 раза, но на копейки — не тревожим.
    assert campaign_anomalies(_history(10_000, 0, 30_000, 0), DAY, day_loaded=True) == []
    assert _kinds(campaign_anomalies(_history(100_000, 2, 20_000, 1), DAY, day_loaded=True)) == {"spend_drop"}
    # Статистика за день не загружена — падение может быть ложным.
    assert campaign_anomalies(_history(100_000, 2, 20_000, 1), DAY, day_loaded=False) == []


def test_new_campaign_has_no_baseline():
    assert campaign_anomalies(_history(100_000, 2, 900_000, 0, days=3), DAY, day_loaded=True) == []


def test_cpa_spike_and_spend_without_conversions():
    # Обычно 1000 ₽ за 2 конверсии (CPA 500 ₽), вчера 1100 ₽ за 1 — CPA ×2,2.
    found = campaign_anomalies(_history(100_000, 2, 110_000, 1), DAY, day_loaded=True)
    assert _kinds(found) == {"cpa_spike"}

    found = campaign_anomalies(_history(100_000, 2, 110_000, 0), DAY, day_loaded=True)
    assert _kinds(found) == {"no_conversions"}

    # Кампания без конверсий в базе и без цели CPA — «нет конверсий» не новость.
    assert campaign_anomalies(_history(100_000, 0, 110_000, 0), DAY, day_loaded=True) == []
    # С целью CPA клиента — новость.
    found = campaign_anomalies(_history(100_000, 0, 110_000, 0), DAY, day_loaded=True, cpa_target_kopecks=50_000)
    assert _kinds(found) == {"no_conversions"}


def _client(name, spend, **extra):
    row = {
        "id": 1,
        "name": name,
        "accounts": 1,
        "spend_yesterday": spend,
        "conversions_yesterday": 0,
        "kpi": {"type": "", "value": None, "status": "none"},
        "budget": None,
        "days_on_balance": None,
    }
    row.update(extra)
    return row


def test_format_digest():
    overview = {
        "yesterday": "2026-10-25",
        "totals": {"spend_yesterday": 300_000, "conversions_yesterday": 6},
        "problems": [
            {"severity": "critical", "client_name": "Ромашка", "object_name": "", "title": "Деньги закончились"},
            {"severity": "warning", "client_name": "Вектор", "object_name": "Поиск", "title": "Расход упал"},
            {"severity": "info", "client_name": "Вектор", "object_name": "", "title": "Недорасход"},
        ],
        "clients": [
            _client("Вектор", 100_000),
            _client(
                "Ромашка",
                200_000,
                conversions_yesterday=6,
                kpi={"type": "cpa", "value": 33_333, "status": "ok"},
                budget={"pace_pct": 104},
                days_on_balance=1.5,
            ),
            _client("Пустой", 0, accounts=0),
        ],
    }
    text = format_digest(overview, "https://aim.example")
    assert "итоги 25.10" in text
    assert f"CPA {rub(50_000)}" in text
    assert "Где горит (2):" in text
    assert "🔴 Ромашка: Деньги закончились" in text
    assert "🟠 Вектор · Поиск: Расход упал" in text
    assert "Недорасход" not in text  # info в дайджест не идёт
    assert text.index("• Ромашка") < text.index("• Вектор")  # по расходу
    assert "темп бюджета 104%" in text and "баланс на 1.5 дн." in text and "✓" in text
    assert "Пустой" not in text
    assert text.endswith("https://aim.example/cabinet/overview")

    calm = format_digest({**overview, "problems": [], "clients": []})
    assert "Ничего не горит" in calm
