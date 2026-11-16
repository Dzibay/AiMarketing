"""Обращения: входящие поддержки — все переписки, сначала те, где последнее слово за пользователем.

Сама переписка и ответ — support.admin_thread / support.admin_reply (по пользователю). Здесь — список слева:
последнее сообщение, кто, тариф, сколько непрочитанных и с какого времени человек ждёт ответа.
"""

from __future__ import annotations

import math

from app.db import pool

PER_PAGE = 40
FILTERS = ("open", "all")
SNIPPET = 140

_LIST = """
WITH last AS (
    SELECT DISTINCT ON (m.thread_id) m.thread_id, m.author, m.body, m.image IS NOT NULL AS has_image, m.created_at
    FROM support_messages m ORDER BY m.thread_id, m.created_at DESC, m.id DESC
), last_admin AS (
    SELECT thread_id, MAX(created_at) AS at FROM support_messages WHERE author = 'admin' GROUP BY thread_id
), base AS (
    SELECT t.id, t.user_id, t.updated_at, t.admin_read_at, u.email, u.name, u.blocked_at, u.last_seen_at,
           pl.name AS plan_name, pl.is_trial, s.ends_at,
           l.author AS last_author, l.body AS last_body, l.has_image AS last_image, l.created_at AS last_at,
           (SELECT MIN(m.created_at) FROM support_messages m
             WHERE m.thread_id = t.id AND m.author = 'user' AND m.created_at > COALESCE(la.at, '-infinity')) AS waiting_since,
           (SELECT COUNT(*) FROM support_messages m
             WHERE m.thread_id = t.id AND m.author = 'user' AND m.created_at > t.admin_read_at) AS unread,
           (SELECT COUNT(*) FROM support_messages m WHERE m.thread_id = t.id) AS total
    FROM support_threads t
    JOIN last l ON l.thread_id = t.id
    LEFT JOIN last_admin la ON la.thread_id = t.id
    LEFT JOIN users u ON u.id = t.user_id
    LEFT JOIN subscriptions s ON s.user_id = t.user_id
    LEFT JOIN plans pl ON pl.id = s.plan_id
)
"""


def _snippet(text: str | None, has_image: bool) -> str:
    text = " ".join((text or "").split())
    if not text:
        return "Скриншот" if has_image else ""
    return text if len(text) <= SNIPPET else text[: SNIPPET - 1] + "…"


def _iso(v) -> str | None:
    return v.isoformat() if v else None


def inbox(filter: str = "open", q: str = "", page: int = 1) -> dict:
    filter = filter if filter in FILTERS else "open"
    where, params = ["TRUE"], {}
    if filter == "open":
        where.append("last_author = 'user'")
    q = (q or "").strip().lstrip("#")
    if q:
        if q.isdigit():
            where.append("user_id = %(qn)s")
            params["qn"] = int(q)
        else:
            where.append("(email ILIKE %(ql)s OR name ILIKE %(ql)s)")
            params["ql"] = f"%{q}%"
    cond = " AND ".join(where)
    with pool.connection() as conn:
        counts = conn.execute(
            _LIST + "SELECT COUNT(*) AS all, COUNT(*) FILTER (WHERE last_author = 'user') AS open, "
            "COALESCE(SUM(unread), 0) AS unread FROM base"
        ).fetchone()
        n = conn.execute(_LIST + f"SELECT COUNT(*) AS n FROM base WHERE {cond}", params).fetchone()["n"]
        pages = max(1, math.ceil(int(n) / PER_PAGE))
        page = min(max(1, int(page or 1)), pages)
        rows = conn.execute(
            _LIST
            + f"""
            SELECT * FROM base WHERE {cond}
            ORDER BY (last_author = 'user') DESC,
                     CASE WHEN last_author = 'user' THEN waiting_since END ASC NULLS LAST,
                     last_at DESC, id DESC
            LIMIT %(lim)s OFFSET %(off)s
            """,
            {**params, "lim": PER_PAGE, "off": (page - 1) * PER_PAGE},
        ).fetchall()
    return {
        "items": [
            {
                "user_id": r["user_id"],
                "email": r["email"] or "",
                "name": r["name"] or "",
                "blocked": r["blocked_at"] is not None,
                "plan": r["plan_name"] or "",
                "trial": bool(r["is_trial"]),
                "last": {
                    "author": r["last_author"],
                    "text": _snippet(r["last_body"], r["last_image"]),
                    "at": _iso(r["last_at"]),
                },
                "waiting": r["last_author"] == "user",
                "waiting_since": _iso(r["waiting_since"]) if r["last_author"] == "user" else None,
                "unread": int(r["unread"]),
                "total": int(r["total"]),
                "last_seen_at": _iso(r["last_seen_at"]),
            }
            for r in rows
        ],
        "counts": {"all": int(counts["all"]), "open": int(counts["open"]), "unread": int(counts["unread"])},
        "page": page,
        "pages": pages,
    }


def waiting_count() -> int:
    """Сколько переписок ждут ответа (последнее слово за пользователем) — для счётчика в меню."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM (
                SELECT DISTINCT ON (thread_id) author FROM support_messages ORDER BY thread_id, created_at DESC, id DESC
            ) l WHERE l.author = 'user'
            """
        ).fetchone()
    return int(row["n"])


def user_card(user_id: int) -> dict | None:
    """Шапка переписки: кто, тариф до какого дня, когда заходил, привязан ли Telegram."""
    from app import plans

    with pool.connection() as conn:
        u = conn.execute(
            "SELECT id, email, name, created_at, last_seen_at, blocked_at, telegram_chat_id FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    if u is None:
        return None
    sub = plans.subscription(user_id)
    return {
        "id": u["id"],
        "email": u["email"] or "",
        "name": u["name"] or "",
        "created_at": _iso(u["created_at"]),
        "last_seen_at": _iso(u["last_seen_at"]),
        "blocked": u["blocked_at"] is not None,
        "telegram": bool(u["telegram_chat_id"]),
        "plan": sub["plan"]["name"] if sub.get("plan") else "",
        "last_day": plans.last_day(sub.get("ends_at")),
        "expired": bool(sub.get("expired")),
    }
