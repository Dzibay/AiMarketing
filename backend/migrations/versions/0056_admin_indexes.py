"""Индексы для дашборда и списков админки

Платежи по дате оплаты (успешные), пользователи по последнему заходу, подписки по дате
окончания («истекают за 7 дней»), визиты сайта по времени начала.

Revision ID: 0056_admin_indexes
Revises: 0055_ai_usage_source
Create Date: 2026-11-16
"""

from alembic import op

revision = "0056_admin_indexes"
down_revision = "0055_ai_usage_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX idx_payments_paid ON payments ((COALESCE(paid_at, created_at))) WHERE status = 'succeeded' AND NOT test"
    )
    # users (created_at) уже есть с базовой схемы (idx_users_created).
    op.execute("CREATE INDEX idx_users_last_seen ON users (last_seen_at)")
    op.execute("CREATE INDEX idx_subscriptions_ends ON subscriptions (ends_at) WHERE ends_at IS NOT NULL")
    op.execute("CREATE INDEX idx_site_visits_started ON site_visits (started_at)")


def downgrade() -> None:
    for name in ("idx_site_visits_started", "idx_subscriptions_ends", "idx_users_last_seen", "idx_payments_paid"):
        op.execute(f"DROP INDEX IF EXISTS {name}")
