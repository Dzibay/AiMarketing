"""API ИИ-ассистента: подключение провайдера (BYOK), чаты пространства, согласование действий."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import ai, ai_agent
from app.api.web import _http
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["ai"])


class AiSettingsBody(BaseModel):
    provider: str = "openai"
    base_url: str = ""
    model: str = ""
    folder_id: str = ""
    # None — оставить сохранённый ключ, "" — удалить.
    api_key: str | None = None
    temperature: float | None = None


class ChatBody(BaseModel):
    title: str = ""


class MessageBody(BaseModel):
    text: str


class DecideBody(BaseModel):
    approve: bool
    remember: bool = False


class DecideAllBody(BaseModel):
    approve: bool


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/ai/settings")
def ai_settings_get(user_id: int = Depends(require_web_user)) -> dict:
    return ai.get_settings(user_id)


@router.put("/ai/settings")
def ai_settings_put(body: AiSettingsBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai.save_settings, user_id, **body.model_dump())


@router.post("/ai/settings/test")
def ai_settings_test(user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai.check_connection, user_id)


@router.get("/workspaces/{workspace_id}/chats")
def chats_list(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    return {"items": _call(ai_agent.list_chats, user_id, workspace_id)}


@router.post("/workspaces/{workspace_id}/chats")
def chats_create(workspace_id: int, body: ChatBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.create_chat, user_id, workspace_id, body.title)


@router.get("/ai/chats/{chat_id}")
def chat_get(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.get_chat, user_id, chat_id)


@router.patch("/ai/chats/{chat_id}")
def chat_rename(chat_id: int, body: ChatBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.rename_chat, user_id, chat_id, body.title)


@router.delete("/ai/chats/{chat_id}")
def chat_delete(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _call(ai_agent.delete_chat, user_id, chat_id)
    return {"ok": True}


@router.post("/ai/chats/{chat_id}/messages")
def chat_message(chat_id: int, body: MessageBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.post_message, user_id, chat_id, body.text)


@router.post("/ai/chats/{chat_id}/decide-all")
def chat_decide_all(chat_id: int, body: DecideAllBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.decide_all, user_id, chat_id, approve=body.approve)


@router.post("/ai/actions/{action_id}/decide")
def action_decide(action_id: int, body: DecideBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.decide_action, user_id, action_id, approve=body.approve, remember=body.remember)
