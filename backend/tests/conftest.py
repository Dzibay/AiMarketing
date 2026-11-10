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


@pytest.fixture(autouse=True)
def _instant_ai_retries(monkeypatch):
    """Автоповтор временных сбоев ИИ в тестах — без настоящих пауз."""
    monkeypatch.setattr("app.ai.RETRY_PAUSES", (0, 0))


@pytest.fixture(autouse=True)
def _no_plan_without_db(request, monkeypatch):
    """Юнит-тесты без БД: у пользователя нет тарифа — все функции открыты, лимитов нет."""
    if {"db", "db_pool"} & set(request.fixturenames):
        return
    monkeypatch.setattr("app.plans._row", lambda user_id: None)


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
    """Пустые таблицы перед каждым тестом (users каскадом чистит почти всё) и общие ключи по умолчанию."""
    from tests.dbutil import reset_services

    with db_pool.connection() as conn:
        conn.execute("TRUNCATE users, jobs, job_schedules, plans RESTART IDENTITY CASCADE")
    reset_services(db_pool)
    return db_pool
