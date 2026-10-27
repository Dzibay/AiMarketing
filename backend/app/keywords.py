"""Ключевые фразы Яндекс Директа."""

from __future__ import annotations

import logging

from app import direct_api
from app.adgroups import _adgroup_row
from app.db import pool
from app.journal import campaigns_of, log_action
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.keywords")


def _call(access_token: str, client_login: str, method: str, params: dict) -> dict:
    return direct_api.call(access_token, client_login, "keywords", method, params)


def _rub_to_micros(rub: float | None) -> int | None:
    if rub is None:
        return None
    value = float(rub)
    if value <= 0:
        return None
    return int(round(value * 1_000_000))


KEYWORD_FIELDS = ["Id", "Keyword", "AdGroupId", "State", "Status", "Bid"]


def fetch_keywords(access_token: str, client_login: str, adgroup_ids: list[int]) -> list[dict]:
    if not adgroup_ids:
        return []
    result = _call(
        access_token,
        client_login,
        "get",
        {"SelectionCriteria": {"AdGroupIds": adgroup_ids}, "FieldNames": KEYWORD_FIELDS},
    )
    return parse_keywords(result.get("Keywords") or [])


def parse_keywords(raw: list[dict]) -> list[dict]:
    """Фразы из ответа keywords.get → строки для upsert_keywords."""
    items = []
    for item in raw:
        kid = item.get("Id")
        if kid is None:
            continue
        bid = item.get("Bid")
        items.append(
            {
                "keyword_id": int(kid),
                "adgroup_id": int(item.get("AdGroupId") or 0),
                "keyword": str(item.get("Keyword") or ""),
                "state": str(item.get("State") or ""),
                "status": str(item.get("Status") or ""),
                "bid_micros": int(bid) if bid is not None else None,
            }
        )
    return items


def upsert_keywords(
    user_id: int,
    account_id: int,
    adgroup_row_by_direct_id: dict[int, int],
    items: list[dict],
) -> int:
    count = 0
    with pool.connection() as conn:
        for item in items:
            adgroup_row_id = adgroup_row_by_direct_id.get(int(item["adgroup_id"]))
            if not adgroup_row_id:
                continue
            conn.execute(
                """
                INSERT INTO direct_keywords (
                    user_id, account_id, adgroup_row_id, keyword_id,
                    keyword, state, status, bid_micros, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id, keyword_id) DO UPDATE SET
                    adgroup_row_id = EXCLUDED.adgroup_row_id,
                    keyword = EXCLUDED.keyword,
                    state = EXCLUDED.state,
                    status = EXCLUDED.status,
                    bid_micros = EXCLUDED.bid_micros,
                    synced_at = NOW()
                """,
                (
                    user_id,
                    account_id,
                    adgroup_row_id,
                    item["keyword_id"],
                    item.get("keyword") or "",
                    item.get("state") or "",
                    item.get("status") or "",
                    item.get("bid_micros"),
                ),
            )
            count += 1
    return count


def sync_campaign_keywords(user_id: int, campaign_row_id: int) -> dict:
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
        return {"keywords": 0, "status": "ok"}
    run_id = start_run(user_id=user_id, kind="direct_keywords", target_id=campaign_row_id)
    try:
        token = get_connection_access_token(groups[0]["connection_id"], user_id)
        client_login = groups[0]["client_login"]
        account_id = groups[0]["account_id"]
        map_ids = {int(g["adgroup_id"]): int(g["id"]) for g in groups}
        items = fetch_keywords(token, client_login, list(map_ids.keys()))
        n = upsert_keywords(user_id, account_id, map_ids, items)
        finish_run(run_id, status="ok", meta={"keywords": n})
        return {"keywords": n, "status": "ok"}
    except YandexError as exc:
        finish_run(run_id, status="error", error=exc.code)
        raise
    except Exception as exc:
        log.exception("sync keywords")
        finish_run(run_id, status="error", error="direct")
        raise YandexError("direct") from exc


def list_keywords(
    user_id: int,
    *,
    campaign_row_id: int | None = None,
    adgroup_row_id: int | None = None,
) -> list[dict]:
    clauses = ["k.user_id = %s"]
    params: list = [user_id]
    if adgroup_row_id is not None:
        clauses.append("k.adgroup_row_id = %s")
        params.append(adgroup_row_id)
    if campaign_row_id is not None:
        clauses.append("g.campaign_row_id = %s")
        params.append(campaign_row_id)
    where = " AND ".join(clauses)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT k.*, g.name AS adgroup_name, g.adgroup_id
            FROM direct_keywords k
            JOIN direct_adgroups g ON g.id = k.adgroup_row_id
            WHERE {where}
            ORDER BY k.keyword ASC, k.id DESC
            """,
            params,
        ).fetchall()
    return [_public(r) for r in rows]


def create_keywords(
    user_id: int,
    *,
    adgroup_row_id: int,
    phrases: list[str],
    bid_rub: float | None = None,
) -> dict:
    cleaned = []
    for raw in phrases:
        phrase = (raw or "").strip()
        if phrase and phrase not in cleaned:
            cleaned.append(phrase[:4096])
    if not cleaned:
        raise YandexError("empty")
    if len(cleaned) > 100:
        raise YandexError("limit")
    group = _adgroup_row(user_id, adgroup_row_id)
    bid_micros = _rub_to_micros(bid_rub)
    token = get_connection_access_token(group["connection_id"], user_id)
    payload_items = []
    for phrase in cleaned:
        item: dict = {"Keyword": phrase, "AdGroupId": int(group["adgroup_id"])}
        if bid_micros is not None:
            item["Bid"] = bid_micros
        payload_items.append(item)
    result = _call(token, group["client_login"], "add", {"Keywords": payload_items})
    created = []
    errors = 0
    first_err = ""
    with pool.connection() as conn:
        for idx, add in enumerate(result.get("AddResults") or []):
            if not add.get("Id"):
                errors += 1
                log.warning("keywords.add item fail: %s", add)
                for e in add.get("Errors") or []:
                    msg = str(e.get("Message") or e.get("Details") or "").strip()
                    if msg and not first_err:
                        first_err = msg
                continue
            kid = int(add["Id"])
            phrase = cleaned[idx] if idx < len(cleaned) else ""
            row = conn.execute(
                """
                INSERT INTO direct_keywords (
                    user_id, account_id, adgroup_row_id, keyword_id,
                    keyword, state, status, bid_micros, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, 'ON', '', %s, NOW())
                ON CONFLICT (account_id, keyword_id) DO UPDATE SET
                    keyword = EXCLUDED.keyword,
                    bid_micros = EXCLUDED.bid_micros,
                    synced_at = NOW()
                RETURNING *
                """,
                (
                    user_id,
                    group["account_id"],
                    adgroup_row_id,
                    kid,
                    phrase,
                    bid_micros,
                ),
            ).fetchone()
            created.append(_public(row))
    if not created and errors:
        raise YandexError("direct", first_err or None)
    log_action(
        user_id,
        "keywords",
        {
            "adgroup_row_id": adgroup_row_id,
            "campaign_row_id": group["campaign_row_id"],
            "phrases": cleaned[:50],
            "bid_rub": bid_rub,
        },
        {"created": len(created), "errors": errors, "message": first_err},
        "partial" if errors else "ok",
        title=f"Фразы в группу «{group['name']}»: +{len(created)}",
        undo={
            "label": "Удалить добавленные фразы",
            "danger": True,
            "steps": [{"op": "keywords_action", "args": {"keyword_row_ids": [c["id"] for c in created], "action": "delete"}}],
        },
        targets=[f"keyword:{c['id']}" for c in created] + [f"adgroup:{adgroup_row_id}"],
    )
    return {"items": created, "created": len(created), "errors": errors, "status": "ok"}


def update_keyword(
    user_id: int,
    keyword_row_id: int,
    *,
    bid_rub: float | None = None,
    keyword: str | None = None,
) -> dict:
    row = _keyword_row(user_id, keyword_row_id)
    patch: dict = {"Id": int(row["keyword_id"])}
    bid_micros = None
    keyword_n = None
    if bid_rub is not None:
        bid_micros = _rub_to_micros(bid_rub)
        if bid_micros is None:
            raise YandexError("budget")
        patch["Bid"] = bid_micros
    if keyword is not None:
        keyword_n = (keyword or "").strip()[:4096]
        if not keyword_n:
            raise YandexError("empty")
        patch["Keyword"] = keyword_n
    if len(patch) == 1:
        raise YandexError("empty")
    token = get_connection_access_token(row["connection_id"], user_id)
    _call(token, row["client_login"], "update", {"Keywords": [patch]})
    sets = []
    params: list = []
    if keyword_n is not None:
        sets.append("keyword = %s")
        params.append(keyword_n)
    if bid_micros is not None:
        sets.append("bid_micros = %s")
        params.append(bid_micros)
    sets.append("synced_at = NOW()")
    params.extend([keyword_row_id, user_id])
    with pool.connection() as conn:
        updated = conn.execute(
            f"""
            UPDATE direct_keywords SET {', '.join(sets)}
            WHERE id = %s AND user_id = %s
            RETURNING *
            """,
            params,
        ).fetchone()
    back: dict = {}
    what = []
    if keyword_n is not None:
        what.append(f"текст «{keyword_n}»")
        if keyword_n != row["keyword"]:
            back["keyword"] = row["keyword"]
    if bid_micros is not None:
        what.append(f"ставка {bid_micros / 1_000_000:g} ₽")
        if row.get("bid_micros") is not None and int(row["bid_micros"]) != bid_micros:
            back["bid_rub"] = int(row["bid_micros"]) / 1_000_000
    log_action(
        user_id,
        "keywords",
        {"keyword_row_ids": [keyword_row_id], "campaign_row_ids": campaigns_of(user_id, "keyword", [keyword_row_id])},
        {"keyword": keyword_n, "bid_micros": bid_micros},
        "ok",
        title=f"Фраза «{row['keyword']}»: {', '.join(what)}",
        undo={
            "label": "Вернуть как было",
            "steps": [{"op": "keyword_update", "args": {"keyword_row_id": keyword_row_id, **back}}] if back else [],
        },
        targets=[f"keyword:{keyword_row_id}"],
    )
    return _public(updated)


def bulk_keyword_action(
    user_id: int,
    *,
    keyword_row_ids: list[int],
    action: str,
    bid_rub: float | None = None,
) -> dict:
    if action not in ("suspend", "resume", "delete", "set_bid"):
        raise YandexError("action")
    if not keyword_row_ids:
        raise YandexError("empty")
    if len(keyword_row_ids) > 100:
        raise YandexError("limit")
    bid_micros = None
    if action == "set_bid":
        bid_micros = _rub_to_micros(bid_rub)
        if bid_micros is None:
            raise YandexError("budget")
    rows = [_keyword_row(user_id, rid) for rid in keyword_row_ids]
    campaign_ids = campaigns_of(user_id, "keyword", [r["id"] for r in rows])
    by_login: dict[str, list[dict]] = {}
    for r in rows:
        by_login.setdefault(r["client_login"], []).append(r)
    applied = 0
    done: list[dict] = []  # строки до изменения — для отката
    failure: YandexError | None = None
    for client_login, group in by_login.items():
        try:
            _apply_group(user_id, client_login, group, action, bid_micros)
        except YandexError as exc:
            failure = failure or exc
            continue
        done.extend(group)
        applied += len(group)
    status = "ok" if failure is None else ("partial" if done else "error")
    log_action(
        user_id,
        "keywords",
        {"keyword_row_ids": [r["id"] for r in rows], "campaign_row_ids": campaign_ids, "action": action, "bid_rub": bid_rub},
        {"applied": applied, "error": failure.code if failure else None},
        status,
        title=_bulk_title(action, rows, bid_micros),
        undo=_bulk_undo(action, done, bid_micros),
        targets=[f"keyword:{r['id']}" for r in rows],
    )
    if failure is not None and not done:
        raise failure
    return {"applied": applied, "status": status}


def _apply_group(user_id: int, client_login: str, group: list[dict], action: str, bid_micros: int | None) -> None:
    token = get_connection_access_token(group[0]["connection_id"], user_id)
    ids = [r["id"] for r in group]
    if action == "set_bid":
        payload = [{"Id": int(r["keyword_id"]), "Bid": bid_micros} for r in group]
        _call(token, client_login, "update", {"Keywords": payload})
        with pool.connection() as conn:
            conn.execute(
                "UPDATE direct_keywords SET bid_micros = %s, synced_at = NOW() WHERE id = ANY(%s)",
                (bid_micros, ids),
            )
        return
    _call(token, client_login, action, {"SelectionCriteria": {"Ids": [int(r["keyword_id"]) for r in group]}})
    with pool.connection() as conn:
        if action == "delete":
            conn.execute("DELETE FROM direct_keywords WHERE user_id = %s AND id = ANY(%s)", (user_id, ids))
        else:
            state = "SUSPENDED" if action == "suspend" else "ON"
            conn.execute("UPDATE direct_keywords SET state = %s, synced_at = NOW() WHERE id = ANY(%s)", (state, ids))


_BULK_TITLES = {"suspend": "Остановка фраз", "resume": "Запуск фраз", "delete": "Удаление фраз"}


def _bulk_title(action: str, rows: list[dict], bid_micros: int | None) -> str:
    names = ", ".join(f"«{r['keyword']}»" for r in rows[:3]) + (f" и ещё {len(rows) - 3}" if len(rows) > 3 else "")
    if action == "set_bid":
        return f"Ставка {(bid_micros or 0) / 1_000_000:g} ₽: {names}"
    return f"{_BULK_TITLES[action]}: {names}"


def _bulk_undo(action: str, done: list[dict], bid_micros: int | None) -> dict | None:
    """Обратное действие только для фраз, что реально изменились."""

    def ids(state: str) -> list[int]:
        return [r["id"] for r in done if r.get("state") == state]

    if action == "suspend":
        steps = [{"op": "keywords_action", "args": {"keyword_row_ids": ids("ON"), "action": "resume"}}]
        return {"label": "Запустить снова", "steps": steps if ids("ON") else []}
    if action == "resume":
        steps = [{"op": "keywords_action", "args": {"keyword_row_ids": ids("SUSPENDED"), "action": "suspend"}}]
        return {"label": "Остановить снова", "steps": steps if ids("SUSPENDED") else []}
    if action == "set_bid":
        bids = {
            str(r["id"]): int(r["bid_micros"])
            for r in done
            if r.get("bid_micros") is not None and int(r["bid_micros"]) != bid_micros
        }
        return {"label": "Вернуть прежние ставки", "steps": [{"op": "keyword_bids", "args": {"bids": bids}}] if bids else []}
    items = [
        {"adgroup_row_id": r["adgroup_row_id"], "keyword": r["keyword"], "bid_micros": r.get("bid_micros")}
        for r in done
        if r.get("keyword")
    ]
    # Удалённое Директ не восстанавливает: фразы добавятся заново, без статистики.
    return {"label": "Добавить фразы заново", "steps": [{"op": "keywords_recreate", "args": {"items": items}}] if items else []}


def _keyword_row(user_id: int, keyword_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT k.*, a.connection_id, a.client_login, a.enabled
            FROM direct_keywords k
            JOIN direct_accounts a ON a.id = k.account_id
            WHERE k.id = %s AND k.user_id = %s
            """,
            (keyword_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def _public(row: dict) -> dict:
    bid = row.get("bid_micros")
    cost = int(row.get("period_cost_kopecks") or 0)
    clicks = int(row.get("period_clicks") or 0)
    conv = int(row.get("period_conversions") or 0)
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "adgroup_row_id": row["adgroup_row_id"],
        "keyword_id": row["keyword_id"],
        "keyword": row.get("keyword") or "",
        "state": row.get("state") or "",
        "status": row.get("status") or "",
        "bid_micros": int(bid) if bid is not None else None,
        "bid_rub": round(int(bid) / 1_000_000, 2) if bid is not None else None,
        "adgroup_name": row.get("adgroup_name") or "",
        "adgroup_id": row.get("adgroup_id"),
        "period_impressions": int(row.get("period_impressions") or 0),
        "period_clicks": clicks,
        "period_cost_kopecks": cost,
        "period_conversions": conv,
        "period_cpc_rub": round(cost / 100 / clicks, 2) if clicks else None,
        "period_cpa_rub": round(cost / 100 / conv, 2) if conv else None,
        "stats_from": row["stats_from"].isoformat() if row.get("stats_from") else None,
        "stats_to": row["stats_to"].isoformat() if row.get("stats_to") else None,
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
    }
