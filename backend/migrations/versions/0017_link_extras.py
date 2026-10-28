"""Метки групп и адреса быстрых ссылок — для проверки ссылок по шаблону сайта.

NULL — ещё не загружено из Директа.

Revision ID: 0017_link_extras
Revises: 0016_rule_run_error
Create Date: 2026-10-28
"""

from alembic import op

revision = "0017_link_extras"
down_revision = "0016_rule_run_error"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS tracking_params TEXT")
    op.execute("ALTER TABLE direct_ads ADD COLUMN IF NOT EXISTS sitelink_set_id BIGINT")
    op.execute("ALTER TABLE direct_ads ADD COLUMN IF NOT EXISTS sitelink_hrefs JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE direct_ads DROP COLUMN IF EXISTS sitelink_hrefs")
    op.execute("ALTER TABLE direct_ads DROP COLUMN IF EXISTS sitelink_set_id")
    op.execute("ALTER TABLE direct_adgroups DROP COLUMN IF EXISTS tracking_params")
