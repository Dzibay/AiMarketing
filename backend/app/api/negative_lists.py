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
    stale_campaign_ids,
    update_negative_list,
)
from app.shared_cleaning import send_to_list, to_list_preview
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web/negative-lists", tags=["negative-lists"])


class ListBody(BaseModel):
    name: str | None = None
    # Текст (строки, запятые, «-слово -слово») или массив фраз.
    phrases: str | list[str] | None = None


class TargetsBody(BaseModel):
    campaign_row_ids: list[int] = Field(default_factory=list)


class FromQueriesBody(BaseModel):
    workspace_id: int
    # [{query_norm, negative_phrase}] — фраза из чистки; пустая — сам запрос.
    items: list[dict] = Field(default_factory=list)
    force: bool = False


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


@router.post("/{list_id}/catch-up/preview")
def web_negative_list_catch_up_preview(list_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """«Догнать N кампаний»: предпросмотр только по отставшим кампаниям списка; остальные привязки не трогаем."""
    try:
        ids = stale_campaign_ids(user_id, list_id)
        return {**preview_apply(user_id, list_id, ids, catch_up=True), "campaign_row_ids": ids}
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{list_id}/catch-up")
def web_negative_list_catch_up(list_id: int, body: TargetsBody, user_id: int = Depends(require_web_user)) -> dict:
    """После подтверждения: та же задача применения, но только по отставшим кампаниям (журнал, откат, колокольчик)."""
    try:
        preview_apply(user_id, list_id, body.campaign_row_ids, catch_up=True)
    except YandexError as exc:
        raise _http(exc) from exc
    return _start_job(
        "apply_negative_list",
        user_id,
        {"list_id": list_id, "campaign_row_ids": sorted(set(body.campaign_row_ids)), "catch_up": True},
    )


@router.post("/{list_id}/from-queries/preview")
def web_negative_list_from_queries_preview(
    list_id: int, body: FromQueriesBody, user_id: int = Depends(require_web_user)
) -> dict:
    """Чистка «В общий список …»: кампании списка и пересечения с их ключами. Ничего не меняет."""
    try:
        return to_list_preview(user_id, body.workspace_id, list_id, body.items)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{list_id}/from-queries")
def web_negative_list_from_queries(list_id: int, body: FromQueriesBody, user_id: int = Depends(require_web_user)) -> dict:
    """Фразы — в список и в память проекта; затем задача «догнать» пишет их во все кампании списка."""
    try:
        result = send_to_list(user_id, body.workspace_id, list_id, body.items, force=body.force)
    except YandexError as exc:
        raise _http(exc) from exc
    job = None
    if result["stale_ids"]:
        job = _start_job(
            "apply_negative_list",
            user_id,
            {"list_id": list_id, "campaign_row_ids": result["stale_ids"], "catch_up": True, "only_add": result["phrases"]},
        )["job"]
    return {"result": result, "job": job}
