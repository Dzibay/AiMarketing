"""Настройки интерфейса в аккаунте: роль, экспертный режим, просмотренные подсказки

Раньше режим лежал только в браузере и на телефоне сбрасывался. Теперь он в `users.ui` и едет за человеком
на любое устройство. Ключи и их значения проверяет app/users.py (`set_ui`).

Revision ID: 0039_user_ui
Revises: 0038_hidden_counters
Create Date: 2026-11-14
"""

from alembic import op

revision = "0039_user_ui"
down_revision = "0038_hidden_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN ui JSONB NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS ui")
