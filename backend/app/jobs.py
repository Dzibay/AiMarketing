"""Очередь задач в Postgres: постановка, захват через FOR UPDATE SKIP LOCKED, ретраи с backoff."""

from __future__ import annotations

import json
from datetime import datetime

from psycopg.types.json import Jsonb

from app.db import pool

PRIORITY_USER = 10
PRIORITY_BACKGROUND = 100

ACTIVE_STATUSES = ("queued", "running")

# Ошибки, которые повтор не исправит: нужен пользователь (переподключить, включить, удалить).
NON_RETRYABLE_CODES = frozenset(
    {
        "not-found",
        "disabled",
        "token-expired",
        "unauthorized",
        "direct-denied",
        "date",
        "unknown-kind",
        "payload",
        # ИИ не подключён или ответил не по формату, сайт недоступен, неверные данные задачи.
        "ai-not-configured",
        "ai-auth",
        "ai-url",
        "ai-json",
        "site",
        "empty",
        "limit",
        "phrase",
        "action",
        "undo",
    }
)

BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 30 * 60
STALE_AFTER_SECONDS = 5 * 60


def backoff_seconds(attempt: int) -> int:
    """Пауза перед повтором после попытки №attempt (1, 2, …): 30 с, 60 с, 120 с … до 30 мин."""
    attempt = max(1, int(attempt))
    return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)


def is_retryable(error_code: str) -> bool:
    return error_code not in NON_RETRYABLE_CODES


def _jsonb(value) -> Jsonb:
    # default=str: даты и Decimal из результатов синков.
    return Jsonb(value, dumps=lambda v: json.dumps(v, ensure_ascii=False, default=str))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def public_job(row: dict) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "payload": row.get("payload") or {},
        "attempts": row["attempts"],
        "max_attempts": row["max_attempts"],
        "progress": {
            "done": row["progress_done"],
            "total": row["progress_total"],
            "text": row["progress_text"],
        },
        "result": row.get("result"),
        "error_code": row["error_code"],
        "error": row["error"],
        "run_after": _iso(row.get("run_after")),
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
    }


def enqueue(
    kind: str,
    payload: dict | None = None,
    *,
    user_id: int | None = None,
    priority: int = PRIORITY_BACKGROUND,
    dedupe_key: str | None = None,
    max_attempts: int = 3,
) -> dict:
    """
    Ставит задачу. С dedupe_key при уже активной задаче возвращает её; если ставит
    пользователь, а там фоновая (в очереди или ждёт ретрая) — поднимает её вперёд.
    """
    with pool.connection() as conn:
        for _ in range(3):
            row = conn.execute(
                """
                INSERT INTO jobs (user_id, kind, payload, priority, dedupe_key, max_attempts)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING
                RETURNING *
                """,
                (user_id, kind, _jsonb(payload or {}), priority, dedupe_key, max(1, max_attempts)),
            ).fetchone()
            if row is not None:
                return row
            row = conn.execute(
                """
                UPDATE jobs
                SET priority = LEAST(priority, %s),
                    run_after = CASE WHEN %s < priority THEN LEAST(run_after, NOW())
                                     ELSE run_after END
                WHERE dedupe_key = %s AND status IN ('queued', 'running')
                RETURNING *
                """,
                (priority, priority, dedupe_key),
            ).fetchone()
            if row is not None:
                return row
            # Активная задача успела завершиться между INSERT и UPDATE — пробуем снова.
    raise RuntimeError(f"не удалось поставить задачу {kind} ({dedupe_key})")


def claim(worker_id: str) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                locked_by = %s,
                locked_at = NOW(),
                heartbeat_at = NOW(),
                started_at = COALESCE(started_at, NOW()),
                error_code = '',
                error = ''
            WHERE id = (
                SELECT id FROM jobs
                WHERE status = 'queued' AND run_after <= NOW()
                ORDER BY priority, run_after, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
            """,
            (worker_id,),
        ).fetchone()


def heartbeat(job_id: int, worker_id: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE jobs SET heartbeat_at = NOW() WHERE id = %s AND locked_by = %s AND status = 'running'",
            (job_id, worker_id),
        )


def set_progress(job_id: int, worker_id: str, done: int, total: int, text: str = "") -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET progress_done = %s, progress_total = %s, progress_text = %s, heartbeat_at = NOW()
            WHERE id = %s AND locked_by = %s AND status = 'running'
            """,
            (max(0, int(done)), max(0, int(total)), (text or "")[:200], job_id, worker_id),
        )


def complete(job_id: int, worker_id: str, result: dict | None) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'done', result = %s, finished_at = NOW(), locked_by = '',
                progress_done = GREATEST(progress_done, progress_total)
            WHERE id = %s AND locked_by = %s AND status = 'running'
            """,
            (_jsonb(result), job_id, worker_id),
        )


def fail(job: dict, worker_id: str, *, error_code: str, error: str = "") -> str:
    """Ошибка попытки захваченной задачи. Возвращает новый статус: 'queued' (ретрай) или 'error'."""
    retry = is_retryable(error_code) and job["attempts"] < job["max_attempts"]
    status = "queued" if retry else "error"
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = %s,
                run_after = NOW() + make_interval(secs => %s),
                finished_at = CASE WHEN %s THEN NULL ELSE NOW() END,
                locked_by = '',
                error_code = %s,
                error = %s
            WHERE id = %s AND locked_by = %s AND status = 'running'
            """,
            (
                status,
                backoff_seconds(job["attempts"]) if retry else 0,
                retry,
                error_code[:100],
                (error or "")[:1000],
                job["id"],
                worker_id,
            ),
        )
    return status


def requeue_stale(stale_after_seconds: int = STALE_AFTER_SECONDS) -> int:
    """Задачи, чей воркер умер (нет heartbeat): в очередь снова или в error, если попытки кончились."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            UPDATE jobs
            SET status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'error' END,
                finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE NOW() END,
                run_after = NOW(),
                locked_by = '',
                error_code = 'stale',
                error = 'Воркер перестал отвечать'
            WHERE status = 'running'
              AND heartbeat_at < NOW() - make_interval(secs => %s)
            RETURNING id
            """,
            (stale_after_seconds,),
        ).fetchall()
    return len(rows)


def purge_finished(older_than_days: int = 14) -> int:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            DELETE FROM jobs
            WHERE status IN ('done', 'error')
              AND finished_at < NOW() - make_interval(days => %s)
            RETURNING id
            """,
            (older_than_days,),
        ).fetchall()
    return len(rows)


def claim_schedule(name: str, interval_seconds: int) -> bool:
    """Атомарно отмечает запуск периодической задачи; True — пора запускать (и запуск наш)."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO job_schedules (name, last_run_at) VALUES (%s, NOW())
            ON CONFLICT (name) DO UPDATE SET last_run_at = NOW()
            WHERE job_schedules.last_run_at <= NOW() - make_interval(secs => %s)
            RETURNING name
            """,
            (name, interval_seconds),
        ).fetchone()
    return row is not None


def get_job(user_id: int, job_id: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        ).fetchone()
    return public_job(row) if row else None


def list_jobs(user_id: int, *, active: bool = False, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with pool.connection() as conn:
        if active:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE user_id = %s AND status IN ('queued', 'running')
                ORDER BY id DESC LIMIT %s
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_id = %s ORDER BY id DESC LIMIT %s",
                (user_id, limit),
            ).fetchall()
    return [public_job(row) for row in rows]
