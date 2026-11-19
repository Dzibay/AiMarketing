"""Причины отказа модерации

StatusClarification объявления и кампании из Директа и отклонённые расширения объявления
(быстрые ссылки, картинка, визитка…) — чтобы показать причину отказа без похода в Директ.

Revision ID: 0059_moderation_reasons
Revises: 0058_ref_group_color
Create Date: 2026-11-19
"""

from alembic import op

revision = "0059_moderation_reasons"
down_revision = "0058_ref_group_color"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE direct_ads ADD COLUMN status_clarification TEXT NOT NULL DEFAULT ''")
    # [{"what": "Быстрые ссылки", "status": "REJECTED", "reason": "…"}] — только отклонённое.
    op.execute("ALTER TABLE direct_ads ADD COLUMN moderation JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE direct_campaigns ADD COLUMN status_clarification TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE direct_campaigns DROP COLUMN status_clarification")
    op.execute("ALTER TABLE direct_ads DROP COLUMN moderation")
    op.execute("ALTER TABLE direct_ads DROP COLUMN status_clarification")
