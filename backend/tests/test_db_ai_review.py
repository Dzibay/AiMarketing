"""Разбор недели по расписанию в БД: настройка, срок запуска, проекты, лимит, одна неделя — один запуск, сводка."""

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import ai_agent, ai_review, workers
from app.clock import MSK, now_msk
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace, set_units_limit

MON = datetime(2026, 11, 16, 9, 5, tzinfo=MSK)  # понедельник, 09:05
WEEK = "2026-11-16"


@pytest.fixture
def cab(db, monkeypatch):
    user = make_user(db)
    conn_id = make_connection(db, user)
    acc = make_account(db, user, conn_id, login="a")
    c1 = make_campaign(db, user, acc, 101)
    c2 = make_campaign(db, user, acc, 102)
    c3 = make_campaign(db, user, acc, 103)
    okna = make_workspace(db, user, 1, name="Окна", accounts=[acc], campaigns=[c1])
    dveri = make_workspace(db, user, 2, name="Двери", accounts=[acc], campaigns=[c2])
    pol = make_workspace(db, user, 3, name="Пол", accounts=[acc], campaigns=[c3])
    empty = make_workspace(db, user, 4, name="Пусто")
    started: list[tuple[int, str]] = []

    def fake_review(user_id, workspace_id, *, prompt="", title="", chat_id=None):
        with db.connection() as conn:
            chat = conn.execute(
                "INSERT INTO ai_chats (user_id, workspace_id, title, status) VALUES (%s, %s, %s, 'running') RETURNING id",
                (user_id, workspace_id, title),
            ).fetchone()["id"]
        started.append((workspace_id, title))
        return {"chat": {"id": chat}}

    monkeypatch.setattr(ai_agent, "start_review", fake_review)
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.notifications.notify", lambda user_id, kind, title, **kw: sent.append({"kind": kind, "title": title, **kw})
    )
    monkeypatch.setattr("app.settings_store.public_base_url", lambda: "https://tolk.test")
    return {"user": user, "okna": okna, "dveri": dveri, "pol": pol, "empty": empty, "started": started, "sent": sent}


def _enable(user, **kw):
    out = ai_review.save_schedule(user, enabled=True, weekday=0, hour=9, **kw)
    return out


def _due_now(db, user, at=MON):
    with db.connection() as conn:
        conn.execute("UPDATE ai_schedules SET next_run_at = %s WHERE user_id = %s", (at.replace(minute=0), user))


def _finish_chats(db, user, pending: dict[int, int]):
    """Чаты ответили; pending — {workspace_id: сколько карточек ждёт решения}."""
    with db.connection() as conn:
        chats = conn.execute("SELECT id, workspace_id FROM ai_chats WHERE user_id = %s", (user,)).fetchall()
        for ch in chats:
            conn.execute("UPDATE ai_chats SET status = 'idle' WHERE id = %s", (ch["id"],))
            msg = conn.execute(
                "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, 'assistant', 'ok') RETURNING id", (ch["id"],)
            ).fetchone()["id"]
            for _ in range(pending.get(ch["workspace_id"], 0)):
                conn.execute(
                    "INSERT INTO ai_actions (chat_id, message_id, user_id, status) VALUES (%s, %s, %s, 'pending')",
                    (ch["id"], msg, user),
                )


def test_schedule_off_by_default_and_saved(db, cab):
    user = cab["user"]
    s = ai_review.get_schedule(user)
    assert not s["enabled"] and s["scope"] == "all" and s["next_run_at"] is None
    assert {p["name"] for p in s["projects"]} == {"Окна", "Двери", "Пол", "Пусто"}
    assert s["unlimited"] and s["per_project_percent"] is None

    s = _enable(user)
    nxt = datetime.fromisoformat(s["next_run_at"])
    assert nxt > now_msk() and nxt.astimezone(MSK).weekday() == 0 and nxt.astimezone(MSK).hour == 9

    with pytest.raises(YandexError):
        ai_review.save_schedule(user, enabled=True, scope="selected", workspace_ids=[])
    with pytest.raises(YandexError):
        ai_review.save_schedule(user, enabled=True, hour=24)
    stranger = make_user(db, "x@example.com")
    stranger_ws = make_workspace(db, stranger, 9, name="Чужой")
    s = ai_review.save_schedule(user, enabled=True, scope="selected", workspace_ids=[cab["okna"], stranger_ws])
    assert s["workspace_ids"] == [cab["okna"]]  # чужой проект отброшен

    off = ai_review.save_schedule(user, enabled=False)
    assert not off["enabled"] and off["next_run_at"] is None and off["scope"] == "selected"


def test_run_starts_reviews_once_per_week_and_sends_summary(db, cab):
    user = cab["user"]
    _enable(user)
    _due_now(db, user)
    assert ai_review.due(MON) == [(user, WEEK)]

    out = ai_review.run_scheduled(user, WEEK, MON)
    assert out["started"] == 3 and out["skipped"] == 1  # «Пусто» — без кампаний
    assert {t for _, t in cab["started"]} == {"Разбор недели · 16 ноября"}
    assert "summary" not in out  # чаты ещё отвечают
    assert cab["sent"] == []
    # Следующий запуск — через неделю; повтор той же недели ничего не запускает.
    assert ai_review.due(MON) == []
    with db.connection() as conn:
        row = conn.execute("SELECT next_run_at, last_run_at FROM ai_schedules WHERE user_id = %s", (user,)).fetchone()
    assert row["next_run_at"] == datetime(2026, 11, 23, 9, tzinfo=MSK) and row["last_run_at"] is not None
    _due_now(db, user)  # даже если кто-то вернул срок назад
    again = ai_review.run_scheduled(user, WEEK, MON)
    assert "started" not in again and len(cab["started"]) == 3
    assert ai_review.open_runs() == [(user, WEEK)]

    chat_id = next(iter(_chat_ids(db, user)))
    assert ai_review.pending_summary_chat(chat_id)

    _finish_chats(db, user, {cab["okna"]: 2, cab["dveri"]: 1})
    done = ai_review.run_scheduled(user, WEEK, MON + timedelta(minutes=10))
    assert done["summary"] == {"found": 2, "sent": True}
    [note] = cab["sent"]
    assert note["kind"] == "ai_review"
    assert note["title"] == "Разбор недели: у 2 клиентов нашлось что поправить"
    assert "Окна (2 предложения)" in note["body"] and "Двери (1 предложение)" in note["body"]
    assert "Без замечаний: Пол" in note["body"] and "В проекте нет кампаний: Пусто" in note["body"]
    assert note["link"] == "/cabinet/pending"
    assert f"https://tolk.test/w/{cab['okna']}/chat/" in note["telegram_text"]
    assert ai_review.open_runs() == []
    assert not ai_review.pending_summary_chat(chat_id)
    # Повтор — без второй сводки.
    ai_review.run_scheduled(user, WEEK, MON + timedelta(minutes=20))
    assert len(cab["sent"]) == 1


def _chat_ids(db, user):
    with db.connection() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM ai_chats WHERE user_id = %s ORDER BY id", (user,))]


def test_selected_projects_only(db, cab):
    user = cab["user"]
    _enable(user, scope="selected", workspace_ids=[cab["dveri"]])
    _due_now(db, user)
    out = ai_review.run_scheduled(user, WEEK, MON)
    assert out == {"week": WEEK, "started": 1, "skipped": 0}
    assert [w for w, _ in cab["started"]] == [cab["dveri"]]


def test_limit_stops_before_exceeding(db, cab):
    user = cab["user"]
    set_units_limit(user, 100)
    # Прошлый разбор стоил 30 единиц, уже потрачено 40: остаток 60 — хватит на два проекта из трёх.
    with db.connection() as conn:
        old = conn.execute(
            "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, %s, 'Разбор 01.11') RETURNING id",
            (user, cab["okna"]),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO ai_usage (user_id, kind, task, model, model_class, charged, chat_id)
            VALUES (%s, 'model', 'chat', 'm', 'standard', 30, %s), (%s, 'model', 'chat', 'm', 'standard', 10, NULL)
            """,
            (user, old, user),
        )
    s = ai_review.get_schedule(user)
    assert not s["unlimited"] and s["per_project_percent"] == 30.0
    assert "units" not in str(s) and "cost" not in str(s)

    _enable(user)
    _due_now(db, user)
    out = ai_review.run_scheduled(user, WEEK, MON)
    assert out["started"] == 2 and out["skipped"] == 2
    with db.connection() as conn:
        reasons = {
            r["name"]: r["reason"]
            for r in conn.execute("SELECT name, reason FROM ai_review_chats WHERE status = 'skipped'").fetchall()
        }
    assert reasons["Пусто"] == "В проекте нет кампаний"
    assert list(reasons.values()).count(ai_review.LIMIT_REASON) == 1

    _finish_chats(db, user, {})
    ai_review.run_scheduled(user, WEEK, MON + timedelta(minutes=10))
    [note] = cab["sent"]
    assert note["title"] == "Разбор недели: замечаний нет"
    assert "Не хватило лимита ИИ:" in note["body"]


def test_stop_code_skips_the_rest(db, cab, monkeypatch):
    user = cab["user"]
    calls = []

    def no_ai(user_id, workspace_id, **kw):
        calls.append(workspace_id)
        raise YandexError("ai-not-configured", "ИИ не подключён")

    monkeypatch.setattr(ai_agent, "start_review", no_ai)
    _enable(user)
    _due_now(db, user)
    out = ai_review.run_scheduled(user, WEEK, MON)
    assert out["started"] == 0 and len(calls) == 1  # после первой такой ошибки дальше не пробуем
    # Ждать нечего — сводка сразу.
    assert out["summary"] == {"found": 0, "sent": True}
    assert cab["sent"][0]["title"] == "Разбор недели не прошёл"


def test_stale_schedule_moves_to_next_week_and_summary_timeout(db, cab):
    user = cab["user"]
    _enable(user)
    _due_now(db, user, MON - timedelta(days=2))
    assert ai_review.due(MON) == []  # пропущено больше чем на сутки — не догоняем
    with db.connection() as conn:
        nxt = conn.execute("SELECT next_run_at FROM ai_schedules WHERE user_id = %s", (user,)).fetchone()["next_run_at"]
    assert nxt == datetime(2026, 11, 23, 9, tzinfo=MSK)

    # Неделя не та, что в расписании, — запуск не занимается.
    _due_now(db, user)
    assert ai_review.run_scheduled(user, "2026-11-09", MON) == {"week": "2026-11-09"}

    ai_review.run_scheduled(user, WEEK, MON)
    # Чат завис: через SUMMARY_WAIT сводка уходит с тем, что есть.
    assert "summary" not in ai_review.run_scheduled(user, WEEK, MON)
    with db.connection() as conn:
        conn.execute("UPDATE ai_review_runs SET started_at = NOW() - INTERVAL '4 hours'")
    late = ai_review.run_scheduled(user, WEEK, now_msk())
    assert late["summary"]["sent"]


def test_enqueue_only_active_users(db, cab, monkeypatch):
    user = cab["user"]
    _enable(user)
    with db.connection() as conn:
        conn.execute("UPDATE ai_schedules SET next_run_at = NOW() - INTERVAL '1 hour' WHERE user_id = %s", (user,))
    submitted = []
    monkeypatch.setattr(workers, "submit", lambda kind, uid, payload=None, **kw: submitted.append((kind, uid, payload)))
    monkeypatch.setattr(workers, "active_user_ids", lambda: set())
    workers._enqueue_ai_reviews()
    assert submitted == []
    monkeypatch.setattr(workers, "active_user_ids", lambda: {user})
    workers._enqueue_ai_reviews()
    assert submitted == [("ai_scheduled_review", user, {"week": ai_review.week_of(now_msk() - timedelta(hours=1)).isoformat()})]


def test_job_handler_and_answer_notice_suppressed(db, cab, monkeypatch):
    from app import job_handlers

    user = cab["user"]
    _enable(user)
    _due_now(db, user)
    ai_review.run_scheduled(user, WEEK, MON)
    chat = _chat_ids(db, user)[0]
    told = []
    monkeypatch.setattr(ai_agent, "run_chat", lambda uid, cid, progress: {"status": "done"})
    monkeypatch.setattr(job_handlers, "_notify_answer", lambda uid, cid, result: told.append(cid))
    job_handlers.HANDLERS["ai_chat"](user, {"chat_id": chat}, lambda *a: None)
    assert told == []  # сводка расскажет обо всех сразу
    assert job_handlers.HANDLERS["ai_scheduled_review"](user, {"week": WEEK}, lambda *a: None)["week"] == WEEK


def test_web_api(db, cab):
    api = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(cab['user'])}"}
    got = api.get("/api/web/ai-schedule", headers=auth)
    assert got.status_code == 200 and got.json()["enabled"] is False
    put = api.put("/api/web/ai-schedule", headers=auth, json={"enabled": True, "weekday": 4, "hour": 18})
    assert put.status_code == 200
    body = put.json()
    assert body["enabled"] and body["weekday"] == 4 and body["hour"] == 18 and body["next_run_at"]
    bad = api.put("/api/web/ai-schedule", headers=auth, json={"enabled": True, "scope": "selected", "workspace_ids": []})
    assert bad.status_code == 400 and bad.json()["detail"]["message"] == "Отметьте хотя бы один проект"
    assert api.get("/api/web/ai-schedule").status_code == 401
    assert date.fromisoformat(WEEK).weekday() == 0


def test_review_chat_never_runs_actions_even_with_full_access(db, monkeypatch):
    """Разбор по расписанию идёт без человека: при полном доступе ИИ действие всё равно ждёт решения."""
    from app import actions, workspaces
    from app.config import settings
    from tests.test_db_ai import _call, _seed, _start, _text

    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")
    s = _seed(db)
    done = []
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: done.append(kw) or {"ok": 1})
    workspaces.update_workspace(s["user"], s["ws"], ai_access={"full_access": True})
    with db.connection() as conn:
        run = conn.execute(
            "INSERT INTO ai_review_runs (user_id, week_start) VALUES (%s, %s) RETURNING id", (s["user"], WEEK)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO ai_review_chats (run_id, workspace_id, chat_id, name, status) VALUES (%s, %s, %s, 'Магазин', 'started')",
            (run, s["ws"], s["chat"]),
        )
    pause = _call("campaigns_action", {"campaign_row_ids": [s["search"]], "action": "pause"}, "p")
    _fake, out = _start(s["user"], s["chat"], ai_review.REVIEW_PROMPT, monkeypatch, pause, _text("Предлагаю."))
    assert done == []
    acts = ai_agent.get_chat(s["user"], s["chat"])["actions"]
    assert [(a["status"], a["auto"]) for a in acts] == [("pending", False)]


def test_summary_reports_failed_and_waits_for_queued_chats(db, cab):
    user = cab["user"]
    _enable(user)
    _due_now(db, user)
    ai_review.run_scheduled(user, WEEK, MON)
    okna, dveri, pol = (cab["okna"], cab["dveri"], cab["pol"])
    with db.connection() as conn:
        chats = {
            r["workspace_id"]: r["id"]
            for r in conn.execute("SELECT id, workspace_id FROM ai_chats WHERE user_id = %s", (user,))
        }
        # Окна ответили, Двери — ошибка, у Пола задача ещё в очереди, а чат «устарел» по updated_at.
        conn.execute("UPDATE ai_chats SET status = 'idle' WHERE id = %s", (chats[okna],))
        conn.execute("UPDATE ai_chats SET status = 'error' WHERE id = %s", (chats[dveri],))
        conn.execute(
            "UPDATE ai_chats SET updated_at = NOW() - INTERVAL '30 minutes' WHERE id = ANY(%s)", (list(chats.values()),)
        )
        job = conn.execute(
            "INSERT INTO jobs (user_id, kind, payload, status) VALUES (%s, 'ai_chat', %s, 'queued') RETURNING id",
            (user, json.dumps({"chat_id": chats[pol]})),
        ).fetchone()["id"]
    later = now_msk()
    assert "summary" not in ai_review.run_scheduled(user, WEEK, later)  # Пол ещё ждёт в очереди
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET status = 'error' WHERE id = %s", (job,))
    out = ai_review.run_scheduled(user, WEEK, later)
    assert out["summary"]["sent"]
    [note] = cab["sent"]
    assert note["title"] == "Разбор недели: замечаний нет, но не все проекты проверены"
    assert "Проверили: Окна" in note["body"]
    # Ошибка и прерванный ответ (задачи нет, чат давно не обновлялся) — не «без замечаний».
    assert "Разбор не прошёл: Двери, Пол" in note["body"]


def test_save_keeps_due_run_and_skips_done_week(db, cab):
    user = cab["user"]
    _enable(user)
    due_at = now_msk() - timedelta(minutes=5)
    with db.connection() as conn:
        conn.execute("UPDATE ai_schedules SET next_run_at = %s WHERE user_id = %s", (due_at, user))
    # Отметили ещё проект до того, как планировщик занял неделю, — срок не уезжает на неделю.
    s = ai_review.save_schedule(user, enabled=True, scope="selected", workspace_ids=[cab["okna"], cab["dveri"]])
    assert datetime.fromisoformat(s["next_run_at"]) == due_at
    # Неделя уже разобрана — новый день показывается уже на следующей неделе.
    this_week = ai_review.week_of(now_msk())
    with db.connection() as conn:
        conn.execute("INSERT INTO ai_review_runs (user_id, week_start) VALUES (%s, %s)", (user, this_week))
    for weekday in range(7):
        s = ai_review.save_schedule(user, enabled=True, weekday=weekday, hour=23)
        assert ai_review.week_of(datetime.fromisoformat(s["next_run_at"])) > this_week
