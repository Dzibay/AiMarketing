"""Скрытый пользователем чек-лист первых шагов в обзоре кабинета.

Revision ID: 0018_onboarding
Revises: 0017_link_extras
Create Date: 2026-10-28
"""

from alembic import op

revision = "0018_onboarding"
down_revision = "0017_link_extras"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL — чек-лист показываем, пока остались несделанные шаги.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_dismissed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS onboarding_dismissed_at")
