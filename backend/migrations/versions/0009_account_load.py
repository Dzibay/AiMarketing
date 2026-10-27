"""Полная загрузка кабинета и счётчика по этапам, метка Changes API и баллы Директа.

Revision ID: 0009_account_load
Revises: 0008_ai_usage
Create Date: 2026-10-27
"""

from alembic import op

revision = "0009_account_load"
down_revision = "0008_ai_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # load_state — этапы загрузки: {"campaigns": {"status", "at", "done", "total", "count", "error"}, …}.
    # loaded_at — первая полная загрузка закончилась; пусто — кабинет ещё грузится (в том числе
    # уже подключённые до этой миграции: их догрузит планировщик).
    # changes_ts — метка сервиса Changes: с неё спрашиваем, какие кампании менялись.
    # units_* — баллы API Директа из последнего ответа (остаток и суточный лимит).
    op.execute(
        """
        ALTER TABLE direct_accounts
            ADD COLUMN load_state   JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN loaded_at    TIMESTAMPTZ,
            ADD COLUMN changes_ts   TEXT NOT NULL DEFAULT '',
            ADD COLUMN units_left   INTEGER,
            ADD COLUMN units_limit  INTEGER,
            ADD COLUMN units_at     TIMESTAMPTZ
        """
    )
    op.execute(
        """
        ALTER TABLE metrika_counters
            ADD COLUMN load_state   JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN loaded_at    TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE metrika_counters
            DROP COLUMN loaded_at,
            DROP COLUMN load_state
        """
    )
    op.execute(
        """
        ALTER TABLE direct_accounts
            DROP COLUMN units_at,
            DROP COLUMN units_limit,
            DROP COLUMN units_left,
            DROP COLUMN changes_ts,
            DROP COLUMN loaded_at,
            DROP COLUMN load_state
        """
    )
