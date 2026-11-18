"""Свои параметры ссылки у кампании

Шаблон ссылки сайта задаёт ref для всех кампаний (ref=Yandex). Маркетолог может дать кампании
ссылку с другим ref — тогда его спрашивают, какой оставить, и ответ хранится здесь:
link_params = {"own": {"ref": "Telegram"}, "asked": {"ref": ["Telegram", "Yandex"]}}.
own — значения вместо шаблонных (создание объявлений и проверка ссылок считают их правильными),
asked — о каких значениях уже спрашивали, чтобы не спрашивать снова.

Revision ID: 0057_campaign_link_params
Revises: 0056_admin_indexes
Create Date: 2026-11-16
"""

from alembic import op

revision = "0057_campaign_link_params"
down_revision = "0056_admin_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE direct_campaigns ADD COLUMN link_params JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE direct_campaigns DROP COLUMN IF EXISTS link_params")
