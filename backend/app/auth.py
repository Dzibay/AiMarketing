"""Админка: вход обычной сессией кабинета, права — по email из ADMIN_EMAILS (.env).

Проверка запроса — web_auth.require_admin.
"""

import hashlib

from app.config import settings


def _secret() -> bytes:
    # Из него выводится секрет сессий кабинета (web_auth), если WEB_SESSION_SECRET не задан:
    # смена формулы разлогинит всех пользователей.
    if settings.admin_token_secret:
        return settings.admin_token_secret.encode()
    return hashlib.sha256(f"aimarketing-admin:{settings.admin_password}".encode()).digest()


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in (settings.admin_emails or "").replace(";", ",").split(",") if e.strip()}


def is_admin(user: dict | None) -> bool:
    email = ((user or {}).get("email") or "").strip().lower()
    return bool(email) and email in admin_emails()
