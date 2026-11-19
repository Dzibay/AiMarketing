"""Колокольчик: «Повторить» у записи о сбое долгой операции

notifications.retry — {"job_id", "label"}: упавшая или частично прошедшая задача, которую можно запустить снова
(«Повторить для оставшихся» / «Повторить»). NULL — кнопки нет (или уже повторили).

Revision ID: 0066_operation_notifications
Revises: 0065_problem_snapshots
Create Date: 2026-11-19
"""

from alembic import op

revision = "0066_operation_notifications"
down_revision = "0065_problem_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS retry JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS retry")
