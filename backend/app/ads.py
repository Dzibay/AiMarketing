"""Объявления Яндекс Директа (TextAd)."""

from __future__ import annotations

import logging

from app import direct_api
from app.adgroups import _adgroup_row
from app.db import pool
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.ads")


def _call(access_token: str, client_login: str, method: str, params: dict) -> dict:
    return direct_api.call(access_token, client_login, "ads", method, params)


def fetch_ads(access_token: str, client_login: str, adgroup_ids: list[int]) -> list[dict]:
    if not adgroup_ids:
        return []
    result = _call(
        access_token,
        client_login,
        "get",
        {
            "SelectionCriteria": {"AdGroupIds": adgroup_ids},
            "FieldNames": ["Id", "AdGroupId", "Type", "State", "Status"],
            "TextAdFieldNames": ["Title", "Title2", "Text", "Href"],
        },
    )
    items = []
    for item in result.get("Ads") or []:
        aid = item.get("Id")
        if aid is None:
            continue
        text_ad = item.get("TextAd") or {}
        items.append(
            {
                "ad_id": int(aid),
                "adgroup_id": int(item.get("AdGroupId") or 0),
                "type": str(item.get("Type") or ""),
                "state": str(item.get("State") or ""),
                "status": str(item.get("Status") or ""),
                "title": str(text_ad.get("Title") or ""),
                "title2": str(text_ad.get("Title2") or ""),
                "text": str(text_ad.get("Text") or ""),
                "href": str(text_ad.get("Href") or ""),
            }
        )
    return items


def upsert_ads(user_id: int, account_id: int, adgroup_row_by_direct_id: dict[int, int], items: list[dict]) -> int:
    count = 0
    with pool.connection() as conn:
        for item in items:
            adgroup_row_id = adgroup_row_by_direct_id.get(int(item["adgroup_id"]))
            if not adgroup_row_id:
                continue
            conn.execute(
                """
                INSERT INTO direct_ads (
                    user_id, account_id, adgroup_row_id, ad_id,
                    type, state, status, title, title2, text, href, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id, ad_id) DO UPDATE SET
                    adgroup_row_id = EXCLUDED.adgroup_row_id,
                    type = EXCLUDED.type,
                    state = EXCLUDED.state,
                    status = EXCLUDED.status,
                    title = EXCLUDED.title,
                    title2 = EXCLUDED.title2,
                    text = EXCLUDED.text,
                    href = EXCLUDED.href,
                    synced_at = NOW()
                """,
                (
                    user_id,
                    account_id,
                    adgroup_row_id,
                    item["ad_id"],
                    item.get("type") or "",
                    item.get("state") or "",
                    item.get("status") or "",
                    item.get("title") or "",
                    item.get("title2") or "",
                    item.get("text") or "",
                    item.get("href") or "",
                ),
            )
            count += 1
    return count


def sync_campaign_ads(user_id: int, campaign_row_id: int) -> dict:
    with pool.connection() as conn:
        groups = conn.execute(
            """
            SELECT g.id, g.adgroup_id, g.account_id, a.connection_id, a.client_login
            FROM direct_adgroups g
            JOIN direct_accounts a ON a.id = g.account_id
            WHERE g.user_id = %s AND g.campaign_row_id = %s
            """,
            (user_id, campaign_row_id),
        ).fetchall()
    if not groups:
        return {"ads": 0, "status": "ok"}
    run_id = start_run(user_id=user_id, kind="direct_ads", target_id=campaign_row_id)
    try:
        token = get_connection_access_token(groups[0]["connection_id"], user_id)
        client_login = groups[0]["client_login"]
        account_id = groups[0]["account_id"]
        map_ids = {int(g["adgroup_id"]): int(g["id"]) for g in groups}
        items = fetch_ads(token, client_login, list(map_ids.keys()))
        n = upsert_ads(user_id, account_id, map_ids, items)
        finish_run(run_id, status="ok", meta={"ads": n})
        return {"ads": n, "status": "ok"}
    except YandexError as exc:
        finish_run(run_id, status="error", error=exc.code)
        raise
    except Exception as exc:
        log.exception("sync ads")
        finish_run(run_id, status="error", error="direct")
        raise YandexError("direct") from exc


def list_ads(user_id: int, *, campaign_row_id: int | None = None, adgroup_row_id: int | None = None) -> list[dict]:
    clauses = ["ad.user_id = %s"]
    params: list = [user_id]
    if adgroup_row_id is not None:
        clauses.append("ad.adgroup_row_id = %s")
        params.append(adgroup_row_id)
    if campaign_row_id is not None:
        clauses.append("g.campaign_row_id = %s")
        params.append(campaign_row_id)
    where = " AND ".join(clauses)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT ad.*, g.name AS adgroup_name, g.adgroup_id
            FROM direct_ads ad
            JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            WHERE {where}
            ORDER BY ad.id DESC
            """,
            params,
        ).fetchall()
    return [_public(r) for r in rows]


def create_text_ad(
    user_id: int,
    *,
    adgroup_row_id: int,
    title: str,
    text: str,
    href: str,
    title2: str = "",
) -> dict:
    title_n = (title or "").strip()[:56]
    text_n = (text or "").strip()[:81]
    href_n = (href or "").strip()[:1024]
    title2_n = (title2 or "").strip()[:30]
    if not title_n or not text_n or not href_n:
        raise YandexError("empty")
    if not href_n.startswith("http://") and not href_n.startswith("https://"):
        href_n = "https://" + href_n
    group = _adgroup_row(user_id, adgroup_row_id)
    token = get_connection_access_token(group["connection_id"], user_id)
    text_ad: dict = {
        "Title": title_n,
        "Text": text_n,
        "Href": href_n,
        "Mobile": "NO",
    }
    if title2_n:
        text_ad["Title2"] = title2_n
    result = _call(
        token,
        group["client_login"],
        "add",
        {"Ads": [{"AdGroupId": int(group["adgroup_id"]), "TextAd": text_ad}]},
    )
    add = (result.get("AddResults") or [{}])[0]
    if not add.get("Id"):
        log.warning("ads.add fail: %s", add)
        raise YandexError("direct")
    ad_id = int(add["Id"])
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO direct_ads (
                user_id, account_id, adgroup_row_id, ad_id,
                type, state, status, title, title2, text, href, synced_at
            )
            VALUES (%s, %s, %s, %s, 'TEXT_AD', 'OFF', '', %s, %s, %s, %s, NOW())
            ON CONFLICT (account_id, ad_id) DO UPDATE SET
                title = EXCLUDED.title, title2 = EXCLUDED.title2,
                text = EXCLUDED.text, href = EXCLUDED.href, synced_at = NOW()
            RETURNING *
            """,
            (
                user_id,
                group["account_id"],
                adgroup_row_id,
                ad_id,
                title_n,
                title2_n,
                text_n,
                href_n,
            ),
        ).fetchone()
    return _public(row)


def update_text_ad(
    user_id: int,
    ad_row_id: int,
    *,
    title: str | None = None,
    title2: str | None = None,
    text: str | None = None,
    href: str | None = None,
) -> dict:
    row = _ad_row(user_id, ad_row_id)
    text_ad: dict = {}
    if title is not None:
        text_ad["Title"] = (title or "").strip()[:56]
    if title2 is not None:
        text_ad["Title2"] = (title2 or "").strip()[:30]
    if text is not None:
        text_ad["Text"] = (text or "").strip()[:81]
    if href is not None:
        href_n = (href or "").strip()[:1024]
        if href_n and not href_n.startswith("http://") and not href_n.startswith("https://"):
            href_n = "https://" + href_n
        text_ad["Href"] = href_n
    if not text_ad:
        raise YandexError("empty")
    token = get_connection_access_token(row["connection_id"], user_id)
    _call(
        token,
        row["client_login"],
        "update",
        {"Ads": [{"Id": int(row["ad_id"]), "TextAd": text_ad}]},
    )
    sets = []
    params: list = []
    mapping = {
        "Title": ("title", title),
        "Title2": ("title2", title2),
        "Text": ("text", text),
        "Href": ("href", href),
    }
    for api_key, (col, val) in mapping.items():
        if api_key in text_ad:
            sets.append(f"{col} = %s")
            params.append(text_ad[api_key])
    sets.append("synced_at = NOW()")
    params.extend([ad_row_id, user_id])
    with pool.connection() as conn:
        updated = conn.execute(
            f"""
            UPDATE direct_ads SET {', '.join(sets)}
            WHERE id = %s AND user_id = %s
            RETURNING *
            """,
            params,
        ).fetchone()
    return _public(updated)


def bulk_ad_action(user_id: int, *, ad_row_ids: list[int], action: str) -> dict:
    if action not in ("suspend", "resume", "archive", "unarchive", "delete"):
        raise YandexError("action")
    if not ad_row_ids:
        raise YandexError("empty")
    rows = [_ad_row(user_id, rid) for rid in ad_row_ids]
    by_login: dict[str, list[dict]] = {}
    for r in rows:
        by_login.setdefault(r["client_login"], []).append(r)
    applied = 0
    for client_login, group in by_login.items():
        token = get_connection_access_token(group[0]["connection_id"], user_id)
        ids = [int(r["ad_id"]) for r in group]
        method = action
        _call(token, client_login, method, {"SelectionCriteria": {"Ids": ids}})
        with pool.connection() as conn:
            if action == "delete":
                conn.execute(
                    "DELETE FROM direct_ads WHERE user_id = %s AND id = ANY(%s)",
                    (user_id, [r["id"] for r in group]),
                )
            elif action == "suspend":
                conn.execute(
                    "UPDATE direct_ads SET state = 'SUSPENDED', synced_at = NOW() WHERE id = ANY(%s)",
                    ([r["id"] for r in group],),
                )
            elif action == "resume":
                conn.execute(
                    "UPDATE direct_ads SET state = 'ON', synced_at = NOW() WHERE id = ANY(%s)",
                    ([r["id"] for r in group],),
                )
            elif action == "archive":
                conn.execute(
                    "UPDATE direct_ads SET state = 'ARCHIVED', synced_at = NOW() WHERE id = ANY(%s)",
                    ([r["id"] for r in group],),
                )
            elif action == "unarchive":
                conn.execute(
                    "UPDATE direct_ads SET state = 'OFF', synced_at = NOW() WHERE id = ANY(%s)",
                    ([r["id"] for r in group],),
                )
        applied += len(group)
    return {"applied": applied, "status": "ok"}


def _ad_row(user_id: int, ad_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT ad.*, a.connection_id, a.client_login, a.enabled
            FROM direct_ads ad
            JOIN direct_accounts a ON a.id = ad.account_id
            WHERE ad.id = %s AND ad.user_id = %s
            """,
            (ad_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def _public(row: dict) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "adgroup_row_id": row["adgroup_row_id"],
        "ad_id": row["ad_id"],
        "type": row.get("type") or "",
        "state": row.get("state") or "",
        "status": row.get("status") or "",
        "title": row.get("title") or "",
        "title2": row.get("title2") or "",
        "text": row.get("text") or "",
        "href": row.get("href") or "",
        "adgroup_name": row.get("adgroup_name") or "",
        "adgroup_id": row.get("adgroup_id"),
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
    }
