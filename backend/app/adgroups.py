"""Группы объявлений Яндекс Директа."""

from __future__ import annotations

import json
import logging

import httpx

from app.db import pool
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, direct_api_error, get_connection_access_token

log = logging.getLogger("app.adgroups")
ADGROUPS_URL = "https://api.direct.yandex.com/json/v5/adgroups"


def _campaign_row(user_id: int, campaign_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.user_id, c.account_id, c.campaign_id, c.name,
                   a.connection_id, a.client_login, a.enabled
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.id = %s AND c.user_id = %s
            """,
            (campaign_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def _call(access_token: str, client_login: str, method: str, params: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
        "Client-Login": client_login,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                ADGROUPS_URL,
                headers=headers,
                json={"method": method, "params": params},
            )
    except httpx.HTTPError as exc:
        raise YandexError("direct") from exc
    payload = response.json() if response.content else {}
    if response.status_code >= 400 or payload.get("error"):
        err = payload.get("error") or {}
        log.warning("adgroups.%s %s %s", method, response.status_code, err or payload)
        raise direct_api_error(payload, response.status_code)
    return payload.get("result") or {}


def _negatives_from_api(item: dict) -> list[str]:
    raw = item.get("NegativeKeywords") or {}
    items = raw.get("Items") if isinstance(raw, dict) else None
    if not items:
        return []
    cleaned = []
    for phrase in items:
        text = str(phrase or "").strip()
        if text and text not in cleaned:
            cleaned.append(text[:4096])
    return cleaned


def _parse_phrases(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                raw = parsed if isinstance(parsed, list) else [text]
            except json.JSONDecodeError:
                raw = text.splitlines()
        else:
            raw = text.splitlines()
    else:
        return []
    cleaned = []
    for phrase in raw:
        text = str(phrase or "").strip()
        if text and text not in cleaned:
            cleaned.append(text[:4096])
    return cleaned


def fetch_adgroups(access_token: str, client_login: str, campaign_id: int) -> list[dict]:
    result = _call(
        access_token,
        client_login,
        "get",
        {
            "SelectionCriteria": {"CampaignIds": [campaign_id]},
            "FieldNames": [
                "Id",
                "Name",
                "CampaignId",
                "Status",
                "Type",
                "RegionIds",
                "NegativeKeywords",
            ],
        },
    )
    items = []
    for item in result.get("AdGroups") or []:
        gid = item.get("Id")
        if gid is None:
            continue
        regions = item.get("RegionIds") or []
        negatives = _negatives_from_api(item)
        items.append(
            {
                "adgroup_id": int(gid),
                "name": str(item.get("Name") or ""),
                "status": str(item.get("Status") or ""),
                "type": str(item.get("Type") or ""),
                "region_ids": json.dumps(regions, ensure_ascii=False),
                "negative_keywords": json.dumps(negatives, ensure_ascii=False),
            }
        )
    return items


def upsert_adgroups(user_id: int, account_id: int, campaign_row_id: int, items: list[dict]) -> int:
    count = 0
    with pool.connection() as conn:
        for item in items:
            conn.execute(
                """
                INSERT INTO direct_adgroups (
                    user_id, account_id, campaign_row_id, adgroup_id,
                    name, status, type, region_ids, negative_keywords, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id, adgroup_id) DO UPDATE SET
                    campaign_row_id = EXCLUDED.campaign_row_id,
                    name = EXCLUDED.name,
                    status = EXCLUDED.status,
                    type = EXCLUDED.type,
                    region_ids = EXCLUDED.region_ids,
                    negative_keywords = EXCLUDED.negative_keywords,
                    synced_at = NOW()
                """,
                (
                    user_id,
                    account_id,
                    campaign_row_id,
                    item["adgroup_id"],
                    item.get("name") or "",
                    item.get("status") or "",
                    item.get("type") or "",
                    item.get("region_ids") or "[]",
                    item.get("negative_keywords") or "[]",
                ),
            )
            count += 1
    return count


def sync_campaign_adgroups(user_id: int, campaign_row_id: int) -> dict:
    camp = _campaign_row(user_id, campaign_row_id)
    run_id = start_run(user_id=user_id, kind="direct_adgroups", target_id=campaign_row_id)
    try:
        token = get_connection_access_token(camp["connection_id"], user_id)
        items = fetch_adgroups(token, camp["client_login"], int(camp["campaign_id"]))
        n = upsert_adgroups(user_id, camp["account_id"], campaign_row_id, items)
        finish_run(run_id, status="ok", meta={"adgroups": n})
        return {"adgroups": n, "status": "ok"}
    except YandexError as exc:
        finish_run(run_id, status="error", error=exc.code)
        raise
    except Exception as exc:
        log.exception("sync adgroups")
        finish_run(run_id, status="error", error="direct")
        raise YandexError("direct") from exc


def list_adgroups(user_id: int, campaign_row_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM direct_adgroups
            WHERE user_id = %s AND campaign_row_id = %s
            ORDER BY name ASC, adgroup_id ASC
            """,
            (user_id, campaign_row_id),
        ).fetchall()
    return [_public(r) for r in rows]


def create_adgroup(
    user_id: int,
    *,
    campaign_row_id: int,
    name: str,
    region_ids: list[int] | None = None,
) -> dict:
    name_n = (name or "").strip()[:255]
    if not name_n:
        raise YandexError("empty")
    camp = _campaign_row(user_id, campaign_row_id)
    regions = region_ids if region_ids else [225]  # Россия
    token = get_connection_access_token(camp["connection_id"], user_id)
    result = _call(
        token,
        camp["client_login"],
        "add",
        {
            "AdGroups": [
                {
                    "Name": name_n,
                    "CampaignId": int(camp["campaign_id"]),
                    "RegionIds": regions,
                }
            ]
        },
    )
    add = (result.get("AddResults") or [{}])[0]
    if not add.get("Id"):
        log.warning("adgroups.add fail: %s", add)
        raise YandexError("direct")
    adgroup_id = int(add["Id"])
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO direct_adgroups (
                user_id, account_id, campaign_row_id, adgroup_id,
                name, status, type, region_ids, synced_at
            )
            VALUES (%s, %s, %s, %s, %s, '', 'TEXT_AD_GROUP', %s, NOW())
            ON CONFLICT (account_id, adgroup_id) DO UPDATE SET
                name = EXCLUDED.name, synced_at = NOW()
            RETURNING *
            """,
            (
                user_id,
                camp["account_id"],
                campaign_row_id,
                adgroup_id,
                name_n,
                json.dumps(regions),
            ),
        ).fetchone()
    return _public(row)


def update_adgroup(
    user_id: int,
    adgroup_row_id: int,
    *,
    name: str | None = None,
    negative_keywords: list[str] | str | None = None,
) -> dict:
    row = _adgroup_row(user_id, adgroup_row_id)
    patch: dict = {"Id": int(row["adgroup_id"])}
    name_n = None
    negatives = None
    if name is not None:
        name_n = (name or "").strip()[:255]
        if not name_n:
            raise YandexError("empty")
        patch["Name"] = name_n
    if negative_keywords is not None:
        negatives = _parse_phrases(negative_keywords)
        if len(negatives) > 300:
            raise YandexError("limit")
        patch["NegativeKeywords"] = {"Items": negatives}
    if len(patch) == 1:
        raise YandexError("empty")
    token = get_connection_access_token(row["connection_id"], user_id)
    _call(token, row["client_login"], "update", {"AdGroups": [patch]})
    sets = []
    params: list = []
    if name_n is not None:
        sets.append("name = %s")
        params.append(name_n)
    if negatives is not None:
        sets.append("negative_keywords = %s")
        params.append(json.dumps(negatives, ensure_ascii=False))
    sets.append("synced_at = NOW()")
    params.extend([adgroup_row_id, user_id])
    with pool.connection() as conn:
        updated = conn.execute(
            f"""
            UPDATE direct_adgroups SET {', '.join(sets)}
            WHERE id = %s AND user_id = %s
            RETURNING *
            """,
            params,
        ).fetchone()
    return _public(updated)


def delete_adgroups(user_id: int, adgroup_row_ids: list[int]) -> dict:
    if not adgroup_row_ids:
        raise YandexError("empty")
    rows = []
    for rid in adgroup_row_ids:
        rows.append(_adgroup_row(user_id, rid))
    # группируем по client_login
    by_login: dict[str, list[dict]] = {}
    for r in rows:
        by_login.setdefault(r["client_login"], []).append(r)
    applied = 0
    for client_login, group in by_login.items():
        token = get_connection_access_token(group[0]["connection_id"], user_id)
        ids = [int(r["adgroup_id"]) for r in group]
        _call(token, client_login, "delete", {"SelectionCriteria": {"Ids": ids}})
        with pool.connection() as conn:
            conn.execute(
                "DELETE FROM direct_adgroups WHERE user_id = %s AND id = ANY(%s)",
                (user_id, [r["id"] for r in group]),
            )
        applied += len(group)
    return {"applied": applied, "status": "ok"}


def _adgroup_row(user_id: int, adgroup_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT g.*, a.connection_id, a.client_login, a.enabled
            FROM direct_adgroups g
            JOIN direct_accounts a ON a.id = g.account_id
            WHERE g.id = %s AND g.user_id = %s
            """,
            (adgroup_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def _public(row: dict) -> dict:
    regions = row.get("region_ids") or "[]"
    if isinstance(regions, str):
        try:
            regions = json.loads(regions)
        except json.JSONDecodeError:
            regions = []
    negatives = _parse_phrases(row.get("negative_keywords") or "[]")
    cost = int(row.get("period_cost_kopecks") or 0)
    clicks = int(row.get("period_clicks") or 0)
    conv = int(row.get("period_conversions") or 0)
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "campaign_row_id": row["campaign_row_id"],
        "adgroup_id": row["adgroup_id"],
        "name": row["name"],
        "status": row.get("status") or "",
        "type": row.get("type") or "",
        "region_ids": regions,
        "negative_keywords": negatives,
        "period_impressions": int(row.get("period_impressions") or 0),
        "period_clicks": clicks,
        "period_cost_kopecks": cost,
        "period_conversions": conv,
        "period_cpc_rub": round(cost / 100 / clicks, 2) if clicks else None,
        "stats_from": row["stats_from"].isoformat() if row.get("stats_from") else None,
        "stats_to": row["stats_to"].isoformat() if row.get("stats_to") else None,
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
    }
