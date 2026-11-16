"""Картинки в чате с ИИ

ai_attachments — картинки, приложенные к сообщению маркетолога (уменьшены в браузере до 2000 px).
purpose: ai — показать ИИ (уходит модели в запросе), ads — для объявлений: проверена по требованиям
Директа и хранится здесь, в библиотеку кабинета загружается только при выполнении одобренного
предложения; direct_hashes — {account_id: хэш}, куда уже загружена (повторно не грузим).
Пока сообщение не отправлено, message_id пуст; такие старше суток удаляются при следующей загрузке.

ai_model_classes.vision — модель класса понимает картинки: сообщение с картинкой уходит такому классу.

Revision ID: 0048_ai_attachments
Revises: 0047_support
Create Date: 2026-11-16
"""

from alembic import op

revision = "0048_ai_attachments"
down_revision = "0047_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_attachments (
            id             BIGSERIAL PRIMARY KEY,
            user_id        BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id   BIGINT REFERENCES workspaces (id) ON DELETE CASCADE,
            chat_id        BIGINT REFERENCES ai_chats (id) ON DELETE CASCADE,
            message_id     BIGINT REFERENCES ai_messages (id) ON DELETE CASCADE,
            purpose        TEXT NOT NULL CHECK (purpose IN ('ai', 'ads')),
            name           TEXT NOT NULL DEFAULT '',
            mime           TEXT NOT NULL,
            width          INTEGER NOT NULL,
            height         INTEGER NOT NULL,
            data           BYTEA NOT NULL,
            direct_hashes  JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_attachments_message ON ai_attachments (message_id)")
    op.execute("CREATE INDEX idx_ai_attachments_user ON ai_attachments (user_id, purpose, created_at)")
    op.execute("ALTER TABLE ai_model_classes ADD COLUMN vision BOOLEAN NOT NULL DEFAULT FALSE")
    # Семейства, которые точно видят картинки; остальное админ отметит сам.
    op.execute(
        r"""
        UPDATE ai_model_classes SET vision = TRUE
        WHERE model ~* '(^|/)(gpt-4o|gpt-4\.1|gpt-[5-9]|o[34]|claude-|gemini|qwen.*vl|pixtral|llama-4)'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_model_classes DROP COLUMN vision")
    op.execute("DROP TABLE ai_attachments")
