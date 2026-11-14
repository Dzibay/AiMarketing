"""Цена кэшированного входа у классов моделей и число кэшированных токенов в журнале расхода

Провайдеры берут за повторяющийся префикс запроса (инструменты, промпт, история) в 10–20 раз меньше, чем за
обычный вход. Без отдельной цены весь вход списывался по полной — в 3–4 раза дороже реального расхода.
Пусто в units_cache / cost_cache — кэш считается как обычный вход (как было до этой миграции).

Revision ID: 0040_ai_cache_price
Revises: 0039_user_ui
Create Date: 2026-11-14
"""

from alembic import op

revision = "0040_ai_cache_price"
down_revision = "0039_user_ui"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_model_classes ADD COLUMN units_cache NUMERIC(14, 4), ADD COLUMN cost_cache NUMERIC(12, 2)")
    # Сколько из tokens_in пришло из кэша провайдера (tokens_in их уже включает).
    op.execute("ALTER TABLE ai_usage ADD COLUMN tokens_cached INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_usage DROP COLUMN IF EXISTS tokens_cached")
    op.execute("ALTER TABLE ai_model_classes DROP COLUMN IF EXISTS units_cache, DROP COLUMN IF EXISTS cost_cache")
