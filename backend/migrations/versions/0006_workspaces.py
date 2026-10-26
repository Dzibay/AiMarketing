"""Рабочие пространства (счётчик Метрики + кампании), роли подключений, песочница, ИИ-чат.

Revision ID: 0006_workspaces
Revises: 0005_negative_lists
Create Date: 2026-10-26
"""

from alembic import op

revision = "0006_workspaces"
down_revision = "0005_negative_lists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Подключение Яндекса — источник Метрики, Директа или обоих; песочница — отдельная запись
    # того же логина (тот же uid), поэтому уникальность учитывает флаг.
    op.execute(
        """
        ALTER TABLE yandex_connections
            ADD COLUMN use_metrika  BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN use_direct   BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN sandbox      BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_yandex_conn_user_uid")
    op.execute("CREATE UNIQUE INDEX idx_yandex_conn_user_uid ON yandex_connections (user_id, yandex_uid, sandbox)")
    # Что подключаем этим входом: metrika | direct | sandbox | '' (всё).
    op.execute("ALTER TABLE oauth_states ADD COLUMN mode TEXT NOT NULL DEFAULT ''")

    # Счётчики из настроек кампании (CounterIds); NULL — ещё не читали.
    op.execute("ALTER TABLE direct_campaigns ADD COLUMN counter_ids JSONB")
    op.execute("ALTER TABLE direct_campaigns ADD COLUMN start_date DATE")

    op.execute(
        """
        CREATE TABLE workspaces (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            -- Папка-клиент; у клиента может быть несколько сайтов.
            client_row_id               BIGINT REFERENCES clients (id) ON DELETE SET NULL,
            -- Номер счётчика Метрики; строка счётчика — через какое подключение его читаем.
            counter_id                  BIGINT NOT NULL,
            counter_row_id              BIGINT REFERENCES metrika_counters (id) ON DELETE SET NULL,
            name                        TEXT NOT NULL DEFAULT '',
            site                        TEXT NOT NULL DEFAULT '',
            kpi_type                    TEXT NOT NULL DEFAULT ''
                                        CHECK (kpi_type IN ('', 'cpa', 'drr')),
            kpi_target                  NUMERIC(18, 4),
            monthly_budget_kopecks      BIGINT,
            -- Основная цель Метрики: конверсии, CPA и подсказки ИИ считаются по ней.
            goal_id                     BIGINT,
            -- Контекст бизнеса для ИИ: что продаём, кому, ограничения.
            ai_notes                    TEXT NOT NULL DEFAULT '',
            ai_access                   JSONB NOT NULL DEFAULT '{}'::jsonb,
            archived_at                 TIMESTAMPTZ,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, counter_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_workspaces_client ON workspaces (client_row_id)")

    op.execute(
        """
        CREATE TABLE workspace_accounts (
            workspace_id                BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (workspace_id, account_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_workspace_accounts_account ON workspace_accounts (account_id)")

    # Кампания — не больше чем в одном пространстве: её статистика и цели считаются по одному сайту.
    op.execute(
        """
        CREATE TABLE workspace_campaigns (
            workspace_id                BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            campaign_row_id             BIGINT NOT NULL UNIQUE REFERENCES direct_campaigns (id) ON DELETE CASCADE,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (workspace_id, campaign_row_id)
        )
        """
    )

    op.execute(
        """
        ALTER TABLE action_logs
            ADD COLUMN workspace_id     BIGINT REFERENCES workspaces (id) ON DELETE SET NULL,
            -- Кто инициировал: user | ai | rule.
            ADD COLUMN source           TEXT NOT NULL DEFAULT 'user',
            ADD COLUMN title            TEXT NOT NULL DEFAULT ''
        """
    )
    op.execute("CREATE INDEX idx_action_logs_workspace ON action_logs (workspace_id, created_at DESC)")
    # Правило можно ограничить кампаниями одного пространства.
    op.execute(
        "ALTER TABLE automation_rules ADD COLUMN scope_workspace_id BIGINT "
        "REFERENCES workspaces (id) ON DELETE CASCADE"
    )

    # ИИ: ключ пользователя (BYOK), чаты пространства, сообщения и действия на согласовании.
    op.execute(
        """
        CREATE TABLE ai_settings (
            user_id                     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            provider                    TEXT NOT NULL DEFAULT 'openai'
                                        CHECK (provider IN ('openai', 'yandexgpt')),
            base_url                    TEXT NOT NULL DEFAULT '',
            model                       TEXT NOT NULL DEFAULT '',
            folder_id                   TEXT NOT NULL DEFAULT '',
            api_key_enc                 TEXT NOT NULL DEFAULT '',
            temperature                 NUMERIC(3, 2) NOT NULL DEFAULT 0.3,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ai_chats (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id                BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            title                       TEXT NOT NULL DEFAULT '',
            -- idle | running | waiting (ждёт решений по действиям) | error
            status                      TEXT NOT NULL DEFAULT 'idle',
            error                       TEXT NOT NULL DEFAULT '',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_chats_workspace ON ai_chats (workspace_id, updated_at DESC)")
    op.execute(
        """
        CREATE TABLE ai_messages (
            id                          BIGSERIAL PRIMARY KEY,
            chat_id                     BIGINT NOT NULL REFERENCES ai_chats (id) ON DELETE CASCADE,
            role                        TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
            content                     TEXT NOT NULL DEFAULT '',
            tool_calls                  JSONB,
            tool_call_id                TEXT NOT NULL DEFAULT '',
            tool_name                   TEXT NOT NULL DEFAULT '',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_messages_chat ON ai_messages (chat_id, id)")
    op.execute(
        """
        CREATE TABLE ai_actions (
            id                          BIGSERIAL PRIMARY KEY,
            chat_id                     BIGINT NOT NULL REFERENCES ai_chats (id) ON DELETE CASCADE,
            message_id                  BIGINT NOT NULL REFERENCES ai_messages (id) ON DELETE CASCADE,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            tool_call_id                TEXT NOT NULL DEFAULT '',
            tool                        TEXT NOT NULL DEFAULT '',
            args                        JSONB NOT NULL DEFAULT '{}'::jsonb,
            summary                     TEXT NOT NULL DEFAULT '',
            category                    TEXT NOT NULL DEFAULT '',
            -- pending | approved | rejected | done | error
            status                      TEXT NOT NULL DEFAULT 'pending',
            auto                        BOOLEAN NOT NULL DEFAULT FALSE,
            result                      JSONB,
            error                       TEXT NOT NULL DEFAULT '',
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at                  TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_actions_chat ON ai_actions (chat_id, id)")

    # --- Перенос данных -------------------------------------------------------
    # Пространство на каждый номер счётчика; папка и KPI — от клиента счётчика.
    op.execute(
        """
        INSERT INTO workspaces (
            user_id, client_row_id, counter_id, counter_row_id, name, site,
            kpi_type, kpi_target, created_at
        )
        SELECT DISTINCT ON (m.user_id, m.counter_id)
            m.user_id, m.client_row_id, m.counter_id, m.id,
            COALESCE(NULLIF(m.name, ''), NULLIF(m.site, ''), 'Счётчик ' || m.counter_id),
            m.site,
            COALESCE(c.kpi_type, ''), c.kpi_target, NOW()
        FROM metrika_counters m
        LEFT JOIN clients c ON c.id = m.client_row_id
        ORDER BY m.user_id, m.counter_id, m.enabled DESC, (m.client_row_id IS NULL), m.id
        """
    )
    op.execute(
        """
        INSERT INTO workspace_accounts (workspace_id, account_id, user_id)
        SELECT DISTINCT w.id, l.account_id, l.user_id
        FROM account_counter_links l
        JOIN metrika_counters m ON m.id = l.counter_row_id
        JOIN workspaces w ON w.user_id = m.user_id AND w.counter_id = m.counter_id
        """
    )
    # Кабинет только одного пространства — все его кампании туда.
    op.execute(
        """
        INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id)
        SELECT s.workspace_id, c.id, c.user_id
        FROM direct_campaigns c
        JOIN (
            SELECT account_id, MIN(workspace_id) AS workspace_id
            FROM workspace_accounts
            GROUP BY account_id
            HAVING COUNT(*) = 1
        ) s ON s.account_id = c.account_id
        """
    )
    op.execute("DROP TABLE account_counter_links")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE account_counter_links (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            account_id                  BIGINT NOT NULL REFERENCES direct_accounts (id) ON DELETE CASCADE,
            counter_row_id              BIGINT NOT NULL REFERENCES metrika_counters (id) ON DELETE CASCADE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_account_counter_links_unique ON account_counter_links (account_id, counter_row_id)"
    )
    op.execute("CREATE INDEX idx_account_counter_links_user ON account_counter_links (user_id)")
    op.execute(
        """
        INSERT INTO account_counter_links (user_id, account_id, counter_row_id)
        SELECT wa.user_id, wa.account_id, w.counter_row_id
        FROM workspace_accounts wa
        JOIN workspaces w ON w.id = wa.workspace_id
        WHERE w.counter_row_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    op.execute("DROP TABLE IF EXISTS ai_actions")
    op.execute("DROP TABLE IF EXISTS ai_messages")
    op.execute("DROP TABLE IF EXISTS ai_chats")
    op.execute("DROP TABLE IF EXISTS ai_settings")
    op.execute(
        """
        ALTER TABLE action_logs
            DROP COLUMN IF EXISTS title,
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS workspace_id
        """
    )
    op.execute("ALTER TABLE automation_rules DROP COLUMN IF EXISTS scope_workspace_id")
    op.execute("DROP TABLE IF EXISTS workspace_campaigns")
    op.execute("DROP TABLE IF EXISTS workspace_accounts")
    op.execute("DROP TABLE IF EXISTS workspaces")
    op.execute("ALTER TABLE direct_campaigns DROP COLUMN IF EXISTS start_date")
    op.execute("ALTER TABLE direct_campaigns DROP COLUMN IF EXISTS counter_ids")
    op.execute("ALTER TABLE oauth_states DROP COLUMN IF EXISTS mode")
    op.execute("DELETE FROM yandex_connections WHERE sandbox")
    op.execute("DROP INDEX IF EXISTS idx_yandex_conn_user_uid")
    op.execute("CREATE UNIQUE INDEX idx_yandex_conn_user_uid ON yandex_connections (user_id, yandex_uid)")
    op.execute(
        """
        ALTER TABLE yandex_connections
            DROP COLUMN IF EXISTS sandbox,
            DROP COLUMN IF EXISTS use_direct,
            DROP COLUMN IF EXISTS use_metrika
        """
    )
