"""Причина, по которой правило применилось не целиком.

Revision ID: 0016_rule_run_error
Revises: 0015_url_template
Create Date: 2026-10-28
"""

from alembic import op

revision = "0016_rule_run_error"
down_revision = "0015_url_template"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE rule_runs ADD COLUMN IF NOT EXISTS error TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE rule_runs DROP COLUMN IF EXISTS error")
