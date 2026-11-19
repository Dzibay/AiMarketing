"""Колокольчик: лента уведомлений кабинета и рассылка того же в Telegram.

Всё, о чём стоит сообщить пользователю, проходит через одну функцию notify(): она смотрит настройки
«событие × куда» (users.notify_prefs), пишет запись в ленту и, если включено, шлёт сообщение в Telegram.

Повторы склеиваются по dedup_key:
- непрочитанная запись с тем же ключом обновляется и поднимается наверх — вторая не появляется;
- прочитанная не повторяется, пока не прошло окно события (KINDS[kind]["window_days"]): один сигнал —
  одна запись в неделю. Окно 0 — каждое новое событие после прочтения даёт новую запись (ответ ИИ).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from psycopg.types.json import Jsonb

from app.clock import now_msk
from app.db import pool

log = logging.getLogger(__name__)

# Порядок — порядок строк в настройках. cabinet/telegram — включено ли по умолчанию.
KINDS: dict[str, dict] = {
    # Срочное (urgent.py): раз в сутки на объект, ночью Telegram ждёт 9:00.
    "urgent": {
        "label": "Срочное: кончаются деньги, кампания отклонена или встала, расход без заявок, итог модерации",
        "cabinet": True,
        "telegram": True,
        "window_days": 1,
    },
    "ai_answer": {
        "label": "ИИ ответил в закрытом чате",
        "cabinet": True,
        "telegram": False,
        "window_days": 0,
    },
    "signal": {
        "label": "Новый сигнал по рекламе: затухание показов, кампании без показов",
        "cabinet": True,
        "telegram": False,
        "window_days": 7,
    },
    "rule": {
        "label": "Сработало правило автопилота",
        "cabinet": True,
        "telegram": True,
        "window_days": 0,
    },
    "load_error": {
        "label": "Не удалось загрузить данные из Директа или Метрики",
        "cabinet": True,
        "telegram": True,
        "window_days": 1,
    },
    # Долгая операция, запущенная человеком, упала или прошла частично (operation_notify.py).
    "operation": {
        "label": "Не получилось или получилось частично: минус-фразы, бюджеты, копирование и другие операции",
        "cabinet": True,
        "telegram": True,
        "window_days": 0,
    },
    # Итог правки через 7 и 14 дней (effects.py): стало хуже — одна запись на правку, с «Вернуть как было».
    "effect": {
        "label": "После правки стало хуже: итог через 7 и 14 дней",
        "cabinet": True,
        "telegram": False,
        "window_days": 3650,
    },
    # Разбор недели по расписанию (ai_review.py): одна сводка по всем проектам запуска.
    "ai_review": {
        "label": "Разбор недели по расписанию: что нашёл ИИ",
        "cabinet": True,
        "telegram": True,
        "window_days": 0,
    },
    "limit": {
        "label": "Лимит ИИ израсходован на 80 % и на 100 %",
        "cabinet": True,
        "telegram": True,
        "window_days": 45,
    },
    "plan": {
        "label": "Тариф заканчивается",
        "cabinet": True,
        "telegram": True,
        "window_days": 30,
    },
    "support": {
        "label": "Поддержка ответила на ваше сообщение",
        "cabinet": True,
        "telegram": True,
        "window_days": 0,
    },
}
SEVERITIES = ("info", "warn", "error", "ok")
KEEP_DAYS = 90
LIST_LIMIT = 50


class NotificationsError(Exception):
    pass


def _bool(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def prefs_from(raw) -> dict[str, dict[str, bool]]:
    """Настройки с подставленными значениями по умолчанию: {kind: {cabinet, telegram}}."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for kind, spec in KINDS.items():
        item = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        out[kind] = {
            "cabinet": _bool(item.get("cabinet"), spec["cabinet"]),
            "telegram": _bool(item.get("telegram"), spec["telegram"]),
        }
    return out


def get_prefs(user_id: int) -> dict:
    """Для настроек: строки «событие × куда» и подключён ли Telegram."""
    from app.telegram_notify import telegram_configured

    with pool.connection() as conn:
        row = conn.execute(
            "SELECT notify_prefs, telegram_chat_id, telegram_decide FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    prefs = prefs_from((row or {}).get("notify_prefs"))
    return {
        "events": [{"kind": k, "label": KINDS[k]["label"], **prefs[k]} for k in KINDS],
        "telegram_available": telegram_configured(),
        "telegram_linked": bool((row or {}).get("telegram_chat_id")),
        # «Решать из Telegram»: кнопки «Выполнить / Отклонить» у предложений ИИ (telegram_decisions.py).
        "telegram_decide": bool((row or {}).get("telegram_decide")),
    }


def set_prefs(user_id: int, changes: dict) -> dict:
    """changes: {kind: {cabinet?: bool, telegram?: bool}} — сливается с сохранённым."""
    if not isinstance(changes, dict) or not changes:
        raise NotificationsError("Нечего сохранять")
    changes = dict(changes)
    decide = changes.pop("telegram_decide", None)  # «Решать из Telegram» — отдельный флаг, не событие
    if decide is not None and not isinstance(decide, bool):
        raise NotificationsError("Неверная настройка")
    with pool.connection() as conn:
        if decide is not None:
            conn.execute("UPDATE users SET telegram_decide = %s WHERE id = %s", (decide, user_id))
        row = conn.execute("SELECT notify_prefs FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        saved = dict((row or {}).get("notify_prefs") or {})
        for kind, item in changes.items():
            if kind not in KINDS or not isinstance(item, dict) or not item:
                raise NotificationsError("Неизвестное событие")
            merged = dict(saved.get(kind) or {})
            for where, value in item.items():
                if where not in ("cabinet", "telegram") or not isinstance(value, bool):
                    raise NotificationsError("Неверная настройка")
                merged[where] = value
            saved[kind] = merged
        conn.execute("UPDATE users SET notify_prefs = %s WHERE id = %s", (Jsonb(saved), user_id))
    return get_prefs(user_id)


def _telegram_text(title: str, body: str, link: str) -> str:
    from app.settings_store import public_base_url

    parts = [title]
    if body:
        parts.append(body)
    if link:
        parts.append(f"{public_base_url().rstrip('/')}{link}")
    return "\n".join(parts)


def notify(
    user_id: int,
    kind: str,
    title: str,
    *,
    body: str = "",
    link: str = "",
    workspace_id: int | None = None,
    severity: str = "info",
    dedup_key: str = "",
    telegram_text: str | None = None,
    telegram_later: bool = False,
    telegram_markup: dict | None = None,
    telegram_off: bool = False,
) -> int | None:
    """Сообщить о событии: запись в ленте и/или Telegram — по настройкам пользователя.

    Возвращает id записи (новой или обновлённой) или None, если в ленту не писали. Сбой не роняет
    вызывающий код: уведомление — побочный эффект.
    telegram_later — тихие часы: запись в ленте сразу, а текст для Telegram ждёт в telegram_pending
    (отправит flush_telegram утром, если событие ещё актуально).
    telegram_markup — кнопки под сообщением Telegram (утром после тихих часов уходит только текст);
    telegram_off — в Telegram не слать: о том же уже сообщили отдельным сообщением (карточки решений).
    """
    try:
        return _notify(
            user_id, kind, title, body, link, workspace_id, severity, dedup_key, telegram_text, telegram_later,
            telegram_markup, telegram_off,
        )
    except Exception:  # noqa: BLE001
        log.exception("Уведомление %s пользователю %s", kind, user_id)
        return None


def _notify(
    user_id, kind, title, body, link, workspace_id, severity, dedup_key, telegram_text, telegram_later=False,
    telegram_markup=None, telegram_off=False,
) -> int | None:
    spec = KINDS.get(kind)
    if spec is None:
        raise ValueError(f"неизвестное уведомление: {kind}")
    severity = severity if severity in SEVERITIES else "info"
    title, body = title.strip()[:200], body.strip()[:1000]
    now = now_msk()
    with pool.connection() as conn:
        user = conn.execute("SELECT notify_prefs, telegram_chat_id FROM users WHERE id = %s", (user_id,)).fetchone()
        if user is None:
            return None
        prefs = dict(prefs_from(user["notify_prefs"])[kind])
        if telegram_off:
            prefs["telegram"] = False
        row_id, fresh = None, True
        if dedup_key:
            last = conn.execute(
                """
                SELECT id, created_at, read_at FROM notifications
                WHERE user_id = %s AND dedup_key = %s ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (user_id, dedup_key),
            ).fetchone()
            if last and last["read_at"] is None:
                # Ещё не видели — обновляем ту же запись: новое событие того же рода, вторая строка не нужна.
                row_id, fresh = int(last["id"]), spec["window_days"] == 0
                conn.execute(
                    """
                    UPDATE notifications SET title = %s, body = %s, link = %s, severity = %s, workspace_id = %s,
                           created_at = %s
                    WHERE id = %s
                    """,
                    (title, body, link, severity, workspace_id, now, row_id),
                )
            elif last and last["created_at"] > now - timedelta(days=spec["window_days"]):
                return None  # уже сообщали в этом окне
        if row_id is None and prefs["cabinet"]:
            row_id = int(
                conn.execute(
                    """
                    INSERT INTO notifications (user_id, workspace_id, kind, severity, title, body, link, dedup_key,
                                               created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                    """,
                    (user_id, workspace_id, kind, severity, title, body, link, dedup_key, now),
                ).fetchone()["id"]
            )
        elif row_id is None and dedup_key:
            # В кабинет не пишем, но повторы в Telegram тоже склеиваем: скрытая строка, сразу прочитанная.
            hidden_id = conn.execute(
                """
                INSERT INTO notifications (user_id, workspace_id, kind, severity, title, body, link, dedup_key,
                                           created_at, read_at, in_cabinet)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE) RETURNING id
                """,
                (user_id, workspace_id, kind, severity, title, body, link, dedup_key, now, now),
            ).fetchone()["id"]
            if telegram_later and fresh and prefs["telegram"]:
                row_id = int(hidden_id)  # только чтобы отложить Telegram; наружу не отдаём
        chat_id = user["telegram_chat_id"] or ""
        if telegram_later and fresh and prefs["telegram"] and row_id is not None:
            # Тихие часы: текст ждёт утра в самой записи.
            conn.execute(
                "UPDATE notifications SET telegram_pending = %s WHERE id = %s",
                (telegram_text or _telegram_text(title, body, link), row_id),
            )
            fresh = False
    if fresh and prefs["telegram"] and chat_id:
        from app.telegram_notify import send_message

        text = telegram_text or _telegram_text(title, body, link)
        if telegram_markup:
            send_message(chat_id, text, telegram_markup)
        else:
            send_message(chat_id, text)
    return row_id if prefs["cabinet"] else None


def _out(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "severity": row["severity"],
        "title": row["title"],
        "body": row["body"],
        "link": row["link"],
        "workspace_id": row["workspace_id"],
        "created_at": row["created_at"].isoformat(),
        "read": row["read_at"] is not None,
        # «Повторить для оставшихся» / «Повторить» у сбоя операции: {job_id, label}; None — кнопки нет.
        "retry": {"label": row["retry"]["label"]} if isinstance(row.get("retry"), dict) else None,
        # «Вернуть как было» у записи «После правки стало хуже»: окно отката по записи журнала.
        # Правку уже вернули — кнопки нет.
        "undo": (
            {"log_id": int(row["undo_log_id"]), "label": "Вернуть как было"}
            if row.get("undo_log_id") and not row.get("undo_done")
            else None
        ),
    }


def unread_count(user_id: int) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id = %s AND read_at IS NULL", (user_id,)
        ).fetchone()
    return int(row["n"])


def list_notifications(user_id: int, limit: int = LIST_LIMIT) -> dict:
    """Лента: последние записи кабинета, новые сверху."""
    limit = max(1, min(int(limit or LIST_LIMIT), 200))
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT n.*,
                   EXISTS (SELECT 1 FROM action_logs l WHERE l.id = n.undo_log_id AND l.undone_at IS NOT NULL)
                       AS undo_done
            FROM notifications n WHERE n.user_id = %s AND n.in_cabinet
            ORDER BY n.created_at DESC, n.id DESC LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return {"items": [_out(r) for r in rows], "unread": unread_count(user_id)}


def mark_read(user_id: int, *, ids: list[int] | None = None, link: str = "", everything: bool = False) -> dict:
    """Прочитано: выбранные записи, все записи, ведущие на страницу link (открыли чат), или вся лента."""
    if not everything and not ids and not link:
        raise NotificationsError("Не указано, что отметить")
    where, params = ["user_id = %s", "read_at IS NULL"], [user_id]
    if not everything:
        if ids:
            where.append("id = ANY(%s)")
            params.append([int(i) for i in ids][:500])
        else:
            where.append("link = %s")
            params.append(link[:500])
    with pool.connection() as conn:
        n = conn.execute(f"UPDATE notifications SET read_at = NOW() WHERE {' AND '.join(where)}", params).rowcount
    return {"marked": n, "unread": unread_count(user_id)}


def purge(days: int = KEEP_DAYS) -> int:
    """Записи старше 90 дней — из планировщика."""
    with pool.connection() as conn:
        return conn.execute(
            "DELETE FROM notifications WHERE created_at < NOW() - make_interval(days => %s)", (days,)
        ).rowcount


def badges(user_id: int) -> dict:
    """Счётчики шапки одним запросом: решения, уведомления, непрочитанные ответы поддержки.

    projects — отметки проектов в меню и переключателе: [{id, pending, urgent: [«деньги на исходе», …]}]
    только у проектов, где что-то есть. Счётчик шапки при выбранном проекте берёт pending своего проекта.
    """
    from app import support
    from app.ai_agent import pending_by_project

    pending = pending_by_project(user_id)
    try:
        from app.urgent import project_signals

        signals = project_signals(user_id)
    except Exception:  # noqa: BLE001 — без красных точек шапка всё равно нужна
        log.exception("Срочные отметки проектов, пользователь %s", user_id)
        signals = {}
    ids = sorted(set(pending["projects"]) | set(signals))
    return {
        "pending": pending["count"],
        "notifications": unread_count(user_id),
        "support": support.unread_count(user_id),
        "projects": [
            {"id": ws_id, "pending": pending["projects"].get(ws_id, 0), "urgent": signals.get(ws_id, [])}
            for ws_id in ids
        ],
    }


# --- Источники ---------------------------------------------------------------------------------------------

SIGNAL_KINDS = ("login_decay", "decay", "idle")


def notify_signals(user_id: int) -> int:
    """Новые сигналы по проектам пользователя: затухание, логин просел, включены без показов. Раз в неделю на сигнал."""
    from app.insights import get_signals
    from app.workspaces import list_workspaces

    covered = _urgent_covered(user_id)
    sent = 0
    for ws in list_workspaces(user_id):
        try:
            signals = get_signals(user_id, int(ws["id"]))
        except Exception:  # noqa: BLE001
            log.exception("Сигналы проекта %s", ws["id"])
            continue
        for s in signals.get("suggestions") or []:
            if s["kind"] not in SIGNAL_KINDS:
                continue
            # Причину уже назвало срочное уведомление (деньги кончились, кампания отклонена или встала) —
            # второй раз о том же не пишем.
            if s.get("account_id") and s["account_id"] in covered["accounts"]:
                continue
            ids = s.get("campaign_row_ids") or []
            if ids and all(i in covered["campaigns"] for i in ids):
                continue
            key = f"signal:{ws['id']}:{s['kind']}:{s.get('account_id') or ''}"
            row = notify(
                user_id,
                "signal",
                f"{ws['name']}: {s['title']}",
                body=s.get("detail") or "",
                link=f"/w/{ws['id']}/chat",
                workspace_id=int(ws["id"]),
                severity="warn",
                dedup_key=key,
            )
            sent += row is not None
    return sent


def _urgent_covered(user_id: int) -> dict:
    """Что уже покрывают срочные уведомления — чтобы сигналы о затухании не повторяли их."""
    try:
        from app.urgent import covered

        return covered(user_id)
    except Exception:  # noqa: BLE001
        log.exception("Срочное: что уже покрыто, пользователь %s", user_id)
        return {"accounts": set(), "campaigns": set()}


def flush_telegram(user_id: int, still_relevant, now=None) -> int:
    """Утро после тихих часов: отложенные сообщения Telegram.

    Каждую отложенную запись забираем (telegram_pending очищается сразу — второй воркер её не пошлёт) и
    отправляем, только если событие ещё актуально (still_relevant(dedup_key)), запись моложе суток, Telegram
    для этого события не выключили и её ещё не прочитали в кабинете. Возвращает число отправленных.
    """
    now = now or now_msk()
    with pool.connection() as conn:
        user = conn.execute("SELECT notify_prefs, telegram_chat_id FROM users WHERE id = %s", (user_id,)).fetchone()
        rows = conn.execute(
            """
            WITH t AS (
                SELECT id, telegram_pending AS text FROM notifications
                WHERE user_id = %s AND telegram_pending <> '' FOR UPDATE
            )
            UPDATE notifications n SET telegram_pending = '' FROM t WHERE n.id = t.id
            RETURNING n.id, n.kind, n.dedup_key, n.created_at, n.read_at, n.in_cabinet, t.text
            """,
            (user_id,),
        ).fetchall()
    if not rows or user is None:
        return 0
    prefs = prefs_from(user["notify_prefs"])
    chat_id = user["telegram_chat_id"] or ""
    sent = 0
    for r in sorted(rows, key=lambda r: (r["created_at"], r["id"])):
        if not chat_id or not prefs.get(r["kind"], {}).get("telegram"):
            continue
        if r["created_at"] < now - timedelta(days=1) or (r["in_cabinet"] and r["read_at"] is not None):
            continue
        if not still_relevant(r["dedup_key"]):
            continue
        from app.telegram_notify import send_message

        send_message(chat_id, r["text"])
        sent += 1
    return sent


def notify_limit(user_id: int) -> None:
    """После списания: перешли 80 % или 100 % лимита ИИ — одна запись на порог за период."""
    from app import billing, plans

    sub = plans.subscription(user_id)
    limit = sub["units_limit"]
    if not limit:
        return
    spent = billing.units_spent(user_id)
    pct = spent * 100 / limit
    if pct < 80:
        return
    step = 100 if pct >= 100 else 80
    period = sub["period_start"].date().isoformat()
    title = "Лимит ИИ израсходован" if step == 100 else "Израсходовано 80 % лимита ИИ"
    body = (
        "ИИ не сможет отвечать до продления или следующего периода."
        if step == 100
        else "Осталась пятая часть. Следите за расходом или смените тариф."
    )
    notify(
        user_id,
        "limit",
        title,
        body=body,
        link="/cabinet/plan",
        severity="error" if step == 100 else "warn",
        dedup_key=f"limit:{period}:{step}",
    )
