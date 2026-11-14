"""Админка владельца: пользователи и их подписки, тарифы, общие ИИ и сервисы, расход.

Вход — отдельный, по паролю ADMIN_PASSWORD (auth.login); админ — не пользователь кабинета, сессии
кабинета сюда не пускают.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import ai, ai_bridge, auth, billing, events, image_gen, mailer, payments, plans, promos, referrals, site_check, site_extras, wordstat
from app.api.web import _http
from app.users import UsersError, count_users, list_users, mark_email_verified, set_user_blocked
from app.yandex import YandexError, yandex_configured

admin_public = APIRouter(tags=["admin"])
router = APIRouter(tags=["admin"], dependencies=[Depends(auth.require_admin)])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except YandexError as exc:
        raise _http(exc) from exc


def _users_error(exc: UsersError) -> HTTPException:
    return HTTPException(status_code=404 if exc.code == "not-found" else 400, detail=exc.code)


# --- Обзор и пользователи -----------------------------------------------------------------------


class LoginBody(BaseModel):
    password: str = ""


def _client(request: Request) -> str:
    # За Caddy и nginx: первый адрес X-Forwarded-For — клиент (Caddy подставляет его сам).
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or request.headers.get("x-real-ip", "") or (request.client.host if request.client else "")


@admin_public.post("/login")
def admin_login(body: LoginBody, request: Request) -> dict:
    try:
        return {"token": auth.login(body.password, _client(request))}
    except auth.LoginError as exc:
        raise HTTPException(status_code=429 if exc.code == "too-many-attempts" else 401, detail=exc.code) from exc


@router.get("/me")
def admin_me() -> dict:
    return {
        "ok": True,
        "users_total": count_users(),
        "yandex_oauth": yandex_configured(),
        "ai_configured": ai.is_configured(),
        "wordstat_configured": wordstat.is_configured(),
        "images_configured": image_gen.is_configured(),
        # Без SMTP письма не уходят — подтверждение почты при регистрации выключено.
        "email_check": plans.email_check_required(),
        "smtp_configured": mailer.configured(),
        "trial_plan": next((p["name"] for p in plans.list_plans(for_admin=True) if p["is_trial"] and p["active"]), None),
        "payments_ready": payments.is_ready(),
        "payments": payments.month_revenue(),
    }


class BlockBody(BaseModel):
    reason: str = ""


class SubscriptionBody(BaseModel):
    # Поля, которых нет в запросе, не меняются. plan_id null — без тарифа; until null — без срока.
    plan_id: int | None = None
    # Последний день подписки «ГГГГ-ММ-ДД» включительно.
    until: str | None = None
    # Поверх тарифа: {"units_limit": число | null, "projects_limit": число | null, "features": {...}}.
    overrides: dict | None = None
    # Продлить на срок тарифа.
    renew: bool = False


@router.get("/users")
def admin_users(limit: int = 100, offset: int = 0) -> dict:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    items = list_users(limit=limit, offset=offset)
    usage = billing.users_usage([u["id"] for u in items])
    return {
        "items": [
            {**u, "usage": usage.get(u["id"]), "subscription": plans.admin_view(u["id"])} for u in items
        ],
        "total": count_users(),
    }


@router.post("/users/{user_id}/block")
def admin_block_user(user_id: int, body: BlockBody | None = None) -> dict:
    reason = (body.reason if body else "") or ""
    try:
        return set_user_blocked(user_id, blocked=True, reason=reason)
    except UsersError as exc:
        raise _users_error(exc) from exc


@router.post("/users/{user_id}/unblock")
def admin_unblock_user(user_id: int) -> dict:
    try:
        return set_user_blocked(user_id, blocked=False)
    except UsersError as exc:
        raise _users_error(exc) from exc


@router.put("/users/{user_id}/subscription")
def admin_user_subscription(user_id: int, body: SubscriptionBody) -> dict:
    fields = body.model_fields_set
    kwargs: dict = {"renew": body.renew}
    if "plan_id" in fields:
        kwargs["plan_id"] = body.plan_id
    if "until" in fields:
        kwargs["until"] = body.until
    if "overrides" in fields:
        kwargs["overrides"] = body.overrides or {}
    return _call(plans.set_subscription, user_id, **kwargs)


@router.post("/users/{user_id}/verify-email")
def admin_user_verify_email(user_id: int) -> dict:
    mark_email_verified(user_id)
    return {"ok": True}


@router.get("/users/{user_id}/events")
def admin_user_events(user_id: int, limit: int = 50) -> dict:
    return {"items": events.list_events(user_id, max(1, min(limit, 200))), "kinds": events.KINDS}


# --- Тарифы --------------------------------------------------------------------------------------


class PlanBody(BaseModel):
    name: str = ""
    price_rub: float | None = None
    days: int | None = None
    # null — без лимита.
    units_limit: float | None = None
    projects_limit: int | None = None
    features: dict[str, bool] = {}
    is_trial: bool = False
    active: bool = True
    position: int = 0
    # Скидка за оплату сразу на 3 и 12 сроков тарифа, %.
    discount_3_pct: float | None = None
    discount_12_pct: float | None = None


class UpgradeBody(BaseModel):
    from_plan_id: int
    to_plan_id: int
    # Пусто или 0 — без скидки.
    discount_pct: float | None = None


@router.get("/plans")
def admin_plans() -> dict:
    return {"items": plans.list_plans(for_admin=True), "upgrades": plans.list_upgrades(), "features": plans.FEATURES}


@router.post("/plans")
def admin_plan_create(body: PlanBody) -> dict:
    return _call(plans.save_plan, None, body.model_dump())


@router.put("/plans/{plan_id}")
def admin_plan_update(plan_id: int, body: PlanBody) -> dict:
    return _call(plans.save_plan, plan_id, body.model_dump())


@router.delete("/plans/{plan_id}")
def admin_plan_delete(plan_id: int) -> dict:
    _call(plans.delete_plan, plan_id)
    return {"ok": True}


@router.put("/plan-upgrades")
def admin_plan_upgrade(body: UpgradeBody) -> dict:
    return _call(plans.set_upgrade, body.from_plan_id, body.to_plan_id, body.discount_pct)


# --- ИИ: ключ и классы моделей -------------------------------------------------------------------


class ModelClassBody(BaseModel):
    id: str
    name: str = ""
    model: str = ""
    # Цена для пользователя — единиц за 1 млн токенов; себестоимость — ₽ за 1 млн токенов.
    units_in: float | None = None
    units_out: float | None = None
    cost_in: float | None = None
    cost_out: float | None = None
    # Кэшированный вход; пусто — как обычный вход.
    units_cache: float | None = None
    cost_cache: float | None = None
    enabled: bool = True


class AiServiceBody(BaseModel):
    provider: str = "yandexgpt"
    base_url: str = ""
    folder_id: str = ""
    # None — оставить сохранённый ключ, "" — удалить.
    api_key: str | None = None
    temperature: float | None = None
    classes: list[ModelClassBody] | None = None
    # {задача: класс} — фоновые задачи (разметка, бриф, площадки…).
    task_classes: dict[str, str] | None = None


@router.get("/ai")
def admin_ai_get() -> dict:
    return ai.get_service_settings()


@router.put("/ai")
def admin_ai_put(body: AiServiceBody) -> dict:
    data = body.model_dump()
    if data["classes"] is not None:
        data["classes"] = [dict(c) for c in data["classes"]]
    return _call(ai.save_service_settings, **data)


@router.post("/ai/test")
def admin_ai_test() -> dict:
    """Короткий запрос к каждому включённому классу — без списания единиц."""
    return _call(ai.check_connection)


# --- Проверка сайта на лендинге ------------------------------------------------------------------


class SiteCheckBody(BaseModel):
    enabled: bool = True
    # Пусто — модель класса задачи «Проверка сайта на лендинге». Адрес API и ключ — общие (раздел ИИ).
    model: str = ""
    ip_daily: int | None = None
    daily: int | None = None
    cache_days: int | None = None


@router.get("/site-check")
def admin_site_check_get() -> dict:
    return site_check.admin_overview()


@router.put("/site-check")
def admin_site_check_put(body: SiteCheckBody) -> dict:
    return _call(site_check.save_settings, **body.model_dump())


@router.post("/site-check/test")
def admin_site_check_test() -> dict:
    """Короткий запрос к модели проверки — без списания единиц."""
    return _call(site_check.test_model)


# --- Сайт: контакты, капча, лист ожидания, ИИ-генераторы ----------------------------------------


class SiteSettingsBody(BaseModel):
    email: str = ""
    legal: str = ""
    telegram: str = ""
    captcha_client_key: str = ""
    # None — серверный ключ не трогаем (в форму он не возвращается), "" — стереть.
    captcha_server_key: str | None = None
    tools_enabled: bool = True
    tools_ip_daily: int = site_extras.TOOLS_DEFAULTS["ip_daily"]
    tools_daily: int = site_extras.TOOLS_DEFAULTS["daily"]


def _site_settings() -> dict:
    return {
        "contacts": site_extras.contacts(),
        "captcha": site_extras.captcha_settings(),
        "tools": site_extras.tools_settings(),
    }


@router.get("/site")
def admin_site_get() -> dict:
    return _site_settings()


@router.put("/site")
def admin_site_put(body: SiteSettingsBody) -> dict:
    _call(site_extras.save_contacts, email=body.email, legal=body.legal, telegram=body.telegram)
    _call(site_extras.save_tools, enabled=body.tools_enabled, ip_daily=body.tools_ip_daily, daily=body.tools_daily)
    site_extras.save_captcha(client_key=body.captcha_client_key, server_key=body.captcha_server_key)
    return _site_settings()


@router.get("/site/waitlist")
def admin_site_waitlist() -> dict:
    return site_extras.waitlist_overview()


@router.delete("/site/waitlist/{item_id}")
def admin_site_waitlist_delete(item_id: int) -> dict:
    return site_extras.waitlist_delete(item_id)


# --- Мост Cloudflare -----------------------------------------------------------------------------


class BridgeSettingsBody(BaseModel):
    enabled: bool = False
    # worker — свой Worker в США, gateway — Cloudflare AI Gateway.
    kind: str = "worker"
    account_id: str = ""
    gateway_id: str = ""
    # None — оставить сохранённый токен шлюза / секрет Worker'а, "" — удалить.
    token: str | None = None
    worker_url: str = ""
    worker_token: str | None = None
    use_chat: bool = True
    use_images: bool = True


class BridgeDeployBody(BaseModel):
    account_id: str
    # API-токен Cloudflare (шаблон «Edit Cloudflare Workers») — только на время развёртывания, не хранится.
    api_token: str


@router.get("/bridge")
def admin_bridge_get() -> dict:
    return ai_bridge.get_settings()


@router.put("/bridge")
def admin_bridge_put(body: BridgeSettingsBody) -> dict:
    return _call(ai_bridge.save_settings, **body.model_dump())


@router.post("/bridge/deploy")
def admin_bridge_deploy(body: BridgeDeployBody) -> dict:
    return _call(ai_bridge.deploy_worker, account_id=body.account_id, api_token=body.api_token)


@router.post("/bridge/test")
def admin_bridge_test() -> dict:
    return _call(ai_bridge.check_worker)


# --- Вордстат ------------------------------------------------------------------------------------


class WordstatBody(BaseModel):
    folder_id: str = ""
    # None — оставить сохранённый ключ, "" — удалить.
    api_key: str | None = None
    # Не переданы — как были. Цена запроса в единицах; себестоимость — ₽ за запрос.
    units_per_request: float | None = None
    cost_per_request: float | None = None


@router.get("/wordstat")
def admin_wordstat_get() -> dict:
    return wordstat.get_settings()


@router.put("/wordstat")
def admin_wordstat_put(body: WordstatBody) -> dict:
    data = body.model_dump()
    for name in ("units_per_request", "cost_per_request"):
        if name not in body.model_fields_set:
            data.pop(name)
    return _call(wordstat.save_settings, **data)


@router.post("/wordstat/test")
def admin_wordstat_test() -> dict:
    return _call(wordstat.check_connection)


# --- Генерация картинок --------------------------------------------------------------------------


class ImagesBody(BaseModel):
    provider: str = "yandexart"
    base_url: str = ""
    model: str = ""
    folder_id: str = ""
    # None — оставить сохранённый ключ, "" — удалить.
    api_key: str | None = None
    # Не переданы — как были. Цена картинки в единицах; себестоимость — ₽ за картинку.
    units_per_image: float | None = None
    cost_per_image: float | None = None


@router.get("/images")
def admin_images_get() -> dict:
    return image_gen.get_settings()


@router.put("/images")
def admin_images_put(body: ImagesBody) -> dict:
    data = body.model_dump()
    for name in ("units_per_image", "cost_per_image"):
        if name not in body.model_fields_set:
            data.pop(name)
    return _call(image_gen.save_settings, **data)


@router.post("/images/test")
def admin_images_test() -> dict:
    """Одна настоящая картинка — платно для сервиса, без списания единиц."""
    return _call(image_gen.check_connection)


# --- Расход --------------------------------------------------------------------------------------


@router.get("/usage")
def admin_usage(month: str = "") -> dict:
    return _call(billing.service_usage, month or None)


# --- Оплата (ЮKassa) -----------------------------------------------------------------------------


class PaymentsSettingsBody(BaseModel):
    shop_id: str = ""
    # None — оставить сохранённый ключ, "" — удалить.
    secret_key: str | None = None
    test_mode: bool = True
    enabled: bool = False


class ReceiptBody(BaseModel):
    done: bool = True


@router.get("/payments/settings")
def admin_payments_settings() -> dict:
    return payments.get_settings()


@router.put("/payments/settings")
def admin_payments_settings_put(body: PaymentsSettingsBody) -> dict:
    return _call(payments.save_settings, **body.model_dump())


@router.post("/payments/settings/test")
def admin_payments_settings_test() -> dict:
    return _call(payments.check_connection)


@router.get("/payments")
def admin_payments(user_id: int | None = None, status: str = "", limit: int = 300) -> dict:
    return payments.admin_list(user_id=user_id, status=status, limit=limit)


@router.post("/payments/{payment_id}/sync")
def admin_payment_sync(payment_id: int) -> dict:
    """Перечитать платёж из ЮKassa: статус и сумму возврата."""
    return _call(payments.admin_refresh, payment_id)


@router.put("/payments/{payment_id}/receipt")
def admin_payment_receipt(payment_id: int, body: ReceiptBody) -> dict:
    """Отметка: чек самозанятого пробит в «Мой налог»."""
    return _call(payments.set_receipt, payment_id, body.done)


# --- Промокоды -----------------------------------------------------------------------------------


class PromoBody(BaseModel):
    code: str = ""
    kind: str = "days"
    units: float | None = None
    days: int | None = None
    plan_id: int | None = None
    max_uses: int | None = None
    # Последний день действия «ГГГГ-ММ-ДД»; пусто — бессрочно.
    valid_until: str | None = None
    active: bool = True
    note: str = ""


@router.get("/promos")
def admin_promos() -> dict:
    return {"items": promos.list_promos(), "kinds": promos.KINDS}


@router.post("/promos")
def admin_promo_create(body: PromoBody) -> dict:
    return _call(promos.save_promo, None, body.model_dump())


@router.put("/promos/{promo_id}")
def admin_promo_update(promo_id: int, body: PromoBody) -> dict:
    return _call(promos.save_promo, promo_id, body.model_dump())


@router.delete("/promos/{promo_id}")
def admin_promo_delete(promo_id: int) -> dict:
    _call(promos.delete_promo, promo_id)
    return {"ok": True}


# --- Реф-ссылки ----------------------------------------------------------------------------------


class RefLinkBody(BaseModel):
    code: str = ""
    name: str = ""
    active: bool = True


class RefBonusBody(BaseModel):
    referrer_days: int = 0
    invitee_days: int = 0


@router.get("/refs")
def admin_refs() -> dict:
    """Ссылки из админки; личные ссылки пользователей — одной сводкой, и бонус за приглашение."""
    return {"items": referrals.list_links(), "bonus": referrals.bonus_settings(), "personal": referrals.personal_summary()}


@router.put("/ref-bonus")
def admin_ref_bonus(body: RefBonusBody) -> dict:
    return _call(referrals.save_bonus_settings, body.model_dump())


@router.post("/refs")
def admin_ref_create(body: RefLinkBody) -> dict:
    return _call(referrals.save_link, None, body.model_dump())


@router.put("/refs/{link_id}")
def admin_ref_update(link_id: int, body: RefLinkBody) -> dict:
    return _call(referrals.save_link, link_id, body.model_dump())


@router.delete("/refs/{link_id}")
def admin_ref_delete(link_id: int) -> dict:
    _call(referrals.delete_link, link_id)
    return {"ok": True}
