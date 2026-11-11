"""Сайт: лист ожидания со страниц «скоро» и запуски ИИ-генераторов (docs/landing-plan.md).

site_waitlist — почта и канал (vk-reklama, ozon, wildberries); одна почта на канал один раз. IP не храним —
только хэш с секретом сервера, для лимита заявок с одного адреса.
site_tool_runs — запуски ИИ-режима генераторов минус-слов и объявлений: только для суточных лимитов.

Revision ID: 0037_site_waitlist_tools
Revises: 0036_site_check_claims
Create Date: 2026-11-12
"""

from alembic import op

revision = "0037_site_waitlist_tools"
down_revision = "0036_site_check_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE site_waitlist (
            id          BIGSERIAL PRIMARY KEY,
            channel     TEXT NOT NULL,
            email       TEXT NOT NULL,
            ip_hash     TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (channel, email)
        )
        """
    )
    op.execute("CREATE INDEX idx_site_waitlist_ip ON site_waitlist (ip_hash, created_at)")
    op.execute(
        """
        CREATE TABLE site_tool_runs (
            id          BIGSERIAL PRIMARY KEY,
            tool        TEXT NOT NULL,
            ip_hash     TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX idx_site_tool_runs_day ON site_tool_runs (created_at, ip_hash)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS site_tool_runs")
    op.execute("DROP TABLE IF EXISTS site_waitlist")
