"""Периодная статистика по группам и ключам (Reports API)."""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app import direct_api
from app.adgroups import _campaign_row
from app.db import pool
from app.stats import _rub_to_kopecks
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.entity_stats")


def _fetch_report(
    access_token: str,
    client_login: str,
    *,
    report_type: str,
    field_names: list[str],
    campaign_id: int,
    date_from: date,
    date_to: date,
    extra_filters: list[dict] | None = None,
) -> list[dict]:
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
    filters = [
        {
            "Field": "CampaignId",
            "Operator": "EQUALS",
            "Values": [str(campaign_id)],
        }
    ]
    if extra_filters:
        filters.extend(extra_filters)
    report_name = (
        f"aimarketing_{report_type[:24]}_{client_login}_{campaign_id}_"
        f"{date_from.isoformat()}_{date_to.isoformat()}"
    )[:255]
    body = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": date_from.isoformat(),
                "DateTo": date_to.isoformat(),
                "Filter": filters,
            },
            "FieldNames": field_names,
            "ReportName": report_name,
            "ReportType": report_type,
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
            "IncludeDiscount": "NO",
        }
    }
    for _attempt in range(10):
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(direct_api.v5_url(access_token, "reports"), headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.warning("entity_stats %s: %s", report_type, exc)
            raise YandexError("direct") from exc
        if response.status_code == 200:
            break
        if response.status_code in (201, 202):
            time.sleep(2)
            continue
        log.warning(
            "entity_stats %s HTTP %s %s",
            report_type,
            response.status_code,
            response.text[:400],
        )
        raise YandexError("direct-denied" if response.status_code in (400, 403) else "direct")
    else:
        raise YandexError("direct")

    text = response.text.strip()
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def _agg_metrics(rows: list[dict], id_field: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in rows:
        try:
            eid = int(item.get(id_field) or 0)
        except ValueError:
            continue
        if not eid:
            continue
        bucket = out.get(eid)
        if bucket is None:
            bucket = {"impressions": 0, "clicks": 0, "cost_kopecks": 0, "conversions": 0}
            out[eid] = bucket
        bucket["impressions"] += int(Decimal(str(item.get("Impressions") or 0)))
        bucket["clicks"] += int(Decimal(str(item.get("Clicks") or 0)))
        bucket["cost_kopecks"] += _rub_to_kopecks(item.get("Cost"))
        bucket["conversions"] += int(Decimal(str(item.get("Conversions") or 0)))
    return out


def sync_campaign_entity_stats(
    user_id: int,
    campaign_row_id: int,
    *,
    days: int = 14,
) -> dict:
    days = max(1, min(int(days or 14), 90))
    camp = _campaign_row(user_id, campaign_row_id)
    date_to = date.today() - timedelta(days=1)
    date_from = date_to - timedelta(days=days - 1)
    run_id = start_run(user_id=user_id, kind="direct_entity_stats", target_id=campaign_row_id)
    try:
        token = get_connection_access_token(camp["connection_id"], user_id)
        login = camp["client_login"]
        cid = int(camp["campaign_id"])

        adgroup_rows = _fetch_report(
            token,
            login,
            report_type="ADGROUP_PERFORMANCE_REPORT",
            field_names=["AdGroupId", "Impressions", "Clicks", "Cost", "Conversions"],
            campaign_id=cid,
            date_from=date_from,
            date_to=date_to,
        )
        adgroup_metrics = _agg_metrics(adgroup_rows, "AdGroupId")

        criteria_rows = _fetch_report(
            token,
            login,
            report_type="CRITERIA_PERFORMANCE_REPORT",
            field_names=[
                "CriteriaId",
                "CriteriaType",
                "Impressions",
                "Clicks",
                "Cost",
                "Conversions",
            ],
            campaign_id=cid,
            date_from=date_from,
            date_to=date_to,
            extra_filters=[
                {
                    "Field": "CriteriaType",
                    "Operator": "EQUALS",
                    "Values": ["KEYWORD"],
                }
            ],
        )
        # CriteriaId в отчёте = keyword Id
        keyword_metrics = _agg_metrics(
            [r for r in criteria_rows if str(r.get("CriteriaType") or "") == "KEYWORD"],
            "CriteriaId",
        )

        with pool.connection() as conn:
            conn.execute(
                """
                UPDATE direct_adgroups SET
                    period_impressions = 0,
                    period_clicks = 0,
                    period_cost_kopecks = 0,
                    period_conversions = 0,
                    stats_from = %s,
                    stats_to = %s,
                    stats_synced_at = NOW()
                WHERE user_id = %s AND campaign_row_id = %s
                """,
                (date_from, date_to, user_id, campaign_row_id),
            )
            for adgroup_id, m in adgroup_metrics.items():
                conn.execute(
                    """
                    UPDATE direct_adgroups SET
                        period_impressions = %s,
                        period_clicks = %s,
                        period_cost_kopecks = %s,
                        period_conversions = %s,
                        stats_from = %s,
                        stats_to = %s,
                        stats_synced_at = NOW()
                    WHERE user_id = %s AND campaign_row_id = %s AND adgroup_id = %s
                    """,
                    (
                        m["impressions"],
                        m["clicks"],
                        m["cost_kopecks"],
                        m["conversions"],
                        date_from,
                        date_to,
                        user_id,
                        campaign_row_id,
                        adgroup_id,
                    ),
                )

            conn.execute(
                """
                UPDATE direct_keywords k SET
                    period_impressions = 0,
                    period_clicks = 0,
                    period_cost_kopecks = 0,
                    period_conversions = 0,
                    stats_from = %s,
                    stats_to = %s,
                    stats_synced_at = NOW()
                FROM direct_adgroups g
                WHERE k.adgroup_row_id = g.id
                  AND k.user_id = %s
                  AND g.campaign_row_id = %s
                """,
                (date_from, date_to, user_id, campaign_row_id),
            )
            for keyword_id, m in keyword_metrics.items():
                conn.execute(
                    """
                    UPDATE direct_keywords SET
                        period_impressions = %s,
                        period_clicks = %s,
                        period_cost_kopecks = %s,
                        period_conversions = %s,
                        stats_from = %s,
                        stats_to = %s,
                        stats_synced_at = NOW()
                    WHERE user_id = %s AND account_id = %s AND keyword_id = %s
                    """,
                    (
                        m["impressions"],
                        m["clicks"],
                        m["cost_kopecks"],
                        m["conversions"],
                        date_from,
                        date_to,
                        user_id,
                        camp["account_id"],
                        keyword_id,
                    ),
                )

        finish_run(
            run_id,
            status="ok",
            meta={
                "adgroups": len(adgroup_metrics),
                "keywords": len(keyword_metrics),
                "days": days,
            },
        )
        return {
            "adgroups": len(adgroup_metrics),
            "keywords": len(keyword_metrics),
            "days": days,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "status": "ok",
        }
    except YandexError as exc:
        finish_run(run_id, status="error", error=exc.code)
        raise
    except Exception as exc:
        log.exception("entity stats")
        finish_run(run_id, status="error", error="direct")
        raise YandexError("direct") from exc
