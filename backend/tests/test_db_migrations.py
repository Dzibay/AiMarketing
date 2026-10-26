"""Миграции на чистой БД и на БД, созданной до Alembic (бывший init.sql)."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db import run_migrations
from tests.conftest import reset_schema
from tests.dbutil import client_of, link, make_account, make_connection, make_counter, make_user

BACKEND = Path(__file__).resolve().parent.parent


def _head() -> str:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()


def _version(pool) -> str:
    with pool.connection() as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"]


def test_fresh_db_migrates_to_head(db_pool):
    reset_schema(db_pool)
    run_migrations()
    with db_pool.connection() as conn:
        tables = {
            r["table_name"]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
    assert _version(db_pool) == _head()
    assert {"users", "clients", "jobs", "job_schedules", "direct_accounts", "metrika_counters"} <= tables
    run_migrations()  # повторный запуск — no-op
    assert _version(db_pool) == _head()


def test_pre_alembic_db_is_adopted_and_backfilled(db_pool):
    reset_schema(db_pool)
    with db_pool.connection() as conn:
        conn.execute((BACKEND / "migrations" / "sql" / "0001_baseline.sql").read_text(encoding="utf-8"))

    user = make_user(db_pool)
    conn_a = make_connection(db_pool, user, "agency")
    alpha = make_account(db_pool, user, conn_a, name="Альфа", login="alpha")
    beta = make_account(db_pool, user, conn_a, name="", login="beta-login")
    linked = make_counter(db_pool, user, conn_a, 1)
    link(db_pool, user, alpha, linked)
    unlinked = make_counter(db_pool, user, conn_a, 3)
    shared = make_counter(db_pool, user, conn_a, 4)
    link(db_pool, user, alpha, shared)
    link(db_pool, user, beta, shared)
    conn_b = make_connection(db_pool, user, "solo")
    solo = make_account(db_pool, user, conn_b, name="Соло", login="solo")
    solo_counter = make_counter(db_pool, user, conn_b, 2, site="solo.ru")

    run_migrations()

    assert _version(db_pool) == _head()
    with db_pool.connection() as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM clients WHERE user_id = %s", (user,))}
    assert names == {"Альфа", "beta-login", "Соло"}
    assert client_of(db_pool, "direct_accounts", alpha) != client_of(db_pool, "direct_accounts", beta)
    # Счётчик, связанный ровно с одним клиентом, — его.
    assert client_of(db_pool, "metrika_counters", linked) == client_of(db_pool, "direct_accounts", alpha)
    # Единственный кабинет подключения забирает его счётчики.
    assert client_of(db_pool, "metrika_counters", solo_counter) == client_of(db_pool, "direct_accounts", solo)
    # Неоднозначные случаи оставляем пользователю.
    assert client_of(db_pool, "metrika_counters", unlinked) is None
    assert client_of(db_pool, "metrika_counters", shared) is None
