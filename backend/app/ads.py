"""Объявления Яндекс Директа: текстовые (TextAd), комбинаторные (ResponsiveAd) и графические.

В ЕПК с 30.06.2026 текстово-графические объявления только редактируются, новые — комбинаторные
(RESPONSIVE_AD): до 7 заголовков, до 3 текстов, до 5 картинок. Читаем их через
ResponsiveAdFieldNames: без него Директ отдаёт комбинаторное как TextAd с первым заголовком,
текстом и картинкой, а правка через TextAd оставляет в объявлении по одному варианту.
"""

from __future__ import annotations

import logging

from psycopg.types.json import Jsonb

from app import direct_api
from app.adgroups import _adgroup_row
from app.db import pool
from app.journal import campaigns_of, log_action
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.ads")


def _call(access_token: str, client_login: str, method: str, params: dict) -> dict:
    # Комбинаторные add/update v5 отклоняет («не поддерживается в v5, используйте v501»).
    responsive = any("ResponsiveAd" in ad for ad in params.get("Ads") or [])
    return direct_api.call(access_token, client_login, "ads", method, params, version="v501" if responsive else "v5")


AD_FIELDS = ["Id", "AdGroupId", "Type", "State", "Status"]
TEXT_AD_FIELDS = ["Title", "Title2", "Text", "Href", "AdImageHash"]
RESPONSIVE_AD_FIELDS = ["Titles", "Texts", "Href", "AdImages"]
TEXT_IMAGE_AD_FIELDS = ["Href", "AdImageHash"]
# Поля ads.get для списков: без ResponsiveAdFieldNames комбинаторные теряют варианты.
AD_GET_FIELDS = {
    "FieldNames": AD_FIELDS,
    "TextAdFieldNames": TEXT_AD_FIELDS,
    "ResponsiveAdFieldNames": RESPONSIVE_AD_FIELDS,
    "TextImageAdFieldNames": TEXT_IMAGE_AD_FIELDS,
}

# Пределы Директа: заголовок до 56 символов, текст до 81, у комбинаторного до 7 заголовков и 3 текстов.
TITLE_LEN = 56
TITLE2_LEN = 30
TEXT_LEN = 81
MAX_TITLES = 7
MAX_TEXTS = 3
# В AdGroupIds у ads.get не больше 1000 групп.
GET_GROUPS = 1000

_RESULT_KEYS = {
    "suspend": "SuspendResults",
    "resume": "ResumeResults",
    "archive": "ArchiveResults",
    "unarchive": "UnarchiveResults",
    "delete": "DeleteResults",
}


def clean_variants(values, limit: int, length: int) -> list[str]:
    """Варианты заголовков или текстов: без пустых и повторов, обрезанные до пределов Директа."""
    out: list[str] = []
    for value in values or []:
        text = str(value or "").strip()[:length].strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _href(value: str | None) -> str:
    href = (value or "").strip()[:1024]
    if href and not href.startswith("http://") and not href.startswith("https://"):
        href = "https://" + href
    return href


def _first_error(result: dict, key: str) -> str | None:
    errors = direct_api.item_errors(result, key)
    return errors[0][1] if errors else None


def fetch_ads(access_token: str, client_login: str, adgroup_ids: list[int]) -> list[dict]:
    items: list[dict] = []
    for part in direct_api.chunks(list(adgroup_ids), GET_GROUPS):
        items.extend(
            direct_api.get_all(
                access_token, client_login, "ads", {"SelectionCriteria": {"AdGroupIds": part}, **AD_GET_FIELDS}, "Ads"
            )
        )
    return parse_ads(items)


def fetch_ad(access_token: str, client_login: str, ad_id: int) -> dict | None:
    """Объявление как оно сейчас в Директе — или None, если его там нет."""
    result = _call(access_token, client_login, "get", {"SelectionCriteria": {"Ids": [int(ad_id)]}, **AD_GET_FIELDS})
    found = parse_ads(result.get("Ads") or [])
    return found[0] if found else None


def parse_ads(raw: list[dict]) -> list[dict]:
    """Объявления из ответа ads.get → строки для upsert_ads."""
    items = []
    for item in raw:
        aid = item.get("Id")
        if aid is None:
            continue
        ad_type = str(item.get("Type") or "")
        responsive = item.get("ResponsiveAd") or {}
        text_ad = item.get("TextAd") or {}
        image_ad = item.get("TextImageAd") or {}
        title2 = ""
        if responsive:
            ad_type = ad_type if ad_type and ad_type != "TEXT_AD" else "RESPONSIVE_AD"
            titles = [str(t.get("Title") or "") for t in responsive.get("Titles") or [] if t.get("Title")]
            texts = [str(t.get("Text") or "") for t in responsive.get("Texts") or [] if t.get("Text")]
            images = [
                str(i.get("ImageHash"))
                for i in (responsive.get("AdImages") or {}).get("Items") or []
                if i.get("ImageHash")
            ]
            href = responsive.get("Href")
        elif text_ad:
            titles = [str(text_ad["Title"])] if text_ad.get("Title") else []
            texts = [str(text_ad["Text"])] if text_ad.get("Text") else []
            images = [str(text_ad["AdImageHash"])] if text_ad.get("AdImageHash") else []
            title2 = str(text_ad.get("Title2") or "")
            href = text_ad.get("Href")
        else:
            titles, texts = [], []
            images = [str(image_ad["AdImageHash"])] if image_ad.get("AdImageHash") else []
            href = image_ad.get("Href")
        items.append(
            {
                "ad_id": int(aid),
                "adgroup_id": int(item.get("AdGroupId") or 0),
                "type": ad_type,
                "state": str(item.get("State") or ""),
                "status": str(item.get("Status") or ""),
                "title": titles[0] if titles else "",
                "title2": title2,
                "text": texts[0] if texts else "",
                "href": str(href or ""),
                "titles": titles,
                "texts": texts,
                "image_hashes": images,
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
                    type, state, status, title, title2, text, href,
                    titles, texts, image_hashes, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (account_id, ad_id) DO UPDATE SET
                    adgroup_row_id = EXCLUDED.adgroup_row_id,
                    type = EXCLUDED.type,
                    state = EXCLUDED.state,
                    status = EXCLUDED.status,
                    title = EXCLUDED.title,
                    title2 = EXCLUDED.title2,
                    text = EXCLUDED.text,
                    href = EXCLUDED.href,
                    titles = EXCLUDED.titles,
                    texts = EXCLUDED.texts,
                    image_hashes = EXCLUDED.image_hashes,
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
                    Jsonb(list(item.get("titles") or [])),
                    Jsonb(list(item.get("texts") or [])),
                    Jsonb(list(item.get("image_hashes") or [])),
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


def _is_unified(group: dict) -> bool:
    return group.get("type") == "UNIFIED_AD_GROUP" or group.get("campaign_type") == "UNIFIED_CAMPAIGN"


def create_text_ad(
    user_id: int,
    *,
    adgroup_row_id: int,
    href: str,
    title: str = "",
    text: str = "",
    title2: str = "",
    titles: list[str] | None = None,
    texts: list[str] | None = None,
) -> dict:
    """Новое объявление: в ЕПК — комбинаторное со всеми вариантами, в остальных — TextAd."""
    titles_n = clean_variants(titles or [title], MAX_TITLES, TITLE_LEN)
    texts_n = clean_variants(texts or [text], MAX_TEXTS, TEXT_LEN)
    href_n = _href(href)
    title2_n = (title2 or "").strip()[:TITLE2_LEN]
    if not titles_n or not texts_n or not href_n:
        raise YandexError("empty")
    group = _adgroup_row(user_id, adgroup_row_id)
    token = get_connection_access_token(group["connection_id"], user_id)
    if _is_unified(group):
        ad_type = "RESPONSIVE_AD"
        title2_n = ""  # в комбинаторном объявлении дополнительного заголовка нет
        payload = {"ResponsiveAd": {"Titles": titles_n, "Texts": texts_n, "Href": href_n}}
    else:
        ad_type = "TEXT_AD"
        titles_n, texts_n = titles_n[:1], texts_n[:1]
        text_ad: dict = {"Title": titles_n[0], "Text": texts_n[0], "Href": href_n, "Mobile": "NO"}
        if title2_n:
            text_ad["Title2"] = title2_n
        payload = {"TextAd": text_ad}
    result = _call(token, group["client_login"], "add", {"Ads": [{"AdGroupId": int(group["adgroup_id"]), **payload}]})
    error = _first_error(result, "AddResults")
    add = (result.get("AddResults") or [{}])[0]
    if error or not add.get("Id"):
        log.warning("ads.add fail: %s", add)
        raise YandexError("direct", error)
    ad_id = int(add["Id"])
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO direct_ads (
                user_id, account_id, adgroup_row_id, ad_id,
                type, state, status, title, title2, text, href,
                titles, texts, synced_at
            )
            VALUES (%s, %s, %s, %s, %s, 'OFF', '', %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (account_id, ad_id) DO UPDATE SET
                type = EXCLUDED.type, title = EXCLUDED.title, title2 = EXCLUDED.title2,
                text = EXCLUDED.text, href = EXCLUDED.href,
                titles = EXCLUDED.titles, texts = EXCLUDED.texts, synced_at = NOW()
            RETURNING *
            """,
            (
                user_id,
                group["account_id"],
                adgroup_row_id,
                ad_id,
                ad_type,
                titles_n[0],
                title2_n,
                texts_n[0],
                href_n,
                Jsonb(titles_n),
                Jsonb(texts_n),
            ),
        ).fetchone()
    log_action(
        user_id,
        "ads",
        {
            "adgroup_row_id": adgroup_row_id,
            "campaign_row_id": group["campaign_row_id"],
            "title": titles_n[0],
            "titles": titles_n,
            "texts": texts_n,
            "href": href_n,
        },
        {"ad_id": ad_id},
        "ok",
        title=f"Объявление в группу «{group['name']}»: «{titles_n[0]}»",
        undo={
            "label": "Удалить объявление",
            "danger": True,
            "steps": [{"op": "ads_action", "args": {"ad_row_ids": [row["id"]], "action": "delete"}}],
        },
        targets=[f"ad:{row['id']}", f"adgroup:{adgroup_row_id}"],
    )
    return _public(row)


def _replace_first(current: list[str], value: str) -> list[str]:
    return [value] + current[1:] if current else [value]


def update_text_ad(
    user_id: int,
    ad_row_id: int,
    *,
    title: str | None = None,
    title2: str | None = None,
    text: str | None = None,
    href: str | None = None,
    titles: list[str] | None = None,
    texts: list[str] | None = None,
) -> dict:
    """Правка объявления. Комбинаторное правим через ResponsiveAd целиком — иначе Директ
    оставит в нём по одному заголовку, тексту и картинке."""
    row = _ad_row(user_id, ad_row_id)
    token = get_connection_access_token(row["connection_id"], user_id)
    # Читаем объявление из Директа: тип в базе мог устареть (ТГО обновляются до комбинаторных),
    # а варианты — поменяться в интерфейсе Директа после синхронизации.
    current = fetch_ad(token, row["client_login"], int(row["ad_id"])) or {
        "type": row.get("type") or "",
        "title": row.get("title") or "",
        "title2": row.get("title2") or "",
        "text": row.get("text") or "",
        "href": row.get("href") or "",
        "titles": list(row.get("titles") or []),
        "texts": list(row.get("texts") or []),
    }
    cur_titles = list(current.get("titles") or ([current["title"]] if current.get("title") else []))
    cur_texts = list(current.get("texts") or ([current["text"]] if current.get("text") else []))
    responsive = current.get("type") == "RESPONSIVE_AD"
    new_titles = None
    if titles is not None:
        new_titles = clean_variants(titles, MAX_TITLES if responsive else 1, TITLE_LEN)
    elif title is not None:
        new_titles = clean_variants(_replace_first(cur_titles, title), MAX_TITLES if responsive else 1, TITLE_LEN)
    new_texts = None
    if texts is not None:
        new_texts = clean_variants(texts, MAX_TEXTS if responsive else 1, TEXT_LEN)
    elif text is not None:
        new_texts = clean_variants(_replace_first(cur_texts, text), MAX_TEXTS if responsive else 1, TEXT_LEN)
    if new_titles == [] or new_texts == []:
        raise YandexError("empty")
    href_n = _href(href) if href is not None else None
    title2_n = (title2 or "").strip()[:TITLE2_LEN] if title2 is not None and not responsive else None

    changes: dict = {}
    if new_titles is not None:
        changes["titles"] = new_titles
    if new_texts is not None:
        changes["texts"] = new_texts
    if title2_n is not None:
        changes["title2"] = title2_n
    if href_n is not None:
        changes["href"] = href_n
    if not changes:
        raise YandexError("empty")

    if responsive:
        ad: dict = {}
        if new_titles is not None:
            ad["Titles"] = new_titles
        if new_texts is not None:
            ad["Texts"] = new_texts
        if href_n is not None:
            ad["Href"] = href_n
        if not ad:
            raise YandexError("empty")
        payload = {"ResponsiveAd": ad}
    else:
        ad = {}
        if new_titles is not None:
            ad["Title"] = new_titles[0]
        if title2_n is not None:
            ad["Title2"] = title2_n
        if new_texts is not None:
            ad["Text"] = new_texts[0]
        if href_n is not None:
            ad["Href"] = href_n
        payload = {"TextAd": ad}
    result = _call(token, row["client_login"], "update", {"Ads": [{"Id": int(row["ad_id"]), **payload}]})
    error = _first_error(result, "UpdateResults")
    if error:
        log.warning("ads.update fail: %s", result)
        raise YandexError("direct", error)

    final_titles = new_titles if new_titles is not None else cur_titles
    final_texts = new_texts if new_texts is not None else cur_texts
    with pool.connection() as conn:
        updated = conn.execute(
            """
            UPDATE direct_ads SET
                type = %s, title = %s, title2 = %s, text = %s, href = %s,
                titles = %s, texts = %s, synced_at = NOW()
            WHERE id = %s AND user_id = %s
            RETURNING *
            """,
            (
                current.get("type") or row.get("type") or "",
                final_titles[0] if final_titles else "",
                title2_n if title2_n is not None else ("" if responsive else current.get("title2") or ""),
                final_texts[0] if final_texts else "",
                href_n if href_n is not None else current.get("href") or "",
                Jsonb(final_titles),
                Jsonb(final_texts),
                ad_row_id,
                user_id,
            ),
        ).fetchone()
    previous = {
        "titles": cur_titles,
        "texts": cur_texts,
        "title2": current.get("title2") or "",
        "href": current.get("href") or "",
    }
    back = {key: previous[key] for key, value in changes.items() if value != previous[key]}
    log_action(
        user_id,
        "ads",
        {"ad_row_ids": [ad_row_id], "campaign_row_ids": campaigns_of(user_id, "ad", [ad_row_id]), "changes": changes},
        {"ad_id": row["ad_id"]},
        "ok",
        title=f"Правка объявления «{(cur_titles or [row.get('title')])[0] or row['ad_id']}»",
        undo={
            "label": "Вернуть прежний текст",
            "steps": [{"op": "ad_update", "args": {"ad_row_id": ad_row_id, **back}}] if back else [],
        },
        targets=[f"ad:{ad_row_id}"],
    )
    return _public(updated)


def bulk_ad_action(user_id: int, *, ad_row_ids: list[int], action: str) -> dict:
    if action not in ("suspend", "resume", "archive", "unarchive", "delete"):
        raise YandexError("action")
    if not ad_row_ids:
        raise YandexError("empty")
    rows = [_ad_row(user_id, rid) for rid in ad_row_ids]
    campaign_ids = campaigns_of(user_id, "ad", [r["id"] for r in rows])
    by_login: dict[str, list[dict]] = {}
    for r in rows:
        by_login.setdefault(r["client_login"], []).append(r)
    applied = 0
    done: list[dict] = []  # строки до изменения — для отката
    failure: YandexError | None = None
    for client_login, group in by_login.items():
        try:
            ok, error = _apply_group(user_id, client_login, group, action)
        except YandexError as exc:
            failure = failure or exc
            continue
        if error:
            failure = failure or YandexError("direct", error)
        done.extend(ok)
        applied += len(ok)
    status = "ok" if failure is None else ("partial" if done else "error")
    names = ", ".join(f"«{r.get('title') or r['ad_id']}»" for r in rows[:3]) + (
        f" и ещё {len(rows) - 3}" if len(rows) > 3 else ""
    )
    log_action(
        user_id,
        "ads",
        {"ad_row_ids": [r["id"] for r in rows], "campaign_row_ids": campaign_ids, "action": action},
        {"applied": applied, "error": failure.code if failure else None, "message": failure.message if failure else None},
        status,
        title=f"{_BULK_TITLES[action]}: {names}",
        undo=_bulk_undo(action, done),
        targets=[f"ad:{r['id']}" for r in rows],
    )
    if failure is not None and not done:
        raise failure
    return {"applied": applied, "status": status, "error": failure.message if failure else None}


_STATES = {"suspend": "SUSPENDED", "resume": "ON", "archive": "ARCHIVED", "unarchive": "OFF"}
_BULK_TITLES = {
    "suspend": "Остановка объявлений",
    "resume": "Запуск объявлений",
    "archive": "Архивация объявлений",
    "unarchive": "Возврат объявлений из архива",
    "delete": "Удаление объявлений",
}


def _apply_group(user_id: int, client_login: str, group: list[dict], action: str) -> tuple[list[dict], str | None]:
    """Действие над объявлениями одного логина → (что применилось, первая ошибка Директа)."""
    token = get_connection_access_token(group[0]["connection_id"], user_id)
    result = _call(token, client_login, action, {"SelectionCriteria": {"Ids": [int(r["ad_id"]) for r in group]}})
    errors = dict(direct_api.item_errors(result, _RESULT_KEYS[action]))
    ok = [r for index, r in enumerate(group) if index not in errors]
    ids = [r["id"] for r in ok]
    if ids:
        with pool.connection() as conn:
            if action == "delete":
                conn.execute("DELETE FROM direct_ads WHERE user_id = %s AND id = ANY(%s)", (user_id, ids))
            else:
                conn.execute(
                    "UPDATE direct_ads SET state = %s, synced_at = NOW() WHERE id = ANY(%s)",
                    (_STATES[action], ids),
                )
    if errors:
        log.warning("ads.%s: %s", action, errors)
    return ok, next(iter(errors.values()), None)


def _bulk_undo(action: str, done: list[dict]) -> dict | None:
    """Обратное действие только для объявлений, что реально изменились."""

    def step(ids: list[int], what: str) -> list[dict]:
        return [{"op": "ads_action", "args": {"ad_row_ids": ids, "action": what}}] if ids else []

    every = [r["id"] for r in done]
    was_on = [r["id"] for r in done if r.get("state") == "ON"]
    if action == "suspend":
        return {"label": "Запустить снова", "steps": step(was_on, "resume")}
    if action == "resume":
        return {"label": "Остановить снова", "steps": step([r["id"] for r in done if r.get("state") == "SUSPENDED"], "suspend")}
    if action == "archive":
        return {"label": "Вернуть из архива", "steps": step(every, "unarchive") + step(was_on, "resume")}
    if action == "unarchive":
        return {"label": "Вернуть в архив", "steps": step(every, "archive")}
    return None  # удаление не откатывается


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
    title = row.get("title") or ""
    text = row.get("text") or ""
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "adgroup_row_id": row["adgroup_row_id"],
        "ad_id": row["ad_id"],
        "type": row.get("type") or "",
        "state": row.get("state") or "",
        "status": row.get("status") or "",
        "title": title,
        "title2": row.get("title2") or "",
        "text": text,
        "href": row.get("href") or "",
        "titles": list(row.get("titles") or ([title] if title else [])),
        "texts": list(row.get("texts") or ([text] if text else [])),
        "image_hashes": list(row.get("image_hashes") or []),
        "adgroup_name": row.get("adgroup_name") or "",
        "adgroup_id": row.get("adgroup_id"),
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
    }
