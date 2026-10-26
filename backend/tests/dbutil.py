"""Фабрики строк для интеграционных тестов."""


def make_user(pool, email: str = "u@example.com", yandex_uid: str = "") -> int:
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO users (email, yandex_uid) VALUES (%s, %s) RETURNING id",
            (email, yandex_uid),
        ).fetchone()["id"]


def make_connection(pool, user_id: int, login: str = "login") -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO yandex_connections (user_id, yandex_uid, yandex_login)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (user_id, f"uid-{login}", login),
        ).fetchone()["id"]


def make_account(pool, user_id: int, connection_id: int, name: str = "", login: str = "acc") -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO direct_accounts (user_id, connection_id, client_login, name)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (user_id, connection_id, login, name),
        ).fetchone()["id"]


def make_counter(pool, user_id: int, connection_id: int, counter_id: int, site: str = "") -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO metrika_counters (user_id, connection_id, counter_id, site)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (user_id, connection_id, counter_id, site),
        ).fetchone()["id"]


def link(pool, user_id: int, account_id: int, counter_row_id: int) -> None:
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO account_counter_links (user_id, account_id, counter_row_id) VALUES (%s, %s, %s)",
            (user_id, account_id, counter_row_id),
        )


def client_of(pool, table: str, row_id: int) -> int | None:
    assert table in ("direct_accounts", "metrika_counters")
    with pool.connection() as conn:
        return conn.execute(f"SELECT client_row_id FROM {table} WHERE id = %s", (row_id,)).fetchone()[
            "client_row_id"
        ]
