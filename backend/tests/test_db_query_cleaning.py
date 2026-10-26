"""Чистка поисковых запросов на настоящем Postgres: очередь, память решений, разметка, применение, откат.

Директ и ИИ — подделки: patch_negatives / create_keywords / complete_json подменяются.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import keywords as keywords_module
from app import query_cleaning as qc
from app import undo
from app.journal import list_action_logs
from app.main import app
from app.negative_lists import phrase_key
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace


def _adgroup(db, user, acc, camp, adgroup_id, name, negatives=()):
    with db.connection() as c:
        return c.execute(
            """
            INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name, negative_keywords)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (user, acc, camp, adgroup_id, name, json.dumps(list(negatives), ensure_ascii=False)),
        ).fetchone()["id"]


def _keyword(db, user, acc, group, keyword_id, text):
    with db.connection() as c:
        return c.execute(
            """
            INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword, state)
            VALUES (%s, %s, %s, %s, %s, 'ON') RETURNING id
            """,
            (user, acc, group, keyword_id, text),
        ).fetchone()["id"]


def _query(db, user, acc, camp, group, text, *, clicks=1, cost_rub=0, conversions=0, matched=""):
    with db.connection() as c:
        c.execute(
            """
            INSERT INTO direct_search_queries (
                user_id, account_id, campaign_row_id, adgroup_row_id, query, matched_keyword,
                impressions, clicks, cost_kopecks, conversions, date_from, date_to
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '2026-10-12', '2026-10-25')
            """,
            (user, acc, camp, group, text, matched, clicks * 10, clicks, int(cost_rub * 100), conversions),
        )


@pytest.fixture
def env(db):
    """Пространство «Кухни»: две поисковые кампании, три группы, ключи и запросы."""
    user = make_user(db, "q@example.com")
    acc = make_account(db, user, make_connection(db, user), login="client-1")
    camp = make_campaign(db, user, acc, 101, "Кухни поиск")
    camp2 = make_campaign(db, user, acc, 102, "Шкафы")
    with db.connection() as c:
        c.execute("UPDATE direct_campaigns SET campaign_type = 'TEXT_CAMPAIGN' WHERE user_id = %s", (user,))
    g1 = _adgroup(db, user, acc, camp, 1001, "Кухни прямые")
    g2 = _adgroup(db, user, acc, camp, 1002, "Кухни недорого")
    g3 = _adgroup(db, user, acc, camp2, 2001, "Шкафы", negatives=["вакансии"])
    _keyword(db, user, acc, g1, 5001, "кухни на заказ")
    _keyword(db, user, acc, g2, 5002, "кухни недорого -бесплатно")
    k3 = _keyword(db, user, acc, g2, 5003, "кухни с бесплатной доставкой")
    _keyword(db, user, acc, g2, 5004, "---autotargeting")
    _query(db, user, acc, camp, g1, "кухни на заказ москва", clicks=10, cost_rub=500, conversions=2, matched="кухни на заказ")
    _query(db, user, acc, camp, g1, "Кухни бесплатно", clicks=4, cost_rub=200)
    _query(db, user, acc, camp, g2, "кухни  бесплатно", clicks=2, cost_rub=100)
    _query(db, user, acc, camp, g2, "кухни своими руками", clicks=3, cost_rub=90)
    _query(db, user, acc, camp2, g3, "шкаф купе вакансии", clicks=1, cost_rub=30)
    _query(db, user, acc, camp, None, "кухни икеа", clicks=1, cost_rub=10)
    ws = make_workspace(db, user, 555, "Кухни", campaigns=[camp, camp2])
    return {"db": db, "user": user, "acc": acc, "camp": camp, "camp2": camp2, "g1": g1, "g2": g2, "g3": g3, "k3": k3, "ws": ws}


def _labels(db, ws):
    with db.connection() as c:
        rows = c.execute("SELECT * FROM query_labels WHERE workspace_id = %s ORDER BY query_norm", (ws,)).fetchall()
    return {r["query_norm"]: r for r in rows}


# --- Очередь ------------------------------------------------------------------------------------


def test_queue_merges_queries_across_groups(env):
    q = qc.query_queue(env["user"], env["ws"])
    assert q["counts"] == {"new": 5, "negative": 0, "target": 0, "done": 0, "all": 5}
    assert q["new_cost_rub"] == 930.0
    assert q["date_from"] == "2026-10-12" and q["date_to"] == "2026-10-25"
    assert [c["name"] for c in q["campaigns"]] == ["Кухни поиск", "Шкафы"]
    by = {i["query_norm"]: i for i in q["items"]}
    assert [i["query_norm"] for i in q["items"]][:2] == ["кухни на заказ москва", "кухни бесплатно"]
    free = by["кухни бесплатно"]
    assert free["clicks"] == 6 and free["cost_rub"] == 300.0
    assert sorted(g["id"] for g in free["adgroups"]) == sorted([env["g1"], env["g2"]])
    assert by["кухни на заказ москва"]["matched_keywords"] == ["кухни на заказ"]
    assert by["кухни на заказ москва"]["cpc_rub"] == 50.0
    # Минус-фраза группы уже отсекает запрос — показываем это.
    assert by["шкаф купе вакансии"]["blocked_by"] == "вакансии"
    assert by["кухни икеа"]["adgroups"] == []


def test_queue_filters(env):
    u, ws = env["user"], env["ws"]
    assert [i["query_norm"] for i in qc.query_queue(u, ws, search="БЕСПЛАТ")["items"]] == ["кухни бесплатно"]
    assert qc.query_queue(u, ws, min_cost_rub=200)["counts"]["all"] == 2
    assert qc.query_queue(u, ws, no_conversions=True)["counts"]["all"] == 4
    assert [i["query"] for i in qc.query_queue(u, ws, campaign_row_id=env["camp2"])["items"]] == ["шкаф купе вакансии"]
    assert len(qc.query_queue(u, ws, limit=2)["items"]) == 2
    assert len(qc.query_queue(u, ws, limit=2, offset=4)["items"]) == 1
    with pytest.raises(YandexError):
        qc.query_queue(make_user(env["db"], "other@example.com"), ws)


# --- Метки и память -----------------------------------------------------------------------------


def test_suggestions_do_not_override_decisions(env):
    u, ws, db = env["user"], env["ws"], env["db"]
    n = qc.suggest_labels(
        u, ws, [{"query_norm": "кухни бесплатно", "verdict": "negative", "negative_phrase": "бесплатно", "level": "campaign", "reason": "ИИ"}]
    )
    assert n == 1
    q = qc.query_queue(u, ws, tab="negative")
    assert q["counts"]["negative"] == 1 and q["counts"]["new"] == 4
    item = q["items"][0]
    assert item["label"]["source"] == "ai" and not item["label"]["confirmed"]
    # «бесплатно» на кампанию задевает ключ «кухни с бесплатной доставкой».
    assert [c["keyword_row_id"] for c in item["conflicts"]] == [env["k3"]]

    qc.remember_decisions(
        u, ws, [{"query_norm": "кухни бесплатно", "verdict": "negative", "negative_phrase": "бесплатно", "level": "campaign", "applied": True}]
    )
    label = _labels(db, ws)["кухни бесплатно"]
    assert label["confirmed"] and label["source"] == "ai" and label["reason"] == "ИИ" and label["applied_at"]
    # Подтверждённое ИИ больше не перетирает.
    assert qc.suggest_labels(u, ws, [{"query_norm": "кухни бесплатно", "verdict": "target"}]) == 0
    assert _labels(db, ws)["кухни бесплатно"]["verdict"] == "negative"
    assert qc.query_queue(u, ws, tab="done")["counts"]["done"] == 1


def test_flipped_suggestion_becomes_users_decision(env):
    u, ws, db = env["user"], env["ws"], env["db"]
    qc.suggest_labels(u, ws, [{"query_norm": "кухни своими руками", "verdict": "negative", "negative_phrase": "руками", "reason": "ИИ"}])
    qc.remember_decisions(u, ws, [{"query_norm": "кухни своими руками", "verdict": "target", "negative_phrase": "руками"}])
    label = _labels(db, ws)["кухни своими руками"]
    assert label["verdict"] == "target" and label["source"] == "user"
    assert label["reason"] == "" and label["negative_phrase"] == "" and label["applied_at"] is None


def test_memory_list_and_forget(env):
    u, ws = env["user"], env["ws"]
    qc.remember_decisions(
        u,
        ws,
        [
            {"query_norm": "кухни бесплатно", "verdict": "negative", "negative_phrase": "бесплатно", "level": "campaign"},
            {"query_norm": "кухни на заказ москва", "verdict": "target"},
        ],
    )
    qc.suggest_labels(u, ws, [{"query_norm": "кухни икеа", "verdict": "negative", "negative_phrase": "икеа"}])
    mem = qc.list_decisions(u, ws)
    assert mem["total"] == 2  # подсказки — не память
    assert [i["query_norm"] for i in qc.list_decisions(u, ws, verdict="target")["items"]] == ["кухни на заказ москва"]
    assert [i["query_norm"] for i in qc.list_decisions(u, ws, search="Бесплатно")["items"]] == ["кухни бесплатно"]
    examples = qc.memory_examples(ws)
    assert [e["query"] for e in examples["negative"]] == ["кухни бесплатно"]

    assert qc.forget_decisions(u, ws, ["кухни бесплатно", "нет такого"]) == {"forgotten": 1}
    assert qc.query_queue(u, ws)["counts"]["new"] == 3  # вернулся в разбор
    with pytest.raises(YandexError):
        qc.forget_decisions(u, ws, [])


def test_check_phrases(env):
    got = qc.check_phrases(
        env["user"],
        env["ws"],
        [
            {"query_norm": "кухни бесплатно", "negative_phrase": "бесплатно", "level": "adgroup"},
            {"query_norm": "кухни бесплатно", "negative_phrase": "  ", "level": "campaign"},
            {"query_norm": "кухни своими руками", "negative_phrase": " ".join(["слово"] * 9)},
            {"query_norm": "шкаф купе вакансии", "negative_phrase": "купе", "level": "campaign"},
        ],
    )
    assert got[0]["conflicts_total"] == 1 and got[0]["problems"] == []
    # Пустая фраза — берём сам запрос.
    assert phrase_key(got[1]["negative_phrase"]) == "кухни бесплатно" and got[1]["problems"] == []
    assert got[2]["problems"]
    assert got[3]["conflicts"] == [] and got[3]["problems"] == []


# --- Разметка -----------------------------------------------------------------------------------


def _fake_ai(monkeypatch, seen):
    monkeypatch.setattr(qc.ai, "load_config", lambda user_id, *a, **kw: {"model": "fake"})

    def complete(cfg, messages):
        seen.append(messages)
        items = json.loads(messages[1]["content"].split("\n", 1)[1])
        out = []
        for it in items:
            if "бесплатно" in it["q"].lower():
                out.append({"id": it["id"], "verdict": "negative", "negative": "бесплатно", "level": "campaign", "reason": "халява"})
            else:
                out.append({"id": it["id"], "verdict": "target", "reason": "наш товар"})
        return {"items": out}

    monkeypatch.setattr(qc.ai, "complete_json", complete)


def test_classify_rules_first_then_ai(env, monkeypatch):
    u, ws, db = env["user"], env["ws"], env["db"]
    with db.connection() as c:
        c.execute("UPDATE workspaces SET brief = %s WHERE id = %s", (json.dumps({"not_ours": ["вакансии"], "product": "Кухни на заказ"}), ws))
        c.execute("UPDATE direct_campaigns SET negative_keywords = %s WHERE id = %s", ('["икеа"]', env["camp"]))
        c.execute("UPDATE direct_adgroups SET negative_keywords = '[]' WHERE id = %s", (env["g3"],))
    # Прошлое решение: «своими руками» минусовали в другом запросе.
    qc.remember_decisions(u, ws, [{"query_norm": "шкаф своими руками", "verdict": "negative", "negative_phrase": "своими руками", "level": "adgroup"}])
    seen: list = []
    _fake_ai(monkeypatch, seen)
    progress: list = []
    res = qc.classify_queries(u, {"workspace_id": ws}, lambda *a: progress.append(a))
    assert res == {"labeled": 5, "negative": 4, "target": 1, "rules": 3, "ai": True, "ai_error": None, "left": 0, "usage": None}
    labels = _labels(db, ws)
    assert labels["кухни икеа"]["source"] == "direct" and labels["кухни икеа"]["negative_phrase"] == "икеа"
    assert labels["кухни своими руками"]["source"] == "memory" and labels["кухни своими руками"]["level"] == "adgroup"
    assert labels["шкаф купе вакансии"]["source"] == "brief"
    assert labels["кухни бесплатно"]["source"] == "ai" and labels["кухни бесплатно"]["negative_phrase"] == "бесплатно"
    assert labels["кухни на заказ москва"]["verdict"] == "target"
    # ИИ видел только два запроса, бриф, ключи и прошлые решения.
    assert len(seen) == 1
    system = seen[0][0]["content"]
    assert "Что продаём: Кухни на заказ" in system and "кухни на заказ" in system
    assert "«шкаф своими руками» → минус «своими руками»" in system
    assert "autotargeting" not in system
    assert progress[-1][2] == "Готово"

    # Повтор без «переразметить» — нечего размечать; с ним — только неподтверждённые.
    assert qc.classify_queries(u, {"workspace_id": ws})["labeled"] == 0
    qc.remember_decisions(u, ws, [{"query_norm": "кухни бесплатно", "verdict": "target"}])
    again = qc.classify_queries(u, {"workspace_id": ws, "relabel": True})
    assert again["labeled"] == 4 and _labels(db, ws)["кухни бесплатно"]["verdict"] == "target"


def test_classify_without_ai_keeps_rule_labels(env, monkeypatch):
    u, ws, db = env["user"], env["ws"], env["db"]

    def no_ai(user_id, *a, **kw):
        raise YandexError("ai-not-configured", "Подключите ИИ в настройках")

    monkeypatch.setattr(qc.ai, "load_config", no_ai)
    res = qc.classify_queries(u, {"workspace_id": ws})
    # «вакансии» уже стоит в группе — это правило «уже в Директе», остальное ждёт ИИ.
    assert res["rules"] == 1 and res["ai"] is False and res["ai_error"] == "Подключите ИИ в настройках"
    assert res["left"] == 4
    assert list(_labels(db, ws)) == ["шкаф купе вакансии"]


def test_classify_ai_error_with_nothing_labeled_raises(env, monkeypatch):
    with env["db"].connection() as c:
        c.execute("UPDATE direct_adgroups SET negative_keywords = '[]'")
    monkeypatch.setattr(qc.ai, "load_config", lambda user_id, *a, **kw: {"model": "fake"})

    def broken(cfg, messages):
        raise YandexError("ai", "Модель не ответила")

    monkeypatch.setattr(qc.ai, "complete_json", broken)
    with pytest.raises(YandexError):
        qc.classify_queries(env["user"], {"workspace_id": env["ws"]})


# --- Применение и откат -------------------------------------------------------------------------


class FakeDirect:
    """patch_negatives и create_keywords без Директа: всё добавляется, ключи пишутся в базу."""

    def __init__(self, db, env):
        self.db, self.env = db, env
        self.patches: list = []
        self.keywords: list = []
        self.next_id = 9000

    def patch(self, user_id, *, campaigns=None, adgroups=None):
        self.patches.append({"campaigns": campaigns, "adgroups": adgroups})
        out = {"campaigns": {}, "adgroups": {}, "skipped": [], "errors": []}
        for kind, changes in (("campaigns", campaigns), ("adgroups", adgroups)):
            for rid, ch in (changes or {}).items():
                out[kind][int(rid)] = {"added": list(ch.get("add") or []), "removed": [], "name": ""}
        return out

    def create_keywords(self, user_id, *, adgroup_row_id, phrases, bid_rub=None):
        self.keywords.append((adgroup_row_id, list(phrases), bid_rub))
        items = []
        for p in phrases:
            self.next_id += 1
            items.append({"id": _keyword(self.db, user_id, self.env["acc"], adgroup_row_id, self.next_id, p), "keyword": p})
        return {"items": items, "created": len(items), "errors": 0, "status": "ok"}


@pytest.fixture
def direct(env, monkeypatch):
    fake = FakeDirect(env["db"], env)
    monkeypatch.setattr(qc, "patch_negatives", fake.patch)
    monkeypatch.setattr(keywords_module, "create_keywords", fake.create_keywords)
    return fake


def _apply_batch(env):
    return qc.apply_decisions(
        env["user"],
        env["ws"],
        [
            {"query_norm": "кухни бесплатно", "verdict": "negative", "negative_phrase": "бесплатно", "level": "adgroup"},
            {"query_norm": "кухни своими руками", "verdict": "negative", "negative_phrase": "своими руками", "level": "campaign"},
            {"query_norm": "кухни икеа", "verdict": "negative", "negative_phrase": "икеа", "level": "adgroup"},
            {"query_norm": "кухни на заказ москва", "verdict": "target", "add_keyword": True},
            {"query_norm": "шкаф купе вакансии", "verdict": "target"},
            {"query_norm": "нет такого", "verdict": "negative"},
        ],
        bid_rub=12,
    )


def test_apply_skips_conflicts_and_logs_undo(env, direct):
    res = _apply_batch(env)
    by = {i["query_norm"]: i for i in res["items"]}
    assert by["кухни бесплатно"]["status"] == "skipped" and by["кухни бесплатно"]["conflicts"][0]["keyword_row_id"] == env["k3"]
    assert by["кухни своими руками"]["status"] == "ok"
    # Запрос без группы минусуется на кампанию.
    assert by["кухни икеа"] == {"query_norm": "кухни икеа", "status": "ok", "message": "", "phrase": "икеа", "level": "campaign"}
    assert by["нет такого"]["status"] == "error"
    assert (res["negative"], res["target"], res["keywords"], res["skipped"], res["errors"]) == (2, 2, 1, 1, 1)
    assert res["status"] == "partial"
    assert direct.patches == [{"campaigns": {env["camp"]: {"add": ["своими руками", "икеа"]}}, "adgroups": {}}]
    assert direct.keywords == [(env["g1"], ["кухни на заказ москва"], 12)]

    labels = _labels(env["db"], env["ws"])
    assert sorted(labels) == ["кухни икеа", "кухни на заказ москва", "кухни своими руками", "шкаф купе вакансии"]
    assert all(lb["confirmed"] and lb["source"] == "user" for lb in labels.values())
    assert labels["кухни икеа"]["level"] == "campaign" and labels["кухни икеа"]["applied_at"]
    assert labels["шкаф купе вакансии"]["applied_at"] is None

    with env["db"].connection() as c:
        log = c.execute("SELECT * FROM action_logs WHERE id = %s", (res["log_id"],)).fetchone()
    assert log["action"] == "queries" and log["status"] == "partial" and log["workspace_id"] == env["ws"]
    assert log["title"] == "Чистка запросов: в минус 2, целевых 2, в ключи 1"
    steps = log["undo"]["steps"]
    assert [s["op"] for s in steps] == ["negatives", "keywords_action", "query_labels_restore"]
    assert steps[0]["args"] == {"campaigns": {str(env["camp"]): {"add": [], "remove": ["своими руками", "икеа"]}}}
    assert steps[1]["args"]["action"] == "delete" and len(steps[1]["args"]["keyword_row_ids"]) == 1
    assert all(item["previous"] is None for item in steps[2]["args"]["labels"])
    assert f"campaign:{env['camp']}" in log["targets"]
    # Сами вызовы create_keywords в журнал не пишутся — одна запись на всё применение.
    assert [r["action"] for r in list_action_logs(env["user"])] == ["queries"]


def test_apply_force_and_targets_only(env, direct):
    res = qc.apply_decisions(
        env["user"],
        env["ws"],
        [{"query_norm": "кухни бесплатно", "verdict": "negative", "negative_phrase": "бесплатно", "level": "adgroup"}],
        force=True,
    )
    assert res["status"] == "ok"
    assert direct.patches[-1]["adgroups"] == {env["g1"]: {"add": ["бесплатно"]}, env["g2"]: {"add": ["бесплатно"]}}

    # Только целевые — без Директа; повторное решение запоминает прежнюю метку для отката.
    res = qc.apply_decisions(env["user"], env["ws"], [{"query_norm": "кухни бесплатно", "verdict": "target"}])
    assert res["status"] == "ok" and len(direct.patches) == 1
    with env["db"].connection() as c:
        log = c.execute("SELECT undo FROM action_logs WHERE id = %s", (res["log_id"],)).fetchone()
    (step,) = log["undo"]["steps"]
    assert step["op"] == "query_labels_restore"
    assert step["args"]["labels"][0]["previous"]["verdict"] == "negative"


def test_apply_target_keyword_needs_workspace_group(env, direct):
    other = make_campaign(env["db"], env["user"], env["acc"], 999, "Чужая")
    foreign = _adgroup(env["db"], env["user"], env["acc"], other, 9999, "Чужая группа")
    res = qc.apply_decisions(
        env["user"],
        env["ws"],
        [
            {"query_norm": "кухни икеа", "verdict": "target", "add_keyword": True},
            {"query_norm": "кухни на заказ москва", "verdict": "target", "add_keyword": True, "adgroup_row_id": foreign},
        ],
    )
    assert res["status"] == "error" and res["errors"] == 2
    assert direct.keywords == []


def test_undo_restores_labels_and_runs_inverse(env, direct, monkeypatch):
    u, ws = env["user"], env["ws"]
    res = _apply_batch(env)
    calls: dict = {}
    monkeypatch.setitem(undo.OPS, "negatives", lambda user_id, args: calls.setdefault("negatives", args) and {"added": 0})
    monkeypatch.setitem(undo.OPS, "keywords_action", lambda user_id, args: calls.setdefault("keywords", args) and {"applied": 1})

    preview = undo.undo_preview(u, res["log_id"])
    assert preview["available"] and preview["label"] == "Вернуть как было"
    assert preview["steps"][0] == "Минус-фразы в 1 кампании: убрать «своими руками», «икеа»"
    assert preview["steps"][1] == "Удалить ключевые фразы: «кухни на заказ москва»"
    assert preview["steps"][2] == "Вернуть разметку запросов: 4"
    assert any("частично" in w for w in preview["warnings"])

    progress: list = []
    out = undo.run_undo(u, res["log_id"], lambda *a: progress.append(a))
    assert out["status"] == "ok" and [s["status"] for s in out["steps"]] == ["ok", "ok", "ok"]
    assert calls["negatives"]["campaigns"][str(env["camp"])]["remove"] == ["своими руками", "икеа"]
    assert calls["keywords"]["action"] == "delete"
    assert _labels(env["db"], ws) == {}
    assert qc.query_queue(u, ws)["counts"]["new"] == 5
    assert progress[-1][2] == "Готово"

    logs = {r["id"]: r for r in list_action_logs(u)}
    assert logs[res["log_id"]]["undone_by"] == out["log_id"] and logs[res["log_id"]]["undo"] is None
    assert logs[out["log_id"]]["undo_of"] == res["log_id"] and logs[out["log_id"]]["title"].startswith("Откат: Чистка запросов")
    assert logs[out["log_id"]]["workspace_id"] == ws

    with pytest.raises(YandexError) as err:
        undo.run_undo(u, res["log_id"])
    assert err.value.message == "Это действие уже откатили"
    assert not undo.undo_preview(u, res["log_id"])["available"]


# --- API ----------------------------------------------------------------------------------------


def test_queries_api(env, direct):
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(env['user'])}"}
    other = {"Authorization": f"Bearer {make_session(make_user(env['db'], 'x@example.com'))}"}
    base = f"/api/web/workspaces/{env['ws']}/queries"

    got = client.get(f"{base}?tab=new&min_cost_rub=100", headers=auth).json()
    assert got["counts"]["new"] == 2 and len(got["items"]) == 2
    assert client.get(base, headers=other).status_code == 404
    assert client.get(f"{base}?campaign_row_id=999999", headers=auth).status_code == 404

    check = client.post(
        f"{base}/check", headers=auth, json={"items": [{"query_norm": "кухни бесплатно", "negative_phrase": "бесплатно", "level": "campaign"}]}
    ).json()
    assert check["items"][0]["conflicts_total"] == 1

    # Только целевые — сразу, без задачи.
    done = client.post(f"{base}/apply", headers=auth, json={"items": [{"query_norm": "кухни на заказ москва", "verdict": "target"}]})
    assert done.status_code == 200 and done.json()["result"]["status"] == "ok"
    # Минус-фразы — задачей.
    job = client.post(
        f"{base}/apply", headers=auth, json={"items": [{"query_norm": "кухни икеа", "verdict": "negative", "negative_phrase": "икеа"}]}
    ).json()["job"]
    assert job["kind"] == "apply_query_decisions"
    assert client.post(f"{base}/classify", headers=auth, json={"limit": 10}).json()["job"]["kind"] == "classify_queries"

    mem = client.get(f"{base}/memory?verdict=target", headers=auth).json()
    assert [i["query_norm"] for i in mem["items"]] == ["кухни на заказ москва"]
    assert client.post(f"{base}/forget", headers=auth, json={"query_norms": ["кухни на заказ москва"]}).json() == {"forgotten": 1}
    assert client.post(f"{base}/forget", headers=auth, json={"query_norms": []}).status_code == 400

    log_id = done.json()["result"]["log_id"]
    preview = client.get(f"/api/web/actions/{log_id}/undo", headers=auth).json()
    assert preview["available"] and preview["steps"] == ["Вернуть разметку запросов: 1"]
    assert client.get(f"/api/web/actions/{log_id}/undo", headers=other).status_code == 404
    started = client.post(f"/api/web/actions/{log_id}/undo", headers=auth).json()["job"]
    assert started["kind"] == "undo_action"


def test_workspace_brief_api(env):
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(env['user'])}"}
    base = f"/api/web/workspaces/{env['ws']}"
    res = client.patch(base, headers=auth, json={"brief": {"product": " Кухни ", "not_ours": ["б/у", "Б/У", "вакансии"]}})
    assert res.status_code == 200
    assert res.json()["brief"] == {"product": "Кухни", "geo": "", "not_ours": ["б/у", "вакансии"], "notes": ""}
    assert client.get(base, headers=auth).json()["brief"]["not_ours"] == ["б/у", "вакансии"]
    job = client.post(f"{base}/brief/draft", headers=auth, json={"site": "kitchen.example"}).json()["job"]
    assert job["kind"] == "brief_draft"
