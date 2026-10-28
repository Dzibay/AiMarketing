"""API рабочего пространства: сводка, аналитика, кампании и действия, настройки, кабинеты."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.actions import (
    bulk_campaign_action,
    campaign_options,
    create_campaign,
    get_campaign_settings,
    moderate_campaign,
    update_campaign_settings,
)
from app.account_load import load_overview
from app.adgroups import list_adgroups
from app.ads import list_ads
from app.analytics import parse_range, workspace_analytics, workspace_summary
from app.api.web import _http, _start_job
from app.db import pool
from app.direct_sync import get_campaign
from app.budgets import validate_args
from app.job_handlers import submit
from app.jobs import PRIORITY_USER, public_job
from app.journal import list_action_logs
from app.keywords import list_keywords
from app.query_cleaning import (
    APPLY_MAX,
    CLASSIFY_MAX,
    apply_decisions,
    check_phrases,
    estimate_classify,
    forget_decisions,
    list_decisions,
    needs_direct,
    query_queue,
)
from app.rules import list_rules
from app.web_auth import require_web_user
from app.workspaces import (
    account_candidates,
    add_workspace_account,
    campaign_candidates,
    counter_access,
    ensure_workspaces,
    get_workspace,
    list_workspace_accounts,
    list_workspaces,
    remove_workspace_account,
    require_workspace,
    require_workspace_campaign,
    set_workspace_campaigns,
    update_workspace,
    workspace_campaign_row_ids,
    workspace_goals,
)
from app.yandex import YandexError

router = APIRouter(prefix="/web/workspaces", tags=["workspaces"])


class WorkspacePatchBody(BaseModel):
    name: str | None = None
    site: str | None = None
    client_id: int | None = None
    kpi_type: str | None = None
    kpi_target: float | None = None
    monthly_budget_rub: float | None = None
    goal_id: int | None = None
    ai_notes: str | None = None
    ai_access: dict | None = None
    brief: dict | None = None
    archived: bool | None = None


class AddAccountBody(BaseModel):
    account_id: int
    campaigns: str = "matching"


class SetCampaignsBody(BaseModel):
    add: list[int] = Field(default_factory=list)
    remove: list[int] = Field(default_factory=list)


class CreateCampaignBody(BaseModel):
    account_id: int
    spec: dict = Field(default_factory=dict)


class SettingsPatchBody(BaseModel):
    changes: dict = Field(default_factory=dict)


class DuplicateBody(BaseModel):
    target_account_id: int | None = None
    name: str = ""
    target_workspace_id: int | None = None


class WsBulkBody(BaseModel):
    campaign_ids: list[int] = Field(default_factory=list)
    action: str = ""
    daily_budget_rub: float | None = None
    budget_change_pct: float | None = None


class WsBudgetBody(BaseModel):
    campaign_ids: list[int] = Field(default_factory=list)
    weekly_budget_rub: float | None = None
    daily_budget_rub: float | None = None
    budget_change_pct: float | None = None
    target_change_pct: float | None = None


class SliceBody(BaseModel):
    dimension: str
    days: int = 30
    campaign_ids: list[int] = Field(default_factory=list)


class SliceAdjustItem(BaseModel):
    key: str
    pct: int
    label: str = ""


class SliceAdjustBody(BaseModel):
    dimension: str
    items: list[SliceAdjustItem] = Field(default_factory=list, max_length=300)
    campaign_ids: list[int] = Field(default_factory=list)


class PlacementItem(BaseModel):
    placement: str
    campaign_ids: list[int] = Field(default_factory=list)


class PlacementsExcludeBody(BaseModel):
    items: list[PlacementItem] = Field(default_factory=list, max_length=1000)


class PlacementsAiBody(BaseModel):
    items: list[dict] = Field(default_factory=list, max_length=300)


class AdReportBody(BaseModel):
    days: int = 30
    campaign_ids: list[int] = Field(default_factory=list)


class AdsAiItem(BaseModel):
    ad_row_id: int
    net: str = ""
    ctr: float | None = None
    ref_ctr: float | None = None
    best_ad_row_id: int | None = None


class AdsAiBody(BaseModel):
    items: list[AdsAiItem] = Field(default_factory=list, max_length=20)


class AdsAbItem(BaseModel):
    ad_row_id: int
    titles: list[str] = Field(default_factory=list, max_length=7)
    texts: list[str] = Field(default_factory=list, max_length=3)
    title2: str = ""


class AdsAbBody(BaseModel):
    items: list[AdsAbItem] = Field(default_factory=list, max_length=50)
    moderate: bool = False


class StatsSyncBody(BaseModel):
    days: int = 7


def _ws(user_id: int, workspace_id: int) -> dict:
    try:
        return require_workspace(user_id, workspace_id)
    except YandexError as exc:
        raise _http(exc) from exc


def _ws_campaign(user_id: int, workspace_id: int, campaign_row_id: int) -> None:
    _ws(user_id, workspace_id)
    try:
        require_workspace_campaign(user_id, workspace_id, campaign_row_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("")
def ws_list(archived: bool = False, user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_workspaces(user_id, include_archived=archived)}


@router.post("/refresh")
def ws_refresh(user_id: int = Depends(require_web_user)) -> dict:
    """Пространства для новых счётчиков (после синка подключений)."""
    created = ensure_workspaces(user_id)
    return {"created": len(created), "items": list_workspaces(user_id)}


@router.get("/{workspace_id}")
def ws_get(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return get_workspace(user_id, workspace_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/{workspace_id}/load")
def ws_load(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Загрузка кабинетов и счётчика пространства: пока идёт — вместо нулей показываем плашку."""
    ws = _ws(user_id, workspace_id)
    with pool.connection() as conn:
        account_ids = [
            int(r["account_id"])
            for r in conn.execute(
                "SELECT account_id FROM workspace_accounts WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id),
            )
        ]
    counter_ids = [int(ws["counter_row_id"])] if ws.get("counter_row_id") else []
    return load_overview(user_id, account_ids=account_ids, counter_ids=counter_ids)


@router.patch("/{workspace_id}")
def ws_patch(workspace_id: int, body: WorkspacePatchBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return update_workspace(user_id, workspace_id, **body.model_dump(exclude_unset=True))
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/{workspace_id}/summary")
def ws_summary(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws(user_id, workspace_id)
    return workspace_summary(user_id, workspace_id)


@router.get("/{workspace_id}/analytics")
def ws_analytics(
    workspace_id: int,
    date_from: str = "",
    date_to: str = "",
    user_id: int = Depends(require_web_user),
) -> dict:
    _ws(user_id, workspace_id)
    try:
        d_from, d_to = parse_range(date_from, date_to)
        return workspace_analytics(user_id, workspace_id, date_from=d_from, date_to=d_to)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/analytics/sync")
def ws_analytics_sync(
    workspace_id: int,
    body: StatsSyncBody | None = None,
    user_id: int = Depends(require_web_user),
) -> dict:
    """Догрузить статистику за N дней (Директ и Метрика всех кабинетов — так дешевле, чем по одному)."""
    _ws(user_id, workspace_id)
    days = max(1, min(int((body or StatsSyncBody()).days), 30))
    d_from, d_to = parse_range("", "", default_days=days)
    return _start_job("stats_range", user_id, {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()})


# --- Кабинеты и кампании пространства ---------------------------------------------------------


@router.get("/{workspace_id}/accounts")
def ws_accounts(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws(user_id, workspace_id)
    return {"items": list_workspace_accounts(user_id, workspace_id)}


@router.get("/{workspace_id}/accounts/candidates")
def ws_account_candidates(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws(user_id, workspace_id)
    return {"items": account_candidates(user_id, workspace_id)}


@router.post("/{workspace_id}/accounts")
def ws_add_account(workspace_id: int, body: AddAccountBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return add_workspace_account(user_id, workspace_id, body.account_id, campaigns=body.campaigns)
    except YandexError as exc:
        raise _http(exc) from exc


@router.delete("/{workspace_id}/accounts/{account_id}")
def ws_remove_account(workspace_id: int, account_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return remove_workspace_account(user_id, workspace_id, account_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/{workspace_id}/campaigns/candidates")
def ws_campaign_candidates(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws(user_id, workspace_id)
    return {"items": campaign_candidates(user_id, workspace_id)}


@router.post("/{workspace_id}/campaigns/set")
def ws_set_campaigns(workspace_id: int, body: SetCampaignsBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return set_workspace_campaigns(user_id, workspace_id, add=body.add, remove=body.remove)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/campaigns/sync")
def ws_sync_campaigns(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Перечитать кампании всех кабинетов пространства из Директа."""
    _ws(user_id, workspace_id)
    jobs = [
        _start_job("sync_account_campaigns", user_id, {"account_id": a["id"]})["job"]
        for a in list_workspace_accounts(user_id, workspace_id)
        if a["enabled"]
    ]
    return {"jobs": jobs}


@router.get("/{workspace_id}/campaigns")
def ws_campaigns(
    workspace_id: int,
    date_from: str = "",
    date_to: str = "",
    user_id: int = Depends(require_web_user),
) -> dict:
    """Кампании пространства со статистикой за период (по умолчанию 7 дней)."""
    _ws(user_id, workspace_id)
    try:
        d_from, d_to = parse_range(date_from, date_to)
        data = workspace_analytics(user_id, workspace_id, date_from=d_from, date_to=d_to, with_tops=False)
    except YandexError as exc:
        raise _http(exc) from exc
    return {
        "date_from": data["date_from"],
        "date_to": data["date_to"],
        "conversions_source": data["conversions_source"],
        "items": data["by_campaign"],
        "accounts": list_workspace_accounts(user_id, workspace_id),
    }


@router.get("/{workspace_id}/campaign-options")
def ws_campaign_options(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """
    Справочники мастера кампании + кабинеты и цели счётчика. other_accounts — кабинеты вне
    пространства: кампанию можно создать и там, кабинет подключится к пространству сам.
    """
    ws = _ws(user_id, workspace_id)
    other = [a for a in account_candidates(user_id, workspace_id) if not a["linked"] and a["enabled"]]
    for a in other:
        a["counter_access"] = counter_access(user_id, workspace_id, a)
    return {
        **campaign_options(),
        "accounts": list_workspace_accounts(user_id, workspace_id),
        "other_accounts": other,
        "goals": workspace_goals(user_id, workspace_id),
        "counter_id": ws["counter_id"],
        "goal_id": ws["goal_id"],
    }


@router.post("/{workspace_id}/campaigns")
def ws_create_campaign(workspace_id: int, body: CreateCampaignBody, user_id: int = Depends(require_web_user)) -> dict:
    _ws(user_id, workspace_id)
    try:
        return create_campaign(user_id, account_id=body.account_id, spec=body.spec, workspace_id=workspace_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/campaigns/actions")
def ws_bulk(workspace_id: int, body: WsBulkBody, user_id: int = Depends(require_web_user)) -> dict:
    _ws(user_id, workspace_id)
    own = set(workspace_campaign_row_ids(user_id, workspace_id))
    if not set(body.campaign_ids) <= own:
        raise HTTPException(status_code=404, detail="not-found")
    try:
        return bulk_campaign_action(
            user_id,
            campaign_ids=body.campaign_ids,
            action=body.action,
            daily_budget_rub=body.daily_budget_rub,
            budget_change_pct=body.budget_change_pct,
            workspace_id=workspace_id,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/campaigns/budget")
def ws_bulk_budget(workspace_id: int, body: WsBudgetBody, user_id: int = Depends(require_web_user)) -> dict:
    """Недельный/дневной бюджет и цена цели для выбранных кампаний — задачей: на кампанию 2–3 вызова Директа."""
    _ws(user_id, workspace_id)
    own = set(workspace_campaign_row_ids(user_id, workspace_id))
    if not body.campaign_ids or not set(body.campaign_ids) <= own:
        raise HTTPException(status_code=404, detail="not-found")
    try:
        validate_args(body.weekly_budget_rub, body.daily_budget_rub, body.budget_change_pct, body.target_change_pct)
    except YandexError as exc:
        raise _http(exc) from exc
    payload = {**body.model_dump(), "workspace_id": workspace_id}
    job = submit("bulk_budget", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


def _own_campaigns(user_id: int, workspace_id: int, ids: list[int]) -> None:
    if ids and not set(ids) <= set(workspace_campaign_row_ids(user_id, workspace_id)):
        raise HTTPException(status_code=404, detail="not-found")


@router.post("/{workspace_id}/slices")
def ws_slice(workspace_id: int, body: SliceBody, user_id: int = Depends(require_web_user)) -> dict:
    """Срез статистики (площадки, устройства, пол-возраст, регионы, часы) — задачей: это отчёт Директа."""
    from app.slices import DIMENSIONS

    _ws(user_id, workspace_id)
    if body.dimension not in DIMENSIONS:
        raise HTTPException(status_code=400, detail="dimension")
    _own_campaigns(user_id, workspace_id, body.campaign_ids)
    payload = {**body.model_dump(), "workspace_id": workspace_id}
    job = submit("slice_report", user_id, payload, priority=PRIORITY_USER, max_attempts=2)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/slices/adjust")
def ws_slice_adjust(workspace_id: int, body: SliceAdjustBody, user_id: int = Depends(require_web_user)) -> dict:
    """Корректировки ставок по срезу на выбранные кампании; одна запись журнала с откатом."""
    from app.slices import ADJUSTABLE

    _ws(user_id, workspace_id)
    if body.dimension not in ADJUSTABLE:
        raise HTTPException(status_code=400, detail="dimension")
    if not body.items:
        raise _http(YandexError("empty", "Не выбрано ни одной корректировки"))
    _own_campaigns(user_id, workspace_id, body.campaign_ids)
    payload = {**body.model_dump(), "workspace_id": workspace_id}
    job = submit("slice_adjust", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/placements/exclude")
def ws_placements_exclude(
    workspace_id: int, body: PlacementsExcludeBody, user_id: int = Depends(require_web_user)
) -> dict:
    """Запретить площадки в кампаниях, где они крутились; одна запись журнала с откатом."""
    _ws(user_id, workspace_id)
    if not body.items:
        raise _http(YandexError("empty", "Не выбрано ни одной площадки"))
    _own_campaigns(user_id, workspace_id, sorted({i for item in body.items for i in item.campaign_ids}))
    payload = {**body.model_dump(), "workspace_id": workspace_id}
    job = submit("exclude_placements", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/placements/ai")
def ws_placements_ai(workspace_id: int, body: PlacementsAiBody, user_id: int = Depends(require_web_user)) -> dict:
    """Подсказки ИИ по площадкам — только вердикты, ничего не меняет."""
    _ws(user_id, workspace_id)
    if not body.items:
        raise _http(YandexError("empty", "Нет площадок для разбора"))
    payload = {"items": body.items, "workspace_id": workspace_id}
    job = submit("placements_ai", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/ads/report")
def ws_ad_report(workspace_id: int, body: AdReportBody, user_id: int = Depends(require_web_user)) -> dict:
    """Статистика объявлений по сетям с пометкой слабых по CTR — задачей: это отчёт Директа."""
    _ws(user_id, workspace_id)
    _own_campaigns(user_id, workspace_id, body.campaign_ids)
    payload = {**body.model_dump(), "workspace_id": workspace_id}
    job = submit("ad_report", user_id, payload, priority=PRIORITY_USER, max_attempts=2)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/ads/ai")
def ws_ads_ai(workspace_id: int, body: AdsAiBody, user_id: int = Depends(require_web_user)) -> dict:
    """Новые тексты от ИИ для слабых объявлений — только предложения, ничего не меняет."""
    _ws(user_id, workspace_id)
    if not body.items:
        raise _http(YandexError("empty", "Не выбраны объявления"))
    payload = {**body.model_dump(), "workspace_id": workspace_id}
    job = submit("ads_ai", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/ads/ab")
def ws_ads_ab(workspace_id: int, body: AdsAbBody, user_id: int = Depends(require_web_user)) -> dict:
    """Новые объявления рядом со старыми (A/B); одна запись журнала с откатом."""
    _ws(user_id, workspace_id)
    if not body.items:
        raise _http(YandexError("empty", "Нет объявлений для добавления"))
    payload = {**body.model_dump(), "workspace_id": workspace_id}
    job = submit("ads_ab", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.get("/{workspace_id}/campaigns/{campaign_row_id}")
def ws_campaign(workspace_id: int, campaign_row_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws_campaign(user_id, workspace_id, campaign_row_id)
    camp = get_campaign(user_id, campaign_row_id)
    if camp is None:
        raise HTTPException(status_code=404, detail="not-found")
    return {
        "campaign": camp,
        "adgroups": list_adgroups(user_id, campaign_row_id),
        "ads": list_ads(user_id, campaign_row_id=campaign_row_id),
        "keywords": list_keywords(user_id, campaign_row_id=campaign_row_id),
    }


@router.get("/{workspace_id}/campaigns/{campaign_row_id}/settings")
def ws_campaign_settings(workspace_id: int, campaign_row_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws_campaign(user_id, workspace_id, campaign_row_id)
    try:
        return get_campaign_settings(user_id, campaign_row_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/{workspace_id}/campaigns/{campaign_row_id}/settings")
def ws_patch_campaign_settings(
    workspace_id: int,
    campaign_row_id: int,
    body: SettingsPatchBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    _ws_campaign(user_id, workspace_id, campaign_row_id)
    try:
        return update_campaign_settings(user_id, campaign_row_id, body.changes)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/campaigns/{campaign_row_id}/duplicate")
def ws_duplicate_campaign(
    workspace_id: int,
    campaign_row_id: int,
    body: DuplicateBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    """Копия — задачей: структура большой кампании копируется минутами."""
    _ws_campaign(user_id, workspace_id, campaign_row_id)
    target_ws = body.target_workspace_id or workspace_id
    _ws(user_id, target_ws)
    if body.target_account_id:
        with pool.connection() as conn:
            owned = conn.execute(
                "SELECT 1 FROM direct_accounts WHERE id = %s AND user_id = %s",
                (body.target_account_id, user_id),
            ).fetchone()
        if owned is None:
            raise HTTPException(status_code=404, detail="not-found")
    payload = {
        "campaign_row_id": campaign_row_id,
        "target_account_id": body.target_account_id,
        "name": body.name.strip(),
        "workspace_id": target_ws,
    }
    # Без повторов: упавшая на середине копия не должна создать вторую кампанию.
    job = submit("duplicate_campaign", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/campaigns/{campaign_row_id}/moderate")
def ws_moderate_campaign(workspace_id: int, campaign_row_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws_campaign(user_id, workspace_id, campaign_row_id)
    try:
        return moderate_campaign(user_id, campaign_row_id)
    except YandexError as exc:
        raise _http(exc) from exc


# --- Журнал и правила -----------------------------------------------------------------------


@router.get("/{workspace_id}/actions")
def ws_actions(
    workspace_id: int,
    source: str = "",
    before_id: int | None = None,
    limit: int = 50,
    user_id: int = Depends(require_web_user),
) -> dict:
    _ws(user_id, workspace_id)
    return {
        "items": list_action_logs(
            user_id, limit, workspace_id=workspace_id, source=source or None, before_id=before_id
        )
    }


@router.get("/{workspace_id}/rules")
def ws_rules(workspace_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _ws(user_id, workspace_id)
    return {"items": list_rules(user_id, workspace_id=workspace_id)}


# --- Чистка поисковых запросов и бриф ---------------------------------------------------------


class QueriesSyncBody(BaseModel):
    days: int = 14


class QueriesClassifyBody(BaseModel):
    limit: int = 200
    relabel: bool = False
    campaign_row_id: int | None = None


class QueriesApplyBody(BaseModel):
    items: list[dict] = Field(default_factory=list)
    force: bool = False
    bid_rub: float | None = None


class QueriesCheckBody(BaseModel):
    items: list[dict] = Field(default_factory=list)


class QueriesForgetBody(BaseModel):
    query_norms: list[str] = Field(default_factory=list)


class BriefDraftBody(BaseModel):
    site: str = ""


@router.get("/{workspace_id}/queries")
def ws_queries(
    workspace_id: int,
    tab: str = "new",
    search: str = "",
    min_cost_rub: float | None = None,
    no_conversions: bool = False,
    campaign_row_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    user_id: int = Depends(require_web_user),
) -> dict:
    if campaign_row_id:
        _ws_campaign(user_id, workspace_id, campaign_row_id)
    try:
        return query_queue(
            user_id,
            workspace_id,
            tab=tab,
            search=search,
            min_cost_rub=min_cost_rub,
            no_conversions=no_conversions,
            campaign_row_id=campaign_row_id,
            limit=limit,
            offset=offset,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/queries/sync")
def ws_queries_sync(
    workspace_id: int, body: QueriesSyncBody | None = None, user_id: int = Depends(require_web_user)
) -> dict:
    _ws(user_id, workspace_id)
    days = max(1, min(int((body or QueriesSyncBody()).days), 90))
    return _start_job("sync_workspace_queries", user_id, {"workspace_id": workspace_id, "days": days})


def _classify_payload(user_id: int, workspace_id: int, body: QueriesClassifyBody) -> dict:
    _ws(user_id, workspace_id)
    if body.campaign_row_id:
        _ws_campaign(user_id, workspace_id, body.campaign_row_id)
    return {
        "workspace_id": workspace_id,
        "limit": max(1, min(int(body.limit), CLASSIFY_MAX)),
        "relabel": bool(body.relabel),
        "campaign_row_id": body.campaign_row_id,
    }


@router.post("/{workspace_id}/queries/classify/estimate")
def ws_queries_classify_estimate(
    workspace_id: int, body: QueriesClassifyBody | None = None, user_id: int = Depends(require_web_user)
) -> dict:
    """Без вызова ИИ: сколько запросов закроют правила, сколько уйдёт в модель и во что обойдётся."""
    payload = _classify_payload(user_id, workspace_id, body or QueriesClassifyBody())
    try:
        return estimate_classify(user_id, payload)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/queries/classify")
def ws_queries_classify(
    workspace_id: int, body: QueriesClassifyBody | None = None, user_id: int = Depends(require_web_user)
) -> dict:
    payload = _classify_payload(user_id, workspace_id, body or QueriesClassifyBody())
    job = submit("classify_queries", user_id, payload, priority=PRIORITY_USER, max_attempts=2)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/queries/apply")
def ws_queries_apply(workspace_id: int, body: QueriesApplyBody, user_id: int = Depends(require_web_user)) -> dict:
    """Только «целевые» без новых ключей — сразу; с минус-фразами и ключами — задачей (это Директ)."""
    _ws(user_id, workspace_id)
    if len(body.items) > APPLY_MAX:
        raise _http(YandexError("limit", f"Не больше {APPLY_MAX} запросов за раз"))
    if not needs_direct(body.items):
        try:
            return {"result": apply_decisions(user_id, workspace_id, body.items)}
        except YandexError as exc:
            raise _http(exc) from exc
    payload = {"workspace_id": workspace_id, "items": body.items, "force": body.force, "bid_rub": body.bid_rub}
    # Без повторов: половина минус-фраз уже в Директе, повтор только запутает журнал.
    job = submit("apply_query_decisions", user_id, payload, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/{workspace_id}/queries/check")
def ws_queries_check(workspace_id: int, body: QueriesCheckBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return {"items": check_phrases(user_id, workspace_id, body.items)}
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/{workspace_id}/queries/memory")
def ws_queries_memory(
    workspace_id: int,
    verdict: str = "",
    search: str = "",
    limit: int = 100,
    offset: int = 0,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return list_decisions(user_id, workspace_id, verdict=verdict or None, search=search, limit=limit, offset=offset)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/queries/forget")
def ws_queries_forget(workspace_id: int, body: QueriesForgetBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return forget_decisions(user_id, workspace_id, body.query_norms)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/{workspace_id}/brief/draft")
def ws_brief_draft(
    workspace_id: int, body: BriefDraftBody | None = None, user_id: int = Depends(require_web_user)
) -> dict:
    """Черновик брифа по сайту — задачей: сайт и ИИ отвечают десятки секунд. Ничего не сохраняет."""
    _ws(user_id, workspace_id)
    site = (body or BriefDraftBody()).site.strip()[:500]
    job = submit("brief_draft", user_id, {"workspace_id": workspace_id, "site": site}, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}
