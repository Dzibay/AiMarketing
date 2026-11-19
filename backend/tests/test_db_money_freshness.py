"""Блок «Деньги», честная свежесть цифр и «Обновить из Директа» на настоящем Postgres."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.account_load import request_stats_refresh
from app.analytics import portfolio_analytics, workspace_analytics, workspace_summary
from app.jobs import claim
from app.main import app
from app.overview import build_overview
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_user
from tests.test_db_overview import TODAY, YESTERDAY, _seed, _sql, _stat

WORKER = "test-host:1:0"


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr("app.overview.today_msk", lambda: TODAY)
    monkeypatch.setattr("app.analytics.today_msk", lambda: TODAY)


def _second_login(db, s):
    """У Ромашки второй логин: 50 000 ₽ при 1 000 ₽ в день — денег на 50 дней."""
    with db.connection() as conn:
        connection = conn.execute(
            "SELECT connection_id FROM direct_accounts WHERE id = %s", (s["acc_r"],)
        ).fetchone()["connection_id"]
    acc = make_account(db, s["user"], connection, name="Ромашка Запас", login="romashka-2")
    campaign = make_campaign(db, s["user"], acc, 41, "Запас")
    for i in range(7):
        _stat(db, s["user"], acc, 41, YESTERDAY - timedelta(days=i), 1000)
    _sql(db, "INSERT INTO workspace_accounts (workspace_id, account_id, user_id) VALUES (%s, %s, %s)", (s["ws_r"], acc, s["user"]))
    _sql(db, "INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id) VALUES (%s, %s, %s)", (s["ws_r"], campaign, s["user"]))
    _sql(db, "UPDATE direct_accounts SET balance_kopecks = 5000000, balance_currency = 'RUB' WHERE id = %s", (acc,))
    return acc


def test_money_block_lists_every_login(db, today):
    s = _seed(db)
    second = _second_login(db, s)
    overview = build_overview(s["user"], TODAY)

    balances = overview["balances"]
    # Первым — у кого деньги кончатся раньше; без счёта — в конце.
    assert [b["login"] for b in balances[:2]] == ["romashka", "romashka-2"]
    first = balances[0]
    assert first["direct_url"] == "https://direct.yandex.ru/dna/grid/campaigns?ulogin=romashka"
    assert first["workspace_ids"] == [s["ws_r"]] and first["workspace_name"] == "Ромашка"
    assert first["days_on_balance"] == round(600_000 / (3_650_000 / 7), 1)
    assert first["runs_out_on"] == (TODAY + timedelta(days=1)).isoformat()
    assert first["spend_per_day_kopecks"] == round(3_650_000 / 7)
    assert balances[1]["days_on_balance"] == 50.0
    assert {b["login"] for b in balances[2:]} >= {"vector", "free"}
    assert all(b["balance_kopecks"] is None for b in balances[2:])
    assert "old" not in {b["login"] for b in balances}  # кабинет архивного клиента скрыт

    # Сумма в строке проекта — по всем логинам, а дни — по самому короткому запасу.
    row = next(r for r in overview["workspaces"] if r["id"] == s["ws_r"])
    assert row["balance_kopecks"] == 5_600_000
    assert row["days_on_balance"] == first["days_on_balance"]

    money = [p for p in overview["problems"] if p["kind"] == "balance"]
    assert len(money) == 1 and money[0]["account_id"] == s["acc_r"]
    assert money[0]["direct_url"].endswith("ulogin=romashka")
    assert money[0]["days_on_balance"] == first["days_on_balance"]

    summary = workspace_summary(s["user"], s["ws_r"])
    assert [b["account_id"] for b in summary["balances"]] == [s["acc_r"], second]
    assert workspace_summary(s["user"], s["ws_v"])["balances"][0]["login"] == "vector"


def test_headline_top_up_opens_that_login(db, today):
    s = _seed(db)
    _sql(db, "UPDATE yandex_connections SET last_sync_status = 'ok', last_sync_error = '' WHERE user_id = %s", (s["user"],))
    _sql(db, "UPDATE direct_accounts SET stats_synced_at = now() WHERE user_id = %s", (s["user"],))
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    for url in ("/api/web/overview/headline", f"/api/web/workspaces/{s['ws_r']}/headline"):
        link = client.get(url, headers=auth).json()["headline"]["link"]
        assert link == {
            "label": "Пополнить в Директе",
            "to": "https://direct.yandex.ru/dna/grid/campaigns?ulogin=romashka",
            "external": True,
        }


def _stage(db, account, **ago):
    at = (datetime.now(timezone.utc) - timedelta(**ago)).isoformat()
    state = {"stats": {"status": "ok", "at": at}}
    _sql(db, "UPDATE direct_accounts SET loaded_at = now(), load_state = %s::jsonb WHERE id = %s", (json.dumps(state), account))


def _rows_updated(db, account, **ago):
    _sql(db, "UPDATE stats_daily SET updated_at = now() - %s WHERE account_id = %s", (timedelta(**ago), account))


def _at(value):
    return datetime.fromisoformat(value)


def test_freshness_is_the_oldest_login(db, today):
    s = _seed(db)
    now = datetime.now(timezone.utc)
    _stage(db, s["acc_r"], hours=1)
    _rows_updated(db, s["acc_r"], hours=3)
    # Вектор загружен, но статистику не перечитывали 10 часов.
    _sql(db, "UPDATE direct_accounts SET loaded_at = now() WHERE id = %s", (s["acc_v"],))
    _rows_updated(db, s["acc_v"], hours=10)

    week = dict(date_from=YESTERDAY - timedelta(days=6), date_to=YESTERDAY, with_tops=False)
    r = workspace_analytics(s["user"], s["ws_r"], **week)["freshness"]
    assert abs((_at(r["direct_at"]) - (now - timedelta(hours=1))).total_seconds()) < 60
    assert r["loading"] is False
    assert [(a["id"], a["lagging"]) for a in r["accounts"]] == [(s["acc_r"], False)]

    v = workspace_analytics(s["user"], s["ws_v"], **week)
    assert v["freshness"]["accounts"][0]["lagging"] is True
    assert v["direct_updated_at"] == v["freshness"]["direct_at"]
    assert abs((_at(v["direct_updated_at"]) - (now - timedelta(hours=10))).total_seconds()) < 60

    # «Все проекты» — по самому старому кабинету.
    p = portfolio_analytics(s["user"], date_from=week["date_from"], date_to=week["date_to"])["freshness"]
    assert p["direct_at"] == v["freshness"]["direct_at"]
    assert {a["id"] for a in p["accounts"] if a["lagging"]} == {s["acc_v"]}

    summary = workspace_summary(s["user"], s["ws_v"])
    assert summary["week"]["freshness"]["accounts"][0]["lagging"] is True


def test_freshness_while_first_load_runs(db, today):
    s = _seed(db)
    _sql(db, "DELETE FROM stats_daily WHERE account_id = %s", (s["acc_v"],))
    _sql(
        db,
        """
        INSERT INTO jobs (user_id, kind, payload, status, progress_done, progress_total, started_at)
        VALUES (%s, 'load_account', %s::jsonb, 'running', 1, 4, now() - interval '3 minutes')
        """,
        (s["user"], json.dumps({"account_id": s["acc_v"]})),
    )
    week = dict(date_from=YESTERDAY - timedelta(days=6), date_to=YESTERDAY, with_tops=False)
    f = workspace_analytics(s["user"], s["ws_v"], **week)["freshness"]
    # 3 минуты на 1 шаг из 4 → ещё ≈9 минут (часы БД и теста могут разойтись на секунды).
    assert f["loading"] is True and f["eta_minutes"] in (9, 10)
    assert f["accounts"][0]["updating"] is True and f["accounts"][0]["lagging"] is False
    assert f["direct_at"] is None


def test_request_stats_refresh(db):
    s = _seed(db)
    acc = s["acc_r"]
    assert request_stats_refresh(s["user"], acc) == "initial"  # первая загрузка принесёт статистику сама

    _stage(db, acc, hours=2)
    assert request_stats_refresh(s["user"], acc) == "queued"
    with db.connection() as conn:
        state = conn.execute("SELECT load_state FROM direct_accounts WHERE id = %s", (acc,)).fetchone()["load_state"]
    assert state["stats"]["requested"] is True and state["stats"]["status"] == "ok"

    _stage(db, acc, minutes=5)
    assert request_stats_refresh(s["user"], acc) == "fresh"

    _stage(db, acc, hours=2)
    from app.job_handlers import submit

    submit("load_account", s["user"], {"account_id": acc})
    assert claim(WORKER)["kind"] == "load_account"
    assert request_stats_refresh(s["user"], acc) == "running"
    with db.connection() as conn:
        state = conn.execute("SELECT load_state FROM direct_accounts WHERE id = %s", (acc,)).fetchone()["load_state"]
    assert "requested" not in state["stats"]  # идущая загрузка затёрла бы отметку — не ставим

    _sql(db, "UPDATE direct_accounts SET enabled = FALSE WHERE id = %s", (acc,))
    assert request_stats_refresh(s["user"], acc) == "disabled"

    stranger = make_user(db, "x@example.com")
    with pytest.raises(YandexError):
        request_stats_refresh(stranger, acc)


def test_sync_campaigns_also_queues_stats(db):
    s = _seed(db)
    _stage(db, s["acc_r"], hours=2)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    url = f"/api/web/workspaces/{s['ws_r']}/campaigns/sync"

    first = client.post(url, headers=auth).json()
    assert [j["kind"] for j in first["jobs"]] == ["sync_account_campaigns"]
    [stats] = first["stats"]
    assert (stats["account_id"], stats["name"], stats["status"]) == (s["acc_r"], "Ромашка Директ", "queued")
    assert stats["job"]["kind"] == "load_account"

    # Второе нажатие — та же задача, без дубля в очереди.
    again = client.post(url, headers=auth).json()
    assert again["stats"][0]["job"]["id"] == stats["job"]["id"]
    with db.connection() as conn:
        count = conn.execute("SELECT count(*) AS n FROM jobs WHERE kind = 'load_account'").fetchone()["n"]
    assert count == 1

    # Статистику только что читали — загрузку не ставим.
    _sql(db, "DELETE FROM jobs")
    _stage(db, s["acc_r"], minutes=3)
    fresh = client.post(url, headers=auth).json()
    assert fresh["stats"][0]["status"] == "fresh" and fresh["stats"][0]["job"] is None

    campaigns = client.get(f"/api/web/workspaces/{s['ws_r']}/campaigns", headers=auth).json()
    assert "direct_at" in campaigns["freshness"] and "accounts" in campaigns["freshness"]

    stranger = make_user(db, "y@example.com")
    other = {"Authorization": f"Bearer {make_session(stranger)}"}
    assert client.post(url, headers=other).status_code == 404
