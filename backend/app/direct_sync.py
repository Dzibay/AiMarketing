"""Синхронизация кампаний Яндекс Директа."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation

from psycopg.types.json import Jsonb

from app import direct_api
from app.adgroups import _parse_phrases
from app.db import pool
from app.strategy_info import priority_goals_from_api, strategy_brief
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


# Ключевые цели и атрибуцию просим для текстовых, динамических и ЕПК.
_GOAL_FIELDS = ["PriorityGoals", "AttributionModel"]
_GOAL_TYPES = ("TEXT_CAMPAIGN", "DYNAMIC_TEXT_CAMPAIGN", "UNIFIED_CAMPAIGN")
# Попытки campaigns.get: (ЕПК, ключевые цели) — старые аккаунты API могут не знать ЕПК или поля целей.
_GET_ATTEMPTS = ((True, True), (True, False), (False, True), (False, False))


def _campaigns_get(access_token: str, client_login: str, *, unified: bool, goals: bool) -> dict:
    fields = ["BiddingStrategy", "CounterIds", *(_GOAL_FIELDS if goals else [])]
    params = {
        "SelectionCriteria": {},
        "FieldNames": ["Id", "Name", "Type", "State", "Status", "DailyBudget", "NegativeKeywords", "StartDate"],
        "TextCampaignFieldNames": fields,
        "DynamicTextCampaignFieldNames": fields,
        "SmartCampaignFieldNames": ["BiddingStrategy", "CounterId"],
    }
    if unified:
        params["UnifiedCampaignFieldNames"] = fields
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
    """Кампании логина. priority_goals и attribution_model — None, если Директ не отдал поля целей:
    записанные раньше значения тогда не трогаем."""
    result: dict = {}
    goals = False
    for index, (unified, goals) in enumerate(_GET_ATTEMPTS):
        try:
            result = _campaigns_get(access_token, client_login, unified=unified, goals=goals)
            break
        except YandexError as exc:
            if exc.code == "units" or index == len(_GET_ATTEMPTS) - 1:
                raise
    items = result.get("Campaigns") or []
    out = []
    for item in items:
        cid = item.get("Id")
        if cid is None:
            continue
        block = next((item[key] for key in _TYPE_BLOCKS if isinstance(item.get(key), dict)), {})
        bidding = block.get("BiddingStrategy") or {}
        strategy = str(
            bidding.get("Search", {}).get("BiddingStrategyType")
            or bidding.get("Network", {}).get("BiddingStrategyType")
            or bidding.get("BiddingStrategyType")
            or ""
        )
        daily = (item.get("DailyBudget") or {}).get("Amount")
        neg_raw = item.get("NegativeKeywords") or {}
        negatives = _parse_phrases(neg_raw.get("Items") if isinstance(neg_raw, dict) else [])
        goals_known = goals and str(item.get("Type") or "") in _GOAL_TYPES
        out.append(
            {
                "campaign_id": int(cid),
                "name": str(item.get("Name") or ""),
                "campaign_type": str(item.get("Type") or ""),
                "state": str(item.get("State") or ""),
                "status": str(item.get("Status") or ""),
                "strategy": strategy,
                "bidding_strategy": bidding,
                "priority_goals": priority_goals_from_api(block.get("PriorityGoals")) if goals_known else None,
                "attribution_model": str(block.get("AttributionModel") or "") if goals_known else None,
                "daily_budget_kopecks": _money_to_kopecks(daily),
                "negative_keywords": negatives,
                "counter_ids": _counter_ids(item),
                "start_date": item.get("StartDate") or None,
            }
        )
    return out


def _changes(old: dict, item: dict) -> list[tuple[str, object, object]]:
    """Что поменялось в кампании с прошлого синка: [(поле, было, стало)].

    Стратегию и цели сравниваем, только если они уже приходили раньше: первый синк после
    обновления — не изменение.
    """
    out: list[tuple[str, object, object]] = []
    for field in ("name", "state", "status", "daily_budget_kopecks"):
        before, after = old.get(field), item.get(field)
        if before != after and (before or after):
            out.append((field, before, after))
    if old.get("bidding_strategy") and item.get("bidding_strategy"):
        before, after = strategy_brief(old["bidding_strategy"]), strategy_brief(item["bidding_strategy"])
        if before != after:
            out.append(("strategy", before, after))
    if old.get("attribution_model") and item.get("attribution_model") is not None:
        if (old.get("priority_goals") or []) != item.get("priority_goals"):
            out.append(("priority_goals", old.get("priority_goals") or [], item["priority_goals"]))
        if old["attribution_model"] != item["attribution_model"]:
            out.append(("attribution_model", old["attribution_model"], item["attribution_model"]))
    return out


_UPSERT_SQL = """
    INSERT INTO direct_campaigns (
        user_id, account_id, campaign_id, name, campaign_type,
        state, status, strategy, bidding_strategy, priority_goals, attribution_model,
        daily_budget_kopecks, negative_keywords, counter_ids, start_date, source_row_id, synced_at
    )
    VALUES (
        %(user)s, %(account)s, %(campaign_id)s, %(name)s, %(type)s,
        %(state)s, %(status)s, %(strategy)s,
        COALESCE(%(bidding)s::jsonb, '{}'::jsonb), COALESCE(%(goals)s::jsonb, '[]'::jsonb),
        COALESCE(%(attribution)s::text, ''),
        %(daily)s, %(negatives)s, %(counters)s, %(start_date)s, %(source)s::bigint, NOW()
    )
    ON CONFLICT (account_id, campaign_id) DO UPDATE SET
        name = EXCLUDED.name,
        campaign_type = EXCLUDED.campaign_type,
        state = EXCLUDED.state,
        status = EXCLUDED.status,
        strategy = EXCLUDED.strategy,
        bidding_strategy = COALESCE(%(bidding)s::jsonb, direct_campaigns.bidding_strategy),
        priority_goals = COALESCE(%(goals)s::jsonb, direct_campaigns.priority_goals),
        attribution_model = COALESCE(%(attribution)s::text, direct_campaigns.attribution_model),
        daily_budget_kopecks = EXCLUDED.daily_budget_kopecks,
        negative_keywords = EXCLUDED.negative_keywords,
        counter_ids = EXCLUDED.counter_ids,
        start_date = EXCLUDED.start_date,
        source_row_id = COALESCE(%(source)s::bigint, direct_campaigns.source_row_id),
        synced_at = NOW()
"""


def _upsert_params(user_id: int, account_id: int, item: dict) -> dict:
    """Параметры _UPSERT_SQL. None в bidding/goals/attribution/source — оставить записанное."""
    bidding = item.get("bidding_strategy")
    goals = item.get("priority_goals")
    return {
        "user": user_id,
        "account": account_id,
        "campaign_id": item["campaign_id"],
        "name": item.get("name") or "",
        "type": item.get("campaign_type") or "",
        "state": item.get("state") or "",
        "status": item.get("status") or "",
        "strategy": item.get("strategy") or "",
        "bidding": Jsonb(bidding) if bidding else None,
        "goals": None if goals is None else Jsonb(goals),
        "attribution": item.get("attribution_model"),
        "daily": item.get("daily_budget_kopecks"),
        "negatives": json.dumps(item.get("negative_keywords") or [], ensure_ascii=False),
        "counters": Jsonb(list(item.get("counter_ids") or [])),
        "start_date": item.get("start_date"),
        "source": item.get("source_row_id"),
    }


def upsert_campaigns(user_id: int, account_id: int, campaigns: list[dict], *, prune: bool = False) -> dict:
    """
    Записывает кампании кабинета. Возвращает {"count", "fresh", "removed", "changed"}: fresh — id
    строк, чьи счётчики узнали впервые (новые кампании и первый синк после обновления), —
    их раскладываем по пространствам. changed — сколько полей кампаний поменялось с прошлого
    синка (история в campaign_changes). prune — удалить кампании, которых в Директе больше нет.
    """
    count = 0
    fresh: list[int] = []
    removed = 0
    changes: list[tuple] = []
    with pool.connection() as conn:
        existing = {
            int(r["campaign_id"]): r
            for r in conn.execute(
                """
                SELECT id, campaign_id, name, state, status, daily_budget_kopecks,
                       bidding_strategy, priority_goals, attribution_model, counter_ids
                FROM direct_campaigns WHERE account_id = %s
                """,
                (account_id,),
            )
        }
        for item in campaigns:
            row = conn.execute(
                _UPSERT_SQL + " RETURNING id, (xmax = 0) AS inserted",
                _upsert_params(user_id, account_id, item),
            ).fetchone()
            count += 1
            old = existing.get(int(item["campaign_id"]))
            if row["inserted"] or old is None or old["counter_ids"] is None:
                fresh.append(int(row["id"]))
            if old is not None:
                for field, before, after in _changes(old, item):
                    changes.append((user_id, int(row["id"]), field, Jsonb(before), Jsonb(after)))
        if changes:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO campaign_changes (user_id, campaign_row_id, field, old_value, new_value)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    changes,
                )
        if prune:
            seen = [int(item["campaign_id"]) for item in campaigns]
            removed = conn.execute(
                "DELETE FROM direct_campaigns WHERE account_id = %s AND NOT (campaign_id = ANY(%s))",
                (account_id, seen),
            ).rowcount
    return {"count": count, "fresh": fresh, "removed": removed, "changed": len(changes)}


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
        meta = {"campaigns": saved["count"], "removed": saved["removed"], "changed": saved["changed"], "attached": attached}
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
    bidding_strategy: dict | None = None,
    priority_goals: list[dict] | None = None,
    attribution_model: str | None = None,
    source_row_id: int | None = None,
) -> dict:
    """Кампания, созданная сервисом, — сразу в базу, не дожидаясь синка. source_row_id — из какой
    кампании сделана копия."""
    item = {
        "campaign_id": campaign_id,
        "name": name,
        "campaign_type": campaign_type,
        "state": state,
        "status": status,
        "strategy": strategy,
        "bidding_strategy": bidding_strategy,
        "priority_goals": priority_goals,
        "attribution_model": attribution_model,
        "daily_budget_kopecks": daily_budget_kopecks,
        "negative_keywords": negative_keywords,
        "counter_ids": counter_ids,
        "start_date": start_date,
        "source_row_id": source_row_id,
    }
    with pool.connection() as conn:
        row = conn.execute(
            _UPSERT_SQL
            + """
            RETURNING id, account_id, campaign_id, name, campaign_type, state, status,
                      strategy, daily_budget_kopecks
            """,
            _upsert_params(user_id, account_id, item),
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
