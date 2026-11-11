"""Реф-ссылки: ?ref=код на любой странице сайта.

Сайт запоминает код при первом заходе (localStorage, 90 дней — первый источник не перебивается) и один
раз сообщает о переходе (visits). При регистрации — по почте или через Яндекс — код сохраняется в
users.ref_link_id. Статистика ссылки: переходы, регистрации, подтверждённые почты, оплаты и выручка
(без тестовых платежей и за вычетом возвратов).

Личная ссылка «Ссылка для друзей» — та же строка ref_links с owner_user_id: создаётся при первом
открытии страницы «Тариф», в админском списке ссылок её нет. Когда приглашённый по ней впервые оплатит
тариф (не тестовым платежом), оба получают дни к подписке — сколько, задаёт админ. Бонус выдаётся один
раз на приглашённого (ref_bonuses), в той же транзакции, что и продление по платежу.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime

from app import plans
from app.db import pool
from app.settings_store import get_setting, public_base_url, set_setting
from app.yandex import YandexError

CODE_RE = re.compile(r"^[a-z0-9_-]{2,40}$")
NOT_FOUND = "Реф-ссылка не найдена"


def normalize(code: str) -> str:
    return (code or "").strip().lower()


def visit(code: str) -> bool:
    """Переход по ссылке: +1 к счётчику активной ссылки."""
    code = normalize(code)
    if not CODE_RE.match(code):
        return False
    with pool.connection() as conn:
        row = conn.execute(
            "UPDATE ref_links SET visits = visits + 1 WHERE code = %s AND active RETURNING id", (code,)
        ).fetchone()
    return row is not None


def link_id(code: str) -> int | None:
    code = normalize(code)
    if not CODE_RE.match(code):
        return None
    with pool.connection() as conn:
        row = conn.execute("SELECT id FROM ref_links WHERE code = %s AND active", (code,)).fetchone()
    return int(row["id"]) if row else None


def attach(user_id: int, code: str) -> bool:
    """Запомнить, по какой ссылке пришёл новый пользователь (только первую)."""
    lid = link_id(code) if code else None
    if lid is None:
        return False
    with pool.connection() as conn:
        row = conn.execute(
            "UPDATE users SET ref_link_id = %s WHERE id = %s AND ref_link_id IS NULL RETURNING id", (lid, user_id)
        ).fetchone()
    return row is not None


# --- Админка ------------------------------------------------------------------------------------


_STATS = """
    SELECT l.id, l.code, l.name, l.visits, l.active, l.created_at,
           COUNT(u.id) AS registrations,
           COUNT(u.id) FILTER (WHERE u.email_verified_at IS NOT NULL) AS verified,
           COUNT(pay.user_id) AS payers,
           COALESCE(SUM(pay.n), 0) AS payments,
           COALESCE(SUM(pay.rub), 0) AS revenue_rub
    FROM ref_links l
    LEFT JOIN users u ON u.ref_link_id = l.id
    LEFT JOIN (
        SELECT user_id, COUNT(*) AS n, SUM(amount_rub - refunded_rub) AS rub
        FROM payments WHERE status = 'succeeded' AND NOT test AND user_id IS NOT NULL
        GROUP BY user_id
    ) pay ON pay.user_id = u.id
"""


def _out(row: dict, base: str) -> dict:
    return {
        "id": int(row["id"]),
        "code": row["code"],
        "name": row["name"] or "",
        "active": bool(row["active"]),
        "created_at": row["created_at"].isoformat(),
        "url": f"{base}/?ref={row['code']}" if base else f"/?ref={row['code']}",
        "visits": int(row["visits"]),
        "registrations": int(row["registrations"]),
        "verified": int(row["verified"]),
        "payers": int(row["payers"]),
        "payments": int(row["payments"]),
        "revenue_rub": float(row["revenue_rub"]),
    }


def list_links() -> list[dict]:
    base = public_base_url()
    with pool.connection() as conn:
        rows = conn.execute(
            _STATS + " WHERE l.owner_user_id IS NULL GROUP BY l.id ORDER BY l.created_at DESC, l.id DESC"
        ).fetchall()
    return [_out(r, base) for r in rows]


def _get(lid: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(_STATS + " WHERE l.id = %s GROUP BY l.id", (lid,)).fetchone()
    return _out(row, public_base_url()) if row else None


def save_link(lid: int | None, data: dict) -> dict:
    code = normalize(data.get("code"))
    if not CODE_RE.match(code):
        raise YandexError("ref-field", "Код — от 2 до 40 символов: латинские буквы, цифры, «-» и «_»")
    name = str(data.get("name") or "").strip()[:120]
    active = bool(data.get("active", True))
    with pool.connection() as conn:
        taken = conn.execute("SELECT 1 FROM ref_links WHERE code = %s AND id IS DISTINCT FROM %s", (code, lid)).fetchone()
        if taken:
            raise YandexError("exists", "Такой код уже есть")
        if lid is None:
            row = conn.execute(
                "INSERT INTO ref_links (code, name, active) VALUES (%s, %s, %s) RETURNING id", (code, name, active)
            ).fetchone()
        else:
            row = conn.execute(
                "UPDATE ref_links SET code = %s, name = %s, active = %s WHERE id = %s RETURNING id",
                (code, name, active, lid),
            ).fetchone()
    if row is None:
        raise YandexError("not-found", NOT_FOUND)
    return _get(int(row["id"]))


def delete_link(lid: int) -> None:
    """Пользователи остаются, просто без источника."""
    with pool.connection() as conn:
        row = conn.execute("DELETE FROM ref_links WHERE id = %s RETURNING id", (lid,)).fetchone()
    if row is None:
        raise YandexError("not-found", NOT_FOUND)


# --- Бонус за приглашение -----------------------------------------------------------------------

BONUS_KEYS = {"referrer_days": "ref_bonus_referrer_days", "invitee_days": "ref_bonus_invitee_days"}
MAX_BONUS_DAYS = 365


def _days(value) -> int:
    try:
        return max(0, min(MAX_BONUS_DAYS, int(value)))
    except (TypeError, ValueError):
        return 0


def bonus_settings() -> dict:
    """Сколько дней получают пригласивший и приглашённый; 0 — бонуса нет."""
    return {k: _days(get_setting(key, "0")) for k, key in BONUS_KEYS.items()}


def save_bonus_settings(data: dict) -> dict:
    clean = {}
    for k in BONUS_KEYS:
        raw = data.get(k)
        try:
            days = int(raw or 0)
        except (TypeError, ValueError) as exc:
            raise YandexError("ref-field", "Дни бонуса — целое число") from exc
        if not 0 <= days <= MAX_BONUS_DAYS:
            raise YandexError("ref-field", f"Дни бонуса — от 0 до {MAX_BONUS_DAYS}")
        clean[k] = days
    for k, key in BONUS_KEYS.items():
        set_setting(key, str(clean[k]))
    return bonus_settings()


def personal_summary() -> dict:
    """Админке: сколько личных ссылок, пришло по ним, выдано бонусов и дней."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM ref_links WHERE owner_user_id IS NOT NULL) AS links,
                   (SELECT COUNT(*) FROM users u JOIN ref_links l ON l.id = u.ref_link_id
                     WHERE l.owner_user_id IS NOT NULL) AS registrations,
                   (SELECT COUNT(*) FROM ref_bonuses) AS bonuses,
                   (SELECT COALESCE(SUM(referrer_days + invitee_days), 0) FROM ref_bonuses) AS bonus_days
            """
        ).fetchone()
    return {k: int(row[k]) for k in ("links", "registrations", "bonuses", "bonus_days")}


def grant_bonus(conn, invitee_id: int, payment_id: int, now: datetime) -> dict | None:
    """Первая оплата приглашённого по личной ссылке: дни обоим. Вызывать в транзакции продления.

    Возвращает {"referrer_id", "referrer", "invitee"} (итоги plans.add_days) или None — не по личной
    ссылке или бонус за этого приглашённого уже был. Строка ref_bonuses пишется и при нулевых днях:
    бонус — только за первую оплату, включённый позже бонус за прежних приглашённых не выдаётся.
    """
    row = conn.execute(
        """
        SELECT l.owner_user_id FROM users u JOIN ref_links l ON l.id = u.ref_link_id
        WHERE u.id = %s AND l.owner_user_id IS NOT NULL AND l.owner_user_id <> u.id
        """,
        (invitee_id,),
    ).fetchone()
    if row is None:
        return None
    referrer_id = int(row["owner_user_id"])
    claimed = conn.execute(
        """
        INSERT INTO ref_bonuses (invitee_user_id, referrer_user_id, payment_id) VALUES (%s, %s, %s)
        ON CONFLICT (invitee_user_id) DO NOTHING RETURNING invitee_user_id
        """,
        (invitee_id, referrer_id, payment_id),
    ).fetchone()
    if claimed is None:
        return None
    cfg = bonus_settings()
    invitee = plans.add_days(conn, invitee_id, cfg["invitee_days"], now)
    referrer = plans.add_days(conn, referrer_id, cfg["referrer_days"], now)
    conn.execute(
        "UPDATE ref_bonuses SET referrer_days = %s, invitee_days = %s WHERE invitee_user_id = %s",
        (referrer["days"], invitee["days"], invitee_id),
    )
    return {"referrer_id": referrer_id, "referrer": referrer, "invitee": invitee}


# --- Личная ссылка пользователя ------------------------------------------------------------------


def _slug(email: str) -> str:
    local = re.sub(r"[^a-z0-9]+", "-", (email or "").split("@")[0].lower()).strip("-")[:20].strip("-")
    return local if len(local) >= 2 else "friend"


def _ensure_link(user_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM ref_links WHERE owner_user_id = %s", (user_id,)).fetchone()
        if row is not None:
            return row
        user = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
        if user is None:
            raise YandexError("not-found", "Пользователь не найден")
        base = _slug(user["email"])
        for _ in range(8):
            row = conn.execute(
                """
                INSERT INTO ref_links (code, name, owner_user_id) VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING RETURNING *
                """,
                (f"{base}-{secrets.token_hex(2)}", f"Личная: {user['email']}"[:120], user_id),
            ).fetchone()
            if row is not None:
                return row
            # Конфликт по владельцу — ссылку только что создал параллельный запрос; иначе занят код.
            row = conn.execute("SELECT * FROM ref_links WHERE owner_user_id = %s", (user_id,)).fetchone()
            if row is not None:
                return row
    raise YandexError("ref-busy", "Не удалось создать ссылку — повторите позже")


def my_link(user_id: int) -> dict:
    """«Ссылка для друзей»: ссылка (создаётся при первом обращении), сколько пришло и оплатило, бонус."""
    link = _ensure_link(user_id)
    base = public_base_url()
    with pool.connection() as conn:
        stats = conn.execute(
            """
            SELECT COUNT(*) AS registrations,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM payments p
                       WHERE p.user_id = u.id AND p.status = 'succeeded' AND NOT p.test
                   )) AS payers
            FROM users u WHERE u.ref_link_id = %s
            """,
            (link["id"],),
        ).fetchone()
        earned = conn.execute(
            "SELECT COALESCE(SUM(referrer_days), 0) AS days FROM ref_bonuses WHERE referrer_user_id = %s", (user_id,)
        ).fetchone()
    return {
        "code": link["code"],
        "url": f"{base}/?ref={link['code']}" if base else f"/?ref={link['code']}",
        "active": bool(link["active"]),
        "registrations": int(stats["registrations"]),
        "payers": int(stats["payers"]),
        "earned_days": int(earned["days"]),
        "bonus": bonus_settings(),
    }
