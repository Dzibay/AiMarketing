"""Группы объявлений Яндекс Директа."""

from __future__ import annotations

import json
import logging

from app import direct_api, geo
from app.db import pool
from app.journal import log_action
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.adgroups")


def _campaign_row(user_id: int, campaign_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.user_id, c.account_id, c.campaign_id, c.name, c.campaign_type,
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
    # Группы ЕПК v5 не создаёт («не поддерживается в v5, используйте v501»).
    unified = any("UnifiedAdGroup" in g for g in params.get("AdGroups") or [])
    return direct_api.call(access_token, client_login, "adgroups", method, params, version="v501" if unified else "v5")


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


ADGROUP_FIELDS = ["Id", "Name", "CampaignId", "Status", "Type", "RegionIds", "NegativeKeywords", "TrackingParams"]


def parse_adgroups(raw: list[dict]) -> list[dict]:
    """Группы из ответа adgroups.get → строки для upsert_adgroups."""
    items = []
    for item in raw:
        gid = item.get("Id")
        if gid is None:
            continue
        regions = item.get("RegionIds") or []
        negatives = _negatives_from_api(item)
        items.append(
            {
                "adgroup_id": int(gid),
                "campaign_id": int(item.get("CampaignId") or 0),
                "name": str(item.get("Name") or ""),
                "status": str(item.get("Status") or ""),
                "type": str(item.get("Type") or ""),
                "region_ids": json.dumps(regions, ensure_ascii=False),
                "negative_keywords": json.dumps(negatives, ensure_ascii=False),
                "tracking_params": str(item.get("TrackingParams") or ""),
            }
        )
    return items


def fetch_adgroups(access_token: str, client_login: str, campaign_id: int) -> list[dict]:
    raw = direct_api.get_all(
        access_token,
        client_login,
        "adgroups",
        {"SelectionCriteria": {"CampaignIds": [campaign_id]}, "FieldNames": ADGROUP_FIELDS},
        "AdGroups",
    )
    return parse_adgroups(raw)


def upsert_adgroups(user_id: int, account_id: int, campaign_row_id: int, items: list[dict]) -> int:
    count = 0
    with pool.connection() as conn:
        for item in items:
            conn.execute(
                """
                INSERT INTO direct_adgroups (
                    user_id, account_id, campaign_row_id, adgroup_id,
                    name, status, type, region_ids, negative_keywords, tracking_params, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id, adgroup_id) DO UPDATE SET
                    campaign_row_id = EXCLUDED.campaign_row_id,
                    name = EXCLUDED.name,
                    status = EXCLUDED.status,
                    type = EXCLUDED.type,
                    region_ids = EXCLUDED.region_ids,
                    negative_keywords = EXCLUDED.negative_keywords,
                    tracking_params = COALESCE(EXCLUDED.tracking_params, direct_adgroups.tracking_params),
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
                    item.get("tracking_params"),
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


def campaign_regions(user_id: int, campaign_row_id: int) -> list[int]:
    """Регионы новой группы, когда их не задали: как у первой группы кампании, групп нет — Россия."""
    groups = [g for g in list_adgroups(user_id, campaign_row_id) if g["region_ids"]]
    if not groups:
        return [225]
    return [int(r) for r in min(groups, key=lambda g: int(g["id"]))["region_ids"]]


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
    regions = region_ids if region_ids else campaign_regions(user_id, campaign_row_id)
    token = get_connection_access_token(camp["connection_id"], user_id)
    group: dict = {"Name": name_n, "CampaignId": int(camp["campaign_id"]), "RegionIds": regions}
    # В ЕПК группа — UNIFIED_AD_GROUP, у неё обязательный OfferRetargeting.
    group_type = "TEXT_AD_GROUP"
    if camp.get("campaign_type") == "UNIFIED_CAMPAIGN":
        group["UnifiedAdGroup"] = {"OfferRetargeting": "NO"}
        group_type = "UNIFIED_AD_GROUP"
    result = _call(token, camp["client_login"], "add", {"AdGroups": [group]})
    errors = direct_api.item_errors(result, "AddResults")
    add = (result.get("AddResults") or [{}])[0]
    if errors or not add.get("Id"):
        log.warning("adgroups.add fail: %s", add)
        raise YandexError("direct", errors[0][1] if errors else None)
    adgroup_id = int(add["Id"])
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO direct_adgroups (
                user_id, account_id, campaign_row_id, adgroup_id,
                name, status, type, region_ids, synced_at
            )
            VALUES (%s, %s, %s, %s, %s, '', %s, %s, NOW())
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
                group_type,
                json.dumps(regions),
            ),
        ).fetchone()
    log_action(
        user_id,
        "adgroups",
        {"campaign_row_id": campaign_row_id, "name": name_n, "region_ids": regions},
        {"adgroup_id": adgroup_id},
        "ok",
        title=f"Группа «{name_n}» в «{camp['name']}»",
        undo={
            "label": "Удалить группу",
            "danger": True,
            "steps": [{"op": "adgroups_delete", "args": {"adgroup_row_ids": [row["id"]]}}],
        },
        targets=[f"adgroup:{row['id']}"],
    )
    return _public(row)


def update_adgroup(
    user_id: int,
    adgroup_row_id: int,
    *,
    name: str | None = None,
    negative_keywords: list[str] | str | None = None,
    tracking_params: str | None = None,
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
    tracking_n = None
    if tracking_params is not None:
        tracking_n = tracking_params.strip().lstrip("?")[:1024]
        patch["TrackingParams"] = tracking_n
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
    if tracking_n is not None:
        sets.append("tracking_params = %s")
        params.append(tracking_n)
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
    steps: list[dict] = []
    what = []
    if name_n is not None:
        what.append(f"название «{name_n}»")
        if name_n != row["name"]:
            steps.append({"op": "adgroup_update", "args": {"adgroup_row_id": adgroup_row_id, "name": row["name"]}})
    if tracking_n is not None:
        what.append("метки группы")
        if tracking_n != (row.get("tracking_params") or ""):
            steps.append(
                {"op": "adgroup_update", "args": {"adgroup_row_id": adgroup_row_id, "tracking_params": row.get("tracking_params") or ""}}
            )
    if negatives is not None:
        from app.negative_lists import phrase_key

        before = _parse_phrases(row.get("negative_keywords") or "[]")
        old_keys = {phrase_key(x) for x in before}
        new_keys = {phrase_key(x) for x in negatives}
        added = [x for x in negatives if phrase_key(x) not in old_keys]
        removed = [x for x in before if phrase_key(x) not in new_keys]
        what.append(f"минус-фразы +{len(added)} −{len(removed)}")
        if added or removed:
            steps.append(
                {"op": "negatives", "args": {"adgroups": {str(adgroup_row_id): {"add": removed, "remove": added}}}}
            )
    log_action(
        user_id,
        "adgroups",
        {"adgroup_row_id": adgroup_row_id, "campaign_row_id": row["campaign_row_id"], "name": name_n},
        {"negative_count": len(negatives) if negatives is not None else None},
        "ok",
        title=f"Группа «{row['name']}»: {', '.join(what)}",
        undo={"label": "Вернуть как было", "steps": steps},
        targets=[f"adgroup:{adgroup_row_id}"],
    )
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
    names = ", ".join(f"«{r['name']}»" for r in rows[:3]) + (f" и ещё {len(rows) - 3}" if len(rows) > 3 else "")
    log_action(
        user_id,
        "adgroups",
        {"adgroup_row_ids": [r["id"] for r in rows], "campaign_row_ids": sorted({r["campaign_row_id"] for r in rows})},
        {"applied": applied},
        "ok",
        title=f"Удаление групп: {names}",
        targets=[f"adgroup:{r['id']}" for r in rows],
    )
    return {"applied": applied, "status": "ok"}


def _adgroup_row(user_id: int, adgroup_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT g.*, c.campaign_type, a.connection_id, a.client_login, a.enabled
            FROM direct_adgroups g
            JOIN direct_accounts a ON a.id = g.account_id
            JOIN direct_campaigns c ON c.id = g.campaign_row_id
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


# --- Регионы показа ------------------------------------------------------------------------------------
#
# Регионы живут в группах (RegionIds); регионы кампании — объединение регионов её групп. Смена у кампании ставит
# один список всем её группам. Прежние регионы каждой группы читаем из Директа (локальная копия могла устареть) и
# кладём в откат журнала: «Отменить» вернёт каждой группе её собственный список.

REGIONS_MAX = 500
PLAN_ITEMS = 20


def _region_list(raw) -> list[int]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
    return geo.parse_region_ids(raw)


def _check_region_ids(user_id: int, region_ids) -> list[int]:
    ids = geo.parse_region_ids(region_ids)
    if not any(rid > 0 for rid in ids):
        raise YandexError("args", "Выберите хотя бы один регион показа — «кроме» работает только вместе с ним")
    if len(ids) > REGIONS_MAX:
        raise YandexError("args", f"Слишком много регионов: не больше {REGIONS_MAX}")
    unknown = geo.unknown_regions(user_id, ids)
    if unknown:
        raise YandexError("args", "Таких регионов нет в справочнике Директа: " + ", ".join(str(r) for r in unknown))
    return ids


_REGION_GROUPS_SQL = """
    SELECT g.id, g.adgroup_id, g.name, g.region_ids, g.account_id, g.campaign_row_id,
           c.name AS campaign_name, c.campaign_type, a.connection_id, a.client_login, a.enabled
    FROM direct_adgroups g
    JOIN direct_campaigns c ON c.id = g.campaign_row_id
    JOIN direct_accounts a ON a.id = g.account_id
    WHERE g.user_id = %s AND {where}
    ORDER BY g.name, g.adgroup_id
"""


def _refresh_campaign_groups(user_id: int, camp: dict) -> set[int] | None:
    """Группы кампании из Директа — в локальную копию: группа, добавленная в Директе после синка, тоже получит
    регионы и попадёт в «изменится в X из Y». → id групп в Директе; Директ недоступен — None (работаем по копии)."""
    try:
        token = get_connection_access_token(camp["connection_id"], user_id)
        items = fetch_adgroups(token, camp["client_login"], int(camp["campaign_id"]))
    except YandexError as exc:
        log.warning("регионы: группы кампании %s не обновились из Директа: %s", camp["id"], exc.code)
        return None
    except Exception:
        log.exception("регионы: группы кампании %s не обновились из Директа", camp["id"])
        return None
    upsert_adgroups(user_id, camp["account_id"], int(camp["id"]), items)
    return {int(item["adgroup_id"]) for item in items}


def _region_groups(
    user_id: int, *, campaign_row_id: int | None = None, adgroup_row_ids: list[int] | None = None
) -> tuple[list[dict], str]:
    """Группы, которым меняем регионы (с кабинетом и типом кампании), и подпись «кампании «…»» / «группы «…»»."""
    if (campaign_row_id is None) == (not adgroup_row_ids):
        raise YandexError("args", "Укажите кампанию или группы")
    if campaign_row_id is not None:
        camp = _campaign_row(user_id, int(campaign_row_id))
        live_ids = _refresh_campaign_groups(user_id, camp)
        with pool.connection() as conn:
            rows = conn.execute(
                _REGION_GROUPS_SQL.format(where="g.campaign_row_id = %s"), (user_id, int(campaign_row_id))
            ).fetchall()
        if live_ids is not None:
            # Удалённые в Директе группы остались в копии — их не трогаем и не считаем.
            rows = [r for r in rows if int(r["adgroup_id"]) in live_ids]
        if not rows:
            raise YandexError(
                "empty", "В кампании нет групп — регионы задаются в группах. Обновите структуру из Директа или создайте группу"
            )
        return rows, f"кампании «{camp['name']}»"
    ids = sorted({int(i) for i in adgroup_row_ids or []})
    with pool.connection() as conn:
        rows = conn.execute(_REGION_GROUPS_SQL.format(where="g.id = ANY(%s)"), (user_id, ids)).fetchall()
    if len(rows) < len(ids):
        raise YandexError("not-found")
    if not all(r.get("enabled") for r in rows):
        raise YandexError("disabled")
    if len(rows) == 1:
        return rows, f"группы «{rows[0]['name']}»"
    campaigns = {r["campaign_name"] for r in rows}
    return rows, f"групп кампании «{rows[0]['campaign_name']}»" if len(campaigns) == 1 else "нескольких групп"


def _union(lists) -> list[int]:
    out: list[int] = []
    for ids in lists:
        for rid in ids:
            if rid not in out:
                out.append(rid)
    return out


def regions_overview(user_id: int, campaign_row_id: int) -> dict:
    """Регионы кампании и её групп по последнему синку — с названиями, для карточки кампании."""
    camp = _campaign_row(user_id, campaign_row_id)
    groups = list_adgroups(user_id, campaign_row_id)
    regions = geo.load_regions(user_id)
    lists = [_region_list(g["region_ids"]) for g in groups]
    union = _union(lists)
    return {
        "campaign": {"id": camp["id"], "name": camp["name"]},
        "region_ids": union,
        "label": geo.region_label(union, regions) if groups else "—",
        "same": len({frozenset(ids) for ids in lists}) <= 1,
        "groups": [
            {"id": g["id"], "name": g["name"], "region_ids": ids, "label": geo.region_label(ids, regions)}
            for g, ids in zip(groups, lists)
        ],
        "selected": geo.regions_named(user_id, union),
    }


def regions_plan(
    user_id: int, region_ids, *, campaign_row_id: int | None = None, adgroup_row_ids: list[int] | None = None
) -> dict:
    """«Было → станет» без записи в Директ — для подтверждения в кабинете и карточки ИИ."""
    ids = _check_region_ids(user_id, region_ids)
    rows, what = _region_groups(user_id, campaign_row_id=campaign_row_id, adgroup_row_ids=adgroup_row_ids)
    regions = geo.load_regions(user_id)
    lists = [_region_list(r["region_ids"]) for r in rows]
    return {
        "what": what,
        "groups": len(rows),
        "changed": sum(1 for current in lists if set(current) != set(ids)),
        "mixed": len({frozenset(current) for current in lists}) > 1,
        "before": geo.region_label(_union(lists), regions),
        "after": geo.region_label(ids, regions),
        "items": [
            {"id": r["id"], "name": r["name"], "before": geo.region_label(current, regions)}
            for r, current in list(zip(rows, lists))[:PLAN_ITEMS]
        ],
    }


def set_regions(
    user_id: int,
    region_ids,
    *,
    campaign_row_id: int | None = None,
    adgroup_row_ids: list[int] | None = None,
    workspace_id: int | None = None,
) -> dict:
    """Ставит регионы группам кампании (или выбранным группам) в Директе и в локальной копии.

    В журнал — «было → станет» и откат к прежним регионам каждой группы. Группы, где регионы уже такие, не
    трогаем; ошибки отдельных групп возвращаются списком, остальные группы обновляются.
    """
    ids = _check_region_ids(user_id, region_ids)
    rows, what = _region_groups(user_id, campaign_row_id=campaign_row_id, adgroup_row_ids=adgroup_row_ids)
    by_account: dict[int, list[dict]] = {}
    for row in rows:
        by_account.setdefault(int(row["account_id"]), []).append(row)
    before: list[int] = []
    previous: dict[str, list[int]] = {}
    updated: list[int] = []
    same: list[int] = []
    errors: list[str] = []
    failure: YandexError | None = None
    for items in by_account.values():
        login = items[0]["client_login"]
        # Группы ЕПК v5 не правит («используйте v501»); v501 принимает и обычные группы.
        version = "v501" if any(r.get("campaign_type") == "UNIFIED_CAMPAIGN" for r in items) else "v5"
        live: dict[int, list[int]] = {}
        # Сбой кабинета (токен, чтение групп) — ошибка его групп, а не всего вызова: группы других кабинетов уже
        # могли поменяться, и им нужны запись в журнале и откат.
        try:
            token = get_connection_access_token(items[0]["connection_id"], user_id)
            for chunk in direct_api.chunks(items, 1000):
                got = direct_api.get_all(
                    token,
                    login,
                    "adgroups",
                    {
                        "SelectionCriteria": {"Ids": [int(r["adgroup_id"]) for r in chunk]},
                        "FieldNames": ["Id", "RegionIds"],
                    },
                    "AdGroups",
                )
                for group in got:
                    if group.get("Id") is not None:
                        live[int(group["Id"])] = geo.parse_region_ids(group.get("RegionIds") or [])
        except YandexError as exc:
            failure = failure or exc
            errors.extend(f"группа «{row['name']}»: {exc.message or exc.code}" for row in items)
            continue
        todo: list[tuple[dict, list[int]]] = []
        for row in items:
            current = live.get(int(row["adgroup_id"]))
            if current is None:
                errors.append(f"группа «{row['name']}»: её нет в Директе — обновите структуру кампании")
                continue
            before = _union([before, current])
            if set(current) == set(ids):
                same.append(int(row["id"]))
            else:
                todo.append((row, current))
        for chunk in direct_api.chunks(todo, 1000):
            try:
                result = direct_api.call(
                    token,
                    login,
                    "adgroups",
                    "update",
                    {"AdGroups": [{"Id": int(row["adgroup_id"]), "RegionIds": ids} for row, _current in chunk]},
                    version=version,
                )
            except YandexError as exc:
                failure = failure or exc
                errors.extend(f"группа «{row['name']}»: {exc.message or exc.code}" for row, _current in chunk)
                continue
            failed = dict(direct_api.item_errors(result, "UpdateResults"))
            for index, (row, current) in enumerate(chunk):
                if index in failed:
                    errors.append(f"группа «{row['name']}»: {failed[index]}")
                else:
                    updated.append(int(row["id"]))
                    previous[str(row["id"])] = current
    if updated or same:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE direct_adgroups SET region_ids = %s, synced_at = NOW() WHERE user_id = %s AND id = ANY(%s)",
                (json.dumps(ids), user_id, updated + same),
            )
    if errors and not updated:
        # Ничего не поменялось — журналу писать нечего, кабинет покажет причину.
        if failure is not None:
            raise failure
        raise YandexError("direct", errors[0])
    regions = geo.load_regions(user_id)
    out = {
        "groups": len(rows),
        "updated": len(updated),
        "unchanged": len(same),
        "errors": errors[:20],
        "before": geo.region_label(before, regions),
        "after": geo.region_label(ids, regions),
    }
    if not updated:
        return out
    campaign_ids = sorted({int(r["campaign_row_id"]) for r in rows})
    payload: dict = {"region_ids": ids, "adgroup_row_ids": [int(r["id"]) for r in rows][:200]}
    if len(campaign_ids) == 1:
        payload["campaign_row_id"] = campaign_ids[0]
    else:
        payload["campaign_row_ids"] = campaign_ids
    log_action(
        user_id,
        "adgroups",
        payload,
        {"groups": len(rows), "updated": len(updated), "errors": errors[:20]},
        "partial" if errors else "ok",
        workspace_id=workspace_id,
        title=f"Регионы {what}: {out['before']} → {out['after']}",
        undo={"label": "Вернуть прежние регионы", "steps": [{"op": "adgroup_regions", "args": {"regions": previous}}]},
        targets=[f"campaign:{campaign_row_id}"] if campaign_row_id is not None else [f"adgroup:{r['id']}" for r in rows],
    )
    return out
