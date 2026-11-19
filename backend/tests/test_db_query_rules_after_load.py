"""Бесплатные правила после загрузки запросов и «Разметить все проекты» на настоящем Postgres.

ИИ — подделка (complete_json), Директ не вызывается вовсе.
"""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import query_cleaning as qc
from app.jobs import PRIORITY_USER
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace
from tests.test_db_query_cleaning import _adgroup, _fake_ai, _labels, _query


@pytest.fixture
def env(db):
    """Три проекта с запросами (Кухни дороже всех, потом Двери, потом Шкафы) и один пустой."""
    user = make_user(db, "rules@example.com")
    acc = make_account(db, user, make_connection(db, user), login="client-1")
    kitchens = make_campaign(db, user, acc, 101, "Кухни поиск")
    wardrobes = make_campaign(db, user, acc, 102, "Шкафы")
    doors = make_campaign(db, user, acc, 103, "Двери")
    with db.connection() as c:
        c.execute("UPDATE direct_campaigns SET campaign_type = 'TEXT_CAMPAIGN' WHERE user_id = %s", (user,))
        c.execute("UPDATE direct_campaigns SET negative_keywords = %s WHERE id = %s", ('["икеа"]', kitchens))
    g1 = _adgroup(db, user, acc, kitchens, 1001, "Кухни")
    g3 = _adgroup(db, user, acc, wardrobes, 2001, "Шкафы", negatives=["вакансии"])
    g4 = _adgroup(db, user, acc, doors, 3001, "Двери")
    _query(db, user, acc, kitchens, g1, "кухни на заказ москва", clicks=10, cost_rub=500, conversions=2)
    _query(db, user, acc, kitchens, g1, "кухни бесплатно", clicks=4, cost_rub=200)
    _query(db, user, acc, kitchens, g1, "кухни икеа", clicks=2, cost_rub=120)
    _query(db, user, acc, kitchens, g1, "кухни угловые", clicks=3, cost_rub=90)
    _query(db, user, acc, kitchens, g1, "кухни бесплатно доставка", clicks=1, cost_rub=60)
    _query(db, user, acc, kitchens, g1, "кухни вакансии", clicks=1, cost_rub=40)
    _query(db, user, acc, kitchens, g1, "вакансии кухни повар", clicks=1, cost_rub=35)
    _query(db, user, acc, doors, g4, "двери межкомнатные", clicks=2, cost_rub=250)
    _query(db, user, acc, wardrobes, g3, "шкаф купе вакансии", clicks=1, cost_rub=30)
    _query(db, user, acc, wardrobes, g3, "шкаф купе", clicks=1, cost_rub=20)
    ws_k = make_workspace(db, user, 555, "Кухни", campaigns=[kitchens])
    ws_d = make_workspace(db, user, 556, "Двери", campaigns=[doors])
    ws_s = make_workspace(db, user, 557, "Шкафы", campaigns=[wardrobes])
    ws_e = make_workspace(db, user, 558, "Пустой")
    with db.connection() as c:
        c.execute("UPDATE workspaces SET brief = %s WHERE id = %s", (json.dumps({"not_ours": ["вакансии"]}), ws_k))
    # Память решений: «бесплатно» уже минусовали в другом запросе.
    qc.remember_decisions(
        user, ws_k, [{"query_norm": "двери бесплатно", "verdict": "negative", "negative_phrase": "бесплатно", "level": "campaign"}]
    )
    return {
        "db": db, "user": user, "acc": acc, "kitchens": kitchens, "wardrobes": wardrobes, "doors": doors,
        "ws_k": ws_k, "ws_d": ws_d, "ws_s": ws_s, "ws_e": ws_e,
    }


@pytest.fixture
def no_ai(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("ИИ вызывать нельзя")

    for name in ("load_config", "complete_json", "check_budget"):
        monkeypatch.setattr(qc.ai, name, boom)


def test_rules_after_load_fill_only_empty_places(env, no_ai):
    u, db, ws = env["user"], env["db"], env["ws_k"]
    # Подсказка ИИ и решение маркетолога, с которыми правила не согласны, — остаются как есть.
    qc.suggest_labels(u, ws, [{"query_norm": "кухни бесплатно", "verdict": "target", "reason": "ИИ: наш товар"}])
    qc.remember_decisions(u, ws, [{"query_norm": "кухни вакансии", "verdict": "target"}])

    assert qc.label_after_load(u, campaign_row_ids=[env["kitchens"]]) == 3
    labels = _labels(db, ws)
    got = {n: (r["verdict"], r["source"], r["confirmed"], r["negative_phrase"], r["level"]) for n, r in labels.items()}
    assert got["кухни икеа"] == ("negative", "direct", False, "икеа", "campaign")
    assert got["кухни бесплатно доставка"] == ("negative", "memory", False, "бесплатно", "campaign")
    assert got["вакансии кухни повар"] == ("negative", "brief", False, "вакансии", "campaign")
    assert got["кухни бесплатно"] == ("target", "ai", False, "", "adgroup")
    assert got["кухни вакансии"][:3] == ("target", "user", True)
    assert labels["кухни икеа"]["applied_at"] is None and labels["кухни икеа"]["query"] == "кухни икеа"
    # Без правила — ждут ИИ во вкладке «Новые».
    assert "кухни на заказ москва" not in labels and "кухни угловые" not in labels
    assert qc.query_queue(u, ws)["counts"]["new"] == 2
    # Повторная загрузка ничего не меняет; соседние проекты не тронуты.
    assert qc.label_after_load(u, campaign_row_ids=[env["kitchens"]]) == 0
    assert _labels(db, env["ws_s"]) == {} and _labels(db, env["ws_d"]) == {}


def test_rules_after_account_load_and_project_load(env, no_ai):
    u, db = env["user"], env["db"]
    # Закрытый (архивный) проект не трогаем.
    with db.connection() as c:
        c.execute("UPDATE workspaces SET archived_at = NOW() WHERE id = %s", (env["ws_s"],))
    assert qc.label_after_load(u, account_id=env["acc"]) == 5
    assert _labels(db, env["ws_s"]) == {}
    assert len(_labels(db, env["ws_k"])) == 1 + 5

    with db.connection() as c:
        c.execute("UPDATE workspaces SET archived_at = NULL WHERE id = %s", (env["ws_s"],))
    assert qc.label_after_load(u, workspace_ids=[env["ws_s"], env["ws_e"]]) == 1
    s = _labels(db, env["ws_s"])
    assert set(s) == {"шкаф купе вакансии"}
    assert (s["шкаф купе вакансии"]["source"], s["шкаф купе вакансии"]["level"]) == ("direct", "adgroup")
    # Чужой кабинет и чужой проект — ничего.
    other = make_user(db, "x@example.com")
    assert qc.label_after_load(other, account_id=env["acc"], workspace_ids=[env["ws_d"]]) == 0


def _estimate_ai(monkeypatch, room):
    _fake_ai(monkeypatch, [])
    monkeypatch.setattr(qc.ai, "units_for", lambda *a, **kw: 1.0)
    monkeypatch.setattr(qc.ai, "budget_left", lambda cfg: room)
    monkeypatch.setattr(qc.ai, "check_budget", lambda cfg, need=0.0: None)
    monkeypatch.setattr(qc.billing, "share", lambda user_id, units: round(units, 2))
    monkeypatch.setattr(qc.ai, "complete_json", lambda *a, **kw: pytest.fail("оценка без вызова ИИ"))


def test_estimate_all_projects(env, monkeypatch):
    _estimate_ai(monkeypatch, 1.0)
    est = qc.estimate_classify_all(env["user"])
    got = [(p["name"], p["queries"], p["rules"], p["ai"], p["percent"], p["fits"]) for p in est["projects"]]
    # По убыванию расхода; пустой проект — пропущен; остатка хватает на одну пачку.
    assert got == [("Кухни", 7, 5, 2, 1.0, True), ("Двери", 1, 0, 1, 1.0, False), ("Шкафы", 2, 1, 1, 1.0, False)]
    assert (est["queries"], est["rules"], est["ai"]) == (10, 6, 4)
    assert est["percent"] == 3.0 and est["left_percent"] == 1.0
    assert est["fits_projects"] == 1 and est["fits_queries"] == 7 + 1
    assert est["ai_error"] is None
    # Оценка ничего не сохраняет.
    assert set(_labels(env["db"], env["ws_k"])) == {"двери бесплатно"}

    monkeypatch.setattr(qc.ai, "budget_left", lambda cfg: None)
    est = qc.estimate_classify_all(env["user"])
    assert est["fits_projects"] == 3 and est["fits_queries"] == 10 and est["left_percent"] is None


def test_classify_all_projects_stops_ai_at_limit(env, monkeypatch):
    seen: list = []
    _fake_ai(monkeypatch, seen)
    budget = {"calls": 0}

    def check(cfg, need=0.0):
        budget["calls"] += 1
        if budget["calls"] > 1:
            raise YandexError("units-exhausted", "Лимит ИИ на этот месяц исчерпан")

    monkeypatch.setattr(qc.ai, "check_budget", check)
    progress: list = []
    res = qc.classify_all_queries(env["user"], {}, lambda *a: progress.append(a))
    assert len(seen) == 1
    assert (res["projects"], res["skipped"], res["labeled"], res["negative"], res["target"], res["rules"], res["left"]) == (
        2, 1, 8, 6, 2, 6, 2,
    )
    assert res["ai_error"] == "Лимит ИИ на этот месяц исчерпан"
    assert [(i["name"], i["labeled"], i["left"], bool(i.get("error"))) for i in res["items"]] == [
        ("Кухни", 7, 0, False), ("Двери", 0, 1, True), ("Шкафы", 1, 1, False),
    ]
    k = _labels(env["db"], env["ws_k"])
    assert (k["кухни на заказ москва"]["verdict"], k["кухни на заказ москва"]["source"]) == ("target", "ai")
    assert _labels(env["db"], env["ws_d"]) == {}
    assert set(_labels(env["db"], env["ws_s"])) == {"шкаф купе вакансии"}
    assert progress[0][:2] == (0, 3) and progress[0][2].startswith("Кухни — ")
    assert progress[-1] == (3, 3, "Готово")

    # Лимит пополнили: остаток — ИИ, по пачке на проект; Кухни уже размечены — их пропускаем.
    monkeypatch.setattr(qc.ai, "check_budget", lambda cfg, need=0.0: None)
    res = qc.classify_all_queries(env["user"], {})
    assert len(seen) == 3 and [i["name"] for i in res["items"]] == ["Двери", "Шкафы"]
    assert (res["projects"], res["labeled"], res["left"], res["ai_error"]) == (2, 2, 0, None)
    # Больше размечать нечего: ни одного вызова.
    res = qc.classify_all_queries(env["user"], {})
    assert len(seen) == 3 and res["projects"] == 0 and res["items"] == []


def test_api_estimate_and_job(env, monkeypatch):
    _estimate_ai(monkeypatch, None)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(env['user'])}"}
    est = client.post("/api/web/queries/classify/estimate", headers=auth)
    assert est.status_code == 200
    body = est.json()
    assert [p["name"] for p in body["projects"]] == ["Кухни", "Двери", "Шкафы"] and body["fits_projects"] == 3
    # Пользователю — проценты, без токенов и денег.
    assert not {"tokens", "cost", "rub", "model"} & set(body)

    job = client.post("/api/web/queries/classify", headers=auth).json()["job"]
    assert job["kind"] == "classify_all_queries"
    with env["db"].connection() as c:
        row = c.execute("SELECT user_id, priority, payload FROM jobs WHERE id = %s", (job["id"],)).fetchone()
    assert (row["user_id"], row["priority"]) == (env["user"], PRIORITY_USER)
    # Повторное нажатие, пока задача ждёт, — та же задача.
    assert client.post("/api/web/queries/classify", headers=auth).json()["job"]["id"] == job["id"]

    other = {"Authorization": f"Bearer {make_session(make_user(env['db'], 'x@example.com'))}"}
    assert client.post("/api/web/queries/classify/estimate", headers=other).json()["projects"] == []


# --- Разметка проекта — одна за раз (кнопка в проекте и «Разметить все проекты») ----------------


def _asked(seen) -> list[str]:
    """Какие запросы ушли в ИИ — по всем вызовам подделки."""
    return [it["q"] for messages in seen for it in json.loads(messages[1]["content"].split("\n", 1)[1])]


def test_classify_skips_rows_labelled_meanwhile(env, monkeypatch):
    u, db, ws = env["user"], env["db"], env["ws_k"]
    seen: list = []
    _fake_ai(monkeypatch, seen)
    fake = qc.ai.complete_json

    def complete(cfg, messages):
        out = fake(cfg, messages)
        if len(seen) == 1:
            # Пока ИИ разбирал первую пачку, маркетолог сам решил по следующему запросу.
            qc.remember_decisions(u, ws, [{"query_norm": "кухни угловые", "verdict": "target"}])
        return out

    monkeypatch.setattr(qc.ai, "complete_json", complete)
    monkeypatch.setattr(qc, "AI_BATCH", 1)
    res = qc.classify_queries(u, {"workspace_id": ws, "limit": 50})
    # Вторая пачка уже решена — в ИИ её не шлём, лимит не тратим.
    assert _asked(seen) == ["кухни на заказ москва"]
    assert (res["labeled"], res["rules"], res["left"]) == (6, 5, 0)
    labels = _labels(db, ws)
    assert (labels["кухни угловые"]["source"], labels["кухни угловые"]["confirmed"]) == ("user", True)
    assert labels["кухни на заказ москва"]["source"] == "ai"

    # Переразметка подсказок: неподтверждённые снова идут в ИИ, решённые — нет.
    rows = [{"norm": "кухни на заказ москва"}, {"norm": "кухни угловые"}, {"norm": "кухни бесплатно"}]
    assert [r["norm"] for r in qc._unlabelled(ws, rows, relabel=True)] == ["кухни на заказ москва", "кухни бесплатно"]
    assert qc._unlabelled(ws, rows, relabel=False) == []


def test_one_classify_per_project_at_a_time(env, monkeypatch):
    u, db, ws = env["user"], env["db"], env["ws_k"]
    seen: list = []
    _fake_ai(monkeypatch, seen)
    monkeypatch.setattr(qc, "CLASSIFY_POLL_SECONDS", 0.05)
    monkeypatch.setattr(qc, "CLASSIFY_WAIT_SECONDS", 0.3)
    waiting = threading.Event()

    def progress(done, total, text):
        if text.endswith("Ждём: запросы проекта уже размечаются"):
            waiting.set()

    lock = "SELECT pg_advisory_lock(hashtext('query_classify'), %s::int)"
    unlock = "SELECT pg_advisory_unlock(hashtext('query_classify'), %s::int)"
    out: dict = {}

    def second():
        try:
            out["res"] = qc.classify_queries(u, {"workspace_id": ws, "limit": 50}, progress)
        except Exception as exc:  # noqa: BLE001 — покажем в проверке ниже
            out["error"] = exc

    worker = threading.Thread(target=second)
    with db.connection() as holder:
        # «Первая разметка» проекта Кухни идёт — замок у неё.
        holder.execute(lock, (ws,))
        holder.commit()
        try:
            with pytest.raises(YandexError) as exc:
                qc.classify_queries(u, {"workspace_id": ws, "limit": 50}, progress)
            assert exc.value.code == "busy" and waiting.is_set()
            assert seen == [] and set(_labels(db, ws)) == {"двери бесплатно"}

            # «Разметить все проекты» в это время: Кухни пропускаем, а ИИ для остальных проектов не выключаем.
            res = qc.classify_all_queries(u, {})
            assert res["ai_error"] is None and res["skipped"] == 1
            assert [(i["name"], bool(i.get("error"))) for i in res["items"]] == [
                ("Кухни", True), ("Двери", False), ("Шкафы", False),
            ]
            assert sorted(_asked(seen)) == ["двери межкомнатные", "шкаф купе"]

            # Вторая разметка Кухонь ждёт первую, пока та не закончит.
            monkeypatch.setattr(qc, "CLASSIFY_WAIT_SECONDS", 30)
            waiting.clear()
            worker.start()
            assert waiting.wait(10)
            qc.suggest_labels(u, ws, [{"query_norm": "кухни на заказ москва", "verdict": "target", "reason": "первая"}])
        finally:
            holder.execute(unlock, (ws,))
            holder.commit()
    worker.join(30)
    assert not worker.is_alive() and "error" not in out
    # Очередь — уже после первой: размеченный ею запрос в ИИ второй раз не уходит.
    assert _asked(seen)[2:] == ["кухни угловые"]
    assert out["res"]["left"] == 0 and _labels(db, ws)["кухни на заказ москва"]["reason"] == "первая"
