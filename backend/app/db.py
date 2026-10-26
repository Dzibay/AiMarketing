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
    max_size=20,
    open=False,
    kwargs={"row_factory": dict_row},
)

# backend/alembic.ini локально и /app/alembic.ini в Docker.
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def run_migrations(revision: str = "head") -> None:
    """alembic upgrade head; параллельные запуски сериализует advisory lock в env.py."""
    # Без этого на каждом старте в лог сыпется «setup plugin …» (пишется уже при импорте alembic).
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    from alembic import command
    from alembic.config import Config

    if not ALEMBIC_INI.is_file():
        raise RuntimeError(f"{ALEMBIC_INI} не найден")
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["configure_logging"] = False
    command.upgrade(cfg, revision)


def ensure_schema(retries: int = 10, delay: float = 2.0) -> None:
    """Открывает пул, дожидается готовности БД и накатывает миграции."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            pool.open()
            with pool.connection() as conn:
                conn.execute("SELECT 1")
            break
        except Exception as exc:  # noqa: BLE001 — ждём поднятия postgres
            last_error = exc
            log.warning("БД недоступна (попытка %d/%d): %s", attempt, retries, exc)
            time.sleep(delay)
    else:
        raise RuntimeError(f"Не удалось подключиться к БД: {last_error}")
    run_migrations()
    log.info("Схема БД готова")
