"""Сводка, синк расхода/баланса и дайджест на настоящем Postgres."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import digest, direct_balance, stats
from app.clients import create_client, update_client
from app.clock import now_msk
from app.main import app
from app.overview import build_overview
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_counter, make_user, make_workspace

TODAY = date(2026, 10, 26)
YESTERDAY = TODAY - timedelta(days=1)


def _stat(pool, user, account, campaign, day, cost_rub, conv=0, revenue_rub=0):
    stats.upsert_direct_stat(
        user_id=user,
        day=day,
        account_id=account,
        campaign_id=campaign,
        impressions=100,
        clicks=10,
        cost_kopecks=cost_rub * 100,
        conversions=conv,
        revenue_kopecks=revenue_rub * 100,
    )


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        conn.execute(query, params)


def _seed(db):
    user = make_user(db)
    connection = make_connection(db, user, "agency")
    romashka = create_client(user, name="ООО Ромашка")
    vector = create_client(user, name="ООО Вектор")
    archived = create_client(user, name="Архив")
    acc_r = make_account(db, user, connection, name="Ромашка Директ", login="romashka")
    acc_v = make_account(db, user, connection, name="Вектор Директ", login="vector")
    acc_free = make_account(db, user, connection, name="Ничей", login="free")
    acc_old = make_account(db, user, connection, name="Старый", login="old")
    update_client(user, archived["id"], archived=True)

    # Ромашка: 14 дней по 4000 ₽ / 4 конв., вчера 12 000 ₽ / 4 конв. — скачок расхода и CPA.
    for i in range(1, 15):
        _stat(db, user, acc_r, 11, YESTERDAY - timedelta(days=i), 4000, 4)
    _stat(db, user, acc_r, 11, YESTERDAY, 12000, 4)
    _stat(db, user, acc_r, 11, TODAY, 1000)
    _stat(db, user, acc_r, 12, TODAY - timedelta(days=6), 500)  # кампания 12 тратила и встала
    r_search = make_campaign(db, user, acc_r, 11, "Поиск")
    r_network = make_campaign(db, user, acc_r, 12, "РСЯ", state="OFF")
    ws_r = make_workspace(
        db,
        user,
        101,
        "Ромашка",
        counter_row_id=make_counter(db, user, connection, 101),
        client_id=romashka["id"],
        accounts=[acc_r],
        campaigns=[r_search, r_network],
        kpi_type="cpa",
        kpi_target=900,
        monthly_budget_rub=100000,
    )
    _sql(db, "UPDATE direct_accounts SET balance_kopecks = 600000, balance_currency = 'RUB' WHERE id = %s", (acc_r,))

    # Вектор: ДРР 10% при цели 20%, кампания отклонена модерацией.
    for i in range(0, 7):
        _stat(db, user, acc_v, 21, YESTERDAY - timedelta(days=i), 1000, 1, 10000)
    v_goods = make_campaign(db, user, acc_v, 21, "Товары", status="REJECTED")
    ws_v = make_workspace(
        db,
        user,
        102,
        "Вектор",
        counter_row_id=make_counter(db, user, connection, 102),
        client_id=vector["id"],
        accounts=[acc_v],
        campaigns=[v_goods],
        kpi_type="drr",
        kpi_target=20,
    )
    # Пространство архивного клиента скрыто вместе с кабинетом.
    old_campaign = make_campaign(db, user, acc_old, 31, "Старая")
    make_workspace(db, user, 103, "Архивный сайт", client_id=archived["id"], accounts=[acc_old], campaigns=[old_campaign])
    _stat(db, user, acc_old, 31, YESTERDAY, 99999)
    _sql(
        db,
        "UPDATE yandex_connections SET last_sync_status = 'error', last_sync_error = 'token-expired' WHERE id = %s",
        (connection,),
    )
    return {
        "user": user,
        "romashka": romashka["id"],
        "vector": vector["id"],
        "ws_r": ws_r,
        "ws_v": ws_v,
        "acc_r": acc_r,
        "acc_v": acc_v,
        "acc_free": acc_free,
    }


def _problems(overview, workspace_name=None):
    return [
        (p["severity"], p["kind"])
        for p in overview["problems"]
        if workspace_name is None or p["workspace_name"] == workspace_name
    ]


def test_overview_rows(db):
    s = _seed(db)
    overview = build_overview(s["user"], TODAY)
    rows = {r["name"]: r for r in overview["workspaces"]}
    assert set(rows) == {"Ромашка", "Вектор", "Без пространства"}  # пространство архивного клиента скрыто

    r = rows["Ромашка"]
    assert (r["spend_today"], r["spend_yesterday"]) == (100_000, 1_200_000)
    # 7 полных дней: 19–24.10 по 4000 ₽ + 500 ₽ кампании 12 + 12 000 ₽ вчера.
    assert r["spend_7d"] == 3_650_000
    assert r["conversions_7d"] == 28
    assert r["cpa_7d"] == round(3_650_000 / 28)
    assert r["kpi"]["status"] == "bad"
    assert r["budget"]["spent_kopecks"] == 6_950_000  # с 11.10 по сегодня
    assert r["budget"]["pace_pct"] == round(6_850_000 / 25 * 31 / 10_000_000 * 100)
    assert r["balance_kopecks"] == 600_000
    assert r["days_on_balance"] == round(600_000 / (3_650_000 / 7), 1)
    assert r["severity"] == "critical"

    v = rows["Вектор"]
    assert (v["revenue_7d"], v["drr_7d"], v["kpi"]["status"]) == (7_000_000, 10.0, "ok")
    assert v["balance_kopecks"] is None and v["days_on_balance"] is None

    assert rows["Без пространства"]["accounts"] == 1
    assert rows["Ромашка"]["client_name"] == "ООО Ромашка" and rows["Ромашка"]["campaigns"] == 2
    assert overview["totals"]["spend_yesterday"] == 1_200_000 + 100_000
    assert overview["today_updated_at"] is not None

    folders = {f["name"]: f for f in overview["folders"]}
    assert set(folders) == {"ООО Ромашка", "ООО Вектор"}
    assert folders["ООО Ромашка"]["workspace_ids"] == [s["ws_r"]]
    assert folders["ООО Ромашка"]["spend_yesterday"] == 1_200_000


def test_client_shared_budget_and_unassigned_spend(db):
    s = _seed(db)
    update_client(s["user"], s["romashka"], monthly_budget_rub=50000)
    second = make_workspace(db, s["user"], 104, "Ромашка Блог", client_id=s["romashka"])
    make_campaign(db, s["user"], s["acc_r"], 13, "Без сайта")
    _stat(db, s["user"], s["acc_r"], 13, YESTERDAY, 300)
    overview = build_overview(s["user"], TODAY)
    folder = next(f for f in overview["folders"] if f["id"] == s["romashka"])
    assert sorted(folder["workspace_ids"]) == sorted([s["ws_r"], second])
    assert folder["budget"]["status"] == "exhausted"  # больше 50 000 ₽ за месяц
    shared = [p for p in overview["problems"] if p["kind"] == "budget" and p["client_id"] == s["romashka"]]
    assert any("Общий бюджет клиента" in p["title"] for p in shared)
    rows = {r["id"]: r for r in overview["workspaces"]}
    # Кампания кабинета вне пространств — в строке «Без пространства».
    assert rows[None]["spend_yesterday"] == 30_000


def test_where_it_burns(db):
    s = _seed(db)
    overview = build_overview(s["user"], TODAY)
    severities = [p["severity"] for p in overview["problems"]]
    assert severities == sorted(severities, key=["critical", "warning", "info"].index)

    assert set(_problems(overview, "Ромашка")) == {
        ("critical", "balance"),
        ("warning", "kpi"),
        ("warning", "stopped"),
        ("warning", "spend_spike"),
        ("warning", "cpa_spike"),
    }
    assert _problems(overview, "Вектор") == [("critical", "moderation")]

    token = [p for p in overview["problems"] if p["kind"] == "token"]
    assert len(token) == 1 and token[0]["severity"] == "critical"
    assert token[0]["workspace_name"] == "Вектор, Ромашка, Без пространства"
    rows = {r["name"]: r for r in overview["workspaces"]}
    # Проблема логина засчитана каждому пространству на нём.
    assert rows["Без пространства"]["problems"] == {"critical": 1, "warning": 0, "info": 0}
    assert rows["Вектор"]["problems"]["critical"] == 2

    stopped = next(p for p in overview["problems"] if p["kind"] == "stopped")
    assert stopped["object_name"] == "РСЯ" and stopped["campaign_row_id"]


def test_overview_is_private(db):
    s = _seed(db)
    stranger = make_user(db, "b@example.com")
    overview = build_overview(stranger, TODAY)
    assert overview["workspaces"] == [] and overview["problems"] == []
    assert build_overview(s["user"], TODAY)["workspaces"]


def test_stats_sync_saves_revenue_and_account_status(db, monkeypatch):
    s = _seed(db)

    def report(token, login, day):
        if login == "vector":
            raise YandexError("direct-denied")
        return [
            {
                "campaign_id": 11,
                "impressions": 5,
                "clicks": 2,
                "cost_kopecks": 777,
                "conversions": 1,
                "revenue_kopecks": 5000,
            }
        ]

    monkeypatch.setattr(stats, "get_connection_access_token", lambda *a: "tok")
    monkeypatch.setattr(stats, "fetch_direct_campaign_report", report)
    result = stats.sync_direct_stats_for_user(s["user"], TODAY)
    assert (result["synced"], result["errors"]) == (3, 1)

    with db.connection() as conn:
        row = conn.execute(
            "SELECT cost_kopecks, revenue_kopecks FROM stats_daily WHERE account_id = %s AND day = %s",
            (s["acc_r"], TODAY),
        ).fetchone()
        accounts = {
            r["id"]: r
            for r in conn.execute("SELECT id, stats_error, stats_synced_at FROM direct_accounts").fetchall()
        }
    assert (row["cost_kopecks"], row["revenue_kopecks"]) == (777, 5000)  # перезаписано, не задвоено
    assert accounts[s["acc_r"]]["stats_error"] == "" and accounts[s["acc_r"]]["stats_synced_at"]
    assert accounts[s["acc_v"]]["stats_error"] == "direct-denied"

    problems = _problems(build_overview(s["user"], TODAY), "Вектор")
    assert ("warning", "stats_error") in problems


def test_balance_sync(db, monkeypatch):
    s = _seed(db)
    calls = []

    def balances(token, logins):
        calls.append(sorted(logins))
        return {"romashka": {"amount_kopecks": 123, "currency": "RUB"}}

    monkeypatch.setattr(direct_balance, "get_connection_access_token", lambda *a: "tok")
    monkeypatch.setattr(direct_balance, "fetch_balances", balances)
    assert direct_balance.sync_balances_for_user(s["user"]) == {"synced": 4, "errors": 0}
    assert calls == [["free", "old", "romashka", "vector"]]  # один запрос на подключение

    def denied(token, logins):
        raise YandexError("token-expired")

    monkeypatch.setattr(direct_balance, "fetch_balances", denied)
    assert direct_balance.sync_balances_for_user(s["user"])["errors"] == 4
    with db.connection() as conn:
        accounts = {
            r["id"]: r
            for r in conn.execute("SELECT id, balance_kopecks, balance_error FROM direct_accounts").fetchall()
        }
    # Ошибка не затирает последний известный баланс.
    assert (accounts[s["acc_r"]]["balance_kopecks"], accounts[s["acc_r"]]["balance_error"]) == (123, "token-expired")
    assert accounts[s["acc_v"]]["balance_kopecks"] is None  # общего счёта нет


def test_digest_schedule_and_send(db, monkeypatch):
    s = _seed(db)
    hour = now_msk().hour
    _sql(db, "UPDATE users SET telegram_chat_id = '42', digest_hour = %s WHERE id = %s", (hour, s["user"]))
    other = make_user(db, "late@example.com")
    connection = make_connection(db, other, "late")
    make_account(db, other, connection, login="late")
    # Час отправки давно прошёл (окно — 3 часа) — сегодня уже не шлём.
    _sql(db, "UPDATE users SET telegram_chat_id = '43', digest_hour = %s WHERE id = %s", ((hour + 12) % 24, other))

    assert [user for user, _ in digest.users_due_for_digest()] == [s["user"]]

    sent = []
    synced = []
    monkeypatch.setattr(digest, "notify_user", lambda user, text: sent.append((user, text)) or True)
    monkeypatch.setattr(digest, "sync_stats_for_user", lambda user, day: synced.append(day))
    monkeypatch.setattr(digest, "sync_balances_for_user", lambda user: None)
    result = digest.send_digest(s["user"], TODAY)
    assert result == {"sent": True, "day": TODAY.isoformat()}
    assert synced == [YESTERDAY]
    assert "Где горит" in sent[0][1] and "Ромашка" in sent[0][1]

    # Отмечено отправленным: за сегодня второй раз не попадает в очередь.
    _sql(db, "UPDATE users SET last_digest_on = %s WHERE id = %s", (now_msk().date(), s["user"]))
    assert digest.users_due_for_digest() == []

    _sql(db, "UPDATE users SET last_digest_on = NULL WHERE id = %s", (s["user"],))
    digest.update_digest_settings(s["user"], enabled=False)
    assert digest.users_due_for_digest() == []


def test_api(db, monkeypatch):
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}

    assert client.get("/api/web/overview").status_code == 401
    overview = client.get("/api/web/overview", headers=auth).json()
    assert {r["name"] for r in overview["workspaces"]} >= {"Ромашка", "Вектор"}

    job = client.post("/api/web/overview/refresh", headers=auth).json()["job"]
    assert job["kind"] == "stats_today"

    settings = client.put("/api/web/digest", headers=auth, json={"hour": 7}).json()
    assert (settings["enabled"], settings["hour"], settings["telegram_linked"]) == (True, 7, False)
    assert client.put("/api/web/digest", headers=auth, json={"hour": 24}).status_code == 400
    assert "AiMarketing" in client.get("/api/web/digest/preview", headers=auth).json()["text"]

    monkeypatch.setattr("app.api.overview.notify_user", lambda user, text: False)
    assert client.post("/api/web/digest/send", headers=auth).status_code == 400
