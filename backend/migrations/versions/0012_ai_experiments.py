"""Эксперименты ИИ: гипотеза, тестовые кампании, лимит ₽/нед., срок и критерий успеха.

Revision ID: 0012_ai_experiments
Revises: 0011_ad_variants
Create Date: 2026-10-28
"""

from alembic import op

revision = "0012_ai_experiments"
down_revision = "0011_ad_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Деньги ограничивает только недельный бюджет кампаний в Директе — сервис ничего сам не останавливает.
    # Прогресс считает код по stats_daily, итоги разбирает ИИ по кнопке.
    op.execute(
        """
        CREATE TABLE ai_experiments (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id                BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            -- Чат и карточка, из которых запущен эксперимент; чат можно удалить — эксперимент останется.
            chat_id                     BIGINT REFERENCES ai_chats (id) ON DELETE SET NULL,
            action_id                   BIGINT REFERENCES ai_actions (id) ON DELETE SET NULL,
            title                       TEXT NOT NULL DEFAULT '',
            hypothesis                  TEXT NOT NULL DEFAULT '',
            -- restart | geo | keywords | type | strategy | other
            kind                        TEXT NOT NULL DEFAULT 'other',
            -- Кампания-образец для сравнения (direct_campaigns.id) и тестовые кампании.
            source_campaign_row_id      BIGINT,
            campaign_row_ids            JSONB NOT NULL DEFAULT '[]'::jsonb,
            -- Лимит на кампанию в неделю; NULL — не задан.
            weekly_limit_kopecks        BIGINT,
            started_on                  DATE NOT NULL,
            days                        INTEGER NOT NULL,
            -- zero_cost | conversions | cr | custom
            criterion                   TEXT NOT NULL,
            target                      DOUBLE PRECISION,
            criterion_text              TEXT NOT NULL DEFAULT '',
            -- running | finished | cancelled
            status                      TEXT NOT NULL DEFAULT 'running',
            -- success | fail | unclear
            verdict                     TEXT NOT NULL DEFAULT '',
            conclusion                  TEXT NOT NULL DEFAULT '',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at                 TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_experiments_workspace ON ai_experiments (workspace_id, id DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_experiments")
