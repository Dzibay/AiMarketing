"""Подключение Вордстата пользователя: API-ключ Search API Yandex Cloud и каталог.

Revision ID: 0013_wordstat_settings
Revises: 0012_ai_experiments
Create Date: 2026-10-28
"""

from alembic import op

revision = "0013_wordstat_settings"
down_revision = "0012_ai_experiments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Отдельно от ai_settings: Вордстат нужен и с ИИ на OpenAI, а ключ YandexGPT без роли Search API.
    op.execute(
        """
        CREATE TABLE wordstat_settings (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            -- Итог последней проверки кнопкой: пусто — работает.
            checked_at                  TIMESTAMPTZ,
            check_error                 TEXT NOT NULL DEFAULT '',
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS wordstat_settings")
