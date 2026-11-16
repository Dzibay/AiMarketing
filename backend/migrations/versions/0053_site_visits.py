"""Визиты сайта и первый источник пользователя

site_visits — визит на лендинг или открытые страницы кабинета (вход, регистрация), как в Метрике: новый
после 30 минут тишины. visitor_id — случайный идентификатор браузера (localStorage), IP не хранится.
Источник визита: сайт-источник (только хост), UTM-метки, реф-код. user_id — проставляется, когда
посетитель регистрируется.

users.visitor_id и first_* — первый источник пользователя: из самого раннего визита его браузера.

Revision ID: 0053_site_visits
Revises: 0052_activity
Create Date: 2026-11-16
"""

from alembic import op

revision = "0053_site_visits"
down_revision = "0052_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE site_visits (
            id                          BIGSERIAL PRIMARY KEY,
            visitor_id                  TEXT NOT NULL,
            day                         DATE NOT NULL,
            started_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            landing_page                TEXT NOT NULL DEFAULT '',
            referrer_host               TEXT NOT NULL DEFAULT '',
            utm_source                  TEXT NOT NULL DEFAULT '',
            utm_medium                  TEXT NOT NULL DEFAULT '',
            utm_campaign                TEXT NOT NULL DEFAULT '',
            utm_content                 TEXT NOT NULL DEFAULT '',
            utm_term                    TEXT NOT NULL DEFAULT '',
            ref_code                    TEXT NOT NULL DEFAULT '',
            -- desktop | mobile | tablet
            device                      TEXT NOT NULL DEFAULT 'desktop',
            pages                       INTEGER NOT NULL DEFAULT 1,
            user_id                     BIGINT REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_site_visits_visitor ON site_visits (visitor_id, last_at DESC)")
    op.execute("CREATE INDEX idx_site_visits_day ON site_visits (day)")
    op.execute("CREATE INDEX idx_site_visits_user ON site_visits (user_id) WHERE user_id IS NOT NULL")
    op.execute(
        """
        ALTER TABLE users
            ADD COLUMN visitor_id TEXT NOT NULL DEFAULT '',
            ADD COLUMN first_referrer TEXT NOT NULL DEFAULT '',
            ADD COLUMN first_landing TEXT NOT NULL DEFAULT '',
            ADD COLUMN first_utm JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE users DROP COLUMN IF EXISTS visitor_id, DROP COLUMN IF EXISTS first_referrer, "
        "DROP COLUMN IF EXISTS first_landing, DROP COLUMN IF EXISTS first_utm"
    )
    op.execute("DROP TABLE IF EXISTS site_visits")
