"""Цвет группы реф-ссылок

Цвет — «#rrggbb» из палитры админки; пустая строка — цвет по номеру группы.

Revision ID: 0058_ref_group_color
Revises: 0057_campaign_link_params
Create Date: 2026-11-16
"""

from alembic import op

revision = "0058_ref_group_color"
down_revision = "0057_campaign_link_params"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ref_link_groups ADD COLUMN color TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE ref_link_groups DROP COLUMN color")
