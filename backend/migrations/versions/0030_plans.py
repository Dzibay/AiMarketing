"""Тарифы, подписки, скидки за переход, подтверждение почты и журнал событий пользователя.

plans — тарифы из админки: цена, срок в днях, лимит единиц и проектов, функции. Пробный — тоже тариф
(is_trial, не больше одного): его получает каждый новый пользователь.

subscriptions — тариф пользователя на период [starts_at, ends_at). Лимит единиц действует на этот период,
остаток сгорает. ends_at пусто — без срока, лимит тогда считается по календарному месяцу. plan_id пусто —
без тарифа: без ограничений, кроме того, что задал админ в overrides. overrides — поверх тарифа:
{"units_limit": число | null, "projects_limit": число | null, "features": {"wordstat": false, …}};
ключа нет — как в тарифе, null — без лимита.

Пользователи, которые были до тарифов, получают подписку без тарифа и без срока, а их месячный лимит
(свой users.units_limit или общий service_ai.default_units_limit) переносится в overrides — после деплоя
доступ ни у кого не меняется. Колонки старого лимита удаляются.

Подтверждение почты: users.email_verified_at; все, кто был до этой миграции, считаются подтвердившими.
Токены писем хранятся хэшем.

user_events — журнал событий пользователя (регистрация, вход, подключения, вопросы ИИ, лимит, тариф)
для статистики в админке.

Revision ID: 0030_plans
Revises: 0029_drop_sandbox
Create Date: 2026-11-10
"""

from alembic import op

revision = "0030_plans"
down_revision = "0029_drop_sandbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE plans (
            id                          SERIAL PRIMARY KEY,
            name                        TEXT NOT NULL,
            price_rub                   NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (price_rub >= 0),
            days                        INTEGER NOT NULL DEFAULT 30 CHECK (days BETWEEN 1 AND 3660),
            -- Лимит единиц на оплаченный период; пусто — без лимита.
            units_limit                 NUMERIC(14, 2) CHECK (units_limit >= 0),
            -- Число проектов; пусто — без лимита.
            projects_limit              INTEGER CHECK (projects_limit >= 0),
            -- Функции тарифа: {"autopilot": true, "wordstat": false, …}; ключа нет — включена.
            features                    JSONB NOT NULL DEFAULT '{}',
            is_trial                    BOOLEAN NOT NULL DEFAULT FALSE,
            -- Выключенный тариф не показывается пользователям, но у тех, кто на нём, работает.
            active                      BOOLEAN NOT NULL DEFAULT TRUE,
            position                    INTEGER NOT NULL DEFAULT 0,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX idx_plans_one_trial ON plans (is_trial) WHERE is_trial")
    op.execute(
        """
        CREATE TABLE plan_upgrades (
            from_plan_id                INTEGER NOT NULL REFERENCES plans (id) ON DELETE CASCADE,
            to_plan_id                  INTEGER NOT NULL REFERENCES plans (id) ON DELETE CASCADE,
            discount_pct                NUMERIC(5, 2) NOT NULL CHECK (discount_pct > 0 AND discount_pct <= 100),
            PRIMARY KEY (from_plan_id, to_plan_id),
            CHECK (from_plan_id <> to_plan_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE subscriptions (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            plan_id                     INTEGER REFERENCES plans (id) ON DELETE SET NULL,
            starts_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ends_at                     TIMESTAMPTZ,
            overrides                   JSONB NOT NULL DEFAULT '{}',
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK (ends_at IS NULL OR ends_at > starts_at)
        )
        """
    )
    op.execute("CREATE INDEX idx_subscriptions_plan ON subscriptions (plan_id)")
    op.execute(
        """
        INSERT INTO subscriptions (user_id, starts_at, overrides)
        SELECT u.id, u.created_at,
               CASE WHEN COALESCE(u.units_limit, s.default_units_limit) IS NULL THEN '{}'::jsonb
                    ELSE jsonb_build_object('units_limit', COALESCE(u.units_limit, s.default_units_limit))
               END
        FROM users u LEFT JOIN service_ai s ON s.id = 1
        """
    )
    op.execute("ALTER TABLE users DROP COLUMN units_limit")
    op.execute("ALTER TABLE service_ai DROP COLUMN default_units_limit")

    op.execute("ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMPTZ")
    op.execute("UPDATE users SET email_verified_at = created_at")
    op.execute(
        """
        CREATE TABLE email_verify_tokens (
            token_hash                  TEXT PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at                  TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_email_verify_user ON email_verify_tokens (user_id, created_at DESC)")

    op.execute(
        """
        CREATE TABLE user_events (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            kind                        TEXT NOT NULL,
            meta                        JSONB NOT NULL DEFAULT '{}',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_user_events_user ON user_events (user_id, created_at DESC)")
    op.execute("CREATE INDEX idx_user_events_kind ON user_events (kind, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_events")
    op.execute("DROP TABLE IF EXISTS email_verify_tokens")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS email_verified_at")
    op.execute("ALTER TABLE service_ai ADD COLUMN default_units_limit NUMERIC(14, 2)")
    op.execute("ALTER TABLE users ADD COLUMN units_limit NUMERIC(14, 2)")
    op.execute(
        """
        UPDATE users u SET units_limit = (s.overrides ->> 'units_limit')::numeric
        FROM subscriptions s
        WHERE s.user_id = u.id AND jsonb_typeof(s.overrides -> 'units_limit') = 'number'
        """
    )
    op.execute("DROP TABLE IF EXISTS subscriptions")
    op.execute("DROP TABLE IF EXISTS plan_upgrades")
    op.execute("DROP TABLE IF EXISTS plans")
