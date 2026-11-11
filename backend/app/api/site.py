import logging
import time

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request
from pydantic import BaseModel

from app import payments, plans, referrals, site_check, site_extras

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


_PLANS_TTL = 300
_plans_cache: dict = {"at": 0.0, "data": None}


@router.get("/site/plans")
def site_plans() -> dict:
    """Тарифы для сайта tolk-ads.ru: при сборке страниц и в браузере. Кэш на 5 минут — сайт дёргает часто."""
    now = time.monotonic()
    if _plans_cache["data"] is None or now - _plans_cache["at"] > _PLANS_TTL:
        _plans_cache["data"] = plans.public_catalog()
        _plans_cache["at"] = now
    return _plans_cache["data"]


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


class SiteCheckBody(BaseModel):
    url: str = ""
    captcha: str = ""


def _client_ip(request: Request) -> str:
    # За Caddy и nginx: первый адрес X-Forwarded-For — клиент.
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or request.headers.get("x-real-ip", "") or (request.client.host if request.client else "")


@router.post("/site/check")
def site_check_start(body: SiteCheckBody, request: Request, background: BackgroundTasks) -> dict:
    """Проверка сайта с лендинга: создать (или взять из кэша) и запустить в фоне; дальше — GET по id."""
    from app.api.web import _http

    try:
        ip = _client_ip(request)
        site_extras.verify_captcha(body.captcha, ip)
        started = site_check.start(body.url, ip)
    except YandexError as exc:
        raise _http(exc) from exc
    if not started["cached"]:
        background.add_task(site_check.run, started["id"])
    return started


@router.get("/site/check/{check_id}")
def site_check_status(check_id: str) -> dict:
    from app.api.web import _http

    try:
        return site_check.public(check_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/site/public")
def site_public() -> dict:
    """Контакты из админки, ключ капчи и включён ли ИИ-режим генераторов — сайт берёт при сборке и в браузере."""
    return site_extras.public()


class WaitlistBody(BaseModel):
    channel: str = ""
    email: str = ""
    consent: bool = False
    captcha: str = ""


@router.post("/site/waitlist")
def site_waitlist(body: WaitlistBody, request: Request) -> dict:
    from app.api.web import _http

    try:
        if not body.consent:
            raise YandexError("consent", "Нужно согласие на обработку персональных данных")
        ip = _client_ip(request)
        site_extras.verify_captcha(body.captcha, ip)
        return site_extras.waitlist_add(body.channel, body.email, ip)
    except YandexError as exc:
        raise _http(exc) from exc


class GenerateBody(BaseModel):
    tool: str = ""
    fields: dict = {}
    captcha: str = ""


@router.post("/site/generate")
def site_generate(body: GenerateBody, request: Request) -> dict:
    """ИИ-режим бесплатных генераторов: минус-слова (tool=minus) и объявления (tool=ads)."""
    from app.api.web import _http

    try:
        ip = _client_ip(request)
        site_extras.verify_captcha(body.captcha, ip)
        return site_extras.generate(body.tool, body.fields, ip)
    except YandexError as exc:
        raise _http(exc) from exc
