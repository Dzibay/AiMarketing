"""Решать из Telegram: одноразовые коды кнопок и переключатель в настройках уведомлений

telegram_decision_tokens — короткий случайный код в callback_data кнопки (Telegram даёт 64 байта): чей он
(user_id и чат, куда ушло сообщение), что делает (kind), к какому предложению ИИ (action_id), сообщение
(message_id — его бот потом правит итогом), payload (исходный текст сообщения и т. п.), срок и отметка
использования. Код одноразовый: нажатие помечает used_at у него и у всех соседних кнопок того же сообщения.

users.telegram_decide — «Решать из Telegram», по умолчанию выключено.

Revision ID: 0072_telegram_decisions
Revises: 0071_report_brands
Create Date: 2026-11-19
"""

from alembic import op

revision = "0072_telegram_decisions"
down_revision = "0071_report_brands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_decide BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_decision_tokens (
            token       TEXT PRIMARY KEY,
            user_id     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            chat_id     TEXT NOT NULL,
            kind        TEXT NOT NULL,
            action_id   BIGINT REFERENCES ai_actions (id) ON DELETE CASCADE,
            message_id  BIGINT,
            payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL,
            used_at     TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS telegram_decision_tokens_action ON telegram_decision_tokens (action_id)")
    op.execute("CREATE INDEX IF NOT EXISTS telegram_decision_tokens_expires ON telegram_decision_tokens (expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS telegram_decision_tokens")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS telegram_decide")
