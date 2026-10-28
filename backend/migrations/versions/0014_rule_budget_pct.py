"""Правила: действие «изменить бюджет на N %» — процент в action_pct.

Revision ID: 0014_rule_budget_pct
Revises: 0013_wordstat_settings
Create Date: 2026-10-28
"""

from alembic import op

revision = "0014_rule_budget_pct"
down_revision = "0013_wordstat_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS action_pct NUMERIC(8, 2)")


def downgrade() -> None:
    op.execute("ALTER TABLE automation_rules DROP COLUMN IF EXISTS action_pct")
