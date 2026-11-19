"""«Ждёт решения» по проектам и красные точки срочного (I28) на настоящем Postgres.

Счётчик выбранного проекта совпадает со страницей «Ждёт решения» с тем же фильтром; срочные отметки проектов
берутся из срочных уведомлений за сутки и не считают скрытое после уведомления. Директ, Метрика и ИИ не вызываются.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import ai_agent, notifications, urgent
from app.clients import create_client
from app.main import app
from app.web_auth import make_session
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace


def _sql(db, query, params=()):
    with db.connection() as conn:
        return conn.execute(query, params)


def _chat(db, user, ws):
    return _sql(
        db, "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, %s, 'Чат') RETURNING id", (user, ws)
    ).fetchone()["id"]


def _action(db, user, chat, *, status="pending", snoozed=False, args=None):
    msg = _sql(
        db, "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, 'assistant', '') RETURNING id", (chat,)
    ).fetchone()["id"]
    return _sql(
        db,
        """
        INSERT INTO ai_actions (chat_id, message_id, user_id, tool, args, summary, status, snoozed_at)
        VALUES (%s, %s, %s, 'pause_campaigns', %s, 'Остановить', %s, %s) RETURNING id
        """,
        (chat, msg, user, Jsonb(args or {}), status, datetime.now(timezone.utc) if snoozed else None),
    ).fetchone()["id"]


def _urgent(db, user, ws, obj, headline, *, ago_hours=1.0, where="Окна"):
    created = datetime.now(timezone.utc) - timedelta(hours=ago_hours)
    _sql(
        db,
        """
        INSERT INTO notifications (user_id, workspace_id, kind, severity, title, dedup_key, created_at)
        VALUES (%s, %s, 'urgent', 'error', %s, %s, %s)
        """,
        (user, ws, f"{where}: {headline}", f"urgent:{obj}:2026-11-19", created),
    )
    return created


def _snooze(db, user, mode, kind, *, key="", scope="", created=None, until=None):
    _sql(
        db,
        """
        INSERT INTO problem_snoozes (user_id, mode, problem_key, kind, scope, until, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (user, mode, key, kind, scope, until, created or datetime.now(timezone.utc)),
    )


def _seed(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, name="Общий логин", login="shared")
    a_camp = make_campaign(db, user, acc, 11, "Поиск окна")
    b_camp = make_campaign(db, user, acc, 12, "Поиск двери")
    client = create_client(user, name="ООО Ромашка")
    ws_a = make_workspace(db, user, 101, "Окна", client_id=client["id"], accounts=[acc], campaigns=[a_camp])
    ws_b = make_workspace(db, user, 102, "Двери", accounts=[acc], campaigns=[b_camp])
    ws_c = make_workspace(db, user, 103, "Тихий")
    return user, acc, ws_a, ws_b, ws_c, a_camp, b_camp, client["id"]


def test_pending_counter_per_project_matches_page(db):
    user, _acc, ws_a, ws_b, _ws_c, *_ = _seed(db)
    chat_a, chat_b, general = _chat(db, user, ws_a), _chat(db, user, ws_b), _chat(db, user, None)
    _action(db, user, chat_a)
    _action(db, user, chat_a)
    _action(db, user, chat_a, snoozed=True)  # «Позже» — не в счёт
    _action(db, user, chat_a, status="done")
    _action(db, user, chat_b)
    _action(db, user, general, args={"project_id": ws_a})  # общий чат, но про проект «Окна»
    _action(db, user, general)  # общий вопрос без проекта — только во «всего»

    by = ai_agent.pending_by_project(user)
    assert by == {"count": 5, "projects": {ws_a: 3, ws_b: 1}}
    assert by["count"] == ai_agent.pending_count(user)["count"]
    # Счётчик выбранного проекта = то, что покажет страница с этим проектом.
    assert ai_agent.pending_actions(user, ws_a)["total"] == by["projects"][ws_a]
    assert ai_agent.pending_actions(user, ws_b)["total"] == by["projects"][ws_b]

    out = notifications.badges(user)
    assert out["pending"] == 5
    assert out["projects"] == [
        {"id": ws_a, "pending": 3, "urgent": []},
        {"id": ws_b, "pending": 1, "urgent": []},
    ]


def test_urgent_marks_projects_and_skips_old_passed_and_snoozed(db):
    user, acc, ws_a, ws_b, ws_c, a_camp, b_camp, _client = _seed(db)
    # Деньги на логине двух проектов: уведомление без проекта — точка у обоих.
    _urgent(db, user, None, f"money:a{acc}", "деньги на исходе", ago_hours=2, where="Окна, Двери")
    _sql(db, "UPDATE direct_campaigns SET state = 'OFF' WHERE id = %s", (a_camp,))  # встала и сейчас
    _urgent(db, user, ws_a, f"stopped:c{a_camp}", "кампания встала")
    _urgent(db, user, ws_a, f"mod:c{b_camp}:7", urgent.MODERATION_PASSED)  # хорошая новость — не тревога
    _urgent(db, user, ws_c, "stopped:c999", "кампания встала", ago_hours=30)  # старше суток — уже не горит
    # Расход без заявок в «Дверях» скрыли («⋯» на 7 дней) уже после уведомления.
    sent = _urgent(db, user, ws_b, f"noconv:c{b_camp}", "расход без заявок")
    _snooze(
        db, user, "days", "no_conversions", key=f"no_conversions:c{b_camp}",
        created=sent + timedelta(minutes=5), until=sent + timedelta(days=7),
    )

    signals = urgent.project_signals(user)
    # Новые первыми.
    assert signals == {ws_a: ["кампания встала", "деньги на исходе"], ws_b: ["деньги на исходе"]}
    assert ws_c not in signals

    out = notifications.badges(user)
    assert out["pending"] == 0
    assert [p["id"] for p in out["projects"]] == sorted([ws_a, ws_b])
    assert all(p["pending"] == 0 for p in out["projects"])


def test_snooze_older_than_notification_does_not_hide(db):
    """Скрытие старше уведомления: проверка уже сверилась с ним и всё равно сообщила — стало хуже."""
    user, _acc, _ws_a, ws_b, _ws_c, _a, b_camp, _client = _seed(db)
    now = datetime.now(timezone.utc)
    _snooze(
        db, user, "intended", "no_conversions", key=f"no_conversions:c{b_camp}", created=now - timedelta(days=2)
    )
    _urgent(db, user, ws_b, f"noconv:c{b_camp}", "расход без заявок")
    assert urgent.project_signals(user) == {ws_b: ["расход без заявок"]}

    # Истёкшее «на 7 дней» тоже не прячет.
    _sql(db, "DELETE FROM problem_snoozes WHERE user_id = %s", (user,))
    _snooze(
        db, user, "days", "no_conversions", key=f"no_conversions:c{b_camp}",
        created=now, until=now - timedelta(minutes=1),
    )
    assert urgent.project_signals(user) == {ws_b: ["расход без заявок"]}


def test_type_snooze_for_client_hides_money_dot(db):
    user, acc, ws_a, ws_b, _ws_c, *_rest, client = _seed(db)
    sent = _urgent(db, user, ws_a, f"money:a{acc}", "деньги закончились")
    _urgent(db, user, ws_b, f"cpa:c{_rest[1]}", "заявки подорожали")
    _snooze(db, user, "type", "balance", scope=f"client:{client}", created=sent + timedelta(minutes=1))
    assert urgent.project_signals(user) == {ws_b: ["заявки подорожали"]}


def test_badges_endpoint_returns_projects(db):
    user, _acc, ws_a, *_ = _seed(db)
    _action(db, user, _chat(db, user, ws_a))
    _urgent(db, user, ws_a, "rejected:c5", "кампания отклонена")
    res = TestClient(app).get("/api/web/me/badges", headers={"Authorization": f"Bearer {make_session(user)}"})
    assert res.status_code == 200
    body = res.json()
    assert body["pending"] == 1
    assert body["projects"] == [{"id": ws_a, "pending": 1, "urgent": ["кампания отклонена"]}]


def test_latest_record_per_object_and_current_state(db):
    """Точка гаснет, как только проблема ушла: счёт пополнили, модерация прошла, кампания снова работает."""
    user, acc, ws_a, ws_b, ws_c, a_camp, b_camp, _client = _seed(db)
    today = datetime.now(urgent.MSK).date()
    for d in range(1, 8):  # 1 000 ₽ в день за неделю
        _sql(
            db,
            "INSERT INTO stats_daily (user_id, source, account_id, campaign_id, day, cost_kopecks)"
            " VALUES (%s, 'direct', %s, 11, %s, 100000)",
            (user, acc, today - timedelta(days=d)),
        )
    _sql(db, "UPDATE direct_accounts SET balance_kopecks = 0 WHERE id = %s", (acc,))
    # Сутки назад «на исходе», теперь «закончились» — показываем только последнее.
    _urgent(db, user, None, f"money:a{acc}", "деньги на исходе", ago_hours=24.5, where="Окна, Двери")
    _urgent(db, user, None, f"money:a{acc}", "деньги закончились", ago_hours=0.5, where="Окна, Двери")
    assert urgent.project_signals(user) == {ws_a: ["деньги закончились"], ws_b: ["деньги закончились"]}

    # Пополнили: денег на 10 дней — точки больше нет, хотя запись ещё в окне.
    _sql(db, "UPDATE direct_accounts SET balance_kopecks = 1000000 WHERE id = %s", (acc,))
    assert urgent.project_signals(user) == {}

    # Кампания отклонена, потом модерация пройдена — «кампания отклонена» больше не горит.
    _sql(db, "UPDATE direct_campaigns SET status = 'REJECTED' WHERE id = %s", (a_camp,))
    _urgent(db, user, ws_a, f"rejected:c{a_camp}", "кампания отклонена", ago_hours=3)
    assert urgent.project_signals(user) == {ws_a: ["кампания отклонена"]}
    _sql(db, "UPDATE direct_campaigns SET status = 'ACCEPTED' WHERE id = %s", (a_camp,))
    assert urgent.project_signals(user) == {}  # статус уже не «отклонена»
    _sql(db, "UPDATE direct_campaigns SET status = 'REJECTED' WHERE id = %s", (a_camp,))
    _urgent(db, user, ws_a, f"mod:c{a_camp}:9", urgent.MODERATION_PASSED, ago_hours=1)
    assert urgent.project_signals(user) == {}  # свежий итог модерации сменил старую тревогу

    # Кампания встала, потом снова включилась.
    _urgent(db, user, ws_b, f"stopped:c{b_camp}", "кампания встала")
    assert urgent.project_signals(user) == {}  # в базе она уже ON

    # Повтор, запоздавший на пару часов после суток, — точка не мигает.
    _urgent(db, user, ws_c, "noconv:c999", "расход без заявок", ago_hours=26)
    assert urgent.project_signals(user) == {ws_c: ["расход без заявок"]}


def test_type_snooze_rehide_refreshes_time(db, monkeypatch):
    """Тип уже скрыт, стало хуже, пришло срочное, тип скрыли снова — точка гаснет."""
    from app import overview, snoozes

    user, acc, ws_a, *_rest, client = _seed(db)
    old = datetime.now(timezone.utc) - timedelta(days=3)
    _snooze(db, user, "type", "balance", scope=f"client:{client}", created=old)
    sent = _urgent(db, user, ws_a, f"money:a{acc}", "деньги закончились")
    assert urgent.project_signals(user) == {ws_a: ["деньги закончились"]}

    problem = {
        "key": f"balance:a{acc}", "kind": "balance", "severity": "critical", "title": "Деньги закончились",
        "workspace_id": ws_a, "workspace_ids": [ws_a], "account_id": acc, "metric": {"balance": 0},
        "type_scope": {"key": f"client:{client}", "level": "client", "name": "ООО Ромашка"},
    }
    monkeypatch.setattr(overview, "build_overview", lambda *a, **k: {"problems": [problem]})
    snoozes.create(user, problem["key"], "type", now=sent + timedelta(minutes=5))
    row = _sql(db, "SELECT created_at FROM problem_snoozes WHERE user_id = %s AND mode = 'type'", (user,)).fetchone()
    assert row["created_at"] > sent
    assert urgent.project_signals(user) == {}
