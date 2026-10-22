from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.settings_store import consent_url, cookies_url, offer_url, privacy_url, public_base_url
from app.users import UsersError, get_user, login_user, public_user, register_user
from app.web_auth import make_session, require_web_user, verify_session
from app.yandex import (
    YandexError,
    complete_oauth,
    delete_connection,
    list_connections,
    list_direct,
    list_metrika,
    set_direct_enabled,
    set_metrika_enabled,
    start_oauth,
    sync_connection,
    yandex_configured,
)

router = APIRouter(prefix="/web", tags=["web"])


class RegisterBody(BaseModel):
    email: str = ""
    password: str = ""
    name: str = ""


class LoginBody(BaseModel):
    email: str = ""
    password: str = ""


class EnabledBody(BaseModel):
    enabled: bool = True


def _http(exc: UsersError | YandexError) -> HTTPException:
    code = exc.code
    status = 400
    if code in ("unauthorized", "credentials", "state", "code"):
        status = 401
    elif code == "blocked":
        status = 403
    elif code == "not-found":
        status = 404
    elif code in ("exists", "yandex-taken"):
        status = 409
    return HTTPException(status_code=status, detail=code)


def _frontend_oauth_redirect(*, token: str = "", error: str = "") -> RedirectResponse:
    base = public_base_url() or ""
    query = urlencode({k: v for k, v in (("token", token), ("error", error)) if v})
    target = f"{base}/oauth/done"
    if query:
        target = f"{target}?{query}"
    return RedirectResponse(url=target, status_code=302)


@router.get("/config")
def web_config() -> dict:
    return {
        "yandex_oauth": yandex_configured(),
        "privacy_url": privacy_url() or "/privacy",
        "consent_url": consent_url() or "/consent",
        "offer_url": offer_url() or "/offer",
        "cookies_url": cookies_url() or "/cookies",
    }


@router.post("/register")
def web_register(body: RegisterBody) -> dict:
    try:
        user = register_user(email=body.email, password=body.password, name=body.name)
    except UsersError as exc:
        raise _http(exc) from exc
    return {"token": make_session(user["id"]), "user": public_user(user)}


@router.post("/login")
def web_login(body: LoginBody) -> dict:
    try:
        user = login_user(email=body.email, password=body.password)
    except UsersError as exc:
        raise _http(exc) from exc
    return {"token": make_session(user["id"]), "user": public_user(user)}


@router.get("/me")
def web_me(user_id: int = Depends(require_web_user)) -> dict:
    user = get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if user.get("blocked_at"):
        raise HTTPException(status_code=403, detail="blocked")
    return {
        "user": public_user(user),
        "connections": list_connections(user_id),
        "metrika": list_metrika(user_id),
        "direct": list_direct(user_id),
    }


@router.get("/yandex/start")
def web_yandex_start(
    purpose: str = "login",
    authorization: str = Header(default=""),
) -> dict:
    """purpose=login — вход и регистрация одним кликом; connect — привязка из кабинета."""
    purpose = (purpose or "login").strip().lower()
    if purpose not in ("login", "connect"):
        raise HTTPException(status_code=400, detail="purpose")
    user_id: int | None = None
    if purpose == "connect":
        scheme, _, token = authorization.partition(" ")
        user_id = verify_session(token) if scheme.lower() == "bearer" else None
        if user_id is None:
            raise HTTPException(status_code=401, detail="unauthorized")
    try:
        url = start_oauth(purpose=purpose, user_id=user_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return {"url": url}


@router.get("/yandex/callback")
def web_yandex_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    if error:
        return _frontend_oauth_redirect(error="yandex")
    try:
        user, _stats = complete_oauth(code=code, state=state)
    except (YandexError, UsersError) as exc:
        return _frontend_oauth_redirect(error=exc.code)
    except Exception:
        return _frontend_oauth_redirect(error="yandex")
    return _frontend_oauth_redirect(token=make_session(user["id"]))


@router.post("/connections/{connection_id}/sync")
def web_sync_connection(
    connection_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return sync_connection(user_id, connection_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.delete("/connections/{connection_id}")
def web_delete_connection(
    connection_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        delete_connection(user_id, connection_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.patch("/metrika/{item_id}")
def web_patch_metrika(
    item_id: int,
    body: EnabledBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return set_metrika_enabled(user_id, item_id, body.enabled)
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/direct/{item_id}")
def web_patch_direct(
    item_id: int,
    body: EnabledBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return set_direct_enabled(user_id, item_id, body.enabled)
    except YandexError as exc:
        raise _http(exc) from exc
