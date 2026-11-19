"""Память чистки «для всех проектов» (/api/web/cleaning-memory)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.web import _http
from app.shared_cleaning import add_shared_memory, list_shared_memory, remove_shared_memory
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web/cleaning-memory", tags=["cleaning-memory"])


class SharedBody(BaseModel):
    # Решения «в минус» из памяти проекта…
    workspace_id: int | None = None
    query_norms: list[str] = Field(default_factory=list)
    # …или одно слово / фраза вручную.
    phrase: str | None = None


class RemoveBody(BaseModel):
    ids: list[int] = Field(default_factory=list)


@router.get("")
def web_cleaning_memory(user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_shared_memory(user_id)}


@router.post("")
def web_add_cleaning_memory(body: SharedBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return add_shared_memory(
            user_id, workspace_id=body.workspace_id, query_norms=body.query_norms, phrase=body.phrase
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/remove")
def web_remove_cleaning_memory(body: RemoveBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return {**remove_shared_memory(user_id, body.ids), "items": list_shared_memory(user_id)}
    except YandexError as exc:
        raise _http(exc) from exc
