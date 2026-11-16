"""Уведомления владельцу в Telegram: оплаты и сбои — в группу поддержки, в отдельные темы «Оплаты» и «Сбои».

Каждый вид включается в «Настройки → Уведомления» (app_settings: admin_alerts). Одно и то же правило пишет
не чаще раза в час: время последней отправки — в app_settings (alert_sent:<правило>), отметка атомарная,
поэтому два процесса не отправят дважды. Темы создаются при первой отправке, их id — в app_settings.

Сбои проверяет планировщик (check_all, раз в 5 минут); оплата и сбой ЮKassa пишутся сразу из своего места.
Ошибка Telegram никогда не мешает основному делу — только в лог.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from app.clock import MSK, now_msk
from app.db import pool
from app.settings_store import get_setting, public_base_url, set_setting

log = logging.getLogger("app.admin_alerts")

KINDS = {
    "payment": {"label": "Новая оплата", "topic": "payments", "hint": "Кто, тариф, срок, сумма, первая или повторная."},
    "ai_down": {
        "label": "ИИ не отвечает",
        "topic": "failures",
        "hint": "Несколько вызовов подряд ни одна модель цепочки не ответила.",
    },
    "yookassa": {
        "label": "Сбой ЮKassa",
        "topic": "failures",
        "hint": "ЮKassa не ответила при сверке платежей или уведомление об оплате не обработано.",
    },
    "no_receipt": {"label": "Платёж без чека больше суток", "topic": "failures", "hint": "Оплачен, а чек не отмечен."},
    "stuck_job": {"label": "Задача застряла в очереди", "topic": "failures", "hint": "Ждёт запуска дольше 15 минут."},
    "ai_spend": {
        "label": "Расход на ИИ вырос вдвое",
        "topic": "failures",
        "hint": "Расход за сегодня — больше двух средних дней прошлой недели.",
    },
}
TOPICS = {"payments": "Оплаты", "failures": "Сбои"}
THROTTLE_MINUTES = 60
AI_DOWN_IN_ROW = 3
AI_DOWN_WINDOW_MIN = 30
STUCK_MINUTES = 15
SPEND_MIN_RUB = 50.0  # меньше — не тревожим: копейки удваиваются легко


def admin_link(path: str) -> str:
    base = (public_base_url() or "").rstrip("/")
    return f"{base}/admin-panel/{path.lstrip('/')}" if base else ""


# --- Настройки -----------------------------------------------------------------------------------


def enabled_kinds() -> dict[str, bool]:
    try:
        saved = json.loads(get_setting("admin_alerts", "") or "{}")
    except ValueError:
        saved = {}
    return {k: bool(saved.get(k, True)) for k in KINDS}


def get_settings() -> dict:
    from app import telegram_notify as tg

    on = enabled_kinds()
    return {
        "configured": tg.support_configured(),
        "bot": tg.telegram_configured(),
        "kinds": [{"key": k, "label": d["label"], "hint": d["hint"], "topic": TOPICS[d["topic"]], "enabled": on[k]} for k, d in KINDS.items()],
        "topics": {k: bool(get_setting(f"admin_alert_topic_{k}", "")) for k in TOPICS},
    }


def save_settings(kinds: dict) -> dict:
    current = enabled_kinds()
    for k, v in (kinds or {}).items():
        if k in KINDS:
            current[k] = bool(v)
    set_setting("admin_alerts", json.dumps(current))
    return get_settings()


# --- Отправка ------------------------------------------------------------------------------------


def _claim(rule: str, minutes: int = THROTTLE_MINUTES) -> bool:
    """Отметить отправку правила; False — уже писали меньше часа назад."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (%(k)s, NOW()::text)
            ON CONFLICT (key) DO UPDATE SET value = NOW()::text
            WHERE app_settings.value = '' OR app_settings.value::timestamptz <= NOW() - make_interval(mins => %(m)s)
            RETURNING key
            """,
            {"k": f"alert_sent:{rule}", "m": minutes},
        ).fetchone()
    return row is not None


def _topic(topic: str) -> int | None:
    from app import telegram_notify as tg

    key = f"admin_alert_topic_{topic}"
    saved = get_setting(key, "")
    if saved.isdigit():
        return int(saved)
    created = tg.create_support_topic(TOPICS[topic])
    if created:
        set_setting(key, str(created))
    return created


def send(kind: str, text: str, *, rule: str | None = None, throttle: bool = True) -> bool:
    """Отправить уведомление вида kind в его тему. rule — ключ для «не чаще раза в час» (по умолчанию kind)."""
    from app import telegram_notify as tg

    if kind not in KINDS or not enabled_kinds()[kind] or not tg.support_configured():
        return False
    try:
        if throttle and not _claim(rule or kind):
            return False
        topic = _topic(KINDS[kind]["topic"])
        if not topic:
            return False
        sent = tg.send_to_support_topic(topic, text)
        if not sent:
            # Тему могли удалить в группе — создадим заново при следующей отправке.
            set_setting(f"admin_alert_topic_{KINDS[kind]['topic']}", "")
        return bool(sent)
    except Exception:  # noqa: BLE001
        log.exception("admin alert %s", kind)
        return False


def send_test(kind: str) -> bool:
    label = KINDS.get(kind, {}).get("label", kind)
    return send(kind, f"Толк · проверка уведомления «{label}». Всё настроено.", throttle=False)


# --- Оплаты --------------------------------------------------------------------------------------


def _money(v) -> str:
    v = float(v or 0)
    return f"{v:,.0f} ₽".replace(",", " ") if v == int(v) else f"{v:,.2f} ₽".replace(",", " ")


def payment(pay: dict) -> None:
    """Оплата прошла и применена: кто, тариф, срок, сумма, первая или повторная. Каждая — без ограничения в час."""
    try:
        if pay.get("test"):
            return
        with pool.connection() as conn:
            u = conn.execute("SELECT email FROM users WHERE id = %s", (pay["user_id"],)).fetchone() or {}
            before = conn.execute(
                """
                SELECT COUNT(*) AS n FROM payments
                WHERE user_id = %s AND status = 'succeeded' AND NOT test AND id < %s AND applied_at IS NOT NULL
                """,
                (pay["user_id"], pay["id"]),
            ).fetchone()["n"]
        months = int(pay.get("months") or 1)
        term = {1: "1 месяц", 3: "3 месяца", 12: "12 месяцев"}.get(months, f"{months} мес.")
        lines = [
            f"💳 Оплата {_money(pay['amount_rub'])} — {'повторная' if before else 'первая'}",
            f"{u.get('email') or '#' + str(pay['user_id'])} · тариф {pay.get('plan_name') or '—'} · {term}",
        ]
        if pay.get("last_day"):
            lines.append(f"Подписка до {pay['last_day'].strftime('%d.%m.%Y')}")
        link = admin_link(f"payments?q={pay['id']}")
        if link:
            lines.append(link)
        send("payment", "\n".join(lines), throttle=False)
    except Exception:  # noqa: BLE001
        log.exception("payment alert")


def yookassa_failed(where: str, detail: str = "") -> None:
    text = f"⚠️ ЮKassa: {where}"
    if detail:
        text += f"\n{detail[:500]}"
    link = admin_link("payments")
    if link:
        text += f"\n{link}"
    send("yookassa", text, rule=f"yookassa:{where}")


# --- Проверки по расписанию ---------------------------------------------------------------------


def _check_ai_down(conn, now) -> str | None:
    rows = conn.execute(
        """
        SELECT created_at, task, model, code FROM ai_errors
        WHERE NOT answered AND NOT retried AND NOT fallback AND created_at > %(now)s - make_interval(mins => %(w)s)
        ORDER BY created_at DESC LIMIT %(n)s
        """,
        {"now": now, "w": AI_DOWN_WINDOW_MIN, "n": AI_DOWN_IN_ROW},
    ).fetchall()
    if len(rows) < AI_DOWN_IN_ROW:
        return None
    since = rows[-1]["created_at"]
    ok = conn.execute(
        "SELECT 1 FROM ai_usage WHERE kind = 'model' AND created_at > %s LIMIT 1", (since,)
    ).fetchone()
    if ok:
        return None
    codes = ", ".join(sorted({r["code"] or "?" for r in rows}))
    return (
        f"🔴 ИИ не отвечает: {len(rows)} вызова подряд без ответа ни одной модели за {AI_DOWN_WINDOW_MIN} мин.\n"
        f"Последняя: {rows[0]['model'] or '—'} ({rows[0]['task']}), коды: {codes}\n{admin_link('ai-costs?tab=reliability')}"
    )


def _check_no_receipt(conn, now) -> str | None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, MIN(COALESCE(paid_at, created_at)) AS oldest FROM payments
        WHERE status = 'succeeded' AND NOT test AND NOT receipt_done AND refunded_rub < amount_rub
          AND COALESCE(paid_at, created_at) < %(now)s - INTERVAL '1 day'
        """,
        {"now": now},
    ).fetchone()
    if not row["n"]:
        return None
    oldest = row["oldest"].astimezone(MSK).strftime("%d.%m %H:%M")
    return f"🧾 Платежей без чека больше суток: {row['n']} (самый старый — {oldest})\n{admin_link('payments?no_receipt=1')}"


def _check_stuck(conn, now) -> str | None:
    row = conn.execute(
        """
        SELECT kind, run_after, (SELECT COUNT(*) FROM jobs WHERE status = 'queued'
               AND run_after < %(now)s - make_interval(mins => %(m)s)) AS n
        FROM jobs WHERE status = 'queued' AND run_after < %(now)s - make_interval(mins => %(m)s)
        ORDER BY run_after LIMIT 1
        """,
        {"now": now, "m": STUCK_MINUTES},
    ).fetchone()
    if row is None:
        return None
    lag = int((now - row["run_after"]).total_seconds() // 60)
    return f"⏳ Задачи застряли в очереди: {row['n']}, самая старая ({row['kind']}) ждёт {lag} мин.\n{admin_link('system')}"


def _check_spend(conn, now) -> str | None:
    today = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cost_rub) FILTER (WHERE created_at >= %(t)s), 0) AS today,
               COALESCE(SUM(cost_rub) FILTER (WHERE created_at >= %(w)s AND created_at < %(t)s), 0) / 7 AS avg
        FROM ai_usage WHERE created_at >= %(w)s
        """,
        {"t": today, "w": today - timedelta(days=7)},
    ).fetchone()
    spent, avg = float(row["today"]), float(row["avg"])
    if spent < SPEND_MIN_RUB or avg <= 0 or spent < avg * 2:
        return None
    return (
        f"📈 Расход на ИИ за сегодня {_money(spent)} — в {spent / avg:.1f} раза больше среднего дня прошлой недели "
        f"({_money(avg)})\n{admin_link('ai-costs')}"
    )


CHECKS = {"ai_down": _check_ai_down, "no_receipt": _check_no_receipt, "stuck_job": _check_stuck, "ai_spend": _check_spend}


def check_all(now=None) -> list[str]:
    """Проверить сбои и отправить включённые. Возвращает, что ушло."""
    from app import telegram_notify as tg

    if not tg.support_configured():
        return []
    now = now or now_msk()
    on = enabled_kinds()
    sent = []
    for kind, fn in CHECKS.items():
        if not on[kind]:
            continue
        try:
            with pool.connection() as conn:
                text = fn(conn, now)
            if text and send(kind, text):
                sent.append(kind)
        except Exception:  # noqa: BLE001
            log.exception("alert check %s", kind)
    return sent
