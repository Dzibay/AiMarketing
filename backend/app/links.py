"""
Шаблон ссылки сайта: проверка ссылок кампаний и исправление.

Шаблон задаётся в настройках сайта, например
https://site.ru/?ref=Yandex&utm_source=yandex&utm_medium={source_type}&utm_campaign={campaign_id}.
Делим его на две части: адрес и параметры без меток (ref=Yandex) идут в ссылку объявления,
utm-метки и параметры с подстановками {…} — в TrackingParams кампании: Директ сам добавит их
ко всем ссылкам, а правка меток не отправляет объявления на модерацию.
Проверка строгая: домен, ref и каждая метка; путь страницы (/tarify) может отличаться.
Метки групп (TrackingParams группы) пока не проверяем.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote, urlsplit

from app.db import pool
from app.journal import journal_quiet, log_action
from app.yandex import YandexError

log = logging.getLogger(__name__)

MAX_LEN = 1024
# Объявления с адресом, которые правим через update_text_ad.
FIXABLE_TYPES = ("TEXT_AD", "RESPONSIVE_AD")


# --- Разбор ------------------------------------------------------------------------------------


def _pairs(query: str) -> list[tuple[str, str]]:
    """Параметры как есть, без перекодирования: {keyword} должен остаться {keyword}."""
    out = []
    for part in (query or "").lstrip("?").split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        if key:
            out.append((key, value))
    return out


def _join(pairs: list[tuple[str, str]]) -> str:
    return "&".join(f"{k}={v}" if v != "" else k for k, v in pairs)


def _same(a: str, b: str) -> bool:
    return unquote(a) == unquote(b)


def _host(value: str) -> str:
    host = (value or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _is_tracking(key: str, value: str) -> bool:
    return key.lower().startswith("utm_") or "{" in value


def parse_template(raw: str | None) -> dict | None:
    """Шаблон → {scheme, host, path, ad_params, tracking}; пустая строка — None."""
    text = (raw or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    if len(text) > MAX_LEN:
        raise YandexError("url-template", "Шаблон ссылки длиннее 1024 символов")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or "." not in parts.hostname:
        raise YandexError("url-template", "Шаблон ссылки — адрес сайта вида https://site.ru/?ref=Yandex&utm_source=yandex")
    ad_params, tracking = [], []
    for key, value in _pairs(parts.query):
        (tracking if _is_tracking(key, value) else ad_params).append((key, value))
    keys = [k for k, _ in ad_params + tracking]
    dup = next((k for k in keys if keys.count(k) > 1), None)
    if dup:
        raise YandexError("url-template", f"Параметр {dup} в шаблоне повторяется")
    host = parts.netloc.split("@")[-1]
    return {
        "raw": text,
        "scheme": parts.scheme,
        "host": host,
        "path": parts.path or "/",
        "ad_params": ad_params,
        "tracking": tracking,
    }


def clean_template(raw: str | None) -> str:
    """Для сохранения в настройках: проверенный шаблон или пустая строка."""
    t = parse_template(raw)
    return t["raw"] if t else ""


def template_tracking(t: dict) -> str:
    return _join(t["tracking"])


def template_href(t: dict, path: str | None = None) -> str:
    query = _join(t["ad_params"])
    return f"{t['scheme']}://{t['host']}{path or t['path']}" + (f"?{query}" if query else "")


def merge_tracking(existing: str | None, t: dict) -> str:
    """Метки шаблона, затем прочие параметры кампании, которых в шаблоне нет."""
    own = {k for k, _ in t["tracking"]}
    ad_keys = {k for k, _ in t["ad_params"]}
    rest = [(k, v) for k, v in _pairs(existing or "") if k not in own and k not in ad_keys and not k.lower().startswith("utm_")]
    return _join(t["tracking"] + rest)[:MAX_LEN]


def normalize_href(href: str | None, t: dict) -> str:
    """Ссылка объявления по шаблону: тот же путь, параметры шаблона, без utm (они в кампании)."""
    href = (href or "").strip()
    if not href:
        return template_href(t)
    if "://" not in href:
        href = "https://" + href
    parts = urlsplit(href)
    if _host(parts.hostname or "") != _host(t["host"]):
        return template_href(t)
    drop = {k for k, _ in t["ad_params"]} | {k for k, _ in t["tracking"]}
    rest = [(k, v) for k, v in _pairs(parts.query) if k not in drop and not k.lower().startswith("utm_")]
    query = _join(t["ad_params"] + rest)
    out = f"{t['scheme']}://{t['host']}{parts.path or '/'}" + (f"?{query}" if query else "")
    if parts.fragment:
        out += f"#{parts.fragment}"
    return out[:MAX_LEN]


# --- Проверка ----------------------------------------------------------------------------------


def _param_problems(pairs: list[tuple[str, str]], wanted: list[tuple[str, str]]) -> list[str]:
    out = []
    for key, value in wanted:
        found = [v for k, v in pairs if k == key]
        if not found:
            out.append(f"нет {key}={value}")
        elif len(found) > 1:
            out.append(f"{key} указан {len(found)} раза")
        elif not _same(found[0], value):
            out.append(f"{key}={found[0] or '(пусто)'} вместо {value}")
    return out


def href_problems(href: str | None, t: dict) -> list[str]:
    href = (href or "").strip()
    if not href:
        return []
    if "://" not in href:
        href = "https://" + href
    parts = urlsplit(href)
    if _host(parts.hostname or "") != _host(t["host"]):
        return [f"домен {parts.hostname or '?'} вместо {t['host']}"]
    pairs = _pairs(parts.query)
    out = _param_problems(pairs, t["ad_params"])
    utm = list(dict.fromkeys(k for k, v in pairs if _is_tracking(k, v)))
    if utm:
        out.append(f"метки в ссылке объявления ({', '.join(utm)}) — они должны быть в параметрах кампании")
    return out


def tracking_problems(tracking: str | None, t: dict) -> list[str]:
    pairs = _pairs(tracking or "")
    out = _param_problems(pairs, t["tracking"])
    ad_keys = {k for k, _ in t["ad_params"]}
    dup = [k for k, _ in pairs if k in ad_keys]
    if dup:
        out.append(f"{', '.join(dict.fromkeys(dup))} и в параметрах кампании, и в ссылке")
    return out


def check_campaign(t: dict, tracking: str | None, ads: list[dict]) -> dict:
    """ads — [{id, ad_id, type, state, href, title}]. tracking None — метки ещё не загружены."""
    campaign = tracking_problems(tracking, t) if tracking is not None else []
    bad_ads = []
    for ad in ads:
        if (ad.get("state") or "") == "ARCHIVED":
            continue
        problems = href_problems(ad.get("href"), t)
        if problems:
            bad_ads.append(
                {
                    "ad_row_id": ad["id"],
                    "ad_id": ad.get("ad_id"),
                    "title": ad.get("title") or "",
                    "href": ad.get("href") or "",
                    "fixed_href": normalize_href(ad.get("href"), t),
                    "problems": problems,
                    "fixable": (ad.get("type") or "") in FIXABLE_TYPES,
                }
            )
    return {
        "tracking_unknown": tracking is None,
        "tracking": tracking or "",
        "fixed_tracking": merge_tracking(tracking, t),
        "campaign_problems": campaign,
        "ads": bad_ads,
        "ok": not campaign and not bad_ads,
    }


def _ws_template(user_id: int, workspace_id: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT url_template FROM workspaces WHERE id = %s AND user_id = %s", (workspace_id, user_id)
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    try:
        return parse_template(row["url_template"])
    except YandexError:
        return None


def campaign_template(user_id: int, campaign_row_id: int) -> dict | None:
    """Шаблон сайта, к которому привязана кампания; None — шаблона нет."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT w.url_template FROM workspace_campaigns wc
            JOIN workspaces w ON w.id = wc.workspace_id
            WHERE wc.campaign_row_id = %s AND wc.user_id = %s
            """,
            (campaign_row_id, user_id),
        ).fetchone()
    if row is None:
        return None
    try:
        return parse_template(row["url_template"])
    except YandexError:
        return None


def adgroup_template(user_id: int, adgroup_row_id: int) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT campaign_row_id FROM direct_adgroups WHERE id = %s AND user_id = %s", (adgroup_row_id, user_id)
        ).fetchone()
    return campaign_template(user_id, int(row["campaign_row_id"])) if row else None


def _campaign_rows(user_id: int, workspace_id: int, campaign_row_ids: list[int] | None) -> list[dict]:
    clauses = ["wc.workspace_id = %s", "wc.user_id = %s", "COALESCE(c.state, '') <> 'ARCHIVED'"]
    params: list = [workspace_id, user_id]
    if campaign_row_ids:
        clauses.append("c.id = ANY(%s)")
        params.append([int(i) for i in campaign_row_ids])
    with pool.connection() as conn:
        return conn.execute(
            f"""
            SELECT c.id, c.campaign_id, c.name, c.state, c.status, c.tracking_params
            FROM workspace_campaigns wc
            JOIN direct_campaigns c ON c.id = wc.campaign_row_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.name, c.id
            """,
            params,
        ).fetchall()


def _campaign_ads(user_id: int, campaign_row_ids: list[int]) -> dict[int, list[dict]]:
    if not campaign_row_ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT ad.id, ad.ad_id, ad.type, ad.state, ad.href, ad.title, g.campaign_row_id
            FROM direct_ads ad
            JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
            WHERE ad.user_id = %s AND g.campaign_row_id = ANY(%s)
            ORDER BY ad.id
            """,
            (user_id, campaign_row_ids),
        ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(int(r["campaign_row_id"]), []).append(r)
    return out


def workspace_link_issues(user_id: int, workspace_id: int, campaign_row_ids: list[int] | None = None) -> dict:
    """Кампании сайта, у которых ссылки не по шаблону. Считается по данным последней синхронизации."""
    t = _ws_template(user_id, workspace_id)
    if t is None:
        return {"configured": False, "template": "", "campaigns": [], "bad": 0, "unknown": 0}
    campaigns = _campaign_rows(user_id, workspace_id, campaign_row_ids)
    ads = _campaign_ads(user_id, [int(c["id"]) for c in campaigns])
    items, unknown = [], 0
    for c in campaigns:
        res = check_campaign(t, c.get("tracking_params"), ads.get(int(c["id"]), []))
        unknown += int(res["tracking_unknown"])
        if res["ok"]:
            continue
        items.append(
            {
                "campaign_row_id": c["id"],
                "campaign_id": c["campaign_id"],
                "name": c["name"],
                "state": c.get("state") or "",
                "status": c.get("status") or "",
                **res,
                "ads_total": len(res["ads"]),
                "ads": res["ads"][:30],
            }
        )
    return {
        "configured": True,
        "template": t["raw"],
        "ad_href": template_href(t),
        "tracking": template_tracking(t),
        "campaigns": items,
        "bad": len(items),
        "unknown": unknown,
    }


# --- Исправление -------------------------------------------------------------------------------


def fix_campaign_links(user_id: int, campaign_row_id: int, *, journal: bool = True) -> dict:
    """
    Метки кампании и адреса объявлений — по шаблону сайта. Метки правятся без модерации,
    исправленное объявление Директ отправит на модерацию заново. Откат — прежние метки и адреса.
    """
    from app.actions import get_campaign_rows, get_campaign_settings, update_campaign_settings
    from app.ads import update_text_ad

    t = campaign_template(user_id, campaign_row_id)
    if t is None:
        raise YandexError("url-template", "У сайта не задан шаблон ссылки — укажите его в настройках сайта")
    rows = get_campaign_rows(user_id, [campaign_row_id])
    if not rows:
        raise YandexError("not-found")
    name = rows[0]["name"]
    out: dict = {"campaign_row_id": campaign_row_id, "name": name, "tracking_fixed": False, "ads_fixed": 0, "errors": []}
    undo_steps: list[dict] = []

    current = get_campaign_settings(user_id, campaign_row_id).get("tracking_params") or ""
    with pool.connection() as conn:
        conn.execute("UPDATE direct_campaigns SET tracking_params = %s WHERE id = %s", (current, campaign_row_id))
    ads = _campaign_ads(user_id, [campaign_row_id]).get(campaign_row_id, [])
    res = check_campaign(t, current, ads)

    with journal_quiet():
        if res["campaign_problems"] and res["fixed_tracking"] != current:
            try:
                update_campaign_settings(user_id, campaign_row_id, {"tracking_params": res["fixed_tracking"]})
                out["tracking_fixed"] = True
                undo_steps.append(
                    {"op": "campaign_settings", "args": {"campaign_row_id": campaign_row_id, "changes": {"tracking_params": current}}}
                )
            except YandexError as exc:
                out["errors"].append(f"Метки кампании: {exc.message or exc.code}")
        for ad in res["ads"]:
            if not ad["fixable"]:
                out["errors"].append(f"Объявление {ad['ad_id']}: такой тип правьте в кабинете Директа")
                continue
            if ad["fixed_href"] == ad["href"]:
                continue
            try:
                update_text_ad(user_id, ad["ad_row_id"], href=ad["fixed_href"])
                out["ads_fixed"] += 1
                undo_steps.append({"op": "ad_update", "args": {"ad_row_id": ad["ad_row_id"], "href": ad["href"]}})
            except YandexError as exc:
                out["errors"].append(f"Объявление {ad['ad_id']}: {exc.message or exc.code}")

    out["status"] = "ok" if not out["errors"] else ("partial" if undo_steps else "error")
    if journal and undo_steps:
        log_action(
            user_id,
            "links",
            {"campaign_row_id": campaign_row_id, "tracking": res["fixed_tracking"], "ads": len(res["ads"])},
            out,
            "ok" if not out["errors"] else "partial",
            title=f"Ссылки «{name}» по шаблону сайта",
            undo={"label": "Вернуть прежние ссылки", "steps": undo_steps},
        )
    out["undo_steps"] = undo_steps
    return out


def fix_links(user_id: int, campaign_row_ids: list[int], progress=None) -> dict:
    items = []
    for n, rid in enumerate(campaign_row_ids):
        if progress:
            progress(n, len(campaign_row_ids), "Исправляю ссылки")
        try:
            items.append(fix_campaign_links(user_id, int(rid)))
        except YandexError as exc:
            items.append({"campaign_row_id": rid, "status": "error", "errors": [exc.message or exc.code]})
    for item in items:
        item.pop("undo_steps", None)
    return {
        "items": items,
        "tracking_fixed": sum(1 for i in items if i.get("tracking_fixed")),
        "ads_fixed": sum(int(i.get("ads_fixed") or 0) for i in items),
        "note": "Исправленные объявления Директ заново отправит на модерацию; метки кампании модерацию не требуют.",
    }
