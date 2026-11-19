"""«Для новых проектов»: настройки по умолчанию пользователя

users.project_defaults — {"url_template": "/?utm_source=…" (без домена), "model_class": "", "ai_access": {…} | null,
"kpi": {"kpi_type", "kpi_target"} | null, "budget_protection": {key: {"enabled", "threshold_kind", "value"}} | null}.
Применяются к каждому новому проекту (сайту).

Revision ID: 0067_project_defaults
Revises: 0066_operation_notifications
Create Date: 2026-11-19
"""

from alembic import op

revision = "0067_project_defaults"
down_revision = "0066_operation_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS project_defaults JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS project_defaults")
