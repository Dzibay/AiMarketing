"""Напоминания об окончании тарифа: за 3 дня и за 1 день — письмом. В интерфейсе то же показывает
плашка кабинета (по last_day из /web/plan).

Каждое напоминание — одна строка plan_reminders (пользователь, конец подписки, за сколько дней): второй
раз не уйдёт. Продлили — конец подписки другой, и следующие напоминания пойдут уже к новому сроку.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app import events, mailer, plans
from app.clock import now_msk
from app.config import settings
from app.db import pool
from app.settings_store import public_base_url

log = logging.getLogger(__name__)

AHEAD_DAYS = 3


def _days_left(ends_at: datetime, now: datetime) -> int:
    """За сколько дней напоминание: остался день или меньше — 1, иначе 3."""
    return 1 if ends_at - now <= timedelta(days=1) else AHEAD_DAYS


def due(now: datetime | None = None) -> list[dict]:
    """Кому пора напомнить: подписка с тарифом кончается в ближайшие 3 дня, напоминания этого шага не было."""
    now = now or now_msk()
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT s.user_id, s.ends_at FROM subscriptions s JOIN users u ON u.id = s.user_id
            WHERE s.plan_id IS NOT NULL AND s.ends_at > %(now)s AND s.ends_at <= %(now)s + make_interval(days => %(ahead)s)
              AND u.blocked_at IS NULL AND u.email IS NOT NULL AND u.email <> ''
            """,
            {"now": now, "ahead": AHEAD_DAYS},
        ).fetchall()
        sent = {
            (int(r["user_id"]), r["ends_at"], int(r["days"]))
            for r in conn.execute(
                "SELECT user_id, ends_at, days FROM plan_reminders WHERE ends_at > %s", (now,)
            ).fetchall()
        }
    out = []
    for r in rows:
        days = _days_left(r["ends_at"], now)
        if (int(r["user_id"]), r["ends_at"], days) not in sent:
            out.append({"user_id": int(r["user_id"]), "ends_at": r["ends_at"].isoformat(), "days": days})
    return out


def _fmt(day: str) -> str:
    return date.fromisoformat(day).strftime("%d.%m.%Y")


def send(user_id: int, ends_at: str, days: int, *, now: datetime | None = None) -> dict:
    """Письмо-напоминание. Подписку за это время продлили — молчим."""
    from app.users import get_user

    now = now or now_msk()
    sub = plans.subscription(user_id, now=now)
    if sub["ends_at"] is None or sub["ends_at"] != datetime.fromisoformat(ends_at) or sub["expired"] or not sub["plan"]:
        return {"sent": False, "reason": "changed"}
    days = 1 if int(days) == 1 else AHEAD_DAYS
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO plan_reminders (user_id, ends_at, days) VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING RETURNING user_id
            """,
            (user_id, sub["ends_at"], days),
        ).fetchone()
    if row is None:
        return {"sent": False, "reason": "already"}
    user = get_user(user_id) or {}
    email = user.get("email") or ""
    last = plans.last_day(sub["ends_at"])
    plan = sub["plan"]
    link = f"{public_base_url()}/cabinet/plan"
    when = "завтра" if days == 1 else f"через {days} дня"
    if plan["is_trial"]:
        subject = f"{settings.app_name}: пробный период закончится {when}"
        lead = f"Пробный период закончится {when} — последний день {_fmt(last)}."
        action = "Выберите тариф, чтобы ИИ-помощник и обновление данных продолжили работать:"
    else:
        subject = f"{settings.app_name}: тариф «{plan['name']}» закончится {when}"
        lead = f"Тариф «{plan['name']}» действует по {_fmt(last)} включительно."
        action = "Продлите тариф, чтобы ИИ-помощник и обновление данных не остановились:"
    body = (
        f"Здравствуйте{', ' + user['name'] if user.get('name') else ''}!\n\n"
        f"{lead}\n{action}\n\n{link}\n\n"
        "После окончания данные сохранятся, но статистика перестанет обновляться, а ИИ — отвечать."
    )
    sent = bool(email) and mailer.send(email, subject, body, link=link)
    if email and not sent and mailer.configured():
        # SMTP сбоит — снимем отметку, попробуем в следующий час.
        with pool.connection() as conn:
            conn.execute(
                "DELETE FROM plan_reminders WHERE user_id = %s AND ends_at = %s AND days = %s",
                (user_id, sub["ends_at"], days),
            )
        return {"sent": False, "reason": "smtp"}
    events.record(user_id, "plan_reminder", days=days, last_day=last, emailed=sent)
    return {"sent": sent}
