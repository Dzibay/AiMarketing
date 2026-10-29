"""Эксперименты ИИ на базе: запуск и итоги по согласованию в чате, прогресс по загруженной статистике,
разбор итогов по кнопке и итог со страницы. Директ и модель — подделки."""

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import actions, ai, ai_agent, experiments
from app.clock import MSK
from app.config import settings
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_counter, make_user, make_workspace
from tests.test_db_ai import FakeModel, _call, _start, _text

TODAY = date(2026, 10, 28)  # среда: неделя Директа идёт с понедельника 26.10
SYNCED = datetime(2026, 10, 28, 6, 0, tzinfo=MSK)  # статистика загружена по 27.10
PAY_FOR_ORDERS = {
    "Search": {"BiddingStrategyType": "PAY_FOR_CONVERSION", "PayForConversion": {"Cpa": 500000000, "GoalId": 5001}},
    "Network": {"BiddingStrategyType": "SERVING_OFF"},
}
CRITERION = "конверсии за ~0 ₽ — от 2 конв. за весь срок, не дороже 1 ₽ за конверсию"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")
    # Один «сегодня» у запуска, прогресса и «было → станет».
    monkeypatch.setattr(experiments, "today_msk", lambda: TODAY)
    monkeypatch.setattr(ai_agent, "today_msk", lambda: TODAY)


def _seed(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, name="Кабинет", login="acc-a")
    counter = make_counter(db, user, conn_id, 555, site="okna.ru")
    source = make_campaign(db, user, acc, 11, "Поиск Москва")
    kazan = make_campaign(db, user, acc, 12, "Казань копия", state="OFF", status="MODERATION")
    samara = make_campaign(db, user, acc, 13, "Самара копия", state="OFF", status="DRAFT")
    ws = make_workspace(db, user, 555, "Окна", counter_row_id=counter, accounts=[acc], campaigns=[source, kazan, samara])
    ai.save_settings(user, provider="openai", api_key="sk-test-1234abcd", model="gpt-test")
    chat = ai_agent.create_chat(user, ws)
    return {"user": user, "acc": acc, "ws": ws, "source": source, "kazan": kazan, "samara": samara, "chat": chat["id"]}


def _d(day: int) -> date:
    return date(2026, 10, day)


def _flat(text: str) -> str:
    """Без неразрывных пробелов из денег."""
    return " ".join(text.split())


def _fields(m: dict) -> tuple:
    return tuple(m[f] for f in experiments.FIELDS)


def _seed_stats(db, s):
    """«Казань» показывается с 21.10, «Самара» — с 26.10; образец «Поиск Москва» — до и во время теста."""
    rows = [
        # (кампания в Директе, день, показы, клики, расход в копейках, конверсии)
        (12, 19, 500, 50, 50000, 1),  # до старта — не в счёт
        (12, 20, 0, 0, 0, 0),  # день старта без показов — срок пойдёт с 21.10
        (12, 21, 100, 10, 0, 2),
        (12, 26, 50, 5, 100, 1),
        (12, 27, 30, 3, 0, 0),
        (13, 26, 200, 20, 60000, 1),
        (13, 27, 100, 10, 35000, 0),
        (11, 6, 999, 99, 99900, 9),  # раньше окна «до старта» (07.10–20.10)
        (11, 10, 1000, 100, 200000, 4),
        (11, 20, 500, 50, 100000, 1),
        (11, 22, 800, 80, 160000, 2),
    ]
    goals = [(12, 19, 5001, 4), (12, 21, 5001, 2), (12, 21, 6001, 7), (12, 26, 5001, 1)]
    with db.connection() as conn:
        for cid, day, impressions, clicks, cost, conversions in rows:
            conn.execute(
                """
                INSERT INTO stats_daily (user_id, day, source, account_id, campaign_id, impressions, clicks,
                                         cost_kopecks, conversions)
                VALUES (%s, %s, 'direct', %s, %s, %s, %s, %s, %s)
                """,
                (s["user"], _d(day), s["acc"], cid, impressions, clicks, cost, conversions),
            )
        for cid, day, goal_id, conversions in goals:
            conn.execute(
                """
                INSERT INTO stats_daily (user_id, day, source, account_id, campaign_id, goal_id, conversions)
                VALUES (%s, %s, 'direct_goal', %s, %s, %s, %s)
                """,
                (s["user"], _d(day), s["acc"], cid, goal_id, conversions),
            )
        conn.execute(
            "UPDATE direct_accounts SET stats_synced_at = %s, load_state = %s WHERE id = %s",
            (SYNCED, Jsonb({"stats": {"goal_ids": [5001, 6001]}}), s["acc"]),
        )
        conn.execute("UPDATE direct_campaigns SET bidding_strategy = %s WHERE id = %s", (Jsonb(PAY_FOR_ORDERS), s["kazan"]))


def _experiments(s) -> tuple[int, int]:
    kazan = experiments.create_experiment(
        s["user"],
        s["ws"],
        experiments.normalize_definition(
            {
                "title": "Казань",
                "hypothesis": "Схема 0 ₽ повторится в Казани",
                "kind": "geo",
                "campaign_row_ids": [s["kazan"]],
                "source_campaign_row_id": s["source"],
                "weekly_budget_rub": 3000,
                "days": 14,
                "criterion": "zero_cost",
                "target": 2,
            }
        ),
        started_on=_d(20),
    )
    samara = experiments.create_experiment(
        s["user"],
        s["ws"],
        experiments.normalize_definition(
            {
                "title": "Самара",
                "hypothesis": "Самара даст 5 заявок за неделю",
                "kind": "geo",
                "campaign_row_ids": [s["samara"]],
                "weekly_budget_rub": 1000,
                "days": 7,
                "criterion": "conversions",
                "target": 5,
            }
        ),
        started_on=_d(25),
    )
    return kazan["id"], samara["id"]


def _clients(db, s):
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    other = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    return client, auth, other


def _experiment_row(db, exp_id: int) -> dict:
    with db.connection() as conn:
        return conn.execute("SELECT * FROM ai_experiments WHERE id = %s", (exp_id,)).fetchone()


def test_start_and_finish_through_chat_approval(db, monkeypatch):
    s = _seed(db)
    current = {
        s["kazan"]: {
            "type": "UNIFIED_CAMPAIGN",
            "daily_budget_rub": None,
            "strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF", "weekly_budget_rub": 10000.0,
                         "goal_id": 5001, "manual": False},
        },
        s["samara"]: {
            "type": "TEXT_CAMPAIGN",
            "daily_budget_rub": 1000.0,
            "strategy": {"search": "HIGHEST_POSITION", "network": "SERVING_OFF", "manual": True},
        },
    }
    updates = []
    monkeypatch.setattr(
        actions, "get_campaign_settings", lambda user_id, cid: {**current[cid], "strategy": dict(current[cid]["strategy"])}
    )
    monkeypatch.setattr(actions, "update_campaign_settings", lambda user_id, cid, changes: updates.append((cid, changes)) or {})
    start = {
        "title": "Казань",
        "hypothesis": "Схема 0 ₽ повторится в Казани и Самаре",
        "kind": "geo",
        "campaign_row_ids": [s["kazan"], s["samara"]],
        "source_campaign_row_id": s["source"],
        "weekly_budget_rub": 3000,
        "days": 14,
        "criterion": "zero_cost",
        "target": 2,
        "reason": "Проверить гео",
    }

    _fake, out = _start(s["user"], s["chat"], "Запусти тест на Казань и Самару", monkeypatch, _call("start_experiment", start))
    assert out["status"] == "waiting" and not updates
    action = ai_agent.get_chat(s["user"], s["chat"])["actions"][0]
    assert action["status"] == "pending" and action["categories"][1]["id"] == "budget"
    assert action["summary"] == (
        f"Эксперимент «Казань»: «Казань копия», «Самара копия», лимит 3 000 ₽/нед. на кампанию, 14 дней, успех — {CRITERION}"
    )
    assert [(c["what"], c["before"], c["after"]) for c in action["changes"]] == [
        ("Лимит «Казань копия»", "10 000 ₽/нед.", "3 000 ₽/нед."),
        ("Лимит «Самара копия»", "1 000 ₽/день", "428 ₽/день; у ручных ставок нет недельного бюджета — лимит задан дневным"),
        ("Срок теста", "—", "14 дней с первого дня показов, примерно до 10.11"),
        ("Критерий успеха", "—", CRITERION),
        ("Сравнение", "—", "с «Поиск Москва»"),
        ("Остановка", "—", "только по вашему решению после итогов"),
    ]

    decided = ai_agent.decide_action(s["user"], action["id"], approve=True)
    assert decided["pending"] == 0 and decided["job"]["kind"] == "ai_chat"
    fake = FakeModel(_call("get_experiments", {}, "c2"), _text("Запустил эксперимент."))
    monkeypatch.setattr(ai, "complete", fake)
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"

    done = ai_agent.get_chat(s["user"], s["chat"])["actions"][0]
    assert done["status"] == "done" and done["error"] == ""
    exp_id = done["result"]["experiment"]["id"]
    assert done["result"]["experiment"]["ends_on"] == "2026-11-10"
    assert [(r["name"], r["kind"], r["changed"]) for r in done["result"]["limits"]] == [
        ("Казань копия", "weekly", True),
        ("Самара копия", "daily", True),
    ]
    assert updates == [
        (s["kazan"], {"strategy": {"search": "WB_MAXIMUM_CONVERSION_RATE", "network": "SERVING_OFF",
                                   "weekly_budget_rub": 3000.0, "goal_id": 5001}}),
        (s["samara"], {"daily_budget_rub": 428}),
    ]
    row = _experiment_row(db, exp_id)
    assert (row["chat_id"], row["action_id"], row["workspace_id"], row["status"]) == (s["chat"], action["id"], s["ws"], "running")
    assert (row["campaign_row_ids"], row["source_campaign_row_id"], row["weekly_limit_kopecks"]) == (
        [s["kazan"], s["samara"]], s["source"], 300000
    )
    assert (row["started_on"], row["days"], row["criterion"], row["target"], row["kind"]) == (TODAY, 14, "zero_cost", 2.0, "geo")

    # Следующий ход уже знает об эксперименте: в системном промпте и в get_experiments.
    system = fake.calls[0][0]["content"]
    assert (
        f"- #{exp_id} «Казань»: с 28.10 на 14 дней, кампании «Казань копия» (campaign_row_id {s['kazan']}), "
        f"«Самара копия» (campaign_row_id {s['samara']})"
    ) in system
    digest = json.loads(fake.calls[1][-1]["content"])["digest"]
    assert f"#{exp_id} «Казань» — новое гео, идёт" in digest
    assert "Сейчас: нет статистики — статистика кабинета ещё не загружалась." in digest

    ctx = ai_agent.Context(s["user"], s["ws"])
    with pytest.raises(YandexError) as exc:
        ai_agent._p_start_experiment(ctx, {**start, "campaign_row_ids": [s["kazan"]]})
    assert exc.value.code == "busy" and f"«Казань копия» — в #{exp_id} «Казань»" in exc.value.message

    # Итоги — тоже по согласованию; кампании и лимиты не трогаются.
    finish = {"experiment_id": exp_id, "verdict": "fail", "conclusion": "Показов не было — проверим модерацию"}
    _fake, out = _start(s["user"], s["chat"], "Подведи итоги", monkeypatch, _call("finish_experiment", finish, "c3"))
    assert out["status"] == "waiting"
    action = ai_agent.get_chat(s["user"], s["chat"])["actions"][-1]
    assert (action["tool"], action["status"], action["summary"]) == (
        "finish_experiment", "pending", "Итоги эксперимента «Казань»: гипотеза не подтвердилась"
    )
    ai_agent.decide_action(s["user"], action["id"], approve=True)
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Записал итоги.")))
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"
    row = _experiment_row(db, exp_id)
    assert (row["status"], row["verdict"], row["conclusion"]) == ("finished", "fail", "Показов не было — проверим модерацию")
    assert row["finished_at"] is not None and len(updates) == 2
    # Кампании завершённого эксперимента свободны для следующего.
    assert ai_agent._p_start_experiment(ctx, {**start, "campaign_row_ids": [s["kazan"]]}).summary.startswith("Эксперимент")

    # Удалили чат — эксперимент остаётся, без ссылок на чат и действие.
    ai_agent.delete_chat(s["user"], s["chat"])
    row = _experiment_row(db, exp_id)
    assert (row["chat_id"], row["action_id"], row["status"]) == (None, None, "finished")


def test_progress_from_loaded_stats(db):
    s = _seed(db)
    _seed_stats(db, s)
    kazan, samara = _experiments(s)
    client, auth, other = _clients(db, s)

    resp = client.get(f"/api/web/workspaces/{s['ws']}/experiments", headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert data["today"] == "2026-10-28"
    assert [e["id"] for e in data["items"]] == [samara, kazan]  # оба идут — новые первыми
    items = {e["id"]: e for e in data["items"]}

    k = items[kazan]
    assert (k["status"], k["weekly_limit_rub"], k["campaign_row_ids"], k["source_campaign_row_id"]) == (
        "running", 3000.0, [s["kazan"]], s["source"]
    )
    p = k["progress"]
    assert (p["status"], p["showing_since"], p["window_start"], p["ends_on"], p["loaded_to"]) == (
        "success", "2026-10-21", "2026-10-21", "2026-11-03", "2026-10-27"
    )
    assert (p["day"], p["days_left"], p["week_from"], p["limit_hit"]) == (8, 7, "2026-10-26", False)
    assert _flat(p["reason"]) == "3 конверсии при расходе 1 ₽, цель — от 2 конв. за ~0 ₽"
    assert p["totals"] == {
        "impressions": 180, "clicks": 18, "cost_kopecks": 100, "conversions": 3, "cr_pct": 16.67, "cpa_kopecks": 33,
        "paid_conversions": 3,  # цель 5001 — до старта и цель 6001 не в счёт
    }
    camp = p["campaigns"][0]
    assert (camp["name"], camp["status"], camp["paid_conversions"]) == ("Казань копия", "MODERATION", 3)
    assert (camp["week_cost_kopecks"], camp["week_share_pct"], camp["limit_hit"]) == (100, 0, False)
    src = p["source"]
    assert (src["name"], src["before_from"], src["before_to"]) == ("Поиск Москва", "2026-10-07", "2026-10-20")
    assert _fields(src["before"]) == (1500, 150, 300000, 5)
    assert _fields(src["during"]) == (800, 80, 160000, 2)

    p = items[samara]["progress"]
    assert (p["status"], p["day"], p["ends_on"], p["reason"]) == ("collecting", 3, "2026-11-01", "день 3 из 7: 1 конверсия")
    assert p["totals"]["paid_conversions"] is None and p["source"] is None  # стратегия не платит за конверсии
    camp = p["campaigns"][0]
    assert (camp["week_cost_kopecks"], camp["week_share_pct"], camp["limit_hit"], p["limit_hit"]) == (95000, 95, True, True)

    assert client.get(f"/api/web/workspaces/{s['ws']}/experiments", headers=other).status_code == 404


def test_review_card_and_chat_with_digest(db, monkeypatch):
    s = _seed(db)
    _seed_stats(db, s)
    kazan, samara = _experiments(s)
    client, auth, _other = _clients(db, s)
    url = f"/api/web/workspaces/{s['ws']}/experiments"

    cards = client.get(f"/api/web/workspaces/{s['ws']}/signals", headers=auth).json()["suggestions"]
    assert (cards[0]["kind"], cards[0]["experiment_id"], cards[0]["title"]) == (
        "experiment", kazan, "Эксперимент «Казань»: критерий выполнен"
    )
    assert cards[0]["prompt"] == "Разбери итоги эксперимента «Казань»"
    assert not any(c.get("experiment_id") == samara for c in cards)  # «Самара» ещё собирает данные

    started = client.post(f"{url}/{kazan}/review", headers=auth, json={}).json()
    chat_id = started["chat"]["id"]
    assert chat_id != s["chat"] and started["chat"]["title"] == "Итоги: Казань" and started["job"]["kind"] == "ai_chat"
    assert started["message"]["content"] == "Разбери итоги эксперимента «Казань»: подтвердилась ли гипотеза и что делать дальше"
    busy = client.post(f"{url}/{kazan}/review", headers=auth, json={"chat_id": chat_id})
    assert busy.status_code == 400 and busy.json()["detail"]["code"] == "busy"
    assert client.post(f"{url}/999999/review", headers=auth, json={}).status_code == 404

    fake = FakeModel(_text("Гипотеза подтвердилась: 3 конверсии за 1 ₽."))
    monkeypatch.setattr(ai, "complete", fake)
    assert ai_agent.run_chat(s["user"], chat_id)["status"] == "idle"
    sent = fake.calls[0]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[2]["tool_calls"][0]["function"]["name"] == "get_experiments"
    assert f"- #{kazan} «Казань»: с 20.10 на 14 дней, кампании «Казань копия» (campaign_row_id {s['kazan']})" in sent[0]["content"]
    digest = _flat(json.loads(sent[-1]["content"])["digest"])
    assert f"#{kazan} «Казань» — новое гео, идёт; 14 дн. с 21.10 по 03.11, день 8 из 14; статистика по 27.10." in digest
    assert "Сейчас: критерий выполнен — 3 конверсии при расходе 1 ₽" in digest
    assert "платных 3" in digest and "Образец [" in digest and "«Поиск Москва»" in digest
    assert f"Ещё идут: #{samara} «Самара» — идёт." in digest

    # В открытый чат: тот же чат, пустое название — по эксперименту.
    again = client.post(f"{url}/{kazan}/review", headers=auth, json={"chat_id": s["chat"]}).json()
    assert again["chat"]["id"] == s["chat"] and again["chat"]["title"] == "Итоги: Казань"


def test_finish_and_cancel_from_page(db):
    s = _seed(db)
    _seed_stats(db, s)
    kazan, samara = _experiments(s)
    client, auth, other = _clients(db, s)
    url = f"/api/web/workspaces/{s['ws']}/experiments"
    blog = make_workspace(db, s["user"], 777, "Блог")

    assert client.post(f"{url}/{kazan}/finish", headers=other, json={"verdict": "success"}).status_code == 404
    foreign = client.post(f"/api/web/workspaces/{blog}/experiments/{kazan}/finish", headers=auth, json={"verdict": "success"})
    assert foreign.status_code == 404 and foreign.json()["detail"]["message"] == f"Эксперимента #{kazan} нет в этом пространстве"
    bad = client.post(f"{url}/{kazan}/finish", headers=auth, json={"verdict": "maybe"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "args"

    cancelled = client.post(f"{url}/{samara}/finish", headers=auth, json={"cancel": True}).json()
    assert [(e["id"], e["status"]) for e in cancelled["items"]] == [(kazan, "running"), (samara, "cancelled")]  # идущие — первыми
    assert cancelled["items"][1]["verdict"] == ""

    done = client.post(f"{url}/{kazan}/finish", headers=auth, json={"verdict": "success", "conclusion": "Схема работает"}).json()
    assert [e["id"] for e in done["items"]] == [kazan, samara]  # недавно завершённый выше, хотя запущен раньше
    k = done["items"][0]
    assert (k["status"], k["verdict"], k["verdict_label"], k["conclusion"]) == ("finished", "success", "подтвердилась", "Схема работает")
    assert k["finished_at"] and k["progress"]["campaigns"][0]["week_cost_kopecks"] is None  # лимит завершённого не считаем
    again = client.post(f"{url}/{kazan}/finish", headers=auth, json={"verdict": "fail"})
    assert again.status_code == 400 and again.json()["detail"] == {"code": "finished", "message": "Эксперимент «Казань» уже завершён"}

    assert experiments.running_experiments(s["user"], s["ws"]) == []
    assert experiments.busy_campaigns(s["user"], s["ws"]) == {}
