"""Файлы контекста пространства (.txt, .md, zip с ними) — ИИ читает их в чатах пространства.

В промпт уходит не больше лимита пространства (символов; NULL — по умолчанию).

Revision ID: 0022_context_files
Revises: 0021_image_gen
Create Date: 2026-10-31
"""

from alembic import op

revision = "0022_context_files"
down_revision = "0021_image_gen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace_context_files (
            id                  BIGSERIAL PRIMARY KEY,
            user_id             BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id        BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            -- Архив, из которого файл; '' — загружен отдельно. Имя — путь внутри архива.
            source              TEXT NOT NULL DEFAULT '',
            filename            TEXT NOT NULL DEFAULT '',
            content             TEXT NOT NULL DEFAULT '',
            chars               INTEGER NOT NULL DEFAULT 0,
            -- Выключенный файл хранится, но в промпт не идёт.
            enabled             BOOLEAN NOT NULL DEFAULT TRUE,
            uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX workspace_context_files_ws_idx ON workspace_context_files (workspace_id, id)")
    op.execute("ALTER TABLE workspaces ADD COLUMN context_files_limit INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS context_files_limit")
    op.execute("DROP TABLE IF EXISTS workspace_context_files")
