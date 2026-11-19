"""Утренний разбор «Где горит»: снимок проблем за день

problem_snapshots — какие проблемы (их устойчивые ключи, как у скрытий) были у пользователя в этот
день по московскому времени. Одна строка на пользователя и день, перезаписывается при каждом
открытии обзора; завтра сводка сравнивается с последним снимком: что нового, что решилось.

Revision ID: 0065_problem_snapshots
Revises: 0064_budget_protection
Create Date: 2026-11-19
"""

from alembic import op

revision = "0065_problem_snapshots"
down_revision = "0064_budget_protection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE problem_snapshots (
            user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day        DATE NOT NULL,
            keys       TEXT[] NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, day)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS problem_snapshots")
