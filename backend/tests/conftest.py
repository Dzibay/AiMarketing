"""
Интеграционные тесты (test_db_*.py) ходят в отдельную одноразовую БД из TEST_DB_*.

Если TEST_DB_NAME задан, DB_* подменяются до первого импорта app.config — иначе тесты
могли бы очистить рабочую БД из .env. Без TEST_DB_NAME интеграционные тесты пропускаются.
"""

import os

import pytest

TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "")

if TEST_DB_NAME:
    if "test" not in TEST_DB_NAME:
        raise RuntimeError("TEST_DB_NAME должен содержать 'test': тесты пересоздают схему БД")
    for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD"):
        value = os.environ.get(f"TEST_DB_{key}")
        if value:
            os.environ[f"DB_{key}"] = value


def reset_schema(pool) -> None:
    with pool.connection() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture(scope="session")
def db_pool():
    """Пул к тестовой БД с чистой схемой, накатанной миграциями до head."""
    if not TEST_DB_NAME:
        pytest.skip("нужна тестовая БД: TEST_DB_HOST / TEST_DB_PORT / TEST_DB_NAME / TEST_DB_USER / TEST_DB_PASSWORD")
    from app.db import pool, run_migrations

    pool.open(wait=True, timeout=15)
    reset_schema(pool)
    run_migrations()
    yield pool
    pool.close()


@pytest.fixture
def db(db_pool):
    """Пустые таблицы перед каждым тестом (users каскадом чистит почти всё)."""
    with db_pool.connection() as conn:
        conn.execute("TRUNCATE users, jobs, job_schedules RESTART IDENTITY CASCADE")
    return db_pool
