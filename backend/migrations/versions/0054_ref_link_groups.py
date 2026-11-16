"""Группы реф-ссылок и клики по дням

ref_link_groups — папки ссылок в админке (заполняются на этапе «Реф-ссылки»); ref_links.group_id.
ref_link_clicks_daily — ссылка × день по Москве: сколько первых приходов браузера. Пишется с момента
включения; старый общий счётчик ref_links.visits остаётся для «за всё время».

Revision ID: 0054_ref_link_groups
Revises: 0053_site_visits
Create Date: 2026-11-16
"""

from alembic import op

revision = "0054_ref_link_groups"
down_revision = "0053_site_visits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ref_link_groups (
            id                          SERIAL PRIMARY KEY,
            name                        TEXT NOT NULL,
            position                    INTEGER NOT NULL DEFAULT 0,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("ALTER TABLE ref_links ADD COLUMN group_id INTEGER REFERENCES ref_link_groups (id) ON DELETE SET NULL")
    op.execute(
        """
        CREATE TABLE ref_link_clicks_daily (
            link_id                     INTEGER NOT NULL REFERENCES ref_links (id) ON DELETE CASCADE,
            day                         DATE NOT NULL,
            clicks                      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (link_id, day)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ref_link_clicks_daily")
    op.execute("ALTER TABLE ref_links DROP COLUMN IF EXISTS group_id")
    op.execute("DROP TABLE IF EXISTS ref_link_groups")
