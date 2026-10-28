"""API ИИ-ассистента: подключение провайдера (BYOK), чаты пространства, согласование действий."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import ai, ai_agent, experiments, insights, workspaces
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
    # Не переданы — остаются как были; null — очистить. Цены — ₽ за 1 млн токенов.
    model_fast: str = ""
    price_in: float | None = None
    price_out: float | None = None
    fast_price_in: float | None = None
    fast_price_out: float | None = None
    monthly_limit_rub: float | None = None


_KEEP_IF_UNSET = ("model_fast", "price_in", "price_out", "fast_price_in", "fast_price_out", "monthly_limit_rub")


class ChatBody(BaseModel):
    title: str = ""


class MessageBody(BaseModel):
    text: str


class DecideBody(BaseModel):
    approve: bool
    remember: bool = False


class DecideAllBody(BaseModel):
    approve: bool


class AnswerBody(BaseModel):
    # [{n, selected: [вариант…], text}] — n — номер вопроса в карточке.
    answers: list[dict] = []
    comment: str = ""
    skip: bool = False


class PlanBody(BaseModel):
    approve: bool = True
    # Номера согласованных шагов плана.
    steps: list[int] = []
    comment: str = ""


class ReviewBody(BaseModel):
    # Пусто — общий разбор пространства; текст карточки сигнала — разбор по ней.
    prompt: str = ""
    title: str = ""
    # Не задан — разбор в новом чате.
    chat_id: int | None = None


class ExperimentReviewBody(BaseModel):
    # Не задан — разбор итогов в новом чате.
    chat_id: int | None = None


class ExperimentFinishBody(BaseModel):
    # success | fail | unclear; при отмене не нужен.
    verdict: str = ""
    conclusion: str = ""
    cancel: bool = False


class ForgetFactBody(BaseModel):
    key: str


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
    data = body.model_dump()
    for name in _KEEP_IF_UNSET:
        if name not in body.model_fields_set:
            data.pop(name)
    return _call(ai.save_settings, user_id, **data)


@router.post("/ai/settings/test")
def ai_settings_test(user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai.check_connection, user_id)


@router.get("/ai/usage")
def ai_usage(month: str = "", user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai.usage_summary, user_id, month or None)


@router.get("/workspaces/{workspace_id}/chats")
def chats_list(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    return {"items": _call(ai_agent.list_chats, user_id, workspace_id)}


@router.post("/workspaces/{workspace_id}/chats")
def chats_create(workspace_id: int, body: ChatBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.create_chat, user_id, workspace_id, body.title)


@router.get("/workspaces/{workspace_id}/signals")
def workspace_signals(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Чем ИИ может помочь — считает код без ИИ, поэтому бесплатно."""
    return _call(insights.signals_overview, user_id, workspace_id)


@router.post("/workspaces/{workspace_id}/review")
def workspace_review(workspace_id: int, body: ReviewBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.start_review, user_id, workspace_id, prompt=body.prompt, title=body.title,
                 chat_id=body.chat_id)


@router.get("/workspaces/{workspace_id}/experiments")
def workspace_experiments(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Эксперименты ИИ с прогрессом — считает код по загруженной статистике, без ИИ."""
    return _call(experiments.experiments_overview, user_id, workspace_id)


@router.post("/workspaces/{workspace_id}/experiments/{experiment_id}/review")
def experiment_review(
    workspace_id: int, experiment_id: int, body: ExperimentReviewBody, user_id: int = Depends(require_web_user)
) -> dict:
    return _call(ai_agent.start_experiment_review, user_id, workspace_id, experiment_id, chat_id=body.chat_id)


@router.post("/workspaces/{workspace_id}/experiments/{experiment_id}/finish")
def experiment_finish(
    workspace_id: int, experiment_id: int, body: ExperimentFinishBody, user_id: int = Depends(require_web_user)
) -> dict:
    """Итог эксперимента вручную. Кампании не трогает — их останавливают отдельно."""
    return _call(experiments.finish_and_list, user_id, workspace_id, experiment_id, verdict=body.verdict,
                 conclusion=body.conclusion, cancel=body.cancel)


@router.post("/workspaces/{workspace_id}/ai-facts/forget")
def workspace_forget_fact(workspace_id: int, body: ForgetFactBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(workspaces.forget_ai_fact, user_id, workspace_id, body.key)


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


@router.post("/ai/actions/{action_id}/answer")
def action_answer(action_id: int, body: AnswerBody, user_id: int = Depends(require_web_user)) -> dict:
    """Ответы в карточке вопросов ИИ; вопросы с key запоминаются в пространстве."""
    return _call(ai_agent.answer_questions, user_id, action_id, answers=body.answers, comment=body.comment,
                 skip=body.skip)


@router.post("/ai/actions/{action_id}/plan")
def action_plan(action_id: int, body: PlanBody, user_id: int = Depends(require_web_user)) -> dict:
    """Решение по плану ИИ: какие шаги делать."""
    return _call(ai_agent.decide_plan, user_id, action_id, steps=body.steps, comment=body.comment,
                 approve=body.approve)
