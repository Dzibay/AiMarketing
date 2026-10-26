"""Базовая схема: снимок бывшего database/init.sql.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-26
"""

from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql" / "0001_baseline.sql"


def upgrade() -> None:
    # Файл из многих команд: psycopg выполняет такой текст только без параметров,
    # поэтому идём мимо SQLAlchemy прямо в курсор драйвера (та же транзакция).
    sql = _SQL.read_text(encoding="utf-8")
    cursor = op.get_bind().connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def downgrade() -> None:
    raise NotImplementedError("Базовую схему не откатываем — пересоздайте БД")
