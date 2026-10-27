"""Сбор и агрегация дневной статистики Директ + Метрика."""

from __future__ import annotations

import csv
import io
import logging
import time
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app import direct_api
from app.clock import yesterday_msk
from app.db import pool
from app.metrika_sync import sync_metrika_stats_for_user
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.stats")


def _rub_to_kopecks(raw) -> int:
    try:
        return int((Decimal(str(raw or 0)) * Decimal(100)).quantize(Decimal("1")))
    except Exception:
        return 0


def _count(raw) -> int:
    """Целое из ячейки отчёта; «--» (нет данных по целям) и мусор — 0."""
    try:
        return int(Decimal(str(raw or 0)))
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
    revenue_kopecks: int = 0,
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
                impressions, clicks, cost_kopecks, conversions, revenue_kopecks, updated_at
            )
            VALUES (%s, %s, 'direct', %s, %s, %s, %s, %s, %s, %s, NOW())
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
                revenue_kopecks,
            ),
        )


def parse_campaign_day_rows(raw: list[dict]) -> list[dict]:
    """Строки CAMPAIGN_PERFORMANCE_REPORT с Date → по строке на (день, кампания)."""
    merged: dict[tuple[date, int], dict] = {}
    for item in raw:
        try:
            day = date.fromisoformat(str(item.get("Date") or "").strip())
            cid = int(item.get("CampaignId") or 0)
        except ValueError:
            continue
        if not cid:
            continue
        row = merged.setdefault(
            (day, cid),
            {
                "day": day,
                "campaign_id": cid,
                "impressions": 0,
                "clicks": 0,
                "cost_kopecks": 0,
                "conversions": 0,
                "revenue_kopecks": 0,
            },
        )
        row["impressions"] += _count(item.get("Impressions"))
        row["clicks"] += _count(item.get("Clicks"))
        row["cost_kopecks"] += _rub_to_kopecks(item.get("Cost"))
        row["conversions"] += _count(item.get("Conversions"))
        row["revenue_kopecks"] += _rub_to_kopecks(item.get("Revenue"))
    return list(merged.values())


def replace_direct_stats(user_id: int, account_id: int, date_from: date, date_to: date, rows: list[dict]) -> int:
    """Статистика кабинета за период целиком: старые строки периода удаляются — в том числе
    кампаний, которых в новом отчёте нет."""
    with pool.connection() as conn:
        conn.execute(
            """
            DELETE FROM stats_daily
            WHERE user_id = %s AND source = 'direct' AND account_id = %s AND day BETWEEN %s AND %s
            """,
            (user_id, account_id, date_from, date_to),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO stats_daily (
                    user_id, day, source, account_id, campaign_id,
                    impressions, clicks, cost_kopecks, conversions, revenue_kopecks, updated_at
                )
                VALUES (%s, %s, 'direct', %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                [
                    (
                        user_id,
                        row["day"],
                        account_id,
                        row["campaign_id"],
                        row["impressions"],
                        row["clicks"],
                        row["cost_kopecks"],
                        row["conversions"],
                        row["revenue_kopecks"],
                    )
                    for row in rows
                    if date_from <= row["day"] <= date_to
                ],
            )
    return len(rows)


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
                "Revenue",
            ],
            # Отчёт с тем же именем и параметрами API отдаёт из кэша: за сегодня и после
            # дозачёта конверсий за прошлые дни нужен свежий, поэтому имя уникально.
            "ReportName": f"aimarketing_{client_login}_{day.isoformat()}_{uuid.uuid4().hex[:12]}"[:255],
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
                response = client.post(direct_api.v5_url(access_token, "reports"), headers=headers, json=body)
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
                "impressions": _count(item.get("Impressions")),
                "clicks": _count(item.get("Clicks")),
                "cost_kopecks": _rub_to_kopecks(item.get("Cost")),
                "conversions": _count(item.get("Conversions")),
                "revenue_kopecks": _rub_to_kopecks(item.get("Revenue")),
            }
        )
    return rows


def set_account_stats_status(account_id: int, error: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE direct_accounts
            SET stats_error = %s,
                stats_synced_at = CASE WHEN %s = '' THEN NOW() ELSE stats_synced_at END
            WHERE id = %s
            """,
            (error, error, account_id),
        )


def sync_direct_stats_for_user(user_id: int, day: date | None = None) -> dict:
    day = day or yesterday_msk()
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
                    revenue_kopecks=row["revenue_kopecks"],
                )
            set_account_stats_status(account["id"], "")
            synced += 1
        except Exception as exc:
            log.exception("direct stats account %s", account["id"])
            set_account_stats_status(account["id"], getattr(exc, "code", "") or "internal")
            errors += 1
    return {"day": day.isoformat(), "synced": synced, "errors": errors}


def sync_stats_for_user(user_id: int, day: date | None = None) -> dict:
    day = day or yesterday_msk()
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


def structure_tops(user_id: int, *, campaign_row_ids: list[int] | None = None, limit: int = 15) -> dict:
    """Топы ключей, групп и запросов по period_* / search_queries (после синка статистики на кампании)."""
    limit = max(1, min(limit, 50))
    scoped = campaign_row_ids is not None
    acc_clause_k = "AND g.campaign_row_id = ANY(%s)" if scoped else ""
    acc_clause_g = "AND g.campaign_row_id = ANY(%s)" if scoped else ""
    acc_clause_q = "AND q.campaign_row_id = ANY(%s)" if scoped else ""
    params_acc = [list(campaign_row_ids)] if scoped else []
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
