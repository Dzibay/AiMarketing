"""Сбор и агрегация дневной статистики Директ + Метрика."""

from __future__ import annotations

import csv
import io
import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.db import pool
from app.metrika_sync import sync_metrika_stats_for_user
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.stats")

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"


def _rub_to_kopecks(raw) -> int:
    try:
        return int((Decimal(str(raw or 0)) * Decimal(100)).quantize(Decimal("1")))
    except Exception:
        return 0


def upsert_direct_stat(
    *,
    user_id: int,
    day: date,
    account_id: int,
    campaign_id: int | None,
    impressions: int,
    clicks: int,
    cost_kopecks: int,
    conversions: int,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            DELETE FROM stats_daily
            WHERE user_id = %s AND day = %s AND source = 'direct'
              AND account_id = %s
              AND COALESCE(campaign_id, 0) = COALESCE(%s, 0)
            """,
            (user_id, day, account_id, campaign_id),
        )
        conn.execute(
            """
            INSERT INTO stats_daily (
                user_id, day, source, account_id, campaign_id,
                impressions, clicks, cost_kopecks, conversions, updated_at
            )
            VALUES (%s, %s, 'direct', %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                user_id,
                day,
                account_id,
                campaign_id,
                impressions,
                clicks,
                cost_kopecks,
                conversions,
            ),
        )


def fetch_direct_campaign_report(access_token: str, client_login: str, day: date) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
        "Client-Login": client_login,
        "returnMoneyInMicros": "false",
        "skipReportHeader": "true",
        "skipColumnHeader": "false",
        "skipReportSummary": "true",
    }
    body = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": day.isoformat(),
                "DateTo": day.isoformat(),
            },
            "FieldNames": [
                "Date",
                "CampaignId",
                "Impressions",
                "Clicks",
                "Cost",
                "Conversions",
            ],
            "ReportName": f"aimarketing_{client_login}_{day.isoformat()}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
            "IncludeDiscount": "NO",
        }
    }
    for _attempt in range(8):
        try:
            with httpx.Client(timeout=90.0) as client:
                response = client.post(REPORTS_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.warning("reports: %s", exc)
            raise YandexError("direct") from exc
        if response.status_code == 200:
            break
        if response.status_code in (201, 202):
            time.sleep(2)
            continue
        log.warning("reports HTTP %s %s", response.status_code, response.text[:400])
        raise YandexError("direct-denied" if response.status_code in (400, 403) else "direct")
    else:
        raise YandexError("direct")

    text = response.text.strip()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = []
    for item in reader:
        try:
            cid = int(item.get("CampaignId") or 0)
        except ValueError:
            continue
        if not cid:
            continue
        rows.append(
            {
                "campaign_id": cid,
                "impressions": int(Decimal(str(item.get("Impressions") or 0))),
                "clicks": int(Decimal(str(item.get("Clicks") or 0))),
                "cost_kopecks": _rub_to_kopecks(item.get("Cost")),
                "conversions": int(Decimal(str(item.get("Conversions") or 0))),
            }
        )
    return rows


def sync_direct_stats_for_user(user_id: int, day: date | None = None) -> dict:
    day = day or (date.today() - timedelta(days=1))
    with pool.connection() as conn:
        accounts = conn.execute(
            """
            SELECT id, connection_id, client_login
            FROM direct_accounts
            WHERE user_id = %s AND enabled = TRUE
            """,
            (user_id,),
        ).fetchall()
    synced = 0
    errors = 0
    for account in accounts:
        try:
            token = get_connection_access_token(account["connection_id"], user_id)
            rows = fetch_direct_campaign_report(token, account["client_login"], day)
            for row in rows:
                upsert_direct_stat(
                    user_id=user_id,
                    day=day,
                    account_id=account["id"],
                    campaign_id=row["campaign_id"],
                    impressions=row["impressions"],
                    clicks=row["clicks"],
                    cost_kopecks=row["cost_kopecks"],
                    conversions=row["conversions"],
                )
            synced += 1
        except Exception:
            log.exception("direct stats account %s", account["id"])
            errors += 1
    return {"day": day.isoformat(), "synced": synced, "errors": errors}


def sync_stats_for_user(user_id: int, day: date | None = None) -> dict:
    day = day or (date.today() - timedelta(days=1))
    run_id = start_run(user_id=user_id, kind="stats_daily", target_id=None)
    try:
        direct = sync_direct_stats_for_user(user_id, day)
        metrika = sync_metrika_stats_for_user(user_id, day)
        result = {"day": day.isoformat(), "direct": direct, "metrika": metrika}
        finish_run(run_id, status="ok", meta=result)
        return result
    except Exception as exc:
        finish_run(run_id, status="error", error=str(exc)[:200])
        raise


def sync_stats_range(
    user_id: int,
    *,
    date_from: date,
    date_to: date,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Бэкфилл статистики по дням (включительно). Максимум 30 дней.

    on_progress(готово, всего, подпись) вызывается перед каждым днём — для прогресса задачи.
    """
    if date_from > date_to:
        raise YandexError("date")
    span = (date_to - date_from).days + 1
    if span > 30:
        raise YandexError("date")
    run_id = start_run(user_id=user_id, kind="stats_backfill", target_id=None)
    days: list[dict] = []
    errors = 0
    try:
        day = date_from
        while day <= date_to:
            if on_progress:
                on_progress(len(days), span, day.isoformat())
            try:
                direct = sync_direct_stats_for_user(user_id, day)
                metrika = sync_metrika_stats_for_user(user_id, day)
                days.append({"day": day.isoformat(), "direct": direct, "metrika": metrika})
            except Exception as exc:
                log.exception("stats backfill %s", day)
                errors += 1
                days.append({"day": day.isoformat(), "error": str(exc)[:200]})
            day += timedelta(days=1)
        result = {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "days_count": len(days),
            "errors": errors,
            "days": days,
        }
        finish_run(
            run_id,
            status="ok" if errors == 0 else "partial",
            meta={
                "date_from": result["date_from"],
                "date_to": result["date_to"],
                "days_count": result["days_count"],
                "errors": errors,
            },
        )
        return result
    except Exception as exc:
        finish_run(run_id, status="error", error=str(exc)[:200])
        raise


def analytics_summary(
    user_id: int,
    *,
    date_from: date,
    date_to: date,
    account_id: int | None = None,
    goal_row_id: int | None = None,
) -> dict:
    yandex_goal_id: int | None = None
    goal_name = ""
    if goal_row_id:
        with pool.connection() as conn:
            g = conn.execute(
                "SELECT goal_id, name FROM metrika_goals WHERE id = %s AND user_id = %s",
                (goal_row_id, user_id),
            ).fetchone()
        if g:
            yandex_goal_id = int(g["goal_id"])
            goal_name = g.get("name") or ""

    with pool.connection() as conn:
        links_count = conn.execute(
            "SELECT COUNT(*) AS n FROM account_counter_links WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        linked_n = int(links_count["n"] or 0)

        if account_id is not None:
            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.cost_kopecks ELSE 0 END), 0) AS cost_kopecks,
                    COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.clicks ELSE 0 END), 0) AS clicks,
                    COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.impressions ELSE 0 END), 0) AS impressions,
                    COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.conversions ELSE 0 END), 0) AS conversions,
                    COALESCE(SUM(CASE WHEN s.source = 'metrika' THEN s.visits ELSE 0 END), 0) AS visits,
                    COALESCE(SUM(CASE WHEN s.source = 'metrika' THEN s.goal_reaches ELSE 0 END), 0) AS goal_reaches
                FROM stats_daily s
                WHERE s.user_id = %s AND s.day >= %s AND s.day <= %s
                  AND (
                    (s.source = 'direct' AND s.account_id = %s)
                    OR (
                      s.source = 'metrika'
                      AND s.counter_row_id IN (
                        SELECT counter_row_id FROM account_counter_links
                        WHERE user_id = %s AND account_id = %s
                      )
                    )
                  )
                """,
                (user_id, date_from, date_to, account_id, user_id, account_id),
            ).fetchone()
        else:
            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN source = 'direct' THEN cost_kopecks ELSE 0 END), 0) AS cost_kopecks,
                    COALESCE(SUM(CASE WHEN source = 'direct' THEN clicks ELSE 0 END), 0) AS clicks,
                    COALESCE(SUM(CASE WHEN source = 'direct' THEN impressions ELSE 0 END), 0) AS impressions,
                    COALESCE(SUM(CASE WHEN source = 'direct' THEN conversions ELSE 0 END), 0) AS conversions,
                    COALESCE(SUM(CASE WHEN source = 'metrika' THEN visits ELSE 0 END), 0) AS visits,
                    COALESCE(SUM(CASE WHEN source = 'metrika' THEN goal_reaches ELSE 0 END), 0) AS goal_reaches
                FROM stats_daily
                WHERE user_id = %s AND day >= %s AND day <= %s
                """,
                (user_id, date_from, date_to),
            ).fetchone()

        selected_goal_reaches = 0
        if yandex_goal_id is not None:
            if account_id is not None:
                sg = conn.execute(
                    """
                    SELECT COALESCE(SUM(s.goal_reaches), 0) AS goal_reaches
                    FROM stats_daily s
                    JOIN account_counter_links l
                      ON l.counter_row_id = s.counter_row_id AND l.user_id = s.user_id
                    WHERE s.user_id = %s AND s.source = 'metrika_goal'
                      AND s.goal_id = %s AND s.campaign_id IS NULL
                      AND s.day >= %s AND s.day <= %s
                      AND l.account_id = %s
                    """,
                    (user_id, yandex_goal_id, date_from, date_to, account_id),
                ).fetchone()
            else:
                sg = conn.execute(
                    """
                    SELECT COALESCE(SUM(goal_reaches), 0) AS goal_reaches
                    FROM stats_daily
                    WHERE user_id = %s AND source = 'metrika_goal'
                      AND goal_id = %s AND campaign_id IS NULL
                      AND day >= %s AND day <= %s
                    """,
                    (user_id, yandex_goal_id, date_from, date_to),
                ).fetchone()
            selected_goal_reaches = int((sg or {}).get("goal_reaches") or 0)

        by_account = conn.execute(
            """
            WITH direct_agg AS (
                SELECT account_id,
                       COALESCE(SUM(cost_kopecks), 0) AS cost_kopecks,
                       COALESCE(SUM(clicks), 0) AS clicks,
                       COALESCE(SUM(conversions), 0) AS conversions
                FROM stats_daily
                WHERE user_id = %s AND source = 'direct'
                  AND day >= %s AND day <= %s
                GROUP BY account_id
            ),
            metrika_agg AS (
                SELECT l.account_id,
                       COALESCE(SUM(m.visits), 0) AS visits,
                       COALESCE(SUM(m.goal_reaches), 0) AS goal_reaches
                FROM account_counter_links l
                JOIN stats_daily m
                  ON m.counter_row_id = l.counter_row_id
                 AND m.source = 'metrika'
                 AND m.user_id = %s
                 AND m.day >= %s AND m.day <= %s
                WHERE l.user_id = %s
                GROUP BY l.account_id
            ),
            link_counts AS (
                SELECT account_id, COUNT(*) AS links
                FROM account_counter_links
                WHERE user_id = %s
                GROUP BY account_id
            )
            SELECT a.id, a.name, a.client_login,
                   COALESCE(d.cost_kopecks, 0) AS cost_kopecks,
                   COALESCE(d.clicks, 0) AS clicks,
                   COALESCE(d.conversions, 0) AS conversions,
                   COALESCE(m.visits, 0) AS visits,
                   COALESCE(m.goal_reaches, 0) AS goal_reaches,
                   COALESCE(lc.links, 0) AS links
            FROM direct_accounts a
            LEFT JOIN direct_agg d ON d.account_id = a.id
            LEFT JOIN metrika_agg m ON m.account_id = a.id
            LEFT JOIN link_counts lc ON lc.account_id = a.id
            WHERE a.user_id = %s AND a.enabled = TRUE
              AND (%s::bigint IS NULL OR a.id = %s)
            ORDER BY cost_kopecks DESC
            """,
            (
                user_id,
                date_from,
                date_to,
                user_id,
                date_from,
                date_to,
                user_id,
                user_id,
                user_id,
                account_id,
                account_id,
            ),
        ).fetchall()

        by_campaign = conn.execute(
            """
            SELECT s.campaign_id, c.name, s.account_id, a.client_login,
                   COALESCE(SUM(s.cost_kopecks), 0) AS cost_kopecks,
                   COALESCE(SUM(s.clicks), 0) AS clicks,
                   COALESCE(SUM(s.conversions), 0) AS conversions
            FROM stats_daily s
            LEFT JOIN direct_campaigns c
              ON c.account_id = s.account_id AND c.campaign_id = s.campaign_id
            LEFT JOIN direct_accounts a ON a.id = s.account_id
            WHERE s.user_id = %s AND s.source = 'direct'
              AND s.day >= %s AND s.day <= %s
              AND (%s::bigint IS NULL OR s.account_id = %s)
            GROUP BY s.campaign_id, c.name, s.account_id, a.client_login
            ORDER BY cost_kopecks DESC
            LIMIT 100
            """,
            (user_id, date_from, date_to, account_id, account_id),
        ).fetchall()

        if account_id is not None:
            by_day_rows = conn.execute(
                """
                SELECT d.day::date AS day,
                       COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.cost_kopecks ELSE 0 END), 0) AS cost_kopecks,
                       COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.clicks ELSE 0 END), 0) AS clicks,
                       COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.conversions ELSE 0 END), 0) AS conversions,
                       COALESCE(SUM(CASE WHEN s.source = 'metrika' THEN s.visits ELSE 0 END), 0) AS visits,
                       COALESCE(SUM(CASE WHEN s.source = 'metrika' THEN s.goal_reaches ELSE 0 END), 0) AS goal_reaches
                FROM generate_series(%s::date, %s::date, '1 day'::interval) AS d(day)
                LEFT JOIN stats_daily s
                  ON s.user_id = %s AND s.day = d.day
                 AND (
                   (s.source = 'direct' AND s.account_id = %s)
                   OR (
                     s.source = 'metrika'
                     AND s.counter_row_id IN (
                       SELECT counter_row_id FROM account_counter_links
                       WHERE user_id = %s AND account_id = %s
                     )
                   )
                 )
                GROUP BY d.day
                ORDER BY d.day ASC
                """,
                (date_from, date_to, user_id, account_id, user_id, account_id),
            ).fetchall()
        else:
            by_day_rows = conn.execute(
                """
                SELECT d.day::date AS day,
                       COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.cost_kopecks ELSE 0 END), 0) AS cost_kopecks,
                       COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.clicks ELSE 0 END), 0) AS clicks,
                       COALESCE(SUM(CASE WHEN s.source = 'direct' THEN s.conversions ELSE 0 END), 0) AS conversions,
                       COALESCE(SUM(CASE WHEN s.source = 'metrika' THEN s.visits ELSE 0 END), 0) AS visits,
                       COALESCE(SUM(CASE WHEN s.source = 'metrika' THEN s.goal_reaches ELSE 0 END), 0) AS goal_reaches
                FROM generate_series(%s::date, %s::date, '1 day'::interval) AS d(day)
                LEFT JOIN stats_daily s
                  ON s.user_id = %s AND s.day = d.day
                 AND s.source IN ('direct', 'metrika')
                GROUP BY d.day
                ORDER BY d.day ASC
                """,
                (date_from, date_to, user_id),
            ).fetchall()

        if yandex_goal_id is not None:
            if account_id is not None:
                goal_days = conn.execute(
                    """
                    SELECT s.day, COALESCE(SUM(s.goal_reaches), 0) AS goal_reaches
                    FROM stats_daily s
                    JOIN account_counter_links l
                      ON l.counter_row_id = s.counter_row_id AND l.user_id = s.user_id
                    WHERE s.user_id = %s AND s.source = 'metrika_goal'
                      AND s.goal_id = %s AND s.campaign_id IS NULL
                      AND s.day >= %s AND s.day <= %s
                      AND l.account_id = %s
                    GROUP BY s.day
                    """,
                    (user_id, yandex_goal_id, date_from, date_to, account_id),
                ).fetchall()
            else:
                goal_days = conn.execute(
                    """
                    SELECT day, COALESCE(SUM(goal_reaches), 0) AS goal_reaches
                    FROM stats_daily
                    WHERE user_id = %s AND source = 'metrika_goal'
                      AND goal_id = %s AND campaign_id IS NULL
                      AND day >= %s AND day <= %s
                    GROUP BY day
                    """,
                    (user_id, yandex_goal_id, date_from, date_to),
                ).fetchall()
            goal_map = {r["day"]: int(r["goal_reaches"] or 0) for r in goal_days}
        else:
            goal_map = {}

    by_day = []
    for r in by_day_rows:
        day = r["day"]
        day_goals = goal_map.get(day, int(r["goal_reaches"] or 0)) if yandex_goal_id is not None else int(r["goal_reaches"] or 0)
        by_day.append(
            {
                "day": day.isoformat() if hasattr(day, "isoformat") else str(day),
                "cost_kopecks": int(r["cost_kopecks"] or 0),
                "clicks": int(r["clicks"] or 0),
                "conversions": int(r["conversions"] or 0),
                "visits": int(r["visits"] or 0),
                "goal_reaches": day_goals,
            }
        )

    cost = int(totals["cost_kopecks"] or 0)
    conv = int(totals["conversions"] or 0)
    goals_any = int(totals["goal_reaches"] or 0)
    goals = selected_goal_reaches if yandex_goal_id is not None else goals_any
    tops = _structure_tops(user_id, account_id=account_id)
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "links_count": linked_n,
        "goal_row_id": goal_row_id,
        "goal_name": goal_name,
        "totals": {
            "cost_kopecks": cost,
            "clicks": int(totals["clicks"] or 0),
            "impressions": int(totals["impressions"] or 0),
            "conversions": conv,
            "visits": int(totals["visits"] or 0),
            "goal_reaches": goals,
            "goal_reaches_any": goals_any,
            "cpa_kopecks": int(cost / conv) if conv else None,
            "cpa_goal_kopecks": int(cost / goals) if goals else None,
        },
        "by_account": [
            {
                "id": r["id"],
                "name": r["name"],
                "client_login": r["client_login"],
                "cost_kopecks": int(r["cost_kopecks"]),
                "clicks": int(r["clicks"]),
                "conversions": int(r["conversions"]),
                "visits": int(r["visits"]),
                "goal_reaches": int(r["goal_reaches"]),
                "links": int(r["links"]),
            }
            for r in by_account
        ],
        "by_campaign": [
            {
                "campaign_id": r["campaign_id"],
                "name": r["name"] or str(r["campaign_id"]),
                "account_id": r["account_id"],
                "client_login": r["client_login"],
                "cost_kopecks": int(r["cost_kopecks"]),
                "clicks": int(r["clicks"]),
                "conversions": int(r["conversions"]),
            }
            for r in by_campaign
            if r["campaign_id"]
        ],
        "by_day": by_day,
        "top_keywords": tops["keywords"],
        "top_adgroups": tops["adgroups"],
        "top_queries": tops["queries"],
        "tops_note": tops["note"],
    }


def _structure_tops(user_id: int, *, account_id: int | None = None, limit: int = 15) -> dict:
    """Топы по period_* / search_queries (после синка статистики на кампании)."""
    limit = max(1, min(limit, 50))
    acc_clause_k = "AND k.account_id = %s" if account_id is not None else ""
    acc_clause_g = "AND g.account_id = %s" if account_id is not None else ""
    acc_clause_q = "AND q.account_id = %s" if account_id is not None else ""
    params_acc = [account_id] if account_id is not None else []
    with pool.connection() as conn:
        kw_rows = conn.execute(
            f"""
            SELECT k.keyword, k.period_clicks AS clicks, k.period_cost_kopecks AS cost_kopecks,
                   k.period_conversions AS conversions, g.name AS adgroup_name,
                   c.name AS campaign_name, k.stats_from, k.stats_to
            FROM direct_keywords k
            JOIN direct_adgroups g ON g.id = k.adgroup_row_id
            JOIN direct_campaigns c ON c.id = g.campaign_row_id
            WHERE k.user_id = %s {acc_clause_k}
              AND COALESCE(k.period_cost_kopecks, 0) + COALESCE(k.period_clicks, 0) > 0
            ORDER BY k.period_cost_kopecks DESC NULLS LAST, k.period_clicks DESC
            LIMIT %s
            """,
            [user_id, *params_acc, limit],
        ).fetchall()
        ag_rows = conn.execute(
            f"""
            SELECT g.name, g.period_clicks AS clicks, g.period_cost_kopecks AS cost_kopecks,
                   g.period_conversions AS conversions, c.name AS campaign_name,
                   g.stats_from, g.stats_to
            FROM direct_adgroups g
            JOIN direct_campaigns c ON c.id = g.campaign_row_id
            WHERE g.user_id = %s {acc_clause_g}
              AND COALESCE(g.period_cost_kopecks, 0) + COALESCE(g.period_clicks, 0) > 0
            ORDER BY g.period_cost_kopecks DESC NULLS LAST, g.period_clicks DESC
            LIMIT %s
            """,
            [user_id, *params_acc, limit],
        ).fetchall()
        q_rows = conn.execute(
            f"""
            SELECT q.query, q.clicks, q.cost_kopecks, q.conversions,
                   g.name AS adgroup_name, c.name AS campaign_name,
                   q.date_from, q.date_to
            FROM direct_search_queries q
            LEFT JOIN direct_adgroups g ON g.id = q.adgroup_row_id
            JOIN direct_campaigns c ON c.id = q.campaign_row_id
            WHERE q.user_id = %s {acc_clause_q}
              AND COALESCE(q.cost_kopecks, 0) + COALESCE(q.clicks, 0) > 0
            ORDER BY q.cost_kopecks DESC NULLS LAST, q.clicks DESC
            LIMIT %s
            """,
            [user_id, *params_acc, limit],
        ).fetchall()

    def _row_period(r):
        return {
            "name": r.get("keyword") or r.get("name") or r.get("query") or "",
            "clicks": int(r.get("clicks") or 0),
            "cost_kopecks": int(r.get("cost_kopecks") or 0),
            "conversions": int(r.get("conversions") or 0),
            "adgroup_name": r.get("adgroup_name") or "",
            "campaign_name": r.get("campaign_name") or "",
        }

    note = "По данным последнего синка статистики на страницах кампаний (не за выбранный период дашборда)."
    return {
        "keywords": [_row_period(r) for r in kw_rows],
        "adgroups": [_row_period(r) for r in ag_rows],
        "queries": [_row_period({**r, "name": r.get("query")}) for r in q_rows],
        "note": note,
    }


def list_user_ids_with_accounts() -> list[int]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT user_id FROM direct_accounts WHERE enabled = TRUE
            UNION
            SELECT DISTINCT user_id FROM metrika_counters WHERE enabled = TRUE
            """
        ).fetchall()
    return [int(r["user_id"]) for r in rows]
