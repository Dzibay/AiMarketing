"""Обработчики задач очереди и исполнение одной захваченной задачи."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import date

from app.adgroups import sync_campaign_adgroups
from app.ads import sync_campaign_ads
from app.clock import today_msk
from app.digest import send_digest
from app.direct_balance import sync_balances_for_user
from app.direct_sync import sync_account_campaigns
from app.entity_stats import sync_campaign_entity_stats
from app.jobs import PRIORITY_BACKGROUND, complete, enqueue, fail, heartbeat, set_progress
from app.keywords import sync_campaign_keywords
from app.metrika_sync import sync_counter_goals
from app.negative_lists import apply_negative_list
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
    return sync_connection(user_id, _int(payload, "connection_id"))


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


HANDLERS: dict[str, Handler] = {
    "sync_connection": _sync_connection,
    "sync_account_campaigns": _sync_account_campaigns,
    "sync_campaign_structure": _sync_campaign_structure,
    "sync_search_queries": _sync_search_queries,
    "sync_goals": _sync_goals,
    "stats_day": _stats_day,
    "stats_range": _stats_range,
    "stats_today": _stats_today,
    "send_digest": _send_digest,
    "apply_negative_list": _apply_negative_list,
    "run_rules": _run_rules,
}


def dedupe_key(kind: str, user_id: int, payload: dict) -> str:
    return f"{kind}:{user_id}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"


def submit(
    kind: str,
    user_id: int,
    payload: dict | None = None,
    *,
    priority: int = PRIORITY_BACKGROUND,
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
