"""Токен Яндекс Аудиторий у подключения — от отдельного OAuth-приложения.

Revision ID: 0020_audience_token
Revises: 0019_rule_campaign
Create Date: 2026-10-30
"""

from alembic import op

revision = "0020_audience_token"
down_revision = "0019_rule_campaign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Основное приложение не может держать четвёртую группу прав — Аудитории выдаются вторым приложением
    # тому же логину. Пустой токен — Аудитории не подключены.
    op.execute(
        """
        ALTER TABLE yandex_connections
            ADD COLUMN IF NOT EXISTS audience_token_enc TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS audience_refresh_enc TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS audience_expires_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS audience_scopes TEXT NOT NULL DEFAULT ''
        """
    )
    # Вход за Аудиториями относится к конкретному логину.
    op.execute(
        "ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS connection_id BIGINT "
        "REFERENCES yandex_connections (id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("DELETE FROM oauth_states WHERE purpose = 'audience'")
    op.execute("ALTER TABLE oauth_states DROP COLUMN IF EXISTS connection_id")
    op.execute(
        """
        ALTER TABLE yandex_connections
            DROP COLUMN IF EXISTS audience_token_enc,
            DROP COLUMN IF EXISTS audience_refresh_enc,
            DROP COLUMN IF EXISTS audience_expires_at,
            DROP COLUMN IF EXISTS audience_scopes
        """
    )
