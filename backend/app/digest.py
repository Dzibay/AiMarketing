"""Утренний дайджест в Telegram: вчерашний расход по сайтам и «где горит»."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.clock import now_msk
from app.db import pool
from app.direct_balance import sync_balances_for_user
from app.overview import build_overview, rub
from app.settings_store import public_base_url
from app.stats import sync_stats_for_user
from app.telegram_notify import notify_user
from app.yandex import YandexError

log = logging.getLogger("app.digest")

MAX_PROBLEMS = 10
MAX_WORKSPACES = 15
# Если воркер лежал, дайджест догоняет ещё два часа, а не приходит вечером.
SEND_WINDOW_HOURS = 3
_ICONS = {"critical": "🔴", "warning": "🟠"}
_KPI_MARKS = {"ok": "✓", "warn": "!", "bad": "‼"}


def format_digest(overview: dict, base_url: str = "") -> str:
    """Текст без разметки: send_message шлёт plain text, обрезка — на 4000 символах."""
    yesterday = date.fromisoformat(overview["yesterday"])
    totals = overview["totals"]
    conversions = totals.get("conversions_yesterday", 0)
    summary = f"Расход {rub(totals.get('spend_yesterday', 0))} · конверсий {conversions}"
    if conversions:
        summary += f" · CPA {rub(totals['spend_yesterday'] / conversions)}"
    lines = [f"AiMarketing · итоги {yesterday:%d.%m}", summary, ""]

    fires = [p for p in overview["problems"] if p["severity"] in _ICONS]
    if fires:
        lines.append(f"Где горит ({len(fires)}):")
        for p in fires[:MAX_PROBLEMS]:
            where = " · ".join(x for x in (p["workspace_name"], p["object_name"]) if x)
            lines.append(f"{_ICONS[p['severity']]} {where + ': ' if where else ''}{p['title']}")
        if len(fires) > MAX_PROBLEMS:
            lines.append(f"…и ещё {len(fires) - MAX_PROBLEMS} — в сводке")
    else:
        lines.append("✅ Ничего не горит")

    rows = sorted(
        (c for c in overview["workspaces"] if c["campaigns"] or c["spend_7d"]),
        key=lambda c: (-c["spend_yesterday"], c["name"].lower()),
    )
    if rows:
        lines += ["", "Сайты за вчера:"]
        for c in rows[:MAX_WORKSPACES]:
            parts = [rub(c["spend_yesterday"])]
            if c["conversions_yesterday"]:
                parts.append(f"{c['conversions_yesterday']} конв.")
            kpi = c["kpi"]
            if kpi["type"] and kpi["value"] is not None:
                label = f"CPA 7 дн. {rub(kpi['value'])}" if kpi["type"] == "cpa" else f"ДРР 7 дн. {kpi['value']:g}%"
                parts.append(f"{label} {_KPI_MARKS.get(kpi['status'], '')}".strip())
            budget = c["budget"]
            if budget and budget["pace_pct"] is not None:
                parts.append(f"темп бюджета {budget['pace_pct']}%")
            if c["days_on_balance"] is not None and c["days_on_balance"] < 14:
                parts.append(f"баланс на {c['days_on_balance']:g} дн.")
            name = f"{c['client_name']} · {c['name']}" if c.get("client_name") else c["name"]
            lines.append(f"• {name} — " + " · ".join(parts))
        if len(rows) > MAX_WORKSPACES:
            lines.append(f"…и ещё {len(rows) - MAX_WORKSPACES} сайтов")

    if base_url:
        lines += ["", f"{base_url}/cabinet/overview"]
    return "\n".join(lines)


def build_digest(user_id: int, today: date | None = None) -> str:
    return format_digest(build_overview(user_id, today), public_base_url() or "")


def send_digest(user_id: int, today: date, *, refresh: bool = True) -> dict:
    """Освежает вчерашнюю статистику и балансы, шлёт дайджест и отмечает день отправленным.

    Сбой синка не отменяет дайджест: лучше вчерашние цифры шестичасовой давности, чем ничего.
    """
    if refresh:
        for name, step in (
            ("stats", lambda: sync_stats_for_user(user_id, today - timedelta(days=1))),
            ("balances", lambda: sync_balances_for_user(user_id)),
        ):
            try:
                step()
            except Exception:
                log.exception("digest %s user %s", name, user_id)
    text = build_digest(user_id, today)
    sent = notify_user(user_id, text)
    # День отмечаем и при неудачной отправке (чат отвязан, бот заблокирован) — иначе
    # планировщик пересобирал бы дайджест каждые 10 минут.
    with pool.connection() as conn:
        conn.execute("UPDATE users SET last_digest_on = %s WHERE id = %s", (today, user_id))
    return {"sent": sent, "day": today.isoformat()}


def users_due_for_digest() -> list[tuple[int, date]]:
    now = now_msk()
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id FROM users u
            WHERE u.digest_enabled AND u.telegram_chat_id <> '' AND u.blocked_at IS NULL
              AND (u.last_digest_on IS NULL OR u.last_digest_on < %s)
              AND %s >= u.digest_hour AND %s < u.digest_hour + %s
              AND EXISTS (SELECT 1 FROM direct_accounts a WHERE a.user_id = u.id AND a.enabled)
            ORDER BY u.id
            """,
            (now.date(), now.hour, now.hour, SEND_WINDOW_HOURS),
        ).fetchall()
    return [(int(r["id"]), now.date()) for r in rows]


def get_digest_settings(user_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT digest_enabled, digest_hour, last_digest_on, telegram_chat_id FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return {
        "enabled": bool(row["digest_enabled"]),
        "hour": int(row["digest_hour"]),
        "last_sent_on": row["last_digest_on"].isoformat() if row["last_digest_on"] else None,
        "telegram_linked": bool(row["telegram_chat_id"]),
    }


def update_digest_settings(user_id: int, *, enabled: bool | None = None, hour: int | None = None) -> dict:
    if hour is not None and not 0 <= hour <= 23:
        raise YandexError("digest-hour", "Час отправки — от 0 до 23")
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET digest_enabled = COALESCE(%s, digest_enabled),
                digest_hour = COALESCE(%s, digest_hour)
            WHERE id = %s
            """,
            (enabled, hour, user_id),
        )
    return get_digest_settings(user_id)
