"""Удалённые из Толка сайты (docs/cabinet-polish-plan.md, фаза 3).

Скрытие хранится по номеру счётчика у пользователя, а не флагом в metrika_counters: строка того же
номера появляется снова — при синхронизации или через другой логин Яндекса. Скрытый счётчик выключен
во всех строках (enabled = FALSE), поэтому загрузчики его не трогают, а проект для него не создаётся.
name и site — снимок на момент удаления: список «Удалённые сайты» не зависит от подключений.
had_project — у сайта был действующий проект: «Вернуть» возвращает и проект, а счётчик, скрытый
из «Добавить сайт», просто снова предлагается там.

Revision ID: 0038_hidden_counters
Revises: 0037_site_waitlist_tools
Create Date: 2026-11-12
"""

from alembic import op

revision = "0038_hidden_counters"
down_revision = "0037_site_waitlist_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE hidden_counters (
            user_id     BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            counter_id  BIGINT NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            site        TEXT NOT NULL DEFAULT '',
            had_project BOOLEAN NOT NULL DEFAULT FALSE,
            hidden_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, counter_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS hidden_counters")
