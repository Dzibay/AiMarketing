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
            SELECT id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
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
        user = get_user(existing_user_id)
        if user is None:
            raise UsersError("unauthorized")
        ensure_not_blocked(user)
        other = get_user_by_yandex_uid(yandex_uid)
        if other and other["id"] != existing_user_id:
            raise UsersError("yandex-taken")
        with pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE users
                SET yandex_uid = %s,
                    name = CASE WHEN name = '' THEN %s ELSE name END,
                    email = CASE WHEN email = '' AND %s <> '' THEN %s ELSE email END
                WHERE id = %s
                RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, created_at
                """,
                (
                    yandex_uid,
                    (name or "").strip()[:120],
                    normalize_email(email),
                    normalize_email(email),
                    existing_user_id,
                ),
            ).fetchone()
        return row

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
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
    }


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
    return [public_user(row) for row in rows]


def count_users() -> int:
    with pool.connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0
