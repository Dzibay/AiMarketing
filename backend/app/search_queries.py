"""Поисковые запросы из отчётов Яндекс Директа."""

from __future__ import annotations

import csv
import io
import logging
import time
from collections import defaultdict
from datetime import date, timedelta

import httpx

from app import direct_api
from app.adgroups import _campaign_row
from app.db import pool
from app.negative_lists import phrase_key
from app.query_cleaning import (
    NORM_SQL,
    add_target_keywords,
    apply_negative_entries,
    clean_negative,
    keyword_text,
    log_query_action,
    remember_decisions,
    snapshot_labels,
)
from app.stats import _count, _rub_to_kopecks
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.search_queries")


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
                response = client.post(direct_api.v5_url(access_token, "reports"), headers=headers, json=body)
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
    return aggregate_query_rows(csv.DictReader(io.StringIO(text), delimiter="\t"))


def aggregate_query_rows(rows) -> list[dict]:
    """Строки SEARCH_QUERY_PERFORMANCE_REPORT → по одной на (группа, запрос без регистра)."""
    aggregated: dict[tuple[int, str], dict] = {}
    for item in rows:
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
        row["impressions"] += _count(item.get("Impressions"))
        row["clicks"] += _count(item.get("Clicks"))
        row["cost_kopecks"] += _rub_to_kopecks(item.get("Cost"))
        row["conversions"] += _count(item.get("Conversions"))
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


def _query_rows(user_id: int, query_row_ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(i) for i in query_row_ids))
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT q.*, {NORM_SQL} AS query_norm, c.campaign_id, a.connection_id, a.client_login, a.enabled,
                   wc.workspace_id
            FROM direct_search_queries q
            JOIN direct_campaigns c ON c.id = q.campaign_row_id
            JOIN direct_accounts a ON a.id = q.account_id
            LEFT JOIN workspace_campaigns wc ON wc.campaign_row_id = q.campaign_row_id AND wc.user_id = q.user_id
            WHERE q.id = ANY(%s) AND q.user_id = %s
            """,
            (ids, user_id),
        ).fetchall()
    by_id = {int(r["id"]): r for r in rows}
    out = []
    for query_row_id in ids:
        row = by_id.get(query_row_id)
        if row is None:
            raise YandexError("not-found")
        if not row.get("enabled"):
            raise YandexError("disabled")
        out.append(row)
    return out


def _query_row(user_id: int, query_row_id: int) -> dict:
    return _query_rows(user_id, [query_row_id])[0]


def _remember(user_id: int, rows: list[dict], decisions: dict[int, dict]) -> dict[int, list[dict]]:
    """Решения по запросам — в память пространства кампании. → прежние метки по пространствам, для отката."""
    by_ws: dict[int, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        decision = decisions.get(int(row["id"]))
        if decision and row.get("workspace_id"):
            norm = row["query_norm"]
            by_ws[int(row["workspace_id"])][norm] = {"query_norm": norm, "query": row["query"], **decision}
    previous: dict[int, list[dict]] = {}
    for ws_id, items in by_ws.items():
        previous[ws_id] = snapshot_labels(ws_id, list(items))
        remember_decisions(user_id, ws_id, list(items.values()))
    return previous


def _log(user_id: int, rows: list[dict], *, title: str, action: str, summary: dict, status: str, **kwargs) -> int | None:
    ws_ids = {int(r["workspace_id"]) for r in rows if r.get("workspace_id")}
    return log_query_action(
        user_id,
        next(iter(ws_ids)) if len(ws_ids) == 1 else None,
        title=title,
        payload={"query_row_ids": [int(r["id"]) for r in rows][:50], "action": action},
        summary=summary,
        status=status,
        campaign_row_ids=sorted({int(r["campaign_row_id"]) for r in rows}),
        **kwargs,
    )


def _status(applied: int, errors: int) -> str:
    return "error" if errors and not applied else ("partial" if errors else "ok")


def _keywords_from_rows(user_id: int, rows: list[dict], adgroup_row_id: int | None, bid_rub) -> tuple[dict, dict]:
    by_group: dict[int, list[tuple[str, str]]] = defaultdict(list)
    outcomes: dict[str, tuple[str, str]] = {}
    for row in rows:
        group_id = adgroup_row_id or row.get("adgroup_row_id")
        phrase = keyword_text(row.get("query") or "")
        key = str(row["id"])
        if not group_id or not phrase:
            outcomes[key] = ("skipped", "Нет группы или фразы")
        elif any(phrase_key(p) == phrase_key(phrase) for _k, p in by_group[int(group_id)]):
            outcomes[key] = ("skipped", "Такая фраза уже в списке")
        else:
            by_group[int(group_id)].append((key, phrase))
    created_ids: list[int] = []
    if by_group:
        created_ids, made = add_target_keywords(user_id, by_group, bid_rub)
        outcomes.update(made)
    applied = sum(1 for s, _m in outcomes.values() if s == "ok")
    errors = sum(1 for s, _m in outcomes.values() if s == "error")
    summary = {
        "status": _status(applied, errors),
        "action": "add_keyword",
        "applied": applied,
        "skipped": sum(1 for s, _m in outcomes.values() if s == "skipped"),
        "errors": errors,
        "log_id": None,
    }
    done = {int(k): {"verdict": "target", "negative_phrase": "", "level": "adgroup", "applied": True}
            for k, (s, _m) in outcomes.items() if s == "ok"}
    if done or errors:
        previous = _remember(user_id, rows, done)
        summary["log_id"] = _log(
            user_id,
            rows,
            title=f"Запросы в ключевые фразы: +{len(created_ids)}",
            action="add_keyword",
            summary={k: summary[k] for k in ("applied", "skipped", "errors")},
            status=summary["status"],
            negatives={"campaigns": {}, "adgroups": {}, "skipped": [], "errors": []},
            created_keyword_ids=created_ids,
            previous_labels=previous,
        )
    return summary, outcomes


def _negatives_from_rows(user_id: int, rows: list[dict], level: str) -> tuple[dict, dict]:
    """Запросы в минус-фразы — общий путь с экраном чистки: журнал с откатом и память пространства."""
    entries = []
    outcomes: dict[str, dict] = {}
    for row in rows:
        phrase = clean_negative(row.get("query") or "")
        key = str(row["id"])
        if not phrase:
            outcomes[key] = {"status": "skipped", "message": "Пустой запрос"}
        elif level == "adgroup" and not row.get("adgroup_row_id"):
            outcomes[key] = {"status": "skipped", "message": "Запрос без группы"}
        else:
            entries.append(
                {
                    "key": key,
                    "phrase": phrase,
                    "level": level,
                    "campaign_row_ids": [int(row["campaign_row_id"])],
                    "adgroup_row_ids": [int(row["adgroup_row_id"])] if row.get("adgroup_row_id") else [],
                }
            )
    result = {"campaigns": {}, "adgroups": {}, "skipped": [], "errors": []}
    if entries:
        result, made = apply_negative_entries(user_id, entries, force=True)
        outcomes.update(made)
    applied = sum(1 for o in outcomes.values() if o["status"] in ("ok", "partial"))
    errors = sum(1 for o in outcomes.values() if o["status"] == "error")
    action = f"negative_{level}"
    summary = {
        "status": _status(applied, errors),
        "action": action,
        "applied": applied,
        "skipped": sum(1 for o in outcomes.values() if o["status"] == "skipped"),
        "errors": errors,
        "log_id": None,
    }
    done = {
        int(k): {"verdict": "negative", "negative_phrase": o["phrase"], "level": o["level"], "applied": True}
        for k, o in outcomes.items()
        if o["status"] in ("ok", "partial")
    }
    if done or errors:
        previous = _remember(user_id, rows, done)
        where = "группы" if level == "adgroup" else "кампании"
        summary["log_id"] = _log(
            user_id,
            rows,
            title=f"Запросы в минус-фразы {where}: {applied}",
            action=action,
            summary={k: summary[k] for k in ("applied", "skipped", "errors")},
            status=summary["status"],
            negatives=result,
            created_keyword_ids=[],
            previous_labels=previous,
        )
    return summary, outcomes


def add_query_as_keyword(
    user_id: int,
    query_row_id: int,
    *,
    adgroup_row_id: int | None = None,
    bid_rub: float | None = None,
) -> dict:
    row = _query_row(user_id, query_row_id)
    if not (adgroup_row_id or row.get("adgroup_row_id")) or not keyword_text(row.get("query") or ""):
        raise YandexError("empty")
    summary, outcomes = _keywords_from_rows(user_id, [row], adgroup_row_id, bid_rub)
    status, message = outcomes.get(str(row["id"]), ("error", ""))
    if status == "error":
        raise YandexError("direct", message or None)
    return {"status": "ok", "query_id": query_row_id, "created": summary["applied"], "log_id": summary["log_id"]}


def add_query_as_negative(
    user_id: int,
    query_row_id: int,
    *,
    scope: str = "adgroup",
) -> dict:
    if scope not in ("adgroup", "campaign"):
        raise YandexError("action")
    row = _query_row(user_id, query_row_id)
    summary, outcomes = _negatives_from_rows(user_id, [row], scope)
    outcome = outcomes.get(str(row["id"])) or {"status": "error", "message": ""}
    if outcome["status"] == "skipped":
        raise YandexError("empty", outcome.get("message") or None)
    if outcome["status"] == "error":
        raise YandexError("direct", outcome.get("message") or None)
    return {"status": "ok", "scope": outcome["level"], "phrase": outcome["phrase"], "log_id": summary["log_id"]}


def bulk_search_query_action(
    user_id: int,
    *,
    query_ids: list[int],
    action: str,
    adgroup_row_id: int | None = None,
    bid_rub: float | None = None,
) -> dict:
    """action: add_keyword | negative_adgroup | negative_campaign. → {status, action, applied, skipped, errors, log_id}"""
    if action not in ("add_keyword", "negative_adgroup", "negative_campaign"):
        raise YandexError("action")
    if not query_ids:
        raise YandexError("empty")
    if len(query_ids) > 100:
        raise YandexError("limit")
    rows = _query_rows(user_id, query_ids)
    if action == "add_keyword":
        summary, _outcomes = _keywords_from_rows(user_id, rows, adgroup_row_id, bid_rub)
    else:
        summary, _outcomes = _negatives_from_rows(user_id, rows, action.removeprefix("negative_"))
    return summary


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
