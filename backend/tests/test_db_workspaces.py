"""Рабочие пространства: раскладка кампаний, настройки, аналитика и API на настоящем Postgres."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import stats, workspaces
from app.analytics import parse_range, workspace_analytics
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_counter, make_user, make_workspace

D1 = date(2026, 10, 19)  # период 19–25 октября, прошлый — 12–18
D7 = date(2026, 10, 25)


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _direct(user, account, campaign, day, cost_rub, clicks=10, conv=0, revenue_rub=0):
    stats.upsert_direct_stat(
        user_id=user,
        day=day,
        account_id=account,
        campaign_id=campaign,
        impressions=clicks * 10,
        clicks=clicks,
        cost_kopecks=cost_rub * 100,
        conversions=conv,
        revenue_kopecks=revenue_rub * 100,
    )


def _metrika(pool, user, counter_row, day, visits, goal_id=None, campaign_id=None, reaches=0):
    if goal_id is None:
        _sql(
            pool,
            """
            INSERT INTO stats_daily (user_id, day, source, counter_row_id, visits, users_count, bounce_rate)
            VALUES (%s, %s, 'metrika', %s, %s, %s, 20)
            """,
            (user, day, counter_row, visits, visits // 2),
        )
    else:
        _sql(
            pool,
            """
            INSERT INTO stats_daily (user_id, day, source, counter_row_id, goal_id, campaign_id, goal_reaches)
            VALUES (%s, %s, 'metrika_goal', %s, %s, %s, %s)
            """,
            (user, day, counter_row, goal_id, campaign_id, reaches),
        )


def _seed(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc_a = make_account(db, user, conn_id, name="Кабинет А", login="acc-a")
    acc_b = make_account(db, user, conn_id, name="Кабинет Б", login="acc-b")
    counter = make_counter(db, user, conn_id, 555, site="shop.ru")
    other_counter = make_counter(db, user, conn_id, 777, site="blog.ru")
    search = make_campaign(db, user, acc_a, 11, "Поиск")
    network = make_campaign(db, user, acc_b, 21, "РСЯ")
    foreign = make_campaign(db, user, acc_b, 22, "Блог")
    ws = make_workspace(
        db, user, 555, "Магазин", counter_row_id=counter, accounts=[acc_a, acc_b], campaigns=[search, network]
    )
    blog = make_workspace(db, user, 777, "Блог", counter_row_id=other_counter, accounts=[acc_b], campaigns=[foreign])
    _sql(
        db,
        "INSERT INTO metrika_goals (user_id, counter_row_id, goal_id, name) VALUES (%s, %s, 9001, 'Заказ')",
        (user, counter),
    )
    return {
        "user": user,
        "conn": conn_id,
        "acc_a": acc_a,
        "acc_b": acc_b,
        "counter": counter,
        "ws": ws,
        "blog": blog,
        "search": search,
        "network": network,
        "foreign": foreign,
    }


def test_ensure_workspaces_creates_and_attaches_by_counter(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, login="acc")
    camp = make_campaign(db, user, acc, 1, "С счётчиком")
    make_campaign(db, user, acc, 2, "Без счётчика")
    _sql(db, "UPDATE direct_campaigns SET counter_ids = '[42, 43]'::jsonb WHERE id = %s", (camp,))
    make_counter(db, user, conn_id, 42, site="a.ru")

    created = workspaces.ensure_workspaces(user)
    assert len(created) == 1
    assert workspaces.ensure_workspaces(user) == []  # повторно не создаёт
    ws = workspaces.list_workspaces(user)[0]
    assert ws["counter_id"] == 42 and ws["name"] == "a.ru"
    assert workspaces.workspace_campaign_row_ids(user, ws["id"]) == [camp]
    assert [a["id"] for a in workspaces.list_workspace_accounts(user, ws["id"])] == [acc]


def test_metrika_only_connection_role_skips_counters(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "direct-only")
    _sql(db, "UPDATE yandex_connections SET use_metrika = FALSE WHERE id = %s", (conn_id,))
    make_counter(db, user, conn_id, 42)
    assert workspaces.ensure_workspaces(user) == []


def test_set_campaigns_moves_between_workspaces_and_links_account(db):
    s = _seed(db)
    user = s["user"]
    out = workspaces.set_workspace_campaigns(user, s["ws"], add=[s["foreign"]])
    assert out == {"added": 1, "moved": 1, "removed": 0}
    assert workspaces.campaign_workspace_id(user, s["foreign"]) == s["ws"]
    assert workspaces.workspace_campaign_row_ids(user, s["blog"]) == []

    removed = workspaces.remove_workspace_account(user, s["ws"], s["acc_b"])
    assert removed["campaigns_removed"] == 2  # РСЯ и перенесённый Блог
    assert workspaces.workspace_campaign_row_ids(user, s["ws"]) == [s["search"]]

    other = make_user(db, "x@example.com")
    with pytest.raises(YandexError):
        workspaces.set_workspace_campaigns(other, s["ws"], add=[s["search"]])


def test_add_account_matching_takes_only_campaigns_with_counter(db):
    s = _seed(db)
    user = s["user"]
    acc_c = make_account(db, user, s["conn"], login="acc-c")
    match = make_campaign(db, user, acc_c, 31, "Со счётчиком")
    make_campaign(db, user, acc_c, 32, "Чужая")
    _sql(db, "UPDATE direct_campaigns SET counter_ids = '[555]'::jsonb WHERE id = %s", (match,))
    out = workspaces.add_workspace_account(user, s["ws"], acc_c)
    assert out["campaigns_added"] == 1
    assert match in workspaces.workspace_campaign_row_ids(user, s["ws"])
    cands = {c["id"]: c for c in workspaces.account_candidates(user, s["ws"])}
    assert cands[acc_c]["linked"] and cands[acc_c]["campaigns_matching"] == 1


def test_update_workspace_validates_and_merges_ai_access(db):
    s = _seed(db)
    user, ws = s["user"], s["ws"]
    out = workspaces.update_workspace(
        user,
        ws,
        name="  Магазин 2 ",
        kpi_type="CPA",
        kpi_target=1500,
        monthly_budget_rub=120000.5,
        goal_id=9001,
        ai_access={"categories": {"budget": True, "unknown": True}, "max_change_pct": 900},
    )
    assert out["name"] == "Магазин 2" and out["kpi_type"] == "cpa" and out["kpi_target"] == 1500
    assert out["monthly_budget_rub"] == 120000.5 and out["goal_name"] == "Заказ"
    assert out["ai_access"]["categories"]["budget"] is True
    assert "unknown" not in out["ai_access"]["categories"]
    assert out["ai_access"]["max_change_pct"] == 500
    # Частичное обновление доступа не сбрасывает остальное.
    out = workspaces.update_workspace(user, ws, ai_access={"full_access": True})
    assert out["ai_access"]["full_access"] and out["ai_access"]["categories"]["budget"]
    with pytest.raises(YandexError):
        workspaces.update_workspace(user, ws, goal_id=1)
    with pytest.raises(YandexError):
        workspaces.update_workspace(user, ws, kpi_type="roi")
    with pytest.raises(YandexError):
        workspaces.update_workspace(user, ws, name=" ")


def test_analytics_uses_workspace_campaigns_and_goal(db):
    s = _seed(db)
    user = s["user"]
    for i in range(7):
        day = D1 + timedelta(days=i)
        _direct(user, s["acc_a"], 11, day, 1000, clicks=20, conv=1)
        _direct(user, s["acc_b"], 21, day, 500, clicks=10)
        _direct(user, s["acc_b"], 22, day, 9999)  # кампания другого пространства
        _direct(user, s["acc_a"], 11, day - timedelta(days=7), 500, clicks=20, conv=1)
        _metrika(db, user, s["counter"], day, 100)
    # Без цели — конверсии Директа.
    data = workspace_analytics(user, s["ws"], date_from=D1, date_to=D7)
    assert data["conversions_source"] == "direct"
    t = data["totals"]
    assert t["cost_kopecks"] == 7 * 1500 * 100 and t["clicks"] == 7 * 30 and t["conversions"] == 7
    assert t["cpa_kopecks"] == 150_000 and t["visits"] == 700 and t["bounce_rate"] == 20
    assert data["previous"]["cost_kopecks"] == 7 * 500 * 100
    assert data["compare"]["cost_kopecks"] == 200.0
    assert [c["name"] for c in data["by_campaign"]] == ["Поиск", "РСЯ"]
    assert {a["login"] for a in data["by_account"]} == {"acc-a", "acc-b"}
    assert len(data["by_day"]) == 7 and data["by_day"][0]["cost_kopecks"] == 150_000

    # С основной целью — конверсии из Метрики по кампаниям пространства.
    workspaces.update_workspace(user, s["ws"], goal_id=9001, kpi_type="cpa", kpi_target=1000)
    for i in range(7):
        day = D1 + timedelta(days=i)
        _metrika(db, user, s["counter"], day, 0, goal_id=9001, campaign_id=11, reaches=2)
        _metrika(db, user, s["counter"], day, 0, goal_id=9001, campaign_id=22, reaches=5)  # не наша
        _metrika(db, user, s["counter"], day, 0, goal_id=9001, campaign_id=None, reaches=4)
    data = workspace_analytics(user, s["ws"], date_from=D1, date_to=D7)
    assert data["conversions_source"] == "goal" and data["goal_name"] == "Заказ"
    assert data["totals"]["conversions"] == 14 and data["totals"]["site_goal_reaches"] == 28
    assert data["totals"]["cpa_kopecks"] == 75_000
    assert data["kpi"]["status"] == "ok"
    by_name = {c["name"]: c for c in data["by_campaign"]}
    assert by_name["Поиск"]["conversions"] == 14 and by_name["РСЯ"]["conversions"] == 0
    assert "tops" in data


def test_parse_range_limits():
    d_from, d_to = parse_range("2026-10-01", "2026-10-07")
    assert (d_to - d_from).days == 6
    with pytest.raises(YandexError):
        parse_range("2026-10-07", "2026-10-01")
    with pytest.raises(YandexError):
        parse_range("2024-01-01", "2026-10-01")
    with pytest.raises(YandexError):
        parse_range("bad", "")


def test_workspace_api(db):
    s = _seed(db)
    user = s["user"]
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    other = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    base = f"/api/web/workspaces/{s['ws']}"

    items = client.get("/api/web/workspaces", headers=auth).json()["items"]
    assert {w["name"] for w in items} == {"Магазин", "Блог"}
    assert client.get(base, headers=other).status_code == 404
    got = client.get(base, headers=auth).json()
    assert got["name"] == "Магазин" and len(got["accounts"]) == 2 and got["goals"][0]["goal_id"] == 9001
    assert client.patch(base, headers=auth, json={"kpi_type": "drr", "kpi_target": 15}).json()["kpi_type"] == "drr"
    assert client.patch(base, headers=auth, json={"kpi_type": "x"}).status_code == 400

    summary = client.get(f"{base}/summary", headers=auth).json()
    assert summary["row"]["name"] == "Магазин" and "week" in summary
    analytics = client.get(f"{base}/analytics?date_from=2026-10-19&date_to=2026-10-25", headers=auth).json()
    assert analytics["date_from"] == "2026-10-19"
    assert client.get(f"{base}/analytics?date_from=2026-10-25&date_to=2026-10-19", headers=auth).status_code == 400
    camps = client.get(f"{base}/campaigns", headers=auth).json()
    assert {c["name"] for c in camps["items"]} == {"Поиск", "РСЯ"}

    # Кампания чужого пространства недоступна через это пространство.
    assert client.get(f"{base}/campaigns/{s['foreign']}", headers=auth).status_code == 404
    assert client.get(f"{base}/campaigns/{s['search']}", headers=auth).json()["campaign"]["name"] == "Поиск"
    bulk = client.post(
        f"{base}/campaigns/actions", headers=auth, json={"campaign_ids": [s["foreign"]], "action": "pause"}
    )
    assert bulk.status_code == 404

    job = client.post(
        f"{base}/campaigns/{s['search']}/duplicate", headers=auth, json={"target_account_id": s["acc_b"]}
    ).json()["job"]
    assert job["kind"] == "duplicate_campaign"
    row = _sql(db, "SELECT max_attempts, payload FROM jobs WHERE id = %s", (job["id"],)).fetchone()
    assert row["max_attempts"] == 1 and row["payload"]["workspace_id"] == s["ws"]

    cands = client.get(f"{base}/campaigns/candidates", headers=auth).json()["items"]
    assert sum(len(g["campaigns"]) for g in cands) == 3
    moved = client.post(f"{base}/campaigns/set", headers=auth, json={"add": [s["foreign"]]}).json()
    assert moved["moved"] == 1
    assert client.get(f"{base}/actions", headers=auth).json()["items"] == []
    assert client.get(f"{base}/rules", headers=auth).json()["items"] == []
    options = client.get(f"{base}/campaign-options", headers=auth).json()
    assert options["counter_id"] == 555 and len(options["accounts"]) == 2
