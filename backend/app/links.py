"""Связи аккаунтов Директа со счётчиками Метрики."""

from __future__ import annotations

from app.db import pool
from app.yandex import YandexError


def create_link(user_id: int, *, account_id: int, counter_row_id: int) -> dict:
    with pool.connection() as conn:
        account = conn.execute(
            "SELECT id FROM direct_accounts WHERE id = %s AND user_id = %s",
            (account_id, user_id),
        ).fetchone()
        counter = conn.execute(
            "SELECT id FROM metrika_counters WHERE id = %s AND user_id = %s",
            (counter_row_id, user_id),
        ).fetchone()
        if account is None or counter is None:
            raise YandexError("not-found")
        row = conn.execute(
            """
            INSERT INTO account_counter_links (user_id, account_id, counter_row_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (account_id, counter_row_id) DO UPDATE SET user_id = EXCLUDED.user_id
            RETURNING id, account_id, counter_row_id, created_at
            """,
            (user_id, account_id, counter_row_id),
        ).fetchone()
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "counter_row_id": row["counter_row_id"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def delete_link(user_id: int, link_id: int) -> None:
    with pool.connection() as conn:
        row = conn.execute(
            "DELETE FROM account_counter_links WHERE id = %s AND user_id = %s RETURNING id",
            (link_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")


def list_links(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.account_id, l.counter_row_id, l.created_at,
                   a.name AS account_name, a.client_login,
                   c.name AS counter_name, c.counter_id, c.site
            FROM account_counter_links l
            JOIN direct_accounts a ON a.id = l.account_id
            JOIN metrika_counters c ON c.id = l.counter_row_id
            WHERE l.user_id = %s
            ORDER BY l.id DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "account_id": r["account_id"],
            "counter_row_id": r["counter_row_id"],
            "account_name": r["account_name"],
            "client_login": r["client_login"],
            "counter_name": r["counter_name"],
            "counter_id": r["counter_id"],
            "site": r["site"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]
