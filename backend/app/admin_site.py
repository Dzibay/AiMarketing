"""Сайт и воронка.

Сайт: визиты и посетители лендинга (site_visits), регистрации, откуда пришли, страницы входа, устройства;
отдельно — инструменты лендинга (проверка сайта, генераторы) и сколько проверок перенесли в кабинет.

Регистрация визита — пользователь, привязанный к визиту (site_visits.user_id), зарегистрированный в период.
Источник визита: ссылка (ref_code) → UTM → сайт-источник → прямой заход.

Воронка: люди, зарегистрированные в период, и сколько из них дошли до каждого шага. Шаги после регистрации
берутся из исходных таблиц (почта, подключения, проекты, платежи) и из user_events — что раньше.
Визиты — посетители лендинга за тот же период; это не те же люди, а вход воронки.
"""

from __future__ import annotations

from datetime import date

from app import admin_dashboard as dash
from app.clock import now_msk
from app.db import pool

TOP = 30
_TZ = "Europe/Moscow"

_SOURCE = """
CASE
    WHEN v.ref_code <> '' THEN 'ref'
    WHEN v.utm_source <> '' THEN 'utm'
    WHEN v.referrer_host <> '' THEN 'referrer'
    ELSE 'direct'
END
"""


def _per(p, date_from, date_to, step, now):
    now = now or now_msk()
    return dash.period(p, date_from, date_to, step, now), now


def _pub(per: dict) -> dict:
    return {**dash._public_range(per), "prev": dash._public_range(per["prev"])}


def _share(part, whole) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _site_numbers(conn, rng: dict) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS visits, COUNT(DISTINCT visitor_id) AS visitors,
               (SELECT COUNT(*) FROM users WHERE created_at >= %(s)s AND created_at < %(e)s) AS registrations,
               (SELECT COUNT(DISTINCT u.id) FROM users u JOIN site_visits v ON v.user_id = u.id
                 WHERE u.created_at >= %(s)s AND u.created_at < %(e)s) AS from_site
        FROM site_visits WHERE started_at >= %(s)s AND started_at < %(e)s
        """,
        {"s": rng["start"], "e": rng["end"]},
    ).fetchone()
    out = {k: int(row[k]) for k in ("visits", "visitors", "registrations", "from_site")}
    out["conversion"] = _share(out["from_site"], out["visitors"])
    return out


def _table(conn, rng: dict, key_sql: str, extra_select: str = "", extra_join: str = "") -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT {key_sql} AS key {extra_select}, COUNT(*) AS visits, COUNT(DISTINCT v.visitor_id) AS visitors,
               COUNT(DISTINCT u.id) AS registrations
        FROM site_visits v
        LEFT JOIN users u ON u.id = v.user_id AND u.created_at >= %(s)s AND u.created_at < %(e)s
        {extra_join}
        WHERE v.started_at >= %(s)s AND v.started_at < %(e)s
        GROUP BY 1 {', 2, 3' if extra_select else ''}
        ORDER BY visits DESC, key
        LIMIT %(top)s
        """,
        {"s": rng["start"], "e": rng["end"], "top": TOP},
    ).fetchall()
    out = []
    for r in rows:
        item = {k: r[k] for k in r.keys() if k not in ("visits", "visitors", "registrations")}
        item.update(visits=int(r["visits"]), visitors=int(r["visitors"]), registrations=int(r["registrations"]))
        item["conversion"] = _share(item["registrations"], item["visitors"])
        out.append(item)
    return out


def site(p: str = "30d", date_from=None, date_to=None, step: str = "", now=None) -> dict:
    per, now = _per(p, date_from, date_to, step, now)
    step = per["step"]
    with pool.connection() as conn:
        cur = _site_numbers(conn, per)
        prev = _site_numbers(conn, per["prev"]) if per["prev"] else None
        series_rows = {
            r["b"]: r
            for r in conn.execute(
                f"""
                SELECT b, SUM(visits) AS visits, SUM(visitors) AS visitors, SUM(regs) AS registrations FROM (
                    SELECT {dash._ts_bucket('started_at', step)} AS b, COUNT(*) AS visits,
                           COUNT(DISTINCT visitor_id) AS visitors, 0 AS regs
                    FROM site_visits WHERE started_at >= %(s)s AND started_at < %(e)s GROUP BY 1
                    UNION ALL
                    SELECT {dash._ts_bucket('created_at', step)}, 0, 0, COUNT(*)
                    FROM users WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
                ) x GROUP BY b
                """,
                {"s": per["start"], "e": per["end"]},
            ).fetchall()
        }
        kinds = _table(conn, per, _SOURCE.replace("\n", " "))
        refs = _table(
            conn,
            per,
            "COALESCE(NULLIF(l.name, ''), NULLIF(v.ref_code, ''))",
            ", l.code AS code, g.name AS group_name",
            "LEFT JOIN ref_links l ON l.code = v.ref_code LEFT JOIN ref_link_groups g ON g.id = l.group_id",
        )
        refs = [r for r in refs if r["key"]]
        utm = _table(
            conn,
            per,
            "NULLIF(v.utm_source, '')",
            ", v.utm_medium AS medium, v.utm_campaign AS campaign",
        )
        utm = [r for r in utm if r["key"]]
        referrers = _table(conn, per, "NULLIF(v.referrer_host, '')")
        referrers = [r for r in referrers if r["key"]]
        pages = _table(conn, per, "COALESCE(NULLIF(v.landing_page, ''), '/')")
        devices = _table(conn, per, "COALESCE(NULLIF(v.device, ''), 'неизвестно')")
    d1, d2 = date.fromisoformat(per["from"]), date.fromisoformat(per["to"])
    series = []
    for b in dash._buckets(d1, d2, step):
        r = series_rows.get(b) or {}
        series.append(
            {
                "day": b.isoformat(),
                "visits": int(r.get("visits") or 0),
                "visitors": int(r.get("visitors") or 0),
                "registrations": int(r.get("registrations") or 0),
            }
        )
    changes = {k: dash._change(cur[k], prev[k]) for k in ("visits", "visitors", "registrations")} if prev else {}
    if prev:
        changes["conversion"] = (
            round(cur["conversion"] - prev["conversion"], 1) if cur["conversion"] is not None and prev["conversion"] is not None else None
        )
    return {
        "period": _pub(per),
        "numbers": cur,
        "previous": prev,
        "changes": changes,
        "series": series,
        "sources": {"kinds": kinds, "refs": refs, "utm": utm, "referrers": referrers},
        "pages": pages,
        "devices": devices,
    }


TOOL_LABELS = {"check": "Проверка сайта", "minus": "Генератор минус-слов", "ads": "Генератор объявлений", "claimed": "Перенесли в кабинет"}


def tools(p: str = "30d", date_from=None, date_to=None, step: str = "", now=None) -> dict:
    """Инструменты лендинга по дням: проверки сайта, запуски генераторов и переносы проверки в кабинет."""
    per, now = _per(p, date_from, date_to, step, now)
    step = per["step"]
    params = {"s": per["start"], "e": per["end"]}
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {dash._ts_bucket('created_at', step)} AS b, 'check' AS tool, COUNT(*) AS n
            FROM site_checks WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
            UNION ALL
            SELECT {dash._ts_bucket('created_at', step)}, tool, COUNT(*)
            FROM site_tool_runs WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1, 2
            UNION ALL
            SELECT {dash._ts_bucket('created_at', step)}, 'claimed', COUNT(*)
            FROM site_check_claims WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
            """,
            params,
        ).fetchall()
        checks = conn.execute(
            """
            SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE status = 'done') AS done,
                   COUNT(*) FILTER (WHERE status = 'error') AS failed
            FROM site_checks WHERE created_at >= %(s)s AND created_at < %(e)s
            """,
            params,
        ).fetchone()
        claimed = conn.execute(
            "SELECT COUNT(*) AS n FROM site_check_claims WHERE created_at >= %(s)s AND created_at < %(e)s", params
        ).fetchone()["n"]
    labels = TOOL_LABELS
    keys: list[str] = []
    by: dict[date, dict] = {}
    totals: dict[str, int] = {}
    for r in rows:
        k = r["tool"]
        if k not in keys:
            keys.append(k)
        by.setdefault(r["b"], {})[k] = int(r["n"])
        totals[k] = totals.get(k, 0) + int(r["n"])
    order = ["check", *sorted(k for k in keys if k not in ("check", "claimed")), "claimed"]
    keys = [k for k in order if k in keys or k in ("check", "claimed")]
    d1, d2 = date.fromisoformat(per["from"]), date.fromisoformat(per["to"])
    return {
        "period": _pub(per),
        "tools": [{"key": k, "label": labels.get(k, k), "total": totals.get(k, 0)} for k in keys],
        "series": [{"day": b.isoformat(), **{k: (by.get(b) or {}).get(k, 0) for k in keys}} for b in dash._buckets(d1, d2, step)],
        "checks": {
            "total": int(checks["n"]),
            "done": int(checks["done"]),
            "failed": int(checks["failed"]),
            "claimed": int(claimed),
            "claim_share": _share(int(claimed), int(checks["done"])),
        },
    }


# --- Воронка -------------------------------------------------------------------------------------

STEPS = [
    ("visit", "Визит"),
    ("register", "Регистрация"),
    ("email", "Почта подтверждена"),
    ("yandex", "Подключён Яндекс"),
    ("project", "Создан проект"),
    ("question", "Вопрос ИИ"),
    ("action", "Выполнено предложение"),
    ("payment", "Оплата"),
]

_REACHED = """
SELECT u.id,
       u.created_at AS register,
       u.email_verified_at AS email,
       LEAST((SELECT MIN(c.created_at) FROM yandex_connections c WHERE c.user_id = u.id),
             (SELECT MIN(e.created_at) FROM user_events e WHERE e.user_id = u.id AND e.kind = 'yandex_connected')) AS yandex,
       LEAST((SELECT MIN(w.created_at) FROM workspaces w WHERE w.user_id = u.id),
             (SELECT MIN(e.created_at) FROM user_events e WHERE e.user_id = u.id AND e.kind = 'project_created')) AS project,
       LEAST((SELECT MIN(e.created_at) FROM user_events e WHERE e.user_id = u.id AND e.kind = 'ai_question'),
             (SELECT MIN(m.created_at) FROM ai_chats c JOIN ai_messages m ON m.chat_id = c.id
               WHERE c.user_id = u.id AND m.role = 'user')) AS question,
       LEAST((SELECT MIN(e.created_at) FROM user_events e WHERE e.user_id = u.id AND e.kind = 'action_applied'),
             (SELECT MIN(a.decided_at) FROM ai_actions a WHERE a.user_id = u.id AND a.status = 'done'
               AND a.tool <> 'board_edit')) AS action,
       (SELECT MIN(COALESCE(p.paid_at, p.created_at)) FROM payments p
         WHERE p.user_id = u.id AND p.status = 'succeeded' AND NOT p.test) AS payment
FROM users u
LEFT JOIN ref_links l ON l.id = u.ref_link_id
WHERE u.created_at >= %(s)s AND u.created_at < %(e)s {cond}
"""


def _source_cond(source: str) -> tuple[str, str, dict]:
    """Срез по источнику → условие на пользователя, условие на визит, параметры."""
    kind, _, value = (source or "").partition(":")
    if kind == "ref" and value.isdigit():
        return "AND u.ref_link_id = %(src)s", "AND v.ref_code = (SELECT code FROM ref_links WHERE id = %(src)s)", {"src": int(value)}
    if kind == "group" and value.isdigit():
        return (
            "AND l.group_id = %(src)s",
            "AND v.ref_code IN (SELECT code FROM ref_links WHERE group_id = %(src)s)",
            {"src": int(value)},
        )
    if kind == "utm" and value:
        return "AND u.first_utm->>'utm_source' = %(src)s", "AND v.utm_source = %(src)s", {"src": value}
    if kind == "direct":
        return (
            "AND u.ref_link_id IS NULL AND COALESCE(u.first_utm->>'utm_source', '') = '' AND COALESCE(u.first_referrer, '') = ''",
            "AND v.ref_code = '' AND v.utm_source = '' AND v.referrer_host = ''",
            {},
        )
    return "", "", {}


def _funnel(conn, rng: dict, source: str) -> dict:
    ucond, vcond, extra = _source_cond(source)
    params = {"s": rng["start"], "e": rng["end"], **extra}
    visitors = conn.execute(
        f"SELECT COUNT(DISTINCT v.visitor_id) AS n FROM site_visits v WHERE v.started_at >= %(s)s AND v.started_at < %(e)s {vcond}",
        params,
    ).fetchone()["n"]
    rows = conn.execute(_REACHED.format(cond=ucond), params).fetchall()
    keys = [k for k, _ in STEPS[1:]]
    counts = {k: sum(1 for r in rows if r[k] is not None) for k in keys}
    counts["visit"] = int(visitors)
    # Типичное время шага — медиана от предыдущего шага у тех, кто прошёл оба, в часах.
    medians: dict[str, float | None] = {"visit": None, "register": None}
    for prev_key, key in zip(keys, keys[1:]):
        gaps = sorted(
            (r[key] - r[prev_key]).total_seconds() / 3600
            for r in rows
            if r[key] is not None and r[prev_key] is not None and r[key] >= r[prev_key]
        )
        if gaps:
            mid = len(gaps) // 2
            medians[key] = round(gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2, 1)
        else:
            medians[key] = None
    return {"counts": counts, "medians": medians}


def _source_options(conn) -> list[dict]:
    groups = conn.execute("SELECT id, name FROM ref_link_groups ORDER BY position, id").fetchall()
    links = conn.execute(
        """
        SELECT l.id, l.name, l.code FROM ref_links l
        WHERE l.owner_user_id IS NULL AND EXISTS (SELECT 1 FROM users u WHERE u.ref_link_id = l.id)
        ORDER BY l.name, l.id
        """
    ).fetchall()
    utms = conn.execute(
        """
        SELECT first_utm->>'utm_source' AS s, COUNT(*) AS n FROM users
        WHERE COALESCE(first_utm->>'utm_source', '') <> '' GROUP BY 1 ORDER BY n DESC LIMIT 30
        """
    ).fetchall()
    out = [{"value": "", "label": "Все источники"}, {"value": "direct", "label": "Прямые заходы"}]
    out += [{"value": f"group:{g['id']}", "label": f"Группа «{g['name']}»"} for g in groups]
    out += [{"value": f"ref:{l['id']}", "label": f"Ссылка «{l['name'] or l['code']}»"} for l in links]
    out += [{"value": f"utm:{u['s']}", "label": f"UTM {u['s']}"} for u in utms]
    return out


def funnel(p: str = "30d", date_from=None, date_to=None, source: str = "", now=None) -> dict:
    per, now = _per(p, date_from, date_to, "", now)
    with pool.connection() as conn:
        cur = _funnel(conn, per, source)
        prev = _funnel(conn, per["prev"], source) if per["prev"] else None
        options = _source_options(conn)
    steps = []
    first = cur["counts"]["visit"] or None
    first_reg = cur["counts"]["register"] or None
    prev_count = None
    for key, label in STEPS:
        n = cur["counts"][key]
        steps.append(
            {
                "key": key,
                "label": label,
                "count": n,
                "of_prev": _share(n, prev_count) if prev_count is not None else None,
                "of_first": _share(n, first) if first else None,
                "of_registered": _share(n, first_reg) if key != "visit" and first_reg else None,
                "median_hours": cur["medians"].get(key),
                "prev_count": prev["counts"][key] if prev else None,
                "change": dash._change(n, prev["counts"][key]) if prev else None,
            }
        )
        prev_count = n
    return {"period": _pub(per), "source": source, "steps": steps, "options": options}
