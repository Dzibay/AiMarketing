"""«Для новых проектов» в общих настройках и «Взять из проекта…» в настройках проекта."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import plans, project_defaults as pd
from app.api.web import _http
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["project-defaults"])


class DefaultsBody(BaseModel):
    # Присланное поле меняется; null — «не задавать».
    url_template: str | None = None
    model_class: str | None = None
    ai_access: dict | None = None
    kpi: dict | None = None
    budget_protection: dict | None = None


class KindsBody(BaseModel):
    kinds: list[str]


class CopyBody(BaseModel):
    source_id: int
    kinds: list[str]


def _out(user_id: int, defaults: dict) -> dict:
    return {"defaults": defaults, **pd.defaults_meta(), "protection_allowed": plans.has_feature(user_id, "autopilot")}


@router.get("/me/project-defaults")
def web_get_defaults(user_id: int = Depends(require_web_user)) -> dict:
    try:
        return _out(user_id, pd.get_defaults(user_id))
    except YandexError as exc:
        raise _http(exc) from exc


@router.put("/me/project-defaults")
def web_set_defaults(body: DefaultsBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return _out(user_id, pd.set_defaults(user_id, body.model_dump(exclude_unset=True)))
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/workspaces/{workspace_id}/as-defaults")
def web_as_defaults(workspace_id: int, body: KindsBody, user_id: int = Depends(require_web_user)) -> dict:
    """«Сделать так по умолчанию?»: значения этого проекта — для новых проектов."""
    try:
        return _out(user_id, pd.save_as_defaults(user_id, workspace_id, body.kinds))
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/workspaces/{workspace_id}/copy-from/{source_id}")
def web_copy_preview(workspace_id: int, source_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """«Взять из проекта…»: было → станет по каждому полю."""
    try:
        return pd.copy_preview(user_id, workspace_id, source_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/workspaces/{workspace_id}/copy-from")
def web_copy_apply(workspace_id: int, body: CopyBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return pd.copy_apply(user_id, workspace_id, body.source_id, body.kinds)
    except YandexError as exc:
        raise _http(exc) from exc
