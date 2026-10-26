"""Поисковые запросы из отчётов Яндекс Директа."""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.adgroups import _campaign_row, _parse_phrases, update_adgroup
from app.db import pool
from app.keywords import create_keywords
from app.stats import _rub_to_kopecks
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.search_queries")
REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"


def fetch_search_query_report(
    access_token: str,
    client_login: str,
    *,
    campaign_id: int,
    date_from: date,
    date_to: date,
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
    report_name = (
        f"aimarketing_sq_{client_login}_{campaign_id}_{date_from.isoformat()}_{date_to.isoformat()}"
    )
    body = {
        "params": {
            "SelectionCriteria": {
                "DateFrom": date_from.isoformat(),
                "DateTo": date_to.isoformat(),
                "Filter": [
                    {
                        "Field": "CampaignId",
                        "Operator": "EQUALS",
                        "Values": [str(campaign_id)],
                    }
                ],
            },
            "FieldNames": [
                "Query",
                "CampaignId",
                "AdGroupId",
                "Criterion",
                "Impressions",
                "Clicks",
                "Cost",
                "Conversions",
            ],
            "ReportName": report_name[:255],
            "ReportType": "SEARCH_QUERY_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES",
            "IncludeDiscount": "NO",
        }
    }
    for _attempt in range(10):
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(REPORTS_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.warning("search_queries report: %s", exc)
            raise YandexError("direct") from exc
        if response.status_code == 200:
            break
        if response.status_code in (201, 202):
            time.sleep(2)
            continue
        log.warning(
            "search_queries report HTTP %s %s",
            response.status_code,
            response.text[:400],
        )
        raise YandexError("direct-denied" if response.status_code in (400, 403) else "direct")
    else:
        raise YandexError("direct")

    text = response.text.strip()
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    aggregated: dict[tuple[int, str], dict] = {}
    for item in reader:
        query = str(item.get("Query") or "").strip()
        if not query:
            continue
        try:
            adgroup_id = int(item.get("AdGroupId") or 0)
        except ValueError:
            adgroup_id = 0
        key = (adgroup_id, query.lower())
        row = aggregated.get(key)
        if row is None:
            row = {
                "query": query[:1024],
                "adgroup_id": adgroup_id or None,
                "matched_keyword": str(item.get("Criterion") or "")[:1024],
                "impressions": 0,
                "clicks": 0,
                "cost_kopecks": 0,
                "conversions": 0,
            }
            aggregated[key] = row
        row["impressions"] += int(Decimal(str(item.get("Impressions") or 0)))
        row["clicks"] += int(Decimal(str(item.get("Clicks") or 0)))
        row["cost_kopecks"] += _rub_to_kopecks(item.get("Cost"))
        row["conversions"] += int(Decimal(str(item.get("Conversions") or 0)))
        if not row["matched_keyword"]:
            row["matched_keyword"] = str(item.get("Criterion") or "")[:1024]
    return list(aggregated.values())


def upsert_search_queries(
    user_id: int,
    *,
    account_id: int,
    campaign_row_id: int,
    date_from: date,
    date_to: date,
    items: list[dict],
    adgroup_row_by_direct_id: dict[int, int],
) -> int:
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM direct_search_queries WHERE user_id = %s AND campaign_row_id = %s",
            (user_id, campaign_row_id),
        )
        count = 0
        for item in items:
            adgroup_id = item.get("adgroup_id")
            adgroup_row_id = None
            if adgroup_id:
                adgroup_row_id = adgroup_row_by_direct_id.get(int(adgroup_id))
            conn.execute(
                """
                INSERT INTO direct_search_queries (
                    user_id, account_id, campaign_row_id, adgroup_row_id, adgroup_id,
                    query, matched_keyword, impressions, clicks, cost_kopecks,
                    conversions, date_from, date_to, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    user_id,
                    account_id,
                    campaign_row_id,
                    adgroup_row_id,
                    adgroup_id,
                    item.get("query") or "",
                    item.get("matched_keyword") or "",
                    int(item.get("impressions") or 0),
                    int(item.get("clicks") or 0),
                    int(item.get("cost_kopecks") or 0),
                    int(item.get("conversions") or 0),
                    date_from,
                    date_to,
                ),
            )
            count += 1
    return count


def sync_campaign_search_queries(
    user_id: int,
    campaign_row_id: int,
    *,
    days: int = 14,
) -> dict:
    days = max(1, min(int(days or 14), 90))
    camp = _campaign_row(user_id, campaign_row_id)
    date_to = date.today() - timedelta(days=1)
    date_from = date_to - timedelta(days=days - 1)
    with pool.connection() as conn:
        groups = conn.execute(
            """
            SELECT id, adgroup_id FROM direct_adgroups
            WHERE user_id = %s AND campaign_row_id = %s
            """,
            (user_id, campaign_row_id),
        ).fetchall()
    map_ids = {int(g["adgroup_id"]): int(g["id"]) for g in groups}
    run_id = start_run(user_id=user_id, kind="direct_search_queries", target_id=campaign_row_id)
    try:
        token = get_connection_access_token(camp["connection_id"], user_id)
        items = fetch_search_query_report(
            token,
            camp["client_login"],
            campaign_id=int(camp["campaign_id"]),
            date_from=date_from,
            date_to=date_to,
        )
        n = upsert_search_queries(
            user_id,
            account_id=camp["account_id"],
            campaign_row_id=campaign_row_id,
            date_from=date_from,
            date_to=date_to,
            items=items,
            adgroup_row_by_direct_id=map_ids,
        )
        finish_run(
            run_id,
            status="ok",
            meta={"queries": n, "days": days, "date_from": date_from.isoformat()},
        )
        return {
            "queries": n,
            "days": days,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "status": "ok",
        }
    except YandexError as exc:
        finish_run(run_id, status="error", error=exc.code)
        raise
    except Exception as exc:
        log.exception("sync search queries")
        finish_run(run_id, status="error", error="direct")
        raise YandexError("direct") from exc


def list_search_queries(
    user_id: int,
    campaign_row_id: int,
    *,
    limit: int = 200,
    offset: int = 0,
    search: str = "",
    min_clicks: int | None = None,
    min_cost_rub: float | None = None,
    no_conversions: bool = False,
) -> dict:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    clauses = ["q.user_id = %s", "q.campaign_row_id = %s"]
    params: list = [user_id, campaign_row_id]
    if search.strip():
        clauses.append("q.query ILIKE %s")
        params.append(f"%{search.strip()}%")
    if min_clicks is not None and int(min_clicks) > 0:
        clauses.append("q.clicks >= %s")
        params.append(int(min_clicks))
    if min_cost_rub is not None and float(min_cost_rub) > 0:
        clauses.append("q.cost_kopecks >= %s")
        params.append(int(float(min_cost_rub) * 100))
    if no_conversions:
        clauses.append("COALESCE(q.conversions, 0) = 0")
    where = " AND ".join(clauses)
    with pool.connection() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM direct_search_queries q WHERE {where}",
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT q.*, g.name AS adgroup_name
            FROM direct_search_queries q
            LEFT JOIN direct_adgroups g ON g.id = q.adgroup_row_id
            WHERE {where}
            ORDER BY q.clicks DESC, q.impressions DESC, q.id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        ).fetchall()
        meta = conn.execute(
            """
            SELECT date_from, date_to, MAX(synced_at) AS synced_at
            FROM direct_search_queries
            WHERE user_id = %s AND campaign_row_id = %s
            GROUP BY date_from, date_to
            ORDER BY synced_at DESC NULLS LAST
            LIMIT 1
            """,
            (user_id, campaign_row_id),
        ).fetchone()
    return {
        "items": [_public(r) for r in rows],
        "total": int(total_row["n"]) if total_row else 0,
        "date_from": meta["date_from"].isoformat() if meta and meta.get("date_from") else None,
        "date_to": meta["date_to"].isoformat() if meta and meta.get("date_to") else None,
        "synced_at": meta["synced_at"].isoformat() if meta and meta.get("synced_at") else None,
    }


def _query_row(user_id: int, query_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT q.*, c.campaign_id, a.connection_id, a.client_login, a.enabled
            FROM direct_search_queries q
            JOIN direct_campaigns c ON c.id = q.campaign_row_id
            JOIN direct_accounts a ON a.id = q.account_id
            WHERE q.id = %s AND q.user_id = %s
            """,
            (query_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def add_query_as_keyword(
    user_id: int,
    query_row_id: int,
    *,
    adgroup_row_id: int | None = None,
    bid_rub: float | None = None,
) -> dict:
    row = _query_row(user_id, query_row_id)
    group_id = adgroup_row_id or row.get("adgroup_row_id")
    if not group_id:
        raise YandexError("empty")
    phrase = (row.get("query") or "").strip()
    if not phrase:
        raise YandexError("empty")
    result = create_keywords(
        user_id,
        adgroup_row_id=int(group_id),
        phrases=[phrase],
        bid_rub=bid_rub,
    )
    return {"status": "ok", "query_id": query_row_id, "keywords": result}


def add_query_as_negative(
    user_id: int,
    query_row_id: int,
    *,
    scope: str = "adgroup",
) -> dict:
    if scope not in ("adgroup", "campaign"):
        raise YandexError("action")
    row = _query_row(user_id, query_row_id)
    phrase = (row.get("query") or "").strip()
    if not phrase:
        raise YandexError("empty")
    # минус без плюс-модификаторов
    neg = phrase.lstrip("+").strip()
    if not neg:
        raise YandexError("empty")
    if scope == "adgroup":
        if not row.get("adgroup_row_id"):
            raise YandexError("empty")
        with pool.connection() as conn:
            group = conn.execute(
                "SELECT negative_keywords FROM direct_adgroups WHERE id = %s AND user_id = %s",
                (row["adgroup_row_id"], user_id),
            ).fetchone()
        current = _parse_phrases((group or {}).get("negative_keywords") or "[]")
        if neg not in current:
            current.append(neg)
        updated = update_adgroup(
            user_id,
            int(row["adgroup_row_id"]),
            negative_keywords=current,
        )
        return {"status": "ok", "scope": "adgroup", "adgroup": updated, "phrase": neg}

    from app.actions import update_campaign
    from app.direct_sync import get_campaign

    camp = get_campaign(user_id, int(row["campaign_row_id"]))
    if camp is None:
        raise YandexError("not-found")
    current = list(camp.get("negative_keywords") or [])
    if neg not in current:
        current.append(neg)
    updated = update_campaign(
        user_id,
        int(row["campaign_row_id"]),
        negative_keywords=current,
    )
    return {"status": "ok", "scope": "campaign", "campaign": updated, "phrase": neg}


def bulk_search_query_action(
    user_id: int,
    *,
    query_ids: list[int],
    action: str,
    adgroup_row_id: int | None = None,
    bid_rub: float | None = None,
) -> dict:
    """action: add_keyword | negative_adgroup | negative_campaign"""
    if action not in ("add_keyword", "negative_adgroup", "negative_campaign"):
        raise YandexError("action")
    if not query_ids:
        raise YandexError("empty")
    if len(query_ids) > 100:
        raise YandexError("limit")

    rows = [_query_row(user_id, qid) for qid in query_ids]
    applied = 0
    skipped = 0
    errors = 0

    if action == "add_keyword":
        by_group: dict[int, list[str]] = {}
        for row in rows:
            group_id = adgroup_row_id or row.get("adgroup_row_id")
            phrase = (row.get("query") or "").strip()
            if not group_id or not phrase:
                skipped += 1
                continue
            by_group.setdefault(int(group_id), [])
            if phrase not in by_group[int(group_id)]:
                by_group[int(group_id)].append(phrase)
        for group_id, phrases in by_group.items():
            # create_keywords лимит 100
            for i in range(0, len(phrases), 100):
                chunk = phrases[i : i + 100]
                try:
                    result = create_keywords(
                        user_id,
                        adgroup_row_id=group_id,
                        phrases=chunk,
                        bid_rub=bid_rub,
                    )
                    applied += int(result.get("created") or 0)
                    errors += int(result.get("errors") or 0)
                except YandexError:
                    errors += len(chunk)
                    log.exception("bulk add keyword group %s", group_id)
        return {
            "status": "ok",
            "action": action,
            "applied": applied,
            "skipped": skipped,
            "errors": errors,
        }

    # negatives: batch by adgroup or one campaign update
    phrases_by_group: dict[int, list[str]] = {}
    campaign_phrases: list[str] = []
    campaign_row_id = None
    for row in rows:
        phrase = (row.get("query") or "").strip().lstrip("+").strip()
        if not phrase:
            skipped += 1
            continue
        if action == "negative_adgroup":
            gid = row.get("adgroup_row_id")
            if not gid:
                skipped += 1
                continue
            phrases_by_group.setdefault(int(gid), [])
            if phrase not in phrases_by_group[int(gid)]:
                phrases_by_group[int(gid)].append(phrase)
        else:
            campaign_row_id = int(row["campaign_row_id"])
            if phrase not in campaign_phrases:
                campaign_phrases.append(phrase)

    if action == "negative_adgroup":
        for gid, phrases in phrases_by_group.items():
            with pool.connection() as conn:
                group = conn.execute(
                    "SELECT negative_keywords FROM direct_adgroups WHERE id = %s AND user_id = %s",
                    (gid, user_id),
                ).fetchone()
            current = _parse_phrases((group or {}).get("negative_keywords") or "[]")
            added = 0
            for phrase in phrases:
                if phrase not in current:
                    current.append(phrase)
                    added += 1
            if added:
                try:
                    update_adgroup(user_id, gid, negative_keywords=current)
                    applied += added
                except YandexError:
                    errors += added
                    log.exception("bulk negative adgroup %s", gid)
            else:
                skipped += len(phrases)
        return {
            "status": "ok",
            "action": action,
            "applied": applied,
            "skipped": skipped,
            "errors": errors,
        }

    from app.actions import update_campaign
    from app.direct_sync import get_campaign

    if not campaign_row_id or not campaign_phrases:
        raise YandexError("empty")
    camp = get_campaign(user_id, campaign_row_id)
    if camp is None:
        raise YandexError("not-found")
    current = list(camp.get("negative_keywords") or [])
    added = 0
    for phrase in campaign_phrases:
        if phrase not in current:
            current.append(phrase)
            added += 1
    if added:
        update_campaign(user_id, campaign_row_id, negative_keywords=current)
        applied = added
    else:
        skipped += len(campaign_phrases)
    return {
        "status": "ok",
        "action": action,
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
    }


def _public(row: dict) -> dict:
    cost = row.get("cost_kopecks") or 0
    clicks = int(row.get("clicks") or 0)
    conv = int(row.get("conversions") or 0)
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "campaign_row_id": row["campaign_row_id"],
        "adgroup_row_id": row.get("adgroup_row_id"),
        "adgroup_id": row.get("adgroup_id"),
        "adgroup_name": row.get("adgroup_name") or "",
        "query": row.get("query") or "",
        "matched_keyword": row.get("matched_keyword") or "",
        "impressions": int(row.get("impressions") or 0),
        "clicks": clicks,
        "cost_kopecks": int(cost),
        "cost_rub": round(int(cost) / 100, 2),
        "conversions": conv,
        "cpc_rub": round(int(cost) / 100 / clicks, 2) if clicks else None,
        "cpa_rub": round(int(cost) / 100 / conv, 2) if conv else None,
        "date_from": row["date_from"].isoformat() if row.get("date_from") else None,
        "date_to": row["date_to"].isoformat() if row.get("date_to") else None,
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
    }
