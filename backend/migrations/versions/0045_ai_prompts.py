"""Свои готовые задачи ИИ: «Сохранить как задачу» из своего сообщения в чате

Встроенный набор живёт в коде (app/ai_prompts.py), здесь — только задачи пользователя.

Revision ID: 0045_ai_prompts
Revises: 0044_ai_action_snoozed
Create Date: 2026-11-14
"""

from alembic import op

revision = "0045_ai_prompts"
down_revision = "0044_ai_action_snoozed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_prompts (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            title       TEXT NOT NULL,
            text        TEXT NOT NULL,
            sort        INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_prompts_user ON ai_prompts (user_id, sort, id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_prompts")
