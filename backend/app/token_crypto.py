"""Шифрование OAuth-токенов перед записью в БД."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _fernet() -> Fernet | None:
    raw = (settings.token_encrypt_secret or "").strip()
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_token(plain: str) -> str:
    if not plain:
        return ""
    f = _fernet()
    if f is None:
        raise RuntimeError("TOKEN_ENCRYPT_SECRET не задан")
    return f.encrypt(plain.encode()).decode()


def decrypt_token(cipher: str) -> str:
    if not cipher:
        return ""
    f = _fernet()
    if f is None:
        raise RuntimeError("TOKEN_ENCRYPT_SECRET не задан")
    try:
        return f.decrypt(cipher.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("не удалось расшифровать токен") from exc
