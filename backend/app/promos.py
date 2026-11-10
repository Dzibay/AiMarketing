"""Промокоды: единицы к лимиту текущего периода, дни к подписке или тариф на N дней.

Код хранится в верхнем регистре, вводить можно в любом. Срок действия — последний день включительно по
Москве, число активаций — на всех пользователей; один пользователь активирует код один раз. Активация —
под блокировкой строки кода, так что лимит активаций не превысить параллельными запросами.

Единицы добавляются только к текущему периоду лимита (subscriptions.bonus_units/bonus_period_start):
в следующем периоде их нет. Пользователь единиц не видит — ему пишем «+N% к лимиту ИИ».
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from app import events, plans
from app.clock import MSK, now_msk, today_msk
from app.db import pool
from app.yandex import YandexError

log = logging.getLogger(__name__)

KINDS = {"units": "Единицы к лимиту", "days": "Дни к подписке", "plan": "Тариф на N дней"}
CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
NOT_FOUND = "Промокод не найден"


def normalize(code: str) -> str:
    return (code or "").strip().upper()


def _out(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "code": row["code"],
        "kind": row["kind"],
        "units": float(row["units"]) if row.get("units") is not None else None,
        "days": row.get("days"),
        "plan_id": row.get("plan_id"),
        "plan_name": row.get("plan_name") or "",
        "max_uses": row.get("max_uses"),
        "valid_until": row["valid_until"].isoformat() if row.get("valid_until") else None,
        "active": bool(row["active"]),
        "note": row.get("note") or "",
        "uses": int(row.get("uses") or 0),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


_SELECT = """
    SELECT c.*, p.name AS plan_name,
           (SELECT COUNT(*) FROM promo_redemptions r WHERE r.promo_id = c.id) AS uses
    FROM promo_codes c LEFT JOIN plans p ON p.id = c.plan_id
"""


def list_promos() -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(_SELECT + " ORDER BY c.created_at DESC, c.id DESC").fetchall()
    return [_out(r) for r in rows]


def _get(promo_id: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(_SELECT + " WHERE c.id = %s", (promo_id,)).fetchone()
    return _out(row) if row else None


def _int(value, what: str, *, low: int, high: int) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(str(value).replace(",", ".").replace(" ", ""))
    except ValueError as exc:
        raise YandexError("promo-field", f"{what} — число") from exc
    if number != int(number) or not low <= number <= high:
        raise YandexError("promo-field", f"{what} — целое число от {low} до {high}")
    return int(number)


def _clean(data: dict) -> dict:
    code = normalize(data.get("code"))
    if not CODE_RE.match(code):
        raise YandexError("promo-field", "Код — от 3 до 32 символов: латинские буквы, цифры, «-» и «_»")
    kind = data.get("kind")
    if kind not in KINDS:
        raise YandexError("promo-field", "Выберите, что даёт промокод")
    units = days = plan_id = None
    if kind == "units":
        units = _int(data.get("units"), "Единиц", low=1, high=plans.MAX_UNITS)
        if not units:
            raise YandexError("promo-field", "Укажите, сколько единиц добавить к лимиту")
    else:
        days = _int(data.get("days"), "Дней", low=1, high=3660)
        if not days:
            raise YandexError("promo-field", "Укажите число дней")
    if kind == "plan":
        plan_id = _int(data.get("plan_id"), "Тариф", low=1, high=2**31 - 1)
        plan = plans.get_plan(plan_id) if plan_id else None
        if plan is None:
            raise YandexError("promo-field", "Выберите тариф")
    valid_until = data.get("valid_until") or None
    if valid_until:
        try:
            valid_until = date.fromisoformat(str(valid_until))
        except ValueError as exc:
            raise YandexError("promo-field", "Срок действия — дата ГГГГ-ММ-ДД") from exc
    return {
        "code": code,
        "kind": kind,
        "units": units,
        "days": days,
        "plan_id": plan_id,
        "max_uses": _int(data.get("max_uses"), "Число активаций", low=1, high=10_000_000),
        "valid_until": valid_until,
        "active": bool(data.get("active", True)),
        "note": str(data.get("note") or "").strip()[:200],
    }


def save_promo(promo_id: int | None, data: dict) -> dict:
    fields = _clean(data)
    with pool.connection() as conn:
        taken = conn.execute(
            "SELECT 1 FROM promo_codes WHERE code = %s AND id IS DISTINCT FROM %s", (fields["code"], promo_id)
        ).fetchone()
        if taken:
            raise YandexError("exists", "Такой промокод уже есть")
        if promo_id is None:
            row = conn.execute(
                """
                INSERT INTO promo_codes (code, kind, units, days, plan_id, max_uses, valid_until, active, note)
                VALUES (%(code)s, %(kind)s, %(units)s, %(days)s, %(plan_id)s, %(max_uses)s, %(valid_until)s,
                        %(active)s, %(note)s)
                RETURNING id
                """,
                fields,
            ).fetchone()
        else:
            row = conn.execute(
                """
                UPDATE promo_codes SET code = %(code)s, kind = %(kind)s, units = %(units)s, days = %(days)s,
                       plan_id = %(plan_id)s, max_uses = %(max_uses)s, valid_until = %(valid_until)s,
                       active = %(active)s, note = %(note)s
                WHERE id = %(id)s RETURNING id
                """,
                {**fields, "id": promo_id},
            ).fetchone()
    if row is None:
        raise YandexError("not-found", NOT_FOUND)
    return _get(int(row["id"]))


def delete_promo(promo_id: int) -> None:
    """Удаление не отнимает выданное: дни, тариф и единицы у пользователей остаются."""
    with pool.connection() as conn:
        row = conn.execute("DELETE FROM promo_codes WHERE id = %s RETURNING id", (promo_id,)).fetchone()
    if row is None:
        raise YandexError("not-found", NOT_FOUND)


# --- Активация ----------------------------------------------------------------------------------


def _usable(conn, code: str, user_id: int | None, *, lock: bool = False) -> dict:
    code = normalize(code)
    if not CODE_RE.match(code):
        raise YandexError("promo", NOT_FOUND)
    row = conn.execute(
        "SELECT c.*, p.name AS plan_name, p.active AS plan_active FROM promo_codes c "
        "LEFT JOIN plans p ON p.id = c.plan_id WHERE c.code = %s" + (" FOR UPDATE OF c" if lock else ""),
        (code,),
    ).fetchone()
    if row is None or not row["active"]:
        raise YandexError("promo", NOT_FOUND)
    if row["valid_until"] and row["valid_until"] < today_msk():
        raise YandexError("promo", "Срок действия промокода закончился")
    if row["max_uses"]:
        used = conn.execute("SELECT COUNT(*) AS n FROM promo_redemptions WHERE promo_id = %s", (row["id"],)).fetchone()
        if int(used["n"]) >= int(row["max_uses"]):
            raise YandexError("promo", "Промокод уже активировали максимальное число раз")
    if user_id is not None:
        mine = conn.execute(
            "SELECT 1 FROM promo_redemptions WHERE promo_id = %s AND user_id = %s", (row["id"], user_id)
        ).fetchone()
        if mine:
            raise YandexError("promo", "Вы уже активировали этот промокод")
    if row["kind"] == "plan" and not row["plan_name"]:
        raise YandexError("promo", "Тариф промокода больше не доступен")
    return row


def describe(row: dict) -> str:
    if row["kind"] == "units":
        return "дополнительный лимит ИИ на текущий период"
    if row["kind"] == "days":
        return f"+{row['days']} дн. к подписке"
    return f"тариф «{row['plan_name']}» на {row['days']} дн."


def check(code: str) -> dict:
    """Проверка кода до регистрации: действует ли и что даёт."""
    with pool.connection() as conn:
        row = _usable(conn, code, None)
    return {"code": row["code"], "kind": row["kind"], "text": describe(row)}


def _fmt_day(value) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else ""


def redeem(user_id: int, code: str) -> dict:
    """Активировать код. Возвращает {"kind", "message", "last_day"} — что получил пользователь."""
    now = now_msk()
    before = plans.subscription(user_id, now=now)
    was_expired = False
    extra: dict = {}
    with pool.connection() as conn, conn.transaction():
        promo = _usable(conn, code, user_id, lock=True)
        sub = plans.lock_subscription(conn, user_id)
        kind = promo["kind"]
        if kind == "units":
            if before["expired"]:
                raise YandexError("promo", "Подписка закончилась — сначала продлите тариф")
            if before["units_limit"] is None:
                raise YandexError("promo", "Лимит ИИ у вас без ограничений — промокод на единицы не нужен")
            units = float(promo["units"])
            period_start = before["period_start"]
            same = sub["bonus_period_start"] == period_start
            conn.execute(
                "UPDATE subscriptions SET bonus_units = %s, bonus_period_start = %s, updated_at = NOW() WHERE user_id = %s",
                ((float(sub["bonus_units"]) if same else 0.0) + units, period_start, user_id),
            )
            base = before["units_limit"] - before["bonus_units"]
            pct = round(units / base * 100) if base > 0 else None
            until = before["period_end"].astimezone(MSK) - timedelta(microseconds=1)
            message = (f"+{pct}% к лимиту ИИ" if pct else "Лимит ИИ увеличен") + f" — до {until:%d.%m.%Y}"
            extra = {"units_pct": pct}
        elif kind == "days":
            if sub["plan_id"] is None or sub["ends_at"] is None:
                raise YandexError("promo", "Промокод продлевает подписку с тарифом — у вас подписка без срока")
            days = int(promo["days"])
            if sub["ends_at"] > now:
                conn.execute(
                    "UPDATE subscriptions SET ends_at = ends_at + make_interval(days => %s), updated_at = NOW() WHERE user_id = %s",
                    (days, user_id),
                )
            else:
                was_expired = True
                conn.execute(
                    """
                    UPDATE subscriptions SET starts_at = %s, ends_at = %s, bonus_units = 0, bonus_period_start = NULL,
                           updated_at = NOW()
                    WHERE user_id = %s
                    """,
                    (now, now + timedelta(days=days), user_id),
                )
            message = f"Подписка продлена на {days} дн."
        else:
            plan_id = int(promo["plan_id"])
            current = before["plan"]
            if (
                current
                and current["id"] != plan_id
                and not current["is_trial"]
                and not before["expired"]
            ):
                raise YandexError(
                    "promo",
                    f"Промокод даёт тариф «{promo['plan_name']}», а у вас действует «{current['name']}» — "
                    "активируйте код после окончания тарифа",
                )
            res = plans.extend_or_switch(conn, user_id, plan_id, int(promo["days"]), now)
            was_expired = res["was_expired"]
            message = (
                f"Тариф «{promo['plan_name']}» продлён на {promo['days']} дн."
                if res["renewed"]
                else f"Подключён тариф «{promo['plan_name']}» на {promo['days']} дн."
            )
        conn.execute("INSERT INTO promo_redemptions (promo_id, user_id) VALUES (%s, %s)", (promo["id"], user_id))
    after = plans.subscription(user_id)
    last = plans.last_day(after["ends_at"])
    if kind != "units" and last:
        message += f" — по {_fmt_day(last)}"
    events.record(
        user_id, "promo", code=promo["code"], promo_kind=kind, plan_id=after["plan"]["id"] if after["plan"] else None,
        last_day=last, **extra,
    )
    if was_expired and not after["expired"]:
        plans.resume(user_id)
    return {"kind": kind, "message": message, "last_day": last}
