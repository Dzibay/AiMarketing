"""Мост Cloudflare AI Gateway: запросы к зарубежным моделям идут через шлюз Cloudflare, а не напрямую с сервера.

Один мост на пользователя — им пользуются и чат (ai_settings), и генерация картинок (image_gen_settings),
каждый по своему флажку. Выключенный мост хранит данные, чтобы включить его снова одной галочкой.

Revision ID: 0025_ai_bridge
Revises: 0024_ai_message_refs
Create Date: 2026-11-04
"""

from alembic import op

revision = "0025_ai_bridge"
down_revision = "0024_ai_message_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_bridge (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            enabled                     BOOLEAN NOT NULL DEFAULT FALSE,
            account_id                  TEXT NOT NULL DEFAULT '',
            gateway_id                  TEXT NOT NULL DEFAULT '',
            -- Токен Authenticated Gateway (cf-aig-authorization), зашифрован. Пусто — шлюз без авторизации.
            token_enc                   TEXT NOT NULL DEFAULT '',
            use_chat                    BOOLEAN NOT NULL DEFAULT TRUE,
            use_images                  BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_bridge")
