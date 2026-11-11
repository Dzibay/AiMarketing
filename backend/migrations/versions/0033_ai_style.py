"""Как ИИ пишет в проекте: тон, обращение к клиентам и модель по умолчанию.

workspaces.ai_style — {"tone": calm | sell | expert, "address": vy | ty, "model_class": "" | класс модели}.
Тон и обращение уходят в подсказку чата проекта и в генерацию текстов объявлений. model_class — класс,
с которым создаются новые чаты проекта; пусто — класс по умолчанию из админки.

Revision ID: 0033_ai_style
Revises: 0032_payments
Create Date: 2026-11-11
"""

from alembic import op

revision = "0033_ai_style"
down_revision = "0032_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspaces ADD COLUMN ai_style JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS ai_style")
