"""Начало выполнения ИИ-действия: ai_actions.started_at

Одобренное действие помечается перед вызовом Директа. Если процесс упал посреди выполнения, действие
навсегда оставалось «approved», и следующий ход чата выполнял его заново — например, ещё раз копировал
кампанию. Теперь одобренное, которое давно начато (или давно одобрено и так и не начато), ход закрывает
ошибкой и просит модель проверить Директ, прежде чем повторять.

Revision ID: 0050_ai_action_started
Revises: 0049_chat_pin_archive
Create Date: 2026-11-16
"""

from alembic import op

revision = "0050_ai_action_started"
down_revision = "0049_chat_pin_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_actions ADD COLUMN started_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_actions DROP COLUMN IF EXISTS started_at")
