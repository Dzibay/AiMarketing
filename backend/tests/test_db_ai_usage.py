"""Расход ИИ: модель на задачу, запись токенов и стоимости, месячный лимит, оценка массовой разметки.

Провайдер — подделка httpx.post: отвечает разметкой «целевой» и отдаёт usage (или не отдаёт).
"""

import json
import math
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai, ai_agent
from app import query_cleaning as qc
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace
from tests.test_db_query_cleaning import _query


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")


def _provider(monkeypatch, *, tokens=(1000, 200)):
    """tokens=None — провайдер без usage; иначе (prompt_tokens, completion_tokens) на каждый вызов."""
    bodies: list[dict] = []

    def post(url, **kw):
        body = kw["json"]
        bodies.append(body)
        last = body["messages"][-1]["content"]
        if last.startswith("Запросы:"):
            items = json.loads(last.split("\n", 1)[1])
            content = json.dumps({"items": [{"id": i["id"], "verdict": "target", "reason": "наш"} for i in items]})
        else:
            content = "готов"
        data = {"choices": [{"message": {"content": content}}]}
        if tokens:
            data["usage"] = {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", post)
    return bodies


def _seed(db, queries=0):
    user = make_user(db, "spend@example.com")
    acc = make_account(db, user, make_connection(db, user), login="client-1")
    camp = make_campaign(db, user, acc, 101, "Кухни поиск")
    for n in range(queries):
        _query(db, user, acc, camp, None, f"кухня модель {n}", clicks=1, cost_rub=10 + n)
    ws = make_workspace(db, user, 555, "Кухни", campaigns=[camp])
    ai.save_settings(
        user, provider="openai", api_key="sk-test-1234abcd", model="gpt-smart", model_fast="gpt-fast",
        price_in=100, price_out=400,
    )
    return user, ws


def _usage_rows(db, user):
    with db.connection() as c:
        return c.execute("SELECT * FROM ai_usage WHERE user_id = %s ORDER BY id", (user,)).fetchall()


def _spend(db, user, rub, *, days_ago=0):
    with db.connection() as c:
        c.execute(
            "INSERT INTO ai_usage (user_id, task, model, cost_rub, created_at) VALUES (%s, 'chat', 'gpt-smart', %s, %s)",
            (user, rub, ai.month_start() + timedelta(hours=1) - timedelta(days=days_ago)),
        )


def test_model_per_task_prices_and_keep(db):
    user, _ws = _seed(db)
    assert ai.load_config(user, "chat")["model"] == "gpt-smart"
    fast = ai.load_config(user, "classify")
    assert fast["model"] == "gpt-fast" and fast["tier"] == "fast"
    assert ai.load_config(user, "brief")["model"] == "gpt-fast"
    # Без своих цен быстрая модель считается по ценам основной.
    assert (fast["price_in"], fast["price_out"]) == (100.0, 400.0)
    ai.save_settings(user, provider="openai", model="gpt-smart", fast_price_in="10,5", fast_price_out=40)
    assert ai.load_config(user, "classify")["price_in"] == 10.5
    assert ai.load_config(user, "chat")["price_in"] == 100.0

    # Сохранение без новых полей (как «Удалить ключ») их не трогает.
    kept = ai.save_settings(user, provider="openai", model="gpt-smart", api_key=None)
    assert kept["model_fast"] == "gpt-fast" and kept["price_out"] == 400.0 and kept["fast_price_in"] == 10.5
    assert [t["id"] for t in kept["tasks"]] == ["chat", "classify", "brief"]

    for bad in (
        {"price_in": 5, "price_out": None},
        {"fast_price_in": None, "fast_price_out": 3},
        {"price_in": -1, "price_out": 1},
        {"price_in": "дорого", "price_out": 1},
        {"monthly_limit_rub": 0},
        {"price_in": None, "price_out": None, "monthly_limit_rub": 100},
    ):
        with pytest.raises(YandexError):
            ai.save_settings(user, provider="openai", model="gpt-smart", **bad)

    # Та же модель, что основная, — быстрой отдельно нет.
    same = ai.save_settings(user, provider="openai", model="gpt-smart", model_fast="gpt-smart")
    assert same["model_fast"] == "" and ai.load_config(user, "classify")["model"] == "gpt-smart"

    ai.save_settings(user, provider="yandexgpt", folder_id="b1g", model="yandexgpt/latest", model_fast="yandexgpt-lite/latest")
    cfg = ai.load_config(user, "classify")
    assert cfg["model"] == "gpt://b1g/yandexgpt-lite/latest" and cfg["model_name"] == "yandexgpt-lite/latest"


def test_complete_records_usage_cost_and_meter(db, monkeypatch):
    user, ws = _seed(db)
    _provider(monkeypatch)
    cfg = ai.load_config(user, "chat", ws)
    with ai.meter() as outer:
        reply = ai.complete(cfg, [{"role": "user", "content": "привет"}])
        with ai.meter() as inner:
            ai.complete(cfg, [{"role": "user", "content": "ещё"}])
    assert reply["content"] == "готов" and reply["usage"]["tokens_in"] == 1000
    # 1000 × 100 ₽ + 200 × 400 ₽ за миллион = 0,18 ₽ за вызов.
    assert outer["calls"] == 2 and round(outer["cost_rub"], 4) == 0.36 and outer["priced"]
    assert inner["calls"] == 1 and inner["tokens_out"] == 200
    rows = _usage_rows(db, user)
    assert [(r["task"], r["model"], r["workspace_id"], r["tokens_in"], r["estimated"]) for r in rows] == [
        ("chat", "gpt-smart", ws, 1000, False),
        ("chat", "gpt-smart", ws, 1000, False),
    ]
    assert float(rows[0]["cost_rub"]) == 0.18 and rows[0]["chars_in"] > 0

    # Провайдер без usage — оценка по символам, помечена.
    _provider(monkeypatch, tokens=None)
    ai.complete(ai.load_config(user, "brief", ws), [{"role": "user", "content": "x" * 250}])
    last = _usage_rows(db, user)[-1]
    assert last["estimated"] and last["task"] == "brief" and last["model"] == "gpt-fast"
    assert last["tokens_in"] == math.ceil(last["chars_in"] / ai.CHARS_PER_TOKEN)

    # Без цен стоимость не считается, но токены пишутся.
    ai.save_settings(user, provider="openai", model="gpt-smart", price_in=None, price_out=None)
    _provider(monkeypatch)
    with ai.meter() as m:
        ai.complete(ai.load_config(user, "chat"), [{"role": "user", "content": "привет"}])
    assert not m["priced"] and _usage_rows(db, user)[-1]["cost_rub"] is None

    summary = ai.usage_summary(user)
    assert summary["total"]["calls"] == 4 and summary["total"]["estimated"] == 1 and summary["total"]["unpriced"] == 1
    assert summary["spent_rub"] == round(0.36 + float(_usage_rows(db, user)[2]["cost_rub"]), 2)
    by_task = {t["task"]: t for t in summary["by_task"]}
    assert by_task["chat"]["calls"] == 3 and by_task["chat"]["label"] == "ИИ-чат" and by_task["brief"]["calls"] == 1
    names = {w["name"] for w in summary["by_workspace"]}
    assert names == {"Кухни", "Без пространства"}
    assert {m["model"] for m in summary["by_model"]} == {"gpt-smart", "gpt-fast"}
    assert summary["months"][0] == summary["month"] and summary["current"]
    past = ai.usage_summary(user, "2020-01")
    assert past["total"]["calls"] == 0 and past["remaining_rub"] is None and not past["current"]
    with pytest.raises(YandexError):
        ai.usage_summary(user, "2026-13")


def test_monthly_limit_blocks_calls_except_check(db, monkeypatch):
    user, ws = _seed(db)
    bodies = _provider(monkeypatch)
    ai.save_settings(user, provider="openai", model="gpt-smart", monthly_limit_rub=1)
    _spend(db, user, 5, days_ago=40)  # прошлый месяц не считается
    _spend(db, user, 0.9)
    cfg = ai.load_config(user, "chat", ws)
    assert round(ai.budget_left(cfg), 2) == 0.1
    ai.complete(cfg, [{"role": "user", "content": "привет"}])  # 0,9 + 0,18 — уже за лимитом
    with pytest.raises(YandexError) as exc:
        ai.complete(cfg, [{"role": "user", "content": "ещё"}])
    assert exc.value.code == "ai-budget" and "1 ₽" in exc.value.message
    assert len(bodies) == 1

    chat = ai_agent.create_chat(user, ws)
    with pytest.raises(YandexError) as exc:
        ai_agent.post_message(user, chat["id"], "Сводка?")
    assert exc.value.code == "ai-budget"

    # Проверка подключения работает и после лимита: ей нужно убедиться, что ключ жив.
    ai.save_settings(user, provider="openai", model="gpt-smart", model_fast="gpt-fast")
    checked = ai.check_connection(user)
    assert checked["reply"] == "готов" and checked["fast"] == {"model": "gpt-fast", "reply": "готов"}
    assert ai.usage_summary(user)["remaining_rub"] < 0


def test_estimate_then_classify_stops_at_limit(db, monkeypatch):
    user, ws = _seed(db, queries=90)
    est = qc.estimate_classify(user, {"workspace_id": ws, "limit": 500})
    assert (est["queries"], est["rules"], est["ai"], est["batches"]) == (90, 0, 90, 3)
    assert est["model"] == "gpt-fast" and est["tokens_out"] == 90 * qc.OUT_TOKENS_PER_QUERY
    assert est["tokens_in"] > 0 and est["priced"] and est["cost_rub"] > 0
    assert est["fits"] is None and est["remaining_rub"] is None and not est["calibrated"]

    # Лимит на полторы пачки: оценка говорит «влезет 40», разметка останавливается после первой.
    plan = qc._classify_plan(user, {"workspace_id": ws, "limit": 500})
    cfg = ai.load_config(user, "classify", ws)
    first = ai.cost_rub(cfg, *qc._batch_tokens(qc._classify_system(user, plan), plan["rest"][:40], 2.5, 50))
    ai.save_settings(user, provider="openai", model="gpt-smart", monthly_limit_rub=round(first * 1.5, 2))
    est = qc.estimate_classify(user, {"workspace_id": ws, "limit": 500})
    assert est["fits"] == 40 and est["remaining_rub"] == round(first * 1.5, 2) and est["spent_rub"] == 0

    bodies = _provider(monkeypatch, tokens=(10_000, 2_000))  # фактически дороже оценки: 1,8 ₽ за пачку
    res = qc.classify_queries(user, {"workspace_id": ws, "limit": 500})
    assert len(bodies) == 1 and res["labeled"] == 40 and res["target"] == 40
    assert res["ai_error"].startswith("Месячного лимита на ИИ не хватает")
    assert res["usage"] == {"calls": 1, "tokens_in": 10_000, "tokens_out": 2_000, "cost_rub": 1.8, "estimated": False}
    row = _usage_rows(db, user)[-1]
    assert (row["task"], row["model"], row["units"], row["workspace_id"]) == ("classify", "gpt-fast", 40, ws)
    # Бюджета не осталось совсем: оценка — ноль запросов, разметка без правил — ошибка.
    assert qc.estimate_classify(user, {"workspace_id": ws, "limit": 500})["fits"] == 0
    with pytest.raises(YandexError) as exc:
        qc.classify_queries(user, {"workspace_id": ws, "limit": 500})
    assert exc.value.code == "ai-budget"


def test_estimate_calibrates_on_history(db):
    user, ws = _seed(db, queries=5)
    cfg = ai.load_config(user, "classify", ws)
    assert ai.chars_per_token(cfg) == ai.CHARS_PER_TOKEN
    with db.connection() as c:
        for _ in range(2):
            c.execute(
                """
                INSERT INTO ai_usage (user_id, task, model, tokens_in, tokens_out, chars_in, units)
                VALUES (%s, 'classify', 'gpt-fast', 3000, 600, 9000, 20)
                """,
                (user,),
            )
    assert ai.chars_per_token(cfg) == 3.0
    assert ai.out_tokens_per_unit(cfg, "classify", 50) == 30.0
    assert ai.out_tokens_per_unit(cfg, "chat", 50) == 50
    est = qc.estimate_classify(user, {"workspace_id": ws})
    assert est["calibrated"] and est["tokens_out"] == 5 * 30


def test_estimate_without_ai_and_api(db, monkeypatch):
    user, ws = _seed(db, queries=3)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    got = client.post(f"/api/web/workspaces/{ws}/queries/classify/estimate", headers=auth, json={"limit": 300}).json()
    assert got["ai"] == 3 and got["batches"] == 1 and got["model"] == "gpt-fast"
    other = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    assert client.post(f"/api/web/workspaces/{ws}/queries/classify/estimate", headers=other, json={}).status_code == 404

    # PUT без новых полей их не стирает; null — стирает.
    put = client.put("/api/web/ai/settings", headers=auth, json={"provider": "openai", "model": "gpt-smart", "api_key": ""})
    assert put.json()["model_fast"] == "gpt-fast" and put.json()["price_in"] == 100.0 and not put.json()["has_key"]
    put = client.put(
        "/api/web/ai/settings", headers=auth,
        json={"provider": "openai", "model": "gpt-smart", "model_fast": "", "price_in": None, "price_out": None},
    )
    assert put.json()["model_fast"] == "" and put.json()["price_in"] is None
    bad = client.put("/api/web/ai/settings", headers=auth, json={"provider": "openai", "monthly_limit_rub": 100})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "ai-price"

    est = qc.estimate_classify(user, {"workspace_id": ws})
    assert est["ai"] == 3 and est["batches"] == 0 and est["ai_error"]
    usage = client.get("/api/web/ai/usage", headers=auth).json()
    assert usage["total"]["calls"] == 0 and usage["limit_rub"] is None
    assert client.get("/api/web/ai/usage?month=май", headers=auth).status_code == 400
