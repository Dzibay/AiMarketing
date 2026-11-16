"""Наглядные блоки в ответе ИИ: график, сравнение периодов, карточки кампаний, таблица.

ИИ присылает только описание — вид блока, метрику, период, разрез. Числа считает этот модуль тем же расчётом,
что и экран «Отчёты» (analytics.workspace_analytics / portfolio_analytics), поэтому нарисовать выдуманные цифры
модель не может, а в блоке и в отчёте за тот же период они совпадают.

Деньги в блоке — в копейках, как в отчётах: форматирует фронтенд теми же функциями.
"""

from __future__ import annotations

from datetime import date

from app.analytics import _metrics, parse_range, portfolio_analytics, workspace_analytics
from app.yandex import YandexError

BLOCK_TYPES = ("chart", "compare", "campaigns", "table")
MAX_BLOCKS_PER_MESSAGE = 4
MAX_TABLE_ROWS = 200
MAX_CARDS = 12
TITLE_LEN = 80

# metric → (поле в отчёте, подпись, единица, хорошо ли рост: up / down / '' — нейтрально)
METRICS: dict[str, tuple[str, str, str, str]] = {
    "cost": ("cost_kopecks", "Расход", "kop", ""),
    "impressions": ("impressions", "Показы", "int", "up"),
    "clicks": ("clicks", "Клики", "int", "up"),
    "ctr": ("ctr", "CTR", "pct", "up"),
    "cpc": ("cpc_kopecks", "Цена клика", "kop", "down"),
    "conversions": ("conversions", "Заявки", "int", "up"),
    "cr": ("cr", "Конверсия в заявку", "pct", "up"),
    "cpa": ("cpa_kopecks", "Цена заявки", "kop", "down"),
    "revenue": ("revenue_kopecks", "Доход", "kop", "up"),
    "drr": ("drr", "ДРР", "pct", "down"),
    "visits": ("visits", "Визиты", "int", "up"),
}
# Метрики, у которых рост к прошлому периоду считается долей (как compare в отчётах).
DEFAULT_COMPARE = ["cost", "clicks", "conversions", "cpa"]
DEFAULT_CARDS = ["cost", "conversions", "cpa"]
DEFAULT_TABLE = ["cost", "clicks", "conversions", "cpa"]
TABLE_BY = ("campaigns", "days", "projects")


def _delta(cur, prev) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / prev * 100, 1)


def _metric(name) -> str:
    key = str(name or "").strip().lower()
    if key not in METRICS:
        raise YandexError("metric", f"Неизвестная метрика {name!r}; можно: {', '.join(METRICS)}")
    return key


def _metric_list(raw, default: list[str], limit: int) -> list[str]:
    if raw in (None, "", []):
        return list(default)
    items = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for item in items:
        key = _metric(item)
        if key not in out:
            out.append(key)
    if not out:
        return list(default)
    return out[:limit]


def _ids(raw) -> list[int]:
    if raw in (None, "", []):
        return []
    items = raw if isinstance(raw, list) else [raw]
    try:
        return [int(i) for i in items]
    except (TypeError, ValueError) as exc:
        raise YandexError("ids", "campaign_row_ids — список чисел") from exc


def _meta(metric: str) -> dict:
    field, label, unit, good = METRICS[metric]
    return {"metric": metric, "label": label, "unit": unit, "good": good}


def _value(row: dict, metric: str):
    """Значение метрики из строки отчёта; ряд по дням хранит не все поля — производные считаем из сумм."""
    field = METRICS[metric][0]
    if field in row:
        return row[field]
    return _metrics(row, use_goal=False)[field]


def _report(user_id: int, workspace_id: int | None, args: dict, *, campaign_row_id: int | None = None) -> dict:
    date_from, date_to = parse_range(args.get("date_from") or "", args.get("date_to") or "")
    if workspace_id is None:
        if campaign_row_id:
            raise YandexError("scope", "Для одной кампании укажи project_id")
        return portfolio_analytics(user_id, date_from=date_from, date_to=date_to)
    return workspace_analytics(
        user_id, workspace_id, date_from=date_from, date_to=date_to, with_tops=False, campaign_row_id=campaign_row_id
    )


def _period(data: dict) -> dict:
    return {
        "date_from": data["date_from"],
        "date_to": data["date_to"],
        "prev_from": data["prev_from"],
        "prev_to": data["prev_to"],
    }


def _campaign_rows(data: dict) -> list[dict]:
    return data.get("by_campaign") if "by_campaign" in data else data.get("campaigns") or []


def _campaign_name(data: dict, campaign_row_id: int | None) -> str:
    if not campaign_row_id:
        return ""
    row = next((c for c in _campaign_rows(data) if int(c["campaign_row_id"]) == int(campaign_row_id)), None)
    return row["name"] if row else ""


def _title(args: dict, fallback: str) -> str:
    text = " ".join(str(args.get("title") or "").split())
    return (text or fallback)[:TITLE_LEN]


def _chart(user_id: int, workspace_id: int | None, args: dict) -> dict:
    metric = _metric(args.get("metric") or "cost")
    campaign = int(args["campaign_row_id"]) if args.get("campaign_row_id") not in (None, "") else None
    data = _report(user_id, workspace_id, args, campaign_row_id=campaign)
    with_prev = args.get("compare_previous") not in (False, "false", 0)
    points = [{"day": d["day"], "value": _value(d, metric)} for d in data["by_day"]]
    prev = [{"day": d["day"], "value": _value(d, metric)} for d in data["prev_by_day"]] if with_prev else []
    total = _value(data["totals"], metric)
    prev_total = _value(data["previous"], metric)
    name = _campaign_name(data, campaign)
    return {
        "type": "chart",
        "title": _title(args, f"{METRICS[metric][1]} по дням" + (f" · {name}" if name else "")),
        **_meta(metric),
        **_period(data),
        "campaign_row_id": campaign,
        "points": points,
        "prev_points": prev,
        "total": total,
        "prev_total": prev_total,
        "delta": _delta(total, prev_total),
    }


def _compare(user_id: int, workspace_id: int | None, args: dict) -> dict:
    metrics = _metric_list(args.get("metrics"), DEFAULT_COMPARE, 8)
    campaign = int(args["campaign_row_id"]) if args.get("campaign_row_id") not in (None, "") else None
    data = _report(user_id, workspace_id, args, campaign_row_id=campaign)
    rows = []
    for m in metrics:
        cur, prev = _value(data["totals"], m), _value(data["previous"], m)
        rows.append({**_meta(m), "cur": cur, "prev": prev, "delta": _delta(cur, prev)})
    name = _campaign_name(data, campaign)
    return {
        "type": "compare",
        "title": _title(args, "Сравнение с прошлым периодом" + (f" · {name}" if name else "")),
        **_period(data),
        "campaign_row_id": campaign,
        "rows": rows,
    }


def _pick_campaigns(data: dict, ids: list[int]) -> list[dict]:
    rows = _campaign_rows(data)
    if not ids:
        return rows
    by_id = {int(c["campaign_row_id"]): c for c in rows}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise YandexError("scope", f"Кампаний {', '.join(map(str, missing))} нет в этом проекте")
    return [by_id[i] for i in ids]


def _campaign_ref(c: dict, workspace_id: int | None) -> dict:
    ws = workspace_id if workspace_id is not None else (c.get("project") or {}).get("id")
    return {
        "campaign_row_id": c["campaign_row_id"],
        "workspace_id": ws,
        "name": c["name"],
        "state": c.get("state") or "",
        "status": c.get("status") or "",
        "type": c.get("type") or "",
        "project": (c.get("project") or {}).get("name") or "",
    }


def _cards(user_id: int, workspace_id: int | None, args: dict) -> dict:
    metrics = _metric_list(args.get("metrics"), DEFAULT_CARDS, 3)
    data = _report(user_id, workspace_id, args)
    rows = _pick_campaigns(data, _ids(args.get("campaign_row_ids")))
    items = [
        {**_campaign_ref(c, workspace_id), "values": [{**_meta(m), "value": _value(c, m)} for m in metrics]}
        for c in rows[:MAX_CARDS]
    ]
    return {
        "type": "campaigns",
        "title": _title(args, "Кампании"),
        **_period(data),
        "items": items,
        "more": max(len(rows) - MAX_CARDS, 0),
    }


def _table(user_id: int, workspace_id: int | None, args: dict) -> dict:
    by = str(args.get("by") or "campaigns").strip().lower()
    if by not in TABLE_BY:
        raise YandexError("by", f"Разрез таблицы — {', '.join(TABLE_BY)}")
    if by == "projects" and workspace_id is not None:
        raise YandexError("by", "Разрез по проектам — только в общем чате, без project_id")
    metrics = _metric_list(args.get("metrics"), DEFAULT_TABLE, 6)
    campaign = int(args["campaign_row_id"]) if by == "days" and args.get("campaign_row_id") not in (None, "") else None
    data = _report(user_id, workspace_id, args, campaign_row_id=campaign)
    if by == "days":
        source = data["by_day"]
        rows = [{"key": d["day"], "label": d["day"], "values": [_value(d, m) for m in metrics]} for d in source]
        first = "День"
    elif by == "projects":
        rows = [
            {"key": str(p["id"]), "label": p["name"], "workspace_id": p["id"], "values": [_value(p["totals"], m) for m in metrics]}
            for p in data["projects"]
        ]
        first = "Проект"
    else:
        picked = _pick_campaigns(data, _ids(args.get("campaign_row_ids")))
        rows = [
            {
                "key": str(c["campaign_row_id"]),
                "label": c["name"],
                **_campaign_ref(c, workspace_id),
                "values": [_value(c, m) for m in metrics],
            }
            for c in picked
        ]
        first = "Кампания"
    sort = args.get("sort")
    sort_metric = _metric(sort) if sort not in (None, "") else ""
    if sort_metric in metrics:
        i = metrics.index(sort_metric)
        rows.sort(key=lambda r: (r["values"][i] is None, -(r["values"][i] or 0)))
    return {
        "type": "table",
        "title": _title(args, {"days": "По дням", "projects": "По проектам"}.get(by, "По кампаниям")),
        **_period(data),
        "by": by,
        "first": first,
        "columns": [_meta(m) for m in metrics],
        "rows": rows[:MAX_TABLE_ROWS],
        "sort": sort_metric if sort_metric in metrics else "",
        "campaign_row_id": campaign,
    }


_BUILDERS = {"chart": _chart, "compare": _compare, "campaigns": _cards, "table": _table}


def build_block(user_id: int, workspace_id: int | None, args: dict) -> dict:
    """Блок по описанию ИИ; workspace_id None — все проекты пользователя (общий чат)."""
    kind = str(args.get("type") or "").strip().lower()
    if kind not in _BUILDERS:
        raise YandexError("type", f"Вид блока — {', '.join(BLOCK_TYPES)}")
    return _BUILDERS[kind](user_id, workspace_id, args)


def _fmt(value, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "kop":
        return f"{round(value / 100):,} ₽".replace(",", " ")
    if unit == "pct":
        return f"{value} %"
    return f"{value:,}".replace(",", " ")


def _days(d_from: str, d_to: str) -> str:
    return f"{date.fromisoformat(d_from):%d.%m}–{date.fromisoformat(d_to):%d.%m.%Y}"


def block_brief(block: dict) -> dict:
    """Ответ модели на show: те же числа коротко, чтобы текст ответа с блоком не расходился."""
    period = _days(block["date_from"], block["date_to"])
    out: dict = {"ok": True, "shown": block["type"], "title": block["title"], "period": period}
    t = block["type"]
    if t == "chart":
        out["total"] = _fmt(block["total"], block["unit"])
        out["previous_total"] = _fmt(block["prev_total"], block["unit"])
        out["delta_pct"] = block["delta"]
        out["days"] = len(block["points"])
    elif t == "compare":
        out["rows"] = [
            {"metric": r["label"], "now": _fmt(r["cur"], r["unit"]), "before": _fmt(r["prev"], r["unit"]), "delta_pct": r["delta"]}
            for r in block["rows"]
        ]
    elif t == "campaigns":
        out["campaigns"] = [
            {"name": i["name"], **{v["label"]: _fmt(v["value"], v["unit"]) for v in i["values"]}} for i in block["items"]
        ]
    else:
        out["rows"] = len(block["rows"])
        cols = block["columns"]
        out["first_rows"] = [
            {"name": r["label"], **{c["label"]: _fmt(v, c["unit"]) for c, v in zip(cols, r["values"])}}
            for r in block["rows"][:5]
        ]
    out["note"] = "Блок уже показан маркетологу. Не повторяй его цифры таблицей в тексте — объясни, что из них следует"
    return out
