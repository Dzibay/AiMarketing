"""Заходы и действия пользователей по дням

users.last_seen_at — последний заход в кабинет (пишется не чаще раза в 5 минут).
user_activity_days — пользователь × день по Москве: заходов (запросы кабинета, не чаще раза в 5 минут),
действий (вопрос ИИ, выполненное предложение, созданный проект, правка кампании), первое и последнее время.
Фоновые задачи активностью не считаются.

История до включения учёта восстанавливается приблизительно — по журналу событий, вопросам ИИ и правкам
кампаний; день с любым из них = один заход. Дата включения — app_settings.activity_tracked_since:
раньше неё на графиках граница «приблизительно».

Revision ID: 0052_activity
Revises: 0051_ai_reserves
Create Date: 2026-11-16
"""

from alembic import op

revision = "0052_activity"
down_revision = "0051_ai_reserves"
branch_labels = None
depends_on = None

MSK = "Europe/Moscow"


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMPTZ")
    op.execute(
        """
        CREATE TABLE user_activity_days (
            user_id                     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            day                         DATE NOT NULL,
            visits                      INTEGER NOT NULL DEFAULT 0,
            actions                     INTEGER NOT NULL DEFAULT 0,
            first_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, day)
        )
        """
    )
    op.execute("CREATE INDEX idx_user_activity_days_day ON user_activity_days (day)")

    # Приблизительная история: события пользователя, его вопросы в чатах ИИ и его правки кампаний.
    op.execute(
        f"""
        WITH src AS (
            SELECT user_id, created_at,
                   kind IN ('ai_question', 'action_applied', 'project_created') AS is_action
            FROM user_events
            UNION ALL
            SELECT c.user_id, m.created_at, FALSE
            FROM ai_messages m JOIN ai_chats c ON c.id = m.chat_id
            WHERE m.role = 'user'
            UNION ALL
            SELECT user_id, created_at, TRUE FROM action_logs WHERE source = 'user'
        )
        INSERT INTO user_activity_days (user_id, day, visits, actions, first_at, last_at)
        SELECT user_id, (created_at AT TIME ZONE '{MSK}')::date, 1,
               COUNT(*) FILTER (WHERE is_action), MIN(created_at), MAX(created_at)
        FROM src
        GROUP BY user_id, (created_at AT TIME ZONE '{MSK}')::date
        """
    )
    op.execute(
        """
        UPDATE users u SET last_seen_at = a.last_at
        FROM (SELECT user_id, MAX(last_at) AS last_at FROM user_activity_days GROUP BY user_id) a
        WHERE a.user_id = u.id
        """
    )
    op.execute(
        f"""
        INSERT INTO app_settings (key, value)
        VALUES ('activity_tracked_since', (NOW() AT TIME ZONE '{MSK}')::date::text)
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'activity_tracked_since'")
    op.execute("DROP TABLE IF EXISTS user_activity_days")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_seen_at")
