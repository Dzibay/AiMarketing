"""«Отчёт клиенту»: данные отчёта за месяц, план и комментарий, отметка «готово», оформление и ИИ-комментарий."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.web import _http
from app.job_handlers import submit
from app.jobs import PRIORITY_USER, public_job
from app.reports import build_report, get_brand, mark_done, reports_status, save_brand, save_report, set_logo
from app.web_auth import require_web_user
from app.workspaces import require_workspace
from app.yandex import YandexError

router = APIRouter(prefix="/web", tags=["reports"])


class ReportBody(BaseModel):
    month: str = ""
    plan: str | None = None
    comment: str | None = None
    sections: dict[str, bool] | None = None


class DoneBody(BaseModel):
    month: str = ""
    done: bool = True


class MonthBody(BaseModel):
    month: str = ""


class BrandBody(BaseModel):
    name: str = ""
    contacts: str = ""
    accent: str = ""


class LogoBody(BaseModel):
    data: str


@router.get("/workspaces/{workspace_id}/report")
def web_report(workspace_id: int, month: str = "", user_id: int = Depends(require_web_user)) -> dict:
    try:
        return build_report(user_id, workspace_id, month or None)
    except YandexError as exc:
        raise _http(exc) from exc


@router.put("/workspaces/{workspace_id}/report")
def web_report_save(workspace_id: int, body: ReportBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return save_report(
            user_id, workspace_id, body.month or None, plan=body.plan, comment=body.comment, sections=body.sections
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/workspaces/{workspace_id}/report/done")
def web_report_done(workspace_id: int, body: DoneBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return mark_done(user_id, workspace_id, body.month or None, body.done)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/workspaces/{workspace_id}/report/ai-comment")
def web_report_ai_comment(workspace_id: int, body: MonthBody, user_id: int = Depends(require_web_user)) -> dict:
    """Черновик комментария — задачей (ИИ отвечает десятки секунд). Тратит лимит, ничего не сохраняет."""
    try:
        require_workspace(user_id, workspace_id)
    except YandexError as exc:
        raise _http(exc) from exc
    payload = {"workspace_id": workspace_id, "month": body.month or ""}
    job = submit("report_comment", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.get("/reports/status")
def web_reports_status(month: str = "", user_id: int = Depends(require_web_user)) -> dict:
    try:
        return reports_status(user_id, month or None)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/report-brand")
def web_brand(user_id: int = Depends(require_web_user)) -> dict:
    return get_brand(user_id)


@router.put("/report-brand")
def web_brand_save(body: BrandBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return save_brand(user_id, name=body.name, contacts=body.contacts, accent=body.accent)
    except YandexError as exc:
        raise _http(exc) from exc


@router.put("/report-brand/logo")
def web_brand_logo(body: LogoBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return set_logo(user_id, body.data)
    except YandexError as exc:
        raise _http(exc) from exc


@router.delete("/report-brand/logo")
def web_brand_logo_delete(user_id: int = Depends(require_web_user)) -> dict:
    return set_logo(user_id, None)
