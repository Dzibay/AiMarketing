"""I33: «Догнать N кампаний», чистка «В общий список …» и память «для всех проектов» на настоящем Postgres.

Директ — подделка (campaigns.get / update списков минус-фраз из test_db_negative_lists).
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import negative_lists as nl
from app import query_cleaning as qc
from app import shared_cleaning as sc
from app import undo
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_connection, make_user, make_workspace
from tests.test_db_negative_lists import FakeDirect, _campaign
from tests.test_db_query_cleaning import _adgroup, _keyword, _query


def _set_stale(db, list_id, campaign_row_id):
    with db.connection() as c:
        c.execute(
            """
            UPDATE negative_list_campaigns SET applied_at = applied_at - INTERVAL '1 hour'
            WHERE list_id = %s AND campaign_row_id = %s
            """,
            (list_id, campaign_row_id),
        )
        c.execute("UPDATE negative_lists SET phrases_updated_at = NOW() WHERE id = %s", (list_id,))


@pytest.fixture
def env(db, monkeypatch):
    user = make_user(db, "shared@example.com")
    acc = make_account(db, user, make_connection(db, user), login="client-s")
    a = _campaign(db, user, acc, 401, "Кухни А")
    b = _campaign(db, user, acc, 402, "Кухни Б")
    other = _campaign(db, user, acc, 403, "Без списка")
    ga = _adgroup(db, user, acc, a, 4001, "Кухни")
    gb = _adgroup(db, user, acc, b, 4002, "Доставка")
    go = _adgroup(db, user, acc, other, 4003, "Прочее")
    _keyword(db, user, acc, ga, 8001, "кухни на заказ")
    _keyword(db, user, acc, gb, 8002, "кухни с бесплатной доставкой")
    _query(db, user, acc, a, ga, "кухни вакансии", clicks=3, cost_rub=90)
    _query(db, user, acc, other, go, "кухни вакансии", clicks=1, cost_rub=30)
    _query(db, user, acc, a, ga, "кухни бесплатно", clicks=2, cost_rub=60)
    _query(db, user, acc, a, ga, "кухни работа мечты", clicks=2, cost_rub=60)
    _query(db, user, acc, a, ga, "работа кухни на заказ", clicks=2, cost_rub=60)
    ws = make_workspace(db, user, 901, "Кухни", campaigns=[a, b, other])
    direct = FakeDirect({401: [], 402: [], 403: []})
    monkeypatch.setattr(nl, "get_connection_access_token", lambda *x: "tok")
    monkeypatch.setattr(nl, "_call_campaigns", direct)
    return {"db": db, "user": user, "acc": acc, "a": a, "b": b, "other": other, "ga": ga, "ws": ws, "direct": direct}


# ---------------------------------------------------------------- «Догнать N кампаний»


def test_catch_up_updates_only_stale_and_detaches_nothing(env):
    user, a, b, direct = env["user"], env["a"], env["b"], env["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="бесплатно")
    nl.apply_negative_list(user, stop["id"], [a, b])
    nl.update_negative_list(user, stop["id"], phrases="бесплатно\nскачать")
    _set_stale(env["db"], stop["id"], a)
    with env["db"].connection() as c:  # b считаем уже обновлённой
        c.execute(
            "UPDATE negative_list_campaigns SET applied_at = NOW() + INTERVAL '1 minute' WHERE list_id = %s AND campaign_row_id = %s",
            (stop["id"], b),
        )
    assert nl.stale_campaign_ids(user, stop["id"]) == [a]

    preview = nl.preview_apply(user, stop["id"], [a], catch_up=True)
    assert [r["campaign_row_id"] for r in preview["campaigns"]] == [a]
    assert preview["campaigns"][0]["action"] == "update" and preview["campaigns"][0]["add"] == ["скачать"]
    assert preview["totals"]["detach"] == 0

    direct.updates.clear()
    out = nl.apply_negative_list(user, stop["id"], [a], catch_up=True)
    assert out["status"] == "ok" and out["detached"] == 0
    assert [u["Id"] for u in direct.updates] == [401]
    assert direct.state[401] == ["бесплатно", "скачать"] and direct.state[402] == ["бесплатно"]
    assert nl._linked_ids(stop["id"]) == {a, b}  # b не отвязали
    assert nl.stale_campaign_ids(user, stop["id"]) == []
    with env["db"].connection() as c:
        log = c.execute(
            "SELECT * FROM action_logs WHERE user_id = %s AND action = 'negative_list_apply' ORDER BY id DESC LIMIT 1",
            (user,),
        ).fetchone()
    payload = log["payload"] if isinstance(log["payload"], dict) else json.loads(log["payload"])
    assert payload["catch_up"] is True
    assert log["title"] == "Список минус-фраз «Стоп» — обновлён в кампаниях: 1"


def test_catch_up_skips_unlinked_and_empty(env):
    user, a, other = env["user"], env["a"], env["other"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="бесплатно")
    nl.apply_negative_list(user, stop["id"], [a])
    with pytest.raises(YandexError) as exc:
        nl.preview_apply(user, stop["id"], [other], catch_up=True)
    assert exc.value.code == "empty"


def test_catch_up_api(env):
    user, a = env["user"], env["a"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="бесплатно")
    nl.apply_negative_list(user, stop["id"], [a])
    _set_stale(env["db"], stop["id"], a)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session(user)}"}
    got = client.post(f"/api/web/negative-lists/{stop['id']}/catch-up/preview", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["campaign_row_ids"] == [a]
    started = client.post(
        f"/api/web/negative-lists/{stop['id']}/catch-up", json={"campaign_row_ids": [a]}, headers=headers
    )
    assert started.status_code == 200, started.text
    job = started.json()["job"]
    with env["db"].connection() as c:
        row = c.execute("SELECT kind, payload FROM jobs WHERE id = %s", (job["id"],)).fetchone()
    assert row["kind"] == "apply_negative_list" and row["payload"]["catch_up"] is True


# ---------------------------------------------------------------- «В общий список …»


def test_to_list_preview_and_send_with_undo(env):
    user, ws, a, b = env["user"], env["ws"], env["a"], env["b"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [a, b])
    items = [
        {"query_norm": "кухни вакансии", "negative_phrase": "вакансии"},
        # «бесплатно» задевает ключ «кухни с бесплатной доставкой» кампании Б из списка.
        {"query_norm": "кухни бесплатно", "negative_phrase": "бесплатно"},
    ]
    plan = sc.to_list_preview(user, ws, stop["id"], items)
    by = {p["query_norm"]: p for p in plan["phrases"]}
    assert by["кухни вакансии"]["conflicts_total"] == 0 and not by["кухни вакансии"]["problems"]
    assert by["кухни бесплатно"]["conflicts_total"] == 1
    assert by["кухни бесплатно"]["conflicts"][0]["keyword"] == "кухни с бесплатной доставкой"
    assert plan["campaigns_total"] == 2
    assert plan["outside"] == ["Без списка"]

    res = sc.send_to_list(user, ws, stop["id"], items)
    statuses = {i["query_norm"]: i["status"] for i in res["items"]}
    assert statuses == {"кухни вакансии": "ok", "кухни бесплатно": "skipped"}
    assert res["added_to_list"] == 1 and sorted(res["stale_ids"]) == sorted([a, b])
    assert nl.get_negative_list(user, stop["id"])["phrases"] == ["скачать", "вакансии"]
    with env["db"].connection() as c:
        label = c.execute(
            "SELECT * FROM query_labels WHERE workspace_id = %s AND query_norm = 'кухни вакансии'", (ws,)
        ).fetchone()
        log = c.execute("SELECT * FROM action_logs WHERE id = %s", (res["log_id"],)).fetchone()
    assert label["confirmed"] and label["verdict"] == "negative" and label["level"] == "campaign"
    assert label["reason"] == "В общий список «Стоп»"
    assert [s["op"] for s in log["undo"]["steps"]] == ["negative_list_phrases", "query_labels_restore"]
    assert "вакансии" in undo.describe_step(user, log["undo"]["steps"][0])

    # Задача «догнать» пишет фразу во все кампании списка.
    out = nl.apply_negative_list(user, stop["id"], res["stale_ids"], catch_up=True)
    assert out["status"] == "ok"
    assert "вакансии" in env["direct"].state[401] and "вакансии" in env["direct"].state[402]

    undone = undo.run_undo(user, res["log_id"])
    assert undone
    assert nl.get_negative_list(user, stop["id"])["phrases"] == ["скачать"]
    with env["db"].connection() as c:
        assert (
            c.execute("SELECT 1 FROM query_labels WHERE workspace_id = %s AND query_norm = 'кухни вакансии'", (ws,)).fetchone()
            is None
        )


def test_to_list_force_and_foreign_list(env, db):
    user, ws, a, b = env["user"], env["ws"], env["a"], env["b"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [a, b])
    res = sc.send_to_list(user, ws, stop["id"], [{"query_norm": "кухни бесплатно", "negative_phrase": "бесплатно"}], force=True)
    assert res["items"][0]["status"] == "ok"
    other = make_user(db, "other-shared@example.com")
    foreign = nl.create_negative_list(other, name="Чужой", phrases="x")
    with pytest.raises(YandexError):
        sc.to_list_preview(user, ws, foreign["id"], [{"query_norm": "кухни вакансии"}])


def test_to_list_job_writes_only_confirmed_phrases(env):
    """Неприменённые правки списка (убрали «скачать», добавили «торрент») не уходят в Директ вместе с фразой из чистки."""
    user, ws, a, b, direct = env["user"], env["ws"], env["a"], env["b"], env["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [a, b])
    nl.update_negative_list(user, stop["id"], phrases="торрент")
    _set_stale(env["db"], stop["id"], a)
    _set_stale(env["db"], stop["id"], b)

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session(user)}"}
    got = client.post(
        f"/api/web/negative-lists/{stop['id']}/from-queries",
        json={"workspace_id": ws, "items": [{"query_norm": "кухни вакансии", "negative_phrase": "вакансии"}]},
        headers=headers,
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["result"]["phrases"] == ["вакансии"]
    with env["db"].connection() as c:
        job = c.execute("SELECT payload FROM jobs WHERE id = %s", (body["job"]["id"],)).fetchone()
    payload = job["payload"]
    assert payload["catch_up"] is True and payload["only_add"] == ["вакансии"]

    out = nl.apply_negative_list(user, stop["id"], payload["campaign_row_ids"], catch_up=True, only_add=payload["only_add"])
    assert out["status"] == "ok" and out["removed"] == 0
    assert direct.state[401] == ["скачать", "вакансии"] and direct.state[402] == ["скачать", "вакансии"]
    # Другие правки ещё ждут «Догнать» — кампании остаются отстающими.
    assert sorted(nl.stale_campaign_ids(user, stop["id"])) == sorted([a, b])
    with env["db"].connection() as c:
        owned = c.execute(
            "SELECT added_phrases FROM negative_list_campaigns WHERE list_id = %s AND campaign_row_id = %s", (stop["id"], a)
        ).fetchone()["added_phrases"]
    assert owned == ["скачать", "вакансии"]

    # Обычное «Догнать» с предпросмотром доводит кампании до списка.
    nl.apply_negative_list(user, stop["id"], [a, b], catch_up=True)
    assert direct.state[401] == ["вакансии", "торрент"]
    assert nl.stale_campaign_ids(user, stop["id"]) == []


def test_to_list_only_add_marks_up_to_date_when_nothing_else_pending(env):
    user, ws, a, b, direct = env["user"], env["ws"], env["a"], env["b"], env["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [a, b])
    res = sc.send_to_list(user, ws, stop["id"], [{"query_norm": "кухни вакансии", "negative_phrase": "вакансии"}])
    nl.apply_negative_list(user, stop["id"], res["stale_ids"], catch_up=True, only_add=res["phrases"])
    assert direct.state[402] == ["скачать", "вакансии"]
    assert nl.stale_campaign_ids(user, stop["id"]) == []


def test_to_list_skips_query_whose_campaigns_lack_the_list(env):
    user, ws, b = env["user"], env["ws"], env["b"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [b])  # запрос «кухни работа мечты» — только в кампании А
    plan = sc.to_list_preview(user, ws, stop["id"], [{"query_norm": "кухни работа мечты", "negative_phrase": "мечты"}])
    assert plan["phrases"][0]["reaches"] is False
    res = sc.send_to_list(user, ws, stop["id"], [{"query_norm": "кухни работа мечты", "negative_phrase": "мечты"}], force=True)
    assert res["items"][0]["status"] == "skipped" and res["added_to_list"] == 0 and res["log_id"] is None
    assert nl.get_negative_list(user, stop["id"])["phrases"] == ["скачать"]
    with env["db"].connection() as c:
        assert c.execute("SELECT 1 FROM query_labels WHERE workspace_id = %s", (ws,)).fetchone() is None


# ---------------------------------------------------------------- память «для всех проектов»


def _rows(env, ws, norms):
    return list(qc._rows_for(env["user"], ws, norms).values())


def test_shared_memory_rules_examples_and_target_protection(env, db):
    user, ws = env["user"], env["ws"]
    # Решение в первом проекте → «для всех проектов».
    qc.remember_decisions(
        user, ws, [{"query_norm": "кухни вакансии", "query": "кухни вакансии", "verdict": "negative", "negative_phrase": "вакансии", "level": "adgroup"}]
    )
    added = sc.add_shared_memory(user, workspace_id=ws, query_norms=["кухни вакансии"])
    assert added["added"] == 1 and [e["phrase"] for e in added["items"]] == ["вакансии"]
    assert sc.add_shared_memory(user, workspace_id=ws, query_norms=["кухни вакансии"])["added"] == 0
    sc.add_shared_memory(user, phrase="работа")
    with pytest.raises(YandexError):
        sc.add_shared_memory(user, phrase="раз два три четыре пять шесть семь восемь")

    # Второй проект того же пользователя: правила из общей памяти работают и тут.
    acc2 = make_account(db, user, make_connection(db, user, "second"), login="client-s2")
    c2 = _campaign(db, user, acc2, 501, "Шкафы")
    g2 = _adgroup(db, user, acc2, c2, 5001, "Шкафы")
    _keyword(db, user, acc2, g2, 9001, "шкафы купе")
    _query(db, user, acc2, c2, g2, "шкафы вакансии", clicks=2, cost_rub=40)
    _query(db, user, acc2, c2, g2, "шкафы работа на дому", clicks=2, cost_rub=40)
    ws2 = make_workspace(db, user, 902, "Шкафы", campaigns=[c2])
    rules = qc._memory_rules(ws2)
    assert {r[0] for r in rules} == {"вакансии", "работа"} and all(r[4] == "shared" for r in rules)
    labels, rest = qc._rule_split(user, ws2, {}, _rows(env, ws2, ["шкафы вакансии", "шкафы работа на дому"]))
    by = {lab["query_norm"]: lab for lab in labels}
    assert by["шкафы вакансии"]["source"] == "memory" and by["шкафы вакансии"]["level"] == "campaign"
    assert by["шкафы вакансии"]["reason"] == "Вы минусуете «вакансии» во всех проектах"
    assert "_shared" not in by["шкафы вакансии"] and not rest
    examples = qc.memory_examples(ws2)
    assert {e["negative_phrase"] for e in examples["negative"]} == {"вакансии", "работа"}

    # Проект отметил целевым запрос со словом «работа» — общее правило там не действует.
    qc.remember_decisions(user, ws2, [{"query_norm": "шкафы работа на дому", "query": "шкафы работа на дому", "verdict": "target"}])
    assert {r[0] for r in qc._memory_rules(ws2)} == {"вакансии"}
    assert {e["negative_phrase"] for e in qc.memory_examples(ws2)["negative"]} == {"вакансии"}

    # Проектная память первая: её фраза не дублируется общей.
    project = [r for r in qc._memory_rules(ws) if len(r) == 4]
    assert [r[0] for r in project] == ["вакансии"]
    assert [r[0] for r in qc._memory_rules(ws) if len(r) > 4] == ["работа"]

    # Другой пользователь общей памяти не видит.
    stranger = make_user(db, "stranger@example.com")
    acc3 = make_account(db, stranger, make_connection(db, stranger), login="client-x")
    ws3 = make_workspace(db, stranger, 903, "Чужой", campaigns=[_campaign(db, stranger, acc3, 601, "Чужая")])
    assert qc._memory_rules(ws3) == []

    removed = sc.remove_shared_memory(user, [e["id"] for e in sc.list_shared_memory(user) if e["phrase"] == "вакансии"])
    assert removed["removed"] == 1
    assert [e["phrase"] for e in sc.list_shared_memory(user)] == ["работа"]
    assert sc.remove_shared_memory(stranger, [e["id"] for e in sc.list_shared_memory(user)])["removed"] == 0


def test_shared_memory_skips_quoted_key_decisions(env):
    """«Ключ взят в кавычки» — решение «в минус» без фразы: для всех проектов его не переносим (весь запрос в минус)."""
    user, ws = env["user"], env["ws"]
    qc.remember_decisions(
        user,
        ws,
        [{"query_norm": "кухни бесплатно", "query": "кухни бесплатно", "verdict": "negative", "negative_phrase": "", "level": "campaign", "reason": qc.KEYWORD_QUOTED_REASON}],
    )
    with pytest.raises(YandexError):
        sc.add_shared_memory(user, workspace_id=ws, query_norms=["кухни бесплатно"])
    assert sc.list_shared_memory(user) == []


def test_shared_rule_skipped_when_it_hits_query_campaign_keywords(env):
    user, ws = env["user"], env["ws"]
    # «работа» задевает ключ? Нет; «заказ» задевает «кухни на заказ» — подсказку не ставим, запрос — ИИ.
    sc.add_shared_memory(user, phrase="заказ")
    labels, rest = qc._rule_split(user, ws, {}, _rows(env, ws, ["работа кухни на заказ"]))
    assert labels == [] and [r["norm"] for r in rest] == ["работа кухни на заказ"]


def test_cleaning_memory_api(env):
    user = env["user"]
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session(user)}"}
    got = client.post("/api/web/cleaning-memory", json={"phrase": "-Вакансии"}, headers=headers)
    assert got.status_code == 200, got.text
    item = got.json()["items"][0]
    assert item["phrase"] == "вакансии" or item["phrase"] == "Вакансии"
    assert client.get("/api/web/cleaning-memory", headers=headers).json()["items"][0]["id"] == item["id"]
    gone = client.post("/api/web/cleaning-memory/remove", json={"ids": [item["id"]]}, headers=headers)
    assert gone.status_code == 200 and gone.json()["items"] == []
    bad = client.post("/api/web/cleaning-memory", json={"workspace_id": env["ws"], "query_norms": ["нет"]}, headers=headers)
    assert bad.status_code == 400
