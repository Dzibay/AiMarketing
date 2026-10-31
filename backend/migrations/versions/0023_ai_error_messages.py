"""Ошибки хода ИИ-чата остаются в ленте сообщений.

Плашка ai_chats.error живёт до следующей отправки; строка role = 'error' в ai_messages — след, по которому
видно, на каком запросе и чем оборвался ход. В запрос к модели такие строки не попадают.

Revision ID: 0023_ai_error_messages
Revises: 0022_context_files
Create Date: 2026-10-31
"""

from alembic import op

revision = "0023_ai_error_messages"
down_revision = "0022_context_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_messages DROP CONSTRAINT ai_messages_role_check")
    op.execute(
        "ALTER TABLE ai_messages ADD CONSTRAINT ai_messages_role_check CHECK (role IN ('user', 'assistant', 'tool', 'error'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_messages WHERE role = 'error'")
    op.execute("ALTER TABLE ai_messages DROP CONSTRAINT ai_messages_role_check")
    op.execute("ALTER TABLE ai_messages ADD CONSTRAINT ai_messages_role_check CHECK (role IN ('user', 'assistant', 'tool'))")
