"""Шаблон ссылки сайта, метки кампаний и загруженные отчёты по заголовкам.

Revision ID: 0015_url_template
Revises: 0014_rule_budget_pct
Create Date: 2026-10-28
"""

from alembic import op

revision = "0015_url_template"
down_revision = "0014_rule_budget_pct"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Эталонная ссылка сайта: адрес и ref — в объявление, utm — в метки кампании.
    op.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS url_template TEXT NOT NULL DEFAULT ''")
    # TrackingParams кампании из Директа; NULL — ещё не синхронизировали.
    op.execute("ALTER TABLE direct_campaigns ADD COLUMN IF NOT EXISTS tracking_params TEXT")
    # Таблица заголовков из Мастера отчётов: API Директа не отдаёт статистику по заголовкам.
    op.execute(
        """
        CREATE TABLE headline_reports (
            id                  BIGSERIAL PRIMARY KEY,
            user_id             BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            workspace_id        BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            campaign_row_id     BIGINT REFERENCES direct_campaigns (id) ON DELETE SET NULL,
            filename            TEXT NOT NULL DEFAULT '',
            rows                JSONB NOT NULL DEFAULT '[]'::jsonb,
            uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX headline_reports_ws_idx ON headline_reports (workspace_id, uploaded_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS headline_reports")
    op.execute("ALTER TABLE direct_campaigns DROP COLUMN IF EXISTS tracking_params")
    op.execute("ALTER TABLE workspaces DROP COLUMN IF EXISTS url_template")
