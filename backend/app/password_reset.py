"""Сброс пароля: токен в БД + письмо (или лог, если SMTP не настроен)."""

from __future__ import annotations

import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from app.config import settings
from app.db import pool
from app.passwords import hash_password
from app.settings_store import public_base_url
from app.users import UsersError, get_user_by_email, validate_email, validate_password

log = logging.getLogger("app.password_reset")

_TTL_HOURS = 2


def request_password_reset(email: str) -> None:
    """Всегда без утечки: не сообщает, есть ли пользователь."""
    try:
        email_n = validate_email(email)
    except UsersError:
        return
    user = get_user_by_email(email_n)
    if user is None or not user.get("password_hash"):
        return
    if user.get("blocked_at"):
        return
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=_TTL_HOURS)
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = %s OR expires_at < NOW()",
            (user["id"],),
        )
        conn.execute(
            """
            INSERT INTO password_reset_tokens (token, user_id, expires_at)
            VALUES (%s, %s, %s)
            """,
            (token, user["id"], expires),
        )
    base = (public_base_url() or "").rstrip("/")
    link = f"{base}/reset-password?token={token}" if base else f"/reset-password?token={token}"
    _send_reset_mail(email_n, link)


def reset_password(*, token: str, password: str) -> None:
    token_n = (token or "").strip()
    if not token_n:
        raise UsersError("state")
    password_n = validate_password(password)
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT token, user_id, expires_at, used_at
            FROM password_reset_tokens
            WHERE token = %s
            """,
            (token_n,),
        ).fetchone()
        if row is None or row.get("used_at"):
            raise UsersError("state")
        expires = row["expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            raise UsersError("state")
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (hash_password(password_n), row["user_id"]),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = NOW() WHERE token = %s",
            (token_n,),
        )


def _send_reset_mail(to_email: str, link: str) -> None:
    subject = f"{settings.app_name}: сброс пароля"
    body = (
        f"Чтобы задать новый пароль, откройте ссылку (действует {_TTL_HOURS} ч.):\n\n"
        f"{link}\n\n"
        "Если вы не запрашивали сброс — просто проигнорируйте письмо."
    )
    if not settings.smtp_host or not settings.smtp_from:
        log.warning("SMTP не настроен. Ссылка сброса для %s: %s", to_email, link)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.set_content(body)
    try:
        if settings.smtp_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                smtp.ehlo()
                if settings.smtp_starttls:
                    smtp.starttls()
                    smtp.ehlo()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        log.info("Письмо сброса отправлено на %s", to_email)
    except Exception:
        log.exception("Не удалось отправить письмо сброса на %s", to_email)
        log.warning("Ссылка сброса для %s: %s", to_email, link)
