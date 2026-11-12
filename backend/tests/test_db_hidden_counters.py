"""Удаление сайта из Толка и возврат (app/sites.py) на настоящем Postgres."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import plans, sites, workspaces, yandex
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_counter, make_user, make_workspace

DAY = date(2026, 10, 20)


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []


def _one(pool, query, params=()):
    rows = _sql(pool, query, params)
    return rows[0] if rows else None


def _seed(db):
    """Два сайта; у магазина — кампания, статистика Метрики и Директа, чат и ждущая загрузка."""
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    account = make_account(db, user, conn_id, name="Кабинет", login="acc")
    shop_row = make_counter(db, user, conn_id, 555, site="shop.ru")
    blog_row = make_counter(db, user, conn_id, 777, site="blog.ru")
    campaign = make_campaign(db, user, account, 11, "Поиск")
    shop = make_workspace(db, user, 555, "Магазин", counter_row_id=shop_row, accounts=[account], campaigns=[campaign])
    blog = make_workspace(db, user, 777, "Блог", counter_row_id=blog_row)
    _sql(
        db,
        """
        INSERT INTO stats_daily (user_id, day, source, counter_row_id, visits, users_count, bounce_rate)
        VALUES (%s, %s, 'metrika', %s, 100, 50, 20), (%s, %s, 'metrika', %s, 40, 20, 20)
        """,
        (user, DAY, shop_row, user, DAY, blog_row),
    )
    _sql(
        db,
        """
        INSERT INTO stats_daily (user_id, day, source, account_id, campaign_id, impressions, clicks, cost_kopecks)
        VALUES (%s, %s, 'direct', %s, 11, 1000, 10, 50000)
        """,
        (user, DAY, account),
    )
    _sql(db, "UPDATE metrika_counters SET load_state = '{\"done\": true}', loaded_at = NOW() WHERE id = %s", (shop_row,))
    _sql(
        db,
        "INSERT INTO jobs (user_id, kind, payload, status) VALUES (%s, 'load_counter', %s, 'queued')",
        (user, f'{{"counter_row_id": {shop_row}}}'),
    )
    return {
        "user": user,
        "conn": conn_id,
        "account": account,
        "campaign": campaign,
        "shop_row": shop_row,
        "blog_row": blog_row,
        "shop": shop,
        "blog": blog,
    }


def _enabled(db, user, counter_id) -> list[bool]:
    return [
        r["enabled"]
        for r in _sql(
            db, "SELECT enabled FROM metrika_counters WHERE user_id = %s AND counter_id = %s ORDER BY id", (user, counter_id)
        )
    ]


def _choice_ids(user) -> list[int]:
    return [c["counter_id"] for c in workspaces.counter_choices(user)["items"]]


def test_hide_archives_project_and_disables_counter(db):
    s = _seed(db)
    out = sites.hide_site(s["user"], 555)
    assert out["workspace_id"] == s["shop"] and out["purged"] is False and out["site"] == "shop.ru"

    assert _enabled(db, s["user"], 555) == [False]
    ws = _one(db, "SELECT archived_at FROM workspaces WHERE id = %s", (s["shop"],))
    assert ws["archived_at"] is not None
    # Ждущая загрузка убрана, данные и кампании на месте.
    assert _one(db, "SELECT COUNT(*) AS n FROM jobs WHERE kind = 'load_counter'")["n"] == 0
    assert _one(db, "SELECT COUNT(*) AS n FROM stats_daily WHERE counter_row_id = %s", (s["shop_row"],))["n"] == 1
    assert _one(db, "SELECT COUNT(*) AS n FROM workspace_campaigns WHERE workspace_id = %s", (s["shop"],))["n"] == 1

    choices = workspaces.counter_choices(s["user"])
    assert [c["counter_id"] for c in choices["items"]] == [777]
    assert [(h["counter_id"], h["site"], h["workspace_id"]) for h in choices["hidden"]] == [(555, "shop.ru", s["shop"])]
    # Блог не задет.
    assert _enabled(db, s["user"], 777) == [True]
    kinds = [r["kind"] for r in _sql(db, "SELECT kind FROM user_events WHERE user_id = %s", (s["user"],))]
    assert "site_hidden" in kinds


def test_hidden_counter_stays_hidden_after_sync_and_role_toggle(db):
    s = _seed(db)
    sites.hide_site(s["user"], 555, purge=True)
    assert workspaces.ensure_workspaces(s["user"]) == []

    # Синхронизация того же подключения и новый логин с тем же счётчиком.
    yandex.upsert_metrika(s["user"], s["conn"], [{"counter_id": 555, "name": "Магазин", "site": "shop.ru"}])
    other = make_connection(db, s["user"], "owner")
    yandex.upsert_metrika(s["user"], other, [{"counter_id": 555, "name": "Магазин", "site": "shop.ru"}])
    assert _enabled(db, s["user"], 555) == [False, False]

    # Роль Метрики выключили и включили — удалённый сайт остаётся выключенным, блог включается.
    yandex.set_connection_roles(s["user"], s["conn"], use_metrika=False)
    assert _enabled(db, s["user"], 777) == [False]
    yandex.set_connection_roles(s["user"], s["conn"], use_metrika=True)
    assert _enabled(db, s["user"], 555) == [False, False]
    assert _enabled(db, s["user"], 777) == [True]

    assert workspaces.ensure_workspaces(s["user"]) == []
    assert 555 not in _choice_ids(s["user"])
    assert _one(db, "SELECT COUNT(*) AS n FROM workspaces WHERE counter_id = 555")["n"] == 0

    listed = {m["counter_id"]: m for m in yandex.list_metrika(s["user"])}
    assert listed[555]["hidden"] is True and listed[777]["hidden"] is False

    with pytest.raises(YandexError) as err:
        yandex.set_metrika_enabled(s["user"], s["shop_row"], True)
    assert err.value.code == "hidden"
    # Выключить можно.
    assert yandex.set_metrika_enabled(s["user"], s["shop_row"], False)["hidden"] is True


def test_purge_deletes_project_and_metrika_data_only(db):
    s = _seed(db)
    out = sites.hide_site(s["user"], 555, purge=True)
    assert out["purged"] is True and out["workspace_id"] is None

    assert _one(db, "SELECT 1 AS x FROM workspaces WHERE id = %s", (s["shop"],)) is None
    assert _one(db, "SELECT COUNT(*) AS n FROM stats_daily WHERE counter_row_id = %s", (s["shop_row"],))["n"] == 0
    # Статистика Директа, кампании, кабинет и второй сайт остаются.
    assert _one(db, "SELECT COUNT(*) AS n FROM stats_daily WHERE source = 'direct'")["n"] == 1
    assert _one(db, "SELECT COUNT(*) AS n FROM stats_daily WHERE counter_row_id = %s", (s["blog_row"],))["n"] == 1
    assert _one(db, "SELECT 1 AS x FROM direct_campaigns WHERE id = %s", (s["campaign"],)) is not None
    assert _one(db, "SELECT 1 AS x FROM direct_accounts WHERE id = %s", (s["account"],)) is not None
    row = _one(db, "SELECT load_state, loaded_at FROM metrika_counters WHERE id = %s", (s["shop_row"],))
    assert row["load_state"] == {} and row["loaded_at"] is None
    assert workspaces.counter_choices(s["user"])["hidden"][0]["workspace_id"] is None


def test_restore_brings_project_back(db):
    s = _seed(db)
    sites.hide_site(s["user"], 555)
    out = sites.restore_site(s["user"], 555)
    assert out["note"] == "" and out["workspace"]["id"] == s["shop"] and out["workspace"]["archived"] is False
    assert _enabled(db, s["user"], 555) == [True]
    assert 555 in _choice_ids(s["user"])
    assert workspaces.counter_choices(s["user"])["hidden"] == []

    with pytest.raises(YandexError) as err:
        sites.restore_site(s["user"], 555)
    assert err.value.code == "not-found"


def test_restore_after_purge_creates_new_project(db):
    s = _seed(db)
    sites.hide_site(s["user"], 555, purge=True)
    out = sites.restore_site(s["user"], 555)
    assert out["workspace"]["id"] != s["shop"] and out["workspace"]["counter_id"] == 555


def test_restore_over_plan_limit_keeps_site_available(db):
    s = _seed(db)
    sites.hide_site(s["user"], 555)
    plans.set_subscription(s["user"], overrides={"projects_limit": 1})
    out = sites.restore_site(s["user"], 555)
    assert out["workspace"] is None and out["note"]
    assert _enabled(db, s["user"], 555) == [True]
    item = next(c for c in workspaces.counter_choices(s["user"])["items"] if c["counter_id"] == 555)
    assert item["state"] == "locked"


def test_unarchiving_project_restores_site(db):
    s = _seed(db)
    sites.hide_site(s["user"], 555)
    ws = workspaces.update_workspace(s["user"], s["shop"], archived=False)
    assert ws["archived"] is False
    assert _enabled(db, s["user"], 555) == [True]
    assert workspaces.counter_choices(s["user"])["hidden"] == []


def test_hiding_frees_plan_slot_for_auto_projects(db):
    """Счётчиков больше, чем мест в тарифе: удалили лишний — проекты создаются сами."""
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    plans.set_subscription(user, overrides={"projects_limit": 1})
    make_counter(db, user, conn_id, 101, site="a.ru")
    make_counter(db, user, conn_id, 202, site="b.ru")
    assert workspaces.ensure_workspaces(user) == []
    sites.hide_site(user, 202)
    created = workspaces.ensure_workspaces(user)
    assert len(created) == 1
    assert _one(db, "SELECT counter_id FROM workspaces WHERE id = %s", (created[0],))["counter_id"] == 101


def test_hide_unknown_or_foreign_counter_is_not_found(db):
    s = _seed(db)
    stranger = make_user(db, "x@example.com")
    with pytest.raises(YandexError) as err:
        sites.hide_site(stranger, 555)
    assert err.value.code == "not-found"
    with pytest.raises(YandexError):
        sites.hide_site(s["user"], 999)
    assert _enabled(db, s["user"], 555) == [True]


def test_api_hide_and_restore(db):
    s = _seed(db)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session(s['user'])}"}

    res = client.post("/api/web/workspaces/sites/555/hide", json={"purge": False}, headers=headers)
    assert res.status_code == 200 and res.json()["workspace_id"] == s["shop"]
    body = client.get("/api/web/workspaces/counters", headers=headers).json()
    assert [h["counter_id"] for h in body["hidden"]] == [555]

    res = client.patch(f"/api/web/metrika/{s['shop_row']}", json={"enabled": True}, headers=headers)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "hidden"

    res = client.post("/api/web/workspaces/sites/555/restore", headers=headers)
    assert res.status_code == 200 and res.json()["workspace"]["id"] == s["shop"]
    queued = _sql(db, "SELECT payload FROM jobs WHERE kind = 'load_counter' AND user_id = %s", (s["user"],))
    assert [q["payload"]["counter_row_id"] for q in queued] == [s["shop_row"]]

    stranger = make_user(db, "x@example.com")
    other = {"Authorization": f"Bearer {make_session(stranger)}"}
    assert client.post("/api/web/workspaces/sites/555/hide", json={}, headers=other).status_code == 404
    assert client.post("/api/web/workspaces/sites/555/restore", headers=other).status_code == 404


def test_restore_dismissed_counter_only_offers_it_again(db):
    """«Не показывать» в «Добавить сайт» и обратно: проект сам не создаётся, архивный — остаётся в архиве."""
    s = _seed(db)
    make_counter(db, s["user"], s["conn"], 303, site="new.ru")
    sites.hide_site(s["user"], 303)
    workspaces.update_workspace(s["user"], s["blog"], archived=True)
    sites.hide_site(s["user"], 777)
    hidden = {h["counter_id"]: h for h in workspaces.counter_choices(s["user"])["hidden"]}
    assert hidden[303]["had_project"] is False and hidden[777]["had_project"] is False

    assert sites.restore_site(s["user"], 303) == {"counter_id": 303, "workspace": None, "note": ""}
    assert sites.restore_site(s["user"], 777)["workspace"] is None
    states = {c["counter_id"]: c["state"] for c in workspaces.counter_choices(s["user"])["items"]}
    assert states[303] == "available" and states[777] == "archived"
    assert _one(db, "SELECT COUNT(*) AS n FROM workspaces WHERE counter_id = 303")["n"] == 0
