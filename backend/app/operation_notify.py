"""Сбой или частичный успех долгой операции, запущенной человеком, — запись в колокольчике.

Человек запустил применение списка минус-фраз, массовую смену бюджетов, копирование, разметку запросов и
ушёл со страницы. Если задача упала окончательно или прошла частично, он узнаёт об этом из колокольчика (и из
Telegram — по его настройкам): «Список минус-фраз «Бренд»: применилось к 8 из 10 кампаний. Причина: …».
Запись ведёт на строку «Истории» (если задача её оставила), у части операций — «Повторить для оставшихся»
или «Повторить» (notifications.retry). Загрузки кабинетов сообщают о себе сами (job_handlers._notify_load_failed),
ответ ИИ — своей записью ai_answer.

Операция «человека» — задача с приоритетом PRIORITY_USER: её поставили кнопкой кабинета или одобренным
предложением ИИ. Те же задачи от расписания (фоновый приоритет) молчат.
"""

from __future__ import annotations

import hashlib
import json
import logging

from psycopg.types.json import Jsonb

from app.db import pool
from app.jobs import PRIORITY_USER, public_job
from app.plain_errors import plain_error
from app.yandex import YandexError

log = logging.getLogger(__name__)

NOTIFY_KIND = "operation"
RETRY_REST = "Повторить для оставшихся"
RETRY_AGAIN = "Повторить"

_CAMPAIGNS = ("кампании", "кампаний", "кампаний")  # «из 10 кампаний»
_QUERIES = ("запроса", "запросов", "запросов")

# kind → title: как операция называется в колокольчике; page: куда вести, если записи в «Истории» нет
# ({ws} — проект из задачи); retry: "rest" — частичный успех повторяется для оставшихся, сбой целиком —
# заново; "again" — только сбой целиком, заново (повтор безопасен); None — без кнопки (повтор задвоил бы
# изменения: новые объявления, ключевые фразы, копия кампании, откат).
OPERATIONS: dict[str, dict] = {
    "apply_negative_list": {"title": "Список минус-фраз", "page": "/cabinet/negative-lists", "retry": "rest"},
    "bulk_budget": {"title": "Бюджеты кампаний", "page": "/w/{ws}/campaigns", "retry": "rest"},
    "duplicate_campaign": {"title": "Копирование кампании", "page": "/w/{ws}/campaigns", "retry": None},
    "apply_query_decisions": {"title": "Решения по запросам", "page": "/w/{ws}/queries", "retry": None},
    "classify_queries": {"title": "Разметка запросов", "page": "/w/{ws}/queries", "retry": "again"},
    "classify_all_queries": {"title": "Разметка запросов всех проектов", "page": "/cabinet/queries", "retry": "again"},
    "sync_workspace_queries": {"title": "Загрузка поисковых запросов", "page": "/w/{ws}/queries", "retry": "again"},
    "slice_report": {"title": "Отчёт по срезу", "page": "/w/{ws}/slices", "retry": "again"},
    "slice_adjust": {"title": "Корректировки ставок", "page": "/w/{ws}/slices", "retry": "again"},
    "exclude_placements": {"title": "Запрет площадок", "page": "/w/{ws}/slices", "retry": "again"},
    "placements_ai": {"title": "Разбор площадок", "page": "/w/{ws}/slices", "retry": "again"},
    "ad_report": {"title": "Отчёт по объявлениям", "page": "/w/{ws}/ad-stats", "retry": "again"},
    "ads_ai": {"title": "Новые тексты объявлений", "page": "/w/{ws}/ad-stats", "retry": "again"},
    "ads_ab": {"title": "Новые объявления для A/B", "page": "/w/{ws}/ad-stats", "retry": None},
    "fix_links": {"title": "Ссылки по шаблону сайта", "page": "/w/{ws}/campaigns", "retry": "again"},
    "brief_draft": {"title": "Черновик брифа", "page": "/w/{ws}/settings", "retry": "again"},
    # Без «Повторить»: текст ИИ не сохраняется задачей, его забирает открытая страница — повтор из колокольчика
    # потратил бы лимит впустую. Запись ведёт на отчёт, там кнопка ИИ.
    "report_comment": {"title": "Комментарий к отчёту", "page": "/w/{ws}/report", "retry": None},
    "undo_action": {"title": "Возврат изменений", "page": "/cabinet/history", "retry": None},
    "sync_campaign_structure": {"title": "Обновление кампании", "page": "/cabinet/campaigns", "retry": "again"},
    "sync_search_queries": {"title": "Загрузка поисковых запросов", "page": "/cabinet/campaigns", "retry": "again"},
    "sync_account_campaigns": {"title": "Обновление кампаний кабинета", "page": "/cabinet/connections", "retry": "again"},
    "sync_goals": {"title": "Загрузка целей Метрики", "page": "/cabinet/connections", "retry": "again"},
    "stats_day": {"title": "Загрузка статистики", "page": "/cabinet/analytics", "retry": "again"},
    "stats_range": {"title": "Загрузка статистики", "page": "/cabinet/analytics", "retry": "again"},
}

# Чем операция «привязана» к объекту: склейка повторов в ленте и «починилось» после удачного повтора.
_OBJECT_KEYS = ("list_id", "campaign_row_id", "workspace_id", "account_id", "counter_row_id", "log_id")


def is_operation(job: dict) -> bool:
    """Задача — долгая операция, запущенная человеком (кнопкой или одобрением), а не расписанием."""
    if job.get("kind") not in OPERATIONS or not job.get("user_id"):
        return False
    try:
        return int(job.get("priority") if job.get("priority") is not None else PRIORITY_USER) <= PRIORITY_USER
    except (TypeError, ValueError):
        return False


# Один объект: удачный прогон над ним гасит прежний сбой. workspace_id / account_id — лишь место, внутри
# которого операция трогает свой набор кампаний (campaign_ids, items…): такие склеиваем только с той же задачей.
_SINGLE_OBJECT_KEYS = frozenset({"list_id", "campaign_row_id", "counter_row_id", "log_id"})
# Поля, которые не меняют, над чем работает задача.
_VOLATILE_KEYS = frozenset({"source"})


def _fingerprint(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k not in _VOLATILE_KEYS}
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def dedup_key(kind: str, payload: dict | None) -> str:
    """Ключ склейки записей в ленте: один объект — по нему, иначе — по набору целей задачи.

    Бюджеты кампаний A и B и бюджет кампании C в одном проекте — разные записи: удача с C не гасит сбой с B
    и не затирает его «Повторить для оставшихся».
    """
    payload = payload or {}
    for key in _OBJECT_KEYS:
        if payload.get(key) not in (None, "", []):
            base = f"op:{kind}:{key}={payload[key]}"
            if key in _SINGLE_OBJECT_KEYS:
                return base
            extra = {k: v for k, v in payload.items() if k != key and k not in _VOLATILE_KEYS}
            return f"{base}:{_fingerprint(extra)}" if extra else base
    return f"op:{kind}:{_fingerprint(payload)}" if payload else f"op:{kind}"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


# ---------------------------------------------------------------- разбор результата


def _errors(result: dict) -> list:
    errors = result.get("errors")
    return errors if isinstance(errors, list) else []


def outcome(kind: str, result) -> str:
    """ok / partial / error по результату завершившейся задачи (статус пишут не все операции)."""
    if not isinstance(result, dict):
        return "ok"
    status = result.get("status")
    if status in ("ok", "partial", "error"):
        return status
    items = result.get("items") if isinstance(result.get("items"), list) else []
    bad = [i for i in items if isinstance(i, dict) and i.get("status") in ("error", "partial")]
    if kind == "fix_links" and bad:
        return "error" if all(i.get("status") == "error" for i in items) else "partial"
    return "partial" if _errors(result) and items else "ok"


def counts(kind: str, result: dict) -> tuple[int, int, tuple[str, str, str]] | None:
    """(прошло, всего, слово для «из N …») для частичного успеха; None — посчитать нечего."""
    if not isinstance(result, dict):
        return None
    if kind == "apply_negative_list":
        ok = int(result.get("linked") or 0) + int(result.get("detached") or 0)
        # Ошибка кабинета целиком несёт «campaigns» — сколько его кампаний не дошло (может быть и 0).
        failed = sum(
            int(e.get("campaigns") or 0) if isinstance(e, dict) and "campaigns" in e else 1 for e in _errors(result)
        )
        skipped = len(result.get("skipped") or [])
        return ok, ok + failed + skipped, _CAMPAIGNS
    if kind == "apply_query_decisions":
        ok = int(result.get("negative") or 0) + int(result.get("target") or 0)
        failed = result.get("errors") if isinstance(result.get("errors"), int) else len(_errors(result))
        return ok, ok + int(failed or 0) + int(result.get("skipped") or 0), _QUERIES
    if kind == "fix_links":
        items = [i for i in result.get("items") or [] if isinstance(i, dict)]
        ok = sum(1 for i in items if i.get("status") not in ("error", "partial"))
        return ok, len(items), _CAMPAIGNS
    if kind in ("bulk_budget", "slice_adjust", "exclude_placements") and isinstance(result.get("items"), list):
        ok = len(result["items"])
        return ok, ok + len(_errors(result)), _CAMPAIGNS
    return None


def first_reason(result) -> str:
    """Причина первой ошибки в результате — словами; пусто — причины в результате нет."""
    if not isinstance(result, dict):
        return ""
    candidates = list(_errors(result))
    for item in result.get("items") or []:
        if isinstance(item, dict) and item.get("status") in ("error", "partial"):
            nested = item.get("errors") or item.get("message") or item.get("error")
            candidates.append(nested[0] if isinstance(nested, list) and nested else nested)
    candidates += [w for w in result.get("warnings") or [] if isinstance(w, str)]
    for item in candidates:
        if isinstance(item, dict):
            code = item.get("error") or item.get("code") or item.get("reason") or ""
            if code or item.get("message"):
                return plain_error(code, item.get("message") or "")
        elif isinstance(item, str) and item.strip():
            return plain_error("", item) if " " in item.strip() else plain_error(item, "")
    return ""


def retry_payload(kind: str, payload: dict, result, status: str) -> tuple[dict, str] | None:
    """Что поставить по кнопке «Повторить»: (payload, подпись) или None — кнопки нет."""
    mode = (OPERATIONS.get(kind) or {}).get("retry")
    payload = dict(payload or {})
    if mode is None:
        return None
    if status == "error":
        return payload, RETRY_AGAIN
    if status != "partial" or mode != "rest":
        return None
    if kind == "apply_negative_list":
        # Применение идемпотентно: где список уже стоит, Директ не трогаем — повтор дойдёт до оставшихся.
        return payload, RETRY_REST
    if kind == "bulk_budget":
        # Только упавшие: «+10 %» у прошедших второй раз прибавил бы ещё 10 %.
        failed = []
        for e in _errors(result if isinstance(result, dict) else {}):
            rid = e.get("campaign_row_id") if isinstance(e, dict) else None
            if rid is not None and int(rid) not in failed:
                failed.append(int(rid))
        if not failed:
            return None
        return {**payload, "campaign_ids": failed}, RETRY_REST
    return None


# ---------------------------------------------------------------- запись в колокольчике


def _subject(kind: str, user_id: int, payload: dict) -> str:
    title = OPERATIONS[kind]["title"]
    if kind == "apply_negative_list" and payload.get("list_id"):
        try:
            with pool.connection() as conn:
                row = conn.execute(
                    "SELECT name FROM negative_lists WHERE id = %s AND user_id = %s", (payload["list_id"], user_id)
                ).fetchone()
        except Exception:  # noqa: BLE001 — название в заголовке не стоит сбоя уведомления
            row = None
        if row and row["name"]:
            return f"{title} «{row['name']}»"
    return title


def _link(kind: str, user_id: int, payload: dict, log_ids: list[int]) -> tuple[str, int | None]:
    """Ссылка на строку «Истории» (последняя запись задачи), иначе — на страницу операции. И проект записи."""
    workspace_id = payload.get("workspace_id")
    if log_ids:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT id, workspace_id FROM action_logs WHERE id = %s AND user_id = %s", (max(log_ids), user_id)
            ).fetchone()
        if row is not None:
            ws = row["workspace_id"]
            base = f"/w/{ws}/history" if ws else "/cabinet/history"
            return f"{base}?log={row['id']}", ws or (int(workspace_id) if workspace_id else None)
    page = OPERATIONS[kind]["page"]
    if "{ws}" in page:
        page = page.format(ws=workspace_id) if workspace_id else "/cabinet/campaigns"
    return page, int(workspace_id) if workspace_id else None


def _send(job: dict, title: str, body: str, severity: str, log_ids: list[int], retry: tuple[dict, str] | None) -> None:
    from app.notifications import notify

    kind, user_id = job["kind"], int(job["user_id"])
    payload = job.get("payload") or {}
    link, workspace_id = _link(kind, user_id, payload, log_ids)
    row_id = notify(
        user_id,
        NOTIFY_KIND,
        title,
        body=body,
        link=link,
        workspace_id=workspace_id,
        severity=severity,
        dedup_key=dedup_key(kind, payload),
    )
    if row_id is None:
        return
    with pool.connection() as conn:
        conn.execute(
            "UPDATE notifications SET retry = %s WHERE id = %s",
            (Jsonb({"job_id": int(job["id"]), "label": retry[1]}) if retry else None, row_id),
        )


def notify_failed(job: dict, error_code: str, error: str, log_ids: list[int] | None = None) -> None:
    """Задача упала окончательно (попытки кончились): «Бюджеты кампаний: не получилось. Причина: …»."""
    try:
        kind = job["kind"]
        payload = job.get("payload") or {}
        subject = _subject(kind, int(job["user_id"]), payload)
        reason = plain_error(error_code, error)
        retry = retry_payload(kind, payload, None, "error")
        _send(job, f"{subject}: не получилось", f"Причина: {reason}.", "error", list(log_ids or []), retry)
    except Exception:  # noqa: BLE001 — уведомление не должно менять исход задачи
        log.exception("operation failed notice for job %s", job.get("id"))


def _safe_to_rerun(kind: str, payload: dict) -> bool:
    """Прерванную на середине задачу можно запустить целиком ещё раз, не задвоив уже сделанное."""
    mode = (OPERATIONS.get(kind) or {}).get("retry")
    if mode is None:
        return False
    if kind == "bulk_budget":
        # «+10 %» к уже изменённым кампаниям прибавил бы ещё 10 %; точная сумма — повтор безопасен.
        return payload.get("budget_change_pct") in (None, "") and payload.get("target_change_pct") in (None, "")
    return True


def notify_stale(job: dict) -> None:
    """Воркер умер посреди задачи (перезапуск, нехватка памяти) и попытки кончились: часть изменений могла
    примениться, а итог и запись в «Истории» не появились. Просим проверить результат."""
    try:
        kind = job["kind"]
        payload = job.get("payload") or {}
        subject = _subject(kind, int(job["user_id"]), payload)
        retry = (payload, RETRY_AGAIN) if _safe_to_rerun(kind, payload) else None
        body = (
            "Причина: задача прервалась на полпути. Часть изменений могла примениться — "
            "проверьте результат, прежде чем запускать заново."
        )
        _send(job, f"{subject}: прервалось", body, "error", [], retry)
    except Exception:  # noqa: BLE001
        log.exception("operation stale notice for job %s", job.get("id"))


def notify_result(job: dict, result, log_ids: list[int] | None = None) -> None:
    """Задача завершилась: частично — запись в колокольчике; целиком — гасим прежнюю запись о сбое этого объекта."""
    try:
        kind = job["kind"]
        payload = job.get("payload") or {}
        status = outcome(kind, result)
        if status == "ok":
            resolve(int(job["user_id"]), kind, payload)
            return
        subject = _subject(kind, int(job["user_id"]), payload)
        counted = counts(kind, result)
        if status == "partial" and counted and counted[1]:
            ok, total, forms = counted
            title = f"{subject}: применилось к {ok} из {total} {_plural(total, forms)}"
        elif status == "partial":
            title = f"{subject}: выполнено частично"
        else:
            title = f"{subject}: не получилось"
        reason = first_reason(result)
        body = f"Причина: {reason}." if reason else "Подробности — в «Истории»."
        retry = retry_payload(kind, payload, result, status)
        _send(job, title, body, "warn" if status == "partial" else "error", list(log_ids or []), retry)
    except Exception:  # noqa: BLE001
        log.exception("operation result notice for job %s", job.get("id"))


def resolve(user_id: int, kind: str, payload: dict) -> None:
    """Операция над тем же объектом прошла целиком — непрочитанная запись о её сбое больше не нужна."""
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE notifications SET read_at = NOW(), retry = NULL
            WHERE user_id = %s AND kind = %s AND dedup_key = %s AND read_at IS NULL
            """,
            (user_id, NOTIFY_KIND, dedup_key(kind, payload)),
        )


# ---------------------------------------------------------------- «Повторить»


def retry(user_id: int, notification_id: int) -> dict:
    """Кнопка «Повторить…» в колокольчике: ставит задачу снова (для оставшихся — только упавшие кампании)."""
    from app.job_handlers import submit

    with pool.connection() as conn:
        note = conn.execute(
            "SELECT id, retry FROM notifications WHERE id = %s AND user_id = %s", (notification_id, user_id)
        ).fetchone()
        if note is None or not isinstance(note["retry"], dict) or not note["retry"].get("job_id"):
            raise YandexError("not-found", "Повторять уже нечего")
        job = conn.execute(
            "SELECT * FROM jobs WHERE id = %s AND user_id = %s", (int(note["retry"]["job_id"]), user_id)
        ).fetchone()
    if job is None:
        raise YandexError("not-found", "Задача устарела — запустите её заново со страницы")
    status = "error" if job["status"] == "error" else outcome(job["kind"], job.get("result"))
    if job.get("error_code") == "stale" and not _safe_to_rerun(job["kind"], job.get("payload") or {}):
        raise YandexError("not-found", "Задача прервалась на середине — проверьте результат и запустите её заново со страницы")
    plan = retry_payload(job["kind"], job.get("payload") or {}, job.get("result"), status)
    if plan is None:
        raise YandexError("not-found", "Повторять уже нечего")
    payload = plan[0]
    if payload.get("workspace_id"):
        from app.workspaces import require_workspace

        # Проект могли закрыть тарифом или удалить, пока запись ждала в ленте.
        require_workspace(user_id, int(payload["workspace_id"]))
    new_job = submit(job["kind"], user_id, payload, priority=PRIORITY_USER, max_attempts=int(job["max_attempts"] or 1))
    with pool.connection() as conn:
        conn.execute(
            "UPDATE notifications SET retry = NULL, read_at = COALESCE(read_at, NOW()) WHERE id = %s",
            (notification_id,),
        )
    return public_job(new_job)
