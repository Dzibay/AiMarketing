"""Срочные сигналы и скрытые проблемы

- problem_snoozes — что пользователь скрыл из «Где горит» и карточек «Сервис заметил»: одну проблему
  на 7 дней, «так задумано» (до ухудшения — снимок в snapshot) или весь тип для клиента/проекта.
- notifications.telegram_pending — текст срочного сообщения, отложенного до 9:00 (тихие часы).

Revision ID: 0061_urgent_and_snoozes
Revises: 0060_ai_rules_client_memory
Create Date: 2026-11-19
"""

from alembic import op

revision = "0061_urgent_and_snoozes"
down_revision = "0060_ai_rules_client_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE problem_snoozes (
            id            BIGSERIAL PRIMARY KEY,
            user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            mode          TEXT NOT NULL CHECK (mode IN ('days', 'intended', 'type')),
            problem_key   TEXT NOT NULL DEFAULT '',
            kind          TEXT NOT NULL DEFAULT '',
            scope         TEXT NOT NULL DEFAULT '',
            title         TEXT NOT NULL DEFAULT '',
            place         TEXT NOT NULL DEFAULT '',
            workspace_ids BIGINT[] NOT NULL DEFAULT '{}',
            snapshot      JSONB NOT NULL DEFAULT '{}'::jsonb,
            until         TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_problem_snoozes_key ON problem_snoozes (user_id, problem_key) WHERE mode <> 'type'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_problem_snoozes_type ON problem_snoozes (user_id, kind, scope) WHERE mode = 'type'"
    )
    op.execute("ALTER TABLE notifications ADD COLUMN telegram_pending TEXT NOT NULL DEFAULT ''")
    op.execute(
        "CREATE INDEX idx_notifications_tg_pending ON notifications (user_id) WHERE telegram_pending <> ''"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notifications_tg_pending")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS telegram_pending")
    op.execute("DROP TABLE IF EXISTS problem_snoozes")
