"""Строка «как дела» без БД: каждое правило и их порядок."""

import re

import pytest

from app.overview import headline, kpi_state

# Термины, которых не должно быть в строке обычного режима.
JARGON = re.compile(r"\b(CPA|CTR|CPC|CR|ДРР|KPI|ROI|ROMI)\b")


def _facts(**over) -> dict:
    """Проект, у которого всё хорошо: 7 дней, 42 заявки по 810 ₽ при цели 900 ₽, неделей раньше — так же."""
    base = {
        "days": 7,
        "accounts": 1,
        "stats_loaded": True,
        "token_lost": False,
        "money_out": False,
        "money_days": 12.0,
        "rejected": [],
        "stopped": [],
        "kpi": kpi_state("cpa", 900, 42 * 81000, 42, 0),
        "project": "",
        "cost": 42 * 81000,
        "conversions": 42,
        "prev_cost": 40 * 81000,
        "prev_conversions": 40,
        "worst": None,
        "all_in_goal": False,
        "goal_link": "/w/5/settings",
        "chat": "/w/5/chat",
        "campaigns": "/w/5/campaigns",
    }
    return {**base, **over}


def _check(h: dict) -> dict:
    assert h["tone"] in ("ok", "warn", "bad")
    assert not JARGON.search(h["text"]), h["text"]
    # Числа с неразрывными пробелами (как в rub) — в проверках удобнее обычные.
    return {**h, "text": h["text"].replace(" ", " ")}


def test_on_plan():
    h = _check(headline(_facts()))
    assert h == {
        "tone": "ok",
        "text": "Всё идёт по плану: за 7 дней 42 заявки по 810 ₽, цель — 900 ₽",
        "link": None,
    }


def test_on_plan_without_goal_offers_to_set_it():
    h = _check(headline(_facts(kpi=None)))
    assert h["tone"] == "ok"
    assert h["text"].startswith("За 7 дней 42 заявки по 810 ₽. Задайте цель")
    assert h["link"] == {"label": "Задать цель", "to": "/w/5/settings"}

    # На главной с несколькими проектами цель задаётся в каждом — ссылки нет.
    h = _check(headline(_facts(kpi=None, goal_link=None, all_in_goal=True)))
    assert h["text"] == "Всё идёт по плану: за 7 дней 42 заявки по 810 ₽, все проекты укладываются в цель"
    assert _check(headline(_facts(kpi=None, goal_link=None)))["text"] == "За 7 дней 42 заявки по 810 ₽"


def test_on_plan_share_of_revenue_and_30_days():
    kpi = kpi_state("drr", 12.5, 9000, 3, 100000)
    h = _check(headline(_facts(days=30, kpi=kpi)))
    assert h["text"] == "Всё идёт по плану: за 30 дней реклама обошлась в 9% от выручки, цель — 12,5%"


def test_spend_without_leads_and_no_goal():
    h = _check(headline(_facts(kpi=None, conversions=0, prev_conversions=0)))
    assert h["tone"] == "warn"
    assert h["text"] == "За 7 дней потрачено 34 020 ₽, а заявок пока нет"
    assert h["link"]["to"] == "/w/5/chat" and "Почему так" in h["link"]["ask"]


@pytest.mark.parametrize(
    ("facts", "tone", "text"),
    [
        ({"accounts": 0}, "warn", "Директ ещё не подключён — сводке пока не из чего считать"),
        ({"stats_loaded": False}, "ok", "Загружаем статистику из Директа — цифры появятся в течение нескольких минут"),
    ],
)
def test_data_not_ready(facts, tone, text):
    h = _check(headline(_facts(**facts)))
    assert (h["tone"], h["text"]) == (tone, text)


def test_lost_access_beats_everything_but_no_accounts():
    h = _check(headline(_facts(token_lost=True, money_out=True, stats_loaded=False)))
    assert h["tone"] == "bad" and h["text"].startswith("Нет доступа к Яндексу")
    assert h["link"]["to"] == "/cabinet/connections"


def test_ads_not_running():
    h = _check(headline(_facts(money_out=True, rejected=[{"name": "Окна", "to": "/w/5/campaigns/9"}])))
    assert (h["tone"], h["text"]) == ("bad", "Реклама не показывается: на счёте закончились деньги")
    assert h["link"]["external"] is True

    h = _check(headline(_facts(money_days=1.5)))
    assert h["text"] == "Деньги на счёте закончатся примерно через 1,5 дня"
    h = _check(headline(_facts(money_days=0.3, money_project="Окна.ру")))
    assert h["text"] == "В «Окна.ру» деньги на счёте закончатся меньше чем через день"

    rejected = [{"name": "Окна — поиск", "to": "/w/5/campaigns/9"}, {"name": "РСЯ", "to": "/w/5/campaigns/10"}]
    h = _check(headline(_facts(rejected=rejected)))
    assert h["text"] == "Реклама не показывается: кампании «Окна — поиск» и ещё 1 отклонены модерацией"
    assert h["link"] == {"label": "Открыть кампанию", "to": "/w/5/campaigns/9"}

    h = _check(headline(_facts(stopped=rejected[:1])))
    assert (h["tone"], h["text"]) == ("warn", "Кампания «Окна — поиск» перестала показываться")

    h = _check(headline(_facts(cost=0, conversions=0, kpi=kpi_state("cpa", 900, 0, 0, 0))))
    assert (h["tone"], h["text"]) == ("warn", "Реклама не показывается: за 7 дней расхода не было")
    assert h["link"]["to"] == "/w/5/campaigns"


def test_top_up_opens_the_login_that_runs_out():
    url = "https://direct.yandex.ru/dna/grid/campaigns?ulogin=okna"
    h = _check(headline(_facts(money_out=True, money_url=url)))
    assert h["link"] == {"label": "Пополнить в Директе", "to": url, "external": True}
    h = _check(headline(_facts(money_days=1.5, money_url=url)))
    assert h["link"]["to"] == url
    # Логин неизвестен — общая главная Директа.
    h = _check(headline(_facts(money_out=True)))
    assert h["link"] == {"label": "Пополнить в Директе", "to": "https://direct.yandex.ru/", "external": True}


def test_lead_price_above_goal():
    worst = {"name": "Окна — поиск", "to": "/w/5/campaigns/9"}
    kpi = kpi_state("cpa", 900, 10 * 124000, 10, 0)
    h = _check(headline(_facts(kpi=kpi, cost=10 * 124000, conversions=10, worst=worst)))
    assert h["tone"] == "bad"  # +38% к цели: дальше порога KPI_WARN_RATIO
    assert h["text"] == "Заявки подорожали: 1 240 ₽ при цели 900 ₽. Дороже всего — «Окна — поиск»"
    assert h["link"]["label"] == "Что с этим делать" and h["link"]["to"] == "/w/5/chat"

    # Чуть дороже цели — жёлтый; на главной называем проект (точка в имени не портит текст).
    kpi = kpi_state("cpa", 900, 10 * 100000, 10, 0)
    h = _check(headline(_facts(kpi=kpi, project="Окна.ру", cost=10 * 100000, conversions=10)))
    assert (h["tone"], h["text"]) == ("warn", "Заявки в «Окна.ру» подорожали: 1 000 ₽ при цели 900 ₽")

    kpi = kpi_state("cpa", 900, 540000, 0, 0)
    h = _check(headline(_facts(kpi=kpi, cost=540000, conversions=0)))
    assert h["text"] == "За 7 дней потрачено 5 400 ₽, а заявок нет — при цели 900 ₽ за заявку"

    kpi = kpi_state("drr", 12, 18000, 0, 100000)
    h = _check(headline(_facts(kpi=kpi)))
    assert h["text"] == "Реклама обходится дороже цели: 18% от выручки при цели 12%"


def test_noticeable_change_against_previous_period():
    h = _check(headline(_facts(kpi=None, conversions=18, prev_conversions=30)))
    assert (h["tone"], h["text"]) == ("warn", "Заявок стало меньше: 18 заявок за 7 дней против 30 за прошлые 7 дней")
    assert h["link"]["ask"]

    h = _check(headline(_facts(days=30, conversions=42, prev_conversions=30)))
    assert (h["tone"], h["text"]) == ("ok", "Заявок стало больше: 42 заявки за 30 дней против 30 за прошлые 30 дней")

    h = _check(headline(_facts(kpi=None, cost=1_200_000, prev_cost=3_000_000)))
    assert (h["tone"], h["text"]) == ("warn", "Расход упал: 12 000 ₽ за 7 дней против 30 000 ₽ за прошлые 7 дней")

    # Мелкая база не тревожит: 2 → 1 заявка и 500 → 50 ₽.
    h = _check(headline(_facts(kpi=None, conversions=1, prev_conversions=2, cost=5000, prev_cost=50000)))
    assert h["text"].startswith("За 7 дней 1 заявка по 50 ₽")


def test_rule_order():
    """Цена выше цели важнее изменения к прошлой неделе; остановка — важнее цены."""
    kpi = kpi_state("cpa", 900, 10 * 200000, 10, 0)
    stopped = [{"name": "РСЯ", "to": "/w/5/campaigns/10"}]
    assert _check(headline(_facts(kpi=kpi, prev_conversions=40, conversions=10)))["text"].startswith("Заявки подорожали")
    assert _check(headline(_facts(kpi=kpi, stopped=stopped)))["text"].startswith("Кампания «РСЯ»")
