"""Общие списки минус-фраз (/api/web/negative-lists)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.web import _http, _start_job
from app.negative_lists import (
    TEMPLATES,
    create_negative_list,
    delete_negative_list,
    get_negative_list,
    list_negative_lists,
    list_targets,
    preview_apply,
    update_negative_list,
)
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web/negative-lists", tags=["negative-lists"])


class ListBody(BaseModel):
    name: str | None = None
    # Текст (строки, запятые, «-слово -слово») или массив фраз.
    phrases: str | list[str] | None = None


class TargetsBody(BaseModel):
    campaign_row_ids: list[int] = Field(default_factory=list)


@router.get("")
def web_negative_lists(user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_negative_lists(user_id), "templates": TEMPLATES}


@router.post("")
def web_create_negative_list(body: ListBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return create_negative_list(user_id, name=body.name or "", phrases=body.phrases)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/targets")
def web_negative_list_targets(user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_targets(user_id)}


@router.get("/{list_id}")
def web_negative_list(list_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return get_negative_list(user_id, list_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/{list_id}")
def web_update_negative_list(list_id: int, body: ListBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return update_negative_list(user_id, list_id, name=body.name, phrases=body.phrases)
    except YandexError as exc:
        raise _http(exc) from exc


@router.delete("/{list_id}")
def web_delete_negative_list(list_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        delete_negative_list(user_id, list_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.post("/{list_id}/preview")
def web_negative_list_preview(list_id: int, body: TargetsBody, user_id: int = Depends(require_web_user)) -> dict:
    """Что изменится в каждой кампании; в Директ ничего не пишет."""
    try:
        return preview_apply(user_id, list_id, body.campaign_row_ids)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{list_id}/apply")
def web_negative_list_apply(list_id: int, body: TargetsBody, user_id: int = Depends(require_web_user)) -> dict:
    """После подтверждения предпросмотра: запись в Директ задачей очереди."""
    try:
        # Проверяем список и кампании сразу, а не ошибкой задачи.
        preview_apply(user_id, list_id, body.campaign_row_ids)
    except YandexError as exc:
        raise _http(exc) from exc
    return _start_job(
        "apply_negative_list",
        user_id,
        {"list_id": list_id, "campaign_row_ids": sorted(set(body.campaign_row_ids))},
    )
