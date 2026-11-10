"""Общие ИИ, Вордстат, картинки и мост сервиса вместо ключей пользователей; расход в единицах.

Подключения теперь одни на весь сервис и настраиваются в админке (по одной строке, id = 1). Модели —
три класса (быстрая, стандартная, думающая): у каждого цена в единицах за 1 млн токенов (её списывают
с пользователя) и себестоимость в рублях (для маржи). Вордстат и картинки — цена за запрос / картинку.

ai_usage — журнал списаний: kind (model | wordstat | image), класс модели и списанные единицы (charged);
cost_rub — себестоимость в рублях. До тарифов (фаза 2) лимит единиц на месяц — users.units_limit,
пусто — общий лимит из настроек сервиса.

Ключи пользователей не переносятся: исключение — если подключение было ровно у одного пользователя
(до продажи сервисом пользовался только владелец), тогда оно становится общим.

Revision ID: 0028_service_ai
Revises: 0027_ai_message_model
Create Date: 2026-11-10
"""

from alembic import op

revision = "0028_service_ai"
down_revision = "0027_ai_message_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE service_ai (
            id                          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            provider                    TEXT NOT NULL DEFAULT 'yandexgpt' CHECK (provider IN ('openai', 'yandexgpt')),
            base_url                    TEXT NOT NULL DEFAULT '',
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            temperature                 NUMERIC(3, 2) NOT NULL DEFAULT 0.3,
            -- Класс модели на задачу: {"chat": "standard", "classify": "fast", …}; нет ключа — по умолчанию.
            task_classes                JSONB NOT NULL DEFAULT '{}',
            -- Лимит единиц на месяц, если у пользователя не задан свой; пусто — без лимита. До тарифов.
            default_units_limit         NUMERIC(14, 2),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("INSERT INTO service_ai (id) VALUES (1)")
    op.execute(
        """
        CREATE TABLE ai_model_classes (
            id                          TEXT PRIMARY KEY CHECK (id IN ('fast', 'standard', 'thinking')),
            position                    SMALLINT NOT NULL,
            name                        TEXT NOT NULL,
            model                       TEXT NOT NULL DEFAULT '',
            -- Единиц за 1 млн токенов входа и выхода — списывается с пользователя; пусто — бесплатно.
            units_in                    NUMERIC(14, 4),
            units_out                   NUMERIC(14, 4),
            -- Себестоимость: рублей за 1 млн токенов — для маржи в админке.
            cost_in                     NUMERIC(12, 2),
            cost_out                    NUMERIC(12, 2),
            enabled                     BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        INSERT INTO ai_model_classes (id, position, name, model, enabled) VALUES
            ('fast', 1, 'Быстрая', 'yandexgpt-lite/latest', TRUE),
            ('standard', 2, 'Стандартная', 'yandexgpt/latest', TRUE),
            ('thinking', 3, 'Думающая', '', FALSE)
        """
    )
    op.execute(
        """
        CREATE TABLE service_wordstat (
            id                          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            units_per_request           NUMERIC(14, 4),
            -- Яндекс берёт 20 ₽ за 1000 запросов.
            cost_per_request            NUMERIC(12, 4) NOT NULL DEFAULT 0.02,
            checked_at                  TIMESTAMPTZ,
            check_error                 TEXT NOT NULL DEFAULT '',
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("INSERT INTO service_wordstat (id) VALUES (1)")
    op.execute(
        """
        CREATE TABLE service_images (
            id                          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            -- openai — OpenAI и совместимые, yandexart — YandexART в Yandex AI Studio.
            provider                    TEXT NOT NULL DEFAULT 'yandexart',
            base_url                    TEXT NOT NULL DEFAULT '',
            model                       TEXT NOT NULL DEFAULT '',
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            units_per_image             NUMERIC(14, 4),
            cost_per_image              NUMERIC(12, 2),
            checked_at                  TIMESTAMPTZ,
            check_error                 TEXT NOT NULL DEFAULT '',
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("INSERT INTO service_images (id) VALUES (1)")
    op.execute(
        """
        CREATE TABLE service_bridge (
            id                          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            enabled                     BOOLEAN NOT NULL DEFAULT FALSE,
            kind                        TEXT NOT NULL DEFAULT 'worker',
            account_id                  TEXT NOT NULL DEFAULT '',
            gateway_id                  TEXT NOT NULL DEFAULT '',
            token_enc                   TEXT NOT NULL DEFAULT '',
            worker_url                  TEXT NOT NULL DEFAULT '',
            worker_token_enc            TEXT NOT NULL DEFAULT '',
            use_chat                    BOOLEAN NOT NULL DEFAULT TRUE,
            use_images                  BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("INSERT INTO service_bridge (id) VALUES (1)")

    # Подключение ровно одного пользователя (владельца) становится общим; иначе вводится заново в админке.
    op.execute(
        """
        UPDATE service_ai s SET provider = a.provider, base_url = a.base_url, folder_id = a.folder_id,
               api_key_enc = a.api_key_enc, temperature = a.temperature
        FROM ai_settings a
        WHERE a.api_key_enc <> '' AND (SELECT COUNT(*) FROM ai_settings WHERE api_key_enc <> '') = 1
        """
    )
    op.execute(
        """
        UPDATE ai_model_classes c SET model = a.model, cost_in = a.price_in, cost_out = a.price_out
        FROM ai_settings a
        WHERE c.id = 'standard' AND a.api_key_enc <> '' AND a.model <> ''
          AND (SELECT COUNT(*) FROM ai_settings WHERE api_key_enc <> '') = 1
        """
    )
    op.execute(
        """
        UPDATE ai_model_classes c SET model = a.model_fast,
               cost_in = COALESCE(a.fast_price_in, a.price_in), cost_out = COALESCE(a.fast_price_out, a.price_out)
        FROM ai_settings a
        WHERE c.id = 'fast' AND a.api_key_enc <> '' AND a.model_fast <> ''
          AND (SELECT COUNT(*) FROM ai_settings WHERE api_key_enc <> '') = 1
        """
    )
    op.execute(
        """
        UPDATE service_wordstat s SET folder_id = w.folder_id, api_key_enc = w.api_key_enc
        FROM wordstat_settings w
        WHERE w.api_key_enc <> '' AND w.folder_id <> ''
          AND (SELECT COUNT(*) FROM wordstat_settings WHERE api_key_enc <> '' AND folder_id <> '') = 1
        """
    )
    op.execute(
        """
        UPDATE service_images s SET provider = i.provider, base_url = i.base_url, model = i.model,
               folder_id = i.folder_id, api_key_enc = i.api_key_enc, cost_per_image = i.price_rub
        FROM image_gen_settings i
        WHERE i.api_key_enc <> '' AND (SELECT COUNT(*) FROM image_gen_settings WHERE api_key_enc <> '') = 1
        """
    )
    op.execute(
        """
        UPDATE service_bridge s SET enabled = b.enabled, kind = b.kind, account_id = b.account_id,
               gateway_id = b.gateway_id, token_enc = b.token_enc, worker_url = b.worker_url,
               worker_token_enc = b.worker_token_enc, use_chat = b.use_chat, use_images = b.use_images
        FROM ai_bridge b
        WHERE (b.worker_url <> '' OR b.account_id <> '')
          AND (SELECT COUNT(*) FROM ai_bridge WHERE worker_url <> '' OR account_id <> '') = 1
        """
    )
    op.execute("DROP TABLE ai_settings")
    op.execute("DROP TABLE wordstat_settings")
    op.execute("DROP TABLE image_gen_settings")
    op.execute("DROP TABLE ai_bridge")

    # Журнал списаний. Старые строки — расход по своим ключам: единиц не списывали.
    op.execute(
        """
        ALTER TABLE ai_usage
            ADD COLUMN kind TEXT NOT NULL DEFAULT 'model' CHECK (kind IN ('model', 'wordstat', 'image')),
            ADD COLUMN model_class TEXT NOT NULL DEFAULT '',
            ADD COLUMN charged NUMERIC(14, 4) NOT NULL DEFAULT 0
        """
    )
    op.execute("UPDATE ai_usage SET kind = 'image' WHERE task = 'images'")
    op.execute("CREATE INDEX idx_ai_usage_time ON ai_usage (created_at)")
    op.execute("ALTER TABLE users ADD COLUMN units_limit NUMERIC(14, 2)")
    # Класс модели, выбранный в чате; пусто — класс чата по умолчанию из настроек сервиса.
    op.execute("ALTER TABLE ai_chats ADD COLUMN model_class TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_chats DROP COLUMN IF EXISTS model_class")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS units_limit")
    op.execute("DROP INDEX IF EXISTS idx_ai_usage_time")
    op.execute("ALTER TABLE ai_usage DROP COLUMN kind, DROP COLUMN model_class, DROP COLUMN charged")
    op.execute(
        """
        CREATE TABLE ai_settings (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            provider                    TEXT NOT NULL DEFAULT 'openai' CHECK (provider IN ('openai', 'yandexgpt')),
            base_url                    TEXT NOT NULL DEFAULT '',
            model                       TEXT NOT NULL DEFAULT '',
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            temperature                 NUMERIC(3, 2) NOT NULL DEFAULT 0.3,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            model_fast                  TEXT NOT NULL DEFAULT '',
            price_in                    NUMERIC(12, 2),
            price_out                   NUMERIC(12, 2),
            fast_price_in               NUMERIC(12, 2),
            fast_price_out              NUMERIC(12, 2),
            monthly_limit_rub           NUMERIC(12, 2)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE wordstat_settings (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            checked_at                  TIMESTAMPTZ,
            check_error                 TEXT NOT NULL DEFAULT '',
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE image_gen_settings (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            provider                    TEXT NOT NULL DEFAULT 'openai',
            base_url                    TEXT NOT NULL DEFAULT '',
            model                       TEXT NOT NULL DEFAULT '',
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            price_rub                   NUMERIC(12, 2),
            checked_at                  TIMESTAMPTZ,
            check_error                 TEXT NOT NULL DEFAULT '',
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ai_bridge (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            enabled                     BOOLEAN NOT NULL DEFAULT FALSE,
            account_id                  TEXT NOT NULL DEFAULT '',
            gateway_id                  TEXT NOT NULL DEFAULT '',
            token_enc                   TEXT NOT NULL DEFAULT '',
            use_chat                    BOOLEAN NOT NULL DEFAULT TRUE,
            use_images                  BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            kind                        TEXT NOT NULL DEFAULT 'gateway',
            worker_url                  TEXT NOT NULL DEFAULT '',
            worker_token_enc            TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute("DROP TABLE IF EXISTS service_bridge")
    op.execute("DROP TABLE IF EXISTS service_images")
    op.execute("DROP TABLE IF EXISTS service_wordstat")
    op.execute("DROP TABLE IF EXISTS ai_model_classes")
    op.execute("DROP TABLE IF EXISTS service_ai")
