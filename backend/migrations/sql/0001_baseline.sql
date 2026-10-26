-- Базовая схема AiMarketing (замороженный снимок бывшего database/init.sql).
-- Идемпотентна: накатывается и на пустую БД, и на БД, созданную до Alembic.
-- Не редактировать — изменения схемы только новыми миграциями.

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
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_chat_id TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique
    ON users (lower(email)) WHERE email <> '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_yandex_uid
    ON users (yandex_uid) WHERE yandex_uid <> '';
CREATE INDEX IF NOT EXISTS idx_users_created
    ON users (created_at DESC);

-- ---------------------------------------------------------------------------
-- OAuth-подключение Яндекса
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
    last_sync_at                TIMESTAMPTZ,
    last_sync_status            TEXT NOT NULL DEFAULT '',
    last_sync_error             TEXT NOT NULL DEFAULT '',
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
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ;
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS last_sync_status TEXT NOT NULL DEFAULT '';
ALTER TABLE yandex_connections ADD COLUMN IF NOT EXISTS last_sync_error TEXT NOT NULL DEFAULT '';
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
-- Цели Метрики
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrika_goals (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    counter_row_id              BIGINT NOT NULL REFERENCES metrika_counters (id) ON DELETE CASCADE,
    goal_id                     BIGINT NOT NULL,
    name                        TEXT NOT NULL DEFAULT '',
    goal_type                   TEXT NOT NULL DEFAULT '',
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE metrika_goals ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE metrika_goals ADD COLUMN IF NOT EXISTS goal_type TEXT NOT NULL DEFAULT '';
ALTER TABLE metrika_goals ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_metrika_goals_unique
    ON metrika_goals (counter_row_id, goal_id);
CREATE INDEX IF NOT EXISTS idx_metrika_goals_user
    ON metrika_goals (user_id);

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
    last_sync_at                TIMESTAMPTZ,
    last_sync_status            TEXT NOT NULL DEFAULT '',
    last_sync_error             TEXT NOT NULL DEFAULT '',
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS client_login TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS client_id BIGINT;
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ;
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS last_sync_status TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS last_sync_error TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_accounts ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_account_unique
    ON direct_accounts (connection_id, client_login);
CREATE INDEX IF NOT EXISTS idx_direct_user
    ON direct_accounts (user_id);

-- ---------------------------------------------------------------------------
-- Кампании Директа
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS direct_campaigns (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
    campaign_id                 BIGINT NOT NULL,
    name                        TEXT NOT NULL DEFAULT '',
    campaign_type               TEXT NOT NULL DEFAULT '',
    state                       TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL DEFAULT '',
    strategy                    TEXT NOT NULL DEFAULT '',
    daily_budget_kopecks        BIGINT,
    negative_keywords           TEXT NOT NULL DEFAULT '[]',
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS campaign_type TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS strategy TEXT NOT NULL DEFAULT '';
ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS daily_budget_kopecks BIGINT;
ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS negative_keywords TEXT NOT NULL DEFAULT '[]';
ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_campaigns_unique
    ON direct_campaigns (account_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_direct_campaigns_user
    ON direct_campaigns (user_id);
CREATE INDEX IF NOT EXISTS idx_direct_campaigns_state
    ON direct_campaigns (user_id, state);

-- ---------------------------------------------------------------------------
-- Группы объявлений Директа
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS direct_adgroups (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
    campaign_row_id             BIGINT NOT NULL REFERENCES direct_campaigns (id) ON DELETE CASCADE,
    adgroup_id                  BIGINT NOT NULL,
    name                        TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL DEFAULT '',
    type                        TEXT NOT NULL DEFAULT '',
    region_ids                  TEXT NOT NULL DEFAULT '',
    negative_keywords           TEXT NOT NULL DEFAULT '[]',
    period_impressions          BIGINT NOT NULL DEFAULT 0,
    period_clicks               BIGINT NOT NULL DEFAULT 0,
    period_cost_kopecks         BIGINT NOT NULL DEFAULT 0,
    period_conversions          BIGINT NOT NULL DEFAULT 0,
    stats_from                  DATE,
    stats_to                    DATE,
    stats_synced_at             TIMESTAMPTZ,
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS negative_keywords TEXT NOT NULL DEFAULT '[]';
ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS period_impressions BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS period_clicks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS period_cost_kopecks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS period_conversions BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS stats_from DATE;
ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS stats_to DATE;
ALTER TABLE direct_adgroups ADD COLUMN IF NOT EXISTS stats_synced_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_adgroups_unique
    ON direct_adgroups (account_id, adgroup_id);
CREATE INDEX IF NOT EXISTS idx_direct_adgroups_campaign
    ON direct_adgroups (campaign_row_id);
CREATE INDEX IF NOT EXISTS idx_direct_adgroups_user
    ON direct_adgroups (user_id);

-- ---------------------------------------------------------------------------
-- Объявления Директа
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS direct_ads (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
    adgroup_row_id              BIGINT NOT NULL REFERENCES direct_adgroups (id) ON DELETE CASCADE,
    ad_id                       BIGINT NOT NULL,
    type                        TEXT NOT NULL DEFAULT '',
    state                       TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL DEFAULT '',
    title                       TEXT NOT NULL DEFAULT '',
    title2                      TEXT NOT NULL DEFAULT '',
    text                        TEXT NOT NULL DEFAULT '',
    href                        TEXT NOT NULL DEFAULT '',
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_ads_unique
    ON direct_ads (account_id, ad_id);
CREATE INDEX IF NOT EXISTS idx_direct_ads_adgroup
    ON direct_ads (adgroup_row_id);
CREATE INDEX IF NOT EXISTS idx_direct_ads_user
    ON direct_ads (user_id);

-- ---------------------------------------------------------------------------
-- Ключевые фразы Директа
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS direct_keywords (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
    adgroup_row_id              BIGINT NOT NULL REFERENCES direct_adgroups (id) ON DELETE CASCADE,
    keyword_id                  BIGINT NOT NULL,
    keyword                     TEXT NOT NULL DEFAULT '',
    state                       TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL DEFAULT '',
    bid_micros                  BIGINT,
    period_impressions          BIGINT NOT NULL DEFAULT 0,
    period_clicks               BIGINT NOT NULL DEFAULT 0,
    period_cost_kopecks         BIGINT NOT NULL DEFAULT 0,
    period_conversions          BIGINT NOT NULL DEFAULT 0,
    stats_from                  DATE,
    stats_to                    DATE,
    stats_synced_at             TIMESTAMPTZ,
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE direct_keywords ADD COLUMN IF NOT EXISTS period_impressions BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_keywords ADD COLUMN IF NOT EXISTS period_clicks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_keywords ADD COLUMN IF NOT EXISTS period_cost_kopecks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_keywords ADD COLUMN IF NOT EXISTS period_conversions BIGINT NOT NULL DEFAULT 0;
ALTER TABLE direct_keywords ADD COLUMN IF NOT EXISTS stats_from DATE;
ALTER TABLE direct_keywords ADD COLUMN IF NOT EXISTS stats_to DATE;
ALTER TABLE direct_keywords ADD COLUMN IF NOT EXISTS stats_synced_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_direct_keywords_unique
    ON direct_keywords (account_id, keyword_id);
CREATE INDEX IF NOT EXISTS idx_direct_keywords_adgroup
    ON direct_keywords (adgroup_row_id);
CREATE INDEX IF NOT EXISTS idx_direct_keywords_user
    ON direct_keywords (user_id);

-- ---------------------------------------------------------------------------
-- Поисковые запросы (агрегат из Reports API)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS direct_search_queries (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
    campaign_row_id             BIGINT NOT NULL REFERENCES direct_campaigns (id) ON DELETE CASCADE,
    adgroup_row_id              BIGINT REFERENCES direct_adgroups (id) ON DELETE SET NULL,
    adgroup_id                  BIGINT,
    query                       TEXT NOT NULL DEFAULT '',
    matched_keyword             TEXT NOT NULL DEFAULT '',
    impressions                 BIGINT NOT NULL DEFAULT 0,
    clicks                      BIGINT NOT NULL DEFAULT 0,
    cost_kopecks                BIGINT NOT NULL DEFAULT 0,
    conversions                 BIGINT NOT NULL DEFAULT 0,
    date_from                   DATE,
    date_to                     DATE,
    synced_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_direct_search_queries_campaign
    ON direct_search_queries (campaign_row_id);
CREATE INDEX IF NOT EXISTS idx_direct_search_queries_user
    ON direct_search_queries (user_id);
CREATE INDEX IF NOT EXISTS idx_direct_search_queries_clicks
    ON direct_search_queries (campaign_row_id, clicks DESC);

-- ---------------------------------------------------------------------------
-- Связь аккаунт Директа ↔ счётчик Метрики
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_counter_links (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
    counter_row_id              BIGINT NOT NULL REFERENCES metrika_counters (id) ON DELETE CASCADE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_account_counter_links_unique
    ON account_counter_links (account_id, counter_row_id);
CREATE INDEX IF NOT EXISTS idx_account_counter_links_user
    ON account_counter_links (user_id);

-- ---------------------------------------------------------------------------
-- Дневная статистика
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats_daily (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    day                         DATE NOT NULL,
    source                      TEXT NOT NULL,
    account_id                  BIGINT REFERENCES direct_accounts (id) ON DELETE CASCADE,
    campaign_id                 BIGINT,
    counter_row_id              BIGINT REFERENCES metrika_counters (id) ON DELETE CASCADE,
    impressions                 BIGINT NOT NULL DEFAULT 0,
    clicks                      BIGINT NOT NULL DEFAULT 0,
    cost_kopecks                BIGINT NOT NULL DEFAULT 0,
    conversions                 BIGINT NOT NULL DEFAULT 0,
    visits                      BIGINT NOT NULL DEFAULT 0,
    users_count                 BIGINT NOT NULL DEFAULT 0,
    bounce_rate                 NUMERIC(8, 4),
    goal_reaches                BIGINT NOT NULL DEFAULT 0,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS impressions BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS clicks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS cost_kopecks BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS conversions BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS visits BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS users_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS bounce_rate NUMERIC(8, 4);
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS goal_reaches BIGINT NOT NULL DEFAULT 0;
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE stats_daily ADD COLUMN IF NOT EXISTS goal_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_stats_daily_direct_unique
    ON stats_daily (user_id, day, source, account_id, (COALESCE(campaign_id, 0)))
    WHERE source = 'direct';
CREATE UNIQUE INDEX IF NOT EXISTS idx_stats_daily_metrika_unique
    ON stats_daily (user_id, day, source, (COALESCE(counter_row_id, 0)))
    WHERE source = 'metrika';
CREATE UNIQUE INDEX IF NOT EXISTS idx_stats_daily_metrika_goal_unique
    ON stats_daily (
        user_id, day, source,
        (COALESCE(counter_row_id, 0)),
        (COALESCE(goal_id, 0)),
        (COALESCE(campaign_id, 0))
    )
    WHERE source = 'metrika_goal';
CREATE INDEX IF NOT EXISTS idx_stats_daily_user_day
    ON stats_daily (user_id, day DESC);

-- ---------------------------------------------------------------------------
-- Прогоны синка
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_runs (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind                        TEXT NOT NULL DEFAULT '',
    target_id                   BIGINT,
    status                      TEXT NOT NULL DEFAULT 'running',
    error                       TEXT NOT NULL DEFAULT '',
    meta                        TEXT NOT NULL DEFAULT '',
    started_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at                 TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_user
    ON sync_runs (user_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- Лог действий (массовые операции)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS action_logs (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    action                      TEXT NOT NULL DEFAULT '',
    payload                     TEXT NOT NULL DEFAULT '',
    result                      TEXT NOT NULL DEFAULT '',
    status                      TEXT NOT NULL DEFAULT '',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_action_logs_user
    ON action_logs (user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Правила автоматизации
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS automation_rules (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name                        TEXT NOT NULL DEFAULT '',
    enabled                     BOOLEAN NOT NULL DEFAULT TRUE,
    dry_run                     BOOLEAN NOT NULL DEFAULT TRUE,
    scope_account_id            BIGINT REFERENCES direct_accounts (id) ON DELETE CASCADE,
    rule_type                   TEXT NOT NULL DEFAULT '',
    threshold_value             NUMERIC(18, 4) NOT NULL DEFAULT 0,
    lookback_days               INT NOT NULL DEFAULT 7,
    action                      TEXT NOT NULL DEFAULT 'pause',
    cooldown_hours              INT NOT NULL DEFAULT 24,
    last_run_at                 TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS dry_run BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS scope_account_id BIGINT;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS rule_type TEXT NOT NULL DEFAULT '';
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS threshold_value NUMERIC(18, 4) NOT NULL DEFAULT 0;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS lookback_days INT NOT NULL DEFAULT 7;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'pause';
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS cooldown_hours INT NOT NULL DEFAULT 24;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMPTZ;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS goal_row_id BIGINT;
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS action_budget_rub NUMERIC(18, 2);
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS target TEXT NOT NULL DEFAULT 'campaign';
ALTER TABLE automation_rules ADD COLUMN IF NOT EXISTS action_bid_rub NUMERIC(18, 2);

CREATE INDEX IF NOT EXISTS idx_automation_rules_user
    ON automation_rules (user_id);

CREATE TABLE IF NOT EXISTS rule_runs (
    id                          BIGSERIAL PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    rule_id                     BIGINT NOT NULL REFERENCES automation_rules (id) ON DELETE CASCADE,
    dry_run                     BOOLEAN NOT NULL DEFAULT TRUE,
    matched                     INT NOT NULL DEFAULT 0,
    applied                     INT NOT NULL DEFAULT 0,
    details                     TEXT NOT NULL DEFAULT '',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rule_runs_user
    ON rule_runs (user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- OAuth state
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
-- Сброс пароля
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token                       TEXT PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at                  TIMESTAMPTZ NOT NULL,
    used_at                     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_password_reset_user
    ON password_reset_tokens (user_id);
CREATE INDEX IF NOT EXISTS idx_password_reset_expires
    ON password_reset_tokens (expires_at);

-- ---------------------------------------------------------------------------
-- Привязка Telegram
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telegram_link_codes (
    code                        TEXT PRIMARY KEY,
    user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at                  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telegram_link_user
    ON telegram_link_codes (user_id);
CREATE INDEX IF NOT EXISTS idx_telegram_link_expires
    ON telegram_link_codes (expires_at);

-- ---------------------------------------------------------------------------
-- Настройки
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    key                         TEXT PRIMARY KEY,
    value                       TEXT NOT NULL DEFAULT ''
);
