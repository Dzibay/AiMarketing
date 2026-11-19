"""Красные точки срочного у проектов (I28): заголовок из уведомления и «скрыто после уведомления»."""

from datetime import datetime, timedelta, timezone

from app import urgent

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
SENT = NOW - timedelta(hours=2)


def _s(mode, kind, *, key="", scope="", created=None, until=None):
    return {"mode": mode, "kind": kind, "problem_key": key, "scope": scope, "created_at": created or NOW, "until": until}


def test_headline_of_takes_text_after_place():
    assert urgent.headline_of("ООО Ромашка · Окна: деньги на исходе") == "деньги на исходе"
    assert urgent.headline_of("Окна: отклонено 2 объявления") == "отклонено 2 объявления"
    assert urgent.headline_of("") == ""


def test_snoozed_after_matches_key_kind_and_time():
    rows = [_s("days", "no_conversions", key="no_conversions:c5", until=NOW + timedelta(days=7))]
    assert urgent.snoozed_after("noconv:c5", SENT, [1], rows, {1: None}, NOW)
    # Другая кампания или другой тип — не прячет.
    assert not urgent.snoozed_after("noconv:c6", SENT, [1], rows, {1: None}, NOW)
    assert not urgent.snoozed_after("cpa:c5", SENT, [1], rows, {1: None}, NOW)
    # Скрыто раньше уведомления — проверка всё равно сообщила, значит, стало хуже.
    old = [_s("days", "no_conversions", key="no_conversions:c5", created=SENT - timedelta(hours=1))]
    assert not urgent.snoozed_after("noconv:c5", SENT, [1], old, {1: None}, NOW)
    # Истёкшее «на 7 дней» не прячет.
    expired = [_s("days", "no_conversions", key="no_conversions:c5", until=NOW - timedelta(minutes=1))]
    assert not urgent.snoozed_after("noconv:c5", SENT, [1], expired, {1: None}, NOW)


def test_snoozed_after_type_scope_and_moderation_kinds():
    by_client = [_s("type", "balance", scope="client:9")]
    assert urgent.snoozed_after("money:a3", SENT, [1, 2], by_client, {1: 9, 2: 9}, NOW)
    # Логин у проектов разных клиентов — типом не скрыть.
    assert not urgent.snoozed_after("money:a3", SENT, [1, 2], by_client, {1: 9, 2: None}, NOW)
    by_ws = [_s("type", "stopped", scope="ws:4")]
    assert urgent.snoozed_after("stopped:c8", SENT, [4], by_ws, {4: None}, NOW)
    # Итог модерации прячется и как «Модерация кампании», и как «Отклонённые объявления».
    ads = [_s("intended", "ads_rejected", key="ads_rejected:c8")]
    assert urgent.snoozed_after("mod:c8:77", SENT, [4], ads, {4: None}, NOW)
    assert not urgent.snoozed_after("unknown:c8", SENT, [4], ads, {4: None}, NOW)


def test_signal_thread_joins_moderation_records_of_one_campaign():
    assert urgent.signal_thread("rejected:c5") == urgent.signal_thread("mod:c5:77") == "mod:c5"
    assert urgent.signal_thread("stopped:c5") == "stopped:c5"
    assert urgent.signal_thread("money:a3") == "money:a3"


def test_still_burning_checks_current_state():
    low = {"balance_kopecks": 100000, "spend_per_day_kopecks": 100000, "days_on_balance": 1.0}
    topped = {"balance_kopecks": 1000000, "spend_per_day_kopecks": 100000, "days_on_balance": 10.0}
    assert urgent.still_burning("money:a3", low, None)
    assert not urgent.still_burning("money:a3", topped, None)
    assert urgent.still_burning("money:a3", {"balance_kopecks": None}, None)  # не знаем — горит
    assert urgent.still_burning("rejected:c5", None, {"state": "ON", "status": "REJECTED"})
    assert not urgent.still_burning("rejected:c5", None, {"state": "ON", "status": "ACCEPTED"})
    assert urgent.still_burning("mod:c5:7", None, {"state": "ON", "status": "ACCEPTED", "rejected_ads": True})
    assert not urgent.still_burning("mod:c5:7", None, {"state": "ON", "status": "ACCEPTED", "rejected_ads": False})
    assert urgent.still_burning("stopped:c5", None, {"state": "OFF", "status": "ACCEPTED"})
    assert not urgent.still_burning("stopped:c5", None, {"state": "ON", "status": "ACCEPTED"})
    assert not urgent.still_burning("stopped:c5", None, {"state": "ARCHIVED", "status": "ACCEPTED"})
    assert urgent.still_burning("stopped:c5", None, None)  # строки нет — не гасим
    assert urgent.still_burning("noconv:c5", None, {"state": "ON", "status": "ACCEPTED"})
