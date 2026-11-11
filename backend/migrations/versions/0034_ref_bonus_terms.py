"""Личные реф-ссылки с бонусом и оплата тарифа на 1, 3 или 12 месяцев.

ref_links.owner_user_id — личная ссылка пользователя «Ссылка для друзей» (одна на пользователя); у ссылок
из админки пусто. Удалили пользователя — его ссылка удаляется, пришедшие по ней остаются без источника.

ref_bonuses — бонус за приглашённого: выдаётся один раз, когда приглашённый по личной ссылке впервые
оплатит тариф (не тестовым платежом). Дни пригласившему и приглашённому задаёт админ (app_settings
ref_bonus_referrer_days / ref_bonus_invitee_days); в строке — сколько дней на самом деле добавили.

plans.discount_3_pct / discount_12_pct — скидка за оплату сразу на 3 и 12 сроков тарифа.
payments.months — на сколько сроков тарифа платёж (days уже умножены на него).

Revision ID: 0034_ref_bonus_terms
Revises: 0033_ai_style
Create Date: 2026-11-11
"""

from alembic import op

revision = "0034_ref_bonus_terms"
down_revision = "0033_ai_style"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ref_links ADD COLUMN owner_user_id BIGINT UNIQUE REFERENCES users (id) ON DELETE CASCADE"
    )
    op.execute(
        """
        CREATE TABLE ref_bonuses (
            invitee_user_id             BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            referrer_user_id            BIGINT REFERENCES users (id) ON DELETE SET NULL,
            payment_id                  BIGINT REFERENCES payments (id) ON DELETE SET NULL,
            referrer_days               INTEGER NOT NULL DEFAULT 0,
            invitee_days                INTEGER NOT NULL DEFAULT 0,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_ref_bonuses_referrer ON ref_bonuses (referrer_user_id)")
    op.execute(
        """
        ALTER TABLE plans
            ADD COLUMN discount_3_pct NUMERIC(5, 2) NOT NULL DEFAULT 0 CHECK (discount_3_pct BETWEEN 0 AND 100),
            ADD COLUMN discount_12_pct NUMERIC(5, 2) NOT NULL DEFAULT 0 CHECK (discount_12_pct BETWEEN 0 AND 100)
        """
    )
    op.execute(
        "ALTER TABLE payments ADD COLUMN months SMALLINT NOT NULL DEFAULT 1 CHECK (months IN (1, 3, 12))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS months")
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS discount_12_pct")
    op.execute("ALTER TABLE plans DROP COLUMN IF EXISTS discount_3_pct")
    op.execute("DROP TABLE IF EXISTS ref_bonuses")
    op.execute("ALTER TABLE ref_links DROP COLUMN IF EXISTS owner_user_id")
