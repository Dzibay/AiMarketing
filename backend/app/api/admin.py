"""Админка владельца: пользователи, лимиты единиц, общие ИИ и сервисы, расход.

Вход — обычной сессией кабинета; доступ — только email из ADMIN_EMAILS (web_auth.require_admin).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import ai, ai_bridge, billing, image_gen, wordstat
from app.api.web import _http
from app.users import UsersError, count_users, get_user, list_users, set_user_blocked
from app.web_auth import require_admin
from app.yandex import YandexError, yandex_configured

router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin)])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except YandexError as exc:
        raise _http(exc) from exc


def _users_error(exc: UsersError) -> HTTPException:
    return HTTPException(status_code=404 if exc.code == "not-found" else 400, detail=exc.code)


# --- Обзор и пользователи -----------------------------------------------------------------------


@router.get("/me")
def admin_me(user_id: int = Depends(require_admin)) -> dict:
    user = get_user(user_id) or {}
    return {
        "ok": True,
        "email": user.get("email") or "",
        "users_total": count_users(),
        "yandex_oauth": yandex_configured(),
        "ai_configured": ai.is_configured(),
        "wordstat_configured": wordstat.is_configured(),
        "images_configured": image_gen.is_configured(),
    }


class BlockBody(BaseModel):
    reason: str = ""


class LimitBody(BaseModel):
    # None — общий лимит сервиса, число — свой лимит единиц в месяц.
    units_limit: float | None = None


@router.get("/users")
def admin_users(limit: int = 100, offset: int = 0) -> dict:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    items = list_users(limit=limit, offset=offset)
    usage = billing.users_usage([u["id"] for u in items])
    empty = {"spent": 0.0, "limit": billing.default_limit(), "own_limit": None, "cost_rub": 0.0, "calls": 0}
    return {
        "items": [{**u, "usage": usage.get(u["id"], empty)} for u in items],
        "total": count_users(),
        "default_units_limit": billing.default_limit(),
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


@router.put("/users/{user_id}/limit")
def admin_user_limit(user_id: int, body: LimitBody) -> dict:
    return _call(billing.set_user_limit, user_id, body.units_limit)


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
    # Общий лимит единиц в месяц; не передан — как был, null — без лимита.
    default_units_limit: float | None = None


@router.get("/ai")
def admin_ai_get() -> dict:
    return ai.get_service_settings()


@router.put("/ai")
def admin_ai_put(body: AiServiceBody) -> dict:
    data = body.model_dump()
    if data["classes"] is not None:
        data["classes"] = [dict(c) for c in data["classes"]]
    if "default_units_limit" not in body.model_fields_set:
        data.pop("default_units_limit")
    return _call(ai.save_service_settings, **data)


@router.post("/ai/test")
def admin_ai_test() -> dict:
    """Короткий запрос к каждому включённому классу — без списания единиц."""
    return _call(ai.check_connection)


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
