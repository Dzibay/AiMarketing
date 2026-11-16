"""Резервные модели классов и след подмены в журнале расхода

ai_model_classes.reserves — запасные модели класса по порядку: [{model, vision, cost_in, cost_out, cost_cache}].
Провайдер не отдал модель класса (нет модели, кончились деньги, регион) — ход идёт резервной, потом
моделями других классов. Пользователь платит по цене выбранного класса, себестоимость — модели, которая ответила.

ai_usage.fallback_from / fallback_reason — вызов сделала запасная: какая модель была назначена и почему
её заменили. Видно только в админке.

Revision ID: 0051_ai_reserves
Revises: 0050_ai_action_started
Create Date: 2026-11-16
"""

from alembic import op

revision = "0051_ai_reserves"
down_revision = "0050_ai_action_started"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_model_classes ADD COLUMN reserves JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute(
        "ALTER TABLE ai_usage ADD COLUMN fallback_from TEXT NOT NULL DEFAULT '', "
        "ADD COLUMN fallback_reason TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ai_usage DROP COLUMN IF EXISTS fallback_from, DROP COLUMN IF EXISTS fallback_reason")
    op.execute("ALTER TABLE ai_model_classes DROP COLUMN IF EXISTS reserves")
