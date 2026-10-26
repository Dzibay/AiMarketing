"""Общие списки минус-фраз и их привязка к кампаниям разных клиентов.

Revision ID: 0005_negative_lists
Revises: 0004_overview
Create Date: 2026-10-26
"""

from alembic import op

revision = "0005_negative_lists"
down_revision = "0004_overview"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE negative_lists (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name                        TEXT NOT NULL DEFAULT '',
            phrases                     JSONB NOT NULL DEFAULT '[]'::jsonb,
            -- Кампании, применённые раньше этого момента, ждут обновления.
            phrases_updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_negative_lists_user ON negative_lists (user_id)")

    # added_phrases — фразы, которых в кампании не было до списка: при отвязке
    # или удалении фразы из списка убираем только их, ручные минус-фразы не трогаем.
    op.execute(
        """
        CREATE TABLE negative_list_campaigns (
            list_id                     BIGINT NOT NULL REFERENCES negative_lists (id) ON DELETE CASCADE,
            campaign_row_id             BIGINT NOT NULL REFERENCES direct_campaigns (id) ON DELETE CASCADE,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            added_phrases               JSONB NOT NULL DEFAULT '[]'::jsonb,
            applied_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (list_id, campaign_row_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_negative_list_campaigns_campaign ON negative_list_campaigns (campaign_row_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS negative_list_campaigns")
    op.execute("DROP TABLE IF EXISTS negative_lists")
