"""Отложенные предложения ИИ: ai_actions.detached_at

Пользователь написал сообщение, оставив предложения ИИ ждать решения («Оставить и отправить»). Модель уже
получила ответ «пользователь пока не решил», а действие ждёт в карточке. Решение по нему позже попадает
в историю служебной заметкой.

Revision ID: 0043_ai_action_detached
Revises: 0042_ai_feedback
Create Date: 2026-11-14
"""

from alembic import op

revision = "0043_ai_action_detached"
down_revision = "0042_ai_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_actions ADD COLUMN detached_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_actions DROP COLUMN IF EXISTS detached_at")
