"""Разбор недели по расписанию: настройка пользователя, запуски по неделям и чаты каждого запуска

ai_schedules — одна строка на пользователя: включено ли, день недели (0 — понедельник) и час по Москве, какие
проекты (scope 'all' — все, 'selected' — workspace_ids), последний и следующий запуск.
ai_review_runs — запуск за неделю (week_start — понедельник): UNIQUE (user_id, week_start) не даёт разобрать
одну неделю дважды. notified_at — сводка в колокольчик ушла.
ai_review_chats — что вышло по каждому проекту запуска: чат разбора или пропуск с причиной.

Revision ID: 0070_ai_review_schedules
Revises: 0069_action_effects
Create Date: 2026-11-19
"""

from alembic import op

revision = "0070_ai_review_schedules"
down_revision = "0069_action_effects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_schedules (
            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            weekday SMALLINT NOT NULL DEFAULT 0 CHECK (weekday BETWEEN 0 AND 6),
            hour SMALLINT NOT NULL DEFAULT 9 CHECK (hour BETWEEN 0 AND 23),
            scope TEXT NOT NULL DEFAULT 'all' CHECK (scope IN ('all', 'selected')),
            workspace_ids BIGINT[] NOT NULL DEFAULT '{}',
            last_run_at TIMESTAMPTZ,
            next_run_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ai_schedules_due ON ai_schedules (next_run_at) WHERE enabled")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_review_runs (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            week_start DATE NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            notified_at TIMESTAMPTZ,
            UNIQUE (user_id, week_start)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ai_review_runs_open ON ai_review_runs (started_at) WHERE notified_at IS NULL")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_review_chats (
            id BIGSERIAL PRIMARY KEY,
            run_id BIGINT NOT NULL REFERENCES ai_review_runs(id) ON DELETE CASCADE,
            workspace_id BIGINT REFERENCES workspaces(id) ON DELETE SET NULL,
            chat_id BIGINT REFERENCES ai_chats(id) ON DELETE SET NULL,
            name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('started', 'skipped')),
            reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ai_review_chats_run ON ai_review_chats (run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ai_review_chats_chat ON ai_review_chats (chat_id) WHERE chat_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_review_chats")
    op.execute("DROP TABLE IF EXISTS ai_review_runs")
    op.execute("DROP TABLE IF EXISTS ai_schedules")
