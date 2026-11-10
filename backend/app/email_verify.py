"""Подтверждение почты при регистрации: письмо со ссылкой, токен в БД хэшем.

Работает, только когда настроен SMTP (plans.email_check_required): без писем подтвердить почту нечем, и
кабинет открыт сразу. Вход через Яндекс ID почту подтверждает сам (users.upsert_yandex_user).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from app import events, mailer
from app.clock import now_msk
from app.config import settings
from app.db import pool
from app.settings_store import public_base_url
from app.users import UsersError, get_user, mark_email_verified
from app.yandex import YandexError

log = logging.getLogger("app.email_verify")

TTL = timedelta(days=3)
# Повторное письмо — не чаще раза в минуту.
RESEND_AFTER = timedelta(seconds=60)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def send(user_id: int, *, throttle: bool = False) -> bool:
    """Новая ссылка подтверждения на почту пользователя. True — письмо ушло."""
    user = get_user(user_id)
    if user is None or not user.get("email") or user.get("email_verified_at"):
        return False
    now = now_msk()
    with pool.connection() as conn:
        last = conn.execute(
            "SELECT created_at FROM email_verify_tokens WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if throttle and last and now - last["created_at"] < RESEND_AFTER:
            raise YandexError("too-soon", "Письмо уже отправлено — повторить можно через минуту")
        token = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM email_verify_tokens WHERE user_id = %s OR expires_at < NOW()", (user_id,))
        conn.execute(
            "INSERT INTO email_verify_tokens (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
            (_hash(token), user_id, now + TTL),
        )
    base = (public_base_url() or "").rstrip("/")
    link = f"{base}/verify-email?token={token}"
    body = (
        "Чтобы подтвердить почту и открыть кабинет, перейдите по ссылке "
        f"(действует {TTL.days} дня):\n\n{link}\n\n"
        "Если вы не регистрировались — просто проигнорируйте письмо."
    )
    return mailer.send(user["email"], f"{settings.app_name}: подтвердите почту", body, link=link)


def confirm(token: str) -> int:
    """Ссылка из письма → id пользователя; почта подтверждена, токены пользователя удалены."""
    token = (token or "").strip()
    if not token:
        raise UsersError("state")
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM email_verify_tokens WHERE token_hash = %s", (_hash(token),)
        ).fetchone()
        if row is None:
            raise UsersError("state")
        if row["expires_at"] <= now_msk():
            raise UsersError("token-expired")
        conn.execute("DELETE FROM email_verify_tokens WHERE user_id = %s", (row["user_id"],))
    user_id = int(row["user_id"])
    mark_email_verified(user_id)
    events.record(user_id, "email_verified")
    return user_id
