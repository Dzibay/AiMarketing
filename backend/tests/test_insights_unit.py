"""Сигналы пространства без БД: затухание, спады логина, серии копий, «0 ₽», выжимка для ИИ."""

from datetime import date, datetime, time, timedelta

import pytest

from app import insights
from app.clock import MSK
from app.insights import (
    SCHEME_FACT,
    build_signals,
    change_text,
    copy_base_name,
    copy_groups,
    decay_info,
    drop_cluster,
    launch_date,
    paid_goal_ids,
    signals_digest,
    strategy_text,
    winner_reasons,
)
from app.overview import rub
from app.strategy_info import strategy_brief
from app.yandex import YandexError

TODAY = date(2026, 10, 27)
DATE_TO = TODAY - timedelta(days=1)
DATE_FROM = DATE_TO - timedelta(days=29)
DAYS = [DATE_FROM + timedelta(days=i) for i in range(30)]
ORDER, CALL = 5001, 5002
NAMES = {ORDER: "Заказ", CALL: "Звонок"}


# --- Чистые расчёты --------------------------------------------------------------------------


def test_decay_finds_peak_and_drop_day():
    found = decay_info([100] * 15 + [5] * 15)
    assert found == {"peak_index": 0, "drop_index": 15, "peak_week": 700, "last_week": 35, "drop_pct": 95}


def test_decay_peak_after_ramp_up():
    found = decay_info([0] * 5 + [100] * 10 + [10] * 15)
    assert (found["peak_index"], found["drop_index"], found["drop_pct"]) == (5, 15, 90)


@pytest.mark.parametrize(
    "series",
    [
        [100] * 13,  # меньше двух недель — сравнивать не с чем
        [100] * 30,
        [10] * 15 + [0] * 15,  # пик меньше 100 показов в неделю — шум
        [100] * 25 + [20, 100, 100, 100, 100],  # провал на один день
        [0] * 25 + [100] * 5,  # только запустилась
    ],
)
def test_no_decay(series):
    assert decay_info(series) is None


def test_drop_cluster():
    day = date(2026, 10, 10)
    drops = [day + timedelta(days=9), day, day + timedelta(days=2), day + timedelta(days=1)]
    assert drop_cluster(drops) == (day, 3)
    assert drop_cluster([]) == (None, 0)


@pytest.mark.parametrize(
    ("name", "base"),
    [
        ("Ремонт (копия)", "ремонт"),
        ("Ремонт (копия) (копия)", "ремонт"),
        ("Копия «Ремонт»", "ремонт"),
        ("Copy of Remont", "remont"),
        ("Ремонт - копия 2", "ремонт"),
        ("Ремонт [копия]", "ремонт"),
        ("Ремонт (2)", "ремонт"),
        ("Квартиры 2", "квартиры 2"),  # голое число — скорее отдельная кампания
        ("Москопия", "москопия"),
        ("Ёлки  в   Москве", "елки в москве"),
    ],
)
def test_copy_base_name(name, base):
    assert copy_base_name(name) == base


def test_copy_groups_by_name_and_source():
    campaigns = [
        {"id": 1, "campaign_id": 300, "name": "Ремонт (копия)", "source_row_id": None},
        {"id": 2, "campaign_id": 100, "name": "Ремонт", "source_row_id": None},
        {"id": 3, "campaign_id": 500, "name": "Ванные под ключ", "source_row_id": 2},  # копия сервисом, переименована
        {"id": 4, "campaign_id": 200, "name": "Окна", "source_row_id": None},
        {"id": 5, "campaign_id": 600, "name": "Окна 2", "source_row_id": None},
    ]
    assert [[c["id"] for c in g] for g in copy_groups(campaigns)] == [[2, 1, 3]]


def test_launch_date():
    start = date(2026, 9, 1)
    assert launch_date(start, DAYS[12], DATE_FROM) == DAYS[12]  # копия с датой старта оригинала
    assert launch_date(start, DATE_FROM, DATE_FROM) == start  # шла и до периода
    assert launch_date(DAYS[10], DAYS[12], DATE_FROM) == DAYS[10]  # пара дней модерации — не сдвиг
    assert launch_date(None, DAYS[3], DATE_FROM) == DAYS[3]
    assert launch_date(None, None, DATE_FROM) is None


def test_paid_goal_ids_expands_key_goals():
    assert paid_goal_ids({"paid_goal_ids": [13]}, [ORDER, CALL]) == [ORDER, CALL]
    assert paid_goal_ids({"paid_goal_ids": [ORDER, 13]}, [ORDER, CALL]) == [ORDER, CALL]
    assert paid_goal_ids({"paid_goal_ids": [0, 12]}, [ORDER]) == []  # служебные цели — не цели Метрики
    assert paid_goal_ids({}, [ORDER]) == []


def test_winner_reasons():
    campaigns = [
        {"id": 1, "clicks": 0, "conversions": 4, "cost_kopecks": 0},
        {"id": 2, "clicks": 100, "conversions": 20, "cost_kopecks": 500_000},
        {"id": 3, "clicks": 1000, "conversions": 10, "cost_kopecks": 900_000},
        {"id": 4, "clicks": 50, "conversions": 1, "cost_kopecks": 10_000},
        {"id": 5, "clicks": 40, "conversions": 2, "cost_kopecks": 10_000},  # CR высокий, но конверсий мало
    ]
    assert winner_reasons(campaigns) == {1: ["free"], 2: ["many", "high_cr"]}


def test_strategy_text_with_period_budget():
    brief = strategy_brief(
        {
            "Search": {
                "BiddingStrategyType": "PAY_FOR_CONVERSION",
                "PayForConversion": {
                    "Cpa": 500_000_000,
                    "GoalId": ORDER,
                    "CustomPeriodBudget": {"SpendLimit": 50_000_000_000, "StartDate": "2026-11-01"},
                },
            },
            "Network": {"BiddingStrategyType": "SERVING_OFF"},
        }
    )
    assert brief["paid_goal_ids"] == [ORDER]
    assert strategy_text(brief, NAMES) == "Оплата за конверсии (цель 5001 «Заказ», CPA 500 ₽, на период 50 000 ₽)"
    assert strategy_text({}) == "—"


def test_change_text():
    assert change_text("state", "ON", "SUSPENDED") == "состояние: идёт → остановлена"
    assert change_text("priority_goals", [], [{"goal_id": ORDER}], NAMES) == "ключевые цели: нет → 5001 «Заказ»"
    assert change_text("daily_budget_kopecks", None, 100_000) == f"дневной бюджет: нет → {rub(100_000)}"


@pytest.mark.parametrize(
    ("n", "text"),
    [(0, "0 кампаний"), (1, "1 кампания"), (3, "3 кампании"), (11, "11 кампаний"), (21, "21 кампания"), (1000, "1 000 кампаний")],
)
def test_campaigns_plural(n, text):
    assert insights._campaigns(n) == text


# --- Сценарий: два логина, схема «0 ₽», затухание, серия копий -------------------------------


def _msk(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=MSK)


def _pay_for_order() -> dict:
    return {
        "Search": {
            "BiddingStrategyType": "PAY_FOR_CONVERSION",
            "PayForConversion": {"Cpa": 500_000_000, "GoalId": ORDER, "WeeklySpendLimit": 3_000_000_000},
        },
        "Network": {"BiddingStrategyType": "SERVING_OFF"},
    }


MAX_CLICKS = {
    "Search": {"BiddingStrategyType": "WB_MAXIMUM_CLICKS", "WbMaximumClicks": {"WeeklySpendLimit": 7_000_000_000}},
    "Network": {"BiddingStrategyType": "SERVING_OFF"},
}


def _campaign(row_id, campaign_id, name, *, account=1, state="ON", bidding=MAX_CLICKS, priority=(), start=date(2026, 9, 1), source=None):
    return {
        "id": row_id,
        "account_id": account,
        "campaign_id": campaign_id,
        "name": name,
        "campaign_type": "TEXT_CAMPAIGN",
        "state": state,
        "status": "ACCEPTED",
        "daily_budget_kopecks": None,
        "start_date": start,
        "bidding_strategy": bidding,
        "priority_goals": [{"goal_id": g, "value_rub": None} for g in priority],
        "attribution_model": "AUTO",
        "source_row_id": source,
    }


def _stats(account, campaign_id, impressions, *, clicks=0, cost=0, conversions=()):
    """Ряды 'direct' на 30 дней: clicks и cost — число или список по дням (только в дни с показами),
    conversions — номера дней, в которые была конверсия."""

    def at(value, i):
        return value[i] if isinstance(value, list) else value

    return [
        {
            "account_id": account,
            "campaign_id": campaign_id,
            "day": day,
            "impressions": shows,
            "clicks": at(clicks, i) if shows else 0,
            "cost_kopecks": at(cost, i) if shows else 0,
            "conversions": 1 if i in conversions else 0,
            "revenue_kopecks": 0,
        }
        for i, (day, shows) in enumerate(zip(DAYS, impressions))
    ]


def _goals(account, campaign_id, goal_id, days):
    return [
        {"account_id": account, "campaign_id": campaign_id, "day": DAYS[i], "goal_id": goal_id, "conversions": 1, "revenue_kopecks": 0}
        for i in days
    ]


def _scenario(facts=()) -> dict:
    a, b = 1, 2
    return {
        "workspace": {"id": 7, "name": "Ремонт", "goal_id": ORDER, "ai_facts": list(facts)},
        "campaigns": [
            # Схема «0 ₽»: платим за заказ, а звонки идут бесплатно. Первую копию остановили, когда затухла.
            _campaign(1, 101, "Ванные", state="SUSPENDED", bidding=_pay_for_order()),
            _campaign(2, 102, "Ванные (копия)", bidding=_pay_for_order(), priority=(ORDER, CALL), source=1),
            _campaign(3, 103, "Кухни", start=date(2026, 8, 1)),
            _campaign(4, 104, "Окна"),
            _campaign(5, 105, "Двери"),
            _campaign(9, 109, "Старая", state="ARCHIVED"),
            # Второй логин: три кампании затухли за три дня подряд.
            _campaign(6, 201, "Б-1", account=b),
            _campaign(7, 202, "Б-2", account=b),
            _campaign(8, 203, "Б-3", account=b, bidding={}),  # стратегия ещё не загружена
        ],
        "accounts": [
            {
                "id": a,
                "name": "Клиент А",
                "client_login": "client-a",
                "balance_kopecks": 1_000_000,
                "balance_currency": "RUB",
                "stats_synced_at": _msk(TODAY, 10),
                "stats_error": "",
                "stats_stage": {"goal_ids": [ORDER, CALL]},
            },
            {
                "id": b,
                "name": "Клиент Б",
                "client_login": "client-b",
                "balance_kopecks": None,
                "balance_currency": "",
                "stats_synced_at": _msk(date(2026, 10, 24), 10),  # статистика только по 23.10
                "stats_error": "",
                "stats_stage": {"goal_ids": []},
            },
        ],
        "stats": [
            *_stats(a, 101, [100] * 12 + [0] * 18, clicks=5, conversions=(0, 3, 6, 9)),
            *_stats(a, 102, [0] * 12 + [100] * 18, clicks=5, conversions=(12, 15, 18, 21, 24, 27)),
            *_stats(
                a, 103, [200] * 20 + [10] * 10,
                clicks=[10] * 20 + [1] * 10, cost=[5000] * 20 + [500] * 10, conversions=(0, 5, 10, 15),
            ),
            *_stats(a, 104, [5] * 10 + [0] * 20),
            *_stats(a, 105, [300] * 30, clicks=20, cost=20_000, conversions=range(30)),
            *_stats(b, 201, [100] * 20 + [0] * 10, cost=1000),
            *_stats(b, 202, [100] * 21 + [0] * 9, cost=1000),
            *_stats(b, 203, [100] * 22 + [0] * 8, cost=1000),
        ],
        "goal_stats": [*_goals(a, 101, CALL, (0, 3, 6, 9)), *_goals(a, 102, CALL, (12, 15, 18, 21, 24, 27))],
        "changes": [
            {"campaign_row_id": 1, "field": "state", "old_value": "ON", "new_value": "SUSPENDED", "changed_at": _msk(DAYS[12], 9)},
            {"campaign_row_id": 3, "field": "name", "old_value": "Кухня", "new_value": "Кухни", "changed_at": _msk(DAYS[2], 10)},
            {
                "campaign_row_id": 3,
                "field": "daily_budget_kopecks",
                "old_value": 500_000,
                "new_value": 100_000,
                "changed_at": _msk(DAYS[19], 15),
            },
        ],
        "actions": [
            {
                "id": 1, "action": "duplicate", "title": "Копия кампании «Ванные»", "source": "user", "status": "ok",
                "created_at": _msk(DAYS[11], 18), "targets": ["campaign:1", "campaign:2"],
            },
            {
                "id": 2, "action": "add_negatives", "title": "Минус-слова: +12", "source": "ai", "status": "ok",
                "created_at": _msk(DAYS[18], 12), "targets": ["campaign:3"],
            },
        ],
        "goal_names": dict(NAMES),
    }


def _signals(**kwargs) -> dict:
    return build_signals(_scenario(**kwargs), date_from=DATE_FROM, date_to=DATE_TO)


def test_lists():
    signals = _signals()
    assert signals["free"] == [2, 1]  # архивная и платные — не в списке
    assert signals["decaying"] == [3, 6, 7, 8]  # остановленная копия затухла, но в работе её нет
    assert signals["idle"] == [4]
    assert signals["winners"] == [5, 2, 1]
    assert signals["totals"]["campaigns"] == 8
    assert signals["totals"]["active"] == 7


def test_zero_cost_scheme():
    scheme = _signals()["scheme"]
    assert (scheme["campaigns"], scheme["conversions"], scheme["pay_for_conversion"]) == (2, 10, 2)
    assert scheme["strategies"] == [("Оплата за конверсии", 2)]
    assert scheme["goals"] == [
        {"goal_id": ORDER, "label": "5001 «Заказ»", "conversions": 0, "paid_in": 2, "key_in": 1},
        {"goal_id": CALL, "label": "5002 «Звонок»", "conversions": 10, "paid_in": 0, "key_in": 1},
    ]
    assert (scheme["free_traffic"], scheme["paid_campaigns"], scheme["paid_conversions"]) == (3, 5, 34)


def test_campaign_info():
    campaigns = _signals()["campaigns"]
    copy = campaigns[2]
    assert copy["launch_date"] == "2026-10-09"  # дата старта осталась от оригинала
    assert copy["age_days"] == 18
    assert copy["paid_conversions"] == 0  # заказов не было — все конверсии бесплатные
    assert copy["goal_conversions"] == {CALL: 6}
    assert copy["strategy"] == "Оплата за конверсии (цель 5001 «Заказ», CPA 500 ₽, неделя 3 000 ₽)"
    assert copy["events"] == [{"day": "2026-10-08", "text": "вы: Копия кампании «Ванные»"}]
    assert campaigns[3]["paid_conversions"] is None  # оплачиваемых целей нет
    assert campaigns[8]["strategy_known"] is False


def test_decay_with_events_near_drop():
    decay = _signals()["campaigns"][3]["decay"]
    assert (decay["peak_from"], decay["drop_day"], decay["drop_pct"], decay["age_at_drop"]) == (
        "2026-09-27",
        "2026-10-17",
        95,
        78,
    )
    assert decay["peak"] == {"impressions": 1400, "clicks": 70, "cost_kopecks": 35_000, "conversions": 2, "revenue_kopecks": 0}
    assert decay["last"]["impressions"] == 70
    # Переименование за две недели до спада — не причина; минус-слова и бюджет — рядом.
    assert decay["events"] == [
        {"day": "2026-10-15", "text": "ИИ: Минус-слова: +12"},
        {"day": "2026-10-16", "text": f"дневной бюджет: {rub(500_000)} → {rub(100_000)}"},
    ]


def test_paid_conversions_by_window():
    decay = _signals()["campaigns"][1]["decay"]
    assert decay["drop_day"] == "2026-10-09"
    assert decay["peak"]["paid_conversions"] == 0
    assert decay["events"] == [
        {"day": "2026-10-08", "text": "вы: Копия кампании «Ванные»"},
        {"day": "2026-10-09", "text": "состояние: идёт → остановлена"},
    ]


def test_logins():
    first, second = _signals()["logins"]
    assert first["name"] == "Клиент А"
    assert first["decay"] is None and first["cluster"] is None
    assert (first["active"], first["decaying"], first["stats_to"]) == (4, 1, "2026-10-26")
    assert first["days_on_balance"] == 48.8  # 10 000 ₽ при расходе 1 435 ₽ за последнюю неделю
    assert second["stats_to"] == "2026-10-23"
    assert second["decay"] == {"drop_day": "2026-10-18", "drop_pct": 86, "peak_week": 2100, "last_week": 300}
    assert second["cluster"] == {"day": "2026-10-17", "campaigns": 3}
    assert second["days_on_balance"] is None


def test_copies():
    assert _signals()["copies"] == [
        {"name": "Ванные", "members": [1, 2], "active": 1, "restart_every_days": 38, "lifetime_days": 39}
    ]


def test_notes_and_suggestions():
    signals = _signals()
    assert signals["notes"] == [
        "Логин Клиент Б: статистика только по 23.10",
        "Стратегия загружена не целиком: 1 кампания — обновите кабинет",
    ]
    cards = {c["kind"]: c for c in signals["suggestions"]}
    assert list(cards) == ["scheme", "login_decay", "decay", "copies", "idle", "winners", "refresh"]
    assert cards["scheme"]["title"] == "Конверсии за 0 ₽: 2 кампании"
    assert cards["scheme"]["prompt"].startswith("Разбери, как устроена схема")
    assert cards["scheme"]["campaign_row_ids"] == [2, 1]
    assert cards["login_decay"]["account_id"] == 2
    assert cards["login_decay"]["title"] == "Логин Клиент Б: показы упали на 86%"
    assert cards["decay"]["title"] == "Затухание показов: 4 кампании"
    assert cards["copies"]["detail"].startswith("Копии затухают примерно на 39-й день")
    assert cards["refresh"]["prompt"] == ""


def test_known_scheme_is_not_asked_again():
    fact = {"key": SCHEME_FACT, "question": "Как устроена схема 0 ₽?", "answer": "Платим только за заказ", "at": "2026-10-20"}
    signals = _signals(facts=[fact])
    scheme = next(c for c in signals["suggestions"] if c["kind"] == "scheme")
    assert scheme["prompt"] == "Проверь, работает ли схема конверсий за 0 ₽ сейчас"
    assert signals["facts"] == [fact]
    # Ответы маркетолога идут в системные правила чата — в выжимке их не дублируем.
    assert "Платим только за заказ" not in signals_digest(signals)


def test_digest_sections():
    digest = signals_digest(_signals())
    for section in (
        "Итого: 8 кампаний (7 идут)",
        "Главная цель пространства: 5001 «Заказ».",
        "КОНВЕРСИИ ЗА 0 ₽: 2 кампании",
        "ЛОГИНЫ:",
        "ЗАТУХАНИЕ",
        "рядом со спадом: 15.10 ИИ: Минус-слова: +12",
        "ВКЛЮЧЕНЫ, НО НЕДЕЛЮ БЕЗ ПОКАЗОВ:",
        "СЕРИИ ПЕРЕЗАПУСКОВ КОПИЯМИ",
        "[1] с 01.09 остановлена, спад 09.10",
        "УСПЕШНЫЕ",
        "ДАННЫЕ:",
    ):
        assert section in digest
    assert "«Старая»" not in digest  # архивная
    assert len(digest) < insights.DIGEST_LIMIT


def test_never_synced_login():
    data = _scenario()
    data["accounts"][1]["stats_synced_at"] = None
    signals = build_signals(data, date_from=DATE_FROM, date_to=DATE_TO)
    assert signals["decaying"] == [3]
    assert "Логин Клиент Б: статистика ещё не загружена" in signals["notes"]
    assert all(c["kind"] != "login_decay" for c in signals["suggestions"])
    assert signals["campaigns"][6]["has_stats"] is False


def test_empty_workspace():
    data = {
        "workspace": {"id": 7, "name": "Пусто", "goal_id": None, "ai_facts": []},
        "campaigns": [],
        "accounts": [],
        "stats": [],
        "goal_stats": [],
        "changes": [],
        "actions": [],
        "goal_names": {},
    }
    signals = build_signals(data, date_from=DATE_FROM, date_to=DATE_TO)
    assert signals["suggestions"] == []
    assert "Итого: 0 кампаний" in signals_digest(signals)


def test_get_signals_and_history(monkeypatch):
    monkeypatch.setattr(insights, "_load", lambda user_id, ws_id, date_from, date_to: _scenario())
    signals = insights.get_signals(1, 7, today=TODAY)
    assert signals["period"] == {"date_from": "2026-09-27", "date_to": "2026-10-26", "days": 30}

    history = insights.campaign_history(1, 7, 2, today=TODAY)
    assert history["campaign"]["launch_date"] == "2026-10-09"
    assert history["stats_to"] == "2026-10-26"
    assert history["decay"] is None
    assert len(history["days"]) == 30
    assert history["days"][12] == {
        "day": "2026-10-09",
        "impressions": 100,
        "clicks": 5,
        "cost_rub": 0.0,
        "conversions": 1,
        "goals": {"5002 «Звонок»": 1},
    }
    assert history["events"] == [{"day": "2026-10-08", "text": "вы: Копия кампании «Ванные»"}]
    with pytest.raises(YandexError):
        insights.campaign_history(1, 7, 99, today=TODAY)
