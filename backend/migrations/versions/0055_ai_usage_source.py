"""Расход ИИ лендинга, чат и длительность вызова; журнал ошибок ИИ

ai_usage.user_id — необязательный: проверка сайта и генераторы на лендинге пишутся без пользователя,
source = 'landing'; всё из кабинета — 'cabinet'. Лимиты считаются по user_id, строки лендинга в них
не попадают. chat_id — чат, ради которого был вызов («самые дорогие чаты»); duration_ms — сколько
длился вызов вместе с повторами.

ai_errors — каждая неудачная попытка вызова модели: задача, модель, код и текст ошибки; retried — после
неё был повтор той же модели, fallback — ход перешёл на запасную; answered — кто-то в итоге ответил
(answered_by — какая модель). Хранится 90 дней.

Revision ID: 0055_ai_usage_source
Revises: 0054_ref_link_groups
Create Date: 2026-11-16
"""

from alembic import op

revision = "0055_ai_usage_source"
down_revision = "0054_ref_link_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ai_usage ALTER COLUMN user_id DROP NOT NULL")
    op.execute(
        """
        ALTER TABLE ai_usage
            ADD COLUMN source TEXT NOT NULL DEFAULT 'cabinet' CHECK (source IN ('cabinet', 'landing')),
            ADD COLUMN chat_id BIGINT REFERENCES ai_chats (id) ON DELETE SET NULL,
            ADD COLUMN duration_ms INTEGER
        """
    )
    op.execute("CREATE INDEX idx_ai_usage_chat ON ai_usage (chat_id) WHERE chat_id IS NOT NULL")
    op.execute(
        """
        CREATE TABLE ai_errors (
            id                          BIGSERIAL PRIMARY KEY,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id                     BIGINT REFERENCES users (id) ON DELETE SET NULL,
            source                      TEXT NOT NULL DEFAULT 'cabinet',
            task                        TEXT NOT NULL DEFAULT '',
            model                       TEXT NOT NULL DEFAULT '',
            model_class                 TEXT NOT NULL DEFAULT '',
            code                        TEXT NOT NULL DEFAULT '',
            message                     TEXT NOT NULL DEFAULT '',
            retried                     BOOLEAN NOT NULL DEFAULT FALSE,
            fallback                    BOOLEAN NOT NULL DEFAULT FALSE,
            answered                    BOOLEAN NOT NULL DEFAULT FALSE,
            answered_by                 TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute("CREATE INDEX idx_ai_errors_time ON ai_errors (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_errors")
    op.execute("DELETE FROM ai_usage WHERE user_id IS NULL")
    op.execute("DROP INDEX IF EXISTS idx_ai_usage_chat")
    op.execute(
        "ALTER TABLE ai_usage DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS chat_id, "
        "DROP COLUMN IF EXISTS duration_ms"
    )
    op.execute("ALTER TABLE ai_usage ALTER COLUMN user_id SET NOT NULL")
