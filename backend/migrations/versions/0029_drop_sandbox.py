"""Песочница Директа убрана: Яндекс отключил её API 15.07.2026, такие подключения не обновляются.

Подключения-песочницы удаляются вместе с их кабинетами и кампаниями (каскадом), флаг — из таблицы.

Revision ID: 0029_drop_sandbox
Revises: 0028_service_ai
Create Date: 2026-11-10
"""

from alembic import op

revision = "0029_drop_sandbox"
down_revision = "0028_service_ai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM yandex_connections WHERE sandbox")
    op.execute("DROP INDEX IF EXISTS idx_yandex_conn_user_uid")
    op.execute("CREATE UNIQUE INDEX idx_yandex_conn_user_uid ON yandex_connections (user_id, yandex_uid)")
    op.execute("ALTER TABLE yandex_connections DROP COLUMN sandbox")
    op.execute("UPDATE oauth_states SET mode = '' WHERE mode = 'sandbox'")


def downgrade() -> None:
    op.execute("ALTER TABLE yandex_connections ADD COLUMN sandbox BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("DROP INDEX IF EXISTS idx_yandex_conn_user_uid")
    op.execute("CREATE UNIQUE INDEX idx_yandex_conn_user_uid ON yandex_connections (user_id, yandex_uid, sandbox)")
