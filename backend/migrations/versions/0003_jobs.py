"""Очередь фоновых задач и расписание периодических синков.

Revision ID: 0003_jobs
Revises: 0002_clients
Create Date: 2026-10-26
"""

from alembic import op

revision = "0003_jobs"
down_revision = "0002_clients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE jobs (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT REFERENCES users (id) ON DELETE CASCADE,
            kind                        TEXT NOT NULL,
            payload                     JSONB NOT NULL DEFAULT '{}',
            status                      TEXT NOT NULL DEFAULT 'queued'
                                        CHECK (status IN ('queued', 'running', 'done', 'error')),
            -- Меньше — раньше: действия пользователя 10, фоновые синки 100.
            priority                    INT NOT NULL DEFAULT 100,
            attempts                    INT NOT NULL DEFAULT 0,
            max_attempts                INT NOT NULL DEFAULT 3,
            run_after                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            dedupe_key                  TEXT,
            locked_by                   TEXT NOT NULL DEFAULT '',
            locked_at                   TIMESTAMPTZ,
            heartbeat_at                TIMESTAMPTZ,
            progress_done               INT NOT NULL DEFAULT 0,
            progress_total              INT NOT NULL DEFAULT 0,
            progress_text               TEXT NOT NULL DEFAULT '',
            result                      JSONB,
            error_code                  TEXT NOT NULL DEFAULT '',
            error                       TEXT NOT NULL DEFAULT '',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at                  TIMESTAMPTZ,
            finished_at                 TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_jobs_pick ON jobs (priority, run_after, id) WHERE status = 'queued'"
    )
    # Одна активная задача на ключ: повторный клик «Синхронизировать» вернёт уже идущую.
    op.execute(
        "CREATE UNIQUE INDEX idx_jobs_dedupe_active ON jobs (dedupe_key) "
        "WHERE status IN ('queued', 'running')"
    )
    op.execute("CREATE INDEX idx_jobs_user ON jobs (user_id, created_at DESC)")
    op.execute("CREATE INDEX idx_jobs_running ON jobs (heartbeat_at) WHERE status = 'running'")
    op.execute(
        "CREATE INDEX idx_jobs_finished ON jobs (finished_at) WHERE status IN ('done', 'error')"
    )

    op.execute(
        """
        CREATE TABLE job_schedules (
            name                        TEXT PRIMARY KEY,
            last_run_at                 TIMESTAMPTZ NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job_schedules")
    op.execute("DROP TABLE IF EXISTS jobs")
