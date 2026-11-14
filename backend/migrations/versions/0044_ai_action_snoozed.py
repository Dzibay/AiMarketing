"""«Позже» у предложений ИИ: ai_actions.snoozed_at

Маркетолог отложил карточку: она ждёт решения, но счётчик «Ждёт решения» её не считает, а на странице
решений она в отдельной группе. Раньше это помнил только браузер.

Revision ID: 0044_ai_action_snoozed
Revises: 0043_ai_action_detached
Create Date: 2026-11-14
"""

from alembic import op

revision = "0044_ai_action_snoozed"
down_revision = "0043_ai_action_detached"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_actions ADD COLUMN snoozed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_actions DROP COLUMN IF EXISTS snoozed_at")
