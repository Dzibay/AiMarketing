"""«Защита бюджета»: переключатели в настройках проекта и «Включить у всех клиентов» на главной."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import plans
from app.api.web import _http
from app.rules_templates import apply_default, default_plan, protection_state, set_protection
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["budget-protection"])


class ProtectionBody(BaseModel):
    enabled: bool
    # rub | cpa_x; без порога — сохранённый или по умолчанию.
    threshold_kind: str | None = None
    value: float | None = None


class ApplyAllBody(BaseModel):
    workspace_ids: list[int]


@router.get("/workspaces/{workspace_id}/budget-protection")
def web_protection(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        state = protection_state(user_id, workspace_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return {**state, "allowed": plans.has_feature(user_id, "autopilot")}


@router.put("/workspaces/{workspace_id}/budget-protection/{key}")
def web_set_protection(
    workspace_id: int, key: str, body: ProtectionBody, user_id: int = Depends(require_web_user)
) -> dict:
    try:
        # Выключить можно и без автопилота на тарифе — включить или сохранить порог только с ним.
        if body.enabled or body.threshold_kind is not None or body.value is not None:
            plans.require_feature(user_id, "autopilot")
        state = set_protection(
            user_id, workspace_id, key, enabled=body.enabled, threshold_kind=body.threshold_kind, value=body.value
        )
    except YandexError as exc:
        raise _http(exc) from exc
    return {**state, "allowed": plans.has_feature(user_id, "autopilot")}


@router.get("/budget-protection/all")
def web_protection_plan(user_id: int = Depends(require_web_user)) -> dict:
    """Что сделает «Включить у всех клиентов»: проекты без защиты и проверки, которые в них включатся."""
    return {**default_plan(user_id), "allowed": plans.has_feature(user_id, "autopilot")}


@router.post("/budget-protection/all")
def web_protection_apply(body: ApplyAllBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        plans.require_feature(user_id, "autopilot")
        return apply_default(user_id, body.workspace_ids)
    except YandexError as exc:
        raise _http(exc) from exc
