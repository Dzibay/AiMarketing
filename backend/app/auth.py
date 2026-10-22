import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from app.config import settings


def _secret() -> bytes:
    if settings.admin_token_secret:
        return settings.admin_token_secret.encode()
    return hashlib.sha256(f"aimarketing-admin:{settings.admin_password}".encode()).digest()


def check_password(password: str) -> bool:
    if not settings.admin_password:
        return False
    return hmac.compare_digest(password, settings.admin_password)


def make_token() -> str:
    expires_at = int(time.time()) + settings.admin_token_ttl_hours * 3600
    signature = hmac.new(_secret(), str(expires_at).encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{signature}"


def verify_token(token: str) -> bool:
    expires_str, _, signature = token.partition(".")
    if not expires_str.isdigit() or not signature:
        return False
    expected = hmac.new(_secret(), expires_str.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    return int(expires_str) > time.time()


def require_admin(authorization: str = Header(default="")) -> None:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not verify_token(token.strip()):
        raise HTTPException(status_code=401, detail="unauthorized")
