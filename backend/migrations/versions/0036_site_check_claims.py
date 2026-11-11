"""Проверка сайта с лендинга переезжает в кабинет после регистрации (docs/landing-plan.md, раздел 5.3).

site_check_claims — какой пользователь забрал какую проверку и в какие чаты она легла. Одна проверка по домену
кэшируется для всех посетителей, поэтому сама проверка пользователю не принадлежит — у каждого своя запись.
chat_id — первый чат с результатом (общий или проекта); project_chat_id — чат проекта этого сайта, где из
проверки собирают черновик кампании (появляется сразу или позже, когда подключат проект с этим сайтом).

Revision ID: 0036_site_check_claims
Revises: 0035_site_checks
Create Date: 2026-11-11
"""

from alembic import op

revision = "0036_site_check_claims"
down_revision = "0035_site_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE site_check_claims (
            check_id                    TEXT NOT NULL REFERENCES site_checks (id) ON DELETE CASCADE,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            domain                      TEXT NOT NULL,
            chat_id                     BIGINT REFERENCES ai_chats (id) ON DELETE SET NULL,
            project_chat_id             BIGINT REFERENCES ai_chats (id) ON DELETE SET NULL,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (check_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_site_check_claims_user ON site_check_claims (user_id, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS site_check_claims")
