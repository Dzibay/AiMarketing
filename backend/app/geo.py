"""Регионы Директа: справочник GeoRegions, поиск по названию, подписи и замена регионов кампании.

Справочник общий для всех: dictionaries.get отдаёт его любому токену Директа. Держим его в памяти
процесса сутки — регионы меняются редко, а ответ тяжёлый. Минус перед id исключает регион:
[225, -1] — Россия без Москвы и области.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from app import direct_api
from app.db import pool
from app.journal import log_action
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.geo")

CACHE_TTL = 24 * 3600
RETRY_AFTER = 300
SEARCH_LIMIT = 20
HINT = "Минус перед id исключает регион: [225, -1] — Россия без Москвы и области"

# Подписи частых регионов, если справочник Директа недоступен.
KNOWN_REGIONS = {
    225: "Россия",
    1: "Москва и Московская область",
    213: "Москва",
    10174: "Санкт-Петербург и Ленинградская область",
    2: "Санкт-Петербург",
    54: "Екатеринбург",
    65: "Новосибирск",
    43: "Казань",
    47: "Нижний Новгород",
    35: "Краснодар",
}
_TYPE_ORDER = {"Country": 0, "Region": 1, "Administrative area": 1, "City": 2}
_TYPE_LABELS = {
    "Country": "страна",
    "Region": "округ",
    "Administrative area": "область",
    "City": "город",
    "Village": "населённый пункт",
    "City district": "район города",
}

_lock = threading.Lock()
_cache: dict = {"regions": {}, "next": 0.0}


def _any_token(user_id: int):
    """Токен любого включённого кабинета пользователя, кроме песочницы; None — кабинетов нет."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT a.connection_id
            FROM direct_accounts a JOIN yandex_connections y ON y.id = a.connection_id
            WHERE a.user_id = %s AND a.enabled AND NOT y.sandbox
            ORDER BY a.connection_id
            """,
            (user_id,),
        ).fetchall()
    for row in rows:
        try:
            return get_connection_access_token(row["connection_id"], user_id)
        except YandexError:
            continue
    return None


def _parse(result: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in result.get("GeoRegions") or []:
        try:
            rid = int(item["GeoRegionId"])
        except (KeyError, TypeError, ValueError):
            continue
        parent = item.get("ParentId")
        out[rid] = {
            "id": rid,
            "name": str(item.get("GeoRegionName") or rid),
            "type": str(item.get("GeoRegionType") or ""),
            "parent": int(parent) if str(parent or "").lstrip("-").isdigit() else None,
        }
    return out


def load_regions(user_id: int) -> dict[int, dict]:
    """Справочник {id: {id, name, type, parent}}; пустой — Директ недоступен."""
    with _lock:
        if time.monotonic() < _cache["next"]:
            return _cache["regions"]
        token = _any_token(user_id)
        if token is None:
            return _cache["regions"]
        try:
            fresh = _parse(direct_api.call(token, None, "dictionaries", "get", {"DictionaryNames": ["GeoRegions"]}))
        except Exception:
            log.warning("Справочник регионов Директа недоступен", exc_info=True)
            fresh = {}
        if fresh:
            _cache.update(regions=fresh, next=time.monotonic() + CACHE_TTL)
        else:
            # Прежний справочник лучше никакого; повторим попытку через несколько минут.
            _cache["next"] = time.monotonic() + RETRY_AFTER
        return _cache["regions"]


def _norm(text) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def _name(regions: dict[int, dict], rid: int) -> str:
    region = regions.get(rid)
    return region["name"] if region else KNOWN_REGIONS.get(rid, str(rid))


def _path(regions: dict[int, dict], region: dict) -> str:
    """Родители региона сверху вниз без «весь мир» и континентов: «Россия › Приволжский ФО»."""
    names: list[str] = []
    parent = region.get("parent")
    seen = {region["id"]}
    while parent and parent in regions and parent not in seen and len(names) < 4:
        seen.add(parent)
        item = regions[parent]
        if item["type"] not in ("World", "Continent"):
            names.append(item["name"])
        parent = item.get("parent")
    return " › ".join(reversed(names))


def search_regions(user_id: int, query: str, limit: int = SEARCH_LIMIT) -> dict:
    """Регионы по названию: точное совпадение, затем начало названия, затем вхождение."""
    q = _norm(query)
    if not q:
        raise YandexError("args", "Укажите название региона или города")
    regions = load_regions(user_id)
    source = regions or {
        rid: {"id": rid, "name": name, "type": "", "parent": None} for rid, name in KNOWN_REGIONS.items()
    }
    ranked = []
    for region in source.values():
        name = _norm(region["name"])
        if name == q:
            rank = 0
        elif name.startswith(q):
            rank = 1
        elif q in name:
            rank = 2
        else:
            continue
        ranked.append((rank, _TYPE_ORDER.get(region["type"], 3), len(name), region["id"], region))
    ranked.sort(key=lambda item: item[:4])
    out: dict = {
        "regions": [
            {
                "id": region["id"],
                "name": region["name"],
                "type": _TYPE_LABELS.get(region["type"], region["type"]),
                "path": _path(source, region),
            }
            for *_, region in ranked[: max(1, min(limit, 50))]
        ],
        "hint": HINT,
    }
    if not regions:
        out["note"] = "Справочник регионов Директа недоступен — искал только среди основных регионов"
    elif not out["regions"]:
        out["note"] = "Ничего не нашлось — попробуй другое написание или регион крупнее"
    return out


def parse_region_ids(raw) -> list[int]:
    """Список id регионов из аргументов: числа без повторов, 0 отбрасывается."""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            rid = int(value)
        except (TypeError, ValueError):
            continue
        if rid and rid not in out:
            out.append(rid)
    return out


def unknown_regions(user_id: int, region_ids: list[int]) -> list[int]:
    """id, которых нет в справочнике; справочник недоступен — проверить нечем, пусто."""
    regions = load_regions(user_id)
    if not regions:
        return []
    return [rid for rid in region_ids if abs(rid) not in regions]


def region_label(region_ids: list[int], regions: dict[int, dict] | None = None, limit: int = 6) -> str:
    """«Москва, Санкт-Петербург кроме Зеленограда»; пустой список — «—»."""
    regions = regions or {}
    plus = [rid for rid in region_ids if rid > 0]
    minus = [-rid for rid in region_ids if rid < 0]

    def names(ids: list[int]) -> str:
        text = ", ".join(_name(regions, rid) for rid in ids[:limit])
        return text + (f" и ещё {len(ids) - limit}" if len(ids) > limit else "")

    text = names(plus)
    if minus:
        text = (text + " " if text else "") + "кроме " + names(minus)
    return text or "—"


def campaign_regions(user_id: int, campaign_row_id: int) -> list[int]:
    """Регионы групп кампании по последнему синку — объединение по всем группам."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT region_ids FROM direct_adgroups WHERE user_id = %s AND campaign_row_id = %s ORDER BY id",
            (user_id, campaign_row_id),
        ).fetchall()
    out: list[int] = []
    for row in rows:
        raw = row.get("region_ids") or "[]"
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        for rid in parse_region_ids(ids):
            if rid not in out:
                out.append(rid)
    return out


def _campaign(user_id: int, campaign_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, a.connection_id, a.client_login, a.enabled
            FROM direct_campaigns c JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.id = %s AND c.user_id = %s
            """,
            (campaign_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def set_campaign_regions(
    user_id: int, campaign_row_id: int, region_ids: list[int], *, workspace_id: int | None = None
) -> dict:
    """Ставит регионы всем группам кампании в Директе и в локальной копии групп.

    Группы читаем из Директа, а не из базы: у свежей копии локальные группы могут быть ещё не
    загружены. Ошибки отдельных групп возвращаются списком, остальные группы обновляются.
    """
    if not any(rid > 0 for rid in region_ids):
        raise YandexError("args", "Нужен хотя бы один регион показа (id без минуса)")
    camp = _campaign(user_id, campaign_row_id)
    token = get_connection_access_token(camp["connection_id"], user_id)
    groups = direct_api.get_all(
        token,
        camp["client_login"],
        "adgroups",
        {"SelectionCriteria": {"CampaignIds": [int(camp["campaign_id"])]}, "FieldNames": ["Id", "RegionIds"]},
        "AdGroups",
    )
    groups = [g for g in groups if g.get("Id")]
    if not groups:
        raise YandexError("empty", "В кампании нет групп — регионы задаются в группах")
    before: list[int] = []
    for group in groups:
        for rid in parse_region_ids(group.get("RegionIds") or []):
            if rid not in before:
                before.append(rid)
    updated: list[int] = []
    errors: list[str] = []
    for chunk in direct_api.chunks(groups, 1000):
        result = direct_api.call(
            token,
            camp["client_login"],
            "adgroups",
            "update",
            {"AdGroups": [{"Id": int(g["Id"]), "RegionIds": region_ids} for g in chunk]},
        )
        failed = dict(direct_api.item_errors(result, "UpdateResults"))
        for index, group in enumerate(chunk):
            if index in failed:
                errors.append(f"группа {group['Id']}: {failed[index]}")
            else:
                updated.append(int(group["Id"]))
    if updated:
        with pool.connection() as conn:
            conn.execute(
                """
                UPDATE direct_adgroups SET region_ids = %s, synced_at = NOW()
                WHERE user_id = %s AND account_id = %s AND adgroup_id = ANY(%s)
                """,
                (json.dumps(region_ids), user_id, camp["account_id"], updated),
            )
    regions = load_regions(user_id)
    log_action(
        user_id,
        "adgroups",
        {"campaign_row_id": campaign_row_id, "region_ids": region_ids},
        {"groups": len(groups), "updated": len(updated), "errors": errors[:20]},
        "ok" if not errors else ("partial" if updated else "error"),
        workspace_id=workspace_id,
        title=f"Регионы «{camp['name']}»: {region_label(before, regions)} → {region_label(region_ids, regions)}",
        targets=[f"campaign:{campaign_row_id}"],
    )
    return {"groups": len(groups), "updated": len(updated), "errors": errors[:20]}
