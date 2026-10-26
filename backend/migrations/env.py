"""Окружение Alembic: миграции пишутся сырым SQL, без моделей SQLAlchemy."""

from __future__ import annotations

import zlib
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import URL

from app.config import settings

config = context.config

# При вызове из приложения логирование уже настроено — не перетираем его.
if config.config_file_name and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

# API и worker стартуют одновременно: без замка оба полезут накатывать миграции.
MIGRATION_LOCK_KEY = zlib.crc32(b"aimarketing:migrations") & 0x7FFFFFFF


def _url() -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # Сессионный замок переживает commit; сам commit нужен, чтобы Alembic
        # открыл собственную транзакцию, а не работал во внешней.
        connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            connection.commit()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
