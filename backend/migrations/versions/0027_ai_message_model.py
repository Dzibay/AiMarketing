"""Какая модель написала ответ ИИ в чате: основная и быстрая могут отличаться, и мост меняет провайдера.

Пусто — сообщения до этой миграции и не-ответы (пользователь, инструменты, ошибки).

Revision ID: 0027_ai_message_model
Revises: 0026_ai_bridge_worker
Create Date: 2026-11-04
"""

from alembic import op

revision = "0027_ai_message_model"
down_revision = "0026_ai_bridge_worker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_messages ADD COLUMN model TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_messages DROP COLUMN IF EXISTS model")
