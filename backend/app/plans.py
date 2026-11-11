"""Тарифы и подписки: что доступно пользователю, сколько у него единиц и проектов и когда кончается период.

Подписка — тариф с началом и концом. Лимит единиц действует на период длиной в срок тарифа: оплатили
30 дней — 30 дней и лимит, продлили ещё на 30 — с конца прошлого периода начинается новый, остаток
прошлого сгорает. Без тарифа (так живут пользователи, что были до тарифов) период — календарный месяц.

Админ может поменять поверх тарифа лимит единиц, число проектов и функции (overrides): ключа нет — как
в тарифе, null — без лимита.

Нет строки подписки или нет тарифа — ограничений нет, кроме overrides. Пробный тариф выдаётся новым
пользователям; пока его нет в админке, новые пользователи тоже без ограничений.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta

from app import mailer
from app.clock import MSK, now_msk
from app.db import pool
from app.yandex import YandexError

log = logging.getLogger(__name__)

FEATURES = {
    "autopilot": "Автопилот: правила",
    "experiments": "Эксперименты",
    "wordstat": "Вордстат",
    "images": "Генерация картинок",
    "digest": "Дайджест в Telegram",
}
EXPIRED = "Подписка закончилась — продлите тариф, чтобы продолжить"
PROJECTS_LIMIT = "На вашем тарифе больше проектов не добавить — перейдите на тариф выше или отправьте лишний проект в архив"
PROJECT_LOCKED = "Проект не входит в ваш тариф — перейдите на тариф выше или отправьте другой проект в архив"
MAX_UNITS = 1_000_000_000
MAX_PROJECTS = 10_000
_UNSET = object()


def _num(value) -> float | None:
    return None if value is None else float(value)


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def feature_locked(key: str) -> YandexError:
    return YandexError("feature-locked", f"«{FEATURES.get(key, key)}» — на тарифе выше")


# --- Тарифы (админка) ---------------------------------------------------------------------------


def _features(raw) -> dict[str, bool]:
    """Полный набор функций: ключа нет — включена."""
    raw = raw if isinstance(raw, dict) else {}
    return {k: bool(raw.get(k, True)) for k in FEATURES}


def _plan_out(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "price_rub": float(row["price_rub"]),
        "days": int(row["days"]),
        "units_limit": _num(row.get("units_limit")),
        "projects_limit": row.get("projects_limit"),
        "features": _features(row.get("features")),
        "is_trial": bool(row["is_trial"]),
        "active": bool(row["active"]),
        "position": int(row["position"]),
        "discount_3_pct": float(row.get("discount_3_pct") or 0),
        "discount_12_pct": float(row.get("discount_12_pct") or 0),
    }


def list_plans(*, for_admin: bool = False) -> list[dict]:
    """Админке — все тарифы с числом пользователей; пользователю — включённые платные."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT p.*, (SELECT COUNT(*) FROM subscriptions s WHERE s.plan_id = p.id) AS users
            FROM plans p
            WHERE %s OR (p.active AND NOT p.is_trial)
            ORDER BY p.is_trial DESC, p.position, p.price_rub, p.id
            """,
            (for_admin,),
        ).fetchall()
    out = []
    for r in rows:
        item = _plan_out(r)
        if for_admin:
            item["users"] = int(r["users"])
        out.append(item)
    return out


# На сколько сроков тарифа можно оплатить сразу (срок обычно месяц).
TERMS = (1, 3, 12)


def term_discount(plan: dict, months: int) -> float:
    if months == 3:
        return float(plan.get("discount_3_pct") or 0)
    if months == 12:
        return float(plan.get("discount_12_pct") or 0)
    return 0.0


def public_catalog_from(rows: list[dict]) -> dict:
    """Тарифы для сайта tolk-ads.ru из строк ``list_plans(for_admin=True)``: только включённые, без служебных полей.

    Единицы, как и в кабинете, не показываем — только во сколько раз лимит больше, чем у младшего платного
    тарифа (``units_factor``; у младшего — 1, без лимита — ``units_unlimited``).
    """
    active = [p for p in rows if p["active"]]
    paid = [p for p in active if not p["is_trial"]]
    trial = next((p for p in active if p["is_trial"]), None)
    base = min((p["units_limit"] for p in paid if p["units_limit"]), default=None)
    items = []
    for plan in paid:
        limit = plan["units_limit"]
        items.append(
            {
                "id": plan["id"],
                "name": plan["name"],
                "price_rub": plan["price_rub"],
                "days": plan["days"],
                "projects_limit": plan["projects_limit"],
                "features": plan["features"],
                "units_unlimited": limit is None,
                "units_factor": round(limit / base, 1) if base and limit else None,
                "term_discounts": {str(m): term_discount(plan, m) for m in TERMS},
            }
        )
    return {
        "items": items,
        "features": FEATURES,
        "trial": {"days": trial["days"], "projects_limit": trial["projects_limit"]} if trial else None,
    }


def public_catalog() -> dict:
    return public_catalog_from(list_plans(for_admin=True))


def get_plan(plan_id: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM plans WHERE id = %s", (plan_id,)).fetchone()
    return _plan_out(row) if row else None


def _number(value, what: str, *, maximum: float, integer: bool = False) -> float | int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(str(value).replace(",", ".").replace(" ", ""))
    except ValueError as exc:
        raise YandexError("plan-field", f"{what} — число") from exc
    if math.isnan(number) or not 0 <= number <= maximum:
        raise YandexError("plan-field", f"{what} — от 0 до {maximum:,.0f}".replace(",", " "))
    if integer:
        if number != int(number):
            raise YandexError("plan-field", f"{what} — целое число")
        return int(number)
    return round(number, 2)


def _clean_plan(data: dict) -> dict:
    name = (data.get("name") or "").strip()[:60]
    if not name:
        raise YandexError("plan-field", "Название тарифа не может быть пустым")
    price = _number(data.get("price_rub"), "Цена", maximum=10_000_000) or 0.0
    days = _number(data.get("days"), "Срок в днях", maximum=3660, integer=True)
    if not days:
        raise YandexError("plan-field", "Срок в днях — от 1 до 3660")
    return {
        "name": name,
        "price_rub": price,
        "days": days,
        "units_limit": _number(data.get("units_limit"), "Лимит единиц", maximum=MAX_UNITS),
        "projects_limit": _number(data.get("projects_limit"), "Число проектов", maximum=MAX_PROJECTS, integer=True),
        "features": json.dumps({k: bool(v) for k, v in (data.get("features") or {}).items() if k in FEATURES}),
        "is_trial": bool(data.get("is_trial")),
        "active": bool(data.get("active", True)),
        "position": int(data.get("position") or 0),
        "discount_3_pct": _number(data.get("discount_3_pct"), "Скидка за 3 срока, %", maximum=99) or 0.0,
        "discount_12_pct": _number(data.get("discount_12_pct"), "Скидка за 12 сроков, %", maximum=99) or 0.0,
    }


def save_plan(plan_id: int | None, data: dict) -> dict:
    fields = _clean_plan(data)
    with pool.connection() as conn, conn.transaction():
        if fields["is_trial"]:
            # Пробный — один: отмеченный снимает отметку с прежнего.
            conn.execute("UPDATE plans SET is_trial = FALSE WHERE is_trial AND id IS DISTINCT FROM %s", (plan_id,))
        if plan_id is None:
            row = conn.execute(
                """
                INSERT INTO plans (name, price_rub, days, units_limit, projects_limit, features, is_trial, active, position,
                                   discount_3_pct, discount_12_pct)
                VALUES (%(name)s, %(price_rub)s, %(days)s, %(units_limit)s, %(projects_limit)s, %(features)s,
                        %(is_trial)s, %(active)s, %(position)s, %(discount_3_pct)s, %(discount_12_pct)s)
                RETURNING id
                """,
                fields,
            ).fetchone()
        else:
            row = conn.execute(
                """
                UPDATE plans SET name = %(name)s, price_rub = %(price_rub)s, days = %(days)s,
                       units_limit = %(units_limit)s, projects_limit = %(projects_limit)s, features = %(features)s,
                       is_trial = %(is_trial)s, active = %(active)s, position = %(position)s,
                       discount_3_pct = %(discount_3_pct)s, discount_12_pct = %(discount_12_pct)s, updated_at = NOW()
                WHERE id = %(id)s
                RETURNING id
                """,
                {**fields, "id": plan_id},
            ).fetchone()
    if row is None:
        raise YandexError("not-found", "Тариф не найден")
    return get_plan(int(row["id"]))


def delete_plan(plan_id: int) -> None:
    with pool.connection() as conn:
        used = conn.execute("SELECT COUNT(*) AS n FROM subscriptions WHERE plan_id = %s", (plan_id,)).fetchone()
        if used and used["n"]:
            raise YandexError("plan-in-use", "На тарифе есть пользователи — выключите тариф вместо удаления")
        promo = conn.execute("SELECT code FROM promo_codes WHERE plan_id = %s LIMIT 1", (plan_id,)).fetchone()
        if promo:
            raise YandexError("plan-in-use", f"Тариф выдаёт промокод {promo['code']} — сначала удалите или измените его")
        row = conn.execute("DELETE FROM plans WHERE id = %s RETURNING id", (plan_id,)).fetchone()
    if row is None:
        raise YandexError("not-found", "Тариф не найден")


def list_upgrades() -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute("SELECT * FROM plan_upgrades ORDER BY from_plan_id, to_plan_id").fetchall()
    return [
        {"from_plan_id": int(r["from_plan_id"]), "to_plan_id": int(r["to_plan_id"]), "discount_pct": float(r["discount_pct"])}
        for r in rows
    ]


def set_upgrade(from_plan_id: int, to_plan_id: int, discount_pct) -> dict:
    """Скидка за переход с тарифа на тариф; пусто или 0 — скидки нет."""
    if from_plan_id == to_plan_id:
        raise YandexError("plan-field", "Переход возможен только на другой тариф")
    pct = _number(discount_pct, "Скидка, %", maximum=100)
    if not get_plan(from_plan_id) or not get_plan(to_plan_id):
        raise YandexError("not-found", "Тариф не найден")
    with pool.connection() as conn:
        if not pct:
            conn.execute(
                "DELETE FROM plan_upgrades WHERE from_plan_id = %s AND to_plan_id = %s", (from_plan_id, to_plan_id)
            )
        else:
            conn.execute(
                """
                INSERT INTO plan_upgrades (from_plan_id, to_plan_id, discount_pct) VALUES (%s, %s, %s)
                ON CONFLICT (from_plan_id, to_plan_id) DO UPDATE SET discount_pct = EXCLUDED.discount_pct
                """,
                (from_plan_id, to_plan_id, pct),
            )
    return {"from_plan_id": from_plan_id, "to_plan_id": to_plan_id, "discount_pct": pct or 0.0}


# --- Подписка -----------------------------------------------------------------------------------


def _month(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1, tzinfo=MSK)
    end = datetime(now.year + now.month // 12, now.month % 12 + 1, 1, tzinfo=MSK)
    return start, end


def period_of(starts_at: datetime, ends_at: datetime | None, days: int | None, now: datetime) -> tuple[datetime, datetime]:
    """Текущий период лимита: окно длиной в срок тарифа от начала подписки; без тарифа — месяц."""
    if not days:
        start, end = _month(now)
        if ends_at and end > ends_at:
            end = max(ends_at, start)
        return start, end
    step = timedelta(days=days)
    moment = now
    if ends_at and moment >= ends_at:
        # Подписка кончилась — показываем последний период.
        moment = ends_at - timedelta(microseconds=1)
    k = max(0, (moment - starts_at) // step)
    start = starts_at + step * k
    end = start + step
    if ends_at and end > ends_at:
        end = ends_at
    return start, end


def _row(user_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT u.id AS user_id, u.email_verified_at, u.created_at AS user_created_at,
                   s.plan_id, s.starts_at, s.ends_at, s.overrides, s.bonus_units, s.bonus_period_start,
                   p.name AS plan_name, p.price_rub, p.days, p.units_limit, p.projects_limit,
                   p.features, p.is_trial
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id
            LEFT JOIN plans p ON p.id = s.plan_id
            WHERE u.id = %s
            """,
            (user_id,),
        ).fetchone()


def email_check_required() -> bool:
    """Подтверждение почты включено, только когда письма действительно уходят (SMTP в .env)."""
    return mailer.configured()


def subscription(user_id: int, *, now: datetime | None = None) -> dict:
    """Действующие условия пользователя: тариф, срок, лимиты с учётом правок админа, функции, период."""
    now = now or now_msk()
    row = _row(user_id) or {}
    has_plan = row.get("plan_id") is not None
    overrides = row.get("overrides") or {}
    starts_at = row.get("starts_at") or row.get("user_created_at") or now
    ends_at = row.get("ends_at")

    def limit(key: str):
        if key in overrides:
            return overrides[key]
        return row.get(key) if has_plan else None

    units = limit("units_limit")
    projects = limit("projects_limit")
    features = _features(row.get("features") if has_plan else {})
    for key, value in (overrides.get("features") or {}).items():
        if key in features:
            features[key] = bool(value)
    period_start, period_end = period_of(starts_at, ends_at, int(row["days"]) if has_plan else None, now)
    # Единицы из промокодов — только в том периоде, где их активировали.
    bonus = float(row.get("bonus_units") or 0)
    if not bonus or row.get("bonus_period_start") != period_start:
        bonus = 0.0
    if units is not None and bonus:
        units = float(units) + bonus
    expired = ends_at is not None and now >= ends_at
    verified = row.get("email_verified_at") is not None
    if row and not verified and email_check_required():
        state = "unverified"
    elif expired:
        state = "expired"
    else:
        state = "ok"
    return {
        "plan": (
            {
                "id": int(row["plan_id"]),
                "name": row["plan_name"],
                "is_trial": bool(row["is_trial"]),
                "price_rub": float(row["price_rub"]),
                "days": int(row["days"]),
            }
            if has_plan
            else None
        ),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "expired": expired,
        "state": state,
        "email_verified": verified,
        "units_limit": _num(units),
        "projects_limit": None if projects is None else int(projects),
        "features": features,
        "overrides": overrides,
        "bonus_units": bonus,
        "period_start": period_start,
        "period_end": period_end,
    }


def last_day(ends_at: datetime | None) -> str | None:
    """Последний день подписки (включительно) по Москве: подписка до 00:00 16-го — «по 15-е»."""
    if ends_at is None:
        return None
    return (ends_at.astimezone(MSK) - timedelta(microseconds=1)).date().isoformat()


def public(sub: dict) -> dict:
    return {
        "plan": sub["plan"],
        "starts_at": _iso(sub["starts_at"]),
        "ends_at": _iso(sub["ends_at"]),
        "last_day": last_day(sub["ends_at"]),
        "expired": sub["expired"],
        "state": sub["state"],
        "email_verified": sub["email_verified"],
        "units_limited": sub["units_limit"] is not None,
        "projects_limit": sub["projects_limit"],
        "features": sub["features"],
        "period_start": _iso(sub["period_start"]),
        "period_end": _iso(sub["period_end"]),
    }


def has_feature(user_id: int, key: str) -> bool:
    return subscription(user_id)["features"].get(key, True)


def require_feature(user_id: int | None, key: str) -> None:
    if user_id and not has_feature(user_id, key):
        raise feature_locked(key)


def start_trial(user_id: int) -> None:
    """Новому пользователю — пробный тариф (если задан) со дня регистрации."""
    with pool.connection() as conn:
        trial = conn.execute("SELECT id, days FROM plans WHERE is_trial AND active LIMIT 1").fetchone()
        if trial:
            conn.execute(
                """
                INSERT INTO subscriptions (user_id, plan_id, starts_at, ends_at)
                VALUES (%s, %s, NOW(), NOW() + make_interval(days => %s))
                ON CONFLICT (user_id) DO NOTHING
                """,
                (user_id, trial["id"], int(trial["days"])),
            )
        else:
            conn.execute("INSERT INTO subscriptions (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))


def _clean_overrides(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    if "units_limit" in raw:
        out["units_limit"] = _number(raw["units_limit"], "Лимит единиц", maximum=MAX_UNITS)
    if "projects_limit" in raw:
        out["projects_limit"] = _number(raw["projects_limit"], "Число проектов", maximum=MAX_PROJECTS, integer=True)
    features = {k: bool(v) for k, v in (raw.get("features") or {}).items() if k in FEATURES}
    if features:
        out["features"] = features
    return out


def _end_of(day: str) -> datetime:
    """«По 15.12 включительно» → 00:00 16.12 по Москве."""
    try:
        d = date.fromisoformat(str(day))
    except ValueError as exc:
        raise YandexError("plan-field", "Дата окончания — в формате ГГГГ-ММ-ДД") from exc
    return datetime(d.year, d.month, d.day, tzinfo=MSK) + timedelta(days=1)


def set_subscription(user_id: int, *, plan_id=_UNSET, until=_UNSET, overrides=_UNSET, renew: bool = False) -> dict:
    """Правка подписки в админке.

    plan_id — другой тариф (None — без тарифа): период начинается заново с сегодняшнего дня, срок — срок
    тарифа, если не задан until. until — последний день подписки «ГГГГ-ММ-ДД» (None — без срока).
    renew — продлить на срок тарифа: с конца текущего срока, а если он прошёл — с сегодняшнего дня.
    """
    from app.users import get_user

    if get_user(user_id) is None:
        raise YandexError("not-found", "Пользователь не найден")
    now = now_msk()
    before = subscription(user_id, now=now)
    with pool.connection() as conn:
        current = conn.execute("SELECT * FROM subscriptions WHERE user_id = %s", (user_id,)).fetchone()
    cur_plan = current["plan_id"] if current else None
    starts_at = current["starts_at"] if current else now
    ends_at = current["ends_at"] if current else None
    stored_overrides = (current["overrides"] if current else None) or {}

    new_plan = cur_plan
    if plan_id is not _UNSET and plan_id != cur_plan:
        plan = get_plan(plan_id) if plan_id is not None else None
        if plan_id is not None and plan is None:
            raise YandexError("not-found", "Тариф не найден")
        new_plan = plan_id
        starts_at = now
        ends_at = now + timedelta(days=plan["days"]) if plan else None
    if until is not _UNSET:
        ends_at = _end_of(until) if until else None
        if ends_at is not None and ends_at <= starts_at:
            # Срок задают задним числом (закрыть доступ) — начало сдвигаем, чтобы период был непустым.
            starts_at = ends_at - timedelta(days=1)
    if renew:
        plan = get_plan(new_plan) if new_plan is not None else None
        if plan is None:
            raise YandexError("plan-field", "Продлить можно только подписку с тарифом")
        if ends_at is None or ends_at <= now:
            starts_at = now
            ends_at = now + timedelta(days=plan["days"])
        else:
            ends_at = ends_at + timedelta(days=plan["days"])
    if overrides is not _UNSET:
        stored_overrides = _clean_overrides(overrides)

    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, plan_id, starts_at, ends_at, overrides, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET plan_id = EXCLUDED.plan_id, starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at, overrides = EXCLUDED.overrides, updated_at = NOW()
            """,
            (user_id, new_plan, starts_at, ends_at, json.dumps(stored_overrides)),
        )
    after = subscription(user_id)
    from app import events

    events.record(
        user_id,
        "plan_changed",
        by="admin",
        plan_id=new_plan,
        last_day=last_day(after["ends_at"]),
        renew=renew,
    )
    if before["expired"] and not after["expired"]:
        resume(user_id)
    return admin_view(user_id, after)


def lock_subscription(conn, user_id: int) -> dict:
    """Строка подписки под блокировкой (в транзакции conn) вместе с тарифом; нет строки — создаётся без тарифа."""
    conn.execute("INSERT INTO subscriptions (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    return conn.execute(
        """
        SELECT s.*, p.name AS plan_name, p.is_trial, p.price_rub
        FROM subscriptions s LEFT JOIN plans p ON p.id = s.plan_id
        WHERE s.user_id = %s FOR UPDATE OF s
        """,
        (user_id,),
    ).fetchone()


def extend_or_switch(conn, user_id: int, plan_id: int, days: int, now: datetime) -> dict:
    """Оплата или промокод на тариф: тот же действующий тариф — продлеваем с конца срока, иначе тариф
    начинается сейчас на days дней. При смене правки админа поверх тарифа и бонус единиц сбрасываются.

    Вызывать в транзакции conn. Возвращает {"was_expired", "renewed", "ends_at"}.
    """
    sub = lock_subscription(conn, user_id)
    ends_at = sub["ends_at"]
    was_expired = ends_at is not None and ends_at <= now
    if sub["plan_id"] == plan_id and ends_at is not None and ends_at > now:
        new_end = ends_at + timedelta(days=days)
        conn.execute(
            "UPDATE subscriptions SET ends_at = %s, updated_at = NOW() WHERE user_id = %s", (new_end, user_id)
        )
        return {"was_expired": False, "renewed": True, "ends_at": new_end}
    new_end = now + timedelta(days=days)
    conn.execute(
        """
        UPDATE subscriptions SET plan_id = %s, starts_at = %s, ends_at = %s, overrides = '{}',
               bonus_units = 0, bonus_period_start = NULL, updated_at = NOW()
        WHERE user_id = %s
        """,
        (plan_id, now, new_end, user_id),
    )
    return {"was_expired": was_expired, "renewed": False, "ends_at": new_end}


def add_days(conn, user_id: int, days: int, now: datetime) -> dict:
    """Дни к подписке с тарифом (бонус за приглашение): действует — с конца срока, закончилась — с сегодня.

    Вызывать в транзакции conn. Подписка без тарифа или бессрочная — ничего не добавляем (days = 0).
    Возвращает {"days", "was_expired", "ends_at"}.
    """
    sub = lock_subscription(conn, user_id)
    if days <= 0 or sub["plan_id"] is None or sub["ends_at"] is None:
        return {"days": 0, "was_expired": False, "ends_at": sub["ends_at"]}
    if sub["ends_at"] > now:
        new_end = sub["ends_at"] + timedelta(days=days)
        conn.execute("UPDATE subscriptions SET ends_at = %s, updated_at = NOW() WHERE user_id = %s", (new_end, user_id))
        return {"days": days, "was_expired": False, "ends_at": new_end}
    new_end = now + timedelta(days=days)
    conn.execute(
        """
        UPDATE subscriptions SET starts_at = %s, ends_at = %s, bonus_units = 0, bonus_period_start = NULL,
               updated_at = NOW()
        WHERE user_id = %s
        """,
        (now, new_end, user_id),
    )
    return {"days": days, "was_expired": True, "ends_at": new_end}


def admin_view(user_id: int, sub: dict | None = None) -> dict:
    sub = sub or subscription(user_id)
    return {**public(sub), "units_limit": sub["units_limit"], "overrides": sub["overrides"]}


def resume(user_id: int) -> None:
    """Подписка снова действует: догружаем данные за время простоя — синк подключений с кампаниями и целями."""
    from app.job_handlers import submit
    from app.jobs import PRIORITY_USER

    with pool.connection() as conn:
        rows = conn.execute("SELECT id FROM yandex_connections WHERE user_id = %s", (user_id,)).fetchall()
    for r in rows:
        try:
            submit("sync_connection", user_id, {"connection_id": int(r["id"]), "deep": True}, priority=PRIORITY_USER)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось поставить синк подключения %s после продления", r["id"])


def active_user_ids() -> set[int]:
    """Кого обслуживают фоновые задачи: не заблокирован, подписка действует, почта подтверждена (если нужно)."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.blocked_at IS NULL
              AND (s.ends_at IS NULL OR s.ends_at > NOW())
              AND (%s OR u.email_verified_at IS NOT NULL)
            """,
            (not email_check_required(),),
        ).fetchall()
    return {int(r["id"]) for r in rows}


def users_with_feature(user_ids, key: str) -> set[int]:
    return {uid for uid in user_ids if has_feature(uid, key)}


def offers(user_id: int) -> list[dict]:
    """Тарифы для пользователя: цена со скидкой за переход с его тарифа."""
    sub = subscription(user_id)
    current = sub["plan"]["id"] if sub["plan"] else None
    discounts = {}
    if current is not None:
        discounts = {u["to_plan_id"]: u["discount_pct"] for u in list_upgrades() if u["from_plan_id"] == current}
    out = []
    # Бонус из промокода — разовый, сравниваем с лимитом самого тарифа.
    base = sub["units_limit"] - sub["bonus_units"] if sub["units_limit"] is not None else None
    for plan in list_plans():
        pct = discounts.get(plan["id"], 0.0)
        # Единицы пользователю не показываем — только во сколько раз лимит больше текущего.
        factor = None
        if base and plan["units_limit"]:
            factor = round(plan["units_limit"] / base, 1)
        out.append(
            {
                "id": plan["id"],
                "name": plan["name"],
                "price_rub": plan["price_rub"],
                "price_with_discount": round(plan["price_rub"] * (100 - pct) / 100, 2),
                "discount_pct": pct,
                "days": plan["days"],
                # Скидка за оплату сразу на 3 и 12 сроков тарифа: {"1": 0, "3": 5, "12": 15}.
                "term_discounts": {str(m): term_discount(plan, m) for m in TERMS},
                "projects_limit": plan["projects_limit"],
                "units_unlimited": plan["units_limit"] is None,
                "units_factor": factor,
                "features": plan["features"],
                "current": plan["id"] == current,
            }
        )
    return out
