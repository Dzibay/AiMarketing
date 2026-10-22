from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import check_password, make_token, require_admin
from app.users import count_users, list_users
from app.yandex import yandex_configured

admin_public = APIRouter(tags=["admin"])
router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


class AdminLoginBody(BaseModel):
    password: str = ""


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
