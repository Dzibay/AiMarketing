"""Файлы контекста клиента: прайс и условия загружаются один раз и видны ИИ во всех проектах клиента.

Файл принадлежит ровно одному владельцу: проекту (workspace_id) или клиенту (client_row_id). Удалили
клиента — его файлы удаляются вместе с ним.

Revision ID: 0063_client_files
Revises: 0062_decisions_undo
Create Date: 2026-11-19
"""

from alembic import op

revision = "0063_client_files"
down_revision = "0062_decisions_undo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE workspace_context_files ALTER COLUMN workspace_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE workspace_context_files "
        "ADD COLUMN client_row_id BIGINT REFERENCES clients (id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE workspace_context_files ADD CONSTRAINT workspace_context_files_owner_chk "
        "CHECK ((workspace_id IS NULL) <> (client_row_id IS NULL))"
    )
    op.execute(
        "CREATE INDEX workspace_context_files_client_idx ON workspace_context_files (client_row_id, id) "
        "WHERE client_row_id IS NOT NULL"
    )


def downgrade() -> None:
    # Файлам клиента без проекта в старой схеме места нет.
    op.execute("DELETE FROM workspace_context_files WHERE workspace_id IS NULL")
    op.execute("DROP INDEX IF EXISTS workspace_context_files_client_idx")
    op.execute("ALTER TABLE workspace_context_files DROP CONSTRAINT IF EXISTS workspace_context_files_owner_chk")
    op.execute("ALTER TABLE workspace_context_files DROP COLUMN IF EXISTS client_row_id")
    op.execute("ALTER TABLE workspace_context_files ALTER COLUMN workspace_id SET NOT NULL")
