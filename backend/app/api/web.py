import logging
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

log = logging.getLogger("app.api.web")

from app import ad_extras
from app.actions import (
    _account_creds,
    bulk_campaign_action,
    create_text_campaign,
    update_campaign,
)
from app.journal import list_action_logs
from app.clients import (
    assign_direct_account,
    assign_metrika_counter,
    create_client,
    delete_client,
    list_clients,
    update_client,
)
from app.adgroups import (
    create_adgroup,
    delete_adgroups,
    list_adgroups,
    update_adgroup,
)
from app.ads import (
    _ad_row as _ad_db_row,
    bulk_ad_action,
    create_text_ad,
    list_ads,
    update_text_ad,
)
from app.account_load import load_overview, reset_account_load
from app.direct_sync import get_campaign, list_campaigns
from app.keywords import (
    bulk_keyword_action,
    create_keywords,
    list_keywords,
    update_keyword,
)
from app.search_queries import (
    add_query_as_keyword,
    add_query_as_negative,
    bulk_search_query_action,
    list_search_queries,
)
from app.db import pool
from app.job_handlers import submit
from app.jobs import PRIORITY_USER, get_job, list_jobs, public_job
from app.metrika_sync import canonical_counters, list_goals
from app.oauth_codes import consume_oauth_code, issue_oauth_code
from app.rules import (
    create_rule,
    delete_rule,
    list_rule_runs,
    list_rules,
    run_rule,
    update_rule,
)
from app.password_reset import request_password_reset, reset_password
from app.settings_store import consent_url, cookies_url, offer_url, privacy_url, public_base_url
from app.sync_runs import list_sync_runs
from app.undo import undo_preview
from app.telegram_notify import (
    create_link_code,
    get_telegram_status,
    handle_update,
    notify_user,
    unlink_telegram,
)
from app.users import (
    UsersError,
    change_password,
    get_user,
    login_user,
    public_user,
    register_user,
)
from app.web_auth import make_session, require_web_user, verify_session
from app.workspaces import ensure_workspaces, list_workspaces
from app.yandex import (
    OAUTH_MODES,
    YandexError,
    get_connection_access_token,
    complete_oauth,
    delete_connection,
    list_connections,
    list_direct,
    list_metrika,
    set_direct_enabled,
    set_connection_roles,
    set_metrika_enabled,
    start_oauth,
    yandex_configured,
)

router = APIRouter(prefix="/web", tags=["web"])


class RegisterBody(BaseModel):
    email: str = ""
    password: str = ""
    name: str = ""


class LoginBody(BaseModel):
    email: str = ""
    password: str = ""


class EnabledBody(BaseModel):
    enabled: bool = True


class ClientBody(BaseModel):
    name: str = ""
    site: str = ""
    notes: str = ""
    kpi_type: str = ""
    kpi_target: float | None = None
    monthly_budget_rub: float | None = None


class ClientPatchBody(BaseModel):
    name: str | None = None
    site: str | None = None
    notes: str | None = None
    kpi_type: str | None = None
    kpi_target: float | None = None
    monthly_budget_rub: float | None = None
    archived: bool | None = None


class ClientAssignBody(BaseModel):
    client_id: int | None = None


class BulkActionBody(BaseModel):
    campaign_ids: list[int] = Field(default_factory=list)
    action: str = ""
    daily_budget_rub: float | None = None


class CreateCampaignBody(BaseModel):
    account_id: int
    name: str = ""
    daily_budget_rub: float = 0
    start_date: str = ""


class UpdateCampaignBody(BaseModel):
    name: str | None = None
    daily_budget_rub: float | None = None
    negative_keywords: list[str] | None = None
    negative_text: str | None = None


class AdGroupBody(BaseModel):
    name: str = ""
    region_ids: list[int] = Field(default_factory=lambda: [225])


class AdGroupPatchBody(BaseModel):
    name: str | None = None
    negative_keywords: list[str] | None = None
    negative_text: str | None = None


class AdGroupIdsBody(BaseModel):
    adgroup_ids: list[int] = Field(default_factory=list)


class SitelinkBody(BaseModel):
    title: str = ""
    href: str = ""
    description: str = ""


class AdExtrasBody(BaseModel):
    # None — не менять, пустой список — снять с объявления.
    sitelinks: list[SitelinkBody] | None = Field(default=None, max_length=8)
    callouts: list[str] | None = Field(default=None, max_length=10)
    image_hashes: list[str] | None = Field(default=None, max_length=5)


class TextAdBody(BaseModel):
    adgroup_row_id: int
    title: str = ""
    title2: str = ""
    text: str = ""
    href: str = ""
    # Варианты комбинаторного объявления ЕПК: до 7 заголовков и 3 текстов.
    titles: list[str] | None = None
    texts: list[str] | None = None
    extras: AdExtrasBody | None = None


class TextAdPatchBody(BaseModel):
    title: str | None = None
    title2: str | None = None
    text: str | None = None
    href: str | None = None
    titles: list[str] | None = None
    texts: list[str] | None = None
    extras: AdExtrasBody | None = None


class AdImageBody(BaseModel):
    name: str = Field(default="", max_length=255)
    data: str = Field(min_length=1, max_length=8 * 1024 * 1024)


class AdActionBody(BaseModel):
    ad_ids: list[int] = Field(default_factory=list)
    action: str = ""


class KeywordsCreateBody(BaseModel):
    adgroup_row_id: int
    phrases: list[str] = Field(default_factory=list)
    text: str = ""
    bid_rub: float | None = None


class KeywordPatchBody(BaseModel):
    keyword: str | None = None
    bid_rub: float | None = None


class KeywordActionBody(BaseModel):
    keyword_ids: list[int] = Field(default_factory=list)
    action: str = ""


class SearchQueriesSyncBody(BaseModel):
    days: int = 14


class SearchQueryKeywordBody(BaseModel):
    adgroup_row_id: int | None = None
    bid_rub: float | None = None


class SearchQueryNegativeBody(BaseModel):
    scope: str = "adgroup"


class SearchQueryBulkBody(BaseModel):
    query_ids: list[int] = Field(default_factory=list)
    action: str = ""
    adgroup_row_id: int | None = None
    bid_rub: float | None = None


class RuleBody(BaseModel):
    name: str = ""
    rule_type: str = ""
    threshold_value: float = 0
    lookback_days: int = 7
    action: str = "pause"
    dry_run: bool = True
    scope_account_id: int | None = None
    scope_workspace_id: int | None = None
    cooldown_hours: int = 24
    goal_row_id: int | None = None
    action_budget_rub: float | None = None
    action_bid_rub: float | None = None
    action_pct: float | None = None
    target: str = "campaign"


class RulePatchBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    dry_run: bool | None = None
    threshold_value: float | None = None
    lookback_days: int | None = None
    cooldown_hours: int | None = None
    action: str | None = None
    goal_row_id: int | None = None
    action_budget_rub: float | None = None
    action_bid_rub: float | None = None
    action_pct: float | None = None
    scope_account_id: int | None = None
    scope_workspace_id: int | None = None
    target: str | None = None
    rule_type: str | None = None


class ConnectionRolesBody(BaseModel):
    use_metrika: bool | None = None
    use_direct: bool | None = None


class OAuthExchangeBody(BaseModel):
    code: str = ""


class AnalyticsSyncBody(BaseModel):
    day: str = ""
    days: int | None = None
    date_from: str = ""
    date_to: str = ""


class ForgotPasswordBody(BaseModel):
    email: str = ""


class ResetPasswordBody(BaseModel):
    token: str = ""
    password: str = ""


class ChangePasswordBody(BaseModel):
    current_password: str = ""
    new_password: str = ""


def _http(exc: UsersError | YandexError) -> HTTPException:
    code = exc.code
    status = 400
    if code in ("unauthorized", "credentials", "state", "code", "token-expired"):
        status = 401
    elif code == "blocked":
        status = 403
    elif code == "not-found":
        status = 404
    elif code in ("exists", "yandex-taken"):
        status = 409
    message = getattr(exc, "message", None)
    detail: str | dict = {"code": code, "message": message} if message else code
    return HTTPException(status_code=status, detail=detail)


def _start_job(kind: str, user_id: int, payload: dict) -> dict:
    """Долгий синк — задачей в очереди; фронт опрашивает GET /web/jobs/{id}."""
    return {"job": public_job(submit(kind, user_id, payload, priority=PRIORITY_USER))}


_OWNED_TABLES = ("yandex_connections", "direct_accounts", "direct_campaigns", "metrika_counters")


def _require_owned(table: str, item_id: int, user_id: int) -> None:
    """404 сразу, а не задачей с ошибкой: объект должен принадлежать пользователю."""
    assert table in _OWNED_TABLES
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE id = %s AND user_id = %s",
            (item_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not-found")


def _frontend_oauth_redirect(*, code: str = "", error: str = "") -> RedirectResponse:
    """В URL уходит только одноразовый code или error — не session token."""
    base = public_base_url() or ""
    query = urlencode({k: v for k, v in (("code", code), ("error", error)) if v})
    target = f"{base}/oauth/done"
    if query:
        target = f"{target}?{query}"
    return RedirectResponse(url=target, status_code=302)


@router.get("/config")
def web_config() -> dict:
    return {
        "yandex_oauth": yandex_configured(),
        "privacy_url": privacy_url() or "/privacy",
        "consent_url": consent_url() or "/consent",
        "offer_url": offer_url() or "/offer",
        "cookies_url": cookies_url() or "/cookies",
    }


@router.post("/register")
def web_register(body: RegisterBody) -> dict:
    try:
        user = register_user(email=body.email, password=body.password, name=body.name)
    except UsersError as exc:
        raise _http(exc) from exc
    return {"token": make_session(user["id"]), "user": public_user(user)}


@router.post("/login")
def web_login(body: LoginBody) -> dict:
    try:
        user = login_user(email=body.email, password=body.password)
    except UsersError as exc:
        raise _http(exc) from exc
    return {"token": make_session(user["id"]), "user": public_user(user)}


@router.post("/forgot-password")
def web_forgot_password(body: ForgotPasswordBody) -> dict:
    request_password_reset(body.email)
    return {"ok": True}


@router.post("/reset-password")
def web_reset_password(body: ResetPasswordBody) -> dict:
    try:
        reset_password(token=body.token, password=body.password)
    except UsersError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.post("/change-password")
def web_change_password(
    body: ChangePasswordBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        change_password(
            user_id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except UsersError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.get("/me")
def web_me(user_id: int = Depends(require_web_user)) -> dict:
    user = get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if user.get("blocked_at"):
        raise HTTPException(status_code=403, detail="blocked")
    return {
        "user": public_user(user),
        "clients": list_clients(user_id),
        "connections": list_connections(user_id),
        "metrika": list_metrika(user_id),
        "direct": list_direct(user_id),
        "workspaces": list_workspaces(user_id),
    }


@router.get("/yandex/start")
def web_yandex_start(
    purpose: str = "login",
    mode: str = "",
    authorization: str = Header(default=""),
) -> dict:
    """mode: "" — Метрика и Директ, "metrika" / "direct" — одна роль, "sandbox" — песочница Директа."""
    purpose = (purpose or "login").strip().lower()
    if purpose not in ("login", "connect"):
        raise HTTPException(status_code=400, detail="purpose")
    mode = (mode or "").strip().lower()
    if mode not in OAUTH_MODES or (mode and purpose != "connect"):
        raise HTTPException(status_code=400, detail="mode")
    user_id: int | None = None
    if purpose == "connect":
        scheme, _, token = authorization.partition(" ")
        user_id = verify_session(token) if scheme.lower() == "bearer" else None
        if user_id is None:
            raise HTTPException(status_code=401, detail="unauthorized")
    try:
        url = start_oauth(purpose=purpose, user_id=user_id, mode=mode)
    except YandexError as exc:
        raise _http(exc) from exc
    return {"url": url}


@router.get("/yandex/callback")
def web_yandex_callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    if error:
        return _frontend_oauth_redirect(error="yandex")
    try:
        user, _job = complete_oauth(code=code, state=state)
    except (YandexError, UsersError) as exc:
        return _frontend_oauth_redirect(error=exc.code)
    except Exception:
        return _frontend_oauth_redirect(error="yandex")
    return _frontend_oauth_redirect(code=issue_oauth_code(user["id"]))


@router.post("/oauth/exchange")
def web_oauth_exchange(body: OAuthExchangeBody) -> dict:
    user_id = consume_oauth_code(body.code)
    if user_id is None:
        raise HTTPException(status_code=401, detail="state")
    user = get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if user.get("blocked_at"):
        raise HTTPException(status_code=403, detail="blocked")
    return {"token": make_session(user_id), "user": public_user(user)}


@router.post("/connections/{connection_id}/sync")
def web_sync_connection(
    connection_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    _require_owned("yandex_connections", connection_id, user_id)
    # deep: ещё кампании всех кабинетов и цели счётчиков — чтобы всё подтянулось сразу.
    return _start_job("sync_connection", user_id, {"connection_id": connection_id, "deep": True})


@router.patch("/connections/{connection_id}")
def web_patch_connection(
    connection_id: int,
    body: ConnectionRolesBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        out = set_connection_roles(
            user_id, connection_id, use_metrika=body.use_metrika, use_direct=body.use_direct
        )
    except YandexError as exc:
        raise _http(exc) from exc
    ensure_workspaces(user_id)
    return out


@router.delete("/connections/{connection_id}")
def web_delete_connection(
    connection_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        delete_connection(user_id, connection_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.patch("/metrika/{item_id}")
def web_patch_metrika(
    item_id: int,
    body: EnabledBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        out = set_metrika_enabled(user_id, item_id, body.enabled)
    except YandexError as exc:
        raise _http(exc) from exc
    # Включённый счётчик сразу получает своё пространство и загрузку данных.
    if body.enabled:
        ensure_workspaces(user_id)
        if any(int(c["id"]) == item_id for c in canonical_counters(user_id)):
            submit("load_counter", user_id, {"counter_row_id": item_id}, priority=PRIORITY_USER)
    return out


@router.patch("/direct/{item_id}")
def web_patch_direct(
    item_id: int,
    body: EnabledBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        out = set_direct_enabled(user_id, item_id, body.enabled)
    except YandexError as exc:
        raise _http(exc) from exc
    # Включили — догружаем (или актуализируем); выключили — идущая загрузка остановится сама.
    if body.enabled:
        submit("load_account", user_id, {"account_id": item_id}, priority=PRIORITY_USER)
    return out


@router.get("/clients")
def web_list_clients(user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_clients(user_id)}


@router.post("/clients")
def web_create_client(body: ClientBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return create_client(user_id, **body.model_dump())
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/clients/{client_id}")
def web_patch_client(
    client_id: int,
    body: ClientPatchBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return update_client(user_id, client_id, **body.model_dump(exclude_unset=True))
    except YandexError as exc:
        raise _http(exc) from exc


@router.delete("/clients/{client_id}")
def web_delete_client(client_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        delete_client(user_id, client_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.put("/direct/{account_id}/client")
def web_assign_direct_client(
    account_id: int,
    body: ClientAssignBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return assign_direct_account(user_id, account_id, body.client_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.put("/metrika/{counter_row_id}/client")
def web_assign_metrika_client(
    counter_row_id: int,
    body: ClientAssignBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return assign_metrika_counter(user_id, counter_row_id, body.client_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/{account_id}/sync")
def web_sync_account_campaigns(
    account_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    _require_owned("direct_accounts", account_id, user_id)
    return _start_job("load_account", user_id, {"account_id": account_id})


@router.post("/direct/{account_id}/load")
def web_reload_account(
    account_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    """Загрузить кабинет заново: все этапы за 30 дней с нуля."""
    _require_owned("direct_accounts", account_id, user_id)
    try:
        reset_account_load(user_id, account_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return _start_job("load_account", user_id, {"account_id": account_id})


@router.post("/metrika/{counter_row_id}/load")
def web_load_counter(
    counter_row_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    _require_owned("metrika_counters", counter_row_id, user_id)
    return _start_job("load_counter", user_id, {"counter_row_id": counter_row_id})


@router.get("/loads")
def web_loads(user_id: int = Depends(require_web_user)) -> dict:
    """Этапы загрузки кабинетов и счётчиков и идущие задачи — для индикатора в шапке."""
    return load_overview(user_id)


@router.get("/direct/campaigns")
def web_list_campaigns(
    account_id: int | None = None,
    state: str = "",
    search: str = "",
    limit: int = 200,
    offset: int = 0,
    user_id: int = Depends(require_web_user),
) -> dict:
    return list_campaigns(
        user_id,
        account_id=account_id,
        state=state,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/direct/campaigns/actions")
def web_bulk_actions(
    body: BulkActionBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return bulk_campaign_action(
            user_id,
            campaign_ids=body.campaign_ids,
            action=body.action,
            daily_budget_rub=body.daily_budget_rub,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/campaigns")
def web_create_campaign(
    body: CreateCampaignBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return create_text_campaign(
            user_id,
            account_id=body.account_id,
            name=body.name,
            daily_budget_rub=body.daily_budget_rub,
            start_date=body.start_date or None,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/direct/campaigns/{campaign_row_id}")
def web_update_campaign(
    campaign_row_id: int,
    body: UpdateCampaignBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    negatives = body.negative_keywords
    if negatives is None and body.negative_text is not None:
        negatives = [
            line.strip() for line in body.negative_text.splitlines() if line.strip()
        ]
    try:
        return update_campaign(
            user_id,
            campaign_row_id,
            name=body.name,
            daily_budget_rub=body.daily_budget_rub,
            negative_keywords=negatives,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/direct/campaigns/{campaign_row_id}")
def web_get_campaign(campaign_row_id: int, user_id: int = Depends(require_web_user)) -> dict:
    camp = get_campaign(user_id, campaign_row_id)
    if camp is None:
        raise HTTPException(status_code=404, detail="not-found")
    return {
        "campaign": camp,
        "adgroups": list_adgroups(user_id, campaign_row_id),
        "ads": list_ads(user_id, campaign_row_id=campaign_row_id),
        "keywords": list_keywords(user_id, campaign_row_id=campaign_row_id),
    }


@router.post("/direct/campaigns/{campaign_row_id}/sync-structure")
def web_sync_campaign_structure(
    campaign_row_id: int,
    user_id: int = Depends(require_web_user),
) -> dict:
    _require_owned("direct_campaigns", campaign_row_id, user_id)
    return _start_job("sync_campaign_structure", user_id, {"campaign_row_id": campaign_row_id})


@router.post("/direct/campaigns/{campaign_row_id}/adgroups")
def web_create_adgroup(
    campaign_row_id: int,
    body: AdGroupBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return create_adgroup(
            user_id,
            campaign_row_id=campaign_row_id,
            name=body.name,
            region_ids=body.region_ids or [225],
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/direct/adgroups/{adgroup_row_id}")
def web_patch_adgroup(
    adgroup_row_id: int,
    body: AdGroupPatchBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    negatives = body.negative_keywords
    if negatives is None and body.negative_text is not None:
        negatives = [
            line.strip() for line in body.negative_text.splitlines() if line.strip()
        ]
    try:
        return update_adgroup(
            user_id,
            adgroup_row_id,
            name=body.name,
            negative_keywords=negatives,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/adgroups/delete")
def web_delete_adgroups(body: AdGroupIdsBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return delete_adgroups(user_id, body.adgroup_ids)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/ads")
def web_create_ad(body: TextAdBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return create_text_ad(
            user_id,
            adgroup_row_id=body.adgroup_row_id,
            title=body.title,
            title2=body.title2,
            text=body.text,
            href=body.href,
            titles=body.titles,
            texts=body.texts,
            extras=body.extras.model_dump(exclude_none=True) if body.extras else None,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/direct/ads/{ad_row_id}")
def web_patch_ad(
    ad_row_id: int,
    body: TextAdPatchBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return update_text_ad(
            user_id,
            ad_row_id,
            title=body.title,
            title2=body.title2,
            text=body.text,
            href=body.href,
            titles=body.titles,
            texts=body.texts,
            extras=body.extras.model_dump(exclude_none=True) if body.extras else None,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/direct/ads/{ad_row_id}/extras")
def web_ad_extras(ad_row_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Быстрые ссылки, уточнения и картинки объявления — из Директа, для формы правки."""
    try:
        row = _ad_db_row(user_id, ad_row_id)
        token = get_connection_access_token(row["connection_id"], user_id)
        return ad_extras.read_extras(token, row["client_login"], int(row["ad_id"]))
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/accounts/{account_id}/ad-images")
def web_upload_ad_image(account_id: int, body: AdImageBody, user_id: int = Depends(require_web_user)) -> dict:
    """Картинка в библиотеку кабинета; хэш потом уходит в объявление."""
    try:
        account = _account_creds(user_id, account_id)
        token = get_connection_access_token(account["connection_id"], user_id)
        return ad_extras.upload_image(token, account["client_login"], name=body.name, data_b64=body.data)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/ads/actions")
def web_ad_actions(body: AdActionBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return bulk_ad_action(user_id, ad_row_ids=body.ad_ids, action=body.action)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/keywords")
def web_create_keywords(body: KeywordsCreateBody, user_id: int = Depends(require_web_user)) -> dict:
    phrases = list(body.phrases or [])
    if body.text.strip():
        phrases.extend(line.strip() for line in body.text.splitlines() if line.strip())
    try:
        return create_keywords(
            user_id,
            adgroup_row_id=body.adgroup_row_id,
            phrases=phrases,
            bid_rub=body.bid_rub,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/direct/keywords/{keyword_row_id}")
def web_patch_keyword(
    keyword_row_id: int,
    body: KeywordPatchBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return update_keyword(
            user_id,
            keyword_row_id,
            keyword=body.keyword,
            bid_rub=body.bid_rub,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/keywords/actions")
def web_keyword_actions(
    body: KeywordActionBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return bulk_keyword_action(
            user_id,
            keyword_row_ids=body.keyword_ids,
            action=body.action,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/direct/campaigns/{campaign_row_id}/search-queries")
def web_list_search_queries(
    campaign_row_id: int,
    limit: int = 200,
    offset: int = 0,
    search: str = "",
    min_clicks: int | None = None,
    min_cost_rub: float | None = None,
    no_conversions: bool = False,
    user_id: int = Depends(require_web_user),
) -> dict:
    return list_search_queries(
        user_id,
        campaign_row_id,
        limit=limit,
        offset=offset,
        search=search,
        min_clicks=min_clicks,
        min_cost_rub=min_cost_rub,
        no_conversions=no_conversions,
    )


@router.post("/direct/campaigns/{campaign_row_id}/search-queries/sync")
def web_sync_search_queries(
    campaign_row_id: int,
    body: SearchQueriesSyncBody | None = None,
    user_id: int = Depends(require_web_user),
) -> dict:
    days = max(1, min(int(body.days if body and body.days else 14), 90))
    _require_owned("direct_campaigns", campaign_row_id, user_id)
    return _start_job(
        "sync_search_queries",
        user_id,
        {"campaign_row_id": campaign_row_id, "days": days},
    )


@router.post("/direct/search-queries/{query_row_id}/add-keyword")
def web_search_query_add_keyword(
    query_row_id: int,
    body: SearchQueryKeywordBody | None = None,
    user_id: int = Depends(require_web_user),
) -> dict:
    body = body or SearchQueryKeywordBody()
    try:
        return add_query_as_keyword(
            user_id,
            query_row_id,
            adgroup_row_id=body.adgroup_row_id,
            bid_rub=body.bid_rub,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/search-queries/{query_row_id}/add-negative")
def web_search_query_add_negative(
    query_row_id: int,
    body: SearchQueryNegativeBody | None = None,
    user_id: int = Depends(require_web_user),
) -> dict:
    body = body or SearchQueryNegativeBody()
    try:
        return add_query_as_negative(user_id, query_row_id, scope=body.scope or "adgroup")
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/direct/search-queries/actions")
def web_search_query_bulk(
    body: SearchQueryBulkBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return bulk_search_query_action(
            user_id,
            query_ids=body.query_ids,
            action=body.action,
            adgroup_row_id=body.adgroup_row_id,
            bid_rub=body.bid_rub,
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/actions")
def web_action_logs(
    source: str = "",
    before_id: int | None = None,
    limit: int = 50,
    user_id: int = Depends(require_web_user),
) -> dict:
    return {"items": list_action_logs(user_id, limit=limit, source=source or None, before_id=before_id)}


@router.get("/actions/{log_id}/undo")
def web_action_undo_preview(log_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return undo_preview(user_id, log_id)
    except YandexError as exc:
        raise _http(exc) from exc


@router.post("/actions/{log_id}/undo")
def web_action_undo(log_id: int, user_id: int = Depends(require_web_user)) -> dict:
    """Откат задачей: шаги идут в Директ. Без повторов — откат не должен выполниться дважды."""
    try:
        preview = undo_preview(user_id, log_id)
    except YandexError as exc:
        raise _http(exc) from exc
    if not preview["available"]:
        raise _http(YandexError("undo", preview["reason"]))
    job = submit("undo_action", user_id, {"log_id": log_id}, priority=PRIORITY_USER, max_attempts=1)
    return {"job": public_job(job)}


@router.post("/analytics/sync")
def web_analytics_sync(
    body: AnalyticsSyncBody | None = None,
    day: str = "",
    days: int | None = None,
    user_id: int = Depends(require_web_user),
) -> dict:
    payload = body or AnalyticsSyncBody()
    day_q = (payload.day or day or "").strip()
    days_n = payload.days if payload.days is not None else days
    date_from_s = (payload.date_from or "").strip()
    date_to_s = (payload.date_to or "").strip()
    try:
        if date_from_s or date_to_s:
            if not date_from_s or not date_to_s:
                raise HTTPException(status_code=400, detail="date")
            d_from = date.fromisoformat(date_from_s)
            d_to = date.fromisoformat(date_to_s)
        elif days_n is not None:
            n = max(1, min(int(days_n), 30))
            d_to = date.today() - timedelta(days=1)
            d_from = d_to - timedelta(days=n - 1)
        else:
            d = date.fromisoformat(day_q) if day_q else date.today() - timedelta(days=1)
            return _start_job("stats_day", user_id, {"day": d.isoformat()})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date") from exc
    if d_from > d_to or (d_to - d_from).days + 1 > 30:
        raise HTTPException(status_code=400, detail="date")
    return _start_job(
        "stats_range",
        user_id,
        {"date_from": d_from.isoformat(), "date_to": d_to.isoformat()},
    )


@router.get("/jobs")
def web_list_jobs(
    active: bool = False,
    limit: int = 30,
    user_id: int = Depends(require_web_user),
) -> dict:
    return {"items": list_jobs(user_id, active=active, limit=limit)}


@router.get("/jobs/{job_id}")
def web_get_job(job_id: int, user_id: int = Depends(require_web_user)) -> dict:
    job = get_job(user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="not-found")
    return job


@router.get("/rules")
def web_list_rules(workspace_id: int | None = None, user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_rules(user_id, workspace_id=workspace_id)}


@router.get("/rules/runs")
def web_rule_runs(user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_rule_runs(user_id)}


@router.get("/sync-runs")
def web_sync_runs(user_id: int = Depends(require_web_user)) -> dict:
    return {"items": list_sync_runs(user_id)}


@router.get("/telegram")
def web_telegram_status(user_id: int = Depends(require_web_user)) -> dict:
    return get_telegram_status(user_id)


@router.post("/telegram/link")
def web_telegram_link(user_id: int = Depends(require_web_user)) -> dict:
    return create_link_code(user_id)


@router.post("/telegram/unlink")
def web_telegram_unlink(user_id: int = Depends(require_web_user)) -> dict:
    unlink_telegram(user_id)
    return {"ok": True}


@router.post("/telegram/test")
def web_telegram_test(user_id: int = Depends(require_web_user)) -> dict:
    ok = notify_user(user_id, "AiMarketing: тестовое сообщение. Telegram подключён.")
    if not ok:
        raise HTTPException(status_code=400, detail="telegram")
    return {"ok": True}


@router.post("/telegram/webhook")
def web_telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    """Опциональный webhook → {PUBLIC_BASE_URL}/api/web/telegram/webhook"""
    from app.telegram_notify import verify_webhook_secret

    if not verify_webhook_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        handle_update(update)
    except Exception:
        log.exception("telegram webhook")
    return {"ok": True}


@router.post("/rules")
def web_create_rule(body: RuleBody, user_id: int = Depends(require_web_user)) -> dict:
    try:
        return create_rule(
            user_id,
            name=body.name,
            rule_type=body.rule_type,
            threshold_value=body.threshold_value,
            lookback_days=body.lookback_days,
            action=body.action,
            dry_run=body.dry_run,
            scope_account_id=body.scope_account_id,
            scope_workspace_id=body.scope_workspace_id,
            cooldown_hours=body.cooldown_hours,
            goal_row_id=body.goal_row_id,
            action_budget_rub=body.action_budget_rub,
            action_bid_rub=body.action_bid_rub,
            action_pct=body.action_pct,
            target=body.target or "campaign",
        )
    except YandexError as exc:
        raise _http(exc) from exc


@router.patch("/rules/{rule_id}")
def web_patch_rule(
    rule_id: int,
    body: RulePatchBody,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return update_rule(user_id, rule_id, **body.model_dump(exclude_unset=True))
    except YandexError as exc:
        raise _http(exc) from exc


@router.delete("/rules/{rule_id}")
def web_delete_rule(rule_id: int, user_id: int = Depends(require_web_user)) -> dict:
    try:
        delete_rule(user_id, rule_id)
    except YandexError as exc:
        raise _http(exc) from exc
    return {"ok": True}


@router.post("/rules/{rule_id}/run")
def web_run_rule(
    rule_id: int,
    dry_run: bool | None = None,
    user_id: int = Depends(require_web_user),
) -> dict:
    try:
        return run_rule(user_id, rule_id, force_dry_run=dry_run)
    except YandexError as exc:
        raise _http(exc) from exc


@router.get("/metrika/goals")
def web_list_goals(
    counter_row_id: int | None = None,
    user_id: int = Depends(require_web_user),
) -> dict:
    return {"items": list_goals(user_id, counter_row_id)}


@router.post("/metrika/{counter_row_id}/goals/sync")
def web_sync_goals(counter_row_id: int, user_id: int = Depends(require_web_user)) -> dict:
    _require_owned("metrika_counters", counter_row_id, user_id)
    return _start_job("sync_goals", user_id, {"counter_row_id": counter_row_id})

