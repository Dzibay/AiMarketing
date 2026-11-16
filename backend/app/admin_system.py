"""Система: сервисы, очередь задач, расписания, свежие сбои, размер базы и версия.

Состояние сервисов — то же, что полоса на дашборде; «Проверить» — пересчитать сейчас, плюс пинг базы и
Telegram. Расписание просрочено, если с последнего запуска прошло больше двух периодов.
"""

from __future__ import annotations

import os
import time

from app import admin_dashboard as dash
from app.clock import now_msk
from app.db import pool

RECENT = 20
STUCK_MINUTES = 15
SERVICE_LABELS = {
    "ai": "ИИ-модели",
    "wordstat": "Вордстат",
    "images": "Картинки",
    "oauth": "Вход через Яндекс",
    "payments": "ЮKassa",
    "email": "Почта (SMTP)",
    "bridge": "Мост к моделям",
    "queue": "Очередь задач",
    "telegram": "Telegram-бот",
    "database": "База данных",
}


def _iso(v) -> str | None:
    return v.isoformat() if v else None


def _services(conn, now) -> list[dict]:
    from app import telegram_notify as tg

    started = time.monotonic()
    conn.execute("SELECT 1").fetchone()
    ping_ms = round((time.monotonic() - started) * 1000, 1)
    out = []
    for s in dash._status(conn, now)["services"]:
        out.append({**s, "label": SERVICE_LABELS.get(s["key"], s["key"])})
    out.append({"key": "telegram", "label": SERVICE_LABELS["telegram"], "ok": tg.telegram_configured(), "support": tg.support_configured()})
    out.append({"key": "database", "label": SERVICE_LABELS["database"], "ok": True, "ping_ms": ping_ms})
    return out


def build(now=None) -> dict:
    from app.workers import SCHEDULES

    now = now or now_msk()
    with pool.connection() as conn:
        services = _services(conn, now)
        queue = conn.execute(
            """
            SELECT kind,
                   COUNT(*) FILTER (WHERE status = 'queued' AND run_after <= %(now)s) AS waiting,
                   COUNT(*) FILTER (WHERE status = 'queued' AND run_after > %(now)s) AS later,
                   COUNT(*) FILTER (WHERE status = 'running') AS running,
                   COUNT(*) FILTER (WHERE status = 'error' AND finished_at > %(now)s - INTERVAL '1 day') AS failed,
                   COUNT(*) FILTER (WHERE status = 'done' AND finished_at > %(now)s - INTERVAL '1 day') AS done,
                   MIN(run_after) FILTER (WHERE status = 'queued' AND run_after <= %(now)s) AS oldest
            FROM jobs GROUP BY kind
            HAVING COUNT(*) FILTER (WHERE status IN ('queued', 'running')
                                    OR finished_at > %(now)s - INTERVAL '1 day') > 0
            ORDER BY kind
            """,
            {"now": now},
        ).fetchall()
        oldest = conn.execute(
            """
            SELECT j.id, j.kind, j.user_id, u.email, j.run_after, j.attempts, j.error
            FROM jobs j LEFT JOIN users u ON u.id = j.user_id
            WHERE j.status = 'queued' AND j.run_after <= %(now)s ORDER BY j.run_after, j.id LIMIT 1
            """,
            {"now": now},
        ).fetchone()
        last_runs = {r["name"]: r["last_run_at"] for r in conn.execute("SELECT name, last_run_at FROM job_schedules").fetchall()}
        failed_jobs = conn.execute(
            """
            SELECT j.id, j.kind, j.user_id, u.email, j.error_code, j.error, j.attempts, j.finished_at
            FROM jobs j LEFT JOIN users u ON u.id = j.user_id
            WHERE j.status = 'error' ORDER BY j.finished_at DESC NULLS LAST, j.id DESC LIMIT %(n)s
            """,
            {"n": RECENT},
        ).fetchall()
        sync_errors = conn.execute(
            """
            SELECT r.id, r.kind, r.user_id, u.email, r.error, r.started_at, r.finished_at
            FROM sync_runs r LEFT JOIN users u ON u.id = r.user_id
            WHERE r.status = 'error' ORDER BY r.started_at DESC, r.id DESC LIMIT %(n)s
            """,
            {"n": RECENT},
        ).fetchall()
        ai_errors = conn.execute(
            """
            SELECT r.created_at, r.task, r.model, r.code, r.message, r.answered, r.user_id, u.email, r.source
            FROM ai_errors r LEFT JOIN users u ON u.id = r.user_id
            ORDER BY r.created_at DESC, r.id DESC LIMIT %(n)s
            """,
            {"n": RECENT},
        ).fetchall()
        db = conn.execute(
            "SELECT pg_database_size(current_database()) AS size, current_setting('server_version') AS pg"
        ).fetchone()
        tables = conn.execute(
            """
            SELECT c.relname AS name, pg_total_relation_size(c.oid) AS size, c.reltuples::bigint AS rows
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = 'public' ORDER BY size DESC LIMIT 10
            """
        ).fetchall()
        migration = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    schedules = []
    for name, (interval, _fn) in SCHEDULES.items():
        last = last_runs.get(name)
        age = (now - last).total_seconds() if last else None
        schedules.append(
            {
                "name": name,
                "interval_seconds": interval,
                "last_run_at": _iso(last),
                "late": age is None or age > interval * 2,
            }
        )
    return {
        "generated_at": now.isoformat(),
        "services": services,
        "queue": {
            "kinds": [
                {
                    "kind": r["kind"],
                    **{k: int(r[k]) for k in ("waiting", "later", "running", "failed", "done")},
                    "oldest": _iso(r["oldest"]),
                    "lag_minutes": int((now - r["oldest"]).total_seconds() // 60) if r["oldest"] else 0,
                }
                for r in queue
            ],
            "oldest": (
                {
                    "id": oldest["id"],
                    "kind": oldest["kind"],
                    "user_id": oldest["user_id"],
                    "email": oldest["email"] or "",
                    "run_after": _iso(oldest["run_after"]),
                    "lag_minutes": int((now - oldest["run_after"]).total_seconds() // 60),
                    "attempts": int(oldest["attempts"] or 0),
                    "error": (oldest["error"] or "")[:300],
                }
                if oldest
                else None
            ),
            "stuck_minutes": STUCK_MINUTES,
        },
        "schedules": schedules,
        "errors": {
            "jobs": [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "user_id": r["user_id"],
                    "email": r["email"] or "",
                    "code": r["error_code"] or "",
                    "error": (r["error"] or "")[:300],
                    "attempts": int(r["attempts"] or 0),
                    "at": _iso(r["finished_at"]),
                }
                for r in failed_jobs
            ],
            "sync": [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "user_id": r["user_id"],
                    "email": r["email"] or "",
                    "error": (r["error"] or "")[:300],
                    "at": _iso(r["finished_at"] or r["started_at"]),
                }
                for r in sync_errors
            ],
            "ai": [
                {
                    "at": _iso(r["created_at"]),
                    "task": r["task"],
                    "model": r["model"] or "",
                    "code": r["code"] or "",
                    "message": (r["message"] or "")[:300],
                    "answered": bool(r["answered"]),
                    "user_id": r["user_id"],
                    "email": r["email"] or "",
                    "landing": r["source"] == "landing",
                }
                for r in ai_errors
            ],
        },
        "database": {
            "size_bytes": int(db["size"]),
            "postgres": db["pg"],
            "migration": migration["version_num"] if migration else "",
            "tables": [{"name": r["name"], "size_bytes": int(r["size"]), "rows": max(0, int(r["rows"]))} for r in tables],
        },
        "version": os.environ.get("APP_VERSION") or os.environ.get("GIT_SHA") or "",
    }
