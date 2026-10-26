"""Одноразовые коды после OAuth — сессия не попадает в URL/логи."""

from __future__ import annotations

import secrets
import threading
import time

_lock = threading.Lock()
_codes: dict[str, tuple[int, float]] = {}
_TTL_SEC = 120


def issue_oauth_code(user_id: int) -> str:
    code = secrets.token_urlsafe(24)
    expires = time.time() + _TTL_SEC
    with _lock:
        _purge_locked()
        _codes[code] = (user_id, expires)
    return code


def consume_oauth_code(code: str) -> int | None:
    code = (code or "").strip()
    if not code:
        return None
    with _lock:
        _purge_locked()
        item = _codes.pop(code, None)
    if item is None:
        return None
    user_id, expires = item
    if expires <= time.time():
        return None
    return user_id


def _purge_locked() -> None:
    now = time.time()
    stale = [k for k, (_, exp) in _codes.items() if exp <= now]
    for k in stale:
        _codes.pop(k, None)
