"""Служебные данные сообщения чата: ai_messages.meta

Первым ключом пришли подсказки «что дальше» (followups) под ответом ИИ. Сюда же лягут блоки ответа и
прочее, что показывает лента, но не видит модель.

Revision ID: 0041_ai_message_meta
Revises: 0040_ai_cache_price
Create Date: 2026-11-14
"""

from alembic import op

revision = "0041_ai_message_meta"
down_revision = "0040_ai_cache_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_messages ADD COLUMN meta JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_messages DROP COLUMN IF EXISTS meta")
