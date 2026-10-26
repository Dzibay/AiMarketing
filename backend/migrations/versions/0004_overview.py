"""Сводка по клиентам: доход в статистике, баланс и свежесть статистики кабинетов, утренний дайджест.

Revision ID: 0004_overview
Revises: 0003_jobs
Create Date: 2026-10-26
"""

from alembic import op

revision = "0004_overview"
down_revision = "0003_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Доход по ключевым целям из отчёта Директа — для ДРР.
    op.execute("ALTER TABLE stats_daily ADD COLUMN revenue_kopecks BIGINT NOT NULL DEFAULT 0")

    op.execute(
        """
        ALTER TABLE direct_accounts
            ADD COLUMN balance_kopecks      BIGINT,
            ADD COLUMN balance_currency     TEXT NOT NULL DEFAULT '',
            ADD COLUMN balance_synced_at    TIMESTAMPTZ,
            ADD COLUMN balance_error        TEXT NOT NULL DEFAULT '',
            ADD COLUMN stats_synced_at      TIMESTAMPTZ,
            ADD COLUMN stats_error          TEXT NOT NULL DEFAULT ''
        """
    )

    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN digest_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
            -- Час отправки по Москве.
            ADD COLUMN digest_hour          SMALLINT NOT NULL DEFAULT 9
                                            CHECK (digest_hour BETWEEN 0 AND 23),
            ADD COLUMN last_digest_on       DATE
        """
    )

    # Сводка читает расход по дням кабинета; уникальный индекс начинается с user_id, day.
    op.execute("CREATE INDEX idx_stats_daily_account_day ON stats_daily (account_id, day) WHERE source = 'direct'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_stats_daily_account_day")
    op.execute(
        """
        ALTER TABLE users
            DROP COLUMN IF EXISTS last_digest_on,
            DROP COLUMN IF EXISTS digest_hour,
            DROP COLUMN IF EXISTS digest_enabled
        """
    )
    op.execute(
        """
        ALTER TABLE direct_accounts
            DROP COLUMN IF EXISTS stats_error,
            DROP COLUMN IF EXISTS stats_synced_at,
            DROP COLUMN IF EXISTS balance_error,
            DROP COLUMN IF EXISTS balance_synced_at,
            DROP COLUMN IF EXISTS balance_currency,
            DROP COLUMN IF EXISTS balance_kopecks
        """
    )
    op.execute("ALTER TABLE stats_daily DROP COLUMN IF EXISTS revenue_kopecks")
