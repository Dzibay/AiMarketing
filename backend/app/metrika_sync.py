"""Цели Метрики и отчёты."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.db import pool
from app.sync_runs import finish_run, start_run
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.metrika_sync")


def fetch_goals(access_token: str, counter_id: int) -> list[dict]:
    url = f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}/goals"
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers={"Authorization": f"OAuth {access_token}"})
    except httpx.HTTPError as exc:
        raise YandexError("metrika") from exc
    if response.status_code >= 400:
        log.warning("goals HTTP %s %s", response.status_code, response.text[:300])
        raise YandexError("metrika")
    goals = (response.json() or {}).get("goals") or []
    result = []
    for item in goals:
        gid = item.get("id")
        if gid is None:
            continue
        result.append(
            {
                "goal_id": int(gid),
                "name": str(item.get("name") or ""),
                "goal_type": str(item.get("type") or ""),
            }
        )
    return result


def upsert_goals(user_id: int, counter_row_id: int, goals: list[dict]) -> int:
    count = 0
    with pool.connection() as conn:
        for item in goals:
            conn.execute(
                """
                INSERT INTO metrika_goals (user_id, counter_row_id, goal_id, name, goal_type, synced_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (counter_row_id, goal_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    goal_type = EXCLUDED.goal_type,
                    synced_at = NOW()
                """,
                (
                    user_id,
                    counter_row_id,
                    item["goal_id"],
                    item.get("name") or "",
                    item.get("goal_type") or "",
                ),
            )
            count += 1
    return count


def sync_counter_goals(user_id: int, counter_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, connection_id, counter_id, enabled
            FROM metrika_counters
            WHERE id = %s AND user_id = %s
            """,
            (counter_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    run_id = start_run(user_id=user_id, kind="metrika_goals", target_id=counter_row_id)
    try:
        token = get_connection_access_token(row["connection_id"], user_id)
        goals = fetch_goals(token, int(row["counter_id"]))
        n = upsert_goals(user_id, counter_row_id, goals)
        finish_run(run_id, status="ok", meta={"goals": n})
        return {"goals": n, "status": "ok"}
    except YandexError as exc:
        finish_run(run_id, status="error", error=exc.code)
        raise


def list_goals(user_id: int, counter_row_id: int | None = None) -> list[dict]:
    clauses = ["g.user_id = %s"]
    params: list = [user_id]
    if counter_row_id is not None:
        clauses.append("g.counter_row_id = %s")
        params.append(counter_row_id)
    where = " AND ".join(clauses)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT g.id, g.counter_row_id, g.goal_id, g.name, g.goal_type, g.synced_at,
                   c.counter_id, c.name AS counter_name
            FROM metrika_goals g
            JOIN metrika_counters c ON c.id = g.counter_row_id
            WHERE {where}
            ORDER BY g.name ASC
            """,
            params,
        ).fetchall()
    return [
        {
            "id": row["id"],
            "counter_row_id": row["counter_row_id"],
            "goal_id": row["goal_id"],
            "name": row["name"],
            "goal_type": row["goal_type"],
            "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
            "counter_id": row["counter_id"],
            "counter_name": row["counter_name"],
        }
        for row in rows
    ]


def fetch_metrika_daily(access_token: str, counter_id: int, day: date) -> dict:
    """Визиты/пользователи/отказы за день."""
    url = "https://api-metrika.yandex.net/stat/v1/data"
    params = {
        "ids": counter_id,
        "metrics": "ym:s:visits,ym:s:users,ym:s:bounceRate,ym:s:goalReachesAny",
        "date1": day.isoformat(),
        "date2": day.isoformat(),
        "accuracy": "full",
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.get(
                url,
                params=params,
                headers={"Authorization": f"OAuth {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise YandexError("metrika") from exc
    if response.status_code >= 400:
        log.warning("metrika stat %s %s", response.status_code, response.text[:300])
        raise YandexError("metrika")
    data = response.json() or {}
    totals = data.get("totals") or [0, 0, 0, 0]
    while len(totals) < 4:
        totals.append(0)

    def _num(v) -> Decimal:
        try:
            return Decimal(str(v or 0))
        except Exception:
            return Decimal(0)

    return {
        "visits": int(_num(totals[0])),
        "users_count": int(_num(totals[1])),
        "bounce_rate": _num(totals[2]),
        "goal_reaches": int(_num(totals[3])),
    }


def fetch_metrika_goal_by_campaign(
    access_token: str,
    counter_id: int,
    goal_id: int,
    day: date,
) -> list[dict]:
    """Достижения цели в разрезе кампаний Директа (если Метрика их отдаёт)."""
    url = "https://api-metrika.yandex.net/stat/v1/data"
    params = {
        "ids": counter_id,
        "metrics": f"ym:s:goal{goal_id}reaches",
        "dimensions": "ym:s:lastSignDirectClickOrder",
        "date1": day.isoformat(),
        "date2": day.isoformat(),
        "accuracy": "full",
        "limit": 10000,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.get(
                url,
                params=params,
                headers={"Authorization": f"OAuth {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise YandexError("metrika") from exc
    if response.status_code >= 400:
        log.warning("metrika goal-by-campaign %s %s", response.status_code, response.text[:300])
        raise YandexError("metrika")
    data = response.json() or {}
    rows = []
    for item in data.get("data") or []:
        dims = item.get("dimensions") or []
        metrics = item.get("metrics") or [0]
        if not dims:
            continue
        raw_id = dims[0].get("name") if isinstance(dims[0], dict) else dims[0]
        try:
            campaign_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            continue
        if campaign_id <= 0:
            continue
        try:
            reaches = int(Decimal(str(metrics[0] or 0)))
        except Exception:
            reaches = 0
        rows.append({"campaign_id": campaign_id, "goal_reaches": reaches})
    return rows


def fetch_metrika_goal_total(access_token: str, counter_id: int, goal_id: int, day: date) -> int:
    url = "https://api-metrika.yandex.net/stat/v1/data"
    params = {
        "ids": counter_id,
        "metrics": f"ym:s:goal{goal_id}reaches",
        "date1": day.isoformat(),
        "date2": day.isoformat(),
        "accuracy": "full",
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.get(
                url,
                params=params,
                headers={"Authorization": f"OAuth {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise YandexError("metrika") from exc
    if response.status_code >= 400:
        log.warning("metrika goal-total %s %s", response.status_code, response.text[:300])
        raise YandexError("metrika")
    totals = (response.json() or {}).get("totals") or [0]
    try:
        return int(Decimal(str(totals[0] or 0)))
    except Exception:
        return 0


def _goals_to_sync(user_id: int, counter_row_id: int) -> list[dict]:
    """Основная цель пространства, цели из правил пользователя + до 20 целей счётчика."""
    with pool.connection() as conn:
        from_rules = conn.execute(
            """
            SELECT g.id, g.goal_id, g.counter_row_id
            FROM metrika_goals g
            JOIN workspaces w ON w.counter_row_id = g.counter_row_id AND w.goal_id = g.goal_id
            WHERE w.user_id = %s AND g.counter_row_id = %s
            UNION
            SELECT g.id, g.goal_id, g.counter_row_id
            FROM automation_rules r
            JOIN metrika_goals g ON g.id = r.goal_row_id
            WHERE r.user_id = %s AND g.counter_row_id = %s AND r.goal_row_id IS NOT NULL
            """,
            (user_id, counter_row_id, user_id, counter_row_id),
        ).fetchall()
        all_goals = conn.execute(
            """
            SELECT id, goal_id, counter_row_id
            FROM metrika_goals
            WHERE user_id = %s AND counter_row_id = %s
            ORDER BY id ASC
            LIMIT 20
            """,
            (user_id, counter_row_id),
        ).fetchall()
    seen = {int(r["id"]) for r in from_rules}
    merged = list(from_rules)
    for row in all_goals:
        if int(row["id"]) not in seen:
            merged.append(row)
            seen.add(int(row["id"]))
    return merged[:20]


def canonical_counters(user_id: int | None = None) -> list[dict]:
    """
    Включённые счётчики, по одной строке на номер счётчика у пользователя: один счётчик бывает
    виден из нескольких подключений — читаем его через строку, к которой привязано пространство.
    """
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT DISTINCT ON (m.user_id, m.counter_id)
                   m.id, m.user_id, m.connection_id, m.counter_id, m.loaded_at
            FROM metrika_counters m
            LEFT JOIN workspaces w ON w.counter_row_id = m.id
            WHERE m.enabled = TRUE AND (%s::bigint IS NULL OR m.user_id = %s)
            ORDER BY m.user_id, m.counter_id, (w.id IS NULL), m.id
            """,
            (user_id, user_id),
        ).fetchall()


def sync_metrika_stats_for_user(user_id: int, day: date | None = None) -> dict:
    day = day or (date.today() - timedelta(days=1))
    with pool.connection() as conn:
        # Один номер счётчика может быть виден из нескольких подключений — читаем его один раз,
        # через строку, к которой привязано пространство.
        counters = conn.execute(
            """
            SELECT DISTINCT ON (m.counter_id) m.id, m.connection_id, m.counter_id
            FROM metrika_counters m
            LEFT JOIN workspaces w ON w.counter_row_id = m.id
            WHERE m.user_id = %s AND m.enabled = TRUE
            ORDER BY m.counter_id, (w.id IS NULL), m.id
            """,
            (user_id,),
        ).fetchall()
    synced = 0
    errors = 0
    goals_synced = 0
    for row in counters:
        try:
            token = get_connection_access_token(row["connection_id"], user_id)
            stats = fetch_metrika_daily(token, int(row["counter_id"]), day)
            with pool.connection() as conn:
                conn.execute(
                    """
                    DELETE FROM stats_daily
                    WHERE user_id = %s AND day = %s AND source = 'metrika'
                      AND COALESCE(counter_row_id, 0) = %s
                    """,
                    (user_id, day, row["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO stats_daily (
                        user_id, day, source, counter_row_id,
                        visits, users_count, bounce_rate, goal_reaches, updated_at
                    )
                    VALUES (%s, %s, 'metrika', %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        user_id,
                        day,
                        row["id"],
                        stats["visits"],
                        stats["users_count"],
                        stats["bounce_rate"],
                        stats["goal_reaches"],
                    ),
                )
            for goal in _goals_to_sync(user_id, row["id"]):
                try:
                    by_camp = fetch_metrika_goal_by_campaign(
                        token, int(row["counter_id"]), int(goal["goal_id"]), day
                    )
                    total = fetch_metrika_goal_total(
                        token, int(row["counter_id"]), int(goal["goal_id"]), day
                    )
                    with pool.connection() as conn:
                        conn.execute(
                            """
                            DELETE FROM stats_daily
                            WHERE user_id = %s AND day = %s AND source = 'metrika_goal'
                              AND counter_row_id = %s AND goal_id = %s
                            """,
                            (user_id, day, row["id"], goal["goal_id"]),
                        )
                        conn.execute(
                            """
                            INSERT INTO stats_daily (
                                user_id, day, source, counter_row_id, goal_id,
                                goal_reaches, updated_at
                            )
                            VALUES (%s, %s, 'metrika_goal', %s, %s, %s, NOW())
                            """,
                            (user_id, day, row["id"], goal["goal_id"], total),
                        )
                        for item in by_camp:
                            conn.execute(
                                """
                                INSERT INTO stats_daily (
                                    user_id, day, source, counter_row_id, goal_id,
                                    campaign_id, goal_reaches, updated_at
                                )
                                VALUES (%s, %s, 'metrika_goal', %s, %s, %s, %s, NOW())
                                """,
                                (
                                    user_id,
                                    day,
                                    row["id"],
                                    goal["goal_id"],
                                    item["campaign_id"],
                                    item["goal_reaches"],
                                ),
                            )
                    goals_synced += 1
                except Exception:
                    log.exception("metrika goal stats %s", goal.get("goal_id"))
                    errors += 1
            synced += 1
        except Exception:
            log.exception("metrika stats counter %s", row["id"])
            errors += 1
    return {
        "day": day.isoformat(),
        "synced": synced,
        "goals_synced": goals_synced,
        "errors": errors,
    }


STAT_URL = "https://api-metrika.yandex.net/stat/v1/data"
STAT_PAGE = 100000
DAILY_METRICS = "ym:s:visits,ym:s:users,ym:s:bounceRate,ym:s:goalReachesAny"
# Метрика принимает до 20 метрик в запросе; по 10 целей — с запасом.
GOALS_PER_REQUEST = 10


def _stat_rows(access_token: str, counter_id: int, *, metrics: str, dimensions: str, date1: date, date2: date) -> list[dict]:
    """Все строки отчёта Метрики за период, постранично."""
    out: list[dict] = []
    offset = 1
    while True:
        params = {
            "ids": counter_id,
            "metrics": metrics,
            "dimensions": dimensions,
            "date1": date1.isoformat(),
            "date2": date2.isoformat(),
            "accuracy": "full",
            "limit": STAT_PAGE,
            "offset": offset,
        }
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.get(STAT_URL, params=params, headers={"Authorization": f"OAuth {access_token}"})
        except httpx.HTTPError as exc:
            raise YandexError("metrika") from exc
        if response.status_code >= 400:
            log.warning("metrika stat range %s %s", response.status_code, response.text[:300])
            raise YandexError("metrika")
        data = response.json() or {}
        rows = data.get("data") or []
        out.extend(rows)
        offset += len(rows)
        if not rows or offset > int(data.get("total_rows") or 0):
            return out


def _dim_value(dim):
    return dim.get("name") if isinstance(dim, dict) else dim


def _dim_day(dim) -> date | None:
    try:
        return date.fromisoformat(str(_dim_value(dim) or "")[:10])
    except ValueError:
        return None


def _dim_campaign(dim) -> int | None:
    try:
        campaign_id = int(str(_dim_value(dim)).strip())
    except (TypeError, ValueError):
        return None
    return campaign_id if campaign_id > 0 else None


def _metric(values: list, index: int) -> Decimal:
    try:
        return Decimal(str(values[index] or 0))
    except Exception:
        return Decimal(0)


def parse_daily_rows(rows: list[dict]) -> dict[date, dict]:
    """Строки отчёта DAILY_METRICS по ym:s:date → {день: визиты, пользователи, отказы, цели}."""
    out: dict[date, dict] = {}
    for item in rows:
        dims = item.get("dimensions") or []
        day = _dim_day(dims[0]) if dims else None
        if day is None:
            continue
        values = item.get("metrics") or []
        out[day] = {
            "visits": int(_metric(values, 0)),
            "users_count": int(_metric(values, 1)),
            "bounce_rate": _metric(values, 2),
            "goal_reaches": int(_metric(values, 3)),
        }
    return out


def parse_goal_rows(rows: list[dict], goal_ids: list[int]) -> dict[tuple, int]:
    """Достижения целей: ym:s:date → {(день, цель): n}; с кампанией → {(день, цель, кампания): n}."""
    out: dict[tuple, int] = {}
    for item in rows:
        dims = item.get("dimensions") or []
        day = _dim_day(dims[0]) if dims else None
        if day is None:
            continue
        campaign_id = _dim_campaign(dims[1]) if len(dims) > 1 else None
        if len(dims) > 1 and campaign_id is None:
            continue
        values = item.get("metrics") or []
        for index, goal_id in enumerate(goal_ids):
            reaches = int(_metric(values, index))
            if campaign_id is None:
                key: tuple = (day, goal_id)
            elif reaches:
                key = (day, goal_id, campaign_id)
            else:
                continue
            out[key] = out.get(key, 0) + reaches
    return out


def sync_counter_stats_range(user_id: int, counter_row_id: int, date_from: date, date_to: date) -> dict:
    """Визиты и цели счётчика за период — запросами за весь период, а не по дню."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, connection_id, counter_id FROM metrika_counters WHERE id = %s AND user_id = %s",
            (counter_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    token = get_connection_access_token(row["connection_id"], user_id)
    counter_id = int(row["counter_id"])
    daily = parse_daily_rows(
        _stat_rows(token, counter_id, metrics=DAILY_METRICS, dimensions="ym:s:date", date1=date_from, date2=date_to)
    )
    goal_ids = [int(g["goal_id"]) for g in _goals_to_sync(user_id, counter_row_id)]
    totals: dict[tuple, int] = {}
    by_campaign: dict[tuple, int] = {}
    for start in range(0, len(goal_ids), GOALS_PER_REQUEST):
        chunk = goal_ids[start : start + GOALS_PER_REQUEST]
        metrics = ",".join(f"ym:s:goal{goal_id}reaches" for goal_id in chunk)
        totals.update(
            parse_goal_rows(
                _stat_rows(token, counter_id, metrics=metrics, dimensions="ym:s:date", date1=date_from, date2=date_to),
                chunk,
            )
        )
        by_campaign.update(
            parse_goal_rows(
                _stat_rows(
                    token,
                    counter_id,
                    metrics=metrics,
                    dimensions="ym:s:date,ym:s:lastSignDirectClickOrder",
                    date1=date_from,
                    date2=date_to,
                ),
                chunk,
            )
        )
    days = [date_from + timedelta(days=n) for n in range((date_to - date_from).days + 1)]
    empty = {"visits": 0, "users_count": 0, "bounce_rate": Decimal(0), "goal_reaches": 0}
    with pool.connection() as conn:
        conn.execute(
            """
            DELETE FROM stats_daily
            WHERE user_id = %s AND source IN ('metrika', 'metrika_goal')
              AND counter_row_id = %s AND day BETWEEN %s AND %s
            """,
            (user_id, counter_row_id, date_from, date_to),
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO stats_daily (
                    user_id, day, source, counter_row_id,
                    visits, users_count, bounce_rate, goal_reaches, updated_at
                )
                VALUES (%s, %s, 'metrika', %s, %s, %s, %s, %s, NOW())
                """,
                [
                    (
                        user_id,
                        day,
                        counter_row_id,
                        daily.get(day, empty)["visits"],
                        daily.get(day, empty)["users_count"],
                        daily.get(day, empty)["bounce_rate"],
                        daily.get(day, empty)["goal_reaches"],
                    )
                    for day in days
                ],
            )
            # Итог цели за день пишем и нулём: так видно, что день загружен.
            goal_rows = [
                (user_id, day, counter_row_id, goal_id, None, totals.get((day, goal_id), 0))
                for day in days
                for goal_id in goal_ids
            ]
            goal_rows += [
                (user_id, day, counter_row_id, goal_id, campaign_id, reaches)
                for (day, goal_id, campaign_id), reaches in by_campaign.items()
                if date_from <= day <= date_to
            ]
            cur.executemany(
                """
                INSERT INTO stats_daily (
                    user_id, day, source, counter_row_id, goal_id,
                    campaign_id, goal_reaches, updated_at
                )
                VALUES (%s, %s, 'metrika_goal', %s, %s, %s, %s, NOW())
                """,
                goal_rows,
            )
    return {"days": len(days), "goals": len(goal_ids)}
