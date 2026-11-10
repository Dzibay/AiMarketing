"""Единицы: лимит пользователя на период, проверка перед платным вызовом и журнал списаний.

Платное — вызовы модели (по токенам и цене класса модели), запросы Вордстата и картинки. Каждое списание —
строка ai_usage: вид (model | wordstat | image), класс модели, единицы (charged) и себестоимость в рублях.

Пользователь не видит ни денег, ни токенов, ни самих единиц — только долю месячного лимита в процентах.
До тарифов (фаза 2) период — календарный месяц по Москве, лимит — users.units_limit или общий
service_ai.default_units_limit; пусто — без лимита.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.clock import MSK, today_msk
from app.db import pool
from app.yandex import YandexError

log = logging.getLogger(__name__)

KINDS = {"model": "ИИ-ассистент", "wordstat": "Вордстат", "image": "Картинки"}
EXHAUSTED = "Лимит ИИ на этот месяц исчерпан — ИИ, Вордстат и картинки снова заработают с 1-го числа"
NOT_ENOUGH = "Остатка месячного лимита ИИ не хватит на эту операцию"


def month_start(year: int | None = None, month: int | None = None) -> datetime:
    today = today_msk()
    return datetime(year or today.year, month or today.month, 1, tzinfo=MSK)


def period(user_id: int) -> tuple[datetime, datetime]:
    """Текущий расчётный период пользователя: [начало, конец)."""
    start = month_start()
    end = month_start(start.year + start.month // 12, start.month % 12 + 1)
    return start, end


def _num(value) -> float | None:
    return None if value is None else float(value)


def default_limit() -> float | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT default_units_limit FROM service_ai WHERE id = 1").fetchone()
    return _num(row["default_units_limit"]) if row else None


def units_limit(user_id: int) -> float | None:
    """Лимит единиц на период; None — без лимита."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(u.units_limit, s.default_units_limit) AS lim
            FROM users u LEFT JOIN service_ai s ON s.id = 1
            WHERE u.id = %s
            """,
            (user_id,),
        ).fetchone()
    return _num(row["lim"]) if row else None


def units_spent(user_id: int) -> float:
    start, _ = period(user_id)
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(charged), 0) AS s FROM ai_usage WHERE user_id = %s AND created_at >= %s",
            (user_id, start),
        ).fetchone()
    return float(row["s"])


def units_left(user_id: int) -> float | None:
    """Остаток единиц в периоде; None — без лимита."""
    limit = units_limit(user_id)
    return None if limit is None else limit - units_spent(user_id)


def check(user_id: int, need: float = 0.0) -> None:
    """Единственная проверка перед платным вызовом: лимит исчерпан или не хватит на need — ошибка."""
    if not user_id:
        return
    left = units_left(user_id)
    if left is None:
        return
    if left <= 0:
        raise YandexError("units-exhausted", EXHAUSTED)
    if need and need > left:
        raise YandexError("units-exhausted", NOT_ENOUGH)


def share(user_id: int, units: float) -> float | None:
    """Сколько процентов месячного лимита составляют units; None — лимита нет."""
    limit = units_limit(user_id)
    if not limit:
        return None
    return round(units * 100 / limit, 2)


def record(
    user_id: int,
    *,
    kind: str,
    task: str,
    workspace_id: int | None = None,
    model: str = "",
    model_class: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    chars_in: int = 0,
    chars_out: int = 0,
    units: int = 0,
    estimated: bool = False,
    charged: float = 0.0,
    cost_rub: float | None = None,
) -> None:
    """Строка списания. Ошибка учёта не роняет операцию: результат уже получен и оплачен сервисом."""
    if not user_id:
        return
    try:
        with pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_usage (user_id, workspace_id, kind, task, model, model_class, tokens_in, tokens_out,
                                      chars_in, chars_out, units, estimated, charged, cost_rub)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    workspace_id,
                    kind,
                    task,
                    model,
                    model_class,
                    int(tokens_in),
                    int(tokens_out),
                    int(chars_in),
                    int(chars_out),
                    int(units),
                    bool(estimated),
                    round(float(charged or 0), 4),
                    cost_rub,
                ),
            )
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать списание (%s, %s)", kind, task)


# --- Что видит пользователь ---------------------------------------------------------------------


def _pct(part: float, whole: float | None) -> float | None:
    if not whole:
        return None
    return round(part * 100 / whole, 1)


def user_summary(user_id: int) -> dict:
    """Шкала периода для пользователя: доля лимита всего и по видам / классам моделей / проектам.

    Без лимита — доли от всего расхода за период (сколько куда ушло), без процента лимита.
    """
    from app import ai

    start, end = period(user_id)
    limit = units_limit(user_id)
    classes = {c["id"]: c for c in ai.model_classes()}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.kind, u.model_class, u.workspace_id, w.name AS ws_name, SUM(u.charged) AS charged, COUNT(*) AS n
            FROM ai_usage u LEFT JOIN workspaces w ON w.id = u.workspace_id
            WHERE u.user_id = %s AND u.created_at >= %s AND u.created_at < %s
            GROUP BY u.kind, u.model_class, u.workspace_id, w.name
            """,
            (user_id, start, end),
        ).fetchall()
    spent = sum(float(r["charged"] or 0) for r in rows)
    base = limit if limit else spent

    def group(key) -> list[dict]:
        acc: dict = {}
        for r in rows:
            k, label = key(r)
            item = acc.setdefault(k, {"id": k, "label": label, "units": 0.0, "calls": 0})
            item["units"] += float(r["charged"] or 0)
            item["calls"] += int(r["n"])
        out = [
            {"id": i["id"], "label": i["label"], "percent": _pct(i["units"], base) or 0.0, "calls": i["calls"]}
            for i in acc.values()
        ]
        return sorted(out, key=lambda i: (-i["percent"], -i["calls"]))

    def kind_key(r):
        if r["kind"] == "model" and r["model_class"] in classes:
            return f"model:{r['model_class']}", classes[r["model_class"]]["name"]
        return r["kind"], KINDS.get(r["kind"], r["kind"])

    def ws_key(r):
        if r["workspace_id"] is None:
            return "none", "Без проекта"
        return str(r["workspace_id"]), r["ws_name"] or "Удалённый проект"

    # Лимит 0 — доступа к платным вызовам нет: шкала заполнена.
    used = 100.0 if limit is not None and limit <= 0 else _pct(spent, limit)
    return {
        "period_start": start.date().isoformat(),
        "period_end": end.date().isoformat(),
        "limited": limit is not None,
        "used_percent": None if used is None else min(used, 100.0),
        "left_percent": None if used is None else max(0.0, round(100 - used, 1)),
        "exhausted": limit is not None and spent >= limit,
        "by_kind": group(kind_key),
        "by_project": group(ws_key),
    }


# --- Админка ------------------------------------------------------------------------------------


def set_user_limit(user_id: int, limit) -> dict:
    """Свой лимит единиц пользователя; None / "" — общий лимит сервиса."""
    value = _limit_value(limit)
    with pool.connection() as conn:
        row = conn.execute("UPDATE users SET units_limit = %s WHERE id = %s RETURNING id", (value, user_id)).fetchone()
    if row is None:
        raise YandexError("not-found", "Пользователь не найден")
    return {"units_limit": value}


def _limit_value(limit) -> float | None:
    if limit is None or (isinstance(limit, str) and not limit.strip()):
        return None
    try:
        value = float(str(limit).replace(",", ".").replace(" ", ""))
    except ValueError as exc:
        raise YandexError("units-limit", "Лимит — число единиц") from exc
    if not 0 <= value <= 1_000_000_000:
        raise YandexError("units-limit", "Лимит — от 0 до миллиарда единиц")
    return round(value, 2)


def users_usage(user_ids: list[int]) -> dict[int, dict]:
    """Для таблицы пользователей: {user_id: {"spent", "limit", "own_limit", "cost_rub", "calls"}} за текущий месяц."""
    if not user_ids:
        return {}
    start = month_start()
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.units_limit, s.default_units_limit,
                   COALESCE(x.charged, 0) AS charged, COALESCE(x.cost, 0) AS cost, COALESCE(x.n, 0) AS n
            FROM users u
            LEFT JOIN service_ai s ON s.id = 1
            LEFT JOIN (
                SELECT user_id, SUM(charged) AS charged, SUM(cost_rub) AS cost, COUNT(*) AS n
                FROM ai_usage WHERE created_at >= %s AND user_id = ANY(%s) GROUP BY user_id
            ) x ON x.user_id = u.id
            WHERE u.id = ANY(%s)
            """,
            (start, user_ids, user_ids),
        ).fetchall()
    out = {}
    for r in rows:
        own = _num(r["units_limit"])
        out[int(r["id"])] = {
            "spent": round(float(r["charged"]), 2),
            "limit": own if own is not None else _num(r["default_units_limit"]),
            "own_limit": own,
            "cost_rub": round(float(r["cost"]), 2),
            "calls": int(r["n"]),
        }
    return out


def service_usage(month: str | None = None) -> dict:
    """Расход сервиса за месяц для админки: единицы и себестоимость по видам, классам моделей и пользователям."""
    from app import ai

    key, start, end = month_bounds(month)
    classes = {c["id"]: c["name"] for c in ai.model_classes()}
    with pool.connection() as conn:
        by_kind = conn.execute(
            """
            SELECT kind, model_class, COUNT(*) AS n, SUM(charged) AS charged, SUM(cost_rub) AS cost,
                   SUM(tokens_in) AS tin, SUM(tokens_out) AS tout
            FROM ai_usage WHERE created_at >= %s AND created_at < %s
            GROUP BY kind, model_class ORDER BY SUM(charged) DESC
            """,
            (start, end),
        ).fetchall()
        by_user = conn.execute(
            """
            SELECT u.user_id, us.email, us.name, COUNT(*) AS n, SUM(u.charged) AS charged, SUM(u.cost_rub) AS cost
            FROM ai_usage u JOIN users us ON us.id = u.user_id
            WHERE u.created_at >= %s AND u.created_at < %s
            GROUP BY u.user_id, us.email, us.name ORDER BY SUM(u.charged) DESC LIMIT 20
            """,
            (start, end),
        ).fetchall()
        months = conn.execute(
            """
            SELECT DISTINCT to_char(created_at AT TIME ZONE 'Europe/Moscow', 'YYYY-MM') AS m
            FROM ai_usage ORDER BY m DESC LIMIT 12
            """
        ).fetchall()

    def money(v) -> float:
        return round(float(v or 0), 2)

    items = []
    for r in by_kind:
        label = KINDS.get(r["kind"], r["kind"])
        if r["kind"] == "model":
            label = f"Модель: {classes.get(r['model_class']) or r['model_class'] or 'до перехода на единицы'}"
        items.append(
            {
                "kind": r["kind"],
                "model_class": r["model_class"],
                "label": label,
                "calls": int(r["n"]),
                "units": money(r["charged"]),
                "cost_rub": money(r["cost"]),
                "tokens_in": int(r["tin"] or 0),
                "tokens_out": int(r["tout"] or 0),
            }
        )
    this_month = month_bounds(None)[0]
    return {
        "month": key,
        "months": sorted({key, this_month, *(r["m"] for r in months)}, reverse=True),
        "total": {
            "calls": sum(i["calls"] for i in items),
            "units": round(sum(i["units"] for i in items), 2),
            "cost_rub": round(sum(i["cost_rub"] for i in items), 2),
        },
        "by_kind": items,
        "by_user": [
            {
                "user_id": int(r["user_id"]),
                "email": r["email"] or "",
                "name": r["name"] or "",
                "calls": int(r["n"]),
                "units": money(r["charged"]),
                "cost_rub": money(r["cost"]),
            }
            for r in by_user
        ],
    }


def month_bounds(month: str | None) -> tuple[str, datetime, datetime]:
    today = today_msk()
    year, mon = today.year, today.month
    if month:
        try:
            year, mon = (int(x) for x in month.split("-", 1))
            if not (2000 <= year <= 2100 and 1 <= mon <= 12):
                raise ValueError
        except ValueError as exc:
            raise YandexError("month", "Месяц в формате ГГГГ-ММ") from exc
    start = month_start(year, mon)
    end = month_start(year + mon // 12, mon % 12 + 1)
    return f"{year:04d}-{mon:02d}", start, end
