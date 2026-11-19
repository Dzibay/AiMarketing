"""Помогла ли правка: итог через 7 и 14 дней

action_effects — по строке на запись журнала и окно (7 или 14 дней): расход, клики, заявки до и после правки
по затронутым кампаниям, вердикт. Считает фоновая задача compute_effects (app.effects).
notifications.undo_log_id — у записи «После правки стало хуже» кнопка «Вернуть как было».

Revision ID: 0069_action_effects
Revises: 0068_shared_cleaning_memory
Create Date: 2026-11-19
"""

from alembic import op

revision = "0069_action_effects"
down_revision = "0068_shared_cleaning_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE action_effects (
            id                          BIGSERIAL PRIMARY KEY,
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            log_id                      BIGINT NOT NULL REFERENCES action_logs (id) ON DELETE CASCADE,
            -- Предложение ИИ, которое сделало правку (цель «ai_action:N» записи), если оно было.
            ai_action_id                BIGINT,
            workspace_id                BIGINT REFERENCES workspaces (id) ON DELETE SET NULL,
            campaign_row_ids            BIGINT[] NOT NULL DEFAULT '{}',
            applied_at                  TIMESTAMPTZ NOT NULL,
            window_days                 SMALLINT NOT NULL CHECK (window_days IN (7, 14)),
            before_cost_kopecks         BIGINT NOT NULL DEFAULT 0,
            before_clicks               BIGINT NOT NULL DEFAULT 0,
            before_conversions          BIGINT NOT NULL DEFAULT 0,
            after_cost_kopecks          BIGINT NOT NULL DEFAULT 0,
            after_clicks                BIGINT NOT NULL DEFAULT 0,
            after_conversions           BIGINT NOT NULL DEFAULT 0,
            -- Чем судили: cpa — цена заявки, conversions — заявки появились / пропали, cost — расход
            -- (минус-фразы без заявок), none — мало данных.
            metric                      TEXT NOT NULL DEFAULT 'none',
            change_pct                  REAL,
            verdict                     TEXT NOT NULL
                                        CHECK (verdict IN ('better', 'worse', 'neutral', 'low_data')),
            -- Минус-фразы: оценка расхода на отсечённые запросы за столько же дней до правки.
            cut_cost_kopecks            BIGINT,
            -- Другие правки тех же кампаний в эти же дни: итог совпал по времени не только с этой.
            others                      INT NOT NULL DEFAULT 0,
            computed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (log_id, window_days)
        )
        """
    )
    op.execute("CREATE INDEX idx_action_effects_user ON action_effects (user_id, computed_at DESC)")
    op.execute(
        "ALTER TABLE notifications ADD COLUMN undo_log_id BIGINT REFERENCES action_logs (id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS undo_log_id")
    op.execute("DROP TABLE IF EXISTS action_effects")
