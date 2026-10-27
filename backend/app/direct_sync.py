"""Синхронизация кампаний Яндекс Директа."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation

from psycopg.types.json import Jsonb

from app import direct_api
from app.adgroups import _parse_phrases
from app.db import pool
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.direct_sync")

# Блоки типов кампаний, где Директ хранит счётчики Метрики и стратегию.
_TYPE_BLOCKS = ("TextCampaign", "DynamicTextCampaign", "SmartCampaign", "UnifiedCampaign", "CpmBannerCampaign")


def _account(user_id: int, account_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, user_id, connection_id, client_login, name, enabled
            FROM direct_accounts
            WHERE id = %s AND user_id = %s
            """,
            (account_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return row


def _set_account_sync(account_id: int, status: str, error: str = "") -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE direct_accounts
            SET last_sync_at = NOW(), last_sync_status = %s, last_sync_error = %s, synced_at = NOW()
            WHERE id = %s
            """,
            (status, error[:500], account_id),
        )


def _money_to_kopecks(raw) -> int | None:
    if raw is None:
        return None
    try:
        # Direct часто отдаёт микроединицы (1e6 = 1 руб) в DailyBudget.Amount
        amount = Decimal(str(raw))
        if amount > 100_000:
            # микрорубли → копейки: / 10000
            return int((amount / Decimal(10000)).quantize(Decimal("1")))
        # уже рубли
        return int((amount * Decimal(100)).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _campaigns_get(access_token: str, client_login: str, unified: bool) -> dict:
    params = {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "Type", "State", "Status", "DailyBudget", "NegativeKeywords", "StartDate"],
        "TextCampaignFieldNames": ["BiddingStrategy", "CounterIds"],
        "DynamicTextCampaignFieldNames": ["BiddingStrategy", "CounterIds"],
        "SmartCampaignFieldNames": ["BiddingStrategy", "CounterId"],
    }
    if unified:
        params["UnifiedCampaignFieldNames"] = ["BiddingStrategy", "CounterIds"]
    return direct_api.call(access_token, client_login, "campaigns", "get", params)


def _counter_ids(item: dict) -> list[int]:
    out: list[int] = []
    for key in _TYPE_BLOCKS:
        block = item.get(key) or {}
        raw = block.get("CounterIds")
        values = raw.get("Items") if isinstance(raw, dict) else raw
        if block.get("CounterId"):
            values = [block["CounterId"], *(values or [])]
        for value in values or []:
            try:
                counter_id = int(value)
            except (TypeError, ValueError):
                continue
            if counter_id not in out:
                out.append(counter_id)
    return out


def fetch_campaigns(access_token: str, client_login: str) -> list[dict]:
    try:
        result = _campaigns_get(access_token, client_login, unified=True)
    except YandexError:
        # Старые аккаунты API могут не знать про ЕПК — повторяем без неё.
        result = _campaigns_get(access_token, client_login, unified=False)
    items = result.get("Campaigns") or []
    out = []
    for item in items:
        cid = item.get("Id")
        if cid is None:
            continue
        strategy = ""
        for key in _TYPE_BLOCKS:
            block = item.get(key) or {}
            bidding = block.get("BiddingStrategy") or {}
            strategy = str(
                bidding.get("Search", {}).get("BiddingStrategyType")
                or bidding.get("Network", {}).get("BiddingStrategyType")
                or bidding.get("BiddingStrategyType")
                or ""
            )
            if strategy:
                break
        daily = (item.get("DailyBudget") or {}).get("Amount")
        neg_raw = item.get("NegativeKeywords") or {}
        negatives = _parse_phrases(neg_raw.get("Items") if isinstance(neg_raw, dict) else [])
        out.append(
            {
                "campaign_id": int(cid),
                "name": str(item.get("Name") or ""),
                "campaign_type": str(item.get("Type") or ""),
                "state": str(item.get("State") or ""),
                "status": str(item.get("Status") or ""),
                "strategy": strategy,
                "daily_budget_kopecks": _money_to_kopecks(daily),
                "negative_keywords": negatives,
                "counter_ids": _counter_ids(item),
                "start_date": item.get("StartDate") or None,
            }
        )
    return out


def upsert_campaigns(user_id: int, account_id: int, campaigns: list[dict], *, prune: bool = False) -> dict:
    """
    Записывает кампании кабинета. Возвращает {"count", "fresh", "removed"}: fresh — id строк,
    чьи счётчики узнали впервые (новые кампании и первый синк после обновления), —
    их раскладываем по пространствам. prune — удалить кампании, которых в Директе больше нет.
    """
    count = 0
    fresh: list[int] = []
    removed = 0
    with pool.connection() as conn:
        unknown = {
            int(r["campaign_id"])
            for r in conn.execute(
                "SELECT campaign_id FROM direct_campaigns WHERE account_id = %s AND counter_ids IS NULL",
                (account_id,),
            )
        }
        for item in campaigns:
            row = conn.execute(
                """
                INSERT INTO direct_campaigns (
                    user_id, account_id, campaign_id, name, campaign_type,
                    state, status, strategy, daily_budget_kopecks,
                    negative_keywords, counter_ids, start_date, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id, campaign_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    campaign_type = EXCLUDED.campaign_type,
                    state = EXCLUDED.state,
                    status = EXCLUDED.status,
                    strategy = EXCLUDED.strategy,
                    daily_budget_kopecks = EXCLUDED.daily_budget_kopecks,
                    negative_keywords = EXCLUDED.negative_keywords,
                    counter_ids = EXCLUDED.counter_ids,
                    start_date = EXCLUDED.start_date,
                    synced_at = NOW()
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    user_id,
                    account_id,
                    item["campaign_id"],
                    item.get("name") or "",
                    item.get("campaign_type") or "",
                    item.get("state") or "",
                    item.get("status") or "",
                    item.get("strategy") or "",
                    item.get("daily_budget_kopecks"),
                    json.dumps(item.get("negative_keywords") or [], ensure_ascii=False),
                    Jsonb(list(item.get("counter_ids") or [])),
                    item.get("start_date"),
                ),
            ).fetchone()
            count += 1
            if row["inserted"] or int(item["campaign_id"]) in unknown:
                fresh.append(int(row["id"]))
        if prune:
            seen = [int(item["campaign_id"]) for item in campaigns]
            removed = conn.execute(
                "DELETE FROM direct_campaigns WHERE account_id = %s AND NOT (campaign_id = ANY(%s))",
                (account_id, seen),
            ).rowcount
    return {"count": count, "fresh": fresh, "removed": removed}


def sync_account_campaigns(user_id: int, account_id: int) -> dict:
    from app.workspaces import auto_attach_campaigns

    account = _account(user_id, account_id)
    if not account.get("enabled"):
        raise YandexError("disabled")
    run_id = start_run(user_id=user_id, kind="direct_campaigns", target_id=account_id)
    try:
        token = get_connection_access_token(account["connection_id"], user_id)
        campaigns = fetch_campaigns(token, account["client_login"])
        saved = upsert_campaigns(user_id, account_id, campaigns, prune=True)
        attached = auto_attach_campaigns(user_id, saved["fresh"])
        _set_account_sync(account_id, "ok")
        meta = {"campaigns": saved["count"], "removed": saved["removed"], "attached": attached}
        finish_run(run_id, status="ok", meta=meta)
        return {**meta, "fresh": saved["fresh"], "status": "ok"}
    except YandexError as exc:
        _set_account_sync(account_id, "error", exc.code)
        finish_run(run_id, status="error", error=exc.code)
        raise
    except Exception as exc:
        log.exception("sync campaigns")
        _set_account_sync(account_id, "error", "direct")
        finish_run(run_id, status="error", error="direct")
        raise YandexError("direct") from exc


def list_campaigns(
    user_id: int,
    *,
    account_id: int | None = None,
    state: str = "",
    search: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    clauses = ["c.user_id = %s"]
    params: list = [user_id]
    if account_id is not None:
        clauses.append("c.account_id = %s")
        params.append(account_id)
    if state:
        clauses.append("c.state = %s")
        params.append(state)
    if search:
        clauses.append("(c.name ILIKE %s OR CAST(c.campaign_id AS TEXT) LIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    where = " AND ".join(clauses)
    with pool.connection() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM direct_campaigns c WHERE {where}",
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.campaign_type,
                   c.state, c.status, c.strategy, c.daily_budget_kopecks, c.synced_at,
                   a.client_login, a.name AS account_name
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE {where}
            ORDER BY c.name ASC, c.campaign_id ASC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        ).fetchall()
    items = [
        {
            "id": row["id"],
            "account_id": row["account_id"],
            "campaign_id": row["campaign_id"],
            "name": row["name"],
            "campaign_type": row["campaign_type"],
            "state": row["state"],
            "status": row["status"],
            "strategy": row["strategy"],
            "daily_budget_kopecks": row["daily_budget_kopecks"],
            "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
            "client_login": row["client_login"],
            "account_name": row["account_name"],
        }
        for row in rows
    ]
    return {"items": items, "total": int(total_row["n"]) if total_row else 0}


def get_campaign(user_id: int, campaign_row_id: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.campaign_type,
                   c.state, c.status, c.strategy, c.daily_budget_kopecks,
                   c.negative_keywords, c.synced_at,
                   a.client_login, a.name AS account_name
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.id = %s AND c.user_id = %s
            """,
            (campaign_row_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "campaign_id": row["campaign_id"],
        "name": row["name"],
        "campaign_type": row["campaign_type"],
        "state": row["state"],
        "status": row["status"],
        "strategy": row["strategy"],
        "daily_budget_kopecks": row["daily_budget_kopecks"],
        "negative_keywords": _parse_phrases(row.get("negative_keywords") or "[]"),
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
        "client_login": row["client_login"],
        "account_name": row["account_name"],
    }


def get_campaign_rows(user_id: int, campaign_row_ids: list[int]) -> list[dict]:
    if not campaign_row_ids:
        return []
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.state, c.status,
                   c.daily_budget_kopecks, a.connection_id, a.client_login
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.user_id = %s AND c.id = ANY(%s)
            """,
            (user_id, campaign_row_ids),
        ).fetchall()
    return list(rows)


def update_campaign_local_state(campaign_row_id: int, *, state: str | None = None, daily_budget_kopecks: int | None = None) -> None:
    with pool.connection() as conn:
        if state is not None and daily_budget_kopecks is not None:
            conn.execute(
                "UPDATE direct_campaigns SET state = %s, daily_budget_kopecks = %s, synced_at = NOW() WHERE id = %s",
                (state, daily_budget_kopecks, campaign_row_id),
            )
        elif state is not None:
            conn.execute(
                "UPDATE direct_campaigns SET state = %s, synced_at = NOW() WHERE id = %s",
                (state, campaign_row_id),
            )
        elif daily_budget_kopecks is not None:
            conn.execute(
                "UPDATE direct_campaigns SET daily_budget_kopecks = %s, synced_at = NOW() WHERE id = %s",
                (daily_budget_kopecks, campaign_row_id),
            )


def insert_campaign_local(
    user_id: int,
    account_id: int,
    *,
    campaign_id: int,
    name: str,
    campaign_type: str = "TEXT_CAMPAIGN",
    state: str = "OFF",
    status: str = "",
    strategy: str = "",
    daily_budget_kopecks: int | None = None,
    negative_keywords: list[str] | None = None,
    counter_ids: list[int] | None = None,
    start_date: str | None = None,
) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO direct_campaigns (
                user_id, account_id, campaign_id, name, campaign_type,
                state, status, strategy, daily_budget_kopecks,
                negative_keywords, counter_ids, start_date, synced_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (account_id, campaign_id) DO UPDATE SET
                name = EXCLUDED.name,
                campaign_type = EXCLUDED.campaign_type,
                state = EXCLUDED.state,
                status = EXCLUDED.status,
                strategy = EXCLUDED.strategy,
                daily_budget_kopecks = EXCLUDED.daily_budget_kopecks,
                negative_keywords = EXCLUDED.negative_keywords,
                counter_ids = EXCLUDED.counter_ids,
                start_date = EXCLUDED.start_date,
                synced_at = NOW()
            RETURNING id, account_id, campaign_id, name, campaign_type, state, status,
                      strategy, daily_budget_kopecks
            """,
            (
                user_id,
                account_id,
                campaign_id,
                name,
                campaign_type,
                state,
                status,
                strategy,
                daily_budget_kopecks,
                json.dumps(negative_keywords or [], ensure_ascii=False),
                Jsonb(list(counter_ids or [])),
                start_date,
            ),
        ).fetchone()
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "campaign_id": row["campaign_id"],
        "name": row["name"],
        "campaign_type": row["campaign_type"],
        "state": row["state"],
        "status": row["status"],
        "strategy": row["strategy"],
        "daily_budget_kopecks": row["daily_budget_kopecks"],
    }


def remove_campaign_local(campaign_row_id: int) -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM direct_campaigns WHERE id = %s", (campaign_row_id,))
