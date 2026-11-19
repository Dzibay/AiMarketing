"""Причина отказа от предложения ИИ

decline_reason — что пользователь сказал при «Сделать иначе» (быстрая причина и «как надо»). Читают ход ИИ
сразу после отказа и выжимка прошлых решений в новом чате проекта.

Revision ID: 0062_decisions_undo
Revises: 0061_urgent_and_snoozes
Create Date: 2026-11-19
"""

from alembic import op

revision = "0062_decisions_undo"
down_revision = "0061_urgent_and_snoozes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_actions ADD COLUMN decline_reason TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_actions DROP COLUMN IF EXISTS decline_reason")
