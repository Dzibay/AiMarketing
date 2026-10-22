-- Схема PostgreSQL AiMarketing.
-- Идемпотентно: docker-entrypoint-initdb.d и backend ensure_schema() при каждом старте.

-- ---------------------------------------------------------------------------
-- Пользователи SaaS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id                          BIGSERIAL PRIMARY KEY,
    email                       TEXT NOT NULL DEFAULT '',
    password_hash               TEXT NOT NULL DEFAULT '',
    name                        TEXT NOT NULL DEFAULT '',
    yandex_uid                  TEXT NOT NULL DEFAULT '',
    blocked_at                  TIMESTAMPTZ,
    blocked_reason              TEXT NOT NULL DEFAULT '',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS yandex_uid TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_reason TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
    ON users (lower(email)) WHERE email <> '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_yandex_uid
    ON users (yandex_uid) WHERE yandex_uid <> '';
CREATE INDEX IF NOT EXISTS idx_users_created
    ON users (created_at DESC);

-- ---------------------------------------------------------------------------
-- OAuth-подключение Яндекса (один токен на Direct + Metrika)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS yandex_connections (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    yandex_uid                  TEXT NOT NULL DEFAULT '',
    yandex_login                TEXT NOT NULL DEFAULT '',
    display_name                TEXT NOT NULL DEFAULT '',
    access_token_enc            TEXT NOT NULL DEFAULT '',
    refresh_token_enc           TEXT NOT NULL DEFAULT '',
    expires_at                  TIMESTAMPTZ,
    scopes                      TEXT NOT NULL DEFAULT '',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS yandex_uid TEXT NOT NULL DEFAULT '';
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS yandex_login TEXT NOT NULL DEFAULT '';
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT '';
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS access_token_enc TEXT NOT NULL DEFAULT '';
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS refresh_token_enc TEXT NOT NULL DEFAULT '';
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS scopes TEXT NOT NULL DEFAULT '';
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_yandex_conn_user_uid
    ON yandex_connections (user_id, yandex_uid);
CREATE INDEX IF NOT EXISTS idx_yandex_conn_user
    ON yandex_connections (user_id);

-- ---------------------------------------------------------------------------
-- Счётчики Метрики
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrika_counters (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    connection_id               BIGINT NOT NULL REFERENCES yandex_connections (id) ON DELETE CASCADE,
    counter_id                  BIGINT NOT NULL,
    name                        TEXT NOT NULL DEFAULT '',
    site                        TEXT NOT NULL DEFAULT '',
    enabled                     BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE metrika_counters ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE metrika_counters ADD COLUMN IF NOT EXISTS site TEXT NOT NULL DEFAULT '';
ALTER TABLE metrika_counters ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE metrika_counters ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_metrika_counter_unique
    ON metrika_counters (connection_id, counter_id);
CREATE INDEX IF NOT EXISTS idx_metrika_user
    ON metrika_counters (user_id);

-- ---------------------------------------------------------------------------
-- Кабинеты / клиенты Директа
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS direct_accounts (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    connection_id               BIGINT NOT NULL REFERENCES yandex_connections (id) ON DELETE CASCADE,
    client_login                TEXT NOT NULL DEFAULT '',
    client_id                   BIGINT,
    name                        TEXT NOT NULL DEFAULT '',
    currency                    TEXT NOT NULL DEFAULT '',
    enabled                     BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS client_login TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS client_id BIGINT;
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_account_unique
    ON direct_accounts (connection_id, client_login);
CREATE INDEX IF NOT EXISTS idx_direct_user
    ON direct_accounts (user_id);

-- ---------------------------------------------------------------------------
-- Одноразовые state для OAuth
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oauth_states (
    state                       TEXT PRIMARY KEY,
    user_id                     BIGINT REFERENCES users (id) ON DELETE CASCADE,
    purpose                     TEXT NOT NULL DEFAULT 'login',
    expires_at                  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_expires
    ON oauth_states (expires_at);

-- ---------------------------------------------------------------------------
-- Настройки, которые меняют из админки без рестарта
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    key                         TEXT PRIMARY KEY,
    value                       TEXT NOT NULL DEFAULT ''
);
