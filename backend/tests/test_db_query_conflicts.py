"""I42: минус-фраза задевает ключ — готовые выходы (группы, уточнённая фраза, ключ в кавычки) на настоящем Postgres.

Директ — подделка: patch_negatives и update_keyword подменяются.
"""

import pytest
from fastapi.testclient import TestClient

from app import keywords as keywords_module
from app import query_cleaning as qc
from app import undo
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace
from tests.test_db_query_cleaning import FakeDirect, _adgroup, _keyword, _query

QUERY = "кухни бесплатно своими руками"


@pytest.fixture
def env(db):
    """«бесплатно» на кампанию задевает ключ «кухни с бесплатной доставкой» в группе «Доставка»."""
    user = make_user(db, "conf@example.com")
    acc = make_account(db, user, make_connection(db, user), login="client-c")
    camp = make_campaign(db, user, acc, 301, "Кухни поиск")
    with db.connection() as c:
        c.execute("UPDATE direct_campaigns SET campaign_type = 'TEXT_CAMPAIGN' WHERE user_id = %s", (user,))
    g1 = _adgroup(db, user, acc, camp, 3001, "Кухни общие")
    g2 = _adgroup(db, user, acc, camp, 3002, "Доставка")
    k1 = _keyword(db, user, acc, g1, 7001, "кухни -икеа")
    k3 = _keyword(db, user, acc, g2, 7003, "кухни с бесплатной доставкой")
    _query(db, user, acc, camp, g1, QUERY, clicks=4, cost_rub=200, matched="кухни -икеа")
    _query(db, user, acc, camp, g2, QUERY, clicks=1, cost_rub=50)
    _query(db, user, acc, camp, g1, "кухни москва", clicks=3, cost_rub=90, conversions=1, matched="кухни -икеа")
    _query(db, user, acc, camp, g1, "кухни", clicks=3, cost_rub=90, matched="кухни -икеа")
    ws = make_workspace(db, user, 777, "Кухни", campaigns=[camp])
    qc.suggest_labels(
        user, ws, [{"query_norm": QUERY, "verdict": "negative", "negative_phrase": "бесплатно", "level": "campaign", "reason": "ИИ"}]
    )
    return {"db": db, "user": user, "acc": acc, "camp": camp, "g1": g1, "g2": g2, "k1": k1, "k3": k3, "ws": ws}


@pytest.fixture
def direct(env, monkeypatch):
    fake = FakeDirect(env["db"], env)
    monkeypatch.setattr(qc, "patch_negatives", fake.patch)
    fake.updates = []
    fake.fail_update = False

    def update_keyword(user_id, keyword_row_id, *, keyword=None, bid_rub=None):
        if fake.fail_update:
            raise YandexError("direct", "Директ не принял фразу")
        fake.updates.append((keyword_row_id, keyword))
        with env["db"].connection() as c:
            c.execute("UPDATE direct_keywords SET keyword = %s WHERE id = %s", (keyword, keyword_row_id))
        return {"id": keyword_row_id, "keyword": keyword}

    monkeypatch.setattr(keywords_module, "update_keyword", update_keyword)
    return fake


def _label(env):
    with env["db"].connection() as c:
        return c.execute(
            "SELECT * FROM query_labels WHERE workspace_id = %s AND query_norm = %s", (env["ws"], QUERY)
        ).fetchone()


def _log(env, log_id):
    with env["db"].connection() as c:
        return c.execute("SELECT * FROM action_logs WHERE id = %s", (log_id,)).fetchone()


def test_options_preview_every_exit(env):
    got = qc.conflict_options(env["user"], env["ws"], QUERY)
    assert (got["phrase"], got["level"], got["conflicts_total"]) == ("бесплатно", "campaign", 1)
    assert got["conflicts"][0]["keyword_row_id"] == env["k3"]
    by = {o["key"]: o for o in got["options"]}
    assert list(by) == ["groups", "narrow:бесплатно своими", "narrow:бесплатно руками", "keyword"]

    groups = by["groups"]
    assert groups["adgroup_row_ids"] == [env["g1"]] and groups["level"] == "adgroup"
    assert groups["left_out"] == [{"id": env["g2"], "name": "Доставка", "keywords": ["кухни с бесплатной доставкой"]}]
    # «бесплатно кухни» всё ещё задевает ключ — такой вариант не предлагаем.
    assert by["narrow:бесплатно своими"]["level"] == "campaign"

    kw = by["keyword"]
    assert kw["keywords"] == [
        {
            "keyword_row_id": env["k1"],
            "keyword": "кухни -икеа",
            "new_keyword": '"кухни" -икеа',
            "adgroup_row_id": env["g1"],
            "adgroup_name": "Кухни общие",
        }
    ]
    # Ключ в кавычках потеряет и другие запросы с лишними словами — в том числе с заявками.
    assert kw["lost"] == {"total": 1, "with_conversions": 1, "examples": ["кухни москва"]}


def test_options_empty_without_conflict(env):
    got = qc.conflict_options(env["user"], env["ws"], QUERY, negative_phrase="своими руками")
    assert got["conflicts_total"] == 0 and got["options"] == []
    with pytest.raises(YandexError):
        qc.conflict_options(env["user"], env["ws"], "нет такого")
    with pytest.raises(YandexError):
        qc.conflict_options(make_user(env["db"], "x2@example.com"), env["ws"], QUERY)


def test_resolve_into_safe_groups(env, direct):
    res = qc.resolve_conflict(env["user"], env["ws"], QUERY, "groups")
    assert res["status"] == "ok" and res["level"] == "adgroup" and res["phrase"] == "бесплатно"
    assert direct.patches == [{"campaigns": {}, "adgroups": {env["g1"]: {"add": ["бесплатно"]}}}]
    label = _label(env)
    assert label["confirmed"] and label["applied_at"] and label["level"] == "adgroup"
    log = _log(env, res["log_id"])
    assert log["action"] == "queries" and log["workspace_id"] == env["ws"]
    assert log["title"] == "Минус-фраза «бесплатно» в группы без задетых ключей"
    assert [s["op"] for s in log["undo"]["steps"]] == ["negatives", "query_labels_restore"]
    assert log["undo"]["steps"][0]["args"] == {"adgroups": {str(env["g1"]): {"add": [], "remove": ["бесплатно"]}}}
    # Прежняя метка — подсказка ИИ, откат вернёт её.
    assert log["undo"]["steps"][1]["args"]["labels"][0]["previous"]["source"] == "ai"


def test_resolve_with_narrowed_phrase(env, direct):
    res = qc.resolve_conflict(env["user"], env["ws"], QUERY, "narrow:бесплатно руками")
    assert res["status"] == "ok" and res["phrase"] == "бесплатно руками" and res["level"] == "campaign"
    assert direct.patches == [{"campaigns": {env["camp"]: {"add": ["бесплатно руками"]}}, "adgroups": {}}]
    assert _label(env)["negative_phrase"] == "бесплатно руками"


def test_resolve_stale_option(env, direct):
    with pytest.raises(YandexError) as err:
        qc.resolve_conflict(env["user"], env["ws"], QUERY, "narrow:бесплатно кухни")
    assert err.value.message == qc._STALE
    # Фраза без пересечения — выходов нет.
    with pytest.raises(YandexError):
        qc.resolve_conflict(env["user"], env["ws"], QUERY, "groups", negative_phrase="своими руками")
    assert direct.patches == []


def test_resolve_by_quoting_keyword_and_undo(env, direct, monkeypatch):
    res = qc.resolve_conflict(env["user"], env["ws"], QUERY, "keyword")
    assert res["status"] == "ok" and res["kind"] == "keyword"
    assert direct.updates == [(env["k1"], '"кухни" -икеа')] and direct.patches == []
    label = _label(env)
    assert label["confirmed"] and label["verdict"] == "negative"
    # Минус-фразы в Директе нет — фразу не запоминаем и применённой не считаем.
    assert label["negative_phrase"] == "" and label["applied_at"] is None
    assert label["reason"] == qc.KEYWORD_QUOTED_REASON
    log = _log(env, res["log_id"])
    assert log["title"] == "Ключ «кухни -икеа» в кавычки вместо минус-фразы «бесплатно»"
    steps = log["undo"]["steps"]
    assert steps[0] == {"op": "keyword_update", "args": {"keyword_row_id": env["k1"], "keyword": "кухни -икеа"}}
    assert steps[1]["op"] == "query_labels_restore"
    assert f"keyword:{env['k1']}" in log["targets"]

    preview = undo.undo_preview(env["user"], res["log_id"])
    assert preview["available"] and preview["steps"][0] == "Вернуть фразе текст «кухни -икеа»"
    calls = []
    monkeypatch.setitem(undo.OPS, "keyword_update", lambda user_id, args: calls.append(args) or {})
    out = undo.run_undo(env["user"], res["log_id"])
    assert out["status"] == "ok" and calls == [{"keyword_row_id": env["k1"], "keyword": "кухни -икеа"}]
    label = _label(env)
    assert label["source"] == "ai" and not label["confirmed"]


def test_resolve_keyword_failure_keeps_label(env, direct):
    direct.fail_update = True
    res = qc.resolve_conflict(env["user"], env["ws"], QUERY, "keyword")
    assert res["status"] == "error" and res["message"] == "Директ не принял фразу"
    assert not _label(env)["confirmed"]
    assert _log(env, res["log_id"])["status"] == "error"


def test_resolve_api(env, direct):
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(env['user'])}"}
    other = {"Authorization": f"Bearer {make_session(make_user(env['db'], 'y@example.com'))}"}
    base = f"/api/web/workspaces/{env['ws']}/queries/resolve"

    got = client.post(f"{base}/options", headers=auth, json={"query_norm": QUERY})
    assert got.status_code == 200
    assert [o["kind"] for o in got.json()["options"]] == ["groups", "narrow", "narrow", "keyword"]
    assert client.post(f"{base}/options", headers=other, json={"query_norm": QUERY}).status_code == 404

    done = client.post(base, headers=auth, json={"query_norm": QUERY, "option": "groups"})
    assert done.status_code == 200 and done.json()["status"] == "ok"
    assert done.headers.get("X-Undo-Log") == str(done.json()["log_id"])
    assert client.post(base, headers=auth, json={"query_norm": QUERY, "option": "nope"}).status_code == 400


def test_quoted_keyword_does_not_teach_phrase(env, direct):
    """Ключ в кавычках: «бесплатно» не становится памятью, примером для ИИ и не считается убранной фразой."""
    qc.resolve_conflict(env["user"], env["ws"], QUERY, "keyword")
    assert all(rule[0] != "бесплатно" for rule in qc._memory_rules(env["ws"]))
    assert qc.memory_examples(env["ws"])["negative"] == []
    assert qc.query_highlights(env["user"], env["ws"])["removed_30d"] == {"phrases": 0, "queries": 0}
    row = next(i for i in qc.query_queue(env["user"], env["ws"], tab="done")["items"] if i["query_norm"] == QUERY)
    assert row["label"]["applied_at"] is None and row["conflicts_total"] == 0


def test_groups_resolution_leaves_no_warning(env, direct):
    """После «только в группы без ключа» строка без «Заденет ключи», и тот же выход второй раз не предлагаем."""
    qc.resolve_conflict(env["user"], env["ws"], QUERY, "groups")
    # Как настоящий patch_negatives: локальная копия группы получает фразу.
    with env["db"].connection() as c:
        c.execute("UPDATE direct_adgroups SET negative_keywords = %s WHERE id = %s", ('["бесплатно"]', env["g1"]))
    row = next(i for i in qc.query_queue(env["user"], env["ws"], tab="done")["items"] if i["query_norm"] == QUERY)
    assert row["label"]["applied_at"] and row["label"]["level"] == "adgroup"
    assert row["conflicts_total"] == 0
    kinds = [o["kind"] for o in qc.conflict_options(env["user"], env["ws"], QUERY)["options"]]
    assert "groups" not in kinds
    with pytest.raises(YandexError):
        qc.resolve_conflict(env["user"], env["ws"], QUERY, "groups")

    # «Всё равно добавить» в группу с ключом — пересечение настоящее, предупреждение остаётся.
    with env["db"].connection() as c:
        c.execute("UPDATE direct_adgroups SET negative_keywords = %s WHERE id = %s", ('["бесплатно"]', env["g2"]))
    row = next(i for i in qc.query_queue(env["user"], env["ws"], tab="done")["items"] if i["query_norm"] == QUERY)
    assert row["conflicts_total"] == 1 and row["conflicts"][0]["keyword_row_id"] == env["k3"]
