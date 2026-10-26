"""Журнал прогонов синка."""

from __future__ import annotations

import json

from app.db import pool


def start_run(*, user_id: int, kind: str, target_id: int | None = None) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO sync_runs (user_id, kind, target_id, status)
            VALUES (%s, %s, %s, 'running')
            RETURNING id
            """,
            (user_id, kind, target_id),
        ).fetchone()
    return int(row["id"])


def finish_run(run_id: int, *, status: str, error: str = "", meta: dict | None = None) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE sync_runs
            SET status = %s, error = %s, meta = %s, finished_at = NOW()
            WHERE id = %s
            """,
            (status, error[:1000], json.dumps(meta or {}, ensure_ascii=False)[:4000], run_id),
        )


def list_sync_runs(user_id: int, limit: int = 40) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, kind, target_id, status, error, meta, started_at, finished_at
            FROM sync_runs
            WHERE user_id = %s
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    result = []
    for r in rows:
        meta = r.get("meta") or ""
        if isinstance(meta, str) and meta:
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {"raw": meta[:200]}
        result.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "target_id": r["target_id"],
                "status": r["status"],
                "error": r.get("error") or "",
                "meta": meta if isinstance(meta, dict) else {},
                "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
                "finished_at": r["finished_at"].isoformat() if r.get("finished_at") else None,
            }
        )
    return result
