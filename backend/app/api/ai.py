"""API ИИ-ассистента: шкала лимита, выбор модели, чаты проекта, согласование действий.

Ключи ИИ, Вордстата и картинок общие — их настраивает администратор (api/admin.py)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import ai, ai_agent, billing, experiments, insights, workspaces
from app.api.web import _http
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["ai"])


class ChatBody(BaseModel):
    title: str = ""
    # Класс модели, выбранный до первого сообщения; пусто — по умолчанию.
    model_class: str = ""


class ChatModelBody(BaseModel):
    # fast | standard | thinking; пусто — класс по умолчанию.
    model_class: str = ""


class MessageBody(BaseModel):
    text: str
    # id других проектов пользователя, упомянутых через @: ИИ получит их на чтение.
    refs: list[int] = []


class DecideBody(BaseModel):
    approve: bool
    remember: bool = False
    # id строк карточки, с которых сняли галочки: их в действии не будет.
    exclude: list[str] = []


class DecideAllBody(BaseModel):
    approve: bool
    # {id действия: [id снятых строк]}.
    exclude: dict[str, list[str]] = {}


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


class ItemsBody(BaseModel):
    approve: bool = True
    # Номера отмеченных строк таблицы.
    rows: list[int] = []
    comment: str = ""


class BoardBody(BaseModel):
    # Правки на доске сборки: [{op, kind, group, text, to, to_group}].
    ops: list[dict] = []


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


@router.get("/ai/models")
def ai_models(user_id: int = Depends(require_web_user)) -> dict:
    """Классы моделей для выбора в чате — без названий моделей и цен."""
    return ai.chat_classes()


class ImageGenerateBody(BaseModel):
    account_id: int
    prompt: str
    count: int = 1


@router.get("/workspaces/{workspace_id}/ad-images")
def workspace_ad_images(workspace_id: int, account_id: int | None = None, user_id: int = Depends(require_web_user)) -> dict:
    """Картинки из объявлений кампаний пространства — взять в новую кампанию."""
    return _call(ai_agent.workspace_images, user_id, workspace_id, account_id=account_id)


@router.post("/workspaces/{workspace_id}/ad-images/generate")
def workspace_generate_images(workspace_id: int, body: ImageGenerateBody, user_id: int = Depends(require_web_user)) -> dict:
    """Генерация общим ключом сервиса (списывает единицы) → библиотека кабинета: [{hash, preview_url, account_id}]."""
    return _call(ai_agent.generate_images, user_id, workspace_id, account_id=body.account_id, prompt=body.prompt,
                 count=body.count)


@router.get("/ai/usage")
def ai_usage(user_id: int = Depends(require_web_user)) -> dict:
    """Шкала лимита за текущий период в процентах: всего и по моделям, Вордстату, картинкам и проектам."""
    return _call(billing.user_summary, user_id)


@router.get("/workspaces/{workspace_id}/chats")
def chats_list(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    return {"items": _call(ai_agent.list_chats, user_id, workspace_id)}


@router.post("/workspaces/{workspace_id}/chats")
def chats_create(workspace_id: int, body: ChatBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.create_chat, user_id, workspace_id, body.title, body.model_class)


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


@router.put("/ai/chats/{chat_id}/model")
def chat_model(chat_id: int, body: ChatModelBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.set_chat_model, user_id, chat_id, body.model_class)


@router.delete("/ai/chats/{chat_id}")
def chat_delete(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _call(ai_agent.delete_chat, user_id, chat_id)
    return {"ok": True}


@router.post("/ai/chats/{chat_id}/messages")
def chat_message(chat_id: int, body: MessageBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.post_message, user_id, chat_id, body.text, refs=body.refs)


@router.post("/ai/chats/{chat_id}/retry")
def chat_retry(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Повтор оборвавшегося хода ИИ без нового сообщения."""
    return _call(ai_agent.retry_chat, user_id, chat_id)


@router.post("/ai/chats/{chat_id}/board")
def chat_board(chat_id: int, body: BoardBody, user_id: int = Depends(require_web_user)) -> dict:
    """Правки маркетолога на доске сборки новой кампании; ответ — чат целиком."""
    return _call(ai_agent.edit_board, user_id, chat_id, body.ops)


@router.post("/ai/chats/{chat_id}/decide-all")
def chat_decide_all(chat_id: int, body: DecideAllBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.decide_all, user_id, chat_id, approve=body.approve, exclude=body.exclude)


@router.post("/ai/actions/{action_id}/decide")
def action_decide(action_id: int, body: DecideBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.decide_action, user_id, action_id, approve=body.approve, remember=body.remember,
                 exclude=body.exclude)


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


@router.post("/ai/actions/{action_id}/items")
def action_items(action_id: int, body: ItemsBody, user_id: int = Depends(require_web_user)) -> dict:
    """Решение по таблице ИИ: какие строки согласованы."""
    return _call(ai_agent.decide_items, user_id, action_id, rows=body.rows, comment=body.comment,
                 approve=body.approve)
