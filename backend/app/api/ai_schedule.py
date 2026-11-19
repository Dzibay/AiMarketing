"""Разбор недели по расписанию: настройка в «Настройки → ИИ» (app.ai_review)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.ai_review import get_schedule, save_schedule
from app.api.web import _http
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["ai-schedule"])


class ScheduleBody(BaseModel):
    enabled: bool
    weekday: int | None = None
    hour: int | None = None
    # all — все проекты; selected — только workspace_ids.
    scope: str | None = None
    workspace_ids: list[int] | None = None


@router.get("/ai-schedule")
def web_ai_schedule(user_id: int = Depends(require_web_user)) -> dict:
    return get_schedule(user_id)


@router.put("/ai-schedule")
def web_set_ai_schedule(body: ScheduleBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return save_schedule(
            user_id,
            enabled=body.enabled,
            weekday=body.weekday,
            hour=body.hour,
            scope=body.scope,
            workspace_ids=body.workspace_ids,
        )
    except YandexError as exc:
        raise _http(exc) from exc
