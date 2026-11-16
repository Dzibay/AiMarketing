"""Колокольчик: лента уведомлений и настройки «событие × куда»

notifications — записи ленты: что случилось, куда ведёт, прочитано ли. Повторы одного события склеиваются по
dedup_key (in_cabinet = FALSE — событие ушло только в Telegram, строка нужна для склейки повторов).
users.notify_prefs — какие события показывать в кабинете и слать в Telegram (пусто — по умолчанию).

Revision ID: 0046_notifications
Revises: 0045_ai_prompts
Create Date: 2026-11-16
"""

from alembic import op

revision = "0046_notifications"
down_revision = "0045_ai_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notifications (
            id            BIGSERIAL PRIMARY KEY,
            user_id       BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id  BIGINT REFERENCES workspaces (id) ON DELETE CASCADE,
            kind          TEXT NOT NULL,
            severity      TEXT NOT NULL DEFAULT 'info',
            title         TEXT NOT NULL,
            body          TEXT NOT NULL DEFAULT '',
            link          TEXT NOT NULL DEFAULT '',
            dedup_key     TEXT NOT NULL DEFAULT '',
            in_cabinet    BOOLEAN NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            read_at       TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_notifications_user ON notifications (user_id, created_at DESC, id DESC)")
    op.execute("CREATE INDEX idx_notifications_unread ON notifications (user_id) WHERE read_at IS NULL")
    op.execute("CREATE INDEX idx_notifications_dedup ON notifications (user_id, dedup_key, created_at DESC)")
    op.execute("CREATE INDEX idx_notifications_created ON notifications (created_at)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_prefs JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS notify_prefs")
    op.execute("DROP TABLE IF EXISTS notifications")
