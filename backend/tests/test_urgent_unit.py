"""Срочное: тихие часы, расход без заявок, подорожавшие заявки, итог модерации — на выдуманных данных и времени."""

from datetime import date, datetime, timedelta, timezone

from app import urgent
from app.clock import MSK

YESTERDAY = date(2026, 11, 18)


def _history(base_days=30, base_cost=200_000, base_conv=2, start=1, **days):
    """По base_cost / base_conv в каждый из base_days дней до вчера (с отступом start) плюс заданные дни."""
    h = {YESTERDAY - timedelta(days=i): (base_cost, base_conv) for i in range(start, start + base_days)}
    for offset, value in days.items():
        h[YESTERDAY - timedelta(days=int(offset[1:]))] = value
    return h


def test_quiet_hours_moscow():
    def at(h, m=0):
        return datetime(2026, 11, 19, h, m, tzinfo=MSK)

    assert urgent.quiet(at(22)) and urgent.quiet(at(23, 59)) and urgent.quiet(at(0)) and urgent.quiet(at(8, 59))
    assert not urgent.quiet(at(9)) and not urgent.quiet(at(21, 59))
    # 19:30 UTC — это 22:30 по Москве.
    assert urgent.quiet(datetime(2026, 11, 19, 19, 30, tzinfo=timezone.utc))


def test_no_conversions_uses_own_cpa():
    # База: 2000 ₽ и 2 заявки в день → обычная заявка 1000 ₽, порог max(1500 ₽, 500 ₽) = 1500 ₽.
    h = _history(d0=(150_000, 0))
    found = urgent.no_conv_event(h, YESTERDAY)
    assert found == {"cost": 150_000, "cpa": 100_000, "threshold": 150_000}
    assert urgent.no_conv_event(_history(d0=(149_000, 0)), YESTERDAY) is None
    assert urgent.no_conv_event(_history(d0=(500_000, 1)), YESTERDAY) is None  # заявка была
    assert urgent.no_conv_event(_history(), YESTERDAY) is None  # вчера ничего не тратили


def test_no_conversions_floor_and_target():
    # Дешёвая заявка 100 ₽: полтора раза — 150 ₽, но меньше 500 ₽ не тревожим.
    cheap = _history(base_cost=20_000, d0=(40_000, 0))
    assert urgent.no_conv_event(cheap, YESTERDAY) is None
    assert urgent.no_conv_event(_history(base_cost=20_000, d0=(50_000, 0)), YESTERDAY)["threshold"] == 50_000
    # Своих заявок меньше трёх — берём цель проекта; нет цели — молчим.
    few = _history(base_days=2, base_conv=1, d0=(300_000, 0))
    assert urgent.no_conv_event(few, YESTERDAY) is None
    assert urgent.no_conv_event(few, YESTERDAY, target_cpa_kopecks=150_000)["threshold"] == 225_000
    assert urgent.no_conv_event(few, YESTERDAY, target_cpa_kopecks=250_000) is None


def test_cpa_spike_three_days_against_thirty():
    # База (дни 3..32): 2000 ₽ / 2 заявки → 1000 ₽. За 3 дня: 9000 ₽ / 4 заявки → 2250 ₽ (в 2,25 раза).
    h = _history(start=3, d0=(3_000_00, 1), d1=(3_000_00, 2), d2=(3_000_00, 1))
    found = urgent.cpa_spike_event(h, YESTERDAY)
    assert found == {"cpa": 225_000, "base_cpa": 100_000, "ratio": 2.2, "conversions": 4}
    # В 1,8 раза — ещё не всплеск.
    calm = _history(start=3, d0=(240_000, 1), d1=(240_000, 2), d2=(240_000, 1))
    assert urgent.cpa_spike_event(calm, YESTERDAY) is None
    # Без заявок за 3 дня — это «расход без заявок», а не рост цены.
    assert urgent.cpa_spike_event(_history(start=3, d0=(900_000, 0)), YESTERDAY) is None
    # Мало заявок в базе — сравнивать не с чем.
    thin = _history(start=3, base_days=2, base_conv=1, d0=(900_000, 1))
    assert urgent.cpa_spike_event(thin, YESTERDAY) is None


def test_cpa_spike_needs_floor_spend():
    h = _history(start=3, base_cost=2_000, base_conv=1, d0=(10_000, 1))  # заявка 100 ₽ при обычных 20 ₽
    assert urgent.cpa_spike_event(h, YESTERDAY) is None


SENT = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
LATER = SENT + timedelta(hours=5)
EARLIER = SENT - timedelta(hours=1)


def _camp(**kw):
    return {"status": "ACCEPTED", "state": "SUSPENDED", "synced_at": LATER, "status_clarification": "", **kw}


def _ad(status="ACCEPTED", synced=LATER, **kw):
    return {"status": status, "state": "ON", "synced_at": synced, "title": "Окна", "reason": "", "extras": [], **kw}


def test_moderation_pending_until_data_is_fresh():
    assert urgent.moderation_outcome(_camp(status="MODERATION"), [_ad()], SENT) is None
    assert urgent.moderation_outcome(_camp(synced_at=EARLIER), [_ad()], SENT) is None
    assert urgent.moderation_outcome(_camp(), [_ad(status="MODERATION")], SENT) is None
    assert urgent.moderation_outcome(_camp(), [_ad(status="PREACCEPTED")], SENT) is None
    assert urgent.moderation_outcome(_camp(), [_ad(status="DRAFT", synced=EARLIER)], SENT) is None
    assert urgent.moderation_outcome(_camp(), [_ad(synced=EARLIER)], SENT) is None  # объявления не перечитаны


def test_moderation_accepted_and_rejected():
    assert urgent.moderation_outcome(_camp(), [_ad()], SENT) == {"result": "accepted", "running": False}
    assert urgent.moderation_outcome(_camp(state="ON"), [_ad()], SENT)["running"] is True
    bad = _ad(status="REJECTED", reason="Нет лицензии")
    out = urgent.moderation_outcome(_camp(), [_ad(), bad, _ad(state="ARCHIVED", status="REJECTED")], SENT)
    assert out["result"] == "rejected" and out["count"] == 1 and out["rows"] == [bad]
    whole = urgent.moderation_outcome(_camp(status="REJECTED", status_clarification="Запрещённый товар"), [], SENT)
    assert whole == {"result": "rejected", "count": 0, "rows": [], "reason": "Запрещённый товар"}


def test_moderation_waits_for_final_status_from_direct():
    """synced_at двигают и правки из сервиса — итог только по окончательному статусу каждого отправленного."""
    # Объявление создал сервис (статус пустой), кампанию правили после отправки — модерация ещё идёт.
    assert urgent.moderation_outcome(_camp(status=""), [_ad(status="")], SENT) is None
    assert urgent.moderation_outcome(_camp(), [_ad(), _ad(status="")], SENT) is None
    # Текст или дополнения правили после отправки: строка «свежая», но статус — от самой отправки.
    assert urgent.moderation_outcome(_camp(status=""), [_ad(status="MODERATION")], SENT) is None
    assert urgent.moderation_outcome(_camp(), [_ad(status="UNKNOWN")], SENT) is None
    # Одного перечитанного мало — нужны все отправленные.
    assert urgent.moderation_outcome(_camp(), [_ad(), _ad(synced=EARLIER)], SENT) is None
    assert urgent.moderation_outcome(_camp(), [_ad(status="REJECTED", synced=EARLIER)], SENT) is None
    # Черновик, перечитанный после отправки, на модерацию не ушёл: итог по остальным, а одни черновики — не итог.
    assert urgent.moderation_outcome(_camp(), [_ad(), _ad(status="DRAFT")], SENT)["result"] == "accepted"
    assert urgent.moderation_outcome(_camp(), [_ad(status="DRAFT")], SENT) is None


def test_naive_timestamps_are_utc():
    naive = SENT.replace(tzinfo=None) + timedelta(hours=1)
    assert urgent.moderation_outcome(_camp(synced_at=naive), [_ad(synced=naive)], SENT)["result"] == "accepted"


def test_money_critical():
    assert urgent.money_critical({"balance_kopecks": 0, "spend_per_day_kopecks": 1000, "days_on_balance": 0})
    assert urgent.money_critical({"balance_kopecks": 1500, "spend_per_day_kopecks": 1000, "days_on_balance": 1.5})
    assert not urgent.money_critical({"balance_kopecks": 5000, "spend_per_day_kopecks": 1000, "days_on_balance": 5})
    assert not urgent.money_critical({"balance_kopecks": 0, "spend_per_day_kopecks": 0, "days_on_balance": None})
    assert not urgent.money_critical({"balance_kopecks": None, "spend_per_day_kopecks": 1000})


def test_where_and_object_of():
    assert urgent.where_of({"workspace_name": "Окна", "client_name": "Иванов"}) == "Иванов · Окна"
    assert urgent.where_of({"workspace_name": "Окна", "client_name": "Окна"}) == "Окна"
    assert urgent.where_of({}) == "Без проекта"
    assert urgent.object_of("urgent:money:a7:2026-11-19") == "money:a7"
    assert urgent.object_of("urgent:mod:c5:42:2026-11-19") == "mod:c5:42"


def test_already_sent_once_a_day_and_once_per_moderation():
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    recent = [
        {"dedup_key": "urgent:money:a7:2026-11-18", "created_at": now - timedelta(hours=23)},
        {"dedup_key": "urgent:stopped:c3:2026-11-18", "created_at": now - timedelta(hours=25)},
        {"dedup_key": "urgent:mod:c5:42:2026-11-12", "created_at": now - timedelta(days=7)},
        {"dedup_key": "urgent:mod:c9:50:2026-11-19", "created_at": now - timedelta(hours=2)},
    ]
    assert urgent._already_sent({"object": "money:a7"}, recent, now)
    assert not urgent._already_sent({"object": "stopped:c3"}, recent, now)  # сутки прошли — можно снова
    assert urgent._already_sent({"object": "mod:c5:42"}, recent, now)  # итог модерации — один раз
    assert not urgent._already_sent({"object": "mod:c5:43"}, recent, now)  # новая отправка
    assert urgent._already_sent({"object": "rejected:c9"}, recent, now)  # итог модерации уже сказал
    assert not urgent._already_sent({"object": "rejected:c5"}, recent, now)
