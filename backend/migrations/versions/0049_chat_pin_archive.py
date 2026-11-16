"""Чаты с ИИ: закрепить и архив

ai_chats.pinned_at — чат закреплён (группа «Закреплённые» над остальными, новые закреплённые выше);
ai_chats.archived_at — чат в архиве: в списке, боковом меню и поиске по умолчанию его нет, открыть можно
из «Архива» внизу списка. Новое сообщение в чате возвращает его из архива.

Revision ID: 0049_chat_pin_archive
Revises: 0048_ai_attachments
Create Date: 2026-11-16
"""

from alembic import op

revision = "0049_chat_pin_archive"
down_revision = "0048_ai_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_chats ADD COLUMN pinned_at TIMESTAMPTZ, ADD COLUMN archived_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_chats DROP COLUMN pinned_at, DROP COLUMN archived_at")
