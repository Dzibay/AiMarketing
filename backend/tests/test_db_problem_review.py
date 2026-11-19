"""Утренний разбор «Где горит» на настоящем Postgres: снимок за день, «новое» и «Со вчера»."""

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from app import snoozes, stats
from app.clients import create_client
from app.clock import MSK
from app.main import app
from app.overview import build_overview
from app.web_auth import make_session
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

TODAY = date(2026, 10, 26)
YESTERDAY = TODAY - timedelta(days=1)


def _now(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=MSK)


def _sql(db, query, params=()):
    with db.connection() as conn:
        return conn.execute(query, params)


def _seed(db):
    """Два клиента, у каждого кабинет с расходом 1000 ₽ в день."""
    user = make_user(db)
    connection = make_connection(db, user, "agency")
    accs = []
    for n, name in enumerate(("Ромашка", "Вектор")):
        client = create_client(user, name=f"ООО {name}")
        acc = make_account(db, user, connection, name=f"{name} Директ", login=f"login{n}")
        camp = make_campaign(db, user, acc, 11 + n, "Поиск")
        make_workspace(db, user, 101 + n, name, client_id=client["id"], accounts=[acc], campaigns=[camp])
        for i in range(0, 14):
            stats.upsert_direct_stat(
                user_id=user, day=YESTERDAY - timedelta(days=i), account_id=acc, campaign_id=11 + n,
                impressions=100, clicks=10, cost_kopecks=100_000, conversions=2,
            )
        accs.append(acc)
    return user, accs


def _balance(db, acc, rub):
    _sql(db, "UPDATE direct_accounts SET balance_kopecks = %s, balance_currency = 'RUB' WHERE id = %s", (rub * 100, acc))


def _review(user, day):
    return build_overview(user, today=day, now=_now(day), review=True)


def _snapshots(db, user):
    return _sql(db, "SELECT day, keys FROM problem_snapshots WHERE user_id = %s ORDER BY day", (user,)).fetchall()


def test_first_day_no_badges_then_new_and_resolved(db):
    user, (acc_r, acc_v) = _seed(db)
    _balance(db, acc_r, 3000)  # 3 дня — «внимание»
    first = _review(user, YESTERDAY)
    assert first["review"] is None
    assert all(p["is_new"] is False for p in first["problems"])
    assert [r["day"] for r in _snapshots(db, user)] == [YESTERDAY]
    assert f"balance:a{acc_r}|warning" in _snapshots(db, user)[0]["keys"]

    # Повторное открытие в тот же день — та же одна строка (upsert).
    _review(user, YESTERDAY)
    assert len(_snapshots(db, user)) == 1

    # Сегодня: у Ромашки деньги пополнили, у Вектора — кончаются.
    _balance(db, acc_r, 100000)
    _balance(db, acc_v, 3000)
    today = _review(user, TODAY)
    assert today["review"] == {"since": YESTERDAY.isoformat(), "new": 1, "resolved": 1}
    p = next(p for p in today["problems"] if p["kind"] == "balance")
    assert p["key"] == f"balance:a{acc_v}" and p["is_new"] is True
    assert [r["day"] for r in _snapshots(db, user)] == [YESTERDAY, TODAY]

    # В течение дня сравниваем всё с тем же вчерашним снимком; сегодняшний перезаписывается.
    again = _review(user, TODAY)
    assert again["review"] == today["review"]
    assert len(_snapshots(db, user)) == 2


def test_snoozed_problem_is_not_resolved(db, monkeypatch):
    monkeypatch.setattr("app.overview.today_msk", lambda: TODAY)  # snoozes.create строит сводку на «сегодня»
    user, (acc_r, _acc_v) = _seed(db)
    _balance(db, acc_r, 3000)
    _review(user, YESTERDAY)
    snoozes.create(user, f"balance:a{acc_r}", "days", now=_now(TODAY))
    today = _review(user, TODAY)
    assert today["review"] == {"since": YESTERDAY.isoformat(), "new": 0, "resolved": 0}
    assert not any(p["kind"] == "balance" for p in today["problems"])


def test_background_overview_does_not_touch_snapshot(db):
    user, _ = _seed(db)
    ov = build_overview(user, today=TODAY, now=_now(TODAY))
    assert ov["review"] is None
    assert _snapshots(db, user) == []


def test_older_snapshot_and_cleanup(db):
    user, (acc_r, _acc_v) = _seed(db)
    _sql(db, "INSERT INTO problem_snapshots (user_id, day, keys) VALUES (%s, %s, %s)", (user, TODAY - timedelta(days=40), ["x:1"]))
    _sql(db, "INSERT INTO problem_snapshots (user_id, day, keys) VALUES (%s, %s, %s)", (user, TODAY - timedelta(days=3), ["x:1"]))
    ov = _review(user, TODAY)
    # Вчера не открывали — сравниваем с последним снимком за неделю; всё нынешнее для него новое.
    assert ov["review"] == {"since": (TODAY - timedelta(days=3)).isoformat(), "new": len(ov["problems"]), "resolved": 1}
    assert all(p["is_new"] for p in ov["problems"])
    assert [r["day"] for r in _snapshots(db, user)] == [TODAY - timedelta(days=3), TODAY]


def test_api_overview_writes_snapshot(db, monkeypatch):
    user, _ = _seed(db)
    monkeypatch.setattr("app.overview.today_msk", lambda: TODAY)
    client = TestClient(app)
    res = client.get("/api/web/overview", headers={"Authorization": f"Bearer {make_session(user)}"})
    assert res.status_code == 200
    assert res.json()["review"] is None
    assert [r["day"] for r in _snapshots(db, user)] == [TODAY]


def test_worse_severity_is_new(db):
    """Тот же ключ, но серьёзнее, чем вчера (деньги: «внимание» → «срочно»), — «новое»; мягче — нет."""
    user, (acc_r, _acc_v) = _seed(db)
    _balance(db, acc_r, 3000)  # 3 дня — «внимание»
    _review(user, YESTERDAY)
    _balance(db, acc_r, 500)  # меньше дня — «срочно»
    today = _review(user, TODAY)
    p = next(p for p in today["problems"] if p["kind"] == "balance")
    assert p["severity"] == "critical" and p["is_new"] is True
    assert today["review"] == {"since": YESTERDAY.isoformat(), "new": 1, "resolved": 0}
