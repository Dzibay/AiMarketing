"""Сводка по клиентам, чек-лист первых шагов и утренний дайджест (/api/web/overview, /onboarding, /digest)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.web import _http, _start_job
from app.digest import build_digest, get_digest_settings, update_digest_settings
from app.onboarding import get_onboarding, set_onboarding_dismissed
from app.overview import build_overview
from app.telegram_notify import notify_user
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["overview"])


class DigestBody(BaseModel):
    enabled: bool | None = None
    hour: int | None = None


@router.get("/overview")
def web_overview(user_id: int = Depends(require_web_user)) -> dict:
    return build_overview(user_id)


@router.post("/overview/refresh")
def web_overview_refresh(user_id: int = Depends(require_web_user)) -> dict:
    """Расход за сегодня и балансы — задачей; фронт ждёт её и перечитывает сводку."""
    return _start_job("stats_today", user_id, {})


@router.get("/onboarding")
def web_onboarding(user_id: int = Depends(require_web_user)) -> dict:
    return get_onboarding(user_id)


@router.post("/onboarding/dismiss")
def web_onboarding_dismiss(user_id: int = Depends(require_web_user)) -> dict:
    """Скрыть чек-лист насовсем — запоминаем у пользователя, а не в браузере."""
    return set_onboarding_dismissed(user_id)


@router.delete("/onboarding/dismiss")
def web_onboarding_restore(user_id: int = Depends(require_web_user)) -> dict:
    return set_onboarding_dismissed(user_id, False)


@router.get("/digest")
def web_digest_settings(user_id: int = Depends(require_web_user)) -> dict:
    try:
        return get_digest_settings(user_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.put("/digest")
def web_update_digest(body: DigestBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return update_digest_settings(user_id, enabled=body.enabled, hour=body.hour)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/digest/preview")
def web_digest_preview(user_id: int = Depends(require_web_user)) -> dict:
    return {"text": build_digest(user_id)}


@router.post("/digest/send")
def web_digest_send(user_id: int = Depends(require_web_user)) -> dict:
    """Отправить сейчас по уже загруженным данным; утреннюю отправку не отменяет."""
    if not notify_user(user_id, build_digest(user_id)):
        raise HTTPException(status_code=400, detail="telegram")
    return {"ok": True}
