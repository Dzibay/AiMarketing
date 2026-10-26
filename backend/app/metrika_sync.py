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
    """Цели из правил пользователя + до 20 целей счётчика."""
    with pool.connection() as conn:
        from_rules = conn.execute(
            """
            SELECT DISTINCT g.id, g.goal_id, g.counter_row_id
            FROM automation_rules r
            JOIN metrika_goals g ON g.id = r.goal_row_id
            WHERE r.user_id = %s AND g.counter_row_id = %s AND r.goal_row_id IS NOT NULL
            """,
            (user_id, counter_row_id),
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


def sync_metrika_stats_for_user(user_id: int, day: date | None = None) -> dict:
    day = day or (date.today() - timedelta(days=1))
    with pool.connection() as conn:
        counters = conn.execute(
            """
            SELECT id, connection_id, counter_id
            FROM metrika_counters
            WHERE user_id = %s AND enabled = TRUE
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
