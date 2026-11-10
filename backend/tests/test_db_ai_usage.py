"""Единицы ИИ: класс модели на задачу, списание за вызов, месячный лимит, оценка массовой разметки, админка.

Провайдер — подделка httpx.post: отвечает разметкой «целевой» и отдаёт usage (или не отдаёт).
"""

import json
import math
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai, ai_agent, billing, wordstat
from app import query_cleaning as qc
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import admin_headers, make_account, make_campaign, make_connection, make_user, make_workspace, set_units_limit, setup_ai
from tests.test_db_query_cleaning import _query

# Единиц и себестоимости (₽) за 1 млн токенов входа / выхода.
PRICES = {
    "standard": {"units_in": 100, "units_out": 400, "cost_in": 50, "cost_out": 200},
    "fast": {"units_in": 10, "units_out": 40, "cost_in": 5, "cost_out": 20},
}


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


def _seed(db, queries=0, prices=PRICES):
    user = make_user(db, "spend@example.com")
    acc = make_account(db, user, make_connection(db, user), login="client-1")
    camp = make_campaign(db, user, acc, 101, "Кухни поиск")
    for n in range(queries):
        _query(db, user, acc, camp, None, f"кухня модель {n}", clicks=1, cost_rub=10 + n)
    ws = make_workspace(db, user, 555, "Кухни", campaigns=[camp])
    setup_ai(model="gpt-smart", fast="gpt-fast", prices=prices)
    return user, ws


def _usage_rows(db, user):
    with db.connection() as c:
        return c.execute("SELECT * FROM ai_usage WHERE user_id = %s ORDER BY id", (user,)).fetchall()


def _spend(db, user, units, *, days_ago=0, kind="model", model_class="standard", cost=None, ws=None):
    with db.connection() as c:
        c.execute(
            """
            INSERT INTO ai_usage (user_id, workspace_id, kind, task, model, model_class, charged, cost_rub, created_at)
            VALUES (%s, %s, %s, 'chat', 'gpt-smart', %s, %s, %s, %s)
            """,
            (user, ws, kind, model_class, units, cost, billing.month_start() + timedelta(hours=1) - timedelta(days=days_ago)),
        )


def test_class_per_task_prices_and_keep(db):
    user, _ws = _seed(db)
    chat = ai.load_config(user, "chat")
    assert (chat["model"], chat["model_class"], chat["class_name"], chat["units_in"]) == ("gpt-smart", "standard", "Стандартная", 100.0)
    fast = ai.load_config(user, "classify")
    assert (fast["model"], fast["model_class"], fast["units_out"], fast["cost_in"]) == ("gpt-fast", "fast", 40.0, 5.0)
    assert ai.load_config(user, "brief")["model"] == "gpt-fast"
    assert ai.load_config(user, "ads")["model"] == "gpt-smart"

    # Класс фоновой задачи — из админки; неизвестные задачи и классы пропускаются.
    saved = ai.save_service_settings(provider="openai", task_classes={"classify": "standard", "brief": "huge", "x": "fast"})
    assert saved["task_classes"]["classify"] == "standard" and saved["task_classes"]["brief"] == "fast"
    assert ai.load_config(user, "classify")["model"] == "gpt-smart"
    assert [t["id"] for t in saved["tasks"]] == ["chat", "classify", "brief", "placements", "ads"]
    # Сохранение без классов (как «Удалить ключ») их не трогает.
    kept = ai.save_service_settings(provider="openai", api_key=None)
    assert kept["classes"][0]["units_in"] == 10.0 and "default_units_limit" not in kept and kept["has_key"]
    # Выключенный быстрый класс — фоновые задачи уходят на стандартный.
    ai.save_service_settings(provider="openai", task_classes={"classify": "fast"},
                             classes=[{"id": "fast", "model": "gpt-fast", "enabled": False}])
    assert ai.load_config(user, "classify")["model_class"] == "standard"

    for bad in (
        {"units_in": 5, "units_out": None},
        {"cost_in": None, "cost_out": 3},
        {"units_in": -1, "units_out": 1},
        {"units_in": "дорого", "units_out": 1},
    ):
        with pytest.raises(YandexError) as err:
            ai.save_service_settings(provider="openai", classes=[{"id": "standard", "model": "gpt-smart", "enabled": True, **bad}])
        assert err.value.code == "ai-price"

    setup_ai(provider="yandexgpt", api_key=None, folder_id="b1g", model="yandexgpt/latest", fast="yandexgpt-lite/latest")
    cfg = ai.load_config(user, "classify")
    assert cfg["model"] == "gpt://b1g/yandexgpt-lite/latest" and cfg["model_name"] == "yandexgpt-lite/latest"


def test_complete_charges_units_and_meter(db, monkeypatch):
    user, ws = _seed(db)
    _provider(monkeypatch)
    cfg = ai.load_config(user, "chat", ws)
    with ai.meter() as outer:
        reply = ai.complete(cfg, [{"role": "user", "content": "привет"}])
        with ai.meter() as inner:
            ai.complete(cfg, [{"role": "user", "content": "ещё"}])
    assert reply["content"] == "готов" and reply["usage"]["tokens_in"] == 1000
    # 1000 × 100 + 200 × 400 единиц за миллион = 0,18 единицы за вызов; себестоимость — 0,09 ₽.
    assert outer["calls"] == 2 and round(outer["units"], 4) == 0.36 and not outer["estimated"]
    assert inner["calls"] == 1 and inner["tokens_out"] == 200
    rows = _usage_rows(db, user)
    assert [(r["kind"], r["task"], r["model"], r["model_class"], r["workspace_id"], r["tokens_in"], r["estimated"]) for r in rows] == [
        ("model", "chat", "gpt-smart", "standard", ws, 1000, False),
        ("model", "chat", "gpt-smart", "standard", ws, 1000, False),
    ]
    assert float(rows[0]["charged"]) == 0.18 and float(rows[0]["cost_rub"]) == 0.09 and rows[0]["chars_in"] > 0

    # Провайдер без usage — оценка по символам, помечена.
    _provider(monkeypatch, tokens=None)
    ai.complete(ai.load_config(user, "brief", ws), [{"role": "user", "content": "x" * 250}])
    last = _usage_rows(db, user)[-1]
    assert last["estimated"] and last["task"] == "brief" and (last["model"], last["model_class"]) == ("gpt-fast", "fast")
    assert last["tokens_in"] == math.ceil(last["chars_in"] / ai.CHARS_PER_TOKEN)

    # Без цен класса — бесплатно для пользователя, токены всё равно пишутся.
    setup_ai(api_key=None, model="gpt-smart", fast="gpt-fast")
    _provider(monkeypatch)
    with ai.meter() as m:
        ai.complete(ai.load_config(user, "chat"), [{"role": "user", "content": "привет"}])
    row = _usage_rows(db, user)[-1]
    assert m["units"] == 0 and float(row["charged"]) == 0 and row["cost_rub"] is None and row["tokens_in"] == 1000

    # Без лимита — только куда ушло (доли расхода), без процента лимита; ни токенов, ни денег.
    summary = billing.user_summary(user)
    assert not summary["limited"] and summary["used_percent"] is None and not summary["exhausted"]
    assert {k["id"]: k["calls"] for k in summary["by_kind"]} == {"model:standard": 3, "model:fast": 1}
    assert sum(k["percent"] for k in summary["by_kind"]) == pytest.approx(100, abs=0.2)
    assert {p["label"] for p in summary["by_project"]} == {"Кухни", "Без проекта"}
    assert not any(word in json.dumps(summary) for word in ("token", "cost", "rub", "units"))
    assert ai.usage_out(outer, user) == {"calls": 2, "percent": None, "estimated": False}

    # С лимитом — доля лимита.
    set_units_limit(user, 10)
    spent = sum(float(r["charged"]) for r in _usage_rows(db, user))
    summary = billing.user_summary(user)
    assert summary["limited"] and summary["used_percent"] == pytest.approx(spent * 10, abs=0.06)
    assert summary["left_percent"] == pytest.approx(100 - spent * 10, abs=0.06)
    assert ai.usage_out(outer, user) == {"calls": 2, "percent": 3.6, "estimated": False}


def test_limit_blocks_paid_calls_except_admin_check(db, monkeypatch):
    user, ws = _seed(db)
    bodies = _provider(monkeypatch)
    set_units_limit(user, 1)
    _spend(db, user, 5, days_ago=40)  # прошлый месяц не считается
    _spend(db, user, 0.9)
    cfg = ai.load_config(user, "chat", ws)
    assert round(ai.budget_left(cfg), 2) == 0.1
    ai.complete(cfg, [{"role": "user", "content": "привет"}])  # 0,9 + 0,18 — уже за лимитом
    with pytest.raises(YandexError) as exc:
        ai.complete(cfg, [{"role": "user", "content": "ещё"}])
    assert exc.value.code == "units-exhausted" and exc.value.message == billing.exhausted_text(billing.period(user)[1])
    assert len(bodies) == 1
    summary = billing.user_summary(user)
    assert summary["exhausted"] and summary["used_percent"] == 100.0 and summary["left_percent"] == 0.0

    chat = ai_agent.create_chat(user, ws)
    with pytest.raises(YandexError) as exc:
        ai_agent.post_message(user, chat["id"], "Сводка?")
    assert exc.value.code == "units-exhausted"

    # Вордстат — та же проверка до запроса.
    wordstat.save_settings(folder_id="b1g", api_key="ws-key", units_per_request=1)
    monkeypatch.setattr(wordstat, "post_json", lambda *a, **k: pytest.fail("запрос к Вордстату при исчерпанном лимите"))
    with pytest.raises(YandexError) as exc:
        wordstat.Client(user, ws).top_requests("диван")
    assert exc.value.code == "units-exhausted"

    # Проверка подключения в админке не списывает единиц и работает при любом лимите.
    rows_before = len(_usage_rows(db, user))
    checked = ai.check_connection()
    assert checked["ok"] and [(m["label"], m["model"], m["reply"]) for m in checked["models"]] == [
        ("Быстрая", "gpt-fast", "готов"),
        ("Стандартная", "gpt-smart", "готов"),
    ]
    assert len(_usage_rows(db, user)) == rows_before

    # Админ поднял лимит — снова работает; не хватит на операцию — понятная ошибка.
    set_units_limit(user, 100)
    ai.complete(cfg, [{"role": "user", "content": "снова"}])
    with pytest.raises(YandexError) as exc:
        billing.check(user, 1000)
    assert exc.value.message == billing.NOT_ENOUGH
    # Лимит 0 — платное закрыто, шкала полная; лимит снят — без ограничений.
    set_units_limit(user, 0)
    with pytest.raises(YandexError) as exc:
        billing.check(user)
    assert exc.value.code == "units-exhausted" and "снова заработают" in exc.value.message
    assert billing.user_summary(user)["used_percent"] == 100.0
    set_units_limit(user, None)
    assert billing.units_limit(user) is None
    set_units_limit(user, 2)
    assert billing.units_limit(user) == 2.0
    with pytest.raises(YandexError):
        set_units_limit(user, "много")


def test_estimate_then_classify_stops_at_limit(db, monkeypatch):
    # Дорогой быстрый класс: оценка пачки — десятки единиц, лимит — с точностью до копеек единицы.
    prices = {**PRICES, "fast": {"units_in": 10_000, "units_out": 40_000, "cost_in": 50, "cost_out": 200}}
    user, ws = _seed(db, queries=90, prices=prices)
    est = qc.estimate_classify(user, {"workspace_id": ws, "limit": 500})
    assert (est["queries"], est["rules"], est["ai"], est["batches"]) == (90, 0, 90, 3)
    assert est["percent"] is None and est["left_percent"] is None and est["fits"] is None and not est["calibrated"]
    assert set(est) == {"queries", "rules", "ai", "batches", "percent", "left_percent", "calibrated", "fits", "ai_error"}

    # Лимит на полторы пачки: оценка говорит «влезет 40», разметка останавливается после первой.
    plan = qc._classify_plan(user, {"workspace_id": ws, "limit": 500})
    cfg = ai.load_config(user, "classify", ws)
    system = qc._classify_system(user, plan)
    first = ai.units_for(cfg, *qc._batch_tokens(system, plan["rest"][:40], ai.CHARS_PER_TOKEN, qc.OUT_TOKENS_PER_QUERY))
    limit = round(first * 1.5, 2)
    set_units_limit(user, limit)
    est = qc.estimate_classify(user, {"workspace_id": ws, "limit": 500})
    assert est["fits"] == 40 and est["left_percent"] == 100.0 and est["percent"] > 100

    bodies = _provider(monkeypatch, tokens=(10_000, 2_000))  # фактически дороже оценки: 180 единиц за пачку
    res = qc.classify_queries(user, {"workspace_id": ws, "limit": 500})
    assert len(bodies) == 1 and res["labeled"] == 40 and res["target"] == 40
    assert res["ai_error"] == billing.exhausted_text(billing.period(user)[1])
    assert res["usage"] == {"calls": 1, "percent": round(180 * 100 / limit, 2), "estimated": False}
    row = _usage_rows(db, user)[-1]
    assert (row["task"], row["model"], row["model_class"], row["units"], row["workspace_id"]) == ("classify", "gpt-fast", "fast", 40, ws)
    assert float(row["charged"]) == 180
    # Лимита не осталось совсем: оценка — ноль запросов, разметка без правил — ошибка.
    est = qc.estimate_classify(user, {"workspace_id": ws, "limit": 500})
    assert est["fits"] == 0 and est["left_percent"] == 0.0
    with pytest.raises(YandexError) as exc:
        qc.classify_queries(user, {"workspace_id": ws, "limit": 500})
    assert exc.value.code == "units-exhausted"


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
    assert est["calibrated"] and est["batches"] == 1


def test_estimate_without_ai_and_usage_api(db, monkeypatch):
    user, ws = _seed(db, queries=3)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    got = client.post(f"/api/web/workspaces/{ws}/queries/classify/estimate", headers=auth, json={"limit": 300}).json()
    assert got["ai"] == 3 and got["batches"] == 1 and "model" not in got and got["percent"] is None
    other = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    assert client.post(f"/api/web/workspaces/{ws}/queries/classify/estimate", headers=other, json={}).status_code == 404

    usage = client.get("/api/web/ai/usage", headers=auth).json()
    assert not usage["limited"] and usage["used_percent"] is None and usage["by_kind"] == []
    set_units_limit(user, 50)
    _spend(db, user, 5, ws=ws)
    _spend(db, user, 5, kind="wordstat", model_class="")
    usage = client.get("/api/web/ai/usage", headers=auth).json()
    assert (usage["used_percent"], usage["left_percent"], usage["exhausted"]) == (20.0, 80.0, False)
    assert {(k["label"], k["percent"]) for k in usage["by_kind"]} == {("Стандартная", 10.0), ("Вордстат", 10.0)}
    assert {(p["label"], p["percent"]) for p in usage["by_project"]} == {("Кухни", 10.0), ("Без проекта", 10.0)}

    # Ключ ИИ удалили в админке — оценка честно говорит, что ИИ недоступен.
    ai.save_service_settings(provider="openai", api_key="")
    est = qc.estimate_classify(user, {"workspace_id": ws})
    assert est["ai"] == 3 and est["batches"] == 0 and "администратор" in est["ai_error"]


def test_admin_usage_and_user_limits(db, monkeypatch):
    user, ws = _seed(db)
    admin = make_user(db, "owner@example.com")
    _spend(db, user, 10, cost=2, ws=ws)
    _spend(db, user, 1, kind="wordstat", model_class="", cost=0.02)
    _spend(db, user, 40, kind="image", model_class="", cost=4)
    _spend(db, user, 7, days_ago=40, cost=1)  # прошлый месяц

    out = billing.service_usage()
    assert out["total"] == {"calls": 3, "units": 51.0, "cost_rub": 6.02}
    assert [(i["label"], i["units"], i["cost_rub"]) for i in out["by_kind"]] == [
        ("Картинки", 40.0, 4.0), ("Модель: Стандартная", 10.0, 2.0), ("Вордстат", 1.0, 0.02),
    ]
    assert out["by_user"] == [{"user_id": user, "email": "spend@example.com", "name": "", "calls": 3, "units": 51.0, "cost_rub": 6.02}]
    assert len(out["months"]) >= 2 and out["months"][0] == out["month"]
    with pytest.raises(YandexError):
        billing.service_usage("2026-13")

    client = TestClient(app)
    session = {"Authorization": f"Bearer {make_session(admin)}"}
    assert client.get("/api/admin/usage", headers=session).status_code == 401
    auth = admin_headers()
    assert client.get("/api/admin/usage", headers=auth).json()["total"]["units"] == 51.0
    assert client.get("/api/admin/usage?month=май", headers=auth).status_code == 400

    put = client.put(f"/api/admin/users/{user}/subscription", headers=auth, json={"overrides": {"units_limit": 100}})
    assert put.status_code == 200 and put.json()["units_limit"] == 100.0
    users = client.get("/api/admin/users", headers=auth).json()
    row = next(u for u in users["items"] if u["id"] == user)
    usage = {k: row["usage"][k] for k in ("spent", "limit", "cost_rub", "calls")}
    assert usage == {"spent": 51.0, "limit": 100.0, "cost_rub": 6.02, "calls": 3}
    assert row["subscription"]["overrides"] == {"units_limit": 100.0} and row["subscription"]["plan"] is None
    assert client.put("/api/admin/users/999999/subscription", headers=auth, json={"overrides": {}}).status_code == 404
    cleared = client.put(f"/api/admin/users/{user}/subscription", headers=auth, json={"overrides": {"units_limit": None}})
    assert cleared.json()["units_limit"] is None
