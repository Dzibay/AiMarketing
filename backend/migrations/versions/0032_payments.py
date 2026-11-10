"""Оплата через ЮKassa, промокоды, реф-ссылки и напоминания об окончании тарифа.

service_payments — подключение ЮKassa из админки (одна строка): shopId, секретный ключ (зашифрован),
тестовый магазин, оплата включена для пользователей, итог проверки подключения.

payments — платёж за тариф: сумма фиксируется при создании (цена со скидкой за переход), provider_id —
id платежа в ЮKassa. applied_at — подписка уже продлена: повторное уведомление не продлевает дважды.
refunded_rub — сколько вернули (возвраты делают вручную в ЮKassa, сумма подтягивается). receipt_done —
админ отметил, что чек самозанятого пробит в «Мой налог» (ЮKassa с 29.12.2025 сама их не регистрирует).
Пользователя удалили — платёж остаётся для учёта (user_id пусто).

promo_codes — промокод: kind units (единицы к лимиту текущего периода), days (дни к подписке), plan
(тариф на N дней). Срок действия — последний день включительно, число активаций — на всех;
один пользователь активирует код один раз (promo_redemptions).

subscriptions.bonus_units — единицы из промокодов, действуют только в периоде bonus_period_start.

ref_links — реф-ссылки ?ref=код: переходы считаются на сайте при первом заходе, регистрация запоминает
ссылку в users.ref_link_id. oauth_states.signup — реф-код, пока новый пользователь входит через Яндекс.

plan_reminders — какие напоминания об окончании тарифа уже ушли: (пользователь, конец подписки, за сколько
дней). Продлили — конец другой, напоминания пойдут заново.

Revision ID: 0032_payments
Revises: 0031_general_chat
Create Date: 2026-11-10
"""

from alembic import op

revision = "0032_payments"
down_revision = "0031_general_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE service_payments (
            id                          SMALLINT PRIMARY KEY CHECK (id = 1),
            shop_id                     TEXT NOT NULL DEFAULT '',
            secret_key_enc              TEXT NOT NULL DEFAULT '',
            test_mode                   BOOLEAN NOT NULL DEFAULT TRUE,
            enabled                     BOOLEAN NOT NULL DEFAULT FALSE,
            checked_at                  TIMESTAMPTZ,
            check_error                 TEXT NOT NULL DEFAULT '',
            -- Что ответила ЮKassa на проверке: магазин тестовый или боевой.
            check_test                  BOOLEAN,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("INSERT INTO service_payments (id) VALUES (1)")

    op.execute(
        """
        CREATE TABLE payments (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT REFERENCES users (id) ON DELETE SET NULL,
            plan_id                     INTEGER REFERENCES plans (id) ON DELETE SET NULL,
            plan_name                   TEXT NOT NULL DEFAULT '',
            days                        INTEGER NOT NULL CHECK (days > 0),
            -- renew — продление своего тарифа, change — переход на другой.
            kind                        TEXT NOT NULL CHECK (kind IN ('renew', 'change')),
            amount_rub                  NUMERIC(12, 2) NOT NULL CHECK (amount_rub > 0),
            provider                    TEXT NOT NULL DEFAULT 'yookassa',
            provider_id                 TEXT UNIQUE,
            -- new (ещё не создан в ЮKassa) | pending | waiting_for_capture | succeeded | canceled | error
            status                      TEXT NOT NULL DEFAULT 'new',
            test                        BOOLEAN NOT NULL DEFAULT FALSE,
            confirmation_url            TEXT NOT NULL DEFAULT '',
            cancel_reason               TEXT NOT NULL DEFAULT '',
            refunded_rub                NUMERIC(12, 2) NOT NULL DEFAULT 0,
            receipt_done                BOOLEAN NOT NULL DEFAULT FALSE,
            paid_at                     TIMESTAMPTZ,
            applied_at                  TIMESTAMPTZ,
            -- Последний день подписки после продления — для истории.
            last_day                    DATE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_payments_user ON payments (user_id, created_at DESC)")
    op.execute("CREATE INDEX idx_payments_open ON payments (created_at) WHERE status IN ('pending', 'waiting_for_capture')")

    op.execute(
        """
        CREATE TABLE promo_codes (
            id                          SERIAL PRIMARY KEY,
            code                        TEXT NOT NULL UNIQUE,
            kind                        TEXT NOT NULL CHECK (kind IN ('units', 'days', 'plan')),
            units                       NUMERIC(14, 2) CHECK (units > 0),
            days                        INTEGER CHECK (days BETWEEN 1 AND 3660),
            plan_id                     INTEGER REFERENCES plans (id) ON DELETE RESTRICT,
            -- Пусто — без ограничения.
            max_uses                    INTEGER CHECK (max_uses > 0),
            -- Последний день действия включительно (по Москве); пусто — бессрочно.
            valid_until                 DATE,
            active                      BOOLEAN NOT NULL DEFAULT TRUE,
            note                        TEXT NOT NULL DEFAULT '',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (kind <> 'units' OR units IS NOT NULL),
            CHECK (kind <> 'days' OR days IS NOT NULL),
            CHECK (kind <> 'plan' OR (plan_id IS NOT NULL AND days IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE promo_redemptions (
            promo_id                    INTEGER NOT NULL REFERENCES promo_codes (id) ON DELETE CASCADE,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (promo_id, user_id)
        )
        """
    )
    op.execute("ALTER TABLE subscriptions ADD COLUMN bonus_units NUMERIC(14, 2) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE subscriptions ADD COLUMN bonus_period_start TIMESTAMPTZ")

    op.execute(
        """
        CREATE TABLE ref_links (
            id                          SERIAL PRIMARY KEY,
            code                        TEXT NOT NULL UNIQUE,
            name                        TEXT NOT NULL DEFAULT '',
            visits                      INTEGER NOT NULL DEFAULT 0,
            active                      BOOLEAN NOT NULL DEFAULT TRUE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("ALTER TABLE users ADD COLUMN ref_link_id INTEGER REFERENCES ref_links (id) ON DELETE SET NULL")
    op.execute("CREATE INDEX idx_users_ref_link ON users (ref_link_id) WHERE ref_link_id IS NOT NULL")
    op.execute("ALTER TABLE oauth_states ADD COLUMN signup JSONB NOT NULL DEFAULT '{}'")

    op.execute(
        """
        CREATE TABLE plan_reminders (
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            ends_at                     TIMESTAMPTZ NOT NULL,
            days                        SMALLINT NOT NULL,
            sent_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, ends_at, days)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plan_reminders")
    op.execute("ALTER TABLE oauth_states DROP COLUMN IF EXISTS signup")
    op.execute("DROP INDEX IF EXISTS idx_users_ref_link")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS ref_link_id")
    op.execute("DROP TABLE IF EXISTS ref_links")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS bonus_period_start")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS bonus_units")
    op.execute("DROP TABLE IF EXISTS promo_redemptions")
    op.execute("DROP TABLE IF EXISTS promo_codes")
    op.execute("DROP TABLE IF EXISTS payments")
    op.execute("DROP TABLE IF EXISTS service_payments")
