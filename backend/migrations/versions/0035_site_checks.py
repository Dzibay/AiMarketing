"""Живая проверка сайта на лендинге без регистрации (docs/landing-plan.md, раздел 5).

site_checks — одна проверка: адрес, домен (для кэша), хэш IP (сами IP не храним), статус и результат.
id — случайная строка: по ней посетитель опрашивает статус, перебрать чужие проверки нельзя.
Настройки (включено, модель, лимиты, кэш) — в app_settings, ключи site_check_*.

Revision ID: 0035_site_checks
Revises: 0034_ref_bonus_terms
Create Date: 2026-11-11
"""

from alembic import op

revision = "0035_site_checks"
down_revision = "0034_ref_bonus_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE site_checks (
            id                          TEXT PRIMARY KEY,
            url                         TEXT NOT NULL,
            domain                      TEXT NOT NULL,
            ip_hash                     TEXT NOT NULL DEFAULT '',
            status                      TEXT NOT NULL DEFAULT 'running'
                                        CHECK (status IN ('running', 'done', 'error')),
            result                      JSONB,
            error                       TEXT NOT NULL DEFAULT '',
            model                       TEXT NOT NULL DEFAULT '',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at                 TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_site_checks_domain ON site_checks (domain, created_at DESC)")
    op.execute("CREATE INDEX idx_site_checks_created ON site_checks (created_at)")
    op.execute("CREATE INDEX idx_site_checks_ip ON site_checks (ip_hash, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS site_checks")
