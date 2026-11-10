"""Админка: отдельный вход по паролю из ADMIN_PASSWORD (.env). Админ — не пользователь кабинета.

Токен — подписанный срок действия, в БД ничего не хранится. Перебор пароля ограничен: после
MAX_FAILURES неверных попыток с одного адреса (или FAILURES_TOTAL со всех) вход закрыт до конца окна.
"""

import hashlib
import hmac
import threading
import time

from fastapi import Header, HTTPException

from app.config import settings

MAX_FAILURES = 5
FAILURES_TOTAL = 30
FAILURE_WINDOW = 15 * 60

_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


class LoginError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _secret() -> bytes:
    # Из него выводится секрет сессий кабинета (web_auth), если WEB_SESSION_SECRET не задан:
    # смена формулы разлогинит всех пользователей.
    if settings.admin_token_secret:
        return settings.admin_token_secret.encode()
    return hashlib.sha256(f"aimarketing-admin:{settings.admin_password}".encode()).digest()


def _sign(expires_at: int) -> str:
    return hmac.new(_secret(), f"admin:{expires_at}".encode(), hashlib.sha256).hexdigest()


def check_password(password: str) -> bool:
    if not settings.admin_password:
        return False
    return hmac.compare_digest((password or "").encode(), settings.admin_password.encode())


def make_token() -> str:
    expires_at = int(time.time()) + settings.admin_token_ttl_hours * 3600
    return f"{expires_at}.{_sign(expires_at)}"


def verify_token(token: str) -> bool:
    expires_str, _, signature = (token or "").strip().partition(".")
    if not expires_str.isdigit() or len(expires_str) > 12 or not signature:
        return False
    if not hmac.compare_digest(signature, _sign(int(expires_str))):
        return False
    return int(expires_str) > time.time()


def _prune(now: float) -> None:
    for key in [k for k, times in _failures.items() if now - times[-1] >= FAILURE_WINDOW]:
        del _failures[key]


def login(password: str, client: str = "") -> str:
    """Токен админки или LoginError: unauthorized — неверный пароль, too-many-attempts — перебор."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        recent = [t for t in _failures.get(client, ()) if now - t < FAILURE_WINDOW]
        total = sum(1 for times in _failures.values() for t in times if now - t < FAILURE_WINDOW)
        if len(recent) >= MAX_FAILURES or total >= FAILURES_TOTAL:
            raise LoginError("too-many-attempts")
        if check_password(password):
            _failures.pop(client, None)
            return make_token()
        _failures[client] = [*recent, now]
    raise LoginError("unauthorized")


def reset_failures() -> None:
    with _lock:
        _failures.clear()


def require_admin(authorization: str = Header(default="")) -> None:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not verify_token(token):
        raise HTTPException(status_code=401, detail="unauthorized")
