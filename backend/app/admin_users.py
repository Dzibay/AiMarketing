"""Пользователи в админке: список с поиском, фильтрами, сортировкой и страницами — и карточка одного человека.

Список — одна выборка на страницу (подписка, платежи, расход ИИ, проекты, источник, обращения), плюс один
запрос на расход за текущий период лимита всех строк страницы. Деньги здесь — только для админа.
"""

from datetime import datetime, timedelta

from app import ai, billing, plans
from app.clock import MSK, now_msk
from app.db import pool
from app.users import admin_user
from app.yandex import YandexError

PER_PAGE = 50
SUBS = {"paying", "trial", "expired", "none", "ending", "ending_paid"}
ENDING_DAYS = 7
ACTIVITY = {"today", "week", "lost", "never"}
# Сортировка: ключ → выражение; пустые значения всегда в конце.
SORTS = {
    "created": "u.created_at",
    "seen": "u.last_seen_at",
    "paid": "COALESCE(pay.paid_rub, 0)",
    "ai": "COALESCE(ai.cost_rub, 0)",
    "ends": "s.ends_at",
}
DAYS_SHOWN = 30

# Платёж считается, если прошёл, не тестовый и не возвращён целиком.
_PAID = "status = 'succeeded' AND NOT test"

_FROM = f"""
    FROM users u
    LEFT JOIN subscriptions s ON s.user_id = u.id
    LEFT JOIN plans p ON p.id = s.plan_id
    LEFT JOIN ref_links rl ON rl.id = u.ref_link_id
    LEFT JOIN ref_link_groups rg ON rg.id = rl.group_id
    LEFT JOIN users ro ON ro.id = rl.owner_user_id
    LEFT JOIN (
        SELECT user_id, SUM(amount_rub - refunded_rub) AS paid_rub,
               COUNT(*) FILTER (WHERE refunded_rub < amount_rub) AS pay_count,
               COALESCE(SUM(days) FILTER (WHERE refunded_rub < amount_rub), 0) AS pay_days
        FROM payments WHERE {_PAID} GROUP BY user_id
    ) pay ON pay.user_id = u.id
    LEFT JOIN (
        SELECT user_id, SUM(cost_rub) AS cost_rub FROM ai_usage WHERE user_id IS NOT NULL GROUP BY user_id
    ) ai ON ai.user_id = u.id
"""

_COLUMNS = """
    u.id, u.email, u.name, u.yandex_uid, u.telegram_chat_id, u.blocked_at, u.blocked_reason, u.email_verified_at,
    u.created_at, u.ui, u.last_seen_at, u.visitor_id, u.first_referrer, u.first_utm,
    u.id AS user_id, u.created_at AS user_created_at,
    s.plan_id, s.starts_at, s.ends_at, s.overrides, s.bonus_units, s.bonus_period_start,
    p.name AS plan_name, p.price_rub, p.days, p.units_limit, p.projects_limit, p.features, p.is_trial,
    rl.id AS ref_id, rl.code AS ref_code, rl.name AS ref_name, rl.group_id AS ref_group_id,
    rg.name AS ref_group_name, rg.color AS ref_group_color, ro.email AS ref_owner_email,
    COALESCE(pay.paid_rub, 0) AS paid_rub, COALESCE(pay.pay_count, 0) AS pay_count,
    COALESCE(pay.pay_days, 0) AS pay_days, COALESCE(ai.cost_rub, 0) AS ai_cost_rub
"""


def _day_start(day) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=MSK)


def _where(f: dict, now: datetime) -> tuple[list[str], dict]:
    where, params = ["TRUE"], {"now": now}
    q = (f.get("q") or "").strip()
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params["like"] = like
        cond = "u.email ILIKE %(like)s OR u.name ILIKE %(like)s OR rl.code ILIKE %(like)s"
        if q.lstrip("#").isdigit() and len(q.lstrip("#")) < 18:
            params["qid"] = int(q.lstrip("#"))
            cond += " OR u.id = %(qid)s"
        where.append(f"({cond})")
    if f.get("plan"):
        params["plan"] = int(f["plan"])
        where.append("s.plan_id = %(plan)s")
    sub = f.get("sub") or ""
    if sub == "none":
        where.append("s.plan_id IS NULL")
    elif sub == "expired":
        where.append("s.plan_id IS NOT NULL AND s.ends_at <= %(now)s")
    elif sub == "trial":
        where.append("p.is_trial AND (s.ends_at IS NULL OR s.ends_at > %(now)s)")
    elif sub == "paying":
        where.append("s.plan_id IS NOT NULL AND NOT p.is_trial AND (s.ends_at IS NULL OR s.ends_at > %(now)s)")
    elif sub in ("ending", "ending_paid"):
        # Подписка кончается в ближайшие 7 дней (как карточка дашборда); «платный» — любой непробный тариф.
        params["ending"] = now + timedelta(days=ENDING_DAYS)
        where.append("s.plan_id IS NOT NULL AND s.ends_at > %(now)s AND s.ends_at <= %(ending)s")
        if sub == "ending_paid":
            where.append("NOT p.is_trial")
    today = _day_start(now.astimezone(MSK).date())
    params["today"] = today
    act = f.get("activity") or ""
    if act == "today":
        where.append("u.last_seen_at >= %(today)s")
    elif act == "week":
        params["week"] = today - timedelta(days=6)
        where.append("u.last_seen_at >= %(week)s")
    elif act == "lost":
        params["lost"] = today - timedelta(days=14)
        where.append("u.last_seen_at < %(lost)s")
    elif act == "never":
        where.append("u.last_seen_at IS NULL")
    source = f.get("source") or ""
    kind, _, ident = source.partition(":")
    if kind == "link" and ident.isdigit():
        params["link"] = int(ident)
        where.append("u.ref_link_id = %(link)s")
    elif kind == "group" and ident.isdigit():
        params["group"] = int(ident)
        where.append("rl.group_id = %(group)s")
    elif source == "personal":
        where.append("rl.owner_user_id IS NOT NULL")
    elif source == "ref":
        where.append("u.ref_link_id IS NOT NULL")
    elif source == "none":
        where.append("u.ref_link_id IS NULL")
    if f.get("paid") == "yes":
        where.append("COALESCE(pay.paid_rub, 0) > 0")
    elif f.get("paid") == "no":
        where.append("COALESCE(pay.paid_rub, 0) <= 0")
    if f.get("blocked"):
        where.append("u.blocked_at IS NOT NULL")
    if f.get("unverified"):
        where.append("u.email_verified_at IS NULL")
    return where, params


def _source(r: dict) -> dict | None:
    """Откуда пришёл: реф-ссылка, метка, сайт-источник, напрямую — или None, если учёта визитов тогда ещё не было."""
    if r.get("ref_id"):
        return {
            "kind": "ref",
            "link_id": r["ref_id"],
            "code": r["ref_code"],
            "name": r["ref_name"] or "",
            "group_id": r["ref_group_id"],
            "group_name": r["ref_group_name"] or "",
            "group_color": r.get("ref_group_color") or "",
            "owner_email": r["ref_owner_email"] or "",
        }
    utm = r.get("first_utm") or {}
    if utm.get("utm_source"):
        return {"kind": "utm", "utm": utm}
    if r.get("first_referrer"):
        return {"kind": "referrer", "referrer": r["first_referrer"]}
    if r.get("visitor_id"):
        return {"kind": "direct"}
    return None


def _periods_spend(subs: dict[int, dict]) -> dict[int, float]:
    """Израсходовано единиц за текущий период лимита — для всех строк одним запросом."""
    if not subs:
        return {}
    ids = list(subs)
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT t.uid, COALESCE(SUM(a.charged), 0) AS spent
            FROM unnest(%s::bigint[], %s::timestamptz[], %s::timestamptz[]) AS t(uid, ps, pe)
            LEFT JOIN ai_usage a ON a.user_id = t.uid AND a.created_at >= t.ps AND a.created_at < t.pe
            GROUP BY t.uid
            """,
            (ids, [subs[i]["period_start"] for i in ids], [subs[i]["period_end"] for i in ids]),
        ).fetchall()
    return {int(r["uid"]): round(float(r["spent"]), 2) for r in rows}


def _extras(ids: list[int]) -> dict[int, dict]:
    """Последняя покупка, проекты, Директ и непрочитанные обращения — для строк страницы."""
    out = {i: {"last_payment": None, "projects": 0, "direct": False, "campaigns": 0, "unread": 0} for i in ids}
    if not ids:
        return out
    with pool.connection() as conn:
        for r in conn.execute(
            f"""
            SELECT DISTINCT ON (user_id) user_id, plan_id, plan_name, days, months, amount_rub, last_day,
                   COALESCE(paid_at, created_at) AS paid_at
            FROM payments WHERE {_PAID} AND refunded_rub < amount_rub AND user_id = ANY(%s)
            ORDER BY user_id, COALESCE(paid_at, created_at) DESC, id DESC
            """,
            (ids,),
        ).fetchall():
            out[r["user_id"]]["last_payment"] = {
                "plan_id": r["plan_id"],
                "plan_name": r["plan_name"] or "",
                "days": int(r["days"]),
                "months": int(r["months"] or 1),
                "amount_rub": float(r["amount_rub"]),
                "last_day": r["last_day"].isoformat() if r["last_day"] else None,
                "paid_at": r["paid_at"].isoformat(),
            }
        for r in conn.execute(
            """
            SELECT u.id,
                   (SELECT COUNT(*) FROM workspaces w WHERE w.user_id = u.id AND w.archived_at IS NULL) AS projects,
                   EXISTS (SELECT 1 FROM direct_accounts d WHERE d.user_id = u.id) AS direct,
                   (SELECT COUNT(*) FROM direct_campaigns c WHERE c.user_id = u.id) AS campaigns,
                   (SELECT COUNT(m.id) FROM support_threads t JOIN support_messages m ON m.thread_id = t.id
                    WHERE t.user_id = u.id AND m.author = 'user' AND m.created_at > t.admin_read_at) AS unread
            FROM users u WHERE u.id = ANY(%s)
            """,
            (ids,),
        ).fetchall():
            out[r["id"]].update(
                projects=int(r["projects"]), direct=bool(r["direct"]), campaigns=int(r["campaigns"]), unread=int(r["unread"])
            )
    return out


def _rows_out(rows: list[dict], now: datetime) -> list[dict]:
    subs = {r["id"]: plans.subscription_from(r, now=now) for r in rows}
    spent = _periods_spend(subs)
    extras = _extras([r["id"] for r in rows])
    items = []
    for r in rows:
        sub, extra = subs[r["id"]], extras[r["id"]]
        paid, cost = round(float(r["paid_rub"]), 2), round(float(r["ai_cost_rub"]), 2)
        items.append(
            {
                **admin_user(r),
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "source": _source(r),
                "subscription": plans.admin_view(r["id"], sub),
                "last_payment": extra["last_payment"],
                "paid": {"rub": paid, "count": int(r["pay_count"]), "days": int(r["pay_days"])},
                "ai_cost_rub": cost,
                "margin_rub": round(paid - cost, 2),
                "usage": {"spent": spent.get(r["id"], 0.0), "limit": sub["units_limit"]},
                "projects": {"count": extra["projects"], "direct": extra["direct"], "campaigns": extra["campaigns"]},
                "support_unread": extra["unread"],
            }
        )
    return items


def list_page(filters: dict | None = None, *, sort: str = "created", desc: bool = True, page: int = 1, now=None) -> dict:
    """Страница списка: строки, итоги по найденным («найдено · платят · заходили сегодня») и число страниц."""
    f = filters or {}
    now = now or now_msk()
    where, params = _where(f, now)
    cond = " AND ".join(where)
    order = SORTS.get(sort, SORTS["created"])
    direction = "DESC" if desc else "ASC"
    with pool.connection() as conn:
        summary = conn.execute(
            f"""
            SELECT COUNT(*) AS found,
                   COUNT(*) FILTER (WHERE s.plan_id IS NOT NULL AND NOT p.is_trial
                                    AND (s.ends_at IS NULL OR s.ends_at > %(now)s)) AS paying,
                   COUNT(*) FILTER (WHERE u.last_seen_at >= %(today)s) AS today
            {_FROM} WHERE {cond}
            """,
            params,
        ).fetchone()
        pages = max(1, -(-int(summary["found"]) // PER_PAGE))
        page = max(1, min(int(page or 1), pages))
        rows = conn.execute(
            f"""
            SELECT {_COLUMNS} {_FROM} WHERE {cond}
            ORDER BY {order} {direction} NULLS LAST, u.id {direction}
            LIMIT {PER_PAGE} OFFSET %(offset)s
            """,
            {**params, "offset": (page - 1) * PER_PAGE},
        ).fetchall()
    return {
        "items": _rows_out(rows, now),
        "summary": {"found": int(summary["found"]), "paying": int(summary["paying"]), "today": int(summary["today"])},
        "page": page,
        "pages": pages,
        "per_page": PER_PAGE,
    }


def filter_options() -> dict:
    """Что выбрать в фильтрах: тарифы, группы и ссылки (без личных — те одним пунктом «по личным ссылкам»)."""
    plan_rows = [
        {"id": p["id"], "name": p["name"], "is_trial": p["is_trial"], "active": p["active"]}
        for p in plans.list_plans(for_admin=True)
    ]
    with pool.connection() as conn:
        groups = conn.execute("SELECT id, name, color FROM ref_link_groups ORDER BY position, id").fetchall()
        links = conn.execute(
            "SELECT id, code, name, group_id FROM ref_links WHERE owner_user_id IS NULL ORDER BY code"
        ).fetchall()
    return {
        "plans": plan_rows,
        "groups": [dict(r) for r in groups],
        "links": [dict(r) for r in links],
    }


def card(user_id: int, *, now=None) -> dict:
    """Карточка: та же строка, что в списке, плюс 30 дней заходов и расхода ИИ, расход по задачам, чаты, проекты."""
    now = now or now_msk()
    with pool.connection() as conn:
        row = conn.execute(f"SELECT {_COLUMNS} {_FROM} WHERE u.id = %(id)s", {"id": user_id}).fetchone()
        if row is None:
            raise YandexError("not-found", "Пользователь не найден")
        today = now.astimezone(MSK).date()
        since_day = today - timedelta(days=DAYS_SHOWN - 1)
        since = _day_start(since_day)
        activity = conn.execute(
            "SELECT day, visits, actions FROM user_activity_days WHERE user_id = %s AND day >= %s ORDER BY day",
            (user_id, since_day),
        ).fetchall()
        ai_days = conn.execute(
            """
            SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                   SUM(cost_rub) AS cost, SUM(charged) AS units, COUNT(*) AS calls
            FROM ai_usage WHERE user_id = %s AND created_at >= %s GROUP BY 1 ORDER BY 1
            """,
            (user_id, since),
        ).fetchall()
        ai_tasks = conn.execute(
            """
            SELECT COALESCE(NULLIF(task, ''), kind) AS task, kind,
                   SUM(cost_rub) AS cost, SUM(charged) AS units, COUNT(*) AS calls
            FROM ai_usage WHERE user_id = %s AND created_at >= %s GROUP BY 1, 2 ORDER BY SUM(cost_rub) DESC
            """,
            (user_id, since),
        ).fetchall()
        chats = conn.execute(
            """
            SELECT ch.id, ch.title, ch.created_at, ch.archived_at, w.name AS project,
                   (SELECT COUNT(*) FROM ai_messages m
                    WHERE m.chat_id = ch.id AND m.role IN ('user', 'assistant') AND m.content <> '') AS messages,
                   (SELECT MAX(m.created_at) FROM ai_messages m WHERE m.chat_id = ch.id) AS last_at,
                   (SELECT COALESCE(SUM(a.cost_rub), 0) FROM ai_usage a WHERE a.chat_id = ch.id) AS cost
            FROM ai_chats ch LEFT JOIN workspaces w ON w.id = ch.workspace_id
            WHERE ch.user_id = %s
            ORDER BY COALESCE((SELECT MAX(m.created_at) FROM ai_messages m WHERE m.chat_id = ch.id), ch.created_at) DESC
            LIMIT 300
            """,
            (user_id,),
        ).fetchall()
        projects = conn.execute(
            """
            SELECT w.id, w.name, w.site, w.archived_at,
                   (SELECT string_agg(DISTINCT d.client_login, ', ') FROM workspace_accounts wa
                    JOIN direct_accounts d ON d.id = wa.account_id WHERE wa.workspace_id = w.id) AS direct,
                   (SELECT COUNT(*) FROM workspace_accounts wa JOIN direct_campaigns c ON c.account_id = wa.account_id
                    WHERE wa.workspace_id = w.id) AS campaigns
            FROM workspaces w WHERE w.user_id = %s ORDER BY w.archived_at NULLS FIRST, w.id
            """,
            (user_id,),
        ).fetchall()
    item = _rows_out([row], now)[0]
    acts = {r["day"]: r for r in activity}
    days = []
    for i in range(DAYS_SHOWN):
        d = since_day + timedelta(days=i)
        a = acts.get(d)
        days.append({"day": d.isoformat(), "visits": int(a["visits"]) if a else 0, "actions": int(a["actions"]) if a else 0})
    spend = {r["day"].isoformat(): r for r in ai_days}
    return {
        "user": item,
        "days": days,
        "ai_days": [
            {
                "day": d["day"],
                "cost_rub": round(float(spend[d["day"]]["cost"]), 2) if d["day"] in spend else 0.0,
                "units": round(float(spend[d["day"]]["units"]), 2) if d["day"] in spend else 0.0,
            }
            for d in days
        ],
        "ai_tasks": [
            {
                "task": r["task"],
                # Задача без своей подписи — её код; запись без задачи — вид расхода («Вордстат», «Картинки»).
                "label": ai.TASK_LABELS.get(r["task"])
                or (billing.KINDS.get(r["kind"]) if r["task"] == r["kind"] else r["task"])
                or r["task"],
                "cost_rub": round(float(r["cost"]), 2),
                "units": round(float(r["units"]), 2),
                "calls": int(r["calls"]),
            }
            for r in ai_tasks
        ],
        "chats": [
            {
                "id": r["id"],
                "title": r["title"] or "",
                "project": r["project"] or "",
                "general": r["project"] is None,
                "archived": r["archived_at"] is not None,
                "messages": int(r["messages"]),
                "cost_rub": round(float(r["cost"]), 2),
                "created_at": r["created_at"].isoformat(),
                "last_at": r["last_at"].isoformat() if r["last_at"] else None,
            }
            for r in chats
        ],
        "project_list": [
            {
                "id": r["id"],
                "name": r["name"] or r["site"] or f"Проект {r['id']}",
                "site": r["site"] or "",
                "archived": r["archived_at"] is not None,
                "direct": r["direct"] or "",
                "campaigns": int(r["campaigns"]),
            }
            for r in projects
        ],
    }
