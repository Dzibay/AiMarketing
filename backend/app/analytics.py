"""Аналитика и сводка рабочего пространства: кампании пространства (Директ) + счётчик сайта (Метрика).

Считается из stats_daily — данные подтягивают задачи stats_day / stats_range. Конверсии берутся из
основной цели пространства (Метрика), если она задана и по ней есть данные, иначе — из Директа.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.journal import list_action_logs
from app.clock import today_msk
from app.db import pool
from app.overview import SEVERITY_ORDER, build_overview, kpi_state
from app.stats import structure_tops
from app.strategy_info import budget_brief
from app.workspaces import require_workspace
from app.yandex import YandexError

MAX_RANGE_DAYS = 366

_DIRECT_KEYS = ("cost_kopecks", "impressions", "clicks", "conversions", "revenue_kopecks")


def parse_range(date_from: str, date_to: str, *, default_days: int = 7) -> tuple[date, date]:
    """Период отчёта; по умолчанию — последние default_days полных дней."""
    yesterday = today_msk() - timedelta(days=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else yesterday
        d_from = date.fromisoformat(date_from) if date_from else d_to - timedelta(days=default_days - 1)
    except ValueError as exc:
        raise YandexError("date", "Неверная дата") from exc
    if d_from > d_to or (d_to - d_from).days + 1 > MAX_RANGE_DAYS:
        raise YandexError("date", "Период — от 1 до 366 дней")
    return d_from, d_to


def _metrics(s: dict, *, use_goal: bool) -> dict:
    cost = int(s.get("cost_kopecks") or 0)
    clicks = int(s.get("clicks") or 0)
    impressions = int(s.get("impressions") or 0)
    revenue = int(s.get("revenue_kopecks") or 0)
    direct_conv = int(s.get("conversions") or 0)
    goal_conv = int(s.get("goal_reaches") or 0)
    conv = goal_conv if use_goal else direct_conv
    return {
        "cost_kopecks": cost,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        "cpc_kopecks": round(cost / clicks) if clicks else None,
        "conversions": conv,
        "direct_conversions": direct_conv,
        "goal_reaches": goal_conv,
        "cr": round(conv / clicks * 100, 2) if clicks else None,
        "cpa_kopecks": round(cost / conv) if conv else None,
        "revenue_kopecks": revenue,
        "drr": round(cost / revenue * 100, 1) if revenue else None,
        "visits": int(s.get("visits") or 0),
        "users": int(s.get("users_count") or 0),
    }


def _delta(cur, prev) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / prev * 100, 1)


def _workspace_campaigns(user_id: int, workspace_id: int) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.state, c.status, c.campaign_type,
                   c.daily_budget_kopecks, c.bidding_strategy, a.client_login, a.name AS account_name
            FROM workspace_campaigns wc
            JOIN direct_campaigns c ON c.id = wc.campaign_row_id
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE wc.workspace_id = %s AND wc.user_id = %s
            ORDER BY c.name, c.id
            """,
            (workspace_id, user_id),
        ).fetchall()


def _load_stats(user_id: int, ws: dict, date_from: date, date_to: date) -> dict:
    with pool.connection() as conn:
        direct = conn.execute(
            """
            SELECT s.day, c.id AS campaign_row_id, s.cost_kopecks, s.impressions, s.clicks,
                   s.conversions, s.revenue_kopecks
            FROM stats_daily s
            JOIN direct_campaigns c ON c.account_id = s.account_id AND c.campaign_id = s.campaign_id
            JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
            WHERE s.user_id = %s AND s.source = 'direct' AND wc.workspace_id = %s
              AND s.day >= %s AND s.day <= %s
            """,
            (user_id, ws["id"], date_from, date_to),
        ).fetchall()
        metrika = []
        goal = []
        if ws["counter_row_id"]:
            metrika = conn.execute(
                """
                SELECT day, visits, users_count, bounce_rate, goal_reaches
                FROM stats_daily
                WHERE user_id = %s AND source = 'metrika' AND counter_row_id = %s
                  AND day >= %s AND day <= %s
                """,
                (user_id, ws["counter_row_id"], date_from, date_to),
            ).fetchall()
            if ws["goal_id"]:
                goal = conn.execute(
                    """
                    SELECT day, campaign_id, goal_reaches
                    FROM stats_daily
                    WHERE user_id = %s AND source = 'metrika_goal' AND counter_row_id = %s
                      AND goal_id = %s AND day >= %s AND day <= %s
                    """,
                    (user_id, ws["counter_row_id"], ws["goal_id"], date_from, date_to),
                ).fetchall()
        fresh = conn.execute(
            """
            SELECT MAX(s.updated_at) AS direct_at,
                   (SELECT MAX(updated_at) FROM stats_daily
                     WHERE user_id = %s AND source = 'metrika' AND counter_row_id = %s) AS metrika_at
            FROM stats_daily s
            JOIN direct_campaigns c ON c.account_id = s.account_id AND c.campaign_id = s.campaign_id
            JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
            WHERE s.user_id = %s AND s.source = 'direct' AND wc.workspace_id = %s
            """,
            (user_id, ws["counter_row_id"], user_id, ws["id"]),
        ).fetchone()
    return {"direct": direct, "metrika": metrika, "goal": goal, "fresh": fresh or {}}


def workspace_analytics(
    user_id: int,
    workspace_id: int,
    *,
    date_from: date,
    date_to: date,
    with_tops: bool = True,
) -> dict:
    ws = require_workspace(user_id, workspace_id)
    days = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    campaigns = _workspace_campaigns(user_id, workspace_id)
    data = _load_stats(user_id, ws, prev_from, date_to)

    by_campaign_id = {int(c["campaign_id"]): int(c["id"]) for c in campaigns}
    cur: dict = defaultdict(int)
    prev: dict = defaultdict(int)
    day_sums: dict[date, dict] = defaultdict(lambda: defaultdict(int))
    camp_sums: dict[int, dict] = defaultdict(lambda: defaultdict(int))

    for r in data["direct"]:
        target = cur if r["day"] >= date_from else prev
        for key in _DIRECT_KEYS:
            target[key] += int(r[key] or 0)
        for key in _DIRECT_KEYS:
            day_sums[r["day"]][key] += int(r[key] or 0)
        if r["day"] >= date_from:
            for key in _DIRECT_KEYS:
                camp_sums[int(r["campaign_row_id"])][key] += int(r[key] or 0)

    bounce_num = 0.0
    for r in data["metrika"]:
        target = cur if r["day"] >= date_from else prev
        target["visits"] += int(r["visits"] or 0)
        target["users_count"] += int(r["users_count"] or 0)
        day_sums[r["day"]]["visits"] += int(r["visits"] or 0)
        if r["day"] >= date_from:
            if r["bounce_rate"] is not None:
                bounce_num += float(r["bounce_rate"]) * int(r["visits"] or 0)

    # Цель пространства: итог по сайту (campaign_id NULL) и по кампаниям Директа.
    goal_rows = 0
    site_goal: dict = defaultdict(int)
    for r in data["goal"]:
        goal_rows += 1
        reaches = int(r["goal_reaches"] or 0)
        if r["campaign_id"] is None:
            site_goal["cur" if r["day"] >= date_from else "prev"] += reaches
            continue
        row_id = by_campaign_id.get(int(r["campaign_id"]))
        if row_id is None:
            continue  # переходы из кампаний вне пространства
        target = cur if r["day"] >= date_from else prev
        target["goal_reaches"] += reaches
        day_sums[r["day"]]["goal_reaches"] += reaches
        if r["day"] >= date_from:
            camp_sums[row_id]["goal_reaches"] += reaches
    use_goal = bool(ws["goal_id"]) and goal_rows > 0

    totals = _metrics(cur, use_goal=use_goal)
    totals["bounce_rate"] = round(bounce_num / totals["visits"], 1) if totals["visits"] else None
    totals["site_goal_reaches"] = site_goal["cur"]
    previous = _metrics(prev, use_goal=use_goal)
    previous["site_goal_reaches"] = site_goal["prev"]
    compare = {
        key: _delta(totals[key], previous[key])
        for key in (
            "cost_kopecks",
            "clicks",
            "impressions",
            "ctr",
            "cpc_kopecks",
            "conversions",
            "cpa_kopecks",
            "revenue_kopecks",
            "drr",
            "visits",
        )
    }

    def days_series(start: date, end: date) -> list[dict]:
        out = []
        d = start
        while d <= end:
            m = _metrics(day_sums.get(d, {}), use_goal=use_goal)
            out.append(
                {
                    "day": d.isoformat(),
                    "cost_kopecks": m["cost_kopecks"],
                    "clicks": m["clicks"],
                    "impressions": m["impressions"],
                    "conversions": m["conversions"],
                    "cpa_kopecks": m["cpa_kopecks"],
                    "revenue_kopecks": m["revenue_kopecks"],
                    "visits": m["visits"],
                }
            )
            d += timedelta(days=1)
        return out

    by_day = days_series(date_from, date_to)
    # Тот же ряд за предыдущий период той же длины — пунктир «было» на графиках.
    prev_by_day = days_series(prev_from, prev_to)

    by_campaign = []
    accounts: dict[int, dict] = {}
    acc_sums: dict[int, dict] = defaultdict(lambda: defaultdict(int))
    for c in campaigns:
        s = camp_sums.get(int(c["id"]), {})
        m = _metrics(s, use_goal=use_goal)
        budget = c.get("daily_budget_kopecks")
        by_campaign.append(
            {
                "campaign_row_id": c["id"],
                "campaign_id": c["campaign_id"],
                "name": c["name"],
                "state": c["state"],
                "status": c["status"],
                "type": c.get("campaign_type") or "",
                "account_id": c["account_id"],
                "account_login": c["client_login"],
                "daily_budget_rub": round(budget / 100, 2) if budget else None,
                **budget_brief(c.get("bidding_strategy")),
                **m,
            }
        )
        accounts.setdefault(
            int(c["account_id"]),
            {"account_id": c["account_id"], "login": c["client_login"], "name": c["account_name"] or ""},
        )
        for key, value in s.items():
            acc_sums[int(c["account_id"])][key] += value
    by_campaign.sort(key=lambda r: (-r["cost_kopecks"], r["name"]))
    by_account = []
    for acc_id, meta in accounts.items():
        m = _metrics(acc_sums.get(acc_id, {}), use_goal=use_goal)
        by_account.append({**meta, "campaigns": sum(1 for c in campaigns if int(c["account_id"]) == acc_id), **m})
    by_account.sort(key=lambda r: -r["cost_kopecks"])

    # KPI пространства, иначе — клиента-папки.
    kpi_type, kpi_target = ws["kpi_type"], ws["kpi_target"]
    if not kpi_type:
        kpi_type, kpi_target = ws.get("client_kpi_type") or "", ws.get("client_kpi_target")
    kpi = None
    if kpi_type:
        kpi = kpi_state(
            kpi_type,
            float(kpi_target) if kpi_target is not None else None,
            totals["cost_kopecks"],
            totals["conversions"],
            totals["revenue_kopecks"],
        )

    fresh = data["fresh"]
    out = {
        "workspace_id": workspace_id,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "prev_from": prev_from.isoformat(),
        "prev_to": prev_to.isoformat(),
        "conversions_source": "goal" if use_goal else "direct",
        "goal_id": ws["goal_id"],
        "goal_name": ws.get("goal_name") or "",
        "counter_ok": bool(ws["counter_row_id"]) and bool(ws.get("counter_enabled")),
        "totals": totals,
        "previous": previous,
        "compare": compare,
        "kpi": kpi,
        "by_day": by_day,
        "prev_by_day": prev_by_day,
        "by_campaign": by_campaign,
        "by_account": by_account,
        "direct_updated_at": fresh["direct_at"].isoformat() if fresh.get("direct_at") else None,
        "metrika_updated_at": fresh["metrika_at"].isoformat() if fresh.get("metrika_at") else None,
    }
    if with_tops:
        ids = [int(c["id"]) for c in campaigns]
        out["tops"] = structure_tops(user_id, campaign_row_ids=ids) if ids else {
            "keywords": [],
            "adgroups": [],
            "queries": [],
            "note": "",
        }
    return out


def workspace_summary(user_id: int, workspace_id: int) -> dict:
    """Главная вкладка пространства: строка сводки, проблемы, неделя к неделе, последние действия."""
    require_workspace(user_id, workspace_id)
    overview = build_overview(user_id)
    row = next((r for r in overview["workspaces"] if r["id"] == workspace_id), None)
    problems = [
        p
        for p in overview["problems"]
        if p.get("workspace_id") == workspace_id or workspace_id in (p.get("workspace_ids") or [])
    ]
    problems.sort(key=lambda p: (SEVERITY_ORDER.get(p["severity"], 9), p.get("object_name") or ""))
    yesterday = date.fromisoformat(overview["today"]) - timedelta(days=1)
    week = workspace_analytics(
        user_id, workspace_id, date_from=yesterday - timedelta(days=6), date_to=yesterday, with_tops=False
    )
    return {
        "today": overview["today"],
        "today_updated_at": overview.get("today_updated_at"),
        "row": row,
        "problems": problems,
        "week": {
            "date_from": week["date_from"],
            "date_to": week["date_to"],
            "totals": week["totals"],
            "previous": week["previous"],
            "compare": week["compare"],
            "kpi": week["kpi"],
            "by_day": week["by_day"],
            "prev_by_day": week["prev_by_day"],
            "conversions_source": week["conversions_source"],
            "goal_name": week["goal_name"],
        },
        "top_campaigns": week["by_campaign"][:5],
        "recent_actions": list_action_logs(user_id, 10, workspace_id=workspace_id),
    }
