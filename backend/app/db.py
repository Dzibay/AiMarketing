import logging
import time
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

log = logging.getLogger("app.db")

pool = ConnectionPool(
    settings.dsn,
    min_size=1,
    max_size=10,
    open=False,
    kwargs={"row_factory": dict_row},
)

_INIT_SQL_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "database" / "init.sql",
    Path(__file__).resolve().parents[2] / "database" / "init.sql",
)


def ensure_schema(retries: int = 10, delay: float = 2.0) -> None:
    """Открывает пул и накатывает идемпотентную схему, дожидаясь готовности БД."""
    sql = None
    for candidate in _INIT_SQL_CANDIDATES:
        if candidate.exists():
            sql = candidate.read_text(encoding="utf-8")
            break
    if sql is None:
        raise RuntimeError("database/init.sql не найден")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            pool.open()
            with pool.connection() as conn:
                conn.execute(sql)
            log.info("Схема БД готова")
            return
        except Exception as exc:  # noqa: BLE001 — ждём поднятия postgres
            last_error = exc
            log.warning("БД недоступна (попытка %d/%d): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError(f"Не удалось подключиться к БД: {last_error}")
