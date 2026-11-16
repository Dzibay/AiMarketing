"""Страница «Расходы на ИИ» админки: обзор, модели, задачи, люди и тарифы, надёжность, журнал; фильтры вида и источника."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import plans
from app.clock import now_msk
from app.main import app
from tests.dbutil import admin_headers, make_plan, make_user


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _use(pool, uid, task, cost, at, *, kind="model", model="gpt-test", cls="standard", tin=0, tout=0, cached=0,
         charged=0, chat=None, source="cabinet", fallback_from="", reason=""):
    _sql(
        pool,
        """
        INSERT INTO ai_usage (user_id, kind, task, model, model_class, tokens_in, tokens_out, tokens_cached, charged,
                              cost_rub, chat_id, source, fallback_from, fallback_reason, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (uid, kind, task, model, cls, tin, tout, cached, charged, cost, chat, source, fallback_from, reason, at),
    )


def _err(pool, uid, at, *, model="gpt-test", code="timeout", source="cabinet", fallback=False, answered=False, by=""):
    _sql(
        pool,
        """
        INSERT INTO ai_errors (user_id, created_at, source, task, model, model_class, code, message, fallback,
                               answered, answered_by)
        VALUES (%s, %s, %s, 'chat', %s, 'standard', %s, 'не дождались', %s, %s, %s)
        """,
        (uid, at, source, model, code, fallback, answered, by),
    )


@pytest.fixture
def client(db):
    return TestClient(app)


@pytest.fixture
def world(db):
    """a — «Про», оплатил 1000 ₽; чат (2 вызова, 40 ₽, 2 вопроса), объявления запасной моделью (5 ₽).
    b — пробный, без оплат: разметка 2 ₽ и Вордстат 1 ₽ — убыточный. Лендинг — проверка сайта 4 ₽.
    40 дней назад — чат a за 100 ₽ (прошлый отрезок месяца). Ошибки: подмена с ответом, потерянный вызов, лендинг."""
    now = now_msk()
    hour = now - timedelta(hours=1)
    _sql(db, "UPDATE ai_model_classes SET cost_in = 1000, cost_out = 2000, cost_cache = 100 WHERE id = 'standard'")
    pro = make_plan(name="Про", price_rub=1000, days=30, units_limit=100)
    trial = make_plan(name="Пробный", price_rub=0, days=7, is_trial=True, units_limit=50)
    a = make_user(db, "a@example.com")
    b = make_user(db, "b@example.com")
    plans.set_subscription(a, plan_id=pro["id"], until=(now.date() + timedelta(days=20)).isoformat())
    plans.set_subscription(b, plan_id=trial["id"], until=(now.date() + timedelta(days=3)).isoformat())
    chat = _sql(db, "INSERT INTO ai_chats (user_id, title) VALUES (%s, 'Почему мало заявок') RETURNING id", (a,)).fetchone()["id"]

    _sql(
        db,
        """
        INSERT INTO payments (user_id, plan_id, plan_name, days, kind, amount_rub, status, refunded_rub, test, paid_at)
        VALUES (%s, %s, 'Про', 30, 'renew', 1000, 'succeeded', 0, FALSE, %s)
        """,
        (a, pro["id"], now - timedelta(days=2)),
    )
    _use(db, a, "chat", 10, hour, tin=2000, cached=1000, tout=100, charged=10, chat=chat)
    _use(db, a, "chat", 30, hour, tin=1000, tout=500, charged=10, chat=chat)
    _use(db, a, "ads", 5, hour, model="reserve-x", tin=1000, tout=1000, charged=5, fallback_from="gpt-test", reason="timeout")
    _use(db, b, "classify", 2, hour, cls="fast", model="lite", tin=500, tout=50, charged=1)
    _use(db, b, "wordstat", 1, hour, kind="wordstat", model="", cls="", charged=2)
    _use(db, None, "site_check", 4, hour, source="landing", tin=300, tout=30)
    _use(db, a, "chat", 100, now - timedelta(days=40), tin=1000, tout=100, charged=50, chat=chat)
    for _ in range(2):
        _sql(db, "INSERT INTO user_events (user_id, kind, meta, created_at) VALUES (%s, 'ai_question', %s, %s)",
             (a, Jsonb({"chat_id": chat}), hour))

    _err(db, a, hour, fallback=True, answered=True, by="reserve-x")
    _err(db, a, hour, code="rate_limit")
    _err(db, None, hour, source="landing")
    return {"a": a, "b": b, "chat": chat, "now": now}


def _get(client, tab, **params):
    res = client.get(f"/api/admin/ai-costs/{tab}", headers=admin_headers(), params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_overview_cards_and_series(world, client):
    data = _get(client, "overview", p="30d")
    n = data["numbers"]
    assert n["cost_rub"] == 52.0 and n["calls"] == 6
    assert n["questions"] == 2 and n["question_rub"] == 20.0
    assert n["landing_rub"] == 4.0
    assert n["units"] == 28.0 and n["markup"] == round(28 / 52, 2)
    assert n["cache_share"] == round(1000 * 100 / 4800, 1)
    assert n["cache_saved_rub"] == 0.9
    assert data["changes"]["cost_rub"] == -48.0  # 52 против 100
    keys = {p["key"] for p in data["parts"]}
    assert keys == {"class:standard", "class:fast", "wordstat"}
    assert len(data["series"]) == 30
    assert sum(sum(s["rub"].values()) for s in data["series"]) == pytest.approx(52)

    kinds = _get(client, "overview", p="30d", split="kind")
    assert {p["key"] for p in kinds["parts"]} == {"model", "wordstat"}

    # Фильтры: только Вордстат, только лендинг.
    assert _get(client, "overview", p="30d", kind="wordstat")["numbers"]["cost_rub"] == 1.0
    landing = _get(client, "overview", p="30d", source="landing")["numbers"]
    assert (landing["cost_rub"], landing["questions"], landing["question_rub"]) == (4.0, 0, None)
    assert client.get("/api/admin/ai-costs/overview", headers=admin_headers(), params={"kind": "x"}).status_code == 400


def test_models_tab(world, client):
    data = _get(client, "models", p="7d")
    items = {i["model"]: i for i in data["items"]}
    assert set(items) == {"gpt-test", "reserve-x", "lite"}  # Вордстат — не модель
    gpt = items["gpt-test"]
    assert (gpt["calls"], gpt["cost_rub"], gpt["tokens_in"], gpt["tokens_cached"]) == (3, 44.0, 3300, 1000)
    assert items["reserve-x"]["substitutes"] == 1 and items["reserve-x"]["class_name"] == "Стандартная"
    assert data["total_rub"] == 51.0
    assert {line["label"] for line in data["lines"]} == {"gpt-test", "reserve-x", "lite"}


def test_tasks_tab_and_chat_block(world, client):
    data = _get(client, "tasks", p="30d")
    items = {i["task"]: i for i in data["items"]}
    assert items["chat"]["cost_rub"] == 40.0 and items["chat"]["label"] == "ИИ-чат"
    assert items["wordstat"]["label"] == "Вордстат"
    assert items["site_check"]["landing"] is True
    chat = data["chat"]
    assert (chat["questions"], chat["calls"], chat["question_rub"], chat["calls_per_question"]) == (2, 2, 20.0, 1.0)
    top = chat["top"][0]
    assert (top["chat_id"], top["title"], top["email"], top["questions"], top["cost_rub"]) == (
        world["chat"], "Почему мало заявок", "a@example.com", 2, 40.0,
    )


def test_people_tab(world, client):
    data = _get(client, "people", p="30d")
    by_plan = {g["key"]: g for g in data["by_plan"]}
    assert by_plan["trial"]["users"] == 1 and by_plan["trial"]["cost_rub"] == 3.0
    pro = next(g for k, g in by_plan.items() if k.startswith("plan:"))
    assert (pro["name"], pro["revenue_rub"], pro["cost_rub"], pro["margin_rub"]) == ("Про", 1000.0, 45.0, 955.0)
    assert pro["margin_pct"] == 95.5
    rows = {r["email"]: r for r in data["items"]}
    assert rows["b@example.com"]["loss"] and not rows["a@example.com"]["loss"]
    assert data["losses"] == 1 and data["items"][0]["email"] == "a@example.com"  # дороже — выше
    assert rows["a@example.com"]["limit"] == 100.0

    asc = _get(client, "people", p="30d", sort="cost", desc="false")
    assert asc["items"][0]["email"] == "b@example.com"
    only = _get(client, "people", p="30d", loss="true")
    assert [r["email"] for r in only["items"]] == ["b@example.com"]


def test_reliability_tab(world, client):
    data = _get(client, "reliability", p="7d")
    n = data["numbers"]
    assert (n["calls"], n["substitutions"], n["errors"], n["lost"]) == (5, 1, 3, 2)
    gpt = next(m for m in data["models"] if m["model"] == "gpt-test")
    assert gpt["errors"] == 3 and gpt["replaced_by"] == [{"model": "reserve-x", "calls": 1}]
    reserve = next(m for m in data["models"] if m["model"] == "reserve-x")
    assert reserve["replaced"] == [{"model": "gpt-test", "calls": 1}]
    assert {r["code"]: r["count"] for r in data["reasons"]} == {"timeout": 2, "rate_limit": 1}
    # Запасная взяла 5 ₽; по ставкам стандартной было бы 1000·1000/1e6 + 1000·2000/1e6 = 3 ₽.
    assert data["overpay"] == {"calls": 1, "actual_rub": 5.0, "planned_rub": 3.0, "overpay_rub": 2.0, "unpriced": 0}
    assert len(data["recent"]) == 3

    assert _get(client, "reliability", p="7d", source="landing")["numbers"]["errors"] == 1
    assert _get(client, "reliability", p="7d", kind="wordstat")["numbers"]["errors"] == 0


def test_journal_tab(world, client):
    data = _get(client, "journal", p="30d")
    assert data["total"]["calls"] == 6 and data["total"]["cost_rub"] == 52.0
    by_cost = _get(client, "journal", p="30d", sort="cost")
    assert by_cost["items"][0]["cost_rub"] == 30.0
    subs = _get(client, "journal", p="30d", subs="true")
    assert [i["model"] for i in subs["items"]] == ["reserve-x"] and subs["items"][0]["fallback_from"] == "gpt-test"
    mine = _get(client, "journal", p="30d", user="b@exa")
    assert mine["total"]["calls"] == 2
    assert _get(client, "journal", p="30d", user=str(world["a"]), task="chat")["total"]["calls"] == 2
    assert {t["value"] for t in data["options"]["tasks"]} == {"chat", "ads", "classify", "wordstat", "site_check"}
    # Всё время — и вызов 40 дней назад.
    assert _get(client, "journal", p="all")["total"]["calls"] == 7


def test_feedback_summary(world, client, db):
    _sql(
        db,
        "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, 'assistant', 'ок') RETURNING id",
        (world["chat"],),
    )
    res = client.get("/api/admin/feedback/summary", headers=admin_headers(), params={"p": "7d"})
    assert res.status_code == 200
    assert res.json()["period"]["preset"] == "7d" and res.json()["models"] == []
