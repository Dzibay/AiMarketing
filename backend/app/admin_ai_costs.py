"""Страница «Расходы на ИИ» админки: шесть вкладок над ai_usage и ai_errors.

Период — тот же, что у дашборда (admin_dashboard.period), сравнение — с прошлым отрезком той же длины.
Общие фильтры: вид (model — модели, wordstat — Вордстат, image — картинки) и источник (cabinet — кабинет,
landing — лендинг). Себестоимость — ai_usage.cost_rub; единицы — ai_usage.charged.

Оценки, а не точные цифры:
- экономия на кэше — кэшированные токены входа по полной цене входа минус по цене кэша, по ставкам класса;
- переплата из-за подмен — фактическая себестоимость подменённых вызовов минус та же работа по ставкам
  назначенного класса (класс у вызова — всегда назначенный, модель — та, что ответила).

«Люди и тарифы»: человек относится к своему текущему тарифу; закончившийся — «без тарифа». Выручка — оплаты
за период (минус возвраты, без тестовых), расход — вызовы за период. Убыточный — расход больше выручки.
"""

from __future__ import annotations

from datetime import date

from app import admin_dashboard as dash
from app import admin_users, ai, billing, plans
from app.clock import MSK, now_msk
from app.db import pool
from app.yandex import YandexError

KINDS = ("model", "wordstat", "image")
SOURCES = ("cabinet", "landing")
SPLITS = ("class", "kind")
PER_PAGE = 50
TOP_CHATS = 10
TOP_MODELS = 6
RECENT_ERRORS = 30
TASK_LABELS = {**ai.TASK_LABELS, "wordstat": "Вордстат", "images": "Картинки"}
PEOPLE_SORTS = ("cost", "revenue", "margin", "units", "share", "calls", "email")
JOURNAL_SORTS = ("time", "cost")

_PAID = dash._PAID
_PAID_AT = dash._PAID_AT


# --- Общее ---------------------------------------------------------------------------------------


def _clean(kind: str = "", source: str = "") -> tuple[str, str]:
    kind, source = (kind or "").strip(), (source or "").strip()
    if kind and kind not in KINDS:
        raise YandexError("ai-costs", "Вид — модели, Вордстат или картинки")
    if source and source not in SOURCES:
        raise YandexError("ai-costs", "Источник — кабинет или лендинг")
    return kind, source


def _where(kind: str, source: str, alias: str = "a") -> str:
    """Условия фильтров для ai_usage (параметры %(kind)s, %(source)s, %(s)s, %(e)s)."""
    out = [f"{alias}.created_at >= %(s)s", f"{alias}.created_at < %(e)s"]
    if kind:
        out.append(f"{alias}.kind = %(kind)s")
    if source:
        out.append(f"{alias}.source = %(source)s")
    return " AND ".join(out)


def _err_where(kind: str, source: str, alias: str = "r") -> str:
    """То же для ai_errors: ошибки бывают только у моделей — при виде «Вордстат» или «картинки» их нет."""
    out = [f"{alias}.created_at >= %(s)s", f"{alias}.created_at < %(e)s"]
    if kind and kind != "model":
        out.append("FALSE")
    if source:
        out.append(f"{alias}.source = %(source)s")
    return " AND ".join(out)


def _period(p: str, date_from: str | None, date_to: str | None, step: str, now) -> dict:
    """Как у дашборда; «всё время» начинается не позже первого вызова ИИ (лендинг мог тратить до регистраций)."""
    per = dash.period(p or "30d", date_from, date_to, step, now)
    if per["preset"] != "all":
        return per
    with pool.connection() as conn:
        first = conn.execute("SELECT MIN(created_at) AS first FROM ai_usage").fetchone()["first"]
    if first is None or first >= per["start"]:
        return per
    d1 = first.astimezone(MSK).date()
    days = (date.fromisoformat(per["to"]) - d1).days + 1
    return {
        **per,
        "from": d1.isoformat(),
        "start": dash._day_start(d1),
        "days": days,
        "step": step if step in dash.STEPS else dash.auto_step(days),
    }


def _public_period(per: dict) -> dict:
    prev = per["prev"]
    return {
        "preset": per["preset"],
        "from": per["from"],
        "to": per["to"],
        "days": per["days"],
        "step": per["step"],
        "prev": {"from": prev["from"], "to": prev["to"]} if prev else None,
    }


def _params(per: dict, kind: str, source: str, rng: dict | None = None) -> dict:
    rng = rng or per
    return {"s": rng["start"], "e": rng["end"], "kind": kind, "source": source}


def _money(v) -> float:
    return round(float(v or 0), 2)


def _cost(v) -> float:
    """Себестоимость — до копейки с запасом: у дешёвых вызовов это доли копейки."""
    return round(float(v or 0), 4)


def _share(part, whole) -> float | None:
    return round(float(part) * 100 / float(whole), 1) if whole else None


def _change(cur, prev) -> float | None:
    if prev is None or cur is None or not prev:
        return None
    return round((float(cur) - float(prev)) * 100 / float(prev), 1)


def _classes() -> dict[str, dict]:
    return {c["id"]: c for c in ai.model_classes()}


def _class_name(classes: dict, cid: str) -> str:
    return (classes.get(cid) or {}).get("name") or cid or "без класса"


def _buckets(per: dict) -> list[date]:
    return dash._buckets(date.fromisoformat(per["from"]), date.fromisoformat(per["to"]), per["step"])


def _bucket_sql(per: dict, column: str = "a.created_at") -> str:
    return dash._ts_bucket(column, per["step"])


def base(kind: str = "", source: str = "", p: str = "30d", date_from=None, date_to=None, step: str = "", now=None):
    kind, source = _clean(kind, source)
    per = _period(p, date_from, date_to, step, now or now_msk())
    return kind, source, per


# --- Обзор ---------------------------------------------------------------------------------------


def _totals(conn, kind: str, source: str, rng: dict) -> dict:
    params = _params(rng, kind, source, rng)
    where = _where(kind, source)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS calls, SUM(a.cost_rub) AS cost, SUM(a.charged) AS units,
               SUM(a.cost_rub) FILTER (WHERE a.source = 'landing') AS landing,
               SUM(a.cost_rub) FILTER (WHERE a.kind = 'model' AND a.task = 'chat') AS chat_cost,
               COUNT(*) FILTER (WHERE a.kind = 'model' AND a.task = 'chat') AS chat_calls,
               SUM(a.tokens_in) FILTER (WHERE a.kind = 'model') AS tin,
               SUM(a.tokens_cached) FILTER (WHERE a.kind = 'model') AS tcached
        FROM ai_usage a WHERE {where}
        """,
        params,
    ).fetchone()
    questions = 0
    if source != "landing" and kind in ("", "model"):
        questions = conn.execute(
            "SELECT COUNT(*) AS n FROM user_events WHERE kind = 'ai_question' AND created_at >= %(s)s AND created_at < %(e)s",
            params,
        ).fetchone()["n"]
    cached_by_class = conn.execute(
        f"""
        SELECT a.model_class, SUM(a.tokens_cached) AS cached FROM ai_usage a
        WHERE {where} AND a.kind = 'model' AND a.tokens_cached > 0 GROUP BY 1
        """,
        params,
    ).fetchall()
    return {
        "calls": int(row["calls"]),
        "cost": float(row["cost"] or 0),
        "units": float(row["units"] or 0),
        "landing": float(row["landing"] or 0),
        "chat_cost": float(row["chat_cost"] or 0),
        "chat_calls": int(row["chat_calls"] or 0),
        "questions": int(questions or 0),
        "tokens_in": int(row["tin"] or 0),
        "tokens_cached": int(row["tcached"] or 0),
        "cached_by_class": {r["model_class"]: int(r["cached"] or 0) for r in cached_by_class},
    }


def _cache_saved(cached_by_class: dict[str, int], classes: dict) -> float:
    saved = 0.0
    for cid, cached in cached_by_class.items():
        cls = classes.get(cid) or {}
        full, cheap = cls.get("cost_in"), cls.get("cost_cache")
        if full is None or cheap is None:
            continue
        saved += cached * (float(full) - float(cheap)) / 1_000_000
    return saved


def _numbers(t: dict, days: int, classes: dict) -> dict:
    return {
        "cost_rub": _money(t["cost"]),
        "per_day_rub": _money(t["cost"] / days) if days else 0.0,
        "calls": t["calls"],
        "question_rub": _cost(t["chat_cost"] / t["questions"]) if t["questions"] else None,
        "questions": t["questions"],
        "cache_share": _share(t["tokens_cached"], t["tokens_in"]),
        "cache_saved_rub": _money(_cache_saved(t["cached_by_class"], classes)),
        "landing_rub": _money(t["landing"]),
        "units": round(t["units"], 2),
        "markup": round(t["units"] / t["cost"], 2) if t["cost"] > 0 else None,
    }


def overview(kind="", source="", p="30d", date_from=None, date_to=None, step="", split="class", now=None) -> dict:
    """Карточки за период с изменением к прошлому отрезку и ряды по дням из частей: по классам или видам."""
    kind, source, per = base(kind, source, p, date_from, date_to, step, now)
    split = split if split in SPLITS else "class"
    classes = _classes()
    with pool.connection() as conn:
        cur = _totals(conn, kind, source, per)
        prev = _totals(conn, kind, source, per["prev"]) if per["prev"] else None
        part = "CASE WHEN a.kind = 'model' THEN 'class:' || a.model_class ELSE a.kind END" if split == "class" else "a.kind"
        rows = conn.execute(
            f"""
            SELECT {_bucket_sql(per)} AS b, {part} AS part, COUNT(*) AS calls, SUM(a.cost_rub) AS cost,
                   SUM(a.tokens_in + a.tokens_out) AS tokens
            FROM ai_usage a WHERE {_where(kind, source)} GROUP BY 1, 2
            """,
            _params(per, kind, source),
        ).fetchall()
    numbers = _numbers(cur, per["days"], classes)
    changes = {}
    if prev is not None:
        before = _numbers(prev, per["days"], classes)
        changes = {k: _change(numbers[k], before[k]) for k in ("cost_rub", "calls", "question_rub", "landing_rub", "units")}
        changes["cache_share"] = (
            round(numbers["cache_share"] - before["cache_share"], 1)
            if numbers["cache_share"] is not None and before["cache_share"] is not None
            else None
        )
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["part"]] = totals.get(r["part"], 0) + float(r["cost"] or 0) + int(r["calls"]) * 1e-9
    parts = sorted(totals, key=lambda k: -totals[k])

    def label(key: str) -> str:
        if key.startswith("class:"):
            return f"Модель: {_class_name(classes, key[6:])}"
        return billing.KINDS.get(key, key)

    by_bucket: dict[date, dict] = {}
    for r in rows:
        slot = by_bucket.setdefault(r["b"], {"rub": {}, "calls": {}, "tokens": {}})
        slot["rub"][r["part"]] = _cost(r["cost"])
        slot["calls"][r["part"]] = int(r["calls"])
        slot["tokens"][r["part"]] = int(r["tokens"] or 0)
    empty = {"rub": {}, "calls": {}, "tokens": {}}
    return {
        "period": _public_period(per),
        "numbers": numbers,
        "changes": changes,
        "split": split,
        "parts": [{"key": k, "label": label(k)} for k in parts],
        "series": [{"day": b.isoformat(), **by_bucket.get(b, empty)} for b in _buckets(per)],
    }


# --- Модели --------------------------------------------------------------------------------------


def models(kind="", source="", p="30d", date_from=None, date_to=None, step="", now=None) -> dict:
    """Какие модели отвечали: вызовы, подмены, токены, рубли, доля; ряд по дням — шесть дорогих и «остальные»."""
    kind, source, per = base(kind, source, p, date_from, date_to, step, now)
    classes = _classes()
    params = _params(per, kind, source)
    where = _where(kind, source) + " AND a.kind = 'model'"
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT a.model, a.model_class, COUNT(*) AS calls, COUNT(*) FILTER (WHERE a.fallback_from <> '') AS subs,
                   SUM(a.tokens_in) AS tin, SUM(a.tokens_cached) AS tcached, SUM(a.tokens_out) AS tout,
                   SUM(a.cost_rub) AS cost, SUM(a.charged) AS units
            FROM ai_usage a WHERE {where} GROUP BY 1, 2 ORDER BY SUM(a.cost_rub) DESC NULLS LAST, COUNT(*) DESC
            """,
            params,
        ).fetchall()
        daily = conn.execute(
            f"""
            SELECT {_bucket_sql(per)} AS b, a.model, SUM(a.cost_rub) AS cost
            FROM ai_usage a WHERE {where} GROUP BY 1, 2
            """,
            params,
        ).fetchall()
    total = sum(float(r["cost"] or 0) for r in rows)
    items = [
        {
            "model": r["model"] or "",
            "model_class": r["model_class"] or "",
            "class_name": _class_name(classes, r["model_class"]),
            "calls": int(r["calls"]),
            "substitutes": int(r["subs"]),
            "tokens_in": int(r["tin"] or 0),
            "tokens_cached": int(r["tcached"] or 0),
            "cache_share": _share(r["tcached"] or 0, r["tin"] or 0),
            "tokens_out": int(r["tout"] or 0),
            "cost_rub": _cost(r["cost"]),
            "units": round(float(r["units"] or 0), 2),
            "per_call_rub": _cost(float(r["cost"] or 0) / int(r["calls"])) if r["calls"] else None,
            "share": _share(r["cost"] or 0, total),
        }
        for r in rows
    ]
    by_model: dict[str, float] = {}
    for r in rows:
        by_model[r["model"] or ""] = by_model.get(r["model"] or "", 0) + float(r["cost"] or 0)
    top = [m for m, _ in sorted(by_model.items(), key=lambda kv: -kv[1])[:TOP_MODELS]]
    lines = [{"key": f"m{i}", "label": m or "без модели"} for i, m in enumerate(top)]
    if len(by_model) > len(top):
        lines.append({"key": "other", "label": "Остальные"})
    key_of = {m: f"m{i}" for i, m in enumerate(top)}
    by_bucket: dict[date, dict] = {}
    for r in daily:
        slot = by_bucket.setdefault(r["b"], {})
        k = key_of.get(r["model"] or "", "other")
        slot[k] = _cost(slot.get(k, 0) + float(r["cost"] or 0))
    return {
        "period": _public_period(per),
        "items": items,
        "total_rub": _money(total),
        "lines": lines,
        "series": [{"day": b.isoformat(), **by_bucket.get(b, {})} for b in _buckets(per)],
    }


# --- Задачи --------------------------------------------------------------------------------------


def tasks(kind="", source="", p="30d", date_from=None, date_to=None, step="", now=None) -> dict:
    """По задачам: вызовы, токены, рубли, цена вызова, доля. По чату — цена вопроса и самые дорогие чаты."""
    kind, source, per = base(kind, source, p, date_from, date_to, step, now)
    params = _params(per, kind, source)
    where = _where(kind, source)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT a.kind, a.task, MIN(a.source) AS src, COUNT(*) AS calls, SUM(a.tokens_in) AS tin,
                   SUM(a.tokens_out) AS tout, SUM(a.cost_rub) AS cost, SUM(a.charged) AS units
            FROM ai_usage a WHERE {where} GROUP BY 1, 2 ORDER BY SUM(a.cost_rub) DESC NULLS LAST, COUNT(*) DESC
            """,
            params,
        ).fetchall()
        t = _totals(conn, kind, source, per)
        chats = conn.execute(
            f"""
            SELECT a.chat_id, COUNT(*) AS calls, SUM(a.cost_rub) AS cost, SUM(a.charged) AS units,
                   c.title, c.user_id, u.email,
                   (SELECT COUNT(*) FROM user_events e WHERE e.kind = 'ai_question' AND e.meta->>'chat_id' = a.chat_id::text
                    AND e.created_at >= %(s)s AND e.created_at < %(e)s) AS questions
            FROM ai_usage a
            JOIN ai_chats c ON c.id = a.chat_id
            LEFT JOIN users u ON u.id = c.user_id
            WHERE {where} AND a.chat_id IS NOT NULL
            GROUP BY a.chat_id, c.title, c.user_id, u.email
            ORDER BY SUM(a.cost_rub) DESC NULLS LAST LIMIT {TOP_CHATS}
            """,
            params,
        ).fetchall()
    total = sum(float(r["cost"] or 0) for r in rows)
    items = []
    for r in rows:
        tokens = int(r["tin"] or 0) + int(r["tout"] or 0)
        items.append(
            {
                "kind": r["kind"],
                "task": r["task"],
                "label": TASK_LABELS.get(r["task"], r["task"]),
                "landing": r["src"] == "landing",
                "calls": int(r["calls"]),
                "tokens_in": int(r["tin"] or 0),
                "tokens_out": int(r["tout"] or 0),
                "tokens": tokens,
                "cost_rub": _cost(r["cost"]),
                "units": round(float(r["units"] or 0), 2),
                "per_call_rub": _cost(float(r["cost"] or 0) / int(r["calls"])) if r["calls"] else None,
                "share": _share(r["cost"] or 0, total),
            }
        )
    return {
        "period": _public_period(per),
        "items": items,
        "total_rub": _money(total),
        "chat": {
            "questions": t["questions"],
            "calls": t["chat_calls"],
            "cost_rub": _money(t["chat_cost"]),
            "question_rub": _cost(t["chat_cost"] / t["questions"]) if t["questions"] else None,
            "calls_per_question": round(t["chat_calls"] / t["questions"], 2) if t["questions"] else None,
            "top": [
                {
                    "chat_id": int(r["chat_id"]),
                    "title": r["title"] or "",
                    "user_id": r["user_id"],
                    "email": r["email"] or "",
                    "calls": int(r["calls"]),
                    "questions": int(r["questions"] or 0),
                    "cost_rub": _cost(r["cost"]),
                    "units": round(float(r["units"] or 0), 2),
                }
                for r in chats
            ],
        },
    }


# --- Люди и тарифы -------------------------------------------------------------------------------


def _people_rows(kind: str, source: str, per: dict, now) -> list[dict]:
    """Все, у кого за период был расход или оплата: расход, выручка, единицы, текущий тариф, доля лимита."""
    params = {**_params(per, kind, source), "now": now}
    where = _where(kind, source)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            WITH c AS (
                SELECT a.user_id, COUNT(*) AS calls, SUM(a.cost_rub) AS cost, SUM(a.charged) AS units
                FROM ai_usage a WHERE {where} AND a.user_id IS NOT NULL GROUP BY 1
            ),
            r AS (
                SELECT p.user_id, SUM(p.amount_rub - p.refunded_rub) AS rub FROM payments p
                WHERE {_PAID} AND {_PAID_AT} >= %(s)s AND {_PAID_AT} < %(e)s AND p.user_id IS NOT NULL GROUP BY 1
            ),
            ids AS (SELECT user_id FROM c UNION SELECT user_id FROM r)
            SELECT {admin_users._COLUMNS},
                   COALESCE(c.calls, 0) AS p_calls, COALESCE(c.cost, 0) AS p_cost, COALESCE(c.units, 0) AS p_units,
                   COALESCE(r.rub, 0) AS p_revenue
            {admin_users._FROM}
            JOIN ids ON ids.user_id = u.id
            LEFT JOIN c ON c.user_id = u.id
            LEFT JOIN r ON r.user_id = u.id
            """,
            params,
        ).fetchall()
    subs = {r["id"]: plans.subscription_from(r, now=now) for r in rows}
    spent = admin_users._periods_spend(subs)
    out = []
    for r in rows:
        sub = subs[r["id"]]
        live = r["plan_id"] is not None and (r["ends_at"] is None or r["ends_at"] > now)
        if not live:
            plan_key, plan_name = "none", "Без тарифа"
        elif r["is_trial"]:
            plan_key, plan_name = "trial", "Пробный"
        else:
            plan_key, plan_name = f"plan:{r['plan_id']}", r["plan_name"] or f"Тариф #{r['plan_id']}"
        cost, revenue = float(r["p_cost"]), float(r["p_revenue"])
        limit = sub["units_limit"]
        out.append(
            {
                "user_id": int(r["id"]),
                "email": r["email"] or "",
                "name": r["name"] or "",
                "plan_key": plan_key,
                "plan_name": plan_name,
                "calls": int(r["p_calls"]),
                "cost_rub": cost,
                "revenue_rub": revenue,
                "margin_rub": revenue - cost,
                "units": float(r["p_units"]),
                "limit_spent": spent.get(r["id"], 0.0),
                "limit": float(limit) if limit is not None else None,
                "share": _share(spent.get(r["id"], 0.0), limit) if limit else None,
                "loss": cost > revenue and cost > 0,
            }
        )
    return out


def people(kind="", source="", p="30d", date_from=None, date_to=None, step="", sort="cost", desc=True, page=1, loss=False, now=None) -> dict:
    """По тарифам — итоги; по людям — страница с сортировкой по любой колонке. loss — только убыточные."""
    now = now or now_msk()
    kind, source, per = base(kind, source, p, date_from, date_to, step, now)
    rows = _people_rows(kind, source, per, now)

    plans_out: dict[str, dict] = {}
    for r in rows:
        g = plans_out.setdefault(
            r["plan_key"],
            {"key": r["plan_key"], "name": r["plan_name"], "users": 0, "revenue_rub": 0.0, "cost_rub": 0.0},
        )
        g["users"] += 1
        g["revenue_rub"] += r["revenue_rub"]
        g["cost_rub"] += r["cost_rub"]
    order = {"trial": 1, "none": 2}
    by_plan = []
    for g in sorted(plans_out.values(), key=lambda g: (order.get(g["key"], 0), -g["revenue_rub"], g["name"])):
        margin = g["revenue_rub"] - g["cost_rub"]
        by_plan.append(
            {
                **g,
                "revenue_rub": _money(g["revenue_rub"]),
                "cost_rub": _money(g["cost_rub"]),
                "margin_rub": _money(margin),
                "margin_pct": _share(margin, g["revenue_rub"]),
                "per_user_rub": _money(g["cost_rub"] / g["users"]) if g["users"] else 0.0,
            }
        )

    sort = sort if sort in PEOPLE_SORTS else "cost"
    key = {
        "cost": lambda r: r["cost_rub"],
        "revenue": lambda r: r["revenue_rub"],
        "margin": lambda r: r["margin_rub"],
        "units": lambda r: r["units"],
        "share": lambda r: r["share"] if r["share"] is not None else -1,
        "calls": lambda r: r["calls"],
        "email": lambda r: r["email"].lower(),
    }[sort]
    losses = sum(1 for r in rows if r["loss"])
    shown = [r for r in rows if r["loss"]] if loss else rows
    shown.sort(key=lambda r: (key(r), r["user_id"]), reverse=bool(desc))
    pages = max(1, -(-len(shown) // PER_PAGE))
    page = max(1, min(int(page or 1), pages))
    chunk = shown[(page - 1) * PER_PAGE : page * PER_PAGE]
    return {
        "period": _public_period(per),
        "by_plan": by_plan,
        "items": [
            {
                **r,
                "cost_rub": _cost(r["cost_rub"]),
                "revenue_rub": _money(r["revenue_rub"]),
                "margin_rub": _money(r["margin_rub"]),
                "units": round(r["units"], 2),
            }
            for r in chunk
        ],
        "found": len(shown),
        "losses": losses,
        "page": page,
        "pages": pages,
        "per_page": PER_PAGE,
    }


# --- Надёжность ----------------------------------------------------------------------------------


def _overpay(conn, kind: str, source: str, per: dict, classes: dict) -> dict:
    """Подменённые вызовы: сколько стоили на деле и сколько стоили бы по ставкам назначенного класса."""
    rows = conn.execute(
        f"""
        SELECT a.model_class, COUNT(*) AS n, SUM(a.tokens_in) AS tin, SUM(a.tokens_out) AS tout,
               SUM(a.tokens_cached) AS tcached, SUM(a.cost_rub) AS cost
        FROM ai_usage a WHERE {_where(kind, source)} AND a.kind = 'model' AND a.fallback_from <> ''
              AND a.cost_rub IS NOT NULL
        GROUP BY 1
        """,
        _params(per, kind, source),
    ).fetchall()
    actual = planned = 0.0
    calls = unpriced = 0
    for r in rows:
        cls = classes.get(r["model_class"]) or {}
        would = ai.cost_rub(cls, int(r["tin"] or 0), int(r["tout"] or 0), int(r["tcached"] or 0))
        if would is None:
            unpriced += int(r["n"])
            continue
        calls += int(r["n"])
        actual += float(r["cost"] or 0)
        planned += would
    return {
        "calls": calls,
        "actual_rub": _money(actual),
        "planned_rub": _money(planned),
        "overpay_rub": _money(actual - planned),
        "unpriced": unpriced,
    }


def reliability(kind="", source="", p="30d", date_from=None, date_to=None, step="", now=None) -> dict:
    """Подмены и ошибки: ряд по дням, по моделям (кого и кем подменяли), причины, переплата, последние сбои."""
    kind, source, per = base(kind, source, p, date_from, date_to, step, now)
    classes = _classes()
    params = _params(per, kind, source)
    uw = _where(kind, source) + " AND a.kind = 'model'"
    ew = _err_where(kind, source)
    with pool.connection() as conn:
        calls_daily = conn.execute(
            f"""
            SELECT {_bucket_sql(per)} AS b, COUNT(*) AS calls, COUNT(*) FILTER (WHERE a.fallback_from <> '') AS subs
            FROM ai_usage a WHERE {uw} GROUP BY 1
            """,
            params,
        ).fetchall()
        errors_daily = conn.execute(
            f"SELECT {_bucket_sql(per, 'r.created_at')} AS b, COUNT(*) AS n FROM ai_errors r WHERE {ew} GROUP BY 1",
            params,
        ).fetchall()
        answered = conn.execute(
            f"SELECT a.model, COUNT(*) AS calls FROM ai_usage a WHERE {uw} GROUP BY 1", params
        ).fetchall()
        failed = conn.execute(
            f"""
            SELECT r.model, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE NOT r.answered AND NOT r.retried AND NOT r.fallback) AS lost
            FROM ai_errors r WHERE {ew} GROUP BY 1
            """,
            params,
        ).fetchall()
        pairs = conn.execute(
            f"""
            SELECT a.fallback_from AS assigned, a.model AS reserve, COUNT(*) AS n
            FROM ai_usage a WHERE {uw} AND a.fallback_from <> '' GROUP BY 1, 2 ORDER BY 3 DESC
            """,
            params,
        ).fetchall()
        reasons = conn.execute(
            f"""
            SELECT r.code, COUNT(*) AS n, (ARRAY_AGG(r.message ORDER BY r.id DESC))[1] AS sample
            FROM ai_errors r WHERE {ew} GROUP BY 1 ORDER BY 2 DESC LIMIT 20
            """,
            params,
        ).fetchall()
        recent = conn.execute(
            f"""
            SELECT r.created_at, r.task, r.model, r.model_class, r.code, r.message, r.retried, r.fallback,
                   r.answered, r.answered_by, r.source, r.user_id, u.email
            FROM ai_errors r LEFT JOIN users u ON u.id = r.user_id
            WHERE {ew} ORDER BY r.created_at DESC, r.id DESC LIMIT {RECENT_ERRORS}
            """,
            params,
        ).fetchall()
        overpay = _overpay(conn, kind, source, per, classes)

    cd = {r["b"]: r for r in calls_daily}
    ed = {r["b"]: int(r["n"]) for r in errors_daily}
    series = []
    for b in _buckets(per):
        calls = int(cd[b]["calls"]) if b in cd else 0
        subs = int(cd[b]["subs"]) if b in cd else 0
        errs = ed.get(b, 0)
        series.append(
            {
                "day": b.isoformat(),
                "calls": calls,
                "subs": subs,
                "errors": errs,
                "sub_share": _share(subs, calls) if calls else None,
                "error_share": _share(errs, calls + errs) if calls + errs else None,
            }
        )

    by_model: dict[str, dict] = {}

    def slot(model: str) -> dict:
        return by_model.setdefault(
            model, {"model": model, "calls": 0, "errors": 0, "lost": 0, "replaced": [], "replaced_by": []}
        )

    for r in answered:
        slot(r["model"] or "")["calls"] = int(r["calls"])
    for r in failed:
        s = slot(r["model"] or "")
        s["errors"], s["lost"] = int(r["n"]), int(r["lost"])
    for r in pairs:
        slot(r["reserve"] or "")["replaced"].append({"model": r["assigned"], "calls": int(r["n"])})
        slot(r["assigned"] or "")["replaced_by"].append({"model": r["reserve"], "calls": int(r["n"])})
    models_out = []
    for s in by_model.values():
        attempts = s["calls"] + s["errors"]
        models_out.append({**s, "error_share": _share(s["errors"], attempts) if attempts else None})
    models_out.sort(key=lambda s: (-s["errors"], -s["calls"], s["model"]))

    total_calls = sum(x["calls"] for x in series)
    total_subs = sum(x["subs"] for x in series)
    total_errors = sum(x["errors"] for x in series)
    return {
        "period": _public_period(per),
        "numbers": {
            "calls": total_calls,
            "substitutions": total_subs,
            "sub_share": _share(total_subs, total_calls),
            "errors": total_errors,
            "error_share": _share(total_errors, total_calls + total_errors),
            "lost": sum(s["lost"] for s in models_out),
        },
        "series": series,
        "models": models_out,
        "reasons": [{"code": r["code"] or "", "count": int(r["n"]), "sample": r["sample"] or ""} for r in reasons],
        "overpay": overpay,
        "recent": [
            {
                "created_at": r["created_at"].isoformat(),
                "task": TASK_LABELS.get(r["task"], r["task"]),
                "model": r["model"],
                "class_name": _class_name(classes, r["model_class"]) if r["model_class"] else "",
                "code": r["code"],
                "message": r["message"],
                "retried": bool(r["retried"]),
                "fallback": bool(r["fallback"]),
                "answered": bool(r["answered"]),
                "answered_by": r["answered_by"],
                "source": r["source"],
                "user_id": r["user_id"],
                "email": r["email"] or "",
            }
            for r in recent
        ],
    }


# --- Журнал --------------------------------------------------------------------------------------


def journal(
    kind="", source="", p="30d", date_from=None, date_to=None, step="",
    user="", task="", model="", subs=False, sort="time", page=1, now=None,
) -> dict:
    """Лента вызовов с фильтрами; sort=cost — самые дорогие вызовы периода."""
    kind, source, per = base(kind, source, p, date_from, date_to, step, now)
    classes = _classes()
    params = _params(per, kind, source)
    where = [_where(kind, source)]
    q = (user or "").strip()
    if q:
        ident = q.lstrip("#")
        if ident.isdigit() and len(ident) < 18:
            params["uid"] = int(ident)
            where.append("a.user_id = %(uid)s")
        else:
            params["like"] = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("u.email ILIKE %(like)s")
    if task:
        params["task"] = task
        where.append("a.task = %(task)s")
    if model:
        params["model"] = model
        where.append("a.model = %(model)s")
    if subs:
        where.append("a.fallback_from <> ''")
    cond = " AND ".join(where)
    order = "a.cost_rub DESC NULLS LAST, a.id DESC" if sort == "cost" else "a.created_at DESC, a.id DESC"
    with pool.connection() as conn:
        head = conn.execute(
            f"""
            SELECT COUNT(*) AS n, SUM(a.cost_rub) AS cost, SUM(a.charged) AS units
            FROM ai_usage a LEFT JOIN users u ON u.id = a.user_id WHERE {cond}
            """,
            params,
        ).fetchone()
        pages = max(1, -(-int(head["n"]) // PER_PAGE))
        page = max(1, min(int(page or 1), pages))
        rows = conn.execute(
            f"""
            SELECT a.id, a.created_at, a.user_id, u.email, a.kind, a.task, a.model, a.model_class, a.fallback_from,
                   a.fallback_reason, a.tokens_in, a.tokens_cached, a.tokens_out, a.charged, a.cost_rub, a.source,
                   a.chat_id, a.duration_ms, a.estimated
            FROM ai_usage a LEFT JOIN users u ON u.id = a.user_id
            WHERE {cond} ORDER BY {order} LIMIT {PER_PAGE} OFFSET %(offset)s
            """,
            {**params, "offset": (page - 1) * PER_PAGE},
        ).fetchall()
        options = conn.execute(
            f"""
            SELECT ARRAY(SELECT DISTINCT a.task FROM ai_usage a WHERE {_where(kind, source)} ORDER BY 1) AS tasks,
                   ARRAY(SELECT DISTINCT a.model FROM ai_usage a WHERE {_where(kind, source)} AND a.model <> ''
                         ORDER BY 1) AS models
            """,
            _params(per, kind, source),
        ).fetchone()
    return {
        "period": _public_period(per),
        "items": [
            {
                "id": int(r["id"]),
                "created_at": r["created_at"].isoformat(),
                "user_id": r["user_id"],
                "email": r["email"] or "",
                "kind": r["kind"],
                "task": r["task"],
                "task_label": TASK_LABELS.get(r["task"], r["task"]),
                "model": r["model"] or "",
                "class_name": _class_name(classes, r["model_class"]) if r["model_class"] else "",
                "fallback_from": r["fallback_from"] or "",
                "fallback_reason": r["fallback_reason"] or "",
                "tokens_in": int(r["tokens_in"]),
                "tokens_cached": int(r["tokens_cached"]),
                "tokens_out": int(r["tokens_out"]),
                "units": round(float(r["charged"] or 0), 4),
                "cost_rub": float(r["cost_rub"]) if r["cost_rub"] is not None else None,
                "source": r["source"],
                "chat_id": r["chat_id"],
                "duration_ms": r["duration_ms"],
                "estimated": bool(r["estimated"]),
            }
            for r in rows
        ],
        "total": {"calls": int(head["n"]), "cost_rub": _money(head["cost"]), "units": round(float(head["units"] or 0), 2)},
        "options": {
            "tasks": [{"value": t, "label": TASK_LABELS.get(t, t)} for t in options["tasks"] or []],
            "models": list(options["models"] or []),
        },
        "page": page,
        "pages": pages,
        "per_page": PER_PAGE,
    }
