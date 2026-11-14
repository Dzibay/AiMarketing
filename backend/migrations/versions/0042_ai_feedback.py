"""Оценки ответов ИИ: «палец вверх / вниз», причины и комментарий

Одна оценка на сообщение; удаляется вместе с чатом. model — класс модели, которым дан ответ
(как в ai_messages.model): по нему админка считает долю «вниз».

Revision ID: 0042_ai_feedback
Revises: 0041_ai_message_meta
Create Date: 2026-11-14
"""

from alembic import op

revision = "0042_ai_feedback"
down_revision = "0041_ai_message_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_feedback (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            chat_id     BIGINT NOT NULL REFERENCES ai_chats (id) ON DELETE CASCADE,
            message_id  BIGINT NOT NULL UNIQUE REFERENCES ai_messages (id) ON DELETE CASCADE,
            rating      SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
            reasons     TEXT[] NOT NULL DEFAULT '{}',
            comment     TEXT NOT NULL DEFAULT '',
            model       TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_feedback_created ON ai_feedback (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_feedback")
