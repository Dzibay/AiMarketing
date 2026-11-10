import logging

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app import payments, referrals

from app.config import settings
from app.legal import legal_consent_data, legal_cookies_data, legal_offer_data, legal_privacy_data
from app.settings_store import consent_url, cookies_url, offer_url, privacy_url
from app.yandex import YandexError, yandex_configured

log = logging.getLogger(__name__)
router = APIRouter(tags=["site"])


class RefBody(BaseModel):
    code: str = ""


@router.post("/site/ref")
def site_ref(body: RefBody) -> dict:
    """Первый заход по ?ref=код: +1 переход. Неизвестный или выключенный код — 404, сайт его забудет."""
    if not referrals.visit(body.code):
        raise HTTPException(status_code=404, detail="not-found")
    return {"ok": True}


@router.post("/yookassa/webhook")
def yookassa_webhook(body: dict = Body(default_factory=dict)) -> dict:
    """HTTP-уведомления ЮKassa. Платёж перечитываем через API; не смогли — 500, ЮKassa повторит."""
    try:
        return {"result": payments.handle_notification(body)}
    except YandexError as exc:
        log.warning("Уведомление ЮKassa не обработано: %s", exc.message or exc.code)
        raise HTTPException(status_code=500, detail="retry") from exc


@router.get("/site/config")
def site_config() -> dict:
    return {
        "app_name": settings.app_name,
        "yandex_oauth": yandex_configured(),
        "privacy_url": privacy_url() or "/privacy",
        "consent_url": consent_url() or "/consent",
        "offer_url": offer_url() or "/offer",
        "cookies_url": cookies_url() or "/cookies",
    }


@router.get("/site/legal/{page}")
def site_legal(page: str) -> dict:
    pages = {
        "privacy": legal_privacy_data,
        "consent": legal_consent_data,
        "offer": legal_offer_data,
        "cookies": legal_cookies_data,
    }
    render = pages.get(page)
    if render is None:
        raise HTTPException(status_code=404, detail="not-found")
    return render()
