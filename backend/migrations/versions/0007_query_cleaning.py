"""Бриф пространства, разметка поисковых запросов (память решений) и откат действий из журнала.

Revision ID: 0007_query_cleaning
Revises: 0006_workspaces
Create Date: 2026-10-26
"""

from alembic import op

revision = "0007_query_cleaning"
down_revision = "0006_workspaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Бриф: что продаём, гео, что точно «не наше» — для ИИ и правил чистки запросов.
    op.execute("ALTER TABLE workspaces ADD COLUMN brief JSONB NOT NULL DEFAULT '{}'::jsonb")

    # Решение по запросу в пространстве. confirmed = FALSE — подсказка (ИИ, память, бриф),
    # TRUE — решение маркетолога: больше не переспрашиваем и показываем ИИ как пример.
    op.execute(
        """
        CREATE TABLE query_labels (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id                BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            query_norm                  TEXT NOT NULL,
            query                       TEXT NOT NULL DEFAULT '',
            verdict                     TEXT NOT NULL CHECK (verdict IN ('target', 'negative')),
            reason                      TEXT NOT NULL DEFAULT '',
            negative_phrase             TEXT NOT NULL DEFAULT '',
            level                       TEXT NOT NULL DEFAULT 'adgroup'
                                        CHECK (level IN ('adgroup', 'campaign')),
            source                      TEXT NOT NULL DEFAULT 'ai',
            confirmed                   BOOLEAN NOT NULL DEFAULT FALSE,
            applied_at                  TIMESTAMPTZ,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (workspace_id, query_norm)
        )
        """
    )
    op.execute("CREATE INDEX idx_query_labels_ws ON query_labels (workspace_id, confirmed, verdict)")

    # Откат: шаги обратного действия, затронутые объекты («campaign:12», «keyword:5»)
    # для предупреждения о более поздних правках, и связь записи отката с исходной.
    op.execute(
        """
        ALTER TABLE action_logs
            ADD COLUMN undo       JSONB,
            ADD COLUMN targets    TEXT[] NOT NULL DEFAULT '{}',
            ADD COLUMN undone_at  TIMESTAMPTZ,
            ADD COLUMN undone_by  BIGINT REFERENCES action_logs (id) ON DELETE SET NULL,
            ADD COLUMN undo_of    BIGINT REFERENCES action_logs (id) ON DELETE SET NULL
        """
    )
    op.execute("CREATE INDEX idx_action_logs_targets ON action_logs USING GIN (targets)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_action_logs_targets")
    op.execute(
        """
        ALTER TABLE action_logs
            DROP COLUMN IF EXISTS undo_of,
            DROP COLUMN IF EXISTS undone_by,
            DROP COLUMN IF EXISTS undone_at,
            DROP COLUMN IF EXISTS targets,
            DROP COLUMN IF EXISTS undo
        """
    )
    op.execute("DROP TABLE IF EXISTS query_labels")
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS brief")
