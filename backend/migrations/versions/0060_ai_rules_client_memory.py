"""Правила ИИ аккаунта и память клиента

users.ai_rules / users.ai_tone — «Мои правила для ИИ» и тон по умолчанию: действуют во всех чатах, в том числе
в общем. clients.ai_notes / clients.ai_facts — «Что ИИ знает о клиенте»: свободный текст и запомненные ответы
того же вида, что ai_facts проекта; видны ИИ в каждом проекте клиента. clients.notes остаются личными заметками
маркетолога и в ИИ не уходят. ai_chats.digest — выжимка прошлых решений проекта, снятая на первом ходе нового
чата (NULL — ещё не снималась), чтобы системный промпт чата не менялся от хода к ходу.

Revision ID: 0060_ai_rules_client_memory
Revises: 0059_moderation_reasons
Create Date: 2026-11-19
"""

from alembic import op

revision = "0060_ai_rules_client_memory"
down_revision = "0059_moderation_reasons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN ai_rules TEXT NOT NULL DEFAULT '',
            ADD COLUMN ai_tone TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute(
        """
        ALTER TABLE clients
            ADD COLUMN ai_notes TEXT NOT NULL DEFAULT '',
            ADD COLUMN ai_facts JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )
    op.execute("ALTER TABLE ai_chats ADD COLUMN digest TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_chats DROP COLUMN IF EXISTS digest")
    op.execute("ALTER TABLE clients DROP COLUMN IF EXISTS ai_facts, DROP COLUMN IF EXISTS ai_notes")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS ai_tone, DROP COLUMN IF EXISTS ai_rules")
