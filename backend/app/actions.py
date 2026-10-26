"""Операции над кампаниями Директа: массовые + CRUD."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date

import httpx

from app.db import pool
from app.direct_sync import (
    get_campaign_rows,
    insert_campaign_local,
    remove_campaign_local,
    update_campaign_local_state,
)
from app.yandex import YandexError, direct_api_error, get_connection_access_token

log = logging.getLogger("app.actions")
CAMPAIGNS_URL = "https://api.direct.yandex.com/json/v5/campaigns"

BULK_ACTIONS = {"pause", "resume", "set_budget", "archive", "unarchive", "delete"}


def _log_action(user_id: int, action: str, payload: dict, result: dict, status: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO action_logs (user_id, action, payload, result, status)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                user_id,
                action,
                json.dumps(payload, ensure_ascii=False)[:4000],
                json.dumps(result, ensure_ascii=False)[:4000],
                status,
            ),
        )


def list_action_logs(user_id: int, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 200))
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, action, payload, result, status, created_at
            FROM action_logs
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "action": r["action"],
            "payload": r["payload"],
            "result": r["result"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]


def _call_campaigns(access_token: str, client_login: str, method: str, params: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
        "Client-Login": client_login,
    }
    body = {"method": method, "params": params}
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(CAMPAIGNS_URL, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise YandexError("direct") from exc
    payload = response.json() if response.content else {}
    if response.status_code >= 400 or payload.get("error"):
        err = payload.get("error") or {}
        log.warning(
            "campaigns.%s error: %s %s %s",
            method,
            response.status_code,
            err.get("error_code") or err.get("error_string"),
            err.get("error_detail") or payload,
        )
        raise direct_api_error(payload, response.status_code)
    return payload.get("result") or {}


def _account_creds(user_id: int, account_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, connection_id, client_login, enabled
            FROM direct_accounts
            WHERE id = %s AND user_id = %s
            """,
            (account_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def create_text_campaign(
    user_id: int,
    *,
    account_id: int,
    name: str,
    daily_budget_rub: float,
    start_date: str | None = None,
) -> dict:
    """Создаёт текстово-графическую кампанию (поиск, HIGHEST_POSITION)."""
    name_n = (name or "").strip()[:255]
    if len(name_n) < 1:
        raise YandexError("empty")
    if daily_budget_rub is None or float(daily_budget_rub) <= 0:
        raise YandexError("budget")
    account = _account_creds(user_id, account_id)
    start = (start_date or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(start)
    except ValueError as exc:
        raise YandexError("date") from exc

    micros = int(float(daily_budget_rub) * 1_000_000)
    kopecks = int(float(daily_budget_rub) * 100)
    campaign_body = {
        "Name": name_n,
        "StartDate": start,
        "DailyBudget": {"Amount": micros, "Mode": "STANDARD"},
        "TextCampaign": {
            "BiddingStrategy": {
                "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            }
        },
    }
    token = get_connection_access_token(account["connection_id"], user_id)
    result = _call_campaigns(
        token,
        account["client_login"],
        "add",
        {"Campaigns": [campaign_body]},
    )
    add_results = result.get("AddResults") or []
    if not add_results or not add_results[0].get("Id"):
        errors = (add_results[0] or {}).get("Errors") if add_results else None
        log.warning("campaigns.add no id: %s", errors or result)
        raise YandexError("direct")
    campaign_id = int(add_results[0]["Id"])
    row = insert_campaign_local(
        user_id,
        account_id,
        campaign_id=campaign_id,
        name=name_n,
        campaign_type="TEXT_CAMPAIGN",
        state="OFF",
        status="DRAFT",
        strategy="HIGHEST_POSITION",
        daily_budget_kopecks=kopecks,
    )
    out = {"campaign": row, "status": "ok"}
    _log_action(
        user_id,
        "create",
        {"account_id": account_id, "name": name_n, "daily_budget_rub": daily_budget_rub},
        out,
        "ok",
    )
    return out


def update_campaign(
    user_id: int,
    campaign_row_id: int,
    *,
    name: str | None = None,
    daily_budget_rub: float | None = None,
    negative_keywords: list[str] | str | None = None,
) -> dict:
    from app.adgroups import _parse_phrases

    rows = get_campaign_rows(user_id, [campaign_row_id])
    if not rows:
        raise YandexError("not-found")
    row = rows[0]
    patch: dict = {"Id": int(row["campaign_id"])}
    name_n = None
    kopecks = None
    negatives = None
    if name is not None:
        name_n = (name or "").strip()[:255]
        if not name_n:
            raise YandexError("empty")
        patch["Name"] = name_n
    if daily_budget_rub is not None:
        if float(daily_budget_rub) <= 0:
            raise YandexError("budget")
        micros = int(float(daily_budget_rub) * 1_000_000)
        kopecks = int(float(daily_budget_rub) * 100)
        patch["DailyBudget"] = {"Amount": micros, "Mode": "STANDARD"}
    if negative_keywords is not None:
        negatives = _parse_phrases(negative_keywords)
        if len(negatives) > 300:
            raise YandexError("limit")
        patch["NegativeKeywords"] = {"Items": negatives}
    if len(patch) == 1:
        raise YandexError("empty")

    token = get_connection_access_token(row["connection_id"], user_id)
    _call_campaigns(token, row["client_login"], "update", {"Campaigns": [patch]})
    with pool.connection() as conn:
        sets = []
        params: list = []
        if name_n is not None:
            sets.append("name = %s")
            params.append(name_n)
        if kopecks is not None:
            sets.append("daily_budget_kopecks = %s")
            params.append(kopecks)
        if negatives is not None:
            sets.append("negative_keywords = %s")
            params.append(json.dumps(negatives, ensure_ascii=False))
        if sets:
            sets.append("synced_at = NOW()")
            params.append(campaign_row_id)
            conn.execute(
                f"UPDATE direct_campaigns SET {', '.join(sets)} WHERE id = %s",
                params,
            )
    out = {
        "id": campaign_row_id,
        "name": name_n,
        "daily_budget_kopecks": kopecks,
        "negative_keywords": negatives,
        "status": "ok",
    }
    _log_action(
        user_id,
        "update",
        {
            "campaign_row_id": campaign_row_id,
            "name": name_n,
            "daily_budget_rub": daily_budget_rub,
            "negative_count": len(negatives) if negatives is not None else None,
        },
        out,
        "ok",
    )
    return out


def bulk_campaign_action(
    user_id: int,
    *,
    campaign_ids: list[int],
    action: str,
    daily_budget_rub: float | None = None,
) -> dict:
    """action: pause | resume | set_budget | archive | unarchive | delete"""
    if action not in BULK_ACTIONS:
        raise YandexError("action")
    if not campaign_ids:
        raise YandexError("empty")
    if len(campaign_ids) > 100:
        raise YandexError("limit")

    rows = get_campaign_rows(user_id, campaign_ids)
    if len(rows) != len(set(campaign_ids)):
        raise YandexError("not-found")

    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["connection_id"], row["client_login"])].append(row)

    applied = 0
    errors: list[dict] = []
    details: list[dict] = []

    for (connection_id, client_login), group in grouped.items():
        try:
            token = get_connection_access_token(connection_id, user_id)
            ids = [int(r["campaign_id"]) for r in group]
            if action == "pause":
                _call_campaigns(token, client_login, "suspend", {"SelectionCriteria": {"Ids": ids}})
                for r in group:
                    update_campaign_local_state(r["id"], state="SUSPENDED")
            elif action == "resume":
                _call_campaigns(token, client_login, "resume", {"SelectionCriteria": {"Ids": ids}})
                for r in group:
                    update_campaign_local_state(r["id"], state="ON")
            elif action == "archive":
                _call_campaigns(token, client_login, "archive", {"SelectionCriteria": {"Ids": ids}})
                for r in group:
                    update_campaign_local_state(r["id"], state="ARCHIVED")
            elif action == "unarchive":
                _call_campaigns(token, client_login, "unarchive", {"SelectionCriteria": {"Ids": ids}})
                for r in group:
                    update_campaign_local_state(r["id"], state="OFF")
            elif action == "delete":
                _call_campaigns(token, client_login, "delete", {"SelectionCriteria": {"Ids": ids}})
                for r in group:
                    remove_campaign_local(r["id"])
            elif action == "set_budget":
                if daily_budget_rub is None or daily_budget_rub <= 0:
                    raise YandexError("budget")
                micros = int(daily_budget_rub * 1_000_000)
                kopecks = int(daily_budget_rub * 100)
                campaigns = [
                    {
                        "Id": int(r["campaign_id"]),
                        "DailyBudget": {"Amount": micros, "Mode": "STANDARD"},
                    }
                    for r in group
                ]
                _call_campaigns(token, client_login, "update", {"Campaigns": campaigns})
                for r in group:
                    update_campaign_local_state(r["id"], daily_budget_kopecks=kopecks)
            applied += len(group)
            details.append({"client_login": client_login, "count": len(group), "ok": True})
        except YandexError as exc:
            errors.append({"client_login": client_login, "error": exc.code})
            details.append({"client_login": client_login, "ok": False, "error": exc.code})
        except Exception:
            log.exception("bulk action")
            errors.append({"client_login": client_login, "error": "direct"})
            details.append({"client_login": client_login, "ok": False, "error": "direct"})

    status = "ok" if not errors else ("partial" if applied else "error")
    result = {"applied": applied, "errors": errors, "details": details, "status": status}
    _log_action(
        user_id,
        action,
        {"campaign_ids": campaign_ids, "daily_budget_rub": daily_budget_rub},
        result,
        status,
    )
    if status == "error":
        raise YandexError(errors[0]["error"] if errors else "direct")
    return result
