"""Пользователи кабинета: регистрация, вход, профиль."""

from __future__ import annotations

import re

from app.db import pool
from app.passwords import hash_password, verify_password

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UsersError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    value = normalize_email(email)
    if not value or not _EMAIL_RE.match(value) or len(value) > 254:
        raise UsersError("email")
    return value


def validate_password(password: str) -> str:
    value = password or ""
    if len(value) < 8 or len(value) > 128:
        raise UsersError("password")
    return value


def get_user(user_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id, email, name, yandex_uid, blocked_at, blocked_reason,
                   telegram_chat_id, created_at
            FROM users WHERE id = %s
            """,
            (user_id,),
        ).fetchone()


def get_user_by_email(email: str) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id, email, name, password_hash, yandex_uid, blocked_at, blocked_reason, created_at
            FROM users WHERE lower(email) = %s
            """,
            (normalize_email(email),),
        ).fetchone()


def get_user_by_yandex_uid(yandex_uid: str) -> dict | None:
    if not yandex_uid:
        return None
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
            FROM users WHERE yandex_uid = %s
            """,
            (yandex_uid,),
        ).fetchone()


def ensure_not_blocked(user: dict) -> None:
    if user.get("blocked_at"):
        raise UsersError("blocked")


def change_password(user_id: int, *, current_password: str, new_password: str) -> None:
    with pool.connection() as conn:
        user = conn.execute(
            """
            SELECT id, password_hash, blocked_at
            FROM users WHERE id = %s
            """,
            (user_id,),
        ).fetchone()
    if user is None:
        raise UsersError("not-found")
    ensure_not_blocked(user)
    stored = user.get("password_hash") or ""
    if not stored or not verify_password(current_password or "", stored):
        raise UsersError("credentials")
    password_n = validate_password(new_password)
    with pool.connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(password_n), user_id),
        )


def register_user(*, email: str, password: str, name: str = "") -> dict:
    email_n = validate_email(email)
    password_n = validate_password(password)
    display = (name or "").strip()[:120]
    if get_user_by_email(email_n):
        raise UsersError("exists")
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO users (email, password_hash, name)
            VALUES (%s, %s, %s)
            RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
            """,
            (email_n, hash_password(password_n), display),
        ).fetchone()
    return row


def login_user(*, email: str, password: str) -> dict:
    email_n = validate_email(email)
    user = get_user_by_email(email_n)
    if user is None or not user.get("password_hash"):
        raise UsersError("credentials")
    if not verify_password(password or "", user["password_hash"]):
        raise UsersError("credentials")
    ensure_not_blocked(user)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "yandex_uid": user["yandex_uid"],
        "blocked_at": user["blocked_at"],
        "blocked_reason": user["blocked_reason"],
        "created_at": user["created_at"],
    }


def upsert_yandex_user(
    *,
    yandex_uid: str,
    email: str = "",
    name: str = "",
    existing_user_id: int | None = None,
) -> dict:
    """Создаёт или находит пользователя по Яндекс-аккаунту. Минимум действий."""
    yandex_uid = (yandex_uid or "").strip()
    if not yandex_uid:
        raise UsersError("yandex")

    if existing_user_id is not None:
        # Подключение кабинета (логин клиента) — не вход: личность пользователя
        # не меняем, иначе клиент смог бы войти своим Яндексом в кабинет фрилансера.
        user = get_user(existing_user_id)
        if user is None:
            raise UsersError("unauthorized")
        ensure_not_blocked(user)
        return user

    found = get_user_by_yandex_uid(yandex_uid)
    if found:
        ensure_not_blocked(found)
        return found

    email_n = normalize_email(email)
    if email_n:
        by_email = get_user_by_email(email_n)
        if by_email:
            ensure_not_blocked(by_email)
            with pool.connection() as conn:
                return conn.execute(
                    """
                    UPDATE users
                    SET yandex_uid = %s,
                        name = CASE WHEN name = '' THEN %s ELSE name END
                    WHERE id = %s
                    RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
                    """,
                    (yandex_uid, (name or "").strip()[:120], by_email["id"]),
                ).fetchone()

    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO users (email, name, yandex_uid)
            VALUES (%s, %s, %s)
            RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
            """,
            (email_n, (name or "").strip()[:120], yandex_uid),
        ).fetchone()


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user.get("email") or "",
        "name": user.get("name") or "",
        "yandex_uid": user.get("yandex_uid") or "",
        "has_yandex": bool(user.get("yandex_uid")),
        "telegram_linked": bool(user.get("telegram_chat_id")),
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
    }


def admin_user(user: dict) -> dict:
    return {
        **public_user(user),
        "blocked": bool(user.get("blocked_at")),
        "blocked_at": user["blocked_at"].isoformat() if user.get("blocked_at") else None,
        "blocked_reason": user.get("blocked_reason") or "",
    }


def set_user_blocked(user_id: int, *, blocked: bool, reason: str = "") -> dict:
    reason_n = (reason or "").strip()[:500]
    with pool.connection() as conn:
        if blocked:
            row = conn.execute(
                """
                UPDATE users
                SET blocked_at = COALESCE(blocked_at, NOW()),
                    blocked_reason = %s
                WHERE id = %s
                RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
                """,
                (reason_n, user_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                UPDATE users
                SET blocked_at = NULL, blocked_reason = ''
                WHERE id = %s
                RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
                """,
                (user_id,),
            ).fetchone()
    if row is None:
        raise UsersError("not-found")
    return admin_user(row)


def list_users(limit: int = 100, offset: int = 0) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        ).fetchall()
    return [admin_user(row) for row in rows]


def count_users() -> int:
    with pool.connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0
