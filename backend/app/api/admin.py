from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import check_password, make_token, require_admin
from app.users import UsersError, count_users, list_users, set_user_blocked
from app.yandex import yandex_configured

admin_public = APIRouter(tags=["admin"])
router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


class AdminLoginBody(BaseModel):
    password: str = ""


class BlockBody(BaseModel):
    reason: str = ""


@admin_public.post("/login")
def admin_login(body: AdminLoginBody) -> dict:
    if not check_password(body.password or ""):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"token": make_token()}


@router.get("/me")
def admin_me() -> dict:
    return {
        "ok": True,
        "users_total": count_users(),
        "yandex_oauth": yandex_configured(),
    }


@router.get("/users")
def admin_users(limit: int = 100, offset: int = 0) -> dict:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return {"items": list_users(limit=limit, offset=offset), "total": count_users()}


@router.post("/users/{user_id}/block")
def admin_block_user(user_id: int, body: BlockBody | None = None) -> dict:
    reason = (body.reason if body else "") or ""
    try:
        return set_user_blocked(user_id, blocked=True, reason=reason)
    except UsersError as exc:
        status = 404 if exc.code == "not-found" else 400
        raise HTTPException(status_code=status, detail=exc.code) from exc


@router.post("/users/{user_id}/unblock")
def admin_unblock_user(user_id: int) -> dict:
    try:
        return set_user_blocked(user_id, blocked=False)
    except UsersError as exc:
        status = 404 if exc.code == "not-found" else 400
        raise HTTPException(status_code=status, detail=exc.code) from exc
