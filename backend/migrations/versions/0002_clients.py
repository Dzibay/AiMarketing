"""Клиенты фрилансера: таблица clients и привязка кабинетов Директа и счётчиков.

Revision ID: 0002_clients
Revises: 0001_baseline
Create Date: 2026-10-26
"""

from alembic import op

revision = "0002_clients"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE clients (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name                        TEXT NOT NULL DEFAULT '',
            site                        TEXT NOT NULL DEFAULT '',
            notes                       TEXT NOT NULL DEFAULT '',
            kpi_type                    TEXT NOT NULL DEFAULT ''
                                        CHECK (kpi_type IN ('', 'cpa', 'drr')),
            -- CPA — рубли за конверсию, ДРР — проценты.
            kpi_target                  NUMERIC(18, 4),
            monthly_budget_kopecks      BIGINT,
            archived_at                 TIMESTAMPTZ,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_clients_user ON clients (user_id, archived_at)")

    # direct_accounts.client_id — это ClientId Директа, поэтому внутренний FK — client_row_id.
    for table in ("direct_accounts", "metrika_counters"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN client_row_id BIGINT "
            "REFERENCES clients (id) ON DELETE SET NULL"
        )
        op.execute(f"CREATE INDEX idx_{table}_client ON {table} (client_row_id)")

    # Существующие кабинеты: по клиенту на кабинет.
    op.execute(
        """
        DO $$
        DECLARE
            acc RECORD;
            new_id BIGINT;
        BEGIN
            FOR acc IN
                SELECT id, user_id, name, client_login
                FROM direct_accounts
                WHERE client_row_id IS NULL
                ORDER BY id
            LOOP
                INSERT INTO clients (user_id, name)
                VALUES (
                    acc.user_id,
                    COALESCE(NULLIF(acc.name, ''), NULLIF(acc.client_login, ''), 'Клиент')
                )
                RETURNING id INTO new_id;
                UPDATE direct_accounts SET client_row_id = new_id WHERE id = acc.id;
            END LOOP;
        END $$
        """
    )
    # Счётчик, связанный с кабинетами ровно одного клиента, — к этому клиенту.
    op.execute(
        """
        UPDATE metrika_counters m
        SET client_row_id = s.client_row_id
        FROM (
            SELECT l.counter_row_id, MIN(d.client_row_id) AS client_row_id
            FROM account_counter_links l
            JOIN direct_accounts d ON d.id = l.account_id
            GROUP BY l.counter_row_id
            HAVING COUNT(DISTINCT d.client_row_id) = 1
        ) s
        WHERE m.id = s.counter_row_id AND m.client_row_id IS NULL
        """
    )
    # Логин клиента с одним кабинетом: его счётчики — тому же клиенту.
    op.execute(
        """
        UPDATE metrika_counters m
        SET client_row_id = s.client_row_id
        FROM (
            SELECT connection_id, MIN(client_row_id) AS client_row_id
            FROM direct_accounts
            GROUP BY connection_id
            HAVING COUNT(*) = 1
        ) s
        WHERE m.connection_id = s.connection_id AND m.client_row_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE metrika_counters DROP COLUMN IF EXISTS client_row_id")
    op.execute("ALTER TABLE direct_accounts DROP COLUMN IF EXISTS client_row_id")
    op.execute("DROP TABLE IF EXISTS clients")
