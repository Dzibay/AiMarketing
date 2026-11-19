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
from app.journal import acting_as, collect_logged, collect_undoable, single_undoable
from app.jobs import PRIORITY_BACKGROUND, PRIORITY_USER, complete, enqueue, fail, heartbeat, set_progress
from app.keywords import sync_campaign_keywords
from app.metrika_sync import canonical_counters, sync_counter_goals
from app.negative_lists import apply_negative_list
from app.notifications import notify, notify_signals
from app.operation_notify import is_operation, notify_failed, notify_result
from app.plain_errors import plain_error
from app.query_cleaning import (
    apply_decisions,
    classify_all_queries,
    classify_queries,
    label_after_load,
    sync_workspace_queries,
)
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
    account_id = _int(payload, "account_id")
    result = run_account(user_id, account_id, progress)
    if result.get("mode") == "initial":
        # Иначе баланс нового кабинета появится только с ежечасной задачей stats_today.
        try:
            sync_balances_for_user(user_id, [account_id])
        except Exception:
            log.exception("balance after load_account %s", account_id)
    if result.get("status") == "ok" and "queries" in (result.get("stages") or []):
        # Свежие запросы — сразу через бесплатные правила: без ИИ и без лимита.
        result["rules"] = label_after_load(user_id, account_id=account_id)
    return result


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
    rules = label_after_load(user_id, campaign_row_ids=[campaign_row_id])
    return {"queries": queries, "entities": entities, "rules": rules, "status": "ok"}


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
    only_add = payload.get("only_add")
    return apply_negative_list(
        user_id,
        _int(payload, "list_id"),
        ids,
        progress,
        catch_up=bool(payload.get("catch_up")),
        only_add=[str(p) for p in only_add] if isinstance(only_add, list) else None,
    )


def _plan_reminder(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.reminders import send

    return send(user_id, str(payload.get("ends_at") or ""), int(payload.get("days") or 3))


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
            region_ids=[int(r) for r in payload.get("region_ids") or []] or None,
            replace=payload.get("replace"),
            copy_shared_sets=bool(payload.get("copy_shared_sets")),
        )


def _ai_chat(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.ai_agent import run_chat

    chat_id = _int(payload, "chat_id")
    result = run_chat(user_id, chat_id, progress)
    from app.ai_review import pending_summary_chat

    # Разбор недели по расписанию сообщит одной сводкой, без «ИИ ответил» о каждом проекте.
    if not pending_summary_chat(chat_id):
        _notify_answer(user_id, chat_id, result)
    else:
        # Но карточки «Решать из Telegram» нужны и его предложениям: кнопки только спрашивают,
        # Директ меняется лишь после «Да, выполнить» пользователя.
        try:
            from app.telegram_decisions import offer_chat

            offer_chat(user_id, chat_id)
        except Exception:  # noqa: BLE001 — карточки в Telegram не должны ронять ход ИИ
            log.exception("Telegram: карточки предложений чата %s", chat_id)
    return result


def _notify_answer(user_id: int, chat_id: int, result: dict) -> None:
    """Ответ ИИ готов — запись в колокольчике. Открытый чат сразу отмечает её прочитанной (по ссылке),
    так что в ленте остаются только ответы в закрытых чатах."""
    with pool.connection() as conn:
        chat = conn.execute("SELECT title, workspace_id FROM ai_chats WHERE id = %s", (chat_id,)).fetchone()
        last = conn.execute(
            """
            SELECT content FROM ai_messages WHERE chat_id = %s AND role = 'assistant' AND content <> ''
            ORDER BY id DESC LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
    if chat is None:
        return
    ws_id = chat["workspace_id"]
    waiting = (result or {}).get("status") == "waiting"
    text = " ".join(((last or {}).get("content") or "").split())
    # «Решать из Telegram»: карточки предложений с кнопками вместо общего «ИИ ждёт решения».
    try:
        from app.telegram_decisions import offer_chat

        cards = offer_chat(user_id, chat_id)
    except Exception:  # noqa: BLE001 — карточки в Telegram не должны ронять ход ИИ
        log.exception("Telegram: карточки предложений чата %s", chat_id)
        cards = 0
    notify(
        user_id,
        "ai_answer",
        ("ИИ ждёт вашего решения: " if waiting else "ИИ ответил: ") + (chat["title"] or "Новый чат"),
        body=text[:160] + ("…" if len(text) > 160 else ""),
        link=f"/w/{ws_id}/chat/{chat_id}" if ws_id else f"/cabinet/chat/{chat_id}",
        workspace_id=ws_id,
        dedup_key=f"ai_answer:{chat_id}",
        telegram_off=bool(cards),
    )


def _notify_signals(user_id: int, payload: dict, progress: Progress) -> dict:
    return {"sent": notify_signals(user_id)}


def _ai_scheduled_review(user_id: int, payload: dict, progress: Progress) -> dict:
    """Разбор недели по расписанию (app.ai_review): запуск по проектам, потом — одна сводка."""
    from app.ai_review import run_scheduled

    return run_scheduled(user_id, str(payload.get("week") or ""))


def _urgent_check(user_id: int, payload: dict, progress: Progress) -> dict:
    """Ежечасная проверка срочного: новые события и отложенный на утро Telegram."""
    from app.urgent import run

    return run(user_id)


def _compute_effects(user_id: int, payload: dict, progress: Progress) -> dict:
    """Итог правок через 7 и 14 дней (app.effects); стало хуже — запись в колокольчике."""
    from app.effects import compute_for_user

    return compute_for_user(user_id)


LOAD_KINDS = ("load_account", "load_counter", "sync_connection")


def _notify_load_failed(job: dict, error_code: str, error: str = "") -> None:
    """Загрузка данных не удалась окончательно (попытки кончились) — запись в колокольчике, причина словами."""
    payload = job.get("payload") or {}
    user_id = int(job["user_id"])
    what, key = "Подключение к Яндексу", f"connection:{payload.get('connection_id')}"
    with pool.connection() as conn:
        if job["kind"] == "load_account":
            row = conn.execute(
                "SELECT client_login FROM direct_accounts WHERE id = %s AND user_id = %s", (payload.get("account_id"), user_id)
            ).fetchone()
            what, key = f"Кабинет Директа {(row or {}).get('client_login') or ''}".strip(), f"account:{payload.get('account_id')}"
        elif job["kind"] == "load_counter":
            what, key = "Счётчик Метрики", f"counter:{payload.get('counter_row_id')}"
    notify(
        user_id,
        "load_error",
        f"Не удалось загрузить данные: {what}",
        body=f"Причина: {plain_error(error_code, error)[:300]}. Попробуем снова по расписанию.",
        link="/cabinet/connections",
        severity="error",
        dedup_key=f"load:{key}",
    )


def _sync_workspace_queries(user_id: int, payload: dict, progress: Progress) -> dict:
    workspace_id = _int(payload, "workspace_id")
    result = sync_workspace_queries(user_id, {**payload, "workspace_id": workspace_id}, progress)
    return {**result, "rules": label_after_load(user_id, workspace_ids=[workspace_id])}


def _classify_queries(user_id: int, payload: dict, progress: Progress) -> dict:
    return classify_queries(user_id, {**payload, "workspace_id": _int(payload, "workspace_id")}, progress)


def _classify_all_queries(user_id: int, payload: dict, progress: Progress) -> dict:
    return classify_all_queries(user_id, payload, progress)


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


def _report_comment(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.reports import ai_comment

    return ai_comment(user_id, {**payload, "workspace_id": _int(payload, "workspace_id")}, progress)


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
    from app.undo import run_undo, run_undo_group

    # log_ids — несколько записей разом: «Вернуть как было» у предложения ИИ или «Отменить» у задачи.
    if payload.get("log_ids"):
        if not payload.get("telegram"):
            return run_undo_group(user_id, payload["log_ids"], progress)
        # «Да, вернуть» из Telegram: итог — в том же сообщении бота.
        from app.plain_errors import plain_error
        from app.telegram_decisions import undo_finished

        try:
            result = run_undo_group(user_id, payload["log_ids"], progress)
        except YandexError as exc:
            undo_finished(payload["telegram"], error=plain_error(exc.code, exc.message))
            raise
        except Exception:
            # Иначе в сообщении навсегда осталось бы «Возвращаю как было…».
            undo_finished(payload["telegram"], error="сбой при откате — проверьте в «Истории»")
            raise
        undo_finished(payload["telegram"], result)
        return result
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


def _fix_links(user_id: int, payload: dict, progress: Progress) -> dict:
    from app.links import fix_links

    with acting_as(str(payload.get("source") or "user")):
        return fix_links(user_id, [int(i) for i in payload.get("campaign_ids") or []], progress)


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
    "classify_all_queries": _classify_all_queries,
    "apply_query_decisions": _apply_query_decisions,
    "brief_draft": _brief_draft,
    "report_comment": _report_comment,
    "undo_action": _undo_action,
    "bulk_budget": _bulk_budget,
    "slice_report": _slice_report,
    "slice_adjust": _slice_adjust,
    "exclude_placements": _exclude_placements,
    "placements_ai": _placements_ai,
    "ad_report": _ad_report,
    "ads_ai": _ads_ai,
    "ads_ab": _ads_ab,
    "fix_links": _fix_links,
    "plan_reminder": _plan_reminder,
    "notify_signals": _notify_signals,
    "urgent_check": _urgent_check,
    "compute_effects": _compute_effects,
    "ai_scheduled_review": _ai_scheduled_review,
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
        with collect_undoable() as undoable, collect_logged() as logged:
            result = handler(job["user_id"], job.get("payload") or {}, progress)
    except YandexError as exc:
        status = fail(job, worker_id, error_code=exc.code, error=exc.message or "")
        log.warning("job %s %s: %s → %s", job["id"], job["kind"], exc.code, status)
        if status == "error" and job["kind"] in LOAD_KINDS:
            _notify_load_failed(job, exc.code, exc.message or "")
        elif status == "error" and is_operation(job):
            notify_failed(job, exc.code, exc.message or "", logged)
        return status
    except Exception as exc:
        log.exception("job %s %s", job["id"], job["kind"])
        status = fail(job, worker_id, error_code="internal", error=str(exc)[:300])
        if status == "error" and job["kind"] in LOAD_KINDS:
            _notify_load_failed(job, "internal")
        elif status == "error" and is_operation(job):
            notify_failed(job, "internal", "", logged)
        return status
    finally:
        done.set()
    undo_log_id = single_undoable(undoable)
    if undo_log_id is not None and isinstance(result, dict):
        # Кабинет покажет «Отменить» в сообщении о результате.
        result = {**result, "undo_log_id": undo_log_id}
    elif 1 < len(set(undoable)) <= 50 and isinstance(result, dict):
        # Несколько записей с откатом — «Отменить» вернёт их все разом (окно покажет каждую словами;
        # 50 — предел группового отката undo.MAX_GROUP).
        result = {**result, "undo_log_ids": sorted(set(undoable))}
    complete(job["id"], worker_id, result)
    if is_operation(job):
        # Частичный успех — в колокольчик; полный — гасит прежнюю запись о сбое того же объекта.
        notify_result(job, result, logged)
    return "done"
