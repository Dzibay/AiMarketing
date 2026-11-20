"""API ИИ-ассистента: шкала лимита, выбор модели, чаты проекта, согласование действий.

Ключи ИИ, Вордстата и картинок общие — их настраивает администратор (api/admin.py)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app import ai, ai_agent, ai_attachments, ai_feedback, ai_prompts, billing, events, experiments, insights, site_check, workspaces
from app.api.web import _http
from app.web_auth import require_web_user
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["ai"])


class ChatBody(BaseModel):
    title: str = ""
    # Класс модели, выбранный до первого сообщения; пусто — по умолчанию.
    model_class: str = ""


class ChatPatchBody(BaseModel):
    # Что не передано — не меняется.
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    # В архив с ждущими предложениями: reject — отклонить, keep — оставить ждать (см. ai_agent.archive_chat).
    pending: str = ""


class ChatModelBody(BaseModel):
    # fast | standard | thinking; пусто — класс по умолчанию.
    model_class: str = ""


class MessageBody(BaseModel):
    text: str
    # id других проектов пользователя, упомянутых через @: ИИ получит их на чтение.
    # Проект — число (как раньше) или {type: project|campaign|group, id}; период — {type: period, from, to, name}.
    refs: list[int | dict] = []
    # Что делать с ждущими предложениями ИИ: reject — отклонить, keep — оставить ждать решения.
    pending: str = "reject"
    # Что открыто в кабинете, когда пишут из панели ИИ: {view, label, object, period, filter, selected}.
    context: dict | None = None
    # Картинки из поля ввода (POST /ai/attachments), до четырёх.
    attachments: list[int] = []


class AttachmentBody(BaseModel):
    # data URL картинки, уменьшенной в браузере до 2000 px.
    data: str
    name: str = ""
    # ai — показать ИИ, ads — для объявлений (проверяется по требованиям Директа).
    purpose: str = "ai"


class AttachmentPatchBody(BaseModel):
    purpose: str


class FeedbackBody(BaseModel):
    # 1 — «вверх», -1 — «вниз», 0 — снять оценку.
    rating: int
    # Ключи ai_feedback.REASONS; только у «вниз».
    reasons: list[str] = []
    comment: str = ""


class PromptBody(BaseModel):
    # Пустое название — начало текста.
    title: str = ""
    text: str


class PromptPatchBody(BaseModel):
    title: str | None = None
    text: str | None = None
    sort: int | None = None


class SiteCheckClaimBody(BaseModel):
    id: str


class DecideBody(BaseModel):
    approve: bool
    remember: bool = False
    # id строк карточки, с которых сняли галочки: их в действии не будет.
    exclude: list[str] = []
    # «Сделать иначе»: быстрая причина (ai_agent.DECLINE_REASONS) и «Как надо» — отказ с ними сразу
    # запускает новый ход ИИ.
    reason: str = ""
    comment: str = ""


class DecideAllBody(BaseModel):
    approve: bool
    # {id действия: [id снятых строк]}.
    exclude: dict[str, list[str]] = {}


class SnoozeBody(BaseModel):
    # id ждущих предложений — перенос «Позже», сохранённых раньше в браузере.
    ids: list[int] = []


class AnswerBody(BaseModel):
    # [{n, selected: [вариант…], text, scope?}] — n — номер вопроса в карточке; scope — «site» или «client».
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
    # «Пропустить» необязательный этап сборки на доске.
    skip: bool = False


class BoardBody(BaseModel):
    # Правки на доске сборки: [{op, kind, group, text, to, to_group}].
    ops: list[dict] = []


class BoardDecideBody(BaseModel):
    approve: bool = True
    # {id таблицы на доске: номера отмеченных строк}; таблицы без записи согласуются как показаны.
    rows: dict[str, list[int]] = {}
    comment: str = ""
    # Решить только эти таблицы (шаг мастера на старой доске); без него — всю доску.
    only: list[int] | None = None


class ReviewBody(BaseModel):
    # Пусто — общий разбор проекта; текст карточки сигнала — разбор по ней.
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
    # site — ответ этого сайта, client — ответ для всего клиента проекта.
    scope: str = "site"


class MoveFactBody(BaseModel):
    key: str
    scope: str


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
    """Картинки из объявлений кампаний проекта — взять в новую кампанию."""
    return _call(ai_agent.workspace_images, user_id, workspace_id, account_id=account_id)


@router.post("/workspaces/{workspace_id}/ad-images/generate")
def workspace_generate_images(workspace_id: int, body: ImageGenerateBody, user_id: int = Depends(require_web_user)) -> dict:
    """Генерация общим ключом сервиса (списывает единицы) → библиотека кабинета: [{hash, preview_url, account_id}].
    Название модели генератора в кабинет не отдаём."""
    out = _call(ai_agent.generate_images, user_id, workspace_id, account_id=body.account_id, prompt=body.prompt,
                count=body.count)
    return {k: v for k, v in out.items() if k != "model"}


@router.get("/ai/usage")
def ai_usage(user_id: int = Depends(require_web_user)) -> dict:
    """Шкала лимита за текущий период в процентах: всего и по моделям, Вордстату, картинкам и проектам."""
    return _call(billing.user_summary, user_id)


@router.get("/workspaces/{workspace_id}/chats")
def chats_list(
    workspace_id: int, q: str = "", archived: bool = False, user_id: int = Depends(require_web_user)
) -> dict:
    """Чаты проекта; q — поиск по названию и тексту сообщений; archived — архив вместо обычного списка."""
    return _chats(user_id, workspace_id, q, archived)


def _chats(user_id: int, workspace_id: int | None, q: str, archived: bool) -> dict:
    if q.strip():
        items = _call(ai_agent.search_chats, user_id, workspace_id, q, archived=archived)
    else:
        items = _call(ai_agent.list_chats, user_id, workspace_id, archived=archived)
    return {"items": items, "archived_count": ai_agent.archived_count(user_id, workspace_id)}


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
    return _call(workspaces.forget_ai_fact, user_id, workspace_id, body.key, body.scope)


@router.post("/workspaces/{workspace_id}/ai-facts/move")
def workspace_move_fact(workspace_id: int, body: MoveFactBody, user_id: int = Depends(require_web_user)) -> dict:
    """Запомненный ответ — «для этого сайта» или «для всего клиента»; возвращает проект."""
    return _call(workspaces.move_ai_fact, user_id, workspace_id, body.key, body.scope)


@router.get("/ai/chats")
def general_chats_list(q: str = "", archived: bool = False, user_id: int = Depends(require_web_user)) -> dict:
    """Общие чаты по всем проектам; q — поиск по названию и тексту сообщений; archived — архив."""
    return _chats(user_id, None, q, archived)


@router.post("/ai/chats")
def general_chats_create(body: ChatBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.create_chat, user_id, None, body.title, body.model_class)


@router.post("/ai/site-check/claim")
def site_check_claim(body: SiteCheckClaimBody, user_id: int = Depends(require_web_user)) -> dict:
    """Проверка сайта с лендинга → чат с полным результатом (общий или проекта этого сайта)."""
    return _call(site_check.claim, user_id, body.id)


@router.get("/ai/pending/count")
def pending_count(user_id: int = Depends(require_web_user)) -> dict:
    """Счётчик «Ждёт решения» в шапке: карточки ИИ без решения во всех проектах."""
    return _call(ai_agent.pending_count, user_id)


@router.get("/ai/pending")
def pending_list(workspace_id: int | None = None, user_id: int = Depends(require_web_user)) -> dict:
    """Очередь «Ждёт решения»: действия, вопросы и планы ИИ без решения; workspace_id — только одного проекта."""
    return _call(ai_agent.pending_actions, user_id, workspace_id)


@router.get("/ai/mentions")
def mentions(workspace_id: int | None = None, user_id: int = Depends(require_web_user)) -> dict:
    """Кампании и группы для @ в поле ввода: проекта чата или, без workspace_id, всех проектов."""
    return _call(ai_agent.mention_options, user_id, workspace_id)


@router.post("/ai/attachments")
def upload_attachment(body: AttachmentBody, user_id: int = Depends(require_web_user)) -> dict:
    """Картинка из поля ввода, ещё без сообщения → {id, purpose, name, width, height, url, direct_problem}."""
    return _call(ai_attachments.upload, user_id, body.data, purpose=body.purpose, name=body.name)


@router.patch("/ai/attachments/{attachment_id}")
def patch_attachment(attachment_id: int, body: AttachmentPatchBody, user_id: int = Depends(require_web_user)) -> dict:
    """«Показать ИИ» ↔ «В объявления», пока сообщение не отправлено."""
    return _call(ai_attachments.set_purpose, user_id, attachment_id, body.purpose)


@router.get("/ai/attachments/{attachment_id}/{signature}")
def attachment_image(attachment_id: int, signature: str) -> Response:
    """Картинка из чата по подписанной ссылке (ai_attachments.url): открывается обычным <img>."""
    found = ai_attachments.image(attachment_id, signature)
    if found is None:
        raise HTTPException(status_code=404, detail="Картинка не найдена")
    data, kind = found
    return Response(content=data, media_type=kind, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/ai/chats/{chat_id}")
def chat_get(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.get_chat, user_id, chat_id)


@router.patch("/ai/chats/{chat_id}")
def chat_patch(chat_id: int, body: ChatPatchBody, user_id: int = Depends(require_web_user)) -> dict:
    """Название, закрепление, архив — что передано."""
    out = None
    if body.title is not None:
        out = _call(ai_agent.rename_chat, user_id, chat_id, body.title)
    if body.archived is not None:
        out = _call(ai_agent.archive_chat, user_id, chat_id, body.archived, body.pending)
    if body.pinned is not None:
        out = _call(ai_agent.pin_chat, user_id, chat_id, body.pinned)
    return out if out is not None else _call(ai_agent.get_chat, user_id, chat_id)["chat"]


@router.put("/ai/chats/{chat_id}/model")
def chat_model(chat_id: int, body: ChatModelBody, user_id: int = Depends(require_web_user)) -> dict:
    return _call(ai_agent.set_chat_model, user_id, chat_id, body.model_class)


@router.delete("/ai/chats/{chat_id}")
def chat_delete(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _call(ai_agent.delete_chat, user_id, chat_id)
    return {"ok": True}


@router.post("/ai/chats/{chat_id}/messages")
def chat_message(chat_id: int, body: MessageBody, user_id: int = Depends(require_web_user)) -> dict:
    out = _call(
        ai_agent.post_message, user_id, chat_id, body.text, refs=body.refs, pending=body.pending, context=body.context,
        attachments=body.attachments,
    )
    events.record(user_id, "ai_question", chat_id=chat_id)
    return out


@router.post("/ai/messages/{message_id}/feedback")
def message_feedback(message_id: int, body: FeedbackBody, user_id: int = Depends(require_web_user)) -> dict:
    """Оценка ответа ИИ → {feedback}; null — оценка снята."""
    return {"feedback": _call(ai_feedback.save, user_id, message_id, body.rating, body.reasons, body.comment)}


@router.get("/ai/prompts")
def prompts_list(user_id: int = Depends(require_web_user)) -> dict:
    """Готовые задачи: категории, встроенный набор с местами показа и свои задачи пользователя."""
    return _call(ai_prompts.overview, user_id)


@router.post("/ai/prompts")
def prompts_create(body: PromptBody, user_id: int = Depends(require_web_user)) -> dict:
    return {"prompt": _call(ai_prompts.create, user_id, body.title, body.text)}


@router.patch("/ai/prompts/{prompt_id}")
def prompts_update(prompt_id: int, body: PromptPatchBody, user_id: int = Depends(require_web_user)) -> dict:
    return {"prompt": _call(ai_prompts.update, user_id, prompt_id, body.title, body.text, body.sort)}


@router.delete("/ai/prompts/{prompt_id}")
def prompts_delete(prompt_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _call(ai_prompts.delete, user_id, prompt_id)
    return {"ok": True}


@router.post("/ai/chats/{chat_id}/retry")
def chat_retry(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Повтор оборвавшегося хода ИИ без нового сообщения."""
    return _call(ai_agent.retry_chat, user_id, chat_id)


@router.post("/ai/chats/{chat_id}/board")
def chat_board(chat_id: int, body: BoardBody, user_id: int = Depends(require_web_user)) -> dict:
    """Правки маркетолога на доске сборки новой кампании; ответ — чат целиком."""
    return _call(ai_agent.edit_board, user_id, chat_id, body.ops)


@router.post("/ai/chats/{chat_id}/board/decide")
def chat_board_decide(chat_id: int, body: BoardDecideBody, user_id: int = Depends(require_web_user)) -> dict:
    """Одно решение по всей кампании на доске: согласовать (ИИ создаст черновик) или отменить сборку."""
    return _call(
        ai_agent.decide_build, user_id, chat_id, approve=body.approve, rows=body.rows, comment=body.comment,
        only=body.only,
    )


@router.post("/ai/chats/{chat_id}/board/write")
def chat_board_write(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """«Повторить» запись согласованных шагов в черновик Директа; ответ — чат целиком."""
    return _call(ai_agent.retry_build_write, user_id, chat_id)


@router.post("/ai/chats/{chat_id}/board/moderate")
def chat_board_moderate(chat_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """«Отправить на модерацию»: черновик, собранный по шагам, — в Директ на модерацию; ответ — чат целиком."""
    return _call(ai_agent.moderate_build, user_id, chat_id)


@router.get("/ai/campaigns/{campaign_row_id}/board")
def campaign_board(campaign_row_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Доска готовой кампании: группы, фразы и объявления из Директа."""
    return _call(ai_agent.campaign_board, user_id, campaign_row_id)


@router.post("/ai/chats/{chat_id}/decide-all")
def chat_decide_all(chat_id: int, body: DecideAllBody, user_id: int = Depends(require_web_user)) -> dict:
    out = _call(ai_agent.decide_all, user_id, chat_id, approve=body.approve, exclude=body.exclude)
    if body.approve:
        events.record(user_id, "action_applied", chat_id=chat_id, all=True)
    return out


@router.post("/ai/actions/{action_id}/decide")
def action_decide(action_id: int, body: DecideBody, user_id: int = Depends(require_web_user)) -> dict:
    out = _call(ai_agent.decide_action, user_id, action_id, approve=body.approve, remember=body.remember,
                exclude=body.exclude, reason=body.reason, comment=body.comment)
    if body.approve:
        events.record(user_id, "action_applied", action_id=action_id)
    return out


@router.get("/ai/actions/{action_id}/undo")
def action_undo_preview(action_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """«Вернуть как было»: записи журнала этого решения словами и что вернуть нельзя."""
    from app.undo import ai_action_undo_preview

    return _call(ai_action_undo_preview, user_id, action_id)


@router.post("/ai/actions/{action_id}/undo")
def action_undo(action_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Возвращает все изменения решения разом — задачей отката (как «Откатить» в «Истории»)."""
    from app.job_handlers import submit
    from app.jobs import PRIORITY_USER, public_job
    from app.undo import ai_action_undo_preview

    preview = _call(ai_action_undo_preview, user_id, action_id)
    if not preview["available"]:
        raise _http(YandexError("undo", preview["reason"]))
    job = submit("undo_action", user_id, {"log_ids": preview["log_ids"]}, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/ai/actions/{action_id}/snooze")
def action_snooze(action_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """«Позже»: предложение ждёт решения, но не в счётчике «Ждёт решения»."""
    return _call(ai_agent.snooze_action, user_id, action_id, True)


@router.post("/ai/actions/{action_id}/unsnooze")
def action_unsnooze(action_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """«Решить сейчас»: вернуть отложенное предложение в «Ждёт решения»."""
    return _call(ai_agent.snooze_action, user_id, action_id, False)


@router.post("/ai/actions/snooze")
def actions_snooze(body: SnoozeBody, user_id: int = Depends(require_web_user)) -> dict:
    """«Позже» сразу для нескольких — один раз перенести номера, сохранённые в браузере → {ids}."""
    return _call(ai_agent.snooze_actions, user_id, body.ids, True)


@router.post("/ai/actions/{action_id}/answer")
def action_answer(action_id: int, body: AnswerBody, user_id: int = Depends(require_web_user)) -> dict:
    """Ответы в карточке вопросов ИИ; вопросы с key запоминаются в проекте."""
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
                 approve=body.approve, skip=body.skip)
