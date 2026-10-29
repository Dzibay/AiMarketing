"""Правило можно ограничить одной кампанией.

Revision ID: 0019_rule_campaign
Revises: 0018_onboarding
Create Date: 2026-10-29
"""

from alembic import op

revision = "0019_rule_campaign"
down_revision = "0018_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL — все кампании кабинета и пространства. Кампания пропала из Директа — правило удаляется вместе с ней,
    # а не расползается на весь кабинет.
    op.execute(
        "ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS scope_campaign_row_id BIGINT "
        "REFERENCES direct_campaigns (id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE automation_rules DROP COLUMN IF EXISTS scope_campaign_row_id")
