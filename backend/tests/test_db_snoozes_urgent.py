"""Скрытие сигналов («⋯») и срочные уведомления на настоящем Postgres: хранение, фильтры сводки,
дайджест, колокольчик, Telegram с тихими часами. Директ, Метрика, ИИ и Telegram не вызываются."""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import digest, notifications, snoozes, stats, urgent
from app.clients import create_client
from app.clock import MSK
from app.main import app
from app.overview import build_overview
from app.web_auth import make_session
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

TODAY = date(2026, 10, 26)
YESTERDAY = TODAY - timedelta(days=1)


def at(hour, minute=0, day=TODAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MSK)


NOON = at(12)


@pytest.fixture
def env(db, monkeypatch):
    """Сегодня по Москве — TODAY; часы колокольчика — env.now; Telegram — список env.sent."""
    state = SimpleNamespace(now=NOON, sent=[])
    monkeypatch.setattr("app.overview.today_msk", lambda: TODAY)
    monkeypatch.setattr("app.notifications.now_msk", lambda: state.now)
    monkeypatch.setattr("app.telegram_notify.send_message", lambda chat_id, text: state.sent.append((chat_id, text)) or True)
    return state


def _sql(db, query, params=()):
    with db.connection() as conn:
        return conn.execute(query, params)


def _stat(user, account, campaign, day, cost_rub, conv):
    stats.upsert_direct_stat(
        user_id=user,
        day=day,
        account_id=account,
        campaign_id=campaign,
        impressions=100,
        clicks=10,
        cost_kopecks=cost_rub * 100,
        conversions=conv,
    )


def _seed(db, *, balance_rub=None, yesterday=(1000, 2), chat_id="42"):
    """Клиент «ООО Ромашка» → проект «Окна» → логин с кампанией «Поиск»: 30 дней по 1000 ₽ и 2 заявки."""
    user = make_user(db)
    connection = make_connection(db, user, "agency")
    client = create_client(user, name="ООО Ромашка")
    acc = make_account(db, user, connection, name="Ромашка Директ", login="romashka")
    search = make_campaign(db, user, acc, 11, "Поиск")
    ws = make_workspace(db, user, 101, "Окна", client_id=client["id"], accounts=[acc], campaigns=[search])
    for i in range(1, 31):
        _stat(user, acc, 11, YESTERDAY - timedelta(days=i), 1000, 2)
    _stat(user, acc, 11, YESTERDAY, *yesterday)
    # День «вчера» перечитан после полуночи — закрыт.
    _sql(db, "UPDATE stats_daily SET updated_at = %s WHERE user_id = %s", (at(6), user))
    if balance_rub is not None:
        _set_balance(db, acc, balance_rub)
    _sql(db, "UPDATE users SET telegram_chat_id = %s WHERE id = %s", (chat_id, user))
    return SimpleNamespace(user=user, connection=connection, client=client["id"], acc=acc, search=search, ws=ws)


def _set_balance(db, acc, rub):
    _sql(db, "UPDATE direct_accounts SET balance_kopecks = %s, balance_currency = 'RUB' WHERE id = %s", (rub * 100, acc))


def _overview(s, now=NOON):
    return build_overview(s.user, today=TODAY, now=now)


def _problem(ov, kind):
    return next((p for p in ov["problems"] if p["kind"] == kind), None)


def _urgent_rows(db, user):
    return _sql(
        db, "SELECT * FROM notifications WHERE user_id = %s AND kind = 'urgent' ORDER BY id", (user,)
    ).fetchall()


# --- Скрытие ----------------------------------------------------------------------------------------


def test_hide_for_seven_days_everywhere_then_returns(env, db):
    s = _seed(db, balance_rub=4000)  # 4 дня при 1000 ₽ в день — «внимание»
    before = _overview(s)
    p = _problem(before, "balance")
    assert p["severity"] == "warning" and p["key"] == f"balance:a{s.acc}"
    assert p["type_scope"] == {"key": f"client:{s.client}", "level": "client", "name": "ООО Ромашка"}
    warnings_before = next(r for r in before["workspaces"] if r["id"] == s.ws)["problems"]["warning"]
    assert "Баланса хватит" in digest.format_digest(before)

    assert snoozes.create(s.user, p["key"], "days", now=NOON) == {"ok": True}
    after = _overview(s, NOON + timedelta(hours=2))
    assert _problem(after, "balance") is None
    row = next(r for r in after["workspaces"] if r["id"] == s.ws)
    assert row["problems"]["warning"] == warnings_before - 1  # счётчики и значки без скрытого
    assert [(h["mode"], h["kind"], h["note"]) for h in after["hidden"]] == [("days", "balance", "Скрыто до 02.11")]
    assert after["hidden"][0]["workspace_ids"] == [s.ws] and after["hidden"][0]["title"] == p["title"]
    assert "Баланса хватит" not in digest.format_digest(after)

    # Через 7 дней скрытие кончается само и строка снимается.
    back = _overview(s, NOON + timedelta(days=7, minutes=1))
    assert _problem(back, "balance") is not None and back["hidden"] == []
    assert snoozes.load(s.user) == []


def test_snooze_errors_and_repeat(env, db):
    s = _seed(db, balance_rub=4000)
    with pytest.raises(snoozes.SnoozeError):
        snoozes.create(s.user, "balance:a999", "days", now=NOON)
    with pytest.raises(snoozes.SnoozeError):
        snoozes.create(s.user, f"balance:a{s.acc}", "forever", now=NOON)
    with pytest.raises(snoozes.SnoozeError):
        snoozes.remove(s.user, 12345)
    snoozes.create(s.user, f"balance:a{s.acc}", "intended", now=NOON)
    rows = snoozes.load(s.user)
    assert [(r["mode"], r["until"]) for r in rows] == [("intended", None)]
    assert rows[0]["snapshot"] == {"severity": "warning", "metric": {"balance": 400000, "days": 4.0}}
    # Повтор из старой вкладки: сигнал уже скрыт — понятная ошибка, строка одна.
    with pytest.raises(snoozes.SnoozeError, match="уже скрыт"):
        snoozes.create(s.user, f"balance:a{s.acc}", "days", now=NOON)
    assert len(snoozes.load(s.user)) == 1


def test_intended_returns_when_worse(env, db):
    s = _seed(db, balance_rub=4000)
    snoozes.create(s.user, f"balance:a{s.acc}", "intended", now=NOON)
    assert _problem(_overview(s), "balance") is None

    _set_balance(db, s.acc, 3500)  # 3,5 дня — почти то же самое: не возвращаем
    assert _problem(_overview(s), "balance") is None

    _set_balance(db, s.acc, 0)  # деньги кончились — стало хуже
    p = _problem(_overview(s), "balance")
    assert p is not None and p["severity"] == "critical"
    assert snoozes.load(s.user) == []


def test_type_snooze_for_client_via_api(env, db):
    s = _seed(db, balance_rub=4000)
    # Второй проект того же клиента и проект без клиента — у каждого свой логин с тем же сигналом.
    acc2 = make_account(db, s.user, s.connection, name="Ромашка-2", login="romashka2")
    acc3 = make_account(db, s.user, s.connection, name="Свой", login="own")
    ws2 = make_workspace(db, s.user, 102, "Двери", client_id=s.client, accounts=[acc2])
    ws3 = make_workspace(db, s.user, 103, "Свой сайт", accounts=[acc3])
    for acc, cid in ((acc2, 21), (acc3, 31)):
        for i in range(1, 8):
            _stat(s.user, acc, cid, YESTERDAY - timedelta(days=i), 1000, 2)
        _set_balance(db, acc, 4000)

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s.user)}"}
    ov = client.get("/api/web/overview", headers=auth).json()
    keys = {p["account_id"]: p["key"] for p in ov["problems"] if p["kind"] == "balance"}
    assert set(keys) == {s.acc, acc2, acc3}

    assert client.post("/api/web/overview/snoozes", headers=auth, json={"key": keys[s.acc], "mode": "type"}).status_code == 200
    ov = client.get("/api/web/overview", headers=auth).json()
    assert [p["account_id"] for p in ov["problems"] if p["kind"] == "balance"] == [acc3]
    hidden = ov["hidden"]
    assert len(hidden) == 1 and hidden[0]["mode"] == "type" and hidden[0]["count"] == 2
    assert hidden[0]["place"] == "Клиент «ООО Ромашка»" and sorted(hidden[0]["workspace_ids"]) == sorted([s.ws, ws2])

    # Сводка проекта показывает только своё скрытое.
    assert len(client.get(f"/api/web/workspaces/{ws2}/summary", headers=auth).json()["hidden"]) == 1
    assert client.get(f"/api/web/workspaces/{ws3}/summary", headers=auth).json()["hidden"] == []

    assert client.post("/api/web/overview/snoozes", headers=auth, json={"key": "kpi:w999", "mode": "days"}).status_code == 400
    assert client.delete(f"/api/web/overview/snoozes/{hidden[0]['id']}", headers=auth).status_code == 200
    assert client.delete(f"/api/web/overview/snoozes/{hidden[0]['id']}", headers=auth).status_code == 404
    ov = client.get("/api/web/overview", headers=auth).json()
    assert len([p for p in ov["problems"] if p["kind"] == "balance"]) == 3 and ov["hidden"] == []


# --- Срочное -----------------------------------------------------------------------------------------


def test_urgent_money_daytime_once_per_day(env, db):
    s = _seed(db, balance_rub=1500)  # 1,5 дня — критично
    out = urgent.run(s.user, now=NOON)
    assert out["sent"] == 1
    rows = _urgent_rows(db, s.user)
    assert len(rows) == 1
    n = rows[0]
    assert n["title"] == "ООО Ромашка · Окна: деньги на исходе"
    assert "хватит примерно на 1,5 дн." in n["body"] and n["link"] == f"/w/{s.ws}/summary"
    assert n["severity"] == "error" and n["in_cabinet"] and n["telegram_pending"] == ""
    assert len(env.sent) == 1 and env.sent[0][0] == "42"
    text = env.sent[0][1].split("\n")
    assert text[0] == "Срочно · ООО Ромашка · Окна" and text[2].startswith("https://") and len(text) == 3

    # Через час — то же событие: ни новой записи, ни второго сообщения.
    env.now = NOON + timedelta(hours=1)
    assert urgent.run(s.user, now=env.now)["sent"] == 0
    assert len(_urgent_rows(db, s.user)) == 1 and len(env.sent) == 1


def test_urgent_quiet_hours_telegram_waits_for_morning(env, db):
    s = _seed(db, balance_rub=1500)
    env.now = at(23, 30)
    assert urgent.run(s.user, now=env.now)["sent"] == 1
    rows = _urgent_rows(db, s.user)
    assert len(rows) == 1 and rows[0]["telegram_pending"].startswith("Срочно · ")
    assert env.sent == []  # ночью в Telegram не пишем

    env.now = at(9, 5, TODAY + timedelta(days=1))
    out = urgent.run(s.user, now=env.now)
    assert out == {"events": 1, "sent": 0, "flushed": 1}  # утром — то самое сообщение, без новой записи
    assert len(env.sent) == 1 and env.sent[0][1].startswith("Срочно · ООО Ромашка · Окна")
    assert _urgent_rows(db, s.user)[0]["telegram_pending"] == ""

    env.now = at(10, 5, TODAY + timedelta(days=1))
    assert urgent.run(s.user, now=env.now)["flushed"] == 0 and len(env.sent) == 1


def test_urgent_quiet_hours_skip_solved_by_morning(env, db):
    s = _seed(db, balance_rub=1500)
    env.now = at(23, 30)
    urgent.run(s.user, now=env.now)
    _set_balance(db, s.acc, 100_000)  # ночью пополнили
    env.now = at(9, 5, TODAY + timedelta(days=1))
    assert urgent.run(s.user, now=env.now) == {"events": 0, "sent": 0, "flushed": 0}
    assert env.sent == [] and _urgent_rows(db, s.user)[0]["telegram_pending"] == ""


def test_urgent_respects_snooze_and_prefs(env, db):
    s = _seed(db, balance_rub=1500)
    snoozes.create(s.user, f"balance:a{s.acc}", "days", now=NOON)
    assert urgent.run(s.user, now=NOON + timedelta(minutes=5))["sent"] == 0
    assert _urgent_rows(db, s.user) == [] and env.sent == []

    snoozes.remove(s.user, snoozes.load(s.user)[0]["id"])
    _sql(db, "UPDATE users SET notify_prefs = %s WHERE id = %s", (json.dumps({"urgent": {"cabinet": False}}), s.user))
    assert urgent.run(s.user, now=NOON + timedelta(minutes=10))["sent"] == 1
    assert [r["in_cabinet"] for r in _urgent_rows(db, s.user)] == [False]  # только Telegram
    assert len(env.sent) == 1


def test_urgent_spend_without_conversions_and_stopped(env, db):
    s = _seed(db, yesterday=(1500, 0))  # обычная заявка 500 ₽, вчера 1500 ₽ без заявок
    net = make_campaign(db, s.user, s.acc, 12, "РСЯ", state="OFF")
    paused = make_campaign(db, s.user, s.acc, 13, "Пауза", state="SUSPENDED")
    _sql(db, "INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id) VALUES (%s, %s, %s), (%s, %s, %s)",
         (s.ws, net, s.user, s.ws, paused, s.user))
    _stat(s.user, s.acc, 12, TODAY - timedelta(days=3), 800, 0)
    _stat(s.user, s.acc, 13, TODAY - timedelta(days=3), 800, 0)

    events = {e["object"]: e for e in urgent.detect(s.user, NOON)}
    assert set(events) == {f"noconv:c{s.search}", f"stopped:c{net}"}  # остановленную вручную не трогаем
    noconv = events[f"noconv:c{s.search}"]
    assert "потратила 1 500 ₽" in noconv["sentence"].replace(" ", " ") and noconv["link"] == f"/w/{s.ws}/campaigns/{s.search}"
    assert events[f"stopped:c{net}"]["headline"] == "кампания встала"

    # «Расход без заявок» скрыли в сводке — срочное о нём тоже молчит.
    p = next(p for p in _overview(s)["problems"] if p["kind"] == "no_conversions")
    assert p["key"] == f"no_conversions:c{s.search}"
    snoozes.create(s.user, p["key"], "days", now=NOON)
    assert set(e["object"] for e in urgent.detect(s.user, NOON + timedelta(minutes=1))) == {f"stopped:c{net}"}


def _moderation_case(db, s, *, ad_statuses, synced, sent_at=NOON - timedelta(hours=3)):
    _sql(db, "UPDATE direct_campaigns SET state = 'SUSPENDED', synced_at = %s WHERE id = %s", (synced, s.search))
    with db.connection() as conn:
        group = conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name)"
            " VALUES (%s, %s, %s, 500, 'Группа') RETURNING id",
            (s.user, s.acc, s.search),
        ).fetchone()["id"]
        for i, (status, reason) in enumerate(ad_statuses):
            conn.execute(
                """
                INSERT INTO direct_ads (user_id, account_id, adgroup_row_id, ad_id, state, status, title,
                                        status_clarification, synced_at)
                VALUES (%s, %s, %s, %s, 'ON', %s, %s, %s, %s)
                """,
                (s.user, s.acc, group, 7000 + i, status, f"Окна {i}", reason, synced),
            )
        return conn.execute(
            """
            INSERT INTO action_logs (user_id, action, payload, result, status, source, title, created_at)
            VALUES (%s, 'moderate', %s, %s, 'ok', 'user', 'На модерацию', %s) RETURNING id
            """,
            (
                s.user,
                json.dumps({"campaign_row_id": s.search}),
                json.dumps({"drafts": len(ad_statuses), "sent": len(ad_statuses), "errors": [], "status": "ok"}),
                sent_at,
            ),
        ).fetchone()["id"]


def test_urgent_moderation_rejected_once(env, db):
    s = _seed(db)
    log_id = _moderation_case(
        db, s, ad_statuses=[("ACCEPTED", ""), ("REJECTED", "Нет лицензии")], synced=NOON - timedelta(hours=1)
    )
    assert urgent.run(s.user, now=NOON)["sent"] == 1
    n = _urgent_rows(db, s.user)[0]
    assert n["dedup_key"].startswith(f"urgent:mod:c{s.search}:{log_id}:")
    assert n["title"] == "ООО Ромашка · Окна: отклонено 1 объявление"
    assert "Нет лицензии" in n["body"] and "исправьте с ИИ" in n["body"]
    assert n["link"] == f"/w/{s.ws}/campaigns/{s.search}"
    env.now = NOON + timedelta(days=1, hours=1)
    assert urgent.run(s.user, now=env.now)["sent"] == 0  # итог одной отправки — один раз


def test_urgent_moderation_accepted_and_pending(env, db):
    s = _seed(db)
    _moderation_case(db, s, ad_statuses=[("ACCEPTED", "")], synced=NOON - timedelta(hours=4))  # до отправки
    assert urgent.detect(s.user, NOON) == []  # данные ещё старые — молчим
    _sql(db, "UPDATE direct_campaigns SET synced_at = %s", (NOON - timedelta(minutes=30),))
    _sql(db, "UPDATE direct_ads SET synced_at = %s", (NOON - timedelta(minutes=30),))
    [event] = urgent.detect(s.user, NOON)
    assert event["headline"] == "модерация пройдена" and "можно включать" in event["sentence"]


def test_signals_skip_what_urgent_already_said(env, db, monkeypatch):
    s = _seed(db, balance_rub=1500)
    other = make_campaign(db, s.user, s.acc, 14, "Другая")
    suggestions = [
        {"kind": "login_decay", "title": "Логин просел", "account_id": s.acc, "campaign_row_ids": []},
        {"kind": "decay", "title": "Затухает кампания", "account_id": None, "campaign_row_ids": [other]},
        {"kind": "idle", "title": "Включены без показов", "account_id": 999, "campaign_row_ids": []},
    ]
    monkeypatch.setattr("app.insights.get_signals", lambda user_id, ws_id, today=None: {"suggestions": suggestions})
    # Деньги на логине кончаются — о логине и его кампаниях уже скажет срочное.
    assert notifications.notify_signals(s.user) == 1
    titles = [r["title"] for r in _sql(db, "SELECT title FROM notifications WHERE kind = 'signal'").fetchall()]
    assert titles == ["Окна: Включены без показов"]

    # Срочное выключено целиком — сигналы приходят как раньше.
    _sql(db, "DELETE FROM notifications")
    _sql(db, "UPDATE users SET notify_prefs = %s WHERE id = %s",
         (json.dumps({"urgent": {"cabinet": False, "telegram": False}}), s.user))
    assert notifications.notify_signals(s.user) == 3


def test_type_snooze_returns_when_money_runs_out(env, db):
    """Скрыли тип «Деньги на счёте» на предупреждении — деньги кончились: сигнал и срочное возвращаются."""
    s = _seed(db, balance_rub=4000)  # 4 дня — «внимание»
    p = _problem(_overview(s), "balance")
    assert p["severity"] == "warning"
    snoozes.create(s.user, p["key"], "type", now=NOON)
    assert snoozes.load(s.user)[0]["snapshot"] == {"severity": "warning", "money_out": False}
    assert _problem(_overview(s), "balance") is None
    assert urgent.detect(s.user, NOON) == []

    _set_balance(db, s.acc, 0)  # деньги кончились — серьёзнее того, что скрывали
    back = _problem(_overview(s), "balance")
    assert back is not None and back["severity"] == "critical"
    assert [e["object"] for e in urgent.detect(s.user, NOON)] == [f"money:a{s.acc}"]
    assert urgent.run(s.user, now=NOON)["sent"] == 1
    assert len(snoozes.load(s.user)) == 1  # скрытие типа остаётся: предупреждения по-прежнему не показываем

    # «Скрыть такой тип» и для критичного — снимок расширяется, теперь скрыто и оно.
    snoozes.create(s.user, back["key"], "type", now=NOON)
    assert snoozes.load(s.user)[0]["snapshot"] == {"severity": "critical", "money_out": True}
    assert _problem(_overview(s), "balance") is None


def test_type_snooze_of_pending_moderation_keeps_rejection(env, db):
    """Скрыли «Кампания на модерации» типом — отказ модерации всё равно виден и приходит срочным."""
    s = _seed(db)
    _sql(db, "UPDATE direct_campaigns SET status = 'MODERATION' WHERE id = %s", (s.search,))
    p = next(p for p in _overview(s)["problems"] if p["kind"] == "moderation")
    assert p["severity"] == "info"
    snoozes.create(s.user, p["key"], "type", now=NOON)
    assert not [p for p in _overview(s)["problems"] if p["kind"] == "moderation"]

    _sql(db, "UPDATE direct_campaigns SET status = 'REJECTED', status_clarification = 'Нет лицензии' WHERE id = %s", (s.search,))
    rejected = [p for p in _overview(s)["problems"] if p["kind"] == "moderation"]
    assert [p["severity"] for p in rejected] == ["critical"]
    assert f"rejected:c{s.search}" in {e["object"] for e in urgent.detect(s.user, NOON)}


def test_moderation_result_only_from_direct_after_send(env, db, monkeypatch):
    """Сервис собрал кампанию (статусы пустые), отправил на модерацию, потом правили бюджет и текст —
    «прошла модерацию» приходит, только когда Директ сам вернул окончательные статусы."""
    from app import actions

    s = _seed(db)
    sent_at = NOON - timedelta(hours=3)
    _sql(db, "UPDATE direct_campaigns SET status = '', state = 'SUSPENDED', synced_at = %s WHERE id = %s",
         (sent_at - timedelta(hours=1), s.search))
    with db.connection() as conn:
        group = conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name)"
            " VALUES (%s, %s, %s, 500, 'Группа') RETURNING id",
            (s.user, s.acc, s.search),
        ).fetchone()["id"]
        rows = [
            conn.execute(
                "INSERT INTO direct_ads (user_id, account_id, adgroup_row_id, ad_id, state, status, title, synced_at)"
                " VALUES (%s, %s, %s, %s, 'ON', '', %s, %s) RETURNING id",
                (s.user, s.acc, group, ad_id, f"Окна {ad_id}", sent_at - timedelta(hours=1)),
            ).fetchone()["id"]
            for ad_id in (7000, 7001)
        ]
    monkeypatch.setattr(actions, "get_connection_access_token", lambda *a, **kw: "token")
    monkeypatch.setattr(actions, "_get_all", lambda *a, **kw: [{"Id": 7000}, {"Id": 7001}])
    monkeypatch.setattr(
        "app.direct_api.call",
        lambda *a, **kw: {"ModerateResults": [{"Id": 7000}, {"Errors": [{"Message": "Нельзя отправить"}]}]},
    )
    assert actions.moderate_campaign(s.user, s.search)["sent"] == 1

    # Отправленное — «на модерации» до чтения из Директа; не ушедшее объявление не трогаем.
    ads = _sql(db, "SELECT id, status FROM direct_ads WHERE adgroup_row_id = %s", (group,)).fetchall()
    statuses = {r["id"]: r["status"] for r in ads}
    assert statuses == {rows[0]: "MODERATION", rows[1]: ""}
    assert _sql(db, "SELECT status FROM direct_campaigns WHERE id = %s", (s.search,)).fetchone()["status"] == "MODERATION"
    log = _sql(db, "SELECT id, targets FROM action_logs WHERE user_id = %s AND action = 'moderate'", (s.user,)).fetchone()
    assert sorted(log["targets"]) == sorted([f"campaign:{s.search}", f"ad:{rows[0]}"])
    _sql(db, "UPDATE action_logs SET created_at = %s WHERE id = %s", (sent_at, log["id"]))

    # Правки из сервиса после отправки двигают synced_at, статусы не меняют — итога нет.
    _sql(db, "UPDATE direct_campaigns SET synced_at = %s WHERE id = %s", (NOON - timedelta(hours=1), s.search))
    _sql(db, "UPDATE direct_ads SET synced_at = %s WHERE adgroup_row_id = %s", (NOON - timedelta(hours=1), group))
    assert urgent.detect(s.user, NOON) == []
    # Директ перечитал кампанию как принятую, а объявление — ещё на модерации: всё ещё ждём.
    _sql(db, "UPDATE direct_campaigns SET status = 'ACCEPTED' WHERE id = %s", (s.search,))
    assert urgent.detect(s.user, NOON) == []
    # Директ вернул итог по отправленному объявлению; второе (не ушедшее, пустой статус) итогу не мешает.
    _sql(db, "UPDATE direct_ads SET status = 'ACCEPTED', synced_at = %s WHERE id = %s", (NOON - timedelta(minutes=30), rows[0]))
    [event] = urgent.detect(s.user, NOON)
    assert event["object"] == f"mod:c{s.search}:{log['id']}" and event["headline"] == "модерация пройдена"


def test_rejected_campaign_reminds_daily_after_moderation_result(env, db):
    """Итог «отклонено» не глушит «кампания отклонена» на неделю: в тот же час и сутки молчит, потом раз в сутки."""
    s = _seed(db)
    log_id = _moderation_case(db, s, ad_statuses=[("REJECTED", "Нет лицензии")], synced=NOON - timedelta(hours=1))
    _sql(db, "UPDATE direct_campaigns SET status = 'REJECTED', status_clarification = 'Нет лицензии' WHERE id = %s",
         (s.search,))
    objects = [e["object"] for e in urgent.detect(s.user, NOON)]
    assert objects == [f"mod:c{s.search}:{log_id}", f"rejected:c{s.search}"]  # итог модерации — первым
    assert urgent.run(s.user, now=NOON)["sent"] == 1
    assert _urgent_rows(db, s.user)[0]["dedup_key"].startswith(f"urgent:mod:c{s.search}:{log_id}:")

    env.now = NOON + timedelta(hours=3)
    assert urgent.run(s.user, now=env.now)["sent"] == 0  # сутки после итога — молчим
    env.now = NOON + timedelta(days=1, minutes=5)
    assert urgent.run(s.user, now=env.now)["sent"] == 1  # дальше — обычное напоминание раз в сутки
    assert _urgent_rows(db, s.user)[-1]["dedup_key"].startswith(f"urgent:rejected:c{s.search}:")


def test_hidden_money_problem_leaves_headline(env, db):
    """«Денег на 1,5 дня» скрыли как «так задумано» — строка «как дела» (главная и проект) о них тоже молчит."""
    from app.analytics import home_headline, workspace_headline

    s = _seed(db, balance_rub=1500)  # 1,5 дня при 1000 ₽ в день — критично
    _sql(db, "UPDATE direct_accounts SET stats_synced_at = now() WHERE id = %s", (s.acc,))
    p = _problem(_overview(s), "balance")
    assert p["severity"] == "critical"
    for h in (home_headline(s.user), workspace_headline(s.user, s.ws)):
        assert "деньги на счёте закончатся" in h["headline"]["text"].lower()

    snoozes.create(s.user, p["key"], "intended", now=NOON)
    for h in (home_headline(s.user), workspace_headline(s.user, s.ws)):
        assert "деньги" not in h["headline"]["text"].lower()
        assert h["headline"]["tone"] != "bad"
