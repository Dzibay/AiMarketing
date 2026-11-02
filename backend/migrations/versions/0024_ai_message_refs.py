"""Ссылки на другие проекты в сообщении маркетолога (@проект в чате ИИ).

refs — [{"id": id пространства, "name": название на момент сообщения}]. Название хранится, чтобы лента не
менялась после переименования и чип проекта показывался даже у архивного пространства.

Revision ID: 0024_ai_message_refs
Revises: 0023_ai_error_messages
Create Date: 2026-11-02
"""

from alembic import op

revision = "0024_ai_message_refs"
down_revision = "0023_ai_error_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_messages ADD COLUMN refs JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_messages DROP COLUMN IF EXISTS refs")
