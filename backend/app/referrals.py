"""Реф-ссылки: ?ref=код на любой странице сайта.

Сайт запоминает код при первом заходе (localStorage, 90 дней — первый источник не перебивается) и один
раз сообщает о переходе (visits). При регистрации — по почте или через Яндекс — код сохраняется в
users.ref_link_id. Статистика ссылки: переходы, регистрации, подтверждённые почты, оплаты и выручка
(без тестовых платежей и за вычетом возвратов).
"""

from __future__ import annotations

import re

from app.db import pool
from app.settings_store import public_base_url
from app.yandex import YandexError

CODE_RE = re.compile(r"^[a-z0-9_-]{2,40}$")
NOT_FOUND = "Реф-ссылка не найдена"


def normalize(code: str) -> str:
    return (code or "").strip().lower()


def visit(code: str) -> bool:
    """Переход по ссылке: +1 к счётчику активной ссылки."""
    code = normalize(code)
    if not CODE_RE.match(code):
        return False
    with pool.connection() as conn:
        row = conn.execute(
            "UPDATE ref_links SET visits = visits + 1 WHERE code = %s AND active RETURNING id", (code,)
        ).fetchone()
    return row is not None


def link_id(code: str) -> int | None:
    code = normalize(code)
    if not CODE_RE.match(code):
        return None
    with pool.connection() as conn:
        row = conn.execute("SELECT id FROM ref_links WHERE code = %s AND active", (code,)).fetchone()
    return int(row["id"]) if row else None


def attach(user_id: int, code: str) -> bool:
    """Запомнить, по какой ссылке пришёл новый пользователь (только первую)."""
    lid = link_id(code) if code else None
    if lid is None:
        return False
    with pool.connection() as conn:
        row = conn.execute(
            "UPDATE users SET ref_link_id = %s WHERE id = %s AND ref_link_id IS NULL RETURNING id", (lid, user_id)
        ).fetchone()
    return row is not None


# --- Админка ------------------------------------------------------------------------------------


_STATS = """
    SELECT l.id, l.code, l.name, l.visits, l.active, l.created_at,
           COUNT(u.id) AS registrations,
           COUNT(u.id) FILTER (WHERE u.email_verified_at IS NOT NULL) AS verified,
           COUNT(pay.user_id) AS payers,
           COALESCE(SUM(pay.n), 0) AS payments,
           COALESCE(SUM(pay.rub), 0) AS revenue_rub
    FROM ref_links l
    LEFT JOIN users u ON u.ref_link_id = l.id
    LEFT JOIN (
        SELECT user_id, COUNT(*) AS n, SUM(amount_rub - refunded_rub) AS rub
        FROM payments WHERE status = 'succeeded' AND NOT test AND user_id IS NOT NULL
        GROUP BY user_id
    ) pay ON pay.user_id = u.id
"""


def _out(row: dict, base: str) -> dict:
    return {
        "id": int(row["id"]),
        "code": row["code"],
        "name": row["name"] or "",
        "active": bool(row["active"]),
        "created_at": row["created_at"].isoformat(),
        "url": f"{base}/?ref={row['code']}" if base else f"/?ref={row['code']}",
        "visits": int(row["visits"]),
        "registrations": int(row["registrations"]),
        "verified": int(row["verified"]),
        "payers": int(row["payers"]),
        "payments": int(row["payments"]),
        "revenue_rub": float(row["revenue_rub"]),
    }


def list_links() -> list[dict]:
    base = public_base_url()
    with pool.connection() as conn:
        rows = conn.execute(_STATS + " GROUP BY l.id ORDER BY l.created_at DESC, l.id DESC").fetchall()
    return [_out(r, base) for r in rows]


def _get(lid: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(_STATS + " WHERE l.id = %s GROUP BY l.id", (lid,)).fetchone()
    return _out(row, public_base_url()) if row else None


def save_link(lid: int | None, data: dict) -> dict:
    code = normalize(data.get("code"))
    if not CODE_RE.match(code):
        raise YandexError("ref-field", "Код — от 2 до 40 символов: латинские буквы, цифры, «-» и «_»")
    name = str(data.get("name") or "").strip()[:120]
    active = bool(data.get("active", True))
    with pool.connection() as conn:
        taken = conn.execute("SELECT 1 FROM ref_links WHERE code = %s AND id IS DISTINCT FROM %s", (code, lid)).fetchone()
        if taken:
            raise YandexError("exists", "Такой код уже есть")
        if lid is None:
            row = conn.execute(
                "INSERT INTO ref_links (code, name, active) VALUES (%s, %s, %s) RETURNING id", (code, name, active)
            ).fetchone()
        else:
            row = conn.execute(
                "UPDATE ref_links SET code = %s, name = %s, active = %s WHERE id = %s RETURNING id",
                (code, name, active, lid),
            ).fetchone()
    if row is None:
        raise YandexError("not-found", NOT_FOUND)
    return _get(int(row["id"]))


def delete_link(lid: int) -> None:
    """Пользователи остаются, просто без источника."""
    with pool.connection() as conn:
        row = conn.execute("DELETE FROM ref_links WHERE id = %s RETURNING id", (lid,)).fetchone()
    if row is None:
        raise YandexError("not-found", NOT_FOUND)
