"""Все варианты комбинаторного объявления: заголовки, тексты и картинки.

Revision ID: 0011_ad_variants
Revises: 0010_campaign_goals
Create Date: 2026-10-27
"""

from alembic import op

revision = "0011_ad_variants"
down_revision = "0010_campaign_goals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # В ЕПК объявления комбинаторные (RESPONSIVE_AD): до 7 заголовков, до 3 текстов, до 5 картинок.
    # title/text остаются первым вариантом — для списков и старого кода; image_hashes — AdImageHash.
    op.execute(
        """
        ALTER TABLE direct_ads
            ADD COLUMN titles        JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN texts         JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN image_hashes  JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE direct_ads
            DROP COLUMN image_hashes,
            DROP COLUMN texts,
            DROP COLUMN titles
        """
    )
