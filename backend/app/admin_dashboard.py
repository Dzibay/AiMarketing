"""Дашборд админки: цифры за период с изменением к прошлому отрезку, пять рядов для графиков, полоса состояния.

Период — скользящее окно, включая сегодня по Москве («Неделя» = последние 7 дней), или свой диапазон дат.
Прошлый период — тот же отрезок, сдвинутый на его длину в днях: «День» сравнивается со вчера до того же часа.
«Всё время» — без сравнения. Всё считается по исходным таблицам; ответ держится минуту в памяти (build_cached).

Доход — успешные нетестовые оплаты минус возвраты, по дате оплаты. Себестоимость ИИ здесь не показывается.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

from app import ai, ai_bridge, image_gen, mailer, payments, wordstat
from app.clock import MSK, now_msk
from app.db import pool
from app.yandex import yandex_configured

PRESETS = {"day": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}
STEPS = ("day", "week", "month")
ENDING_DAYS = 7
# Окно конверсии в оплату: длина пробного тарифа + столько дней.
CONVERSION_EXTRA_DAYS = 7
# Задача, ждущая очереди дольше этого, — жёлтая точка «Очередь задач».
QUEUE_LAG_MINUTES = 15
CACHE_SECONDS = 60

_TZ = "Europe/Moscow"
# Платёж считается, если прошёл, не тестовый и не возвращён целиком.
_PAID = "p.status = 'succeeded' AND NOT p.test AND p.refunded_rub < p.amount_rub"
_PAID_AT = "COALESCE(p.paid_at, p.created_at)"


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=MSK)


# --- Период ------------------------------------------------------------------------------------


def _first_day(today: date) -> date:
    with pool.connection() as conn:
        row = conn.execute(
            f"""
            SELECT LEAST(
                (SELECT MIN(created_at) FROM users),
                (SELECT MIN({_PAID_AT}) FROM payments p WHERE {_PAID})
            ) AS first
            """
        ).fetchone()
    first = row["first"] if row else None
    return min(first.astimezone(MSK).date(), today) if first else today


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def auto_step(days: int) -> str:
    """Шаг графика по длине периода: дни до квартала, недели до года, дальше месяцы."""
    if days <= 92:
        return "day"
    if days <= 400:
        return "week"
    return "month"


def period(p: str = "30d", date_from: str | None = None, date_to: str | None = None, step: str = "", now: datetime | None = None) -> dict:
    """Границы периода и прошлого отрезка: start включительно, end — не включая (для текущих — сейчас)."""
    now = now or now_msk()
    today = now.astimezone(MSK).date()
    d1, d2 = _parse_day(date_from), _parse_day(date_to)
    if d1 and d2:
        d1, d2 = sorted((d1, d2))
        d2 = min(d2, today)
        d1 = min(d1, d2)
        key = "custom"
    elif p == "all":
        d1, d2, key = _first_day(today), today, "all"
    else:
        key = p if p in PRESETS else "30d"
        d1, d2 = today - timedelta(days=PRESETS[key] - 1), today
    days = (d2 - d1).days + 1
    start = _day_start(d1)
    end = min(_day_start(d2 + timedelta(days=1)), now)
    out = {
        "preset": key,
        "from": d1.isoformat(),
        "to": d2.isoformat(),
        "days": days,
        "start": start,
        "end": end,
        "step": step if step in STEPS else auto_step(days),
        "prev": None,
    }
    if key != "all":
        shift = timedelta(days=days)
        out["prev"] = {"from": (d1 - shift).isoformat(), "to": (d2 - shift).isoformat(), "start": start - shift, "end": end - shift}
    return out


# --- Ряды для графиков ---------------------------------------------------------------------------


def _bucket(day: date, step: str) -> date:
    if step == "week":
        return day - timedelta(days=day.weekday())
    if step == "month":
        return day.replace(day=1)
    return day


def _buckets(d1: date, d2: date, step: str) -> list[date]:
    out, day = [], d1
    while day <= d2:
        b = _bucket(day, step)
        if not out or out[-1] != b:
            out.append(b)
        day += timedelta(days=1)
    return out


def _ts_bucket(column: str, step: str) -> str:
    return f"date_trunc('{step}', {column} AT TIME ZONE '{_TZ}')::date"


def _day_bucket(column: str, step: str) -> str:
    return f"date_trunc('{step}', {column}::timestamp)::date"


def _series_rows(conn, rng: dict, step: str) -> dict[date, dict]:
    """Все ряды дашборда за отрезок, по корзинам шага. Ключ — первый день корзины."""
    s, e = rng["start"], rng["end"]
    d1, d2 = date.fromisoformat(rng["from"]), date.fromisoformat(rng["to"])
    params = {"s": s, "e": e, "d1": d1, "d2": d2}
    rows: dict[date, dict] = {}

    def put(query: str, *fields: str) -> None:
        for r in conn.execute(query, params).fetchall():
            slot = rows.setdefault(r["b"], {})
            for f in fields:
                slot[f] = float(r[f] or 0)

    put(f"SELECT {_ts_bucket('created_at', step)} AS b, COUNT(*) AS users FROM users WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1", "users")
    put(
        f"""
        SELECT {_ts_bucket(_PAID_AT, step)} AS b, SUM(p.amount_rub - p.refunded_rub) AS revenue
        FROM payments p WHERE {_PAID} AND {_PAID_AT} >= %(s)s AND {_PAID_AT} < %(e)s GROUP BY 1
        """,
        "revenue",
    )
    put(
        f"""
        SELECT {_day_bucket('day', step)} AS b, COUNT(DISTINCT user_id) AS visited,
               COUNT(DISTINCT user_id) FILTER (WHERE actions > 0) AS acted
        FROM user_activity_days WHERE day BETWEEN %(d1)s AND %(d2)s GROUP BY 1
        """,
        "visited",
        "acted",
    )
    put(
        f"""
        SELECT {_ts_bucket('created_at', step)} AS b, COUNT(*) AS questions
        FROM user_events WHERE kind = 'ai_question' AND created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
        """,
        "questions",
    )
    put(
        f"""
        SELECT {_ts_bucket('started_at', step)} AS b, COUNT(*) AS visits, COUNT(DISTINCT visitor_id) AS visitors
        FROM site_visits WHERE started_at >= %(s)s AND started_at < %(e)s GROUP BY 1
        """,
        "visits",
        "visitors",
    )
    return rows


SERIES_KEYS = ("users", "revenue", "visited", "acted", "questions", "visits", "visitors")


def _series(conn, rng: dict, step: str) -> list[dict]:
    rows = _series_rows(conn, rng, step)
    d1, d2 = date.fromisoformat(rng["from"]), date.fromisoformat(rng["to"])
    return [{"day": b.isoformat(), **{k: rows.get(b, {}).get(k, 0.0) for k in SERIES_KEYS}} for b in _buckets(d1, d2, step)]


# --- Цифры за отрезок ----------------------------------------------------------------------------


def conversion_window_days() -> int:
    """W = длина действующего пробного тарифа + 7 дней (без пробного — 7)."""
    with pool.connection() as conn:
        row = conn.execute("SELECT days FROM plans WHERE is_trial AND active ORDER BY id LIMIT 1").fetchone()
    return int(row["days"] or 0) + CONVERSION_EXTRA_DAYS if row else CONVERSION_EXTRA_DAYS


def _numbers(conn, rng: dict, window: int, now: datetime) -> dict:
    """Цифры дашборда за отрезок [start, end)."""
    s, e = rng["start"], rng["end"]
    d1, d2 = date.fromisoformat(rng["from"]), date.fromisoformat(rng["to"])
    days = (d2 - d1).days + 1
    params = {"s": s, "e": e, "d1": d1, "d2": d2, "w": timedelta(days=window), "now": now, "today": now.astimezone(MSK).date()}

    one = lambda q: conn.execute(q, params).fetchone()  # noqa: E731

    pay = one(
        f"""
        SELECT COALESCE(SUM(p.amount_rub - p.refunded_rub), 0) AS revenue, COUNT(*) AS payments,
               COUNT(DISTINCT p.user_id) AS payers
        FROM payments p WHERE {_PAID} AND {_PAID_AT} >= %(s)s AND {_PAID_AT} < %(e)s
        """
    )
    new_users = one("SELECT COUNT(*) AS n FROM users WHERE created_at >= %(s)s AND created_at < %(e)s")["n"]
    conv = one(
        f"""
        SELECT COUNT(*) AS m,
               COUNT(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM payments p WHERE p.user_id = u.id AND {_PAID} AND {_PAID_AT} < u.created_at + %(w)s
               )) AS n,
               BOOL_OR(u.created_at + %(w)s > %(now)s) AS growing
        FROM users u WHERE u.created_at >= %(s)s AND u.created_at < %(e)s
        """
    )
    act = one(
        """
        SELECT COUNT(*) AS user_days, COUNT(DISTINCT user_id) AS visited
        FROM user_activity_days WHERE day BETWEEN %(d1)s AND %(d2)s
        """
    )
    questions = one(
        "SELECT COUNT(*) AS n FROM user_events WHERE kind = 'ai_question' AND created_at >= %(s)s AND created_at < %(e)s"
    )["n"]
    site = one(
        """
        SELECT COUNT(*) AS visits, COUNT(DISTINCT visitor_id) AS visitors
        FROM site_visits WHERE started_at >= %(s)s AND started_at < %(e)s
        """
    )
    # Удержание: у платежа последний оплаченный день попал в отрезок (и уже наступил) — это «окончание».
    # Следующая оплата того же человека раньше этого дня — досрочно, в этот день — в день окончания, позже — вернулись.
    ret = one(
        f"""
        WITH ends AS (
            SELECT p.last_day,
                   (SELECT MIN(({_PAID_AT.replace('p.', 'q.')} AT TIME ZONE '{_TZ}')::date)
                    FROM payments q
                    WHERE q.user_id = p.user_id AND q.id <> p.id
                      AND q.status = 'succeeded' AND NOT q.test AND q.refunded_rub < q.amount_rub
                      AND {_PAID_AT.replace('p.', 'q.')} > {_PAID_AT}) AS next_day
            FROM payments p
            WHERE {_PAID} AND p.user_id IS NOT NULL AND p.last_day BETWEEN %(d1)s AND LEAST(%(d2)s, %(today)s)
        )
        SELECT COUNT(*) AS endings,
               COUNT(*) FILTER (WHERE next_day < last_day) AS early,
               COUNT(*) FILTER (WHERE next_day = last_day) AS on_day,
               COUNT(*) FILTER (WHERE next_day > last_day) AS returned
        FROM ends
        """
    )
    revenue = float(pay["revenue"])
    payers = int(pay["payers"])
    visited = int(act["visited"])
    endings = int(ret["endings"])
    kept = int(ret["early"]) + int(ret["on_day"])
    visitors = int(site["visitors"])
    return {
        "new_users": int(new_users),
        "revenue": round(revenue, 2),
        "payments": int(pay["payments"]),
        "avg_check": round(revenue / int(pay["payments"]), 2) if pay["payments"] else None,
        "payers": payers,
        "per_payer": round(revenue / payers, 2) if payers else None,
        "conversion": {
            "paid": int(conv["n"]),
            "registered": int(conv["m"]),
            "rate": round(int(conv["n"]) / int(conv["m"]) * 100, 1) if conv["m"] else None,
            "growing": bool(conv["growing"]),
        },
        "active_avg": round(int(act["user_days"]) / days, 1) if days else 0.0,
        "active_users": visited,
        "questions": int(questions),
        "questions_per_active": round(int(questions) / visited, 1) if visited else None,
        "visits": int(site["visits"]),
        "visitors": visitors,
        "site_conversion": round(int(new_users) / visitors * 100, 1) if visitors else None,
        "retention": {
            "endings": endings,
            "early": int(ret["early"]),
            "on_day": int(ret["on_day"]),
            "returned": int(ret["returned"]),
            "rate": round(kept / endings * 100, 1) if endings else None,
            "return_rate": round((kept + int(ret["returned"])) / endings * 100, 1) if endings else None,
        },
    }


def _change(cur, prev) -> float | None:
    """Изменение в процентах; None — не с чем сравнивать (прошлое пусто или его нет)."""
    if cur is None or prev is None or not prev:
        return None
    return round((cur - prev) / abs(prev) * 100, 1)


# Цифры, у которых показывается изменение: ключ → как достать из _numbers.
_COMPARED = {
    "new_users": lambda n: n["new_users"],
    "revenue": lambda n: n["revenue"],
    "payments": lambda n: n["payments"],
    "avg_check": lambda n: n["avg_check"],
    "payers": lambda n: n["payers"],
    "per_payer": lambda n: n["per_payer"],
    "conversion": lambda n: n["conversion"]["rate"],
    "active_avg": lambda n: n["active_avg"],
    "questions": lambda n: n["questions"],
    "questions_per_active": lambda n: n["questions_per_active"],
    "visits": lambda n: n["visits"],
    "visitors": lambda n: n["visitors"],
    "site_conversion": lambda n: n["site_conversion"],
    "retention": lambda n: n["retention"]["rate"],
    "return_rate": lambda n: n["retention"]["return_rate"],
}


# --- Срез на сейчас ------------------------------------------------------------------------------


def _snapshot(conn, now: datetime, window: int) -> dict:
    today = now.astimezone(MSK).date()
    params = {"now": now, "ending": now + timedelta(days=ENDING_DAYS), "today": today, "w": timedelta(days=window)}
    one = lambda q: conn.execute(q, params).fetchone()  # noqa: E731

    total = int(one("SELECT COUNT(*) AS n FROM users")["n"])
    plans_rows = conn.execute(
        """
        SELECT p.id, p.name, p.is_trial, COUNT(*) AS n
        FROM subscriptions s JOIN plans p ON p.id = s.plan_id
        WHERE s.ends_at IS NULL OR s.ends_at > %(now)s
        GROUP BY p.id, p.name, p.is_trial, p.position
        ORDER BY p.is_trial, p.position, p.id
        """,
        params,
    ).fetchall()
    by_plan = [{"plan_id": r["id"], "name": r["name"], "trial": bool(r["is_trial"]), "users": int(r["n"])} for r in plans_rows]
    paying = sum(p["users"] for p in by_plan if not p["trial"])
    trial = sum(p["users"] for p in by_plan if p["trial"])
    ending = one(
        """
        SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE NOT p.is_trial) AS paid
        FROM subscriptions s JOIN plans p ON p.id = s.plan_id
        WHERE s.ends_at > %(now)s AND s.ends_at <= %(ending)s
        """
    )
    all_pay = one(
        f"""
        SELECT COALESCE(SUM(p.amount_rub - p.refunded_rub), 0) AS revenue, COUNT(*) AS payments,
               COUNT(DISTINCT p.user_id) AS payers
        FROM payments p WHERE {_PAID}
        """
    )
    all_conv = one(
        f"""
        SELECT COUNT(*) AS m,
               COUNT(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM payments p WHERE p.user_id = u.id AND {_PAID} AND {_PAID_AT} < u.created_at + %(w)s
               )) AS n
        FROM users u
        """
    )
    act = one(
        """
        SELECT COUNT(DISTINCT user_id) FILTER (WHERE day = %(today)s) AS today_visited,
               COUNT(DISTINCT user_id) FILTER (WHERE day = %(today)s AND actions > 0) AS today_acted,
               COUNT(DISTINCT user_id) FILTER (WHERE day > %(today)s - 7) AS week,
               COUNT(DISTINCT user_id) AS month
        FROM user_activity_days WHERE day > %(today)s - 30
        """
    )
    payments_n, payers_n = int(all_pay["payments"]), int(all_pay["payers"])
    return {
        "users_total": total,
        "subscriptions": {
            "paying": paying,
            "paying_pct": round(paying / total * 100, 1) if total else None,
            "trial": trial,
            "none": total - paying - trial,
            "by_plan": [p for p in by_plan if not p["trial"]],
        },
        "ending": {"total": int(ending["n"]), "paid": int(ending["paid"])},
        "all_time": {
            "revenue": round(float(all_pay["revenue"]), 2),
            "payments": payments_n,
            "payers": payers_n,
            "payments_per_payer": round(payments_n / payers_n, 2) if payers_n else None,
            "conversion": {
                "paid": int(all_conv["n"]),
                "registered": int(all_conv["m"]),
                "rate": round(int(all_conv["n"]) / int(all_conv["m"]) * 100, 1) if all_conv["m"] else None,
            },
        },
        "active": {
            "today_visited": int(act["today_visited"]),
            "today_acted": int(act["today_acted"]),
            "week": int(act["week"]),
            "month": int(act["month"]),
        },
    }


def _status(conn, now: datetime) -> dict:
    """Полоса «Состояние»: подключено ли и работает, плюс обращения без ответа и платежи без чека."""
    queue = conn.execute(
        """
        SELECT MIN(run_after) AS oldest, COUNT(*) AS waiting
        FROM jobs WHERE status = 'queued' AND run_after <= %(now)s
        """,
        {"now": now},
    ).fetchone()
    lag = int((now - queue["oldest"]).total_seconds() // 60) if queue["oldest"] else 0
    support_open = conn.execute(
        """
        SELECT COUNT(*) AS n FROM support_threads t
        WHERE (SELECT m.author FROM support_messages m WHERE m.thread_id = t.id ORDER BY m.created_at DESC, m.id DESC LIMIT 1) = 'user'
        """
    ).fetchone()["n"]
    no_receipt = conn.execute(
        "SELECT COUNT(*) AS n FROM payments p WHERE p.status = 'succeeded' AND NOT p.test AND NOT p.receipt_done AND p.refunded_rub < p.amount_rub"
    ).fetchone()["n"]
    bridge = ai_bridge.get_settings()
    services = [
        {"key": "ai", "ok": ai.is_configured()},
        {"key": "wordstat", "ok": wordstat.is_configured()},
        {"key": "images", "ok": image_gen.is_configured()},
        {"key": "oauth", "ok": yandex_configured()},
        {"key": "payments", "ok": payments.is_ready()},
        {"key": "email", "ok": mailer.configured()},
        # Мост необязателен: выключен — серая точка, включён, но не дозаполнен — жёлтая.
        {"key": "bridge", "ok": bridge["active"] if bridge["enabled"] else None},
        {"key": "queue", "ok": lag < QUEUE_LAG_MINUTES, "waiting": int(queue["waiting"]), "lag_minutes": lag},
    ]
    return {"services": services, "support_open": int(support_open), "no_receipt": int(no_receipt)}


# --- Сборка --------------------------------------------------------------------------------------


def _public_range(rng: dict | None) -> dict | None:
    if rng is None:
        return None
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in rng.items() if k != "prev"}


def build(p: str = "30d", date_from: str | None = None, date_to: str | None = None, step: str = "", now: datetime | None = None) -> dict:
    now = now or now_msk()
    per = period(p, date_from, date_to, step, now)
    window = conversion_window_days()
    with pool.connection() as conn:
        cur = _numbers(conn, per, window, now)
        prev = _numbers(conn, per["prev"], window, now) if per["prev"] else None
        series = _series(conn, per, per["step"])
        prev_series = _series(conn, per["prev"], per["step"]) if per["prev"] else []
        snapshot = _snapshot(conn, now, window)
        status = _status(conn, now)
    changes = {k: _change(get(cur), get(prev)) for k, get in _COMPARED.items()} if prev else {}
    return {
        "period": {**_public_range(per), "prev": _public_range(per["prev"])},
        "conversion_window_days": window,
        "current": cur,
        "previous": prev,
        "changes": changes,
        "now": snapshot,
        "series": series,
        "prev_series": prev_series[: len(series)],
        "status": status,
        "generated_at": now.isoformat(),
    }


_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def build_cached(p: str = "30d", date_from: str | None = None, date_to: str | None = None, step: str = "") -> dict:
    """Тот же ответ на одинаковый запрос держится минуту: дашборд открывают часто, а считать его дорого."""
    key = (p, date_from or "", date_to or "", step)
    mono = time.monotonic()
    with _cache_lock:
        for k in [k for k, (at, _) in _cache.items() if mono - at > CACHE_SECONDS]:
            del _cache[k]
        hit = _cache.get(key)
    if hit:
        return hit[1]
    out = build(p, date_from, date_to, step)
    with _cache_lock:
        _cache[key] = (mono, out)
    return out


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
