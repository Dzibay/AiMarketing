"""Отчёт клиенту за месяц: оформление пользователя, набор разделов проекта и отчёты по месяцам

report_brands — одно оформление на пользователя: название, строка контактов, цвет акцента и логотип
(PNG, JPEG или SVG до 1 МБ, хранится в базе, как картинки чата).
report_settings — какие разделы отчёта включены в проекте: в следующем месяце тот же набор.
client_reports — план и комментарий специалиста за месяц и отметка «готово» (распечатали или нажали «Готово»).

Revision ID: 0071_report_brands
Revises: 0070_ai_review_schedules
Create Date: 2026-11-19
"""

from alembic import op

revision = "0071_report_brands"
down_revision = "0070_ai_review_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE report_brands (
            user_id     BIGINT PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
            name        TEXT NOT NULL DEFAULT '',
            contacts    TEXT NOT NULL DEFAULT '',
            accent      TEXT NOT NULL DEFAULT '',
            logo        BYTEA,
            logo_mime   TEXT NOT NULL DEFAULT '',
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE report_settings (
            workspace_id BIGINT PRIMARY KEY REFERENCES workspaces (id) ON DELETE CASCADE,
            user_id      BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            sections     JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE client_reports (
            workspace_id BIGINT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            month        DATE NOT NULL,
            user_id      BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            plan_text    TEXT,
            comment_text TEXT NOT NULL DEFAULT '',
            done_at      TIMESTAMPTZ,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, month)
        )
        """
    )
    op.execute("CREATE INDEX client_reports_user_month_idx ON client_reports (user_id, month)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS client_reports")
    op.execute("DROP TABLE IF EXISTS report_settings")
    op.execute("DROP TABLE IF EXISTS report_brands")
