"""Модель на задачу (быстрая / умная), цены, месячный лимит и учёт расхода ИИ.

Revision ID: 0008_ai_usage
Revises: 0007_query_cleaning
Create Date: 2026-10-26
"""

from alembic import op

revision = "0008_ai_usage"
down_revision = "0007_query_cleaning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # model — умная модель (чат), model_fast — быстрая для массовых задач (разметка, бриф);
    # пусто — та же, что умная. Цены — рубли за 1 млн токенов, у быстрой пусто — как у умной.
    # Лимит — рубли в календарный месяц по Москве, считается по ценам выше.
    op.execute(
        """
        ALTER TABLE ai_settings
            ADD COLUMN model_fast           TEXT NOT NULL DEFAULT '',
            ADD COLUMN price_in             NUMERIC(12, 2),
            ADD COLUMN price_out            NUMERIC(12, 2),
            ADD COLUMN fast_price_in        NUMERIC(12, 2),
            ADD COLUMN fast_price_out       NUMERIC(12, 2),
            ADD COLUMN monthly_limit_rub    NUMERIC(12, 2)
        """
    )

    # Каждый вызов модели. estimated = TRUE — провайдер не вернул usage, токены оценены по символам.
    # cost_rub пусто, если цены не заданы. chars_* и units (сколько запросов в пачке) — для оценки
    # стоимости следующих массовых задач по фактическим токенам этой модели.
    op.execute(
        """
        CREATE TABLE ai_usage (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id                BIGINT REFERENCES workspaces (id) ON DELETE SET NULL,
            task                        TEXT NOT NULL,
            model                       TEXT NOT NULL DEFAULT '',
            tokens_in                   INTEGER NOT NULL DEFAULT 0,
            tokens_out                  INTEGER NOT NULL DEFAULT 0,
            chars_in                    INTEGER NOT NULL DEFAULT 0,
            chars_out                   INTEGER NOT NULL DEFAULT 0,
            units                       INTEGER NOT NULL DEFAULT 0,
            estimated                   BOOLEAN NOT NULL DEFAULT FALSE,
            cost_rub                    NUMERIC(14, 6),
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_usage_user_time ON ai_usage (user_id, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_usage")
    op.execute(
        """
        ALTER TABLE ai_settings
            DROP COLUMN IF EXISTS monthly_limit_rub,
            DROP COLUMN IF EXISTS fast_price_out,
            DROP COLUMN IF EXISTS fast_price_in,
            DROP COLUMN IF EXISTS price_out,
            DROP COLUMN IF EXISTS price_in,
            DROP COLUMN IF EXISTS model_fast
        """
    )
