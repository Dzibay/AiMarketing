"""Общий чат ИИ по всем проектам и очередь «Ждёт решения».

ai_chats.workspace_id пусто — общий чат пользователя: модель читает любой его проект и предлагает действия
в нём с project_id, каждое — только после подтверждения. Индекс по ждущим действиям — для счётчика
«Ждёт решения» в шапке, который спрашивается на каждой странице.

Revision ID: 0031_general_chat
Revises: 0030_plans
Create Date: 2026-11-10
"""

from alembic import op

revision = "0031_general_chat"
down_revision = "0030_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_chats ALTER COLUMN workspace_id DROP NOT NULL")
    op.execute(
        "CREATE INDEX idx_ai_chats_general ON ai_chats (user_id, updated_at DESC) WHERE workspace_id IS NULL"
    )
    op.execute("CREATE INDEX idx_ai_actions_pending ON ai_actions (user_id) WHERE status = 'pending'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ai_actions_pending")
    op.execute("DROP INDEX IF EXISTS idx_ai_chats_general")
    op.execute("DELETE FROM ai_chats WHERE workspace_id IS NULL")
    op.execute("ALTER TABLE ai_chats ALTER COLUMN workspace_id SET NOT NULL")
