"""Генерация картинок для объявлений по API пользователя: OpenAI-совместимый или YandexART.

Revision ID: 0021_image_gen
Revises: 0020_audience_token
Create Date: 2026-10-30
"""

from alembic import op

revision = "0021_image_gen"
down_revision = "0020_audience_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Отдельно от ai_settings: картинки часто рисует другой провайдер, чем пишет тексты (YandexGPT + gpt-image).
    op.execute(
        """
        CREATE TABLE image_gen_settings (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            -- openai — OpenAI и совместимые, yandexart — YandexART в Yandex AI Studio.
            provider                    TEXT NOT NULL DEFAULT 'openai',
            base_url                    TEXT NOT NULL DEFAULT '',
            model                       TEXT NOT NULL DEFAULT '',
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            -- Рублей за одну картинку: расход идёт в ai_usage и в месячный лимит ИИ. Пусто — не считаем.
            price_rub                   NUMERIC(12, 2),
            checked_at                  TIMESTAMPTZ,
            check_error                 TEXT NOT NULL DEFAULT '',
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS image_gen_settings")
