"""Платежи в админке: поиск по человеку и номеру, фильтры, итоги по выборке, страницы.

Итоги считаются по всей выборке, не по странице: сколько платежей, сколько пришло денег (оплаченные минус
возвраты), средний чек оплаченных и возвраты. Карточки сверху (месяц, всего, без чека) — как раньше,
из payments.admin_list.
"""

from __future__ import annotations

import math

from app import admin_dashboard as dash
from app import payments
from app.clock import now_msk
from app.db import pool

PER_PAGE = 50
STATUSES = ("paid", "refunded", "pending", "canceled", "error")
TESTS = ("hide", "only", "all")
TERMS = (1, 3, 12)


def _filters(q: str, status: str, plan: str, months: int, no_receipt: bool, test: str, per: dict) -> tuple[list[str], dict]:
    where = ["p.status <> 'new'", "p.created_at >= %(s)s", "p.created_at < %(e)s"]
    params: dict = {"s": per["start"], "e": per["end"]}
    q = (q or "").strip()
    if q:
        term = q.lstrip("#№ ")
        if term.isdigit():
            # Число — номер платежа (ссылка из уведомления в Telegram — ?q=номер) или кусок id в ЮKassa.
            where.append("(p.id = %(qn)s OR p.provider_id ILIKE %(ql)s)")
            params.update(qn=int(term), ql=f"%{term}%")
        else:
            where.append("(u.email ILIKE %(ql)s OR p.provider_id ILIKE %(ql)s)")
            params["ql"] = f"%{q}%"
    if status == "paid":
        where.append("p.status = 'succeeded'")
    elif status == "refunded":
        where.append("p.refunded_rub > 0")
    elif status == "pending":
        where.append("p.status IN ('pending', 'waiting_for_capture')")
    elif status in ("canceled", "error"):
        where.append("p.status = %(st)s")
        params["st"] = status
    if plan:
        where.append("p.plan_name = %(plan)s")
        params["plan"] = plan
    if months in TERMS:
        where.append("COALESCE(p.months, 1) = %(months)s")
        params["months"] = months
    if no_receipt:
        where.append("p.status = 'succeeded' AND NOT p.test AND NOT p.receipt_done AND p.refunded_rub < p.amount_rub")
    if test == "hide":
        where.append("NOT p.test")
    elif test == "only":
        where.append("p.test")
    return where, params


def build(
    q: str = "",
    status: str = "",
    plan: str = "",
    months: int = 0,
    no_receipt: bool = False,
    test: str = "hide",
    p: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    now=None,
) -> dict:
    now = now or now_msk()
    per = dash.period(p, date_from, date_to, "", now)
    status = status if status in STATUSES else ""
    test = test if test in TESTS else "hide"
    where, params = _filters(q, status, plan, int(months or 0), no_receipt, test, per)
    sql_where = " AND ".join(where)
    with pool.connection() as conn:
        tot = conn.execute(
            f"""
            SELECT COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE p.status = 'succeeded') AS paid,
                   COALESCE(SUM(p.amount_rub) FILTER (WHERE p.status = 'succeeded'), 0) AS gross,
                   COALESCE(SUM(p.refunded_rub), 0) AS refunded,
                   COUNT(*) FILTER (WHERE p.refunded_rub > 0) AS refunds
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE {sql_where}
            """,
            params,
        ).fetchone()
        pages = max(1, math.ceil(int(tot["n"]) / PER_PAGE))
        page = min(max(1, int(page or 1)), pages)
        rows = conn.execute(
            f"""
            SELECT p.*, u.email FROM payments p LEFT JOIN users u ON u.id = p.user_id
            WHERE {sql_where}
            ORDER BY p.created_at DESC, p.id DESC LIMIT %(lim)s OFFSET %(off)s
            """,
            {**params, "lim": PER_PAGE, "off": (page - 1) * PER_PAGE},
        ).fetchall()
        plan_names = [
            r["plan_name"]
            for r in conn.execute(
                "SELECT DISTINCT plan_name FROM payments WHERE status <> 'new' AND COALESCE(plan_name, '') <> '' ORDER BY 1"
            ).fetchall()
        ]
    paid = int(tot["paid"])
    gross = float(tot["gross"])
    refunded = float(tot["refunded"])
    return {
        "items": [payments._admin(r) for r in rows],
        "total": {
            "count": int(tot["n"]),
            "paid": paid,
            "sum_rub": round(gross - refunded, 2),
            "avg_rub": round(gross / paid, 2) if paid else None,
            "refunded_rub": refunded,
            "refunds": int(tot["refunds"]),
        },
        "summary": payments.admin_list(limit=1)["summary"],
        "options": {"plans": plan_names},
        "period": dash._public_range({k: v for k, v in per.items() if k != "prev"}),
        "page": page,
        "pages": pages,
    }
