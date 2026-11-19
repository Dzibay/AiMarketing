"""Аналитика и сводка проекта: кампании проекта (Директ) + счётчик сайта (Метрика).

Считается из stats_daily — данные подтягивают задачи stats_day / stats_range. Конверсии берутся из
основной цели проекта (Метрика), если она задана и по ней есть данные, иначе — из Директа.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from app.journal import list_action_logs
from app.clock import today_msk
from app.db import pool
from app.overview import BALANCE_OUT_TITLE, SEVERITY_ORDER, build_overview, direct_account_url, headline, kpi_state
from app.stats import structure_tops
from app.strategy_info import budget_brief
from app.workspaces import list_workspaces, require_workspace
from app.yandex import YandexError

MAX_RANGE_DAYS = 366

_DIRECT_KEYS = ("cost_kopecks", "impressions", "clicks", "conversions", "revenue_kopecks")

# Статистику кабинета загрузчик перечитывает раз в 6 часов; отстал больше чем на 8 — что-то мешает
# (кончились баллы, ошибка доступа), и об этом нужно сказать, а не прятать за временем соседнего кабинета.
STATS_LAG = timedelta(hours=8)
# Окно, которое загрузчик перечитывает при плановом обновлении: неделя по вчера.
FRESH_WINDOW_DAYS = 7


def parse_range(date_from: str, date_to: str, *, default_days: int = 7) -> tuple[date, date]:
    """Период отчёта; по умолчанию — последние default_days полных дней."""
    yesterday = today_msk() - timedelta(days=1)
    try:
        d_to = date.fromisoformat(date_to) if date_to else yesterday
        d_from = date.fromisoformat(date_from) if date_from else d_to - timedelta(days=default_days - 1)
    except ValueError as exc:
        raise YandexError("date", "Неверная дата") from exc
    if d_from > d_to or (d_to - d_from).days + 1 > MAX_RANGE_DAYS:
        raise YandexError("date", "Период — от 1 до 366 дней")
    return d_from, d_to


def _metrics(s: dict, *, use_goal: bool) -> dict:
    cost = int(s.get("cost_kopecks") or 0)
    clicks = int(s.get("clicks") or 0)
    impressions = int(s.get("impressions") or 0)
    revenue = int(s.get("revenue_kopecks") or 0)
    direct_conv = int(s.get("conversions") or 0)
    goal_conv = int(s.get("goal_reaches") or 0)
    conv = goal_conv if use_goal else direct_conv
    return {
        "cost_kopecks": cost,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        "cpc_kopecks": round(cost / clicks) if clicks else None,
        "conversions": conv,
        "direct_conversions": direct_conv,
        "goal_reaches": goal_conv,
        "cr": round(conv / clicks * 100, 2) if clicks else None,
        "cpa_kopecks": round(cost / conv) if conv else None,
        "revenue_kopecks": revenue,
        "drr": round(cost / revenue * 100, 1) if revenue else None,
        "visits": int(s.get("visits") or 0),
        "users": int(s.get("users_count") or 0),
    }


def _delta(cur, prev) -> float | None:
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / prev * 100, 1)


def _ts(value) -> datetime | None:
    """Время из базы или из load_state (ISO-строка); без пояса — UTC."""
    if not value:
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_eta_minutes(job: dict | None, now: datetime) -> int | None:
    """Сколько примерно ждать загрузку: прошедшее время × оставшаяся доля. В очереди или без прогресса — не знаем."""
    if not job or job.get("status") != "running":
        return None
    done, total = int(job.get("done") or 0), int(job.get("total") or 0)
    started = _ts(job.get("started_at"))
    if started is None or done <= 0 or total <= 0 or done >= total:
        return None
    elapsed = max(0.0, (now - started).total_seconds() / 60)
    return max(1, math.ceil(elapsed * (total - done) / done))


def data_freshness(accounts: list[dict], metrika_at, now: datetime) -> dict:
    """
    Насколько свежи цифры отчёта — честно, по каждому кабинету.

    accounts — включённые кабинеты проекта: id, name, login, loaded_at, stage_at (когда загрузчик последний раз
    перечитал статистику), rows_at (когда последний раз записаны прошедшие дни — в том числе ручной загрузкой),
    job ({status, done, total, started_at} — идущая загрузка или None).

    Время Директа — по самому старому кабинету: пока один отстал, суммы за период неполные. Расход за сегодня
    (обновляется каждый час) сюда не входит: отчёты заканчиваются вчерашним днём.
    loading — первая загрузка ещё не принесла статистику; lagging — кабинет отстал больше чем на STATS_LAG.
    """
    items = []
    for a in accounts:
        times = [t for t in (_ts(a.get("stage_at")), _ts(a.get("rows_at"))) if t]
        stats_at = max(times) if times else None
        job = a.get("job")
        loading = a.get("loaded_at") is None and stats_at is None
        items.append(
            {
                "id": a["id"],
                "name": a.get("name") or a.get("login") or "",
                "login": a.get("login") or "",
                "stats_at": stats_at.isoformat() if stats_at else None,
                "loading": loading,
                "updating": job is not None,
                "lagging": not loading and (stats_at is None or now - stats_at > STATS_LAG),
                "eta_minutes": load_eta_minutes(job, now) if loading else None,
            }
        )
    times = [_ts(i["stats_at"]) for i in items if i["stats_at"]]
    loading = [i for i in items if i["loading"]]
    etas = [i["eta_minutes"] for i in loading]
    metrika = _ts(metrika_at)
    return {
        "direct_at": min(times).isoformat() if times else None,
        "metrika_at": metrika.isoformat() if metrika else None,
        "loading": bool(loading),
        # Ждать столько, сколько самому долгому; хоть один в очереди — срок неизвестен.
        "eta_minutes": max(etas) if etas and None not in etas else None,
        "accounts": items,
    }


def _freshness_accounts(user_id: int, account_ids: list[int], workspace_id: int | None) -> list[dict]:
    """Кабинеты для строки свежести: указанные и (если задан проект) привязанные к проекту, только включённые."""
    today = today_msk()
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, COALESCE(NULLIF(a.name, ''), a.client_login) AS name, a.client_login AS login,
                   a.loaded_at, a.load_state->'stats'->>'at' AS stage_at,
                   (SELECT MAX(s.updated_at) FROM stats_daily s
                     WHERE s.source = 'direct' AND s.account_id = a.id
                       AND s.day >= %(week)s AND s.day < %(today)s) AS rows_at,
                   j.status AS job_status, j.progress_done AS job_done, j.progress_total AS job_total,
                   j.started_at AS job_started_at
            FROM direct_accounts a
            LEFT JOIN LATERAL (
                SELECT status, progress_done, progress_total, started_at FROM jobs
                WHERE user_id = a.user_id AND kind = 'load_account' AND status IN ('queued', 'running')
                  AND payload->>'account_id' = a.id::text
                ORDER BY id LIMIT 1
            ) j ON TRUE
            WHERE a.user_id = %(user)s AND a.enabled
              AND (a.id = ANY(%(ids)s) OR a.id IN (
                    SELECT account_id FROM workspace_accounts WHERE workspace_id = %(ws)s AND user_id = %(user)s))
            ORDER BY lower(COALESCE(NULLIF(a.name, ''), a.client_login)), a.id
            """,
            {
                "user": user_id,
                "ids": account_ids,
                "ws": workspace_id or 0,
                "today": today,
                "week": today - timedelta(days=FRESH_WINDOW_DAYS),
            },
        ).fetchall()
    return [
        {
            **{k: r[k] for k in ("id", "name", "login", "loaded_at", "stage_at", "rows_at")},
            "job": {
                "status": r["job_status"],
                "done": r["job_done"],
                "total": r["job_total"],
                "started_at": r["job_started_at"],
            }
            if r["job_status"]
            else None,
        }
        for r in rows
    ]


def merge_freshness(items: list[dict]) -> dict:
    """Свежесть нескольких проектов разом (отчёт «Все проекты»): кабинет — один раз, время — по самому старому."""
    accounts: dict[int, dict] = {}
    for f in items:
        for a in f["accounts"]:
            accounts.setdefault(a["id"], a)
    direct = [f["direct_at"] for f in items if f["direct_at"]]
    metrika = [f["metrika_at"] for f in items if f["metrika_at"]]
    loading = [a for a in accounts.values() if a["loading"]]
    etas = [a["eta_minutes"] for a in loading]
    return {
        "direct_at": min(direct, key=_ts) if direct else None,
        "metrika_at": min(metrika, key=_ts) if metrika else None,
        "loading": bool(loading),
        "eta_minutes": max(etas) if etas and None not in etas else None,
        "accounts": sorted(accounts.values(), key=lambda a: (a["name"].lower(), a["id"])),
    }


def _workspace_campaigns(user_id: int, workspace_id: int) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.state, c.status, c.campaign_type,
                   c.daily_budget_kopecks, c.bidding_strategy, c.start_date, c.status_clarification,
                   a.client_login, a.name AS account_name, a.balance_kopecks,
                   (
                     SELECT COUNT(*) FROM direct_ads ad
                     JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
                     WHERE g.campaign_row_id = c.id AND ad.state <> 'ARCHIVED'
                       AND (ad.status = 'REJECTED' OR ad.moderation <> '[]'::jsonb)
                   ) AS ads_rejected
            FROM workspace_campaigns wc
            JOIN direct_campaigns c ON c.id = wc.campaign_row_id
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE wc.workspace_id = %s AND wc.user_id = %s
            ORDER BY c.name, c.id
            """,
            (workspace_id, user_id),
        ).fetchall()


def _load_stats(user_id: int, ws: dict, date_from: date, date_to: date) -> dict:
    with pool.connection() as conn:
        direct = conn.execute(
            """
            SELECT s.day, c.id AS campaign_row_id, s.cost_kopecks, s.impressions, s.clicks,
                   s.conversions, s.revenue_kopecks
            FROM stats_daily s
            JOIN direct_campaigns c ON c.account_id = s.account_id AND c.campaign_id = s.campaign_id
            JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
            WHERE s.user_id = %s AND s.source = 'direct' AND wc.workspace_id = %s
              AND s.day >= %s AND s.day <= %s
            """,
            (user_id, ws["id"], date_from, date_to),
        ).fetchall()
        metrika = []
        goal = []
        if ws["counter_row_id"]:
            metrika = conn.execute(
                """
                SELECT day, visits, users_count, bounce_rate, goal_reaches
                FROM stats_daily
                WHERE user_id = %s AND source = 'metrika' AND counter_row_id = %s
                  AND day >= %s AND day <= %s
                """,
                (user_id, ws["counter_row_id"], date_from, date_to),
            ).fetchall()
            if ws["goal_id"]:
                goal = conn.execute(
                    """
                    SELECT day, campaign_id, goal_reaches
                    FROM stats_daily
                    WHERE user_id = %s AND source = 'metrika_goal' AND counter_row_id = %s
                      AND goal_id = %s AND day >= %s AND day <= %s
                    """,
                    (user_id, ws["counter_row_id"], ws["goal_id"], date_from, date_to),
                ).fetchall()
        # Свежесть Директа считается по кабинетам (data_freshness), здесь — только Метрика.
        fresh = conn.execute(
            """
            SELECT MAX(updated_at) AS metrika_at FROM stats_daily
            WHERE user_id = %s AND source = 'metrika' AND counter_row_id = %s
            """,
            (user_id, ws["counter_row_id"]),
        ).fetchone()
    return {"direct": direct, "metrika": metrika, "goal": goal, "fresh": fresh or {}}


def workspace_analytics(
    user_id: int,
    workspace_id: int,
    *,
    date_from: date,
    date_to: date,
    with_tops: bool = True,
    campaign_row_id: int | None = None,
) -> dict:
    """campaign_row_id — отчёт по одной кампании проекта: только её Директ и её переходы по цели,
    без визитов сайта (Метрика считает их на весь сайт)."""
    ws = require_workspace(user_id, workspace_id)
    days = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    campaigns = _workspace_campaigns(user_id, workspace_id)
    data = _load_stats(user_id, ws, prev_from, date_to)
    # Источник заявок — как у всего проекта, даже если у этой кампании переходов по цели не было.
    goal_any = bool(data["goal"])
    if campaign_row_id:
        campaigns = [c for c in campaigns if int(c["id"]) == int(campaign_row_id)]
        if not campaigns:
            raise YandexError("scope", "Кампания не входит в этот проект")
        data["direct"] = [r for r in data["direct"] if int(r["campaign_row_id"]) == int(campaign_row_id)]
        data["metrika"] = []
        only = int(campaigns[0]["campaign_id"])
        data["goal"] = [r for r in data["goal"] if r["campaign_id"] is not None and int(r["campaign_id"]) == only]
        with_tops = False

    by_campaign_id = {int(c["campaign_id"]): int(c["id"]) for c in campaigns}
    cur: dict = defaultdict(int)
    prev: dict = defaultdict(int)
    day_sums: dict[date, dict] = defaultdict(lambda: defaultdict(int))
    camp_sums: dict[int, dict] = defaultdict(lambda: defaultdict(int))

    for r in data["direct"]:
        target = cur if r["day"] >= date_from else prev
        for key in _DIRECT_KEYS:
            target[key] += int(r[key] or 0)
        for key in _DIRECT_KEYS:
            day_sums[r["day"]][key] += int(r[key] or 0)
        if r["day"] >= date_from:
            for key in _DIRECT_KEYS:
                camp_sums[int(r["campaign_row_id"])][key] += int(r[key] or 0)

    bounce_num = 0.0
    for r in data["metrika"]:
        target = cur if r["day"] >= date_from else prev
        target["visits"] += int(r["visits"] or 0)
        target["users_count"] += int(r["users_count"] or 0)
        day_sums[r["day"]]["visits"] += int(r["visits"] or 0)
        if r["day"] >= date_from:
            if r["bounce_rate"] is not None:
                bounce_num += float(r["bounce_rate"]) * int(r["visits"] or 0)

    # Цель проекта: итог по сайту (campaign_id NULL) и по кампаниям Директа.
    goal_rows = 0
    site_goal: dict = defaultdict(int)
    for r in data["goal"]:
        goal_rows += 1
        reaches = int(r["goal_reaches"] or 0)
        if r["campaign_id"] is None:
            site_goal["cur" if r["day"] >= date_from else "prev"] += reaches
            continue
        row_id = by_campaign_id.get(int(r["campaign_id"]))
        if row_id is None:
            continue  # переходы из кампаний вне проекта
        target = cur if r["day"] >= date_from else prev
        target["goal_reaches"] += reaches
        day_sums[r["day"]]["goal_reaches"] += reaches
        if r["day"] >= date_from:
            camp_sums[row_id]["goal_reaches"] += reaches
    use_goal = bool(ws["goal_id"]) and (goal_rows > 0 or goal_any)

    totals = _metrics(cur, use_goal=use_goal)
    totals["bounce_rate"] = round(bounce_num / totals["visits"], 1) if totals["visits"] else None
    totals["site_goal_reaches"] = site_goal["cur"]
    previous = _metrics(prev, use_goal=use_goal)
    previous["site_goal_reaches"] = site_goal["prev"]
    compare = {
        key: _delta(totals[key], previous[key])
        for key in (
            "cost_kopecks",
            "clicks",
            "impressions",
            "ctr",
            "cpc_kopecks",
            "conversions",
            "cpa_kopecks",
            "revenue_kopecks",
            "drr",
            "visits",
        )
    }

    def days_series(start: date, end: date) -> list[dict]:
        out = []
        d = start
        while d <= end:
            m = _metrics(day_sums.get(d, {}), use_goal=use_goal)
            out.append(
                {
                    "day": d.isoformat(),
                    "cost_kopecks": m["cost_kopecks"],
                    "clicks": m["clicks"],
                    "impressions": m["impressions"],
                    "conversions": m["conversions"],
                    "cpa_kopecks": m["cpa_kopecks"],
                    "revenue_kopecks": m["revenue_kopecks"],
                    "visits": m["visits"],
                }
            )
            d += timedelta(days=1)
        return out

    by_day = days_series(date_from, date_to)
    # Тот же ряд за предыдущий период той же длины — пунктир «было» на графиках.
    prev_by_day = days_series(prev_from, prev_to)

    by_campaign = []
    accounts: dict[int, dict] = {}
    acc_sums: dict[int, dict] = defaultdict(lambda: defaultdict(int))
    for c in campaigns:
        s = camp_sums.get(int(c["id"]), {})
        m = _metrics(s, use_goal=use_goal)
        budget = c.get("daily_budget_kopecks")
        by_campaign.append(
            {
                "campaign_row_id": c["id"],
                "campaign_id": c["campaign_id"],
                "name": c["name"],
                "state": c["state"],
                "status": c["status"],
                "type": c.get("campaign_type") or "",
                "account_id": c["account_id"],
                "account_login": c["client_login"],
                "daily_budget_rub": round(budget / 100, 2) if budget else None,
                # Почему кампания не показывается — словами (campaignHealth): дата старта, отказ модерации,
                # деньги кабинета и пояснение Директа.
                "start_date": str(c["start_date"]) if c.get("start_date") else None,
                "status_clarification": c.get("status_clarification") or "",
                "ads_rejected": int(c.get("ads_rejected") or 0),
                "balance_kopecks": c.get("balance_kopecks"),
                "money_url": direct_account_url(c["client_login"]),
                **budget_brief(c.get("bidding_strategy")),
                **m,
            }
        )
        accounts.setdefault(
            int(c["account_id"]),
            {"account_id": c["account_id"], "login": c["client_login"], "name": c["account_name"] or ""},
        )
        for key, value in s.items():
            acc_sums[int(c["account_id"])][key] += value
    by_campaign.sort(key=lambda r: (-r["cost_kopecks"], r["name"]))
    by_account = []
    for acc_id, meta in accounts.items():
        m = _metrics(acc_sums.get(acc_id, {}), use_goal=use_goal)
        by_account.append({**meta, "campaigns": sum(1 for c in campaigns if int(c["account_id"]) == acc_id), **m})
    by_account.sort(key=lambda r: -r["cost_kopecks"])

    # KPI проекта, иначе — клиента-папки.
    kpi_type, kpi_target = ws["kpi_type"], ws["kpi_target"]
    if not kpi_type:
        kpi_type, kpi_target = ws.get("client_kpi_type") or "", ws.get("client_kpi_target")
    kpi = None
    if kpi_type:
        kpi = kpi_state(
            kpi_type,
            float(kpi_target) if kpi_target is not None else None,
            totals["cost_kopecks"],
            totals["conversions"],
            totals["revenue_kopecks"],
        )

    fresh = data["fresh"]
    linked = None if campaign_row_id else workspace_id
    freshness = data_freshness(
        _freshness_accounts(user_id, sorted({int(c["account_id"]) for c in campaigns}), linked),
        fresh.get("metrika_at") if not campaign_row_id else None,
        datetime.now(timezone.utc),
    )
    out = {
        "workspace_id": workspace_id,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "prev_from": prev_from.isoformat(),
        "prev_to": prev_to.isoformat(),
        "conversions_source": "goal" if use_goal else "direct",
        "goal_id": ws["goal_id"],
        "goal_name": ws.get("goal_name") or "",
        "counter_ok": bool(ws["counter_row_id"]) and bool(ws.get("counter_enabled")),
        "totals": totals,
        "previous": previous,
        "compare": compare,
        "kpi": kpi,
        "by_day": by_day,
        "prev_by_day": prev_by_day,
        "by_campaign": by_campaign,
        "by_account": by_account,
        # Директ — по самому старому кабинету проекта (подробности и отстающие — в freshness).
        "direct_updated_at": freshness["direct_at"],
        "metrika_updated_at": fresh["metrika_at"].isoformat() if fresh.get("metrika_at") else None,
        "freshness": freshness,
    }
    if with_tops:
        ids = [int(c["id"]) for c in campaigns]
        out["tops"] = structure_tops(user_id, campaign_row_ids=ids) if ids else {
            "keywords": [],
            "adgroups": [],
            "queries": [],
            "note": "",
        }
    return out


_SUM_KEYS = ("cost_kopecks", "impressions", "clicks", "conversions", "revenue_kopecks", "visits")
_COMPARE_KEYS = ("cost_kopecks", "clicks", "impressions", "ctr", "cpc_kopecks", "conversions", "cpa_kopecks",
                 "revenue_kopecks", "drr", "visits")


def _combined(rows: list[dict]) -> dict:
    """Сумма метрик нескольких проектов: счётчики складываются, доли и цены считаются заново из сумм."""
    s = {key: sum(int(r.get(key) or 0) for r in rows) for key in _SUM_KEYS}
    cost, clicks, impressions = s["cost_kopecks"], s["clicks"], s["impressions"]
    conv, revenue = s["conversions"], s["revenue_kopecks"]
    return {
        **s,
        "ctr": round(clicks / impressions * 100, 2) if impressions else None,
        "cpc_kopecks": round(cost / clicks) if clicks else None,
        "cr": round(conv / clicks * 100, 2) if clicks else None,
        "cpa_kopecks": round(cost / conv) if conv else None,
        "drr": round(cost / revenue * 100, 1) if revenue else None,
    }


def portfolio_analytics(user_id: int, *, date_from: date, date_to: date) -> dict:
    """Аналитика и кампании при «Все проекты»: сумма проектов с динамикой, строка на проект и все кампании
    с их проектом. Каждый проект считает конверсии по своей цели (или по Директу) — в сумме они складываются
    как есть. Проекты в архиве и сверх лимита тарифа не входят."""
    days = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)
    projects: list[dict] = []
    campaigns: list[dict] = []
    cur_days: dict[str, list[dict]] = defaultdict(list)
    prev_days: dict[str, list[dict]] = defaultdict(list)
    freshness: list[dict] = []
    for ws in list_workspaces(user_id):
        if ws["locked"]:
            continue
        data = workspace_analytics(user_id, ws["id"], date_from=date_from, date_to=date_to, with_tops=False)
        project = {"id": ws["id"], "name": ws["name"]}
        projects.append(
            {
                **project,
                "site": ws["site"],
                "client_name": ws["client_name"],
                "conversions_source": data["conversions_source"],
                "goal_name": data["goal_name"],
                "kpi": data["kpi"],
                "totals": data["totals"],
                "compare": data["compare"],
                "campaigns": len(data["by_campaign"]),
                "spark": [d["cost_kopecks"] for d in data["by_day"]],
                "counter_ok": data["counter_ok"],
                "metrika_updated_at": data["metrika_updated_at"],
            }
        )
        freshness.append(data["freshness"])
        campaigns.extend({**c, "project": project} for c in data["by_campaign"])
        for d in data["by_day"]:
            cur_days[d["day"]].append(d)
        for d in data["prev_by_day"]:
            prev_days[d["day"]].append(d)

    def series(start: date, rows: dict[str, list[dict]]) -> list[dict]:
        out = []
        for i in range(days):
            day = (start + timedelta(days=i)).isoformat()
            out.append({"day": day, **_combined(rows.get(day, []))})
        return out

    totals = _combined([p["totals"] for p in projects])
    # Отказы — среднее по визитам проектов, где Метрика их отдала.
    bounce_visits = [(p["totals"]["bounce_rate"], p["totals"]["visits"]) for p in projects
                     if p["totals"].get("bounce_rate") is not None and p["totals"]["visits"]]
    weight = sum(v for _, v in bounce_visits)
    totals["bounce_rate"] = round(sum(b * v for b, v in bounce_visits) / weight, 1) if weight else None
    previous = _combined([r for rows in prev_days.values() for r in rows])
    metrika_at = [p["metrika_updated_at"] for p in projects if p["metrika_updated_at"]]
    projects.sort(key=lambda p: (-p["totals"]["cost_kopecks"], p["name"].lower()))
    campaigns.sort(key=lambda c: (-c["cost_kopecks"], c["name"]))
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "prev_from": prev_from.isoformat(),
        "prev_to": prev_to.isoformat(),
        "totals": totals,
        "previous": previous,
        "compare": {key: _delta(totals[key], previous[key]) for key in _COMPARE_KEYS},
        "by_day": series(date_from, cur_days),
        "prev_by_day": series(prev_from, prev_days),
        # Метрика есть хотя бы у одного проекта — иначе визиты в сумме всегда 0 и плитку не показываем.
        "counter_ok": any(p["counter_ok"] for p in projects),
        "metrika_updated_at": max(metrika_at) if metrika_at else None,
        "freshness": merge_freshness(freshness),
        "projects": projects,
        "campaigns": campaigns,
    }


def workspace_summary(user_id: int, workspace_id: int) -> dict:
    """Главная вкладка проекта: строка сводки, проблемы, неделя к неделе, последние действия."""
    require_workspace(user_id, workspace_id)
    overview = build_overview(user_id)
    row = next((r for r in overview["workspaces"] if r["id"] == workspace_id), None)
    problems = [
        p
        for p in overview["problems"]
        if p.get("workspace_id") == workspace_id or workspace_id in (p.get("workspace_ids") or [])
    ]
    problems.sort(key=lambda p: (SEVERITY_ORDER.get(p["severity"], 9), p.get("object_name") or ""))
    yesterday = date.fromisoformat(overview["today"]) - timedelta(days=1)
    week = workspace_analytics(
        user_id, workspace_id, date_from=yesterday - timedelta(days=6), date_to=yesterday, with_tops=False
    )
    return {
        "today": overview["today"],
        "today_updated_at": overview.get("today_updated_at"),
        "row": row,
        "problems": problems,
        # Скрытые сигналы проекта — строка «Скрыто: N» под списком.
        "hidden": [h for h in overview.get("hidden") or [] if workspace_id in h["workspace_ids"]],
        "week": {
            "date_from": week["date_from"],
            "date_to": week["date_to"],
            "totals": week["totals"],
            "previous": week["previous"],
            "compare": week["compare"],
            "kpi": week["kpi"],
            "by_day": week["by_day"],
            "prev_by_day": week["prev_by_day"],
            "conversions_source": week["conversions_source"],
            "goal_name": week["goal_name"],
            "counter_ok": week["counter_ok"],
            "metrika_updated_at": week["metrika_updated_at"],
            "direct_updated_at": week["direct_updated_at"],
            "freshness": week["freshness"],
        },
        # Деньги на счетах логинов проекта — строка «На счёте … хватит примерно на …».
        "balances": [b for b in overview["balances"] if workspace_id in b["workspace_ids"]],
        "top_campaigns": week["by_campaign"][:5],
        "recent_actions": list_action_logs(user_id, 10, workspace_id=workspace_id),
    }


# --- Сводка «как дела»: строка и три числа первым блоком главной и главной проекта -------------------

HEADLINE_PERIODS = (7, 30)


def _period(today: str, days: int) -> tuple[date, date]:
    date_to = date.fromisoformat(today) - timedelta(days=1)
    return date_to - timedelta(days=days - 1), date_to


def _campaign_link(p: dict) -> str:
    ws = p.get("workspace_id")
    return f"/w/{ws}/campaigns/{p['campaign_row_id']}" if ws else "/cabinet/campaigns"


def _campaign_problems(problems: list[dict], kind: str, severity: str | None = None) -> list[dict]:
    return [
        {"name": p["object_name"], "to": _campaign_link(p)}
        for p in problems
        if p["kind"] == kind and p.get("campaign_row_id") and (severity is None or p["severity"] == severity)
    ]


def _worst_campaign(campaigns: list[dict], workspace_id: int) -> dict | None:
    """Самая дорогая заявка среди кампаний с расходом; без заявок «цена» — весь расход. Одна кампания — не с чем сравнить."""
    spent = [c for c in campaigns if c["cost_kopecks"] > 0]
    if len(spent) < 2:
        return None
    worst = max(spent, key=lambda c: c["cpa_kopecks"] if c["conversions"] else c["cost_kopecks"])
    return {"name": worst["name"], "to": f"/w/{workspace_id}/campaigns/{worst['campaign_row_id']}"}


def _money_url(problems: list[dict]) -> str:
    """Кабинет, который пополнять первым: где деньги уже кончились, иначе — где их меньше всего дней."""
    money = [p for p in problems if p["kind"] == "balance" and p.get("direct_url")]
    if not money:
        return ""
    first = min(
        money,
        key=lambda p: (p["title"] != BALANCE_OUT_TITLE, p["days_on_balance"] if p.get("days_on_balance") is not None else 0),
    )
    return first["direct_url"]


def _shortest_money(problems: list[dict]) -> dict | None:
    """Видимая проблема «деньги» с самым коротким запасом дней. Скрытую («⋯», snoozes) строка «как дела»
    не повторяет: деньги в ней — только из проблем сводки, как money_out и money_url."""
    money = [p for p in problems if p["kind"] == "balance" and p.get("days_on_balance") is not None]
    return min(money, key=lambda p: p["days_on_balance"]) if money else None


def _base_facts(days: int, problems: list[dict]) -> dict:
    return {
        "days": days,
        "token_lost": any(p["kind"] == "token" and p["severity"] == "critical" for p in problems),
        "money_out": any(p["kind"] == "balance" and p["title"] == BALANCE_OUT_TITLE for p in problems),
        "money_url": _money_url(problems),
        "rejected": _campaign_problems(problems, "moderation", "critical"),
        "stopped": _campaign_problems(problems, "stopped"),
    }


def _numbers(days: int, date_from: date, date_to: date, totals: dict, previous: dict, compare: dict) -> dict:
    return {
        "period": days,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "cost": totals["cost_kopecks"],
        "conversions": totals["conversions"],
        "cpa": totals["cpa_kopecks"],
        "drr": totals["drr"],
        "prev_cost": previous["cost_kopecks"],
        "prev_conversions": previous["conversions"],
        "change": {
            "cost": compare["cost_kopecks"],
            "conversions": compare["conversions"],
            "cpa": compare["cpa_kopecks"],
            "drr": compare["drr"],
        },
    }


def workspace_headline(user_id: int, workspace_id: int, days: int = 7) -> dict:
    """Строка «как дела» и три числа по проекту за 7 или 30 дней."""
    require_workspace(user_id, workspace_id)
    days = days if days in HEADLINE_PERIODS else HEADLINE_PERIODS[0]
    overview = build_overview(user_id)
    row = next((r for r in overview["workspaces"] if r["id"] == workspace_id), None) or {}
    problems = [
        p
        for p in overview["problems"]
        if p.get("workspace_id") == workspace_id or workspace_id in (p.get("workspace_ids") or [])
    ]
    date_from, date_to = _period(overview["today"], days)
    a = workspace_analytics(user_id, workspace_id, date_from=date_from, date_to=date_to, with_tops=False)
    kpi = a["kpi"] if a["kpi"] and a["kpi"]["status"] != "none" else None
    base = f"/w/{workspace_id}"
    numbers = _numbers(days, date_from, date_to, a["totals"], a["previous"], a["compare"])
    money = _shortest_money(problems)
    facts = {
        **_base_facts(days, problems),
        "accounts": row.get("accounts", 0),
        "stats_loaded": bool(row.get("stats_synced_at") or a["direct_updated_at"]),
        "money_days": money["days_on_balance"] if money else None,
        "kpi": kpi,
        "project": "",
        "cost": numbers["cost"],
        "conversions": numbers["conversions"],
        "prev_cost": numbers["prev_cost"],
        "prev_conversions": numbers["prev_conversions"],
        "worst": _worst_campaign(a["by_campaign"], workspace_id),
        "all_in_goal": False,
        "goal_link": f"{base}/settings",
        "chat": f"{base}/chat",
        "campaigns": f"{base}/campaigns",
    }
    return {"headline": headline(facts), "numbers": numbers, "goal": kpi, "goal_link": f"{base}/settings"}


def home_headline(user_id: int, days: int = 7) -> dict:
    """Строка «как дела» и три числа по всем проектам. Проект один — как на его главной: с его целью."""
    days = days if days in HEADLINE_PERIODS else HEADLINE_PERIODS[0]
    overview = build_overview(user_id)
    rows = overview["workspaces"]
    date_from, date_to = _period(overview["today"], days)
    p = portfolio_analytics(user_id, date_from=date_from, date_to=date_to)
    projects = p["projects"]
    numbers = _numbers(days, date_from, date_to, p["totals"], p["previous"], p["compare"])

    def goal_of(pr: dict) -> dict | None:
        return pr["kpi"] if pr["kpi"] and pr["kpi"]["status"] != "none" else None

    with_goal = [pr for pr in projects if goal_of(pr)]
    over = [pr for pr in with_goal if pr["kpi"]["status"] in ("warn", "bad")]
    single = projects[0] if len(projects) == 1 else None
    focus = single or (max(over, key=lambda pr: pr["kpi"]["ratio"] or 0) if over else None)
    kpi = goal_of(focus) if focus else None
    worst = None
    if focus and kpi:
        own = [c for c in p["campaigns"] if c["project"]["id"] == focus["id"]]
        worst = _worst_campaign(own, focus["id"])
    shortest = _shortest_money(overview["problems"])
    money_project = ""
    if shortest and len(rows) > 1:
        # Логин нескольких проектов — называем первый из них, как в списке проектов.
        names = [r["name"] for r in rows if r["id"] in (shortest.get("workspace_ids") or [])]
        money_project = names[0] if names else shortest.get("workspace_name") or ""
    goal_link = f"/w/{single['id']}/settings" if single else None
    facts = {
        **_base_facts(days, overview["problems"]),
        "accounts": sum(r["accounts"] for r in rows),
        "stats_loaded": any(r["stats_synced_at"] for r in rows if r["accounts"]) or numbers["cost"] > 0,
        "money_days": shortest["days_on_balance"] if shortest else None,
        "money_project": money_project,
        "kpi": kpi,
        "project": focus["name"] if focus and not single else "",
        "cost": numbers["cost"],
        "conversions": numbers["conversions"],
        "prev_cost": numbers["prev_cost"],
        "prev_conversions": numbers["prev_conversions"],
        "worst": worst,
        "all_in_goal": bool(with_goal) and not over and not single,
        "goal_link": goal_link,
        "chat": f"/w/{focus['id']}/chat" if focus else "/cabinet/chat",
        "campaigns": "/cabinet/campaigns",
    }
    return {
        "headline": headline(facts),
        "numbers": numbers,
        "goal": goal_of(single) if single else None,
        "goal_link": goal_link,
    }
