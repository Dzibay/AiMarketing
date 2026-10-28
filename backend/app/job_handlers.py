"""Обработчики задач очереди и исполнение одной захваченной задачи."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import date

from app.account_load import run_account, run_counter
from app.actions import duplicate_campaign
from app.adgroups import sync_campaign_adgroups
from app.ads import sync_campaign_ads
from app.brief import draft_brief
from app.clock import today_msk
from app.db import pool
from app.digest import send_digest
from app.direct_balance import sync_balances_for_user
from app.direct_sync import sync_account_campaigns
from app.entity_stats import sync_campaign_entity_stats
from app.journal import acting_as
from app.jobs import PRIORITY_BACKGROUND, PRIORITY_USER, complete, enqueue, fail, heartbeat, set_progress
from app.keywords import sync_campaign_keywords
from app.metrika_sync import canonical_counters, sync_counter_goals
from app.negative_lists import apply_negative_list
from app.query_cleaning import apply_decisions, classify_queries, sync_workspace_queries
from app.rules import run_enabled_rules_for_user
from app.search_queries import sync_campaign_search_queries
from app.stats import sync_direct_stats_for_user, sync_stats_for_user, sync_stats_range
from app.yandex import YandexError, sync_connection

log = logging.getLogger("app.job_handlers")

Progress = Callable[[int, int, str], None]
Handler = Callable[[int, dict, Progress], dict]

HEARTBEAT_SECONDS = 30


def _int(payload: dict, key: str) -> int:
    try:
        return int(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise YandexError("payload", f"В задаче нет {key}") from exc


def _day(payload: dict, key: str) -> date | None:
    raw = payload.get(key)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise YandexError("date") from exc


def _sync_connection(user_id: int, payload: dict, progress: Progress) -> dict:
    """
    Аккаунты и счётчики подключения, затем загрузка данных отдельными задачами: deep (подключение,
    «Синхронизировать») — всех включённых кабинетов и счётчиков вперёд очереди; плановый —
    только ещё ни разу не загруженных (новых), остальных актуализирует расписание.
    """
    connection_id = _int(payload, "connection_id")
    deep = bool(payload.get("deep"))
    progress(0, 1, "Аккаунты и счётчики")
    result = sync_connection(user_id, connection_id)
    with pool.connection() as conn:
        accounts = conn.execute(
            """
            SELECT id FROM direct_accounts
            WHERE connection_id = %s AND user_id = %s AND enabled AND (%s OR loaded_at IS NULL)
            ORDER BY id
            """,
            (connection_id, user_id, deep),
        ).fetchall()
    counters = [
        c for c in canonical_counters(user_id) if c["connection_id"] == connection_id and (deep or c["loaded_at"] is None)
    ]
    priority = PRIORITY_USER if deep else PRIORITY_BACKGROUND
    for account in accounts:
        submit("load_account", user_id, {"account_id": int(account["id"])}, priority=priority)
    for counter in counters:
        submit("load_counter", user_id, {"counter_row_id": int(counter["id"])}, priority=priority)
    return {**result, "loads": len(accounts) + len(counters)}


def _load_account(user_id: int, payload: dict, progress: Progress) -> dict:
    return run_account(user_id, _int(payload, "account_id"), progress)


def _load_counter(user_id: int, payload: dict, progress: Progress) -> dict:
    return run_counter(user_id, _int(payload, "counter_row_id"), progress)


def _sync_account_campaigns(user_id: int, payload: dict, progress: Progress) -> dict:
    return sync_account_campaigns(user_id, _int(payload, "account_id"))


def _sync_campaign_structure(user_id: int, payload: dict, progress: Progress) -> dict:
    campaign_row_id = _int(payload, "campaign_row_id")
    progress(0, 3, "Группы объявлений")
    groups = sync_campaign_adgroups(user_id, campaign_row_id)
    progress(1, 3, "Объявления")
    ads = sync_campaign_ads(user_id, campaign_row_id)
    progress(2, 3, "Ключевые фразы")
    keywords = sync_campaign_keywords(user_id, campaign_row_id)
    return {"adgroups": groups, "ads": ads, "keywords": keywords}


def _sync_search_queries(user_id: int, payload: dict, progress: Progress) -> dict:
    campaign_row_id = _int(payload, "campaign_row_id")
    days = int(payload.get("days") or 14)
    progress(0, 2, "Поисковые запросы")
    queries = sync_campaign_search_queries(user_id, campaign_row_id, days=days)
    progress(1, 2, "Статистика групп и фраз")
    entities = sync_campaign_entity_stats(user_id, campaign_row_id, days=days)
    return {"queries": queries, "entities": entities, "status": "ok"}


def _sync_goals(user_id: int, payload: dict, progress: Progress) -> dict:
    return sync_counter_goals(user_id, _int(payload, "counter_row_id"))


def _stats_day(user_id: int, payload: dict, progress: Progress) -> dict:
    return sync_stats_for_user(user_id, _day(payload, "day"))


def _stats_range(user_id: int, payload: dict, progress: Progress) -> dict:
    date_from = _day(payload, "date_from")
    date_to = _day(payload, "date_to")
    if date_from is None or date_to is None:
        raise YandexError("date")
    return sync_stats_range(user_id, date_from=date_from, date_to=date_to, on_progress=progress)


def _stats_today(user_id: int, payload: dict, progress: Progress) -> dict:
    """Расход за сегодня (данные Директа неокончательные) и балансы — для сводки."""
    progress(0, 2, "Расход за сегодня")
    direct = sync_direct_stats_for_user(user_id, today_msk())
    progress(1, 2, "Балансы")
    balances = sync_balances_for_user(user_id)
    return {"direct": direct, "balances": balances}


def _send_digest(user_id: int, payload: dict, progress: Progress) -> dict:
    return send_digest(user_id, _day(payload, "day") or today_msk())


def _apply_negative_list(user_id: int, payload: dict, progress: Progress) -> dict:
    ids = payload.get("campaign_row_ids")
    if not isinstance(ids, list):
        raise YandexError("payload", "В задаче нет campaign_row_ids")
    return apply_negative_list(user_id, _int(payload, "list_id"), ids, progress)


def _run_rules(user_id: int, payload: dict, progress: Progress) -> dict:
    return {"rules_run": run_enabled_rules_for_user(user_id)}


def _duplicate_campaign(user_id: int, payload: dict, progress: Progress) -> dict:
    target = payload.get("target_account_id")
    workspace_id = payload.get("workspace_id")
    with acting_as(str(payload.get("source") or "user")):
        return duplicate_campaign(
            user_id,
            _int(payload, "campaign_row_id"),
            target_account_id=int(target) if target else None,
            name=payload.get("name") or None,
            workspace_id=int(workspace_id) if workspace_id else None,
            progress=progress,
        )


def _ai_chat(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.ai_agent import run_chat

    return run_chat(user_id, _int(payload, "chat_id"), progress)


def _sync_workspace_queries(user_id: int, payload: dict, progress: Progress) -> dict:
    return sync_workspace_queries(user_id, {**payload, "workspace_id": _int(payload, "workspace_id")}, progress)


def _classify_queries(user_id: int, payload: dict, progress: Progress) -> dict:
    return classify_queries(user_id, {**payload, "workspace_id": _int(payload, "workspace_id")}, progress)


def _apply_query_decisions(user_id: int, payload: dict, progress: Progress) -> dict:
    items = payload.get("items")
    if not isinstance(items, list):
        raise YandexError("payload", "В задаче нет items")
    bid = payload.get("bid_rub")
    with acting_as(str(payload.get("source") or "user")):
        return apply_decisions(
            user_id,
            _int(payload, "workspace_id"),
            items,
            force=bool(payload.get("force")),
            bid_rub=float(bid) if bid not in (None, "") else None,
            progress=progress,
        )


def _brief_draft(user_id: int, payload: dict, progress: Progress) -> dict:
    return draft_brief(user_id, {**payload, "workspace_id": _int(payload, "workspace_id")}, progress)


def _bulk_budget(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.budgets import bulk_budget

    def num(key: str) -> float | None:
        value = payload.get(key)
        return float(value) if value not in (None, "") else None

    workspace_id = payload.get("workspace_id")
    with acting_as(str(payload.get("source") or "user")):
        return bulk_budget(
            user_id,
            campaign_ids=[int(i) for i in payload.get("campaign_ids") or []],
            weekly_budget_rub=num("weekly_budget_rub"),
            daily_budget_rub=num("daily_budget_rub"),
            budget_change_pct=num("budget_change_pct"),
            target_change_pct=num("target_change_pct"),
            workspace_id=int(workspace_id) if workspace_id else None,
            progress=progress,
        )


def _undo_action(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.undo import run_undo

    return run_undo(user_id, _int(payload, "log_id"), progress)


def _slice_report(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.slices import slice_report

    return slice_report(user_id, payload, progress)


def _slice_adjust(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.slices import apply_adjustments

    with acting_as(str(payload.get("source") or "user")):
        return apply_adjustments(user_id, payload, progress)


def _exclude_placements(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.slices import exclude_placements

    with acting_as(str(payload.get("source") or "user")):
        return exclude_placements(user_id, payload, progress)


def _placements_ai(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.slices import ai_placements

    return ai_placements(user_id, payload, progress)


def _ad_report(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.ad_stats import ad_report

    return ad_report(user_id, payload, progress)


def _ads_ai(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.ad_stats import ai_rewrite_ads

    return ai_rewrite_ads(user_id, payload, progress)


def _ads_ab(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.ad_stats import create_ab_ads

    with acting_as(str(payload.get("source") or "user")):
        return create_ab_ads(user_id, payload, progress)


HANDLERS: dict[str, Handler] = {
    "sync_connection": _sync_connection,
    "sync_account_campaigns": _sync_account_campaigns,
    "load_account": _load_account,
    "load_counter": _load_counter,
    "sync_campaign_structure": _sync_campaign_structure,
    "sync_search_queries": _sync_search_queries,
    "sync_goals": _sync_goals,
    "stats_day": _stats_day,
    "stats_range": _stats_range,
    "stats_today": _stats_today,
    "send_digest": _send_digest,
    "apply_negative_list": _apply_negative_list,
    "run_rules": _run_rules,
    "duplicate_campaign": _duplicate_campaign,
    "ai_chat": _ai_chat,
    "sync_workspace_queries": _sync_workspace_queries,
    "classify_queries": _classify_queries,
    "apply_query_decisions": _apply_query_decisions,
    "brief_draft": _brief_draft,
    "undo_action": _undo_action,
    "bulk_budget": _bulk_budget,
    "slice_report": _slice_report,
    "slice_adjust": _slice_adjust,
    "exclude_placements": _exclude_placements,
    "placements_ai": _placements_ai,
    "ad_report": _ad_report,
    "ads_ai": _ads_ai,
    "ads_ab": _ads_ab,
}


def dedupe_key(kind: str, user_id: int, payload: dict) -> str:
    return f"{kind}:{user_id}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"


def submit(
    kind: str,
    user_id: int,
    payload: dict | None = None,
    *,
    priority: int = PRIORITY_BACKGROUND,
    max_attempts: int = 3,
) -> dict:
    """Ставит задачу известного вида; одинаковая активная задача не дублируется."""
    if kind not in HANDLERS:
        raise ValueError(f"неизвестный вид задачи: {kind}")
    payload = payload or {}
    return enqueue(
        kind,
        payload,
        user_id=user_id,
        priority=priority,
        dedupe_key=dedupe_key(kind, user_id, payload),
        max_attempts=max_attempts,
    )


def _heartbeat_loop(job_id: int, worker_id: str, done: threading.Event) -> None:
    while not done.wait(HEARTBEAT_SECONDS):
        try:
            heartbeat(job_id, worker_id)
        except Exception:
            log.exception("heartbeat job %s", job_id)


def run_job(job: dict, worker_id: str) -> str:
    """Выполняет захваченную задачу; возвращает итоговый статус (done / queued / error)."""
    handler = HANDLERS.get(job["kind"])
    if handler is None:
        return fail(job, worker_id, error_code="unknown-kind", error=job["kind"])

    def progress(done_n: int, total: int, text: str = "") -> None:
        set_progress(job["id"], worker_id, done_n, total, text)

    done = threading.Event()
    threading.Thread(
        target=_heartbeat_loop,
        args=(job["id"], worker_id, done),
        name=f"job-{job['id']}-heartbeat",
        daemon=True,
    ).start()
    try:
        result = handler(job["user_id"], job.get("payload") or {}, progress)
    except YandexError as exc:
        status = fail(job, worker_id, error_code=exc.code, error=exc.message or "")
        log.warning("job %s %s: %s → %s", job["id"], job["kind"], exc.code, status)
        return status
    except Exception as exc:
        log.exception("job %s %s", job["id"], job["kind"])
        return fail(job, worker_id, error_code="internal", error=str(exc)[:300])
    finally:
        done.set()
    complete(job["id"], worker_id, result)
    return "done"
