"""Память чистки «для всех проектов»

user_cleaning_memory — минус-слова и фразы, которые маркетолог пометил «для всех проектов»: бесплатные правила
разметки (после загрузки и перед ИИ) и примеры для ИИ во всех его проектах. Слабее памяти самого проекта и не
действуют там, где проект отметил задетый запрос целевым.

Revision ID: 0068_shared_cleaning_memory
Revises: 0067_project_defaults
Create Date: 2026-11-19
"""

from alembic import op

revision = "0068_shared_cleaning_memory"
down_revision = "0067_project_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_cleaning_memory (
            id              BIGSERIAL PRIMARY KEY,
            user_id         BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            phrase          TEXT NOT NULL,
            phrase_key      TEXT NOT NULL,
            query           TEXT NOT NULL DEFAULT '',
            workspace_id    BIGINT REFERENCES workspaces (id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, phrase_key)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_cleaning_memory")
