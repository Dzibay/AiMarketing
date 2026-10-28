"""ИИ, третий этап, без БД и модели: эксперименты — определение, лимит в Директе, прогресс, итоги."""

import math
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app import actions, ai, ai_agent, experiments, insights
from app.overview import rub
from app.workspaces import normalize_ai_access
from app.yandex import YandexError

TODAY = date(2026, 10, 28)  # среда: неделя Директа — с 26.10
SYNCED = datetime(2026, 10, 28, 6, 0)  # статистика загружена по 27.10


def _d(day: int) -> date:
    return date(2026, 10, day)


# --- Определение эксперимента -----------------------------------------------------------------

BASE = {
    "title": "Казань",
    "hypothesis": "Схема 0 ₽ повторится в Казани",
    "criterion": "zero_cost",
    "weekly_budget_rub": 3000,
    "campaign_row_ids": [70],
}


def test_definition_defaults_and_cleanup():
    d = experiments.normalize_definition(
        {**BASE, "title": "  Казань:  копия  ", "hypothesis": "Схема\n\n\n  работает  ", "campaign_row_ids": [70, "70", 71]}
    )
    assert d == {
        "title": "Казань: копия",
        "hypothesis": "Схема\n\nработает",
        "kind": "other",
        "campaign_row_ids": [70, 71],
        "source_campaign_row_id": None,
        "weekly_budget_rub": 3000.0,
        "days": 14,
        "criterion": "zero_cost",
        "target": 1.0,
        "criterion_text": "",
    }


def test_definition_targets_by_criterion():
    norm = experiments.normalize_definition
    assert norm({**BASE, "criterion": "conversions"})["target"] == 5.0
    assert norm({**BASE, "criterion": "conversions", "target": 2.2})["target"] == 3.0  # конверсии — целые
    assert norm({**BASE, "criterion": "cr", "target": "3.333"})["target"] == 3.33
    custom = norm({**BASE, "criterion": "custom", "target": 7, "criterion_text": " заявки  из Казани "})
    assert (custom["target"], custom["criterion_text"]) == (None, "заявки из Казани")


def test_definition_other_fields():
    d = experiments.normalize_definition(
        {**BASE, "kind": " GEO ", "days": "30", "weekly_budget_rub": "3000.5", "source_campaign_row_id": "7"}
    )
    assert (d["kind"], d["days"], d["weekly_budget_rub"], d["source_campaign_row_id"]) == ("geo", 30, 3000.5, 7)
    assert experiments.normalize_definition({**BASE, "source_campaign_row_id": 0})["source_campaign_row_id"] is None


@pytest.mark.parametrize(
    "patch, text",
    [
        ({"title": "  "}, "Укажите title — короткое название эксперимента"),
        ({"hypothesis": "\n \n"}, "Укажите hypothesis — что проверяем и на каких данных это основано"),
        ({"kind": "bogus"}, f"kind — одно из {list(experiments.KINDS)}"),
        ({"criterion": ""}, f"criterion — одно из {list(experiments.CRITERIA)}"),
        ({"criterion": "cr"}, "Для criterion=cr укажите target — CR в процентах, больше 0 и не больше 100"),
        ({"criterion": "cr", "target": 0}, "Для criterion=cr укажите target — CR в процентах, больше 0 и не больше 100"),
        ({"criterion": "cr", "target": 120}, "Для criterion=cr укажите target — CR в процентах, больше 0 и не больше 100"),
        ({"criterion": "custom"}, "Для criterion=custom опишите критерий словами в criterion_text"),
        ({"criterion": "conversions", "target": 0}, "target — сколько конверсий нужно за весь срок, не меньше 1"),
        ({"target": "много"}, "target — число"),
        ({"days": 2}, "days — срок теста от 3 до 90 дней"),
        ({"days": 91}, "days — срок теста от 3 до 90 дней"),
        ({"weekly_budget_rub": None}, "Укажите weekly_budget_rub — лимит ₽ в неделю на кампанию из ответа маркетолога"),
        ({"weekly_budget_rub": 299}, "Недельный бюджет в Директе — не меньше 300 ₽"),
        ({"weekly_budget_rub": "nan"}, "weekly_budget_rub — число"),
        ({"campaign_row_ids": []}, "Укажите campaign_row_ids — тестовые кампании (непустой список id)"),
        ({"campaign_row_ids": "70"}, "Укажите campaign_row_ids — тестовые кампании (непустой список id)"),
        ({"campaign_row_ids": ["x"]}, "campaign_row_ids: id должны быть числами"),
        ({"campaign_row_ids": list(range(1, 12))}, "Не больше 10 тестовых кампаний в одном эксперименте"),
    ],
)
def test_definition_errors(patch, text):
    with pytest.raises(YandexError) as exc:
        experiments.normalize_definition({**BASE, **patch})
    assert (exc.value.code, exc.value.message) == ("args", text)


def test_criterion_text_and_goal():
    zero = {"criterion": "zero_cost", "target": 2.0}
    assert experiments.criterion_text(zero) == "конверсии за ~0 ₽ — от 2 конв. за весь срок, не дороже 1 ₽ за конверсию"
    assert experiments.criterion_text({**zero, "criterion_text": "по цели «Заявка»"}).endswith("; по цели «Заявка»")
    assert experiments.criterion_text({"criterion": "conversions", "target": 5.0}) == "от 5 конв. за весь срок"
    assert experiments.criterion_text({"criterion": "cr", "target": 2.5}) == "CR от 2,5% (считаем от 30 кликов)"
    assert experiments.criterion_text({"criterion": "custom", "criterion_text": "заявки из Казани"}) == "заявки из Казани"
    assert experiments.criterion_text({"criterion": "custom"}) == "свой критерий"

    assert experiments._goal_short(zero) == "от 2 конв. за ~0 ₽"
    assert experiments._goal_short({"criterion": "conversions", "target": 5.0}) == "от 5 конв."
    assert experiments._goal_short({"criterion": "cr", "target": 2.5}) == "CR от 2,5%"
    assert experiments._goal_short({"criterion": "custom", "criterion_text": "заявки"}) == "заявки"


def test_normalize_finish():
    assert experiments.normalize_finish(" SUCCESS ", "  Масштабировать \n\n\n на Самару ", False) == (
        "success",
        "Масштабировать\n\nна Самару",
    )
    assert experiments.normalize_finish("bogus", "Передумали", True) == ("", "Передумали")
    assert experiments.normalize_finish("fail", None, True) == ("fail", "")
    with pytest.raises(YandexError) as exc:
        experiments.normalize_finish("", "Вывод", False)
    assert exc.value.message == "verdict — одно из ['success', 'fail', 'unclear']"


# --- Лимит расхода в Директе -------------------------------------------------------------------


def _settings(ctype="UNIFIED_CAMPAIGN", search="WB_MAXIMUM_CONVERSION_RATE", network="SERVING_OFF", daily=None, **extra):
    strategy = {"search": search, "network": network, **extra}
    strategy["manual"] = search == "HIGHEST_POSITION" or (search == "SERVING_OFF" and network == "MAXIMUM_COVERAGE")
    return {"name": "Казань", "type": ctype, "daily_budget_rub": daily, "strategy": strategy}


def test_limit_auto_strategy_gets_weekly_budget():
    plan = experiments.limit_plan(_settings(weekly_budget_rub=10000.0, goal_id=5001), 3000)
    assert plan == {
        "kind": "weekly",
        "before": f"{experiments._rub(10000)}/нед.",
        "after": f"{experiments._rub(3000)}/нед.",
        "changes": {"strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF",
                                 "weekly_budget_rub": 3000, "goal_id": 5001}},
        "note": "",
    }
    no_weekly = experiments.limit_plan(_settings(search="AVERAGE_CPA", average_cpa_rub=500.0, goal_id=5001), 3000)
    assert no_weekly["before"] == "без недельного бюджета"
    assert no_weekly["changes"]["strategy"]["weekly_budget_rub"] == 3000
    networks = experiments.limit_plan(_settings(ctype="TEXT_CAMPAIGN", search="SERVING_OFF", network="WB_MAXIMUM_CLICKS"), 3000)
    assert networks["changes"] == {"strategy": {"search": "SERVING_OFF", "network": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 3000}}


def test_limit_never_raises_a_smaller_budget():
    weekly = experiments.limit_plan(_settings(weekly_budget_rub=2000.0), 3000)
    assert weekly == {"kind": "weekly", "before": f"{experiments._rub(2000)}/нед.",
                      "after": f"{experiments._rub(2000)}/нед. — не больше лимита, не меняем", "changes": {}, "note": ""}
    daily = experiments.limit_plan(_settings(search="HIGHEST_POSITION", daily=300.0), 3000)  # 2 100 ₽ за неделю
    assert daily == {"kind": "daily", "before": f"{experiments._rub(300)}/день",
                     "after": f"{experiments._rub(300)}/день — за неделю не больше лимита, не меняем", "changes": {}, "note": ""}


def test_limit_unified_manual_moves_to_weekly():
    plan = experiments.limit_plan(_settings(search="HIGHEST_POSITION", daily=1000.0), 3000)
    assert plan == {
        "kind": "weekly",
        "before": f"{experiments._rub(1000)}/день",
        "after": f"{experiments._rub(3000)}/нед.",
        "changes": {"strategy": {"search": "HIGHEST_POSITION", "network": "SERVING_OFF", "weekly_budget_rub": 3000}},
        "note": "дневной бюджет снимется: у ручных ставок ЕПК с недельным бюджетом его нет",
    }
    bare = experiments.limit_plan(_settings(search="HIGHEST_POSITION"), 3000)
    assert (bare["before"], bare["note"]) == ("без недельного бюджета", "")


def test_limit_text_manual_uses_daily_budget():
    plan = experiments.limit_plan(_settings(ctype="TEXT_CAMPAIGN", search="HIGHEST_POSITION", daily=1000.0), 3500)
    assert plan == {
        "kind": "daily",
        "before": f"{experiments._rub(1000)}/день",
        "after": f"{experiments._rub(500)}/день",
        "changes": {"daily_budget_rub": 500},
        "note": "у ручных ставок нет недельного бюджета — лимит задан дневным",
    }
    keep = experiments.limit_plan(_settings(ctype="TEXT_CAMPAIGN", search="HIGHEST_POSITION", daily=400.0), 3500)
    assert (keep["changes"], keep["after"]) == ({}, f"{experiments._rub(400)}/день — за неделю не больше лимита, не меняем")
    networks = experiments.limit_plan(_settings(ctype="TEXT_CAMPAIGN", search="SERVING_OFF", network="MAXIMUM_COVERAGE"), 3000)
    assert (networks["before"], networks["changes"]) == ("без дневного бюджета", {"daily_budget_rub": 428})


def test_limit_refusals():
    with pytest.raises(YandexError) as exc:
        experiments.limit_plan(_settings(ctype="TEXT_CAMPAIGN", search="HIGHEST_POSITION", daily=1000.0), 2000)
    assert exc.value.code == "limit"
    assert exc.value.message == (
        f"«Казань»: у ручных ставок нет недельного бюджета, а дневной — от 300 ₽, то есть до {experiments._rub(2100)} в "
        f"неделю: больше лимита. Предложи лимит от {experiments._rub(2100)}/нед., стратегию с недельным бюджетом или "
        "set_limit=false, если маркетолог ограничит расход сам"
    )
    hint = "попроси маркетолога задать недельный бюджет в Директе вручную и запусти эксперимент с set_limit=false"
    with pytest.raises(YandexError) as exc:
        experiments.limit_plan(_settings(ctype="MOBILE_APP_CAMPAIGN"), 3000)
    assert (exc.value.code, exc.value.message) == (
        "unsupported", f"«Казань»: у этого типа кампаний лимит ставится только в Директе — {hint}"
    )
    with pytest.raises(YandexError) as exc:
        experiments.limit_plan(_settings(ctype="TEXT_CAMPAIGN", search="AVERAGE_CRR"), 3000)
    assert (exc.value.code, exc.value.message) == (
        "strategy", f"«Казань»: стратегию «Доля рекламных расходов» сервис не меняет — {hint}"
    )
    with pytest.raises(YandexError) as exc:
        experiments.limit_plan({"type": "TEXT_CAMPAIGN", "strategy": {}}, 3000)
    assert exc.value.message == f"стратегию «не загружена» сервис не меняет — {hint}"


SETTINGS = {
    70: {"type": "UNIFIED_CAMPAIGN", "daily_budget_rub": None,
         "strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF", "weekly_budget_rub": 10000.0,
                      "goal_id": 5001, "manual": False}},
    71: {"type": "TEXT_CAMPAIGN", "daily_budget_rub": 1000.0,
         "strategy": {"search": "HIGHEST_POSITION", "network": "SERVING_OFF", "manual": True}},
    72: {"type": "UNIFIED_CAMPAIGN", "daily_budget_rub": None,
         "strategy": {"search": "WB_MAXIMUM_CLICKS", "network": "SERVING_OFF", "weekly_budget_rub": 2000.0, "manual": False}},
    73: {"type": "TEXT_CAMPAIGN", "daily_budget_rub": None,
         "strategy": {"search": "AVERAGE_CRR", "network": "SERVING_OFF", "manual": False}},
}


@pytest.fixture
def direct(monkeypatch):
    """Настройки кампаний без Директа; updates — что ушло бы в Директ, fail — ошибки по id кампании."""
    fake = SimpleNamespace(updates=[], fail={})

    def get(user_id, cid):
        return {**SETTINGS[cid], "strategy": dict(SETTINGS[cid]["strategy"])}

    def update(user_id, cid, changes):
        if cid in fake.fail:
            raise fake.fail[cid]
        fake.updates.append((cid, changes))
        return {}

    monkeypatch.setattr(actions, "get_campaign_settings", get)
    monkeypatch.setattr(actions, "update_campaign_settings", update)
    return fake


def test_apply_limits_changes_only_what_is_needed(direct):
    rows = [{"id": 70, "name": "Казань копия"}, {"id": 72, "name": "Самара копия"}]
    out = experiments.apply_limits(1, rows, 3000.0)
    assert direct.updates == [
        (70, {"strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF", "weekly_budget_rub": 3000.0,
                           "goal_id": 5001}}),
    ]
    assert [(o["campaign_row_id"], o["kind"], o["changed"]) for o in out] == [(70, "weekly", True), (72, "weekly", False)]
    assert out[1]["after"] == f"{experiments._rub(2000)}/нед. — не больше лимита, не меняем"


def test_apply_limits_checks_everything_first(direct):
    rows = [{"id": 70, "name": "Казань копия"}, {"id": 73, "name": "Тула копия"}]
    with pytest.raises(YandexError) as exc:
        experiments.apply_limits(1, rows, 3000.0)
    assert exc.value.code == "strategy"
    assert direct.updates == []  # ошибка до первого изменения — в Директе ничего не поменяли


def test_apply_limits_reports_what_is_already_set(direct):
    rows = [{"id": 70, "name": "Казань копия"}, {"id": 71, "name": "Самара копия"}]
    direct.fail[71] = YandexError("budget", "Директ не принял бюджет")
    with pytest.raises(YandexError) as exc:
        experiments.apply_limits(1, rows, 3000.0)
    assert exc.value.code == "budget"
    assert exc.value.message == (
        "Лимит «Самара копия» не поставлен: Директ не принял бюджет. Уже поставлен: «Казань копия» — откатить можно "
        "в журнале. Эксперимент не записан."
    )
    direct.fail[70] = YandexError("units")
    with pytest.raises(YandexError) as exc:
        experiments.apply_limits(1, rows, 3000.0)
    assert exc.value.message == "Лимит «Казань копия» не поставлен: units. Эксперимент не записан."


# --- Прогресс по загруженной статистике --------------------------------------------------------


def _account(aid=3, synced=SYNCED, goal_ids=()):
    return {"id": aid, "stats_synced_at": synced, "stats_stage": {"goal_ids": list(goal_ids)}}


def _camp(cid, name, *, account_id=3, state="ON", status="ACCEPTED", bidding=None):
    return {"id": cid, "account_id": account_id, "campaign_id": cid * 100, "name": name, "state": state,
            "status": status, "campaign_type": "TEXT_CAMPAIGN", "bidding_strategy": bidding, "priority_goals": []}


def _stat(cid, day, impressions=0, clicks=0, cost=0, conversions=0):
    return {"account_id": 3, "campaign_id": cid * 100, "day": day, "impressions": impressions, "clicks": clicks,
            "cost_kopecks": cost, "conversions": conversions}


def _data(stats=(), goal_stats=(), campaigns=None, accounts=None):
    if campaigns is None:
        campaigns = [_camp(70, "Казань копия"), _camp(7, "Поиск Москва")]
    return experiments.prepare_data(campaigns, accounts or [_account()], list(stats), list(goal_stats))


def _exp(**kw):
    row = {
        "id": 1, "title": "Казань", "hypothesis": "Схема 0 ₽ повторится в Казани", "kind": "geo",
        "campaign_row_ids": [70], "source_campaign_row_id": None, "weekly_limit_kopecks": 300000,
        "started_on": _d(20), "days": 14, "criterion": "zero_cost", "target": 1.0, "criterion_text": "",
        "status": "running", "verdict": "", "conclusion": "", "chat_id": 5,
        "created_at": datetime(2026, 10, 20, 12, 0), "finished_at": None,
    }
    row.update(kw)
    return row


COLLECTING = [
    _stat(70, _d(19), 100, 9, 900),  # до старта — не в счёт
    _stat(70, _d(22), 50, 5, 3000),
    _stat(70, _d(27), 40, 3, 2000),
    _stat(70, _d(28), 1000, 100, 99900),  # сегодня — ещё не загружено
]


def test_progress_without_stats():
    never = experiments.progress(_exp(), _data(accounts=[_account(synced=None)]), TODAY)
    assert (never["status"], never["reason"], never["loaded_to"]) == (
        "no_data", "статистика кабинета ещё не загружалась", None
    )
    foreign = experiments.progress(_exp(), _data(campaigns=[_camp(70, "Казань копия", account_id=9)]), TODAY)
    assert foreign["status"] == "no_data"  # кабинета нет среди загруженных

    missing = experiments.progress(_exp(campaign_row_ids=[99]), _data(), TODAY)
    assert (missing["status"], missing["reason"]) == ("no_data", "тестовых кампаний нет в базе")
    assert missing["campaigns"] == [{
        "id": 99, "name": "[99]", "missing": True, "impressions": 0, "clicks": 0, "cost_kopecks": 0, "conversions": 0,
        "cr_pct": None, "cpa_kopecks": None, "paid_conversions": None, "week_cost_kopecks": None, "week_share_pct": None,
        "limit_hit": False,
    }]
    assert missing["totals"]["paid_conversions"] is None


def test_progress_waits_for_impressions_then_stalls():
    waiting = experiments.progress(_exp(), _data([_stat(70, _d(19), impressions=100)]), TODAY)
    assert waiting["status"] == "waiting"
    assert waiting["reason"] == "показов пока нет: кампании на модерации или ещё не стартовали"
    assert (waiting["showing_since"], waiting["window_start"], waiting["ends_on"]) == (None, "2026-10-20", "2026-11-02")
    assert (waiting["day"], waiting["days_left"], waiting["loaded_to"]) == (9, 6, "2026-10-27")

    stalled = experiments.progress(_exp(started_on=_d(1), days=7), _data(), TODAY)
    assert stalled["status"] == "stalled"
    assert stalled["reason"] == "за 7 дн. у тестовых кампаний не было показов — проверьте модерацию, ставки и баланс"
    assert (stalled["day"], stalled["days_left"]) == (7, 0)


def test_progress_term_starts_with_first_impressions():
    p = experiments.progress(_exp(), _data(COLLECTING), TODAY)
    assert (p["showing_since"], p["window_start"], p["ends_on"]) == ("2026-10-22", "2026-10-22", "2026-11-04")
    assert (p["day"], p["days_left"], p["week_from"]) == (7, 8, "2026-10-26")
    assert p["totals"] == {"impressions": 90, "clicks": 8, "cost_kopecks": 5000, "conversions": 0, "cr_pct": 0.0,
                           "cpa_kopecks": None, "paid_conversions": None}
    assert (p["status"], p["met"]) == ("collecting", False)
    assert p["reason"] == f"день 7 из 14: 0 конверсий при расходе {rub(5000)}"
    campaign = p["campaigns"][0]
    assert (campaign["name"], campaign["state"], campaign["missing"]) == ("Казань копия", "ON", False)
    assert (campaign["week_cost_kopecks"], campaign["week_share_pct"], campaign["limit_hit"]) == (2000, 1, False)


def test_progress_zero_cost_success_comes_early():
    exp = _exp(target=2.0)
    p = experiments.progress(exp, _data([_stat(70, _d(22), 300, 30, 200, 3)]), TODAY)
    assert (p["status"], p["label"], p["met"]) == ("success", "критерий выполнен", True)
    assert p["reason"] == f"3 конверсии при расходе {rub(200)}, цель — от 2 конв. за ~0 ₽"
    pricey = experiments.progress(exp, _data([_stat(70, _d(22), 300, 30, 400, 3)]), TODAY)
    assert pricey["status"] == "collecting"  # дороже рубля за конверсию — не «за ~0 ₽»


def test_progress_fails_after_term():
    exp = _exp(criterion="conversions", target=5.0, started_on=_d(1), days=7)
    stats = [_stat(70, _d(1), 100, 10, 5000, 1), _stat(70, _d(7), 100, 10, 5000, 1), _stat(70, _d(8), 100, 10, 5000, 7)]
    p = experiments.progress(exp, _data(stats), TODAY)
    assert (p["status"], p["reason"]) == ("fail", "срок вышел: 2 конверсии, а цель — от 5 конв.")
    assert p["totals"]["conversions"] == 2  # после срока — не в счёт


def test_progress_cr_needs_enough_clicks():
    over = _exp(criterion="cr", target=5.0, started_on=_d(1), days=7)
    few = experiments.progress(over, _data([_stat(70, _d(2), 100, 10, 1000, 1)]), TODAY)
    assert (few["status"], few["met"]) == ("ready", None)
    assert few["reason"] == "срок вышел, но для CR мало кликов: 10 из 30"
    good = experiments.progress(over, _data([_stat(70, _d(2), 400, 40, 4000, 4)]), TODAY)
    assert (good["status"], good["reason"]) == ("success", "CR 10% на 40 кликах, цель — CR от 5%")
    low = experiments.progress(_exp(criterion="cr", target=5.0), _data([_stat(70, _d(22), 400, 40, 4000, 1)]), TODAY)
    assert (low["status"], low["reason"]) == ("collecting", "день 7 из 14: CR 2,5% на 40 кликах")


def test_progress_custom_criterion_is_for_the_marketer():
    exp = _exp(criterion="custom", target=None, criterion_text="заявки из Казани дешевле 500 ₽", started_on=_d(1), days=7)
    p = experiments.progress(exp, _data([_stat(70, _d(1), 100, 21, 5000, 2)]), TODAY)
    assert (p["status"], p["met"]) == ("ready", None)
    assert p["reason"] == (
        f"срок вышел: 2 конверсии, 21 клик, расход {rub(5000)} — оцените критерий «заявки из Казани дешевле 500 ₽»"
    )


def test_progress_weekly_limit():
    stats = [_stat(70, _d(22), 100, 10, 5000), _stat(70, _d(26), 100, 10, 140000), _stat(70, _d(27), 100, 10, 130000)]
    p = experiments.progress(_exp(), _data(stats), TODAY)
    assert (p["campaigns"][0]["week_cost_kopecks"], p["campaigns"][0]["week_share_pct"]) == (270000, 90)
    assert p["limit_hit"] is True  # 90% недельного лимита

    finished = experiments.progress(_exp(status="finished", finished_at=datetime(2026, 10, 27, 9, 0)), _data(stats), TODAY)
    no_limit = experiments.progress(_exp(weekly_limit_kopecks=None), _data(stats), TODAY)
    stale = experiments.progress(_exp(), _data(stats, accounts=[_account(synced=datetime(2026, 10, 26, 6, 0))]), TODAY)
    for q in (finished, no_limit, stale):  # стale: загружено по 25.10 — эта неделя ещё не видна
        assert (q["campaigns"][0]["week_cost_kopecks"], q["limit_hit"]) == (None, False)


def test_progress_stops_at_finish():
    stats = [_stat(70, _d(10), 100, 10, 1000, 1), _stat(70, _d(15), 100, 10, 1000, 1), _stat(70, _d(16), 100, 10, 1000, 5)]
    exp = _exp(criterion="conversions", target=5.0, started_on=_d(10), status="finished",
               finished_at=datetime(2026, 10, 15, 9, 0))
    p = experiments.progress(exp, _data(stats), TODAY)
    assert (p["totals"]["conversions"], p["status"], p["ends_on"]) == (2, "fail", "2026-10-23")
    aware = experiments.progress({**exp, "finished_at": datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)}, _data(stats), TODAY)
    assert aware["totals"]["conversions"] == 2  # 01:30 по Москве 15.10


def test_msk_day():
    assert experiments._msk_day(datetime(2026, 10, 14, 22, 30, tzinfo=timezone.utc)) == _d(15)
    assert experiments._msk_day(datetime(2026, 10, 14, 22, 30)) == _d(14)
    assert experiments._msk_day(None) is None


SOURCE_STATS = [
    _stat(70, _d(20), 100, 10, 1000, 1),
    _stat(7, _d(12), 999, 99, 9900, 9),  # раньше окна «до старта»
    _stat(7, _d(13), 100, 20, 2000, 2),
    _stat(7, _d(19), 100, 20, 2000, 2),
    _stat(7, _d(20), 50, 10, 1000, 1),
    _stat(7, _d(26), 50, 10, 1000, 1),
    _stat(7, _d(27), 500, 50, 5000, 5),  # после срока
]


def test_progress_compares_with_source():
    p = experiments.progress(_exp(source_campaign_row_id=7, days=7), _data(SOURCE_STATS), TODAY)
    assert p["source"] == {
        "id": 7, "name": "Поиск Москва", "state": "ON", "status": "ACCEPTED", "missing": False,
        "during": {"impressions": 100, "clicks": 20, "cost_kopecks": 2000, "conversions": 2, "cr_pct": 10.0,
                   "cpa_kopecks": 1000},
        "before": {"impressions": 200, "clicks": 40, "cost_kopecks": 4000, "conversions": 4, "cr_pct": 10.0,
                   "cpa_kopecks": 1000},
        "before_from": "2026-10-13",
        "before_to": "2026-10-19",
    }
    gone = experiments.progress(_exp(source_campaign_row_id=99), _data(), TODAY)
    assert gone["source"] == {"id": 99, "name": "[99]", "missing": True}


PAY_FOR_ORDERS = {
    "Search": {"BiddingStrategyType": "PAY_FOR_CONVERSION", "PayForConversion": {"Cpa": 500000000, "GoalId": 5001}},
    "Network": {"BiddingStrategyType": "SERVING_OFF"},
}


def test_progress_counts_paid_conversions():
    campaigns = [_camp(70, "Казань копия", bidding=PAY_FOR_ORDERS)]
    stats = [_stat(70, _d(22), 100, 10, 1000, 3)]
    goals = [
        {"account_id": 3, "campaign_id": 7000, "day": _d(22), "goal_id": 5001, "conversions": 2},
        {"account_id": 3, "campaign_id": 7000, "day": _d(19), "goal_id": 5001, "conversions": 4},  # до старта
        {"account_id": 3, "campaign_id": 7000, "day": _d(22), "goal_id": 6002, "conversions": 1},  # не платная цель
        {"account_id": 3, "campaign_id": 7000, "day": _d(22), "goal_id": None, "conversions": 7},
    ]
    paid = experiments.progress(_exp(), _data(stats, goals, campaigns, [_account(goal_ids=[5001])]), TODAY)
    assert (paid["campaigns"][0]["paid_conversions"], paid["totals"]["paid_conversions"]) == (2, 2)
    unloaded = experiments.progress(_exp(), _data(stats, goals, campaigns), TODAY)
    assert unloaded["totals"]["paid_conversions"] is None  # цели стратегии не загружены — не знаем


# --- Список, карточки и выжимка ----------------------------------------------------------------


def test_experiment_out():
    prog = {"status": "waiting"}
    out = experiments.experiment_out(_exp(), prog)
    assert out["progress"] is prog
    assert (out["weekly_limit_rub"], out["started_on"], out["created_at"], out["finished_at"]) == (
        3000.0, "2026-10-20", "2026-10-20T12:00:00", None
    )
    assert (out["kind_label"], out["criterion_label"], out["status_label"], out["verdict_label"]) == (
        "новое гео", "конверсии за ~0 ₽", "идёт", ""
    )
    assert out["criterion_summary"] == experiments.criterion_text(_exp())
    done = experiments.experiment_out(_exp(status="finished", verdict="fail", weekly_limit_kopecks=None))
    assert (done["status_label"], done["verdict_label"], done["weekly_limit_rub"], done["progress"]) == (
        "завершён", "не подтвердилась", None, None
    )


def _item(prog_status: str, reason: str = "причина", **kw) -> dict:
    prog = {"status": prog_status, "label": experiments.PROGRESS_LABELS[prog_status], "reason": reason}
    return experiments.experiment_out(_exp(**kw), prog)


def test_review_cards_only_for_running_with_outcome():
    items = [
        _item("success", "3 конверсии при расходе 2 ₽, цель — от 2 конв. за ~0 ₽", id=1, title="Казань"),
        _item("collecting", id=2, title="Самара"),
        _item("fail", id=3, title="Тула", status="finished"),
        _item("stalled", "за 7 дн. у тестовых кампаний не было показов", id=4, title="Пермь"),
    ]
    assert experiments.review_cards(items) == [
        {
            "kind": "experiment",
            "title": "Эксперимент «Казань»: критерий выполнен",
            "detail": "3 конверсии при расходе 2 ₽, цель — от 2 конв. за ~0 ₽. ИИ разберёт итоги и предложит, что дальше",
            "prompt": "Разбери итоги эксперимента «Казань»",
            "experiment_id": 1,
        },
        {
            "kind": "experiment",
            "title": "Эксперимент «Пермь»: не было показов",
            "detail": "За 7 дн. у тестовых кампаний не было показов. ИИ разберёт итоги и предложит, что дальше",
            "prompt": "Разбери итоги эксперимента «Пермь»",
            "experiment_id": 4,
        },
    ]


def test_chat_cards_never_break_signals(monkeypatch):
    items = [_item("ready", id=1)]
    monkeypatch.setattr(experiments, "experiments_overview", lambda user_id, ws_id, today=None: {"items": items})
    assert experiments.chat_cards(1, 2) == experiments.review_cards(items)

    def broken(user_id, ws_id, today=None):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(experiments, "experiments_overview", broken)
    assert experiments.chat_cards(1, 2) == []


def test_signals_overview_puts_experiments_first(monkeypatch):
    signals = {
        "period": {}, "totals": {}, "notes": [], "campaigns": {7: {"name": "Поиск Москва"}},
        "suggestions": [{"kind": "decay", "title": "Затухает", "campaign_row_ids": [7]}],
    }
    card = {"kind": "experiment", "title": "Эксперимент «Казань»: срок вышел", "experiment_id": 1}
    monkeypatch.setattr(insights, "get_signals", lambda user_id, ws_id, today=None: signals)
    monkeypatch.setattr(experiments, "chat_cards", lambda user_id, ws_id, today=None: [card])
    out = insights.signals_overview(1, 2)
    assert out["suggestions"][0] == {**card, "campaigns": [], "campaigns_total": 0}
    assert out["suggestions"][1]["campaigns"] == [{"id": 7, "name": "Поиск Москва"}]


def test_overview_and_finish_from_page(monkeypatch):
    calls = []
    monkeypatch.setattr(experiments, "require_workspace", lambda user_id, ws_id: calls.append(("ws", user_id, ws_id)))
    monkeypatch.setattr(experiments, "list_experiments", lambda user_id, ws_id: [_exp(target=2.0)])
    monkeypatch.setattr(experiments, "_load", lambda user_id, rows, today: _data([_stat(70, _d(22), 300, 30, 200, 3)]))
    out = experiments.experiments_overview(1, 2, TODAY)
    assert out["today"] == "2026-10-28"
    assert out["items"][0]["progress"]["status"] == "success"

    def finish(user_id, exp_id, **kw):
        calls.append(("finish", user_id, exp_id, kw))

    monkeypatch.setattr(experiments, "finish_experiment", finish)
    monkeypatch.setattr(experiments, "experiments_overview", lambda user_id, ws_id: {"items": [], "today": "2026-10-28"})
    calls.clear()
    assert experiments.finish_and_list(1, 2, 4, verdict="success", conclusion="Масштабировать") == {
        "items": [], "today": "2026-10-28"
    }
    assert calls == [
        ("ws", 1, 2),
        ("finish", 1, 4, {"verdict": "success", "conclusion": "Масштабировать", "cancel": False, "workspace_id": 2}),
    ]


def test_load_without_campaigns_skips_db():
    data = experiments._load(1, [_exp(campaign_row_ids=[], source_campaign_row_id=None)], TODAY)
    assert (data["campaigns"], data["accounts"]) == ({}, {})


def test_busy_campaigns(monkeypatch):
    first = {"id": 1, "campaign_row_ids": [70, 71]}
    second = {"id": 2, "campaign_row_ids": [71, "72"]}
    monkeypatch.setattr(experiments, "running_experiments", lambda user_id, ws_id: [first, second, {"id": 3, "campaign_row_ids": None}])
    assert experiments.busy_campaigns(1, 2) == {70: first, 71: first, 72: second}


def _running_item():
    return experiments.experiment_out(_exp(), experiments.progress(_exp(), _data(COLLECTING), TODAY))


def _past_item(n=2, **kw):
    row = _exp(id=n, title="Москва ключи", kind="keywords", started_on=_d(1), days=7, criterion="conversions",
               target=5.0, status="finished", verdict="success", conclusion="Ключи с «купить» дают заявки — масштабировать",
               finished_at=datetime(2026, 10, 8, 10, 0))
    row.update(kw)
    return experiments.experiment_out(row)


def test_digest_running_in_full_and_past_in_short():
    text = experiments.digest([_running_item(), _past_item()])
    assert text.startswith("Эксперименты пространства. #N — experiment_id, номера в [скобках] — campaign_row_id.")
    lines = text.splitlines()
    week = f"за эту неделю {rub(2000)} из {experiments._rub(3000)} (1%)"
    metrics = f"показы 90, клики 8, расход {rub(5000)}, конверсии 0, CR 0%"
    for line in (
        "#1 «Казань» — новое гео, идёт; 14 дн. с 22.10 по 04.11, день 7 из 14; статистика по 27.10.",
        "Гипотеза: Схема 0 ₽ повторится в Казани",
        "Критерий успеха: конверсии за ~0 ₽ — от 1 конв. за весь срок, не дороже 1 ₽ за конверсию.",
        f"Сейчас: идёт — день 7 из 14: 0 конверсий при расходе {rub(5000)}.",
        f"Лимит: {experiments._rub(3000)}/нед. на кампанию.",
        f"Итого тестовых: {metrics}.",
        f"- [70] «Казань копия» (идёт): {metrics}; {week}",
        "Прошлые эксперименты:",
        "- #2 «Москва ключи» (новые ключи, с 01.10, 7 дн.): подтвердилась — Ключи с «купить» дают заявки — масштабировать",
    ):
        assert line in lines, line
    assert "Ещё идут" not in text


def test_digest_chosen_experiment():
    text = experiments.digest([_running_item(), _past_item()], {2})
    lines = text.splitlines()
    assert "Ещё идут: #1 «Казань» — идёт." in lines
    assert "#2 «Москва ключи» — новые ключи, завершён." in lines
    assert "Итог: подтвердилась. Вывод: Ключи с «купить» дают заявки — масштабировать." in lines
    assert "Прошлые эксперименты:" not in lines


def test_digest_details():
    waiting = experiments.experiment_out(_exp(id=3, title="Тула"), experiments.progress(_exp(), _data(), TODAY))
    missing_exp = _exp(id=4, title="Пермь", campaign_row_ids=[70, 99], source_campaign_row_id=7, days=7)
    missing = experiments.experiment_out(missing_exp, experiments.progress(missing_exp, _data(SOURCE_STATS), TODAY))
    gone_exp = _exp(id=5, title="Сочи", source_campaign_row_id=98)
    gone = experiments.experiment_out(gone_exp, experiments.progress(gone_exp, _data(accounts=[_account(synced=None)]), TODAY))
    lines = experiments.digest([waiting, missing, gone]).splitlines()
    assert (
        "#3 «Тула» — новое гео, идёт; 14 дн. с 20.10 по 02.11 (показов ещё не было — срок пойдёт с первого дня "
        "показов); статистика по 27.10."
    ) in lines
    assert "- [99] нет в базе — удалена или не загружена" in lines
    during = f"показы 100, клики 20, расход {rub(2000)}, конверсии 2, CR 10%, CPA {rub(1000)}"
    before = f"показы 200, клики 40, расход {rub(4000)}, конверсии 4, CR 10%, CPA {rub(1000)}"
    assert (
        f"Образец [7] «Поиск Москва» (идёт): за те же дни — {during}; за 7 дн. до старта (13.10–19.10) — {before}."
    ) in lines
    assert any(line.startswith("#5 «Сочи»") and line.endswith("; статистики нет.") for line in lines)
    assert "Образец [98] — нет в базе." in lines


def test_digest_limits_past_and_handles_empty():
    assert experiments.digest([]) == "Экспериментов в этом пространстве ещё не было."
    text = experiments.digest([_past_item(n) for n in range(2, 9)])
    assert sum(line.startswith("- #") for line in text.splitlines()) == experiments.DIGEST_PAST


def test_state_text_prefers_moderation_status():
    assert experiments._state_text({"state": "OFF", "status": "MODERATION"}) == "на модерации"
    assert experiments._state_text({"state": "SUSPENDED", "status": "ACCEPTED"}) == "остановлена"
    assert experiments._state_text({}) == "—"


# --- Инструменты ИИ ---------------------------------------------------------------------------

CAMPAIGNS = {
    70: {"id": 70, "name": "Казань копия", "state": "OFF", "status": "MODERATION", "account_id": 3,
         "daily_budget_kopecks": None},
    71: {"id": 71, "name": "Самара копия", "state": "OFF", "status": "DRAFT", "account_id": 3,
         "daily_budget_kopecks": 100000},
    7: {"id": 7, "name": "Поиск Москва", "state": "ON", "status": "ACCEPTED", "account_id": 3,
        "daily_budget_kopecks": 150000},
}

START = {
    "title": "Казань",
    "hypothesis": "Схема 0 ₽ повторится в Казани",
    "kind": "geo",
    "campaign_row_ids": [70, 71],
    "source_campaign_row_id": 7,
    "weekly_budget_rub": 3000,
    "days": 14,
    "criterion": "zero_cost",
    "target": 2,
    "reason": "Проверить гео",
}
CRITERION = "конверсии за ~0 ₽ — от 2 конв. за весь срок, не дороже 1 ₽ за конверсию"


class FakeContext:
    """Context без БД: пространство из словаря, кампании — из CAMPAIGNS."""

    def __init__(self, ws: dict | None = None):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "accounts": [], **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))
        self.action = None

    def campaigns(self, ids: list[int]) -> list[dict]:
        foreign = [i for i in ids if i not in CAMPAIGNS]
        if foreign:
            raise YandexError("scope", f"Кампании {foreign} не входят в это пространство")
        return [CAMPAIGNS[i] for i in ids]

    def campaign(self, campaign_row_id: int) -> dict:
        return self.campaigns([campaign_row_id])[0]


@pytest.fixture
def store(monkeypatch):
    """Эксперименты без БД: busy — кампании идущих экспериментов, created — что записали бы."""
    fake = SimpleNamespace(busy={}, created=[])

    def create(user_id, ws_id, d, *, chat_id=None, action_id=None, started_on=None):
        fake.created.append({"user_id": user_id, "ws_id": ws_id, "d": d, "chat_id": chat_id, "action_id": action_id})
        return {"id": 9, "title": d["title"], "started_on": TODAY, "days": d["days"]}

    monkeypatch.setattr(experiments, "busy_campaigns", lambda user_id, ws_id: fake.busy)
    monkeypatch.setattr(experiments, "create_experiment", create)
    return fake


def test_start_plan_always_asks_for_new_limit(store):
    plan = ai_agent._p_start_experiment(FakeContext(), START)
    assert plan.summary == (
        f"Эксперимент «Казань»: «Казань копия», «Самара копия», лимит 3 000 ₽/нед. на кампанию, 14 дней, успех — {CRITERION}"
    )
    assert (plan.categories, plan.change_pct) == (["campaigns", "budget"], math.inf)
    generous = normalize_ai_access({"categories": {"campaigns": True, "budget": True}, "max_change_pct": 500})
    assert not ai_agent._allowed(generous, plan.categories, plan.change_pct)
    assert ai_agent._allowed(normalize_ai_access({"full_access": True}), plan.categories, plan.change_pct)


def test_start_plan_without_limit(store):
    plan = ai_agent._p_start_experiment(FakeContext(), {**START, "set_limit": "false", "days": 21})
    assert "лимит 3 000 ₽/нед. на кампанию (ставит маркетолог), 21 день," in plan.summary
    assert (plan.categories, plan.change_pct) == (["campaigns"], None)


def test_start_refuses_busy_and_foreign_campaigns(store):
    store.busy = {71: {"id": 4, "title": "Самара: ключи"}}
    with pytest.raises(YandexError) as exc:
        ai_agent._p_start_experiment(FakeContext(), START)
    assert (exc.value.code, exc.value.message) == (
        "busy",
        "Кампании уже в идущих экспериментах: «Самара копия» — в #4 «Самара: ключи». Заверши эксперимент "
        "(finish_experiment) или возьми другие кампании",
    )
    with pytest.raises(YandexError) as exc:
        ai_agent._p_start_experiment(FakeContext(), {**START, "campaign_row_ids": [70, 99]})
    assert exc.value.message == "Кампании [99] не входят в это пространство"


def test_start_sets_limits_then_records(store, direct):
    ctx = FakeContext()
    ctx.action = {"id": 11, "chat_id": 5}
    out = ai_agent._x_start_experiment(ctx, START)
    assert direct.updates == [
        (70, {"strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF", "weekly_budget_rub": 3000.0,
                           "goal_id": 5001}}),
        (71, {"daily_budget_rub": 428}),
    ]
    assert out["experiment"] == {"id": 9, "title": "Казань", "started_on": "2026-10-28", "days": 14, "ends_on": "2026-11-10"}
    assert [(o["campaign_row_id"], o["kind"], o["changed"]) for o in out["limits"]] == [(70, "weekly", True), (71, "daily", True)]
    assert out["note"] == ai_agent._EXPERIMENT_NOTE
    created = store.created[0]
    assert (created["user_id"], created["ws_id"], created["chat_id"], created["action_id"]) == (1, 2, 5, 11)
    assert (created["d"]["campaign_row_ids"], created["d"]["source_campaign_row_id"], created["d"]["target"]) == ([70, 71], 7, 2.0)


def test_start_without_limit_leaves_direct_alone(store, direct):
    out = ai_agent._x_start_experiment(FakeContext(), {**START, "set_limit": False})
    assert (direct.updates, out["limits"]) == ([], [])
    assert out["note"] == "Лимит в Директе не меняли — его ставит маркетолог. " + ai_agent._EXPERIMENT_NOTE
    assert (store.created[0]["chat_id"], store.created[0]["action_id"]) == (None, None)


def test_start_is_not_recorded_when_limit_fails(store, direct):
    direct.fail[71] = YandexError("budget", "Директ не принял бюджет")
    with pytest.raises(YandexError) as exc:
        ai_agent._x_start_experiment(FakeContext(), START)
    assert "Уже поставлен: «Казань копия»" in exc.value.message
    assert store.created == []


def test_start_changes(monkeypatch, store):
    monkeypatch.setattr(ai_agent, "_current_settings", lambda ctx, cid: SETTINGS[cid])
    monkeypatch.setattr(ai_agent, "today_msk", lambda: TODAY)
    tail = [
        {"what": "Срок теста", "before": "—", "after": "14 дней с первого дня показов, примерно до 10.11"},
        {"what": "Критерий успеха", "before": "—", "after": CRITERION},
        {"what": "Сравнение", "before": "—", "after": "с «Поиск Москва»"},
        {"what": "Остановка", "before": "—", "after": "только по вашему решению после итогов"},
    ]
    assert ai_agent._changes(FakeContext(), "start_experiment", START) == [
        {"what": "Лимит «Казань копия»", "before": "10 000 ₽/нед.", "after": "3 000 ₽/нед."},
        {"what": "Лимит «Самара копия»", "before": "1 000 ₽/день",
         "after": "428 ₽/день; у ручных ставок нет недельного бюджета — лимит задан дневным"},
        *tail,
    ]
    assert ai_agent._changes(FakeContext(), "start_experiment", {**START, "set_limit": False}) == [
        {"what": "Лимит в Директе", "before": "как есть", "after": "не меняем — 3 000 ₽/нед. на кампанию ставит маркетолог"},
        *tail,
    ]


def test_start_changes_show_why_limit_fails(monkeypatch, store):
    monkeypatch.setattr(ai_agent, "_current_settings", lambda ctx, cid: {**SETTINGS[73], "name": "x"} if cid == 71 else SETTINGS[cid])
    monkeypatch.setattr(ai_agent, "today_msk", lambda: TODAY)
    rows = ai_agent._changes(FakeContext(), "start_experiment", START)
    assert rows[1] == {
        "what": "Лимит «Самара копия»",
        "before": ai_agent._strategy_text(SETTINGS[73]["strategy"], {}),
        "after": "не выйдет: «Самара копия»: стратегию «Доля рекламных расходов» сервис не меняет — попроси маркетолога "
        "задать недельный бюджет в Директе вручную и запусти эксперимент с set_limit=false",
    }


EXPS = {
    4: {"id": 4, "workspace_id": 2, "title": "Казань", "status": "running"},
    5: {"id": 5, "workspace_id": 2, "title": "Самара", "status": "finished"},
}


@pytest.fixture
def finishing(monkeypatch):
    calls = []

    def get(user_id, exp_id, *, workspace_id=None):
        if exp_id not in EXPS:
            raise YandexError("not-found", f"Эксперимента #{exp_id} нет в этом пространстве")
        return EXPS[exp_id]

    def finish(user_id, exp_id, **kw):
        calls.append((user_id, exp_id, kw))
        return {**EXPS[exp_id], "status": "cancelled" if kw["cancel"] else "finished", "verdict": kw["verdict"]}

    monkeypatch.setattr(experiments, "get_experiment", get)
    monkeypatch.setattr(experiments, "finish_experiment", finish)
    return calls


FINISH = {"experiment_id": 4, "verdict": "success", "conclusion": "Масштабировать на Самару"}


def test_finish_plan(finishing):
    plan = ai_agent._p_finish_experiment(FakeContext(), FINISH)
    assert (plan.summary, plan.categories, plan.change_pct) == (
        "Итоги эксперимента «Казань»: гипотеза подтвердилась", ["campaigns"], None
    )
    cancel = ai_agent._p_finish_experiment(FakeContext(), {"experiment_id": 4, "cancel": True})
    assert cancel.summary == "Отменить эксперимент «Казань»"


@pytest.mark.parametrize(
    "args, code, text",
    [
        ({**FINISH, "experiment_id": 5}, "finished", "Эксперимент «Самара» уже завершён"),
        ({"experiment_id": 4, "verdict": "fail"}, "args", "Укажите conclusion — что узнали и что делать дальше"),
        ({**FINISH, "verdict": "maybe"}, "args", "verdict — одно из ['success', 'fail', 'unclear']"),
        ({**FINISH, "experiment_id": 99}, "not-found", "Эксперимента #99 нет в этом пространстве"),
        ({"verdict": "success"}, "args", "Укажите experiment_id (число)"),
    ],
)
def test_finish_refusals(finishing, args, code, text):
    with pytest.raises(YandexError) as exc:
        ai_agent._p_finish_experiment(FakeContext(), args)
    assert (exc.value.code, exc.value.message) == (code, text)


def test_finish_records_verdict_only(finishing):
    out = ai_agent._x_finish_experiment(FakeContext(), FINISH)
    assert finishing == [
        (1, 4, {"verdict": "success", "conclusion": "Масштабировать на Самару", "cancel": False, "workspace_id": 2})
    ]
    assert out == {
        "experiment": {"id": 4, "title": "Казань", "status": "finished", "verdict": "success"},
        "note": "Кампании эксперимента не тронуты: остановить или масштабировать — отдельными действиями",
    }


def test_finish_changes(finishing):
    keep = {"what": "Кампании эксперимента", "before": "как есть", "after": "без изменений"}
    assert ai_agent._changes(FakeContext(), "finish_experiment", FINISH) == [
        {"what": "Эксперимент «Казань»", "before": "идёт", "after": "завершён"},
        {"what": "Гипотеза", "before": "проверяется", "after": "подтвердилась"},
        {"what": "Вывод", "before": "—", "after": "Масштабировать на Самару"},
        keep,
    ]
    assert ai_agent._changes(FakeContext(), "finish_experiment", {"experiment_id": 4, "cancel": True}) == [
        {"what": "Эксперимент «Казань»", "before": "идёт", "after": "отменён"},
        keep,
    ]


def test_get_experiments_tool(monkeypatch):
    items = [_running_item(), _past_item()]
    monkeypatch.setattr(experiments, "experiments_overview", lambda user_id, ws_id: {"items": items})
    assert ai_agent._t_experiments(FakeContext(), {}) == {"digest": experiments.digest(items)}
    assert ai_agent._t_experiments(FakeContext(), {"experiment_id": "2"}) == {"digest": experiments.digest(items, {2})}
    assert ai_agent._t_experiments(FakeContext(), {"experiment_id": ""}) == {"digest": experiments.digest(items)}
    with pytest.raises(YandexError) as exc:
        ai_agent._t_experiments(FakeContext(), {"experiment_id": 99})
    assert (exc.value.code, exc.value.message) == ("not-found", "Эксперимента #99 нет в этом пространстве")


def test_experiment_tools_registered():
    assert "get_experiments" in ai_agent.READ_TOOLS
    for name in ("start_experiment", "finish_experiment"):
        assert name in ai_agent.WRITE_TOOLS and name in ai_agent._DIFFS and name in ai_agent.TOOL_LABELS
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in ai_agent.TOOLS}
    start = schemas["start_experiment"]
    assert start["properties"]["kind"]["enum"] == list(experiments.KINDS)
    assert start["properties"]["criterion"]["enum"] == list(experiments.CRITERIA)
    assert set(start["required"]) == {"title", "hypothesis", "campaign_row_ids", "weekly_budget_rub", "criterion"}
    assert schemas["finish_experiment"]["properties"]["verdict"]["enum"] == list(experiments.VERDICTS)


def test_prompt_lists_running_experiments(monkeypatch):
    monkeypatch.setattr(ai_agent, "memory_examples", lambda ws_id, limit: {})
    monkeypatch.setattr(ai_agent, "_running_experiments", lambda ctx: [])
    quiet = ai_agent._system_prompt(FakeContext())
    for name in ("start_experiment", "get_experiments", "finish_experiment", "success_criteria"):
        assert name in quiet, name
    assert "Идут эксперименты" not in quiet

    running = [{"id": 4, "title": "Казань", "started_on": _d(20), "days": 14, "campaign_row_ids": [70, 71]}]
    monkeypatch.setattr(ai_agent, "_running_experiments", lambda ctx: running)
    prompt = ai_agent._system_prompt(FakeContext())
    assert "Идут эксперименты (не меняй настройки их кампаний без просьбы маркетолога" in prompt
    assert "- #4 «Казань»: с 20.10 на 14 дней, кампании [70], [71]" in prompt.splitlines()


def test_running_experiments_failure_keeps_chat_alive(monkeypatch):
    def broken(user_id, ws_id):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(experiments, "running_experiments", broken)
    assert ai_agent._running_experiments(FakeContext()) == []


@pytest.fixture
def review(monkeypatch):
    fake = SimpleNamespace(items=[_running_item(), _past_item()], started=[], budget=[], overview=0)

    def overview(user_id, ws_id):
        fake.overview += 1
        return {"items": fake.items}

    def start(user_id, workspace_id, chat_id, **kw):
        fake.started.append((user_id, workspace_id, chat_id, kw))
        return {"chat": {"id": 5}}

    monkeypatch.setattr(ai, "load_config", lambda user_id, purpose: {"purpose": purpose})
    monkeypatch.setattr(ai, "check_budget", lambda config: fake.budget.append(config))
    monkeypatch.setattr(experiments, "experiments_overview", overview)
    monkeypatch.setattr(ai_agent, "_start_with_digest", start)
    return fake


def test_experiment_review_starts_chat_with_digest(review):
    assert ai_agent.start_experiment_review(1, 2, 1, chat_id=7) == {"chat": {"id": 5}}
    assert review.budget == [{"purpose": "chat"}]
    assert review.started == [(1, 2, 7, {
        "text": "Разбери итоги эксперимента «Казань»: подтвердилась ли гипотеза и что делать дальше",
        "title": "Итоги: Казань",
        "tool": "get_experiments",
        "args": {"experiment_id": 1},
        "digest": experiments.digest(review.items, {1}),
        "skip_reason": "Пропущено: пользователь запустил разбор итогов эксперимента",
    })]


def test_experiment_review_refusals(review, monkeypatch):
    with pytest.raises(YandexError) as exc:
        ai_agent.start_experiment_review(1, 2, 99)
    assert (exc.value.code, exc.value.message) == ("not-found", "Эксперимент не найден")

    def no_ai(config):
        raise YandexError("ai-off", "ИИ не подключён")

    monkeypatch.setattr(ai, "check_budget", no_ai)
    review.overview = 0
    with pytest.raises(YandexError):
        ai_agent.start_experiment_review(1, 2, 1)
    assert (review.overview, review.started) == (0, [])  # без ИИ — даже статистику не считаем
