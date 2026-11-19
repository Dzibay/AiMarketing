"""Прогноз бюджета месяца с учётом дней недели (I27): профиль по неделям и досчёт остатка месяца."""

from datetime import date, timedelta

from app.overview import budget_pace, weekday_forecast, weekday_profile

TODAY = date(2026, 11, 19)  # четверг; 1 ноября — воскресенье, в ноябре 30 дней
WEEKDAY, WEEKEND = 5000, 1000


def _history(days: int, today: date = TODAY) -> dict[date, int]:
    """days дней до сегодня: будни по 5000, выходные по 1000."""
    out = {}
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        out[d] = WEEKEND if d.weekday() >= 5 else WEEKDAY
    return out


def test_profile_needs_four_weeks():
    assert weekday_profile(_history(27), TODAY) is None
    assert weekday_profile({}, TODAY) is None
    assert weekday_profile({d: 0 for d in _history(40)}, TODAY) is None  # расхода не было
    profile = weekday_profile(_history(28), TODAY)
    assert profile == [WEEKDAY] * 5 + [WEEKEND] * 2


def test_profile_uses_at_most_eight_weeks():
    history = _history(120)
    # Старше 8 недель — другой уровень расхода: в профиль не попадает.
    for d in list(history):
        if d < TODAY - timedelta(days=56):
            history[d] = 999_999
    assert weekday_profile(history, TODAY) == [WEEKDAY] * 5 + [WEEKEND] * 2


def test_profile_ignores_today():
    history = _history(35)
    history[TODAY] = 10**9
    assert weekday_profile(history, TODAY) == [WEEKDAY] * 5 + [WEEKEND] * 2


def test_weekday_forecast_counts_weekends():
    profile = [WEEKDAY] * 5 + [WEEKEND] * 2
    # 1–18 ноября: 13 будней и 5 выходных = 70 000; 19–30: 8 будней и 4 выходных = 44 000.
    assert weekday_forecast(profile, 70_000, TODAY) == 114_000
    # Тратим вдвое больше обычного — остаток тоже вдвое.
    assert weekday_forecast(profile, 140_000, TODAY) == 228_000
    # Первый день месяца: прошедших дней нет — остаток по средним прошлых недель.
    assert weekday_forecast(profile, 0, date(2026, 11, 1)) == 21 * WEEKDAY + 9 * WEEKEND
    # Прошли только дни, в которые обычно не тратят, — множитель не определён.
    assert weekday_forecast([1, 1, 1, 1, 1, 0, 0], 500, date(2026, 11, 2)) is None


def test_budget_pace_with_profile():
    profile = weekday_profile(_history(56), TODAY)
    pace = budget_pace(100_000, 72_000, 70_000, TODAY, profile=profile)
    assert pace["forecast_kopecks"] == 114_000 and pace["forecast_method"] == "weekdays"
    assert pace["status"] == "over"
    assert pace["day"] == 19 and pace["days_in_month"] == 30
    # Поровну было бы 70 000 / 18 × 30 ≈ 116 667.
    even = budget_pace(100_000, 72_000, 70_000, TODAY)
    assert even["forecast_kopecks"] == 116_667 and even["forecast_method"] == "even"
    # Профиль есть, но множитель не определён — откат к делению поровну.
    fallback = budget_pace(100_000, 500, 500, date(2026, 11, 2), profile=[1, 1, 1, 1, 1, 0, 0])
    assert fallback["forecast_method"] == "even" and fallback["forecast_kopecks"] == 15_000


def test_forecast_not_below_spent():
    profile = [WEEKDAY] * 5 + [WEEKEND] * 2
    # 30 ноября: прошло 29 дней по «обычному» темпу, а сегодня уже потратили очень много.
    pace = budget_pace(10**6, 900_000, 110_000, date(2026, 11, 30), profile=profile)
    assert pace["forecast_kopecks"] == 900_000


def test_budget_pace_first_day_uses_history():
    profile = [WEEKDAY] * 5 + [WEEKEND] * 2
    pace = budget_pace(100_000, 0, 0, date(2026, 11, 1), profile=profile)
    assert pace["forecast_kopecks"] == 114_000 and pace["status"] == "over"
    # Без профиля в первый день прогноза нет, как раньше.
    assert budget_pace(100_000, 0, 0, date(2026, 11, 1))["forecast_kopecks"] is None
