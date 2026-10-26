"""Очередь задач на настоящем Postgres."""

from datetime import datetime, timedelta, timezone

from app import job_handlers
from app.job_handlers import run_job, submit
from app.jobs import (
    PRIORITY_BACKGROUND,
    PRIORITY_USER,
    claim,
    claim_schedule,
    complete,
    enqueue,
    fail,
    get_job,
    list_jobs,
    purge_finished,
    requeue_stale,
)
from app.yandex import YandexError
from tests.dbutil import make_user

WORKER = "test-host:1:0"


def _row(pool, job_id: int) -> dict:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def test_active_duplicate_is_not_enqueued(db):
    user = make_user(db)
    first = submit("sync_goals", user, {"counter_row_id": 1})
    again = submit("sync_goals", user, {"counter_row_id": 1})
    other = submit("sync_goals", user, {"counter_row_id": 2})
    assert again["id"] == first["id"]
    assert other["id"] != first["id"]


def test_user_request_bumps_background_duplicate(db):
    user = make_user(db)
    background = submit("stats_day", user, {"day": "2026-01-01"}, priority=PRIORITY_BACKGROUND)
    bumped = submit("stats_day", user, {"day": "2026-01-01"}, priority=PRIORITY_USER)
    assert bumped["id"] == background["id"]
    assert bumped["priority"] == PRIORITY_USER


def test_finished_job_does_not_block_new_one(db):
    user = make_user(db)
    job = submit("sync_goals", user, {"counter_row_id": 1})
    claimed = claim(WORKER)
    complete(claimed["id"], WORKER, {"goals": 3})
    fresh = submit("sync_goals", user, {"counter_row_id": 1})
    assert fresh["id"] != job["id"]
    assert get_job(user, job["id"])["result"] == {"goals": 3}


def test_claim_prefers_user_priority(db):
    user = make_user(db)
    submit("run_rules", user, priority=PRIORITY_BACKGROUND)
    urgent = submit("sync_goals", user, {"counter_row_id": 1}, priority=PRIORITY_USER)
    claimed = claim(WORKER)
    assert claimed["id"] == urgent["id"]
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["locked_by"] == WORKER


def test_retryable_error_backs_off(db):
    user = make_user(db)
    submit("sync_goals", user, {"counter_row_id": 1})
    job = claim(WORKER)
    assert fail(job, WORKER, error_code="metrika", error="503") == "queued"
    row = _row(db, job["id"])
    assert row["error_code"] == "metrika"
    assert row["run_after"] > datetime.now(timezone.utc) + timedelta(seconds=20)
    assert claim(WORKER) is None  # ещё рано


def test_non_retryable_error_is_final(db):
    user = make_user(db)
    submit("sync_goals", user, {"counter_row_id": 1})
    job = claim(WORKER)
    assert fail(job, WORKER, error_code="token-expired") == "error"
    assert _row(db, job["id"])["finished_at"] is not None


def test_attempts_run_out(db):
    user = make_user(db)
    enqueue("sync_goals", {"counter_row_id": 1}, user_id=user, max_attempts=1)
    job = claim(WORKER)
    assert fail(job, WORKER, error_code="metrika") == "error"


def test_stale_running_job_is_requeued(db):
    user = make_user(db)
    submit("sync_goals", user, {"counter_row_id": 1})
    job = claim(WORKER)
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET heartbeat_at = NOW() - make_interval(mins => 10) WHERE id = %s",
            (job["id"],),
        )
    assert requeue_stale() == 1
    row = _row(db, job["id"])
    assert row["status"] == "queued"
    assert row["error_code"] == "stale"
    assert claim(WORKER)["id"] == job["id"]


def test_schedule_claimed_once_per_interval(db):
    assert claim_schedule("t", 3600) is True
    assert claim_schedule("t", 3600) is False
    with db.connection() as conn:
        conn.execute(
            "UPDATE job_schedules SET last_run_at = NOW() - make_interval(hours => 2) WHERE name = %s",
            ("t",),
        )
    assert claim_schedule("t", 3600) is True


def test_purge_removes_only_old_finished(db):
    user = make_user(db)
    submit("sync_goals", user, {"counter_row_id": 1})
    old = claim(WORKER)
    complete(old["id"], WORKER, {})
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET finished_at = NOW() - make_interval(days => 30) WHERE id = %s",
            (old["id"],),
        )
    active = submit("sync_goals", user, {"counter_row_id": 2})
    assert purge_finished(14) == 1
    assert [j["id"] for j in list_jobs(user)] == [active["id"]]


def test_run_job_outcomes(db, monkeypatch):
    user = make_user(db)

    def ok(user_id, payload, progress):
        progress(1, 2, "половина")
        return {"user": user_id, "n": payload["n"]}

    def denied(user_id, payload, progress):
        raise YandexError("token-expired", "Токен истёк")

    def crash(user_id, payload, progress):
        raise RuntimeError("boom")

    monkeypatch.setitem(job_handlers.HANDLERS, "t_ok", ok)
    monkeypatch.setitem(job_handlers.HANDLERS, "t_denied", denied)
    monkeypatch.setitem(job_handlers.HANDLERS, "t_crash", crash)

    ok_job = enqueue("t_ok", {"n": 5}, user_id=user)
    assert run_job(claim(WORKER), WORKER) == "done"
    row = _row(db, ok_job["id"])
    assert row["result"] == {"user": user, "n": 5}
    # complete() добивает прогресс до 100%, подпись последнего шага остаётся.
    assert (row["progress_done"], row["progress_total"], row["progress_text"]) == (2, 2, "половина")

    denied_job = enqueue("t_denied", {}, user_id=user)
    assert run_job(claim(WORKER), WORKER) == "error"
    assert _row(db, denied_job["id"])["error"] == "Токен истёк"

    crash_job = enqueue("t_crash", {}, user_id=user)
    assert run_job(claim(WORKER), WORKER) == "queued"  # internal — ретраится
    assert _row(db, crash_job["id"])["error_code"] == "internal"

    enqueue("t_missing", {}, user_id=user)
    assert run_job(claim(WORKER), WORKER) == "error"


def test_jobs_are_private(db):
    owner = make_user(db, "a@example.com")
    stranger = make_user(db, "b@example.com")
    job = submit("sync_goals", owner, {"counter_row_id": 1})
    assert get_job(stranger, job["id"]) is None
    assert list_jobs(stranger) == []
    assert [j["id"] for j in list_jobs(owner, active=True)] == [job["id"]]
