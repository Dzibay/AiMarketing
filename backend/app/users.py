"""Пользователи кабинета: регистрация, вход, профиль."""

from __future__ import annotations

import re

from psycopg.types.json import Jsonb

from app.db import pool
from app.passwords import hash_password, verify_password

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Настройки интерфейса в аккаунте (users.ui). Чужие ключи не принимаем: это не свалка для фронтенда.
UI_ROLES = ("owner", "pro")
UI_KEYS = ("role", "expert", "hints_seen", "whats_new_seen")
_UI_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,39}$")
_UI_HINTS_MAX = 200


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
                   telegram_chat_id, email_verified_at, created_at, ui
            FROM users WHERE id = %s
            """,
            (user_id,),
        ).fetchone()


def get_user_by_email(email: str) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id, email, name, password_hash, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at,
                   ui
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
            SELECT id, email, name, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at, ui
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
            RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at, ui
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
        "email_verified_at": user["email_verified_at"],
        "created_at": user["created_at"],
        "ui": user["ui"],
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
        if found.get("email_verified_at") is None:
            mark_email_verified(found["id"])
            found = get_user(found["id"])
        return found

    email_n = normalize_email(email)
    if email_n:
        by_email = get_user_by_email(email_n)
        if by_email:
            ensure_not_blocked(by_email)
            # Вход Яндексом подтверждает почту. Если аккаунт с этой почтой её не подтвердил, пароль
            # от него задал неизвестно кто — сбрасываем его (вернуть можно через «Забыли пароль?»).
            with pool.connection() as conn:
                return conn.execute(
                    """
                    UPDATE users
                    SET yandex_uid = %s,
                        name = CASE WHEN name = '' THEN %s ELSE name END,
                        password_hash = CASE WHEN email_verified_at IS NULL THEN NULL ELSE password_hash END,
                        email_verified_at = COALESCE(email_verified_at, NOW())
                    WHERE id = %s
                    RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at, ui
                    """,
                    (yandex_uid, (name or "").strip()[:120], by_email["id"]),
                ).fetchone()

    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO users (email, name, yandex_uid, email_verified_at)
            VALUES (%s, %s, %s, NOW())
            RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at, ui
            """,
            (email_n, (name or "").strip()[:120], yandex_uid),
        ).fetchone()
    # Новый аккаунт: вызывающий выдаст пробный тариф и запишет регистрацию.
    return {**row, "_created": True}


def set_name(user_id: int, name: str) -> dict:
    """Имя в профиле: пустое — показываем почту."""
    name = " ".join((name or "").split())
    if len(name) > 120:
        raise UsersError("name-too-long")
    with pool.connection() as conn:
        conn.execute("UPDATE users SET name = %s WHERE id = %s", (name, user_id))
    user = get_user(user_id)
    if user is None:
        raise UsersError("not-found")
    return public_user(user)


def _ui_value(key: str, value):
    """Одно значение настроек интерфейса; кривое — ошибка, а не молчаливая правка."""
    if key == "role":
        ok = value in UI_ROLES
    elif key == "expert":
        ok = isinstance(value, bool)
    elif key == "hints_seen":
        ok = (
            isinstance(value, list)
            and len(value) <= _UI_HINTS_MAX
            and all(isinstance(item, str) and _UI_ID_RE.match(item) for item in value)
        )
    else:  # whats_new_seen — метка последней прочитанной записи «Что нового»
        ok = isinstance(value, str) and bool(_UI_ID_RE.match(value))
    if not ok:
        raise UsersError("ui-value")
    return value


def set_ui(user_id: int, patch: dict) -> dict:
    """Настройки интерфейса: меняет только присланные ключи, остальные остаются как были.

    `null` сбрасывает ключ. Подсказки (`hints_seen`) дополняют сохранённый список, а не заменяют его:
    телефон и компьютер закрывают разные подсказки и не должны затирать друг друга.
    """
    patch = patch or {}
    if any(key not in UI_KEYS for key in patch):
        raise UsersError("ui-unknown")
    clean = {key: None if value is None else _ui_value(key, value) for key, value in patch.items()}
    with pool.connection() as conn:
        row = conn.execute("SELECT ui FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if row is None:
            raise UsersError("not-found")
        ui = dict(row["ui"] or {})
        for key, value in clean.items():
            if value is None:
                ui.pop(key, None)
            elif key == "hints_seen":
                ui[key] = list(dict.fromkeys([*(ui.get(key) or []), *value]))[-_UI_HINTS_MAX:]
            else:
                ui[key] = value
        conn.execute("UPDATE users SET ui = %s WHERE id = %s", (Jsonb(ui), user_id))
    user = get_user(user_id)
    if user is None:
        raise UsersError("not-found")
    return public_user(user)


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user.get("email") or "",
        "name": user.get("name") or "",
        "yandex_uid": user.get("yandex_uid") or "",
        "has_yandex": bool(user.get("yandex_uid")),
        "telegram_linked": bool(user.get("telegram_chat_id")),
        "email_verified": user.get("email_verified_at") is not None,
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
        # Настройки интерфейса: роль, режим, просмотренные подсказки. Пусто — ещё ничего не выбирали.
        "ui": dict(user.get("ui") or {}),
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
                RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at, ui
                """,
                (reason_n, user_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                UPDATE users
                SET blocked_at = NULL, blocked_reason = ''
                WHERE id = %s
                RETURNING id, email, name, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at, ui
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
            SELECT id, email, name, yandex_uid, blocked_at, blocked_reason, email_verified_at, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        ).fetchall()
    return [admin_user(row) for row in rows]


def mark_email_verified(user_id: int) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE users SET email_verified_at = COALESCE(email_verified_at, NOW()) WHERE id = %s", (user_id,)
        )


def count_users() -> int:
    with pool.connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0
