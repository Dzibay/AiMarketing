"""Фоновые потоки: исполнители очереди задач, планировщик периодических синков, Telegram.

Периодические задачи ставятся только тем, у кого действует подписка (и подтверждена почта, если это
включено): у остальных данные хранятся, но не обновляются; после продления — догрузка (plans.resume).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from collections.abc import Callable

from app.config import settings
from app.digest import users_due_for_digest
from app.job_handlers import run_job, submit
from app.jobs import claim, claim_schedule, purge_finished, requeue_stale
from app.metrika_sync import canonical_counters
from app.plans import active_user_ids, has_feature
from app.rules import list_users_with_rules
from app.stats import list_user_ids_with_accounts
from app.telegram_notify import poll_updates_once, telegram_configured
from app.worker_lock import try_worker_lock
from app.yandex import list_all_connection_ids, list_enabled_direct_account_ids

log = logging.getLogger("app.workers")

IDLE_POLL_SECONDS = 2.0
SCHEDULER_TICK_SECONDS = 30.0

_stop = threading.Event()
_threads: list[threading.Thread] = []


def _enqueue_connections() -> None:
    active = active_user_ids()
    for connection_id, user_id in list_all_connection_ids():
        if user_id in active:
            submit("sync_connection", user_id, {"connection_id": connection_id})


def _enqueue_accounts() -> None:
    """Актуализация кабинетов: задача сама решает, что пора перечитать (account_load)."""
    active = active_user_ids()
    for account_id, user_id in list_enabled_direct_account_ids():
        if user_id in active:
            submit("load_account", user_id, {"account_id": account_id})


def _enqueue_counters() -> None:
    active = active_user_ids()
    for counter in canonical_counters():
        if int(counter["user_id"]) in active:
            submit("load_counter", int(counter["user_id"]), {"counter_row_id": int(counter["id"])})


def _enqueue_today() -> None:
    active = active_user_ids()
    for user_id in list_user_ids_with_accounts():
        if user_id in active:
            submit("stats_today", user_id)


def _enqueue_digests() -> None:
    active = active_user_ids()
    for user_id, day in users_due_for_digest():
        if user_id in active and has_feature(user_id, "digest"):
            submit("send_digest", user_id, {"day": day.isoformat()})


def _enqueue_rules() -> None:
    active = active_user_ids()
    for user_id in list_users_with_rules():
        if user_id in active and has_feature(user_id, "autopilot"):
            submit("run_rules", user_id)


def _enqueue_reminders() -> None:
    from app.reminders import due

    for item in due():
        submit("plan_reminder", item["user_id"], {"ends_at": item["ends_at"], "days": item["days"]})


def _sync_payments() -> None:
    """Сверка с ЮKassa: на случай, если уведомление не дошло, и чтобы подтянуть возвраты."""
    from app.payments import sync_open

    result = sync_open()
    if result["checked"] or result["errors"]:
        log.info("Сверка платежей: %s", result)


def _purge_jobs() -> None:
    n = purge_finished()
    if n:
        log.info("Удалено старых задач: %d", n)


# Имя → (период в секундах, постановка задач). Время последнего запуска — в job_schedules,
# поэтому рестарт процесса не сбрасывает расписание, а несколько воркеров не дублируют запуск.
SCHEDULES: dict[str, tuple[int, Callable[[], None]]] = {
    "connections": (3600, _enqueue_connections),
    # Кампании, структура по Changes API, статистика за неделю, запросы — каждая по своей давности.
    "accounts": (3600, _enqueue_accounts),
    "counters": (3600, _enqueue_counters),
    "today": (3600, _enqueue_today),
    # Часто, но дёшево: один запрос; дайджест уходит в течение 10 минут после часа пользователя.
    "digest": (600, _enqueue_digests),
    "rules": (3600, _enqueue_rules),
    "purge_jobs": (86400, _purge_jobs),
    "plan_reminders": (3600, _enqueue_reminders),
    "payments": (600, _sync_payments),
}


def start_workers() -> None:
    _stop.clear()
    _threads.clear()
    base_id = f"{socket.gethostname()}:{os.getpid()}"
    targets: list[tuple[str, Callable[..., None], tuple]] = [("job-scheduler", _scheduler_loop, ())]
    for i in range(max(1, settings.worker_concurrency)):
        targets.append((f"job-runner-{i}", _runner_loop, (f"{base_id}:{i}",)))
    if telegram_configured():
        targets.append(("telegram-poll", _telegram_loop, ()))
    for name, target, args in targets:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        _threads.append(thread)


def stop_workers(timeout: float = 10.0) -> None:
    """Просит потоки остановиться и ждёт, пока исполнители доделают текущие задачи."""
    _stop.set()
    for thread in _threads:
        thread.join(timeout=timeout)
        if thread.is_alive():
            log.warning("Поток %s не остановился за %.0f с", thread.name, timeout)


def _scheduler_loop() -> None:
    _stop.wait(15)
    while not _stop.is_set():
        try:
            stale = requeue_stale()
            if stale:
                log.warning("Зависших задач возвращено в очередь: %d", stale)
        except Exception:
            log.exception("requeue stale")
        for name, (interval, fan_out) in SCHEDULES.items():
            if _stop.is_set():
                break
            try:
                if claim_schedule(name, interval):
                    log.info("Расписание %s: ставлю задачи", name)
                    fan_out()
            except Exception:
                log.exception("расписание %s", name)
        _stop.wait(SCHEDULER_TICK_SECONDS)


def _runner_loop(worker_id: str) -> None:
    heavy_limit = max(1, settings.worker_concurrency - 1)
    while not _stop.is_set():
        try:
            job = claim(worker_id, heavy_limit=heavy_limit)
        except Exception:
            log.exception("claim job")
            _stop.wait(5)
            continue
        if job is None:
            _stop.wait(IDLE_POLL_SECONDS)
            continue
        try:
            run_job(job, worker_id)
        except Exception:
            # run_job сам пишет ошибки задачи; сюда попадает только сбой БД при записи статуса.
            log.exception("run job %s", job["id"])


def _telegram_loop() -> None:
    _stop.wait(15)
    while not _stop.is_set():
        try:
            with try_worker_lock("telegram") as locked:
                if locked:
                    poll_updates_once()
        except Exception:
            log.exception("telegram loop")
        _stop.wait(25)
