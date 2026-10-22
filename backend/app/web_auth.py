"""Сессии личного кабинета: подписанный токен с id пользователя и сроком."""

import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from app.auth import _secret as _admin_secret
from app.config import settings


def _secret() -> bytes:
    if settings.web_session_secret:
        return settings.web_session_secret.encode()
    return hashlib.sha256(b"aimarketing-web:" + _admin_secret()).digest()


def _sign(user_id: int, expires_at: int) -> str:
    return hmac.new(_secret(), f"{user_id}.{expires_at}".encode(), hashlib.sha256).hexdigest()


def make_session(user_id: int) -> str:
    expires_at = int(time.time()) + settings.web_session_ttl_days * 86400
    return f"{user_id}.{expires_at}.{_sign(user_id, expires_at)}"


def verify_session(token: str) -> int | None:
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    user_str, expires_str, signature = parts
    if not user_str.isdigit() or not expires_str.isdigit() or not signature:
        return None
    user_id, expires_at = int(user_str), int(expires_str)
    if not hmac.compare_digest(signature, _sign(user_id, expires_at)):
        return None
    if expires_at <= time.time():
        return None
    return user_id


def require_web_user(authorization: str = Header(default="")) -> int:
    scheme, _, token = authorization.partition(" ")
    user_id = verify_session(token) if scheme.lower() == "bearer" else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user_id
