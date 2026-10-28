"""Срезы статистики пространства и действия по ним.

Площадки РСЯ, устройства, пол и возраст, регионы — отчётом Директа (CUSTOM_REPORT) по выбранным
кампаниям; время суток — из Метрики (в Директе нет разбивки по часам): визиты и цели по часу визита
с переходом из этих кампаний.

По площадкам — подсказки, что запретить (игры, приложения, расход без конверсий, аномальный CTR),
и запрет пачкой (ExcludedSites кампаний). По остальным срезам — корректировки ставок: устройства,
пол-возраст и регионы — сервисом BidModifiers, часы — процентом ставки во временном таргетинге.
Каждое применение — одна запись в журнале с откатом.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import timedelta

from app import actions, ai, direct_api
from app.brief import brief_prompt
from app.clock import today_msk
from app.db import pool
from app.direct_reports import fetch_report
from app.journal import journal_quiet, log_action
from app.metrika_sync import _dim_campaign, _dim_value, _metric, _stat_rows
from app.stats import _count, _rub_to_kopecks, goal_columns
from app.workspaces import require_workspace
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.slices")

Progress = Callable[[int, int, str], None]

DIMENSIONS = ("placement", "device", "demography", "region", "hour")
ADJUSTABLE = ("device", "demography", "region", "hour")
MAX_DAYS = 90
CAMPAIGNS_MAX = 100
EXCLUDE_MAX_SITES = 1000
# Текущее расписание читается кампанией за кампанией — для больших выборок не показываем.
HOUR_CURRENT_MAX = 30
AI_BATCH = 60
AI_MAX = 300
# Регионы Директ отдаёт до сёл: показываем самые затратные.
REGIONS_MAX = 300

DEVICE_LABELS = {"DESKTOP": "Компьютеры", "MOBILE": "Смартфоны", "TABLET": "Планшеты", "SMART_TV": "Смарт-ТВ"}
GENDER_LABELS = {"GENDER_MALE": "Мужчины", "GENDER_FEMALE": "Женщины"}
AGE_LABELS = {
    "AGE_0_17": "до 18",
    "AGE_18_24": "18–24",
    "AGE_25_34": "25–34",
    "AGE_35_44": "35–44",
    "AGE_45_54": "45–54",
    "AGE_55": "55+",
}
_REPORT_FIELDS = {
    "placement": ["Placement"],
    "device": ["Device"],
    "demography": ["Gender", "Age"],
    "region": ["LocationOfPresenceId", "LocationOfPresenceName"],
}
_MOD_TYPES = {
    "device": ["MOBILE_ADJUSTMENT", "TABLET_ADJUSTMENT", "DESKTOP_ADJUSTMENT", "DESKTOP_ONLY_ADJUSTMENT", "SMART_TV_ADJUSTMENT"],
    "demography": ["DEMOGRAPHICS_ADJUSTMENT"],
    "region": ["REGIONAL_ADJUSTMENT"],
}
# DesktopAdjustment в API — компьютеры вместе с планшетами и пересекается с TabletAdjustment;
# компьютеры отдельно — DesktopOnlyAdjustment. Старую общую корректировку видим, но не трогаем.
_DEVICE_BLOCKS = {
    "MOBILE": "MobileAdjustment",
    "TABLET": "TabletAdjustment",
    "DESKTOP": "DesktopOnlyAdjustment",
    "SMART_TV": "SmartTvAdjustment",
}
_DESKTOP_AND_TABLET = "DESKTOP+TABLET"
# Пределы корректировок Директа, %: 100 — без изменений, 0 — не показывать.
_PCT_RANGE = {"device": (0, 1300), "demography": (0, 1300), "region": (10, 1300), "hour": (0, 200)}
# Подсказка не уходит дальше ±50 %: остальное — решение маркетолога.
SUGGEST_RANGE = (50, 150)
MIN_CLICKS = {"device": 30, "demography": 30, "region": 30, "hour": 0}
MIN_VISITS_HOUR = 30
# Сила «приора» для сглаживания CR: столько конверсий по среднему CR добавляем каждому срезу.
PRIOR_CONVERSIONS = 5

ACTION_TITLES = {"device": "Устройства", "demography": "Пол и возраст", "region": "Регионы", "hour": "Время суток"}


# --- Кампании и KPI --------------------------------------------------------------------------------


def _campaigns(user_id: int, workspace_id: int, campaign_ids: list[int] | None) -> list[dict]:
    ids = [int(i) for i in campaign_ids or []]
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.campaign_id, c.name, c.campaign_type, c.state, c.account_id,
                   a.connection_id, a.client_login, a.enabled
            FROM workspace_campaigns wc
            JOIN direct_campaigns c ON c.id = wc.campaign_row_id
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE wc.workspace_id = %s AND wc.user_id = %s
              AND (cardinality(%s::bigint[]) = 0 OR c.id = ANY(%s::bigint[]))
            ORDER BY c.id
            """,
            (workspace_id, user_id, ids, ids),
        ).fetchall()
    if ids and len(rows) != len(set(ids)):
        raise YandexError("not-found", "Часть кампаний не входит в пространство")
    rows = [r for r in rows if r["enabled"]]
    if not rows:
        raise YandexError("empty", "В пространстве нет кампаний из включённых кабинетов")
    if len(rows) > CAMPAIGNS_MAX:
        raise YandexError("limit", f"За раз — до {CAMPAIGNS_MAX} кампаний")
    return rows


def _target_cpa_kopecks(ws: dict) -> int | None:
    kpi_type = ws.get("kpi_type") or ws.get("client_kpi_type")
    target = ws.get("kpi_target") if ws.get("kpi_type") else ws.get("client_kpi_target")
    if kpi_type == "cpa" and target:
        return int(float(target) * 100)
    return None


def _period(days) -> tuple:
    days = max(1, min(int(days or 30), MAX_DAYS))
    date_to = today_msk() - timedelta(days=1)
    return date_to - timedelta(days=days - 1), date_to


# --- Метрики -------------------------------------------------------------------------------------


def _finish(row: dict) -> dict:
    clicks, cost, conv, imp = row["clicks"], row["cost_kopecks"], row["conversions"], row["impressions"]
    row["ctr"] = round(clicks / imp * 100, 2) if imp else None
    row["cpc_kopecks"] = round(cost / clicks) if clicks else None
    row["cr"] = round(conv / clicks * 100, 2) if clicks else None
    row["cpa_kopecks"] = round(cost / conv) if conv else None
    row["bounce_rate"] = round(row.pop("bounces", 0) / clicks * 100, 1) if clicks and row.get("bounces") is not None else None
    return row


def _blank(key: str, label: str) -> dict:
    return {
        "key": key,
        "label": label,
        "impressions": 0,
        "clicks": 0,
        "cost_kopecks": 0,
        "conversions": 0,
        "bounces": 0,
        "campaigns": {},
    }


def suggest_pct(conversions: float, base: float, avg_rate: float | None, *, min_base: float, step: int = 5) -> int | None:
    """Корректировка по конверсии среза против средней: CR сглажен к среднему, предел ±50 %.

    base — клики (или визиты для часов); мало данных или нет конверсий вовсе — подсказки нет.
    """
    if not avg_rate or avg_rate <= 0 or base < max(min_base, 1):
        return None
    prior = PRIOR_CONVERSIONS / avg_rate
    rate = (conversions + PRIOR_CONVERSIONS) / (base + prior)
    pct = rate / avg_rate * 100
    lo, hi = SUGGEST_RANGE
    pct = max(lo, min(hi, round(pct / step) * step))
    return int(pct)


# --- Площадки: эвристики ------------------------------------------------------------------------

_APP_RE = re.compile(r"^(com|ru|org|net|io|me|app|air|tv|jp|kr|cn|de|br|uk|by|kz|ua|mobi|games?)\.[a-z0-9_]+(\.[a-z0-9_]+)+$")
_IOS_RE = re.compile(r"^(id)?\d{6,}$")
# Bundle id без доменного префикса («puzzle.snake.wiggle»): у сайта последняя часть — доменная зона.
_SITE_ZONES = {
    "ru", "com", "net", "org", "su", "info", "io", "me", "by", "kz", "ua", "uz", "pro", "online", "site", "club",
    "biz", "tv", "xyz", "top", "shop", "store", "fm", "cc", "co", "de", "uk", "us", "eu", "ws", "life", "space",
    "media", "news", "blog", "tech", "app", "games", "fun", "website", "live", "world", "today", "moscow", "spb",
}
_GAME_WORDS = (
    "game", "igra", "igry", "igri", "puzzle", "solitaire", "sudoku", "mahjong", "casino", "bingo", "poker",
    "klondike", "chess", "tetris", "match3", "crossword", "scanword", "skanvord", "krossvord", "candy",
    "bubble", "jewel", "zombie", "slots", "durak", "shashki", "nonogram", "jigsaw", "tycoon", "merge",
    "idle", "arcade", "racing", "shooter", "runner", "words", "quiz", "trivia", "blockpuzzle", "farm",
)
_SHORT_GAME_WORDS = {"idle", "farm", "quiz", "words", "merge", "chess", "slots", "durak", "candy"}


def placement_kind(placement: str) -> str:
    """yandex — площадка Яндекса; game — похоже на игру; app — мобильное приложение; '' — сайт."""
    p = (placement or "").strip().lower()
    if not p:
        return ""
    if p in ("yandex.ru", "ya.ru", "yandex.com", "yandex.by", "yandex.kz") or p.endswith((".yandex.ru", ".yandex.com")):
        return "yandex"
    tokens = [t for t in re.split(r"[^a-z0-9]+", p) if t]
    for token in tokens:
        for word in _GAME_WORDS:
            if (token == word) if word in _SHORT_GAME_WORDS else (word in token):
                return "game"
    if _APP_RE.match(p) or _IOS_RE.match(p):
        return "app"
    parts = p.split(".")
    if len(parts) >= 3 and parts[-1] not in _SITE_ZONES and "xn--" not in parts[-1] and not p.endswith(".рф"):
        return "app"
    return ""


def excludable(placement: str) -> bool:
    """Числовые id App Store Директ в ExcludedSites не принимает (и отклоняет весь список кампании)."""
    p = (placement or "").strip().lower()
    return bool(p) and not _IOS_RE.match(p) and placement_kind(p) != "yandex"


def placement_flags(row: dict, *, ref_cpa: int | None, avg_ctr: float | None, waste_kopecks: int) -> list[str]:
    """Причины запретить площадку; пусто — запрещать не за что."""
    kind = row.get("kind")
    if kind == "yandex":
        return []
    conv, clicks, cost = row["conversions"], row["clicks"], row["cost_kopecks"]
    reasons: list[str] = []
    if not conv:
        if kind == "game" and cost:
            reasons.append("игра без конверсий")
        elif kind == "app" and cost:
            reasons.append("приложение без конверсий")
        if cost >= waste_kopecks:
            reasons.append(f"расход {cost / 100:,.0f} ₽ без конверсий".replace(",", " "))
        ctr = row.get("ctr")
        if clicks >= 20 and ctr and avg_ctr and ctr >= max(3 * avg_ctr, 3.0):
            reasons.append(f"CTR {ctr:g} % при среднем {avg_ctr:g} % — похоже на случайные клики")
        bounce = row.get("bounce_rate")
        if clicks >= 20 and bounce is not None and bounce >= 70:
            reasons.append(f"отказы {bounce:g} %")
    elif ref_cpa and row.get("cpa_kopecks") and row["cpa_kopecks"] >= 3 * ref_cpa and cost >= 2 * ref_cpa:
        reasons.append(f"CPA {row['cpa_kopecks'] / 100:,.0f} ₽ — втрое выше цели".replace(",", " "))
    return reasons


# --- Отчёт Директа ------------------------------------------------------------------------------


def _row_key(dimension: str, item: dict) -> tuple[str, str] | None:
    if dimension == "placement":
        p = str(item.get("Placement") or "").strip()
        return (p.lower(), p) if p and p != "--" else None
    if dimension == "device":
        d = str(item.get("Device") or "")
        return (d, DEVICE_LABELS.get(d, d or "Не определено")) if d else None
    if dimension == "demography":
        g, a = str(item.get("Gender") or ""), str(item.get("Age") or "")
        label = f"{GENDER_LABELS.get(g, 'Пол не определён')}, {AGE_LABELS.get(a, 'возраст не определён')}"
        return f"{g}|{a}", label
    if dimension == "region":
        rid = str(item.get("LocationOfPresenceId") or "").strip()
        if not rid or rid == "--":
            return None
        return rid, str(item.get("LocationOfPresenceName") or rid)
    return None


def adjustable_key(dimension: str, key: str) -> bool:
    if dimension == "device":
        return key in _DEVICE_BLOCKS
    if dimension == "demography":
        g, _, a = key.partition("|")
        return g in GENDER_LABELS and a in AGE_LABELS
    if dimension == "region":
        return key.isdigit() and int(key) > 0
    if dimension == "hour":
        return key.isdigit() and 0 <= int(key) < 24
    return False


def _direct_slice(ws: dict, campaigns: list[dict], dimension: str, date_from, date_to, progress: Progress, user_id: int):
    by_account: dict[int, list[dict]] = defaultdict(list)
    for c in campaigns:
        by_account[c["account_id"]].append(c)
    goal = int(ws["goal_id"]) if ws.get("goal_id") else None
    rows: dict[str, dict] = {}
    errors: list[dict] = []
    for n, (_account_id, camps) in enumerate(by_account.items()):
        login = camps[0]["client_login"]
        progress(n, len(by_account), f"Отчёт Директа: {login}")
        by_cid = {int(c["campaign_id"]): c for c in camps}
        filters = [{"Field": "CampaignId", "Operator": "IN", "Values": [str(i) for i in by_cid]}]
        if dimension == "placement":
            filters.append({"Field": "AdNetworkType", "Operator": "EQUALS", "Values": ["AD_NETWORK"]})
        try:
            token = get_connection_access_token(camps[0]["connection_id"], user_id)
            raw = fetch_report(
                token,
                login,
                report_type="CUSTOM_REPORT",
                field_names=["CampaignId", *_REPORT_FIELDS[dimension], "Impressions", "Clicks", "Cost", "Conversions", "Bounces"],
                date_from=date_from,
                date_to=date_to,
                filters=filters,
                goals=[goal] if goal else None,
            )
        except YandexError as exc:
            if exc.code in ("token-expired", "unauthorized") or len(by_account) == 1:
                raise
            errors.append({"client_login": login, "error": exc.code, "message": exc.message or ""})
            continue
        conv_col = (goal_columns(raw).get(goal) or {}).get("Conversions") if goal else "Conversions"
        for item in raw:
            found = _row_key(dimension, item)
            camp = by_cid.get(_count(item.get("CampaignId")))
            if found is None or camp is None:
                continue
            key, label = found
            row = rows.setdefault(key, _blank(key, label))
            clicks, cost = _count(item.get("Clicks")), _rub_to_kopecks(item.get("Cost"))
            conv = _count(item.get(conv_col)) if conv_col else 0
            row["impressions"] += _count(item.get("Impressions"))
            row["clicks"] += clicks
            row["cost_kopecks"] += cost
            row["conversions"] += conv
            row["bounces"] += _count(item.get("Bounces"))
            per = row["campaigns"].setdefault(camp["id"], {"clicks": 0, "cost_kopecks": 0, "conversions": 0})
            per["clicks"] += clicks
            per["cost_kopecks"] += cost
            per["conversions"] += conv
    return rows, errors


# --- Время суток: Метрика ------------------------------------------------------------------------


def _hour_slice(user_id: int, ws: dict, campaigns: list[dict], date_from, date_to) -> dict[str, dict]:
    if not ws.get("counter_row_id"):
        raise YandexError("metrika", "У пространства нет счётчика Метрики — время суток считается по нему")
    with pool.connection() as conn:
        counter = conn.execute(
            "SELECT connection_id, counter_id FROM metrika_counters WHERE id = %s AND user_id = %s",
            (ws["counter_row_id"], user_id),
        ).fetchone()
    if counter is None:
        raise YandexError("metrika", "Счётчик пространства не найден")
    token = get_connection_access_token(counter["connection_id"], user_id)
    goal = int(ws["goal_id"]) if ws.get("goal_id") else None
    goal_metric = f"ym:s:goal{goal}reaches" if goal else "ym:s:anyGoalReaches"
    data = _stat_rows(
        token,
        int(counter["counter_id"]),
        metrics=f"ym:s:visits,ym:s:bounceRate,{goal_metric}",
        dimensions="ym:s:lastSignDirectClickOrder,ym:s:hour",
        date1=date_from,
        date2=date_to,
    )
    by_cid = {int(c["campaign_id"]): c for c in campaigns}
    rows = {str(h): {**_blank(str(h), f"{h:02d}:00–{(h + 1) % 24:02d}:00"), "visits": 0, "bounce_sum": 0.0} for h in range(24)}
    for item in data:
        dims = item.get("dimensions") or []
        if len(dims) < 2:
            continue
        camp = by_cid.get(_dim_campaign(dims[0]) or 0)
        match = re.match(r"\s*(\d{1,2})", str(_dim_value(dims[1]) or ""))
        if camp is None or not match or int(match.group(1)) > 23:
            continue
        metrics = item.get("metrics") or []
        visits = int(_metric(metrics, 0))
        reaches = int(_metric(metrics, 2))
        row = rows[str(int(match.group(1)))]
        row["visits"] += visits
        row["bounce_sum"] += float(_metric(metrics, 1)) * visits
        row["conversions"] += reaches
        per = row["campaigns"].setdefault(camp["id"], {"clicks": 0, "cost_kopecks": 0, "conversions": 0, "visits": 0})
        per["visits"] += visits
        per["conversions"] += reaches
    for row in rows.values():
        v = row["visits"]
        row["bounce_rate"] = round(row.pop("bounce_sum") / v, 1) if v else None
        row["cr"] = round(row["conversions"] / v * 100, 2) if v else None
        row.pop("bounces", None)
    return rows


# --- Корректировки: чтение и запись ---------------------------------------------------------------


def _pct_of(adj: dict) -> int:
    value = adj.get("BidModifier")
    return 100 if value is None else int(value)


def _parse_modifier(item: dict) -> dict | None:
    """BidModifier из get → {id, campaign_id, key, pct, enabled}; по ОС — ключ с суффиксом (не трогаем)."""
    base = {"id": int(item["Id"]), "campaign_id": int(item.get("CampaignId") or 0)}
    for key, block in _DEVICE_BLOCKS.items():
        adj = item.get(block)
        if adj:
            os_type = adj.get("OperatingSystemType")
            return {**base, "key": f"{key}:{os_type}" if os_type else key, "pct": _pct_of(adj), "enabled": True}
    if item.get("DesktopAdjustment"):
        return {**base, "key": _DESKTOP_AND_TABLET, "pct": _pct_of(item["DesktopAdjustment"]), "enabled": True}
    adj = item.get("DemographicsAdjustment")
    if adj:
        key = f"{adj.get('Gender') or ''}|{adj.get('Age') or ''}"
        return {**base, "key": key, "pct": _pct_of(adj), "enabled": adj.get("Enabled", "YES") == "YES"}
    adj = item.get("RegionalAdjustment")
    if adj:
        return {
            **base,
            "key": str(adj.get("RegionId")),
            "pct": _pct_of(adj),
            "enabled": adj.get("Enabled", "YES") == "YES",
        }
    return None


def get_modifiers(token, login: str, campaign_ids: list[int], dimension: str) -> list[dict]:
    out: list[dict] = []
    for chunk in direct_api.chunks([int(i) for i in campaign_ids], 10):
        params = {
            "SelectionCriteria": {"CampaignIds": chunk, "Levels": ["CAMPAIGN"], "Types": _MOD_TYPES[dimension]},
            "FieldNames": ["Id", "CampaignId", "Type"],
        }
        if dimension == "device":
            params["MobileAdjustmentFieldNames"] = ["BidModifier", "OperatingSystemType"]
            params["TabletAdjustmentFieldNames"] = ["BidModifier", "OperatingSystemType"]
            params["DesktopAdjustmentFieldNames"] = ["BidModifier"]
            params["DesktopOnlyAdjustmentFieldNames"] = ["BidModifier"]
            params["SmartTvAdjustmentFieldNames"] = ["BidModifier"]
        elif dimension == "demography":
            params["DemographicsAdjustmentFieldNames"] = ["Gender", "Age", "BidModifier", "Enabled"]
        else:
            params["RegionalAdjustmentFieldNames"] = ["RegionId", "BidModifier", "Enabled"]
        for item in direct_api.get_all(token, login, "bidmodifiers", params, "BidModifiers"):
            parsed = _parse_modifier(item)
            if parsed:
                out.append(parsed)
    return out


def _clean_pct(dimension: str, value) -> int:
    lo, hi = _PCT_RANGE[dimension]
    try:
        pct = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise YandexError("adjust", "Корректировка — число процентов") from exc
    if dimension == "hour":
        pct = 0 if pct <= 0 else max(10, round(pct / 10) * 10)
    if not lo <= pct <= hi:
        raise YandexError("adjust", f"Корректировка — от {lo} до {hi} % ставки")
    return pct


def _errors_text(result: dict, key: str) -> list[str]:
    return [msg for _i, msg in direct_api.item_errors(result, key)]


def set_bid_modifiers(user_id: int, campaign_row_id: int, dimension: str, values: dict) -> dict:
    """Корректировки одной кампании: {ключ: % ставки}; 100 — снять. → {"previous", "errors"}."""
    from app.adgroups import _campaign_row

    camp = _campaign_row(user_id, campaign_row_id)
    token = get_connection_access_token(camp["connection_id"], user_id)
    login, cid = camp["client_login"], int(camp["campaign_id"])
    existing = {m["key"]: m for m in get_modifiers(token, login, [cid], dimension)}
    deletes: list[int] = []
    sets: list[dict] = []
    add_device: dict[str, dict] = {}
    add_list: list[dict] = []
    previous: dict[str, int] = {}
    for key, value in values.items():
        if not adjustable_key(dimension, key):
            continue
        pct = _clean_pct(dimension, value)
        cur = existing.get(key)
        old = cur["pct"] if cur and cur["enabled"] else 100
        if old == pct and (cur is None or cur["enabled"]):
            continue
        previous[key] = old
        if cur and not cur["enabled"]:
            deletes.append(cur["id"])
            cur = None
        if pct == 100:
            if cur:
                deletes.append(cur["id"])
        elif cur:
            sets.append({"Id": cur["id"], "BidModifier": pct})
        elif dimension == "device":
            add_device[_DEVICE_BLOCKS[key]] = {"BidModifier": pct}
        elif dimension == "demography":
            g, _, a = key.partition("|")
            add_list.append({"Gender": g, "Age": a, "BidModifier": pct})
        else:
            add_list.append({"RegionId": int(key), "BidModifier": pct})
    errors: list[str] = []
    if deletes:
        result = direct_api.call(token, login, "bidmodifiers", "delete", {"SelectionCriteria": {"Ids": deletes}})
        errors += _errors_text(result, "DeleteResults")
    if sets:
        result = direct_api.call(token, login, "bidmodifiers", "set", {"BidModifiers": sets})
        errors += _errors_text(result, "SetResults")
    # Одно поле корректировки на элемент add.
    adds = [{"CampaignId": cid, block: value} for block, value in add_device.items()]
    if add_list:
        block = "DemographicsAdjustments" if dimension == "demography" else "RegionalAdjustments"
        adds.append({"CampaignId": cid, block: add_list})
    if adds:
        result = direct_api.call(token, login, "bidmodifiers", "add", {"BidModifiers": adds})
        errors += _errors_text(result, "AddResults")
    return {"previous": previous, "errors": errors}


def _current_by_campaign(user_id: int, campaigns: list[dict], dimension: str) -> dict[int, dict[str, int]]:
    """Текущие корректировки (или проценты расписания) кампаний: {id строки: {ключ: %}}."""
    out: dict[int, dict[str, int]] = {}
    if dimension == "hour":
        if len(campaigns) > HOUR_CURRENT_MAX:
            return out
        for c in campaigns:
            try:
                settings = actions.get_campaign_settings(user_id, c["id"])
            except YandexError:
                continue
            grid = settings.get("schedule")
            if not grid:
                out[c["id"]] = {}
                continue
            # Час «как правило» — самый частый процент среди дней, где он включён.
            hours: dict[str, int] = {}
            for h in range(24):
                values = [row[h] for row in grid if row[h]]
                if values:
                    hours[str(h)] = max(set(values), key=values.count)
                else:
                    hours[str(h)] = 0
            out[c["id"]] = hours
        return out
    by_account: dict[int, list[dict]] = defaultdict(list)
    for c in campaigns:
        by_account[c["account_id"]].append(c)
    for camps in by_account.values():
        try:
            token = get_connection_access_token(camps[0]["connection_id"], user_id)
            mods = get_modifiers(token, camps[0]["client_login"], [int(c["campaign_id"]) for c in camps], dimension)
        except YandexError as exc:
            log.warning("bidmodifiers get %s: %s %s", camps[0]["client_login"], exc.code, exc.message)
            continue
        row_of = {int(c["campaign_id"]): c["id"] for c in camps}
        for c in camps:
            out[c["id"]] = {}
        for m in mods:
            if m["enabled"] and m["campaign_id"] in row_of:
                out[row_of[m["campaign_id"]]][m["key"]] = m["pct"]
    return out


# --- Отчёт целиком ------------------------------------------------------------------------------


def slice_report(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    progress = progress or (lambda *_a: None)
    workspace_id = int(payload["workspace_id"])
    dimension = str(payload.get("dimension") or "")
    if dimension not in DIMENSIONS:
        raise YandexError("dimension", "Неизвестный срез")
    ws = require_workspace(user_id, workspace_id)
    campaigns = _campaigns(user_id, workspace_id, payload.get("campaign_ids"))
    date_from, date_to = _period(payload.get("days"))
    errors: list[dict] = []
    if dimension == "hour":
        progress(0, 2, "Отчёт Метрики по часам")
        rows = _hour_slice(user_id, ws, campaigns, date_from, date_to)
    else:
        rows, errors = _direct_slice(ws, campaigns, dimension, date_from, date_to, progress, user_id)
    current: dict[int, dict[str, int]] = {}
    if dimension in ADJUSTABLE:
        progress(1, 2, "Текущие корректировки")
        current = _current_by_campaign(user_id, campaigns, dimension)

    items = list(rows.values())
    if dimension != "hour":
        items = [_finish(r) for r in items]
    totals = {k: sum(r.get(k) or 0 for r in items) for k in ("impressions", "clicks", "cost_kopecks", "conversions", "visits")}
    base_key = "visits" if dimension == "hour" else "clicks"
    avg_rate = totals["conversions"] / totals[base_key] if totals[base_key] else None
    avg_ctr = round(totals["clicks"] / totals["impressions"] * 100, 2) if totals["impressions"] else None
    ref_cpa = _target_cpa_kopecks(ws)
    avg_cpa = round(totals["cost_kopecks"] / totals["conversions"]) if totals["conversions"] else None
    avg_cpc = totals["cost_kopecks"] / totals["clicks"] if totals["clicks"] else 0
    waste = ref_cpa or avg_cpa or max(int(avg_cpc * 20), 50_000)

    names = {c["id"]: c["name"] for c in campaigns}
    for row in items:
        row["campaigns"] = [
            {"campaign_row_id": rid, "name": names.get(rid, ""), **m}
            for rid, m in sorted(row["campaigns"].items(), key=lambda kv: -(kv[1].get("cost_kopecks") or kv[1].get("visits") or 0))
        ]
        if dimension == "placement":
            row["kind"] = placement_kind(row["label"])
            row["excludable"] = excludable(row["label"])
            row["reasons"] = placement_flags(row, ref_cpa=ref_cpa, avg_ctr=avg_ctr, waste_kopecks=waste) if row["excludable"] else []
        elif dimension in ADJUSTABLE:
            row["adjustable"] = adjustable_key(dimension, row["key"])
            values = {current[c].get(row["key"], 100) for c in current}
            row["current_pct"] = next(iter(values)) if len(values) == 1 else None
            row["current_mixed"] = len(values) > 1
            base = row.get(base_key) or 0
            min_base = MIN_VISITS_HOUR if dimension == "hour" else MIN_CLICKS[dimension]
            row["suggested_pct"] = (
                suggest_pct(row["conversions"], base, avg_rate, min_base=min_base, step=10 if dimension == "hour" else 5)
                if row["adjustable"]
                else None
            )
    sort_key = "visits" if dimension == "hour" else "cost_kopecks"
    if dimension == "hour":
        items.sort(key=lambda r: int(r["key"]))
    else:
        items.sort(key=lambda r: (-(r.get(sort_key) or 0), -(r.get("clicks") or 0), -(r.get("impressions") or 0)))
    hidden = 0
    if dimension == "region" and len(items) > REGIONS_MAX:
        hidden = len(items) - REGIONS_MAX
        items = items[:REGIONS_MAX]
    progress(2, 2, "Готово")
    return {
        "dimension": dimension,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "goal_name": ws.get("goal_name") if ws.get("goal_id") else None,
        "target_cpa_kopecks": ref_cpa,
        "waste_kopecks": waste if dimension == "placement" else None,
        "totals": {
            **totals,
            "ctr": avg_ctr,
            "cr": round(avg_rate * 100, 2) if avg_rate else None,
            "cpa_kopecks": avg_cpa,
        },
        "campaigns": [{"campaign_row_id": c["id"], "name": c["name"], "type": c["campaign_type"]} for c in campaigns],
        "current_known": bool(current),
        "items": items,
        "hidden": hidden,
        "errors": errors,
    }


# --- Запрет площадок ----------------------------------------------------------------------------


def exclude_placements(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """items: [{placement, campaign_ids}] → площадки в ExcludedSites кампаний; одна запись журнала."""
    progress = progress or (lambda *_a: None)
    workspace_id = int(payload["workspace_id"])
    require_workspace(user_id, workspace_id)
    per_campaign: dict[int, list[str]] = defaultdict(list)
    for item in payload.get("items") or []:
        site = actions._sites([str(item.get("placement") or "")])
        if not site or not excludable(site[0]):
            continue
        for rid in item.get("campaign_ids") or []:
            if site[0] not in per_campaign[int(rid)]:
                per_campaign[int(rid)].append(site[0])
    if not per_campaign:
        raise YandexError("empty", "Не выбрано ни одной площадки")
    allowed = {c["id"]: c for c in _campaigns(user_id, workspace_id, list(per_campaign))}

    applied: list[dict] = []
    errors: list[dict] = []
    undo_steps: list[dict] = []
    all_sites: list[str] = []
    for n, (rid, sites) in enumerate(per_campaign.items()):
        name = allowed.get(rid, {}).get("name") or ""
        progress(n, len(per_campaign), name)
        try:
            settings = actions.get_campaign_settings(user_id, rid)
            current = list(settings.get("excluded_sites") or [])
            new = [s for s in sites if s not in current]
            if not new:
                applied.append({"campaign_row_id": rid, "name": name, "added": 0})
                continue
            if len(current) + len(new) > EXCLUDE_MAX_SITES:
                raise YandexError(
                    "limit",
                    f"в кампании уже {len(current)} запрещённых площадок — с новыми будет больше {EXCLUDE_MAX_SITES}",
                )
            with journal_quiet():
                actions.update_campaign_settings(user_id, rid, {"excluded_sites": current + new})
            undo_steps.append({"op": "campaign_settings", "args": {"campaign_row_id": rid, "changes": {"excluded_sites": current}}})
            applied.append({"campaign_row_id": rid, "name": name, "added": len(new)})
            all_sites += [s for s in new if s not in all_sites]
        except YandexError as exc:
            errors.append({"campaign_row_id": rid, "name": name, "error": exc.code, "message": exc.message or ""})
        except Exception:
            log.exception("exclude placements %s", rid)
            errors.append({"campaign_row_id": rid, "name": name, "error": "direct", "message": ""})

    changed = [a for a in applied if a["added"]]
    status = "ok" if not errors else ("partial" if changed else "error")
    result = {"applied": len(changed), "sites": len(all_sites), "items": applied, "errors": errors, "status": status}
    if changed or errors:
        shown = ", ".join(all_sites[:3])
        more = len(all_sites) - 3
        log_action(
            user_id,
            "exclude_sites",
            {"items": payload.get("items") or []},
            result,
            status,
            workspace_id=workspace_id,
            title=f"Запрещены площадки: {shown or '—'}" + (f" и ещё {more}" if more > 0 else "") + f" ({len(changed)} кампаний)",
            undo={"label": "Вернуть площадки", "steps": undo_steps} if undo_steps else None,
        )
    if status == "error":
        first = errors[0]
        raise YandexError(first["error"], f"«{first['name']}»: {first['message']}" if first["message"] else None)
    return result


# --- Корректировки ставок -----------------------------------------------------------------------


def _pct_text(pct: int) -> str:
    if pct == 0:
        return "не показывать"
    diff = pct - 100
    return "без корректировки" if diff == 0 else f"{diff:+d} %".replace("-", "−")


def _set_hours(user_id: int, campaign_row_id: int, values: dict) -> dict:
    settings = actions.get_campaign_settings(user_id, campaign_row_id)
    old = settings.get("schedule")
    grid = [list(r) for r in (old or [[100] * 24 for _ in range(7)])]
    for key, value in values.items():
        if not adjustable_key("hour", key):
            continue
        pct = _clean_pct("hour", value)
        h = int(key)
        for day in grid:
            # Выключенные часы расписания не включаем: корректировка меняет ставку, а не время показа.
            if day[h]:
                day[h] = pct if pct else 0
    if grid == (old or [[100] * 24 for _ in range(7)]):
        return {"previous": None}
    weekends = settings.get("consider_working_weekends", True)
    with journal_quiet():
        actions.update_campaign_settings(
            user_id, campaign_row_id, {"schedule": grid, "consider_working_weekends": weekends}
        )
    return {"previous": {"schedule": old, "consider_working_weekends": weekends}}


def apply_adjustments(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """items: [{key, pct}] на кампании campaign_ids (все выбранные — одно и то же)."""
    progress = progress or (lambda *_a: None)
    workspace_id = int(payload["workspace_id"])
    dimension = str(payload.get("dimension") or "")
    if dimension not in ADJUSTABLE:
        raise YandexError("dimension", "По этому срезу корректировок нет")
    require_workspace(user_id, workspace_id)
    values: dict[str, int] = {}
    labels: dict[str, str] = {}
    for item in payload.get("items") or []:
        key = str(item.get("key") or "")
        if adjustable_key(dimension, key):
            values[key] = _clean_pct(dimension, item.get("pct"))
            labels[key] = str(item.get("label") or key)
    if not values:
        raise YandexError("empty", "Не выбрано ни одной корректировки")
    if dimension == "device" and all(values.get(k) == 0 for k in ("MOBILE", "TABLET", "DESKTOP")):
        raise YandexError("adjust", "Нельзя отключить показы на всех устройствах сразу")
    campaigns = _campaigns(user_id, workspace_id, payload.get("campaign_ids"))

    applied: list[dict] = []
    errors: list[dict] = []
    undo_steps: list[dict] = []
    for n, camp in enumerate(campaigns):
        progress(n, len(campaigns), camp["name"])
        try:
            if dimension == "hour":
                res = _set_hours(user_id, camp["id"], values)
                if res["previous"] is not None:
                    undo_steps.append(
                        {"op": "campaign_settings", "args": {"campaign_row_id": camp["id"], "changes": res["previous"]}}
                    )
                applied.append({"campaign_row_id": camp["id"], "name": camp["name"], "changed": res["previous"] is not None})
                continue
            res = set_bid_modifiers(user_id, camp["id"], dimension, values)
            if res["previous"]:
                undo_steps.append(
                    {
                        "op": "bid_modifiers",
                        "args": {"campaign_row_id": camp["id"], "dimension": dimension, "values": res["previous"]},
                    }
                )
            if res["errors"]:
                errors.append({"campaign_row_id": camp["id"], "name": camp["name"], "error": "direct", "message": "; ".join(res["errors"])[:500]})
            applied.append({"campaign_row_id": camp["id"], "name": camp["name"], "changed": bool(res["previous"])})
        except YandexError as exc:
            errors.append({"campaign_row_id": camp["id"], "name": camp["name"], "error": exc.code, "message": exc.message or ""})
        except Exception:
            log.exception("adjust %s %s", dimension, camp["id"])
            errors.append({"campaign_row_id": camp["id"], "name": camp["name"], "error": "direct", "message": ""})

    changed = sum(1 for a in applied if a["changed"])
    status = "ok" if not errors else ("partial" if changed else "error")
    result = {"applied": changed, "items": applied, "errors": errors, "status": status}
    if changed or errors:
        parts = [f"{labels[k]} {_pct_text(v)}" for k, v in list(values.items())[:4]]
        more = len(values) - len(parts)
        log_action(
            user_id,
            "bid_adjust",
            {"dimension": dimension, "values": values, "campaign_ids": [c["id"] for c in campaigns]},
            result,
            status,
            workspace_id=workspace_id,
            title=f"Корректировки · {ACTION_TITLES[dimension]}: {', '.join(parts)}"
            + (f" и ещё {more}" if more > 0 else "")
            + f" ({changed} кампаний)",
            undo={"label": "Вернуть корректировки", "steps": undo_steps} if undo_steps else None,
        )
    if status == "error":
        first = errors[0]
        raise YandexError(first["error"], f"«{first['name']}»: {first['message']}" if first["message"] else None)
    return result


# --- ИИ: разбор площадок ------------------------------------------------------------------------

_AI_SYSTEM = """Ты — специалист по Яндекс Директу. Разбираешь площадки РСЯ, где показывались объявления клиента.

{brief}
{kpi}

Для каждой площадки реши:
- exclude — запретить: игры и игровые приложения, мобильные приложения со случайными кликами, сайты,
  чья аудитория явно не купит (детские, развлекательные, не по теме), площадки с заметным расходом
  без конверсий, с аномально высоким CTR или с отказами выше 70 %;
- watch — пока мало данных или неоднозначно;
- keep — оставить: площадки с конверсиями по приемлемой цене и тематические сайты.
Площадки Яндекса (yandex.ru, ya.ru) не запрещай. Если по названию не понять, что за площадка, решай по метрикам.

Верни только JSON: {{"items": [{{"p": "площадка как в списке", "v": "exclude|watch|keep", "r": "причина, до 80 символов"}}]}}"""


def _ai_lines(items: list[dict]) -> str:
    lines = ["площадка | показы | клики | расход ₽ | конверсии | CTR % | отказы %"]
    for i in items:
        lines.append(
            " | ".join(
                str(v)
                for v in (
                    str(i.get("label") or i.get("key") or "")[:120],
                    int(i.get("impressions") or 0),
                    int(i.get("clicks") or 0),
                    round((i.get("cost_kopecks") or 0) / 100),
                    int(i.get("conversions") or 0),
                    i.get("ctr") if i.get("ctr") is not None else "—",
                    i.get("bounce_rate") if i.get("bounce_rate") is not None else "—",
                )
            )
        )
    return "\n".join(lines)


def ai_placements(user_id: int, payload: dict, progress: Progress | None = None) -> dict:
    """Подсказки ИИ по площадкам: {"verdicts": {площадка: {verdict, reason}}, "usage"}. Ничего не меняет."""
    progress = progress or (lambda *_a: None)
    workspace_id = int(payload["workspace_id"])
    ws = require_workspace(user_id, workspace_id)
    items = [i for i in payload.get("items") or [] if isinstance(i, dict) and (i.get("label") or i.get("key"))][:AI_MAX]
    if not items:
        raise YandexError("empty", "Нет площадок для разбора")
    cfg = ai.load_config(user_id, "placements", workspace_id)
    target = _target_cpa_kopecks(ws)
    kpi = f"Целевая цена конверсии: {target / 100:.0f} ₽." if target else "Целевая цена конверсии не задана."
    system = _AI_SYSTEM.format(brief=brief_prompt(ws), kpi=kpi)
    verdicts: dict[str, dict] = {}
    error = None
    with ai.meter() as spent:
        for start in range(0, len(items), AI_BATCH):
            batch = items[start : start + AI_BATCH]
            progress(start, len(items), f"ИИ разбирает площадки: {start} из {len(items)}")
            try:
                data = ai.complete_json(
                    {**cfg, "units": len(batch)},
                    [{"role": "system", "content": system}, {"role": "user", "content": _ai_lines(batch)}],
                )
            except YandexError as exc:
                if not verdicts:
                    raise
                error = exc.message or exc.code
                break
            known = {str(i.get("label") or i.get("key")).lower(): str(i.get("key") or i.get("label")) for i in batch}
            for got in data.get("items") or []:
                if not isinstance(got, dict):
                    continue
                key = known.get(str(got.get("p") or "").strip().lower())
                verdict = str(got.get("v") or "").strip().lower()
                if key and verdict in ("exclude", "watch", "keep"):
                    verdicts[key] = {"verdict": verdict, "reason": str(got.get("r") or "").strip()[:120]}
    progress(len(items), len(items), "Готово")
    usage = None
    if spent["calls"]:
        usage = {
            "calls": spent["calls"],
            "tokens_in": spent["tokens_in"],
            "tokens_out": spent["tokens_out"],
            "cost_rub": round(spent["cost_rub"], 4) if spent["priced"] else None,
            "estimated": spent["estimated"],
        }
    return {"verdicts": verdicts, "usage": usage, "error": error}
