"""Стратегия кампании целиком, ключевые цели, история настроек, конверсии по целям, ответы для ИИ.

Revision ID: 0010_campaign_goals
Revises: 0009_account_load
Create Date: 2026-10-27
"""

from alembic import op

revision = "0010_campaign_goals"
down_revision = "0009_account_load"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # bidding_strategy — BiddingStrategy из Директа как есть (Search/Network с параметрами: цена
    # конверсии, оплачиваемая цель, недельный бюджет). priority_goals — ключевые цели
    # [{goal_id, value_rub}], attribution_model — модель атрибуции кампании.
    # source_row_id — из какой кампании сделана копия (копии, созданные сервисом).
    op.execute(
        """
        ALTER TABLE direct_campaigns
            ADD COLUMN bidding_strategy   JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN priority_goals     JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN attribution_model  TEXT NOT NULL DEFAULT '',
            ADD COLUMN source_row_id      BIGINT REFERENCES direct_campaigns (id) ON DELETE SET NULL
        """
    )
    # Что поменялось в кампании между синхронизациями: по этой истории ищем причину затухания.
    op.execute(
        """
        CREATE TABLE campaign_changes (
            id                BIGSERIAL PRIMARY KEY,
            user_id           BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            campaign_row_id   BIGINT NOT NULL REFERENCES direct_campaigns (id) ON DELETE CASCADE,
            field             TEXT NOT NULL,
            old_value         JSONB,
            new_value         JSONB,
            changed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_campaign_changes_campaign ON campaign_changes (campaign_row_id, changed_at DESC)")
    # Конверсии Директа по отдельным целям: source = 'direct_goal', goal_id, conversions, revenue.
    op.execute(
        """
        CREATE UNIQUE INDEX idx_stats_daily_direct_goal_unique
            ON stats_daily (user_id, day, account_id, campaign_id, goal_id)
            WHERE source = 'direct_goal'
        """
    )
    op.execute("CREATE INDEX idx_stats_daily_goal_account_day ON stats_daily (account_id, day) WHERE source = 'direct_goal'")
    # Ответы маркетолога на вопросы ИИ: [{key, question, answer, at}] — ИИ учитывает их и не
    # переспрашивает.
    op.execute("ALTER TABLE workspaces ADD COLUMN ai_facts JSONB NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS ai_facts")
    op.execute("DROP INDEX IF EXISTS idx_stats_daily_goal_account_day")
    op.execute("DROP INDEX IF EXISTS idx_stats_daily_direct_goal_unique")
    op.execute("DELETE FROM stats_daily WHERE source = 'direct_goal'")
    op.execute("DROP TABLE IF EXISTS campaign_changes")
    op.execute(
        """
        ALTER TABLE direct_campaigns
            DROP COLUMN source_row_id,
            DROP COLUMN attribution_model,
            DROP COLUMN priority_goals,
            DROP COLUMN bidding_strategy
        """
    )
