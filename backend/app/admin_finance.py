"""Финансы по месяцам: выручка, возвраты, платежи и платящие, средний чек, расход на ИИ, маржа.

Платёж относится к месяцу оплаты (по Москве), возврат — к месяцу своего платежа: дат возвратов ЮKassa в базе
нет. Тестовые не считаются. Расход на ИИ — вся себестоимость ai_usage, вместе с лендингом.
«Новый» платёж — первый успешный у человека, остальные — повторные.
"""

from __future__ import annotations

from datetime import date

from app.clock import MSK, now_msk
from app.db import pool

RANGES = {"6": 6, "12": 12, "24": 24, "all": 0}
_TZ = "Europe/Moscow"
_MONTH = f"date_trunc('month', COALESCE(p.paid_at, p.created_at) AT TIME ZONE '{_TZ}')::date"
_OK = "p.status = 'succeeded' AND NOT p.test"


def _months(first: date, last: date) -> list[date]:
    out, d = [], first.replace(day=1)
    while d <= last:
        out.append(d)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def _r(v) -> float:
    return round(float(v or 0), 2)


def build(range_: str = "12", now=None) -> dict:
    now = now or now_msk()
    this_month = now.astimezone(MSK).date().replace(day=1)
    n = RANGES.get(range_, 12)
    with pool.connection() as conn:
        if n:
            first = this_month
            for _ in range(n - 1):
                first = date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
        else:
            row = conn.execute(
                f"""
                SELECT LEAST((SELECT MIN({_MONTH}) FROM payments p WHERE {_OK}),
                             (SELECT MIN(date_trunc('month', created_at AT TIME ZONE '{_TZ}')::date) FROM ai_usage)) AS f
                """
            ).fetchone()
            first = row["f"] or this_month
        start = first
        pay = {
            r["m"]: r
            for r in conn.execute(
                f"""
                WITH firsts AS (
                    SELECT user_id, MIN(id) AS first_id FROM payments p WHERE {_OK} GROUP BY user_id
                )
                SELECT {_MONTH} AS m,
                       SUM(p.amount_rub) AS gross, SUM(p.refunded_rub) AS refunded,
                       COUNT(*) AS payments, COUNT(DISTINCT p.user_id) AS payers,
                       COUNT(*) FILTER (WHERE p.id = f.first_id) AS new_payments,
                       SUM(p.amount_rub - p.refunded_rub) FILTER (WHERE p.id = f.first_id) AS new_rub
                FROM payments p LEFT JOIN firsts f ON f.user_id = p.user_id
                WHERE {_OK} AND {_MONTH} >= %(s)s
                GROUP BY 1
                """,
                {"s": start},
            ).fetchall()
        }
        ai = {
            r["m"]: r
            for r in conn.execute(
                f"""
                SELECT date_trunc('month', created_at AT TIME ZONE '{_TZ}')::date AS m,
                       SUM(cost_rub) AS cost, SUM(cost_rub) FILTER (WHERE source = 'landing') AS landing
                FROM ai_usage WHERE created_at >= %(s)s GROUP BY 1
                """,
                {"s": start},
            ).fetchall()
        }
        by_plan = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(p.plan_name, ''), 'без тарифа') AS plan, COUNT(*) AS payments,
                   SUM(p.amount_rub - p.refunded_rub) AS rub
            FROM payments p WHERE {_OK} AND {_MONTH} >= %(s)s GROUP BY 1 ORDER BY rub DESC
            """,
            {"s": start},
        ).fetchall()
        by_term = conn.execute(
            f"""
            SELECT COALESCE(p.months, 1) AS months, COUNT(*) AS payments, SUM(p.amount_rub - p.refunded_rub) AS rub
            FROM payments p WHERE {_OK} AND {_MONTH} >= %(s)s GROUP BY 1 ORDER BY 1
            """,
            {"s": start},
        ).fetchall()
    rows = []
    for m in _months(start, this_month):
        pr, ar = pay.get(m) or {}, ai.get(m) or {}
        gross, refunded = _r(pr.get("gross")), _r(pr.get("refunded"))
        net = round(gross - refunded, 2)
        cost = _r(ar.get("cost"))
        count = int(pr.get("payments") or 0)
        new = int(pr.get("new_payments") or 0)
        rows.append(
            {
                "month": m.isoformat(),
                "revenue_rub": gross,
                "refunded_rub": refunded,
                "net_rub": net,
                "payments": count,
                "payers": int(pr.get("payers") or 0),
                "avg_rub": round(gross / count, 2) if count else None,
                "ai_rub": cost,
                "ai_landing_rub": _r(ar.get("landing")),
                "margin_rub": round(net - cost, 2),
                "margin_pct": round((net - cost) / net * 100, 1) if net > 0 else None,
                "new_payments": new,
                "repeat_payments": count - new,
                "new_rub": _r(pr.get("new_rub")),
            }
        )
    total_net = sum(r["net_rub"] for r in rows)
    total_ai = sum(r["ai_rub"] for r in rows)
    total_gross = sum(r["revenue_rub"] for r in rows)
    total_count = sum(r["payments"] for r in rows)
    plan_total = sum(float(r["rub"] or 0) for r in by_plan) or 0
    return {
        "range": range_ if range_ in RANGES else "12",
        "from": start.isoformat(),
        "current_month": this_month.isoformat(),
        "months": rows,
        "total": {
            "revenue_rub": round(total_gross, 2),
            "refunded_rub": round(sum(r["refunded_rub"] for r in rows), 2),
            "net_rub": round(total_net, 2),
            "payments": total_count,
            "avg_rub": round(total_gross / total_count, 2) if total_count else None,
            "ai_rub": round(total_ai, 2),
            "margin_rub": round(total_net - total_ai, 2),
            "margin_pct": round((total_net - total_ai) / total_net * 100, 1) if total_net > 0 else None,
            "new_payments": sum(r["new_payments"] for r in rows),
            "repeat_payments": sum(r["repeat_payments"] for r in rows),
        },
        "by_plan": [
            {
                "plan": r["plan"],
                "payments": int(r["payments"]),
                "rub": _r(r["rub"]),
                "share": round(float(r["rub"] or 0) / plan_total * 100, 1) if plan_total else None,
            }
            for r in by_plan
        ],
        "by_term": [{"months": int(r["months"]), "payments": int(r["payments"]), "rub": _r(r["rub"])} for r in by_term],
    }
