"""Advisory-lock в Postgres, чтобы воркеры не гоняли один и тот же цикл параллельно."""

from __future__ import annotations

import zlib
from contextlib import contextmanager
from typing import Iterator

from app.db import pool


def _key(name: str) -> int:
    return zlib.crc32(f"aimarketing:{name}".encode()) & 0x7FFFFFFF


@contextmanager
def try_worker_lock(name: str) -> Iterator[bool]:
    """
    Держит соединение на время работы: pg advisory lock привязан к сессии.
    yield True — замок взят; False — другой процесс уже крутит этот цикл.
    """
    key = _key(name)
    with pool.connection() as conn:
        row = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (key,)).fetchone()
        locked = bool(row and row["ok"])
        try:
            yield locked
        finally:
            if locked:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
