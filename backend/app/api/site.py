from fastapi import APIRouter, HTTPException

from app.config import settings
from app.legal import legal_consent_data, legal_cookies_data, legal_offer_data, legal_privacy_data
from app.settings_store import consent_url, cookies_url, offer_url, privacy_url
from app.yandex import yandex_configured

router = APIRouter(tags=["site"])


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
