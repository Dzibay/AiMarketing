"""Поддержка: обращения пользователя и переписка с ними

support_threads — одно обращение на пользователя (вся переписка с поддержкой); tg_topic_id — тема в
Telegram-группе поддержки, куда уходят сообщения и откуда приходят ответы. user_read_at / admin_read_at —
до какого момента переписку прочитал пользователь и админ (счётчики непрочитанного).
support_messages — сообщения: author user | admin, текст, скриншот (уменьшен в браузере), с какой страницы,
проекта, тарифа и браузера написано (context), номер сообщения в Telegram (склейка повторов webhook/опроса),
emailed_at — ответ ушёл письмом, потому что его долго не читали, а Telegram не привязан.

Revision ID: 0047_support
Revises: 0046_notifications
Create Date: 2026-11-16
"""

from alembic import op

revision = "0047_support"
down_revision = "0046_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE support_threads (
            id             BIGSERIAL PRIMARY KEY,
            user_id        BIGINT NOT NULL UNIQUE REFERENCES users (id) ON DELETE CASCADE,
            tg_topic_id    BIGINT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_read_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            admin_read_at  TIMESTAMPTZ NOT NULL DEFAULT 'epoch'
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX idx_support_threads_topic ON support_threads (tg_topic_id) WHERE tg_topic_id IS NOT NULL")
    op.execute(
        """
        CREATE TABLE support_messages (
            id             BIGSERIAL PRIMARY KEY,
            thread_id      BIGINT NOT NULL REFERENCES support_threads (id) ON DELETE CASCADE,
            author         TEXT NOT NULL CHECK (author IN ('user', 'admin')),
            body           TEXT NOT NULL DEFAULT '',
            image          BYTEA,
            image_type     TEXT NOT NULL DEFAULT '',
            context        JSONB NOT NULL DEFAULT '{}'::jsonb,
            tg_message_id  BIGINT,
            emailed_at     TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_support_messages_thread ON support_messages (thread_id, created_at, id)")
    op.execute(
        "CREATE UNIQUE INDEX idx_support_messages_tg ON support_messages (thread_id, tg_message_id) "
        "WHERE tg_message_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS support_messages")
    op.execute("DROP TABLE IF EXISTS support_threads")
