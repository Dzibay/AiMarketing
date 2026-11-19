"""Колокольчик о сбое и частичном успехе операций на настоящем Postgres: запись, ссылка в «Историю», «Повторить»."""

import pytest

from app import job_handlers, operation_notify, telegram_notify
from app import notifications as n
from app.job_handlers import run_job
from app.jobs import PRIORITY_BACKGROUND, PRIORITY_USER, claim, enqueue
from app.journal import log_action
from app.yandex import YandexError
from tests.dbutil import make_user

WORKER = "test-host:1:0"


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(telegram_notify, "send_message", lambda chat, text: out.append((chat, text)) or True)
    return out


def _notes(db, user):
    with db.connection() as conn:
        return conn.execute(
            "SELECT * FROM notifications WHERE user_id = %s AND kind = 'operation' ORDER BY id", (user,)
        ).fetchall()


def _partial_budget(user_id, payload, progress):
    ids = payload["campaign_ids"]
    log_action(user_id, "bulk_budget", payload, {"changed": 1}, "partial", targets=[f"campaign:{ids[0]}"])
    return {
        "status": "partial",
        "items": [{"campaign_row_id": ids[0]}],
        "errors": [{"campaign_row_id": i, "error": "direct", "message": "Кампания в архиве"} for i in ids[1:]],
    }


def test_partial_bulk_budget_notifies_with_history_link_and_retries_rest(db, sent, monkeypatch):
    user = make_user(db)
    monkeypatch.setitem(job_handlers.HANDLERS, "bulk_budget", _partial_budget)
    job = enqueue("bulk_budget", {"campaign_ids": [11, 12], "budget_change_pct": 10}, user_id=user,
                  priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "done"

    [note] = _notes(db, user)
    assert note["title"] == "Бюджеты кампаний: применилось к 1 из 2 кампаний"
    assert note["body"] == "Причина: Кампания в архиве."
    with db.connection() as conn:
        log_id = conn.execute("SELECT MAX(id) AS id FROM action_logs WHERE user_id = %s", (user,)).fetchone()["id"]
    assert note["link"] == f"/cabinet/history?log={log_id}"
    assert note["retry"] == {"job_id": job["id"], "label": "Повторить для оставшихся"}
    item = n.list_notifications(user)["items"][0]
    assert item["retry"] == {"label": "Повторить для оставшихся"}

    again = operation_notify.retry(user, note["id"])
    with db.connection() as conn:
        row = conn.execute("SELECT payload, priority FROM jobs WHERE id = %s", (again["id"],)).fetchone()
    # Только упавшая кампания: «+10 %» прошедшей второй раз не прибавляем.
    assert row["payload"] == {"campaign_ids": [12], "budget_change_pct": 10} and row["priority"] == PRIORITY_USER
    [note] = _notes(db, user)
    assert note["retry"] is None and note["read_at"] is not None
    with pytest.raises(YandexError):
        operation_notify.retry(user, note["id"])
    # Чужую запись повторить нельзя.
    with pytest.raises(YandexError):
        operation_notify.retry(make_user(db, "x@example.com"), note["id"])


def test_background_job_failure_is_silent(db, sent, monkeypatch):
    user = make_user(db)

    def boom(user_id, payload, progress):
        raise YandexError("units", "")

    monkeypatch.setitem(job_handlers.HANDLERS, "bulk_budget", boom)
    enqueue("bulk_budget", {"campaign_ids": [1]}, user_id=user, priority=PRIORITY_BACKGROUND, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "error"
    assert _notes(db, user) == []


def test_negative_list_failure_then_success_resolves(db, sent, monkeypatch):
    user = make_user(db)
    with db.connection() as conn:
        list_id = conn.execute(
            "INSERT INTO negative_lists (user_id, name, phrases) VALUES (%s, 'Бесплатно', '[]'::jsonb) RETURNING id",
            (user,),
        ).fetchone()["id"]
    payload = {"list_id": list_id, "campaign_row_ids": [1, 2]}

    def denied(user_id, payload, progress):
        raise YandexError("direct-denied", "Кампания в архиве (ошибка 8300).")

    monkeypatch.setitem(job_handlers.HANDLERS, "apply_negative_list", denied)
    enqueue("apply_negative_list", payload, user_id=user, priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "error"
    [note] = _notes(db, user)
    assert note["title"] == "Список минус-фраз «Бесплатно»: не получилось"
    assert note["body"] == "Причина: Кампания в архиве."
    assert note["retry"]["label"] == "Повторить" and note["read_at"] is None

    monkeypatch.setitem(job_handlers.HANDLERS, "apply_negative_list",
                        lambda u, p, pr: {"linked": 2, "detached": 0, "skipped": [], "errors": [], "status": "ok"})
    enqueue("apply_negative_list", payload, user_id=user, priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "done"
    [note] = _notes(db, user)
    # Прошло целиком — прежняя запись о сбое прочитана, кнопки больше нет.
    assert note["read_at"] is not None and note["retry"] is None


def test_telegram_follows_prefs(db, sent, monkeypatch):
    user = make_user(db)
    with db.connection() as conn:
        conn.execute("UPDATE users SET telegram_chat_id = '42' WHERE id = %s", (user,))

    def crash(user_id, payload, progress):
        raise RuntimeError("KeyError: 'secret'")

    monkeypatch.setitem(job_handlers.HANDLERS, "duplicate_campaign", crash)
    enqueue("duplicate_campaign", {"campaign_row_id": 5}, user_id=user, priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "error"
    [note] = _notes(db, user)
    assert note["title"] == "Копирование кампании: не получилось" and note["retry"] is None
    assert "secret" not in note["body"] and "Сбой на нашей стороне" in note["body"]
    assert len(sent) == 1 and sent[0][0] == "42" and "secret" not in sent[0][1]

    n.set_prefs(user, {"operation": {"telegram": False}})
    enqueue("duplicate_campaign", {"campaign_row_id": 6}, user_id=user, priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "error"
    assert len(_notes(db, user)) == 2 and len(sent) == 1


def test_load_failure_reason_in_words(db, sent, monkeypatch):
    user = make_user(db)

    def crash(user_id, payload, progress):
        raise RuntimeError("boom")

    monkeypatch.setitem(job_handlers.HANDLERS, "load_counter", crash)
    enqueue("load_counter", {"counter_row_id": 3}, user_id=user, priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "error"
    with db.connection() as conn:
        note = conn.execute("SELECT * FROM notifications WHERE user_id = %s AND kind = 'load_error'", (user,)).fetchone()
    assert "Сбой на нашей стороне" in note["body"] and "boom" not in note["body"]
    assert _notes(db, user) == []


def _make_stale(db, kind, user, payload, priority=PRIORITY_USER):
    from app.jobs import requeue_stale

    enqueue(kind, payload, user_id=user, priority=priority, max_attempts=1)
    job = claim(WORKER)
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET heartbeat_at = NOW() - make_interval(mins => 10) WHERE id = %s", (job["id"],))
    assert requeue_stale() == 1
    return job


def test_stale_user_job_notifies_without_unsafe_retry(db, sent):
    user = make_user(db)
    # Воркер умер посреди «+10 %»: часть бюджетов могла измениться — повтор целиком задвоил бы прибавку.
    job = _make_stale(db, "bulk_budget", user, {"workspace_id": None, "campaign_ids": [1, 2], "budget_change_pct": 10})
    [note] = _notes(db, user)
    assert note["title"] == "Бюджеты кампаний: прервалось" and note["severity"] == "error"
    assert "проверьте результат" in note["body"] and note["retry"] is None
    with db.connection() as conn:
        assert conn.execute("SELECT status FROM jobs WHERE id = %s", (job["id"],)).fetchone()["status"] == "error"

    # Точная сумма — повторить можно.
    _make_stale(db, "bulk_budget", user, {"campaign_ids": [3], "weekly_budget_rub": 900})
    notes = _notes(db, user)
    assert len(notes) == 2 and notes[1]["retry"]["label"] == "Повторить"
    again = operation_notify.retry(user, notes[1]["id"])
    with db.connection() as conn:
        row = conn.execute("SELECT payload FROM jobs WHERE id = %s", (again["id"],)).fetchone()
    assert row["payload"] == {"campaign_ids": [3], "weekly_budget_rub": 900}


def test_stale_background_job_is_silent(db, sent):
    user = make_user(db)
    _make_stale(db, "bulk_budget", user, {"campaign_ids": [1]}, priority=PRIORITY_BACKGROUND)
    assert _notes(db, user) == []


def test_success_on_other_campaigns_keeps_failure_notice(db, sent, monkeypatch):
    user = make_user(db)
    monkeypatch.setitem(job_handlers.HANDLERS, "bulk_budget", _partial_budget)
    first = enqueue("bulk_budget", {"campaign_ids": [11, 12], "weekly_budget_rub": 900}, user_id=user,
                    priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "done"
    # Другой набор кампаний прошёл целиком — запись о B и её «Повторить для оставшихся» остаются.
    monkeypatch.setitem(job_handlers.HANDLERS, "bulk_budget",
                        lambda u, p, pr: {"status": "ok", "items": [{"campaign_row_id": 13}], "errors": []})
    enqueue("bulk_budget", {"campaign_ids": [13], "weekly_budget_rub": 900}, user_id=user,
            priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "done"
    [note] = _notes(db, user)
    assert note["read_at"] is None and note["retry"]["job_id"] == first["id"]

    # Другой набор упал частично — отдельная запись, кнопка первой не затёрта.
    monkeypatch.setitem(job_handlers.HANDLERS, "bulk_budget", _partial_budget)
    enqueue("bulk_budget", {"campaign_ids": [14, 15], "weekly_budget_rub": 900}, user_id=user,
            priority=PRIORITY_USER, max_attempts=1)
    assert run_job(claim(WORKER), WORKER) == "done"
    notes = _notes(db, user)
    assert len(notes) == 2 and notes[0]["retry"]["job_id"] == first["id"]
