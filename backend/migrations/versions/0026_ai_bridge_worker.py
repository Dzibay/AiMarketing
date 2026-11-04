"""Мост через свой Cloudflare Worker: Anthropic не пускает запросы из части дата-центров AI Gateway.

Worker закреплён в регионе США и пересылает запросы провайдерам сам. kind — gateway (AI Gateway) или
worker; у каждого свой токен, чтобы переключение туда-обратно ничего не теряло.

Revision ID: 0026_ai_bridge_worker
Revises: 0025_ai_bridge
Create Date: 2026-11-04
"""

from alembic import op

revision = "0026_ai_bridge_worker"
down_revision = "0025_ai_bridge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ai_bridge
            ADD COLUMN kind TEXT NOT NULL DEFAULT 'gateway',
            ADD COLUMN worker_url TEXT NOT NULL DEFAULT '',
            -- Секрет Worker'а (заголовок x-bridge-token), зашифрован.
            ADD COLUMN worker_token_enc TEXT NOT NULL DEFAULT ''
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_bridge DROP COLUMN kind, DROP COLUMN worker_url, DROP COLUMN worker_token_enc")
