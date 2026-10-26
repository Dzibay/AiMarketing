"""Сводка по клиентам: расход, KPI против цели, темп месячного бюджета, баланс и список «где горит».

Всё считается из уже загруженных данных (stats_daily, direct_campaigns, балансы кабинетов) —
сводка открывается мгновенно и не ходит в API. Свежесть данных — задачи stats_today / stats_day.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median

from app.clock import MSK, today_msk
from app.db import pool

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# Пороги пока константами: подбираются на живых кабинетах, потом станут настройками.
KPI_WARN_RATIO = 1.3
BALANCE_CRITICAL_DAYS = 2
BALANCE_WARNING_DAYS = 5
PACE_OVER_PCT = 110
PACE_UNDER_PCT = 85
PACE_UNDER_MIN_DAY = 8  # недорасход в первую неделю месяца — норма
STATS_STALE_HOURS = 36
TOKEN_WARN_DAYS = 7

ANOMALY_BASE_DAYS = 14
ANOMALY_MIN_ACTIVE_DAYS = 5
ANOMALY_MIN_KOPECKS = 50_000  # 500 ₽: копеечные колебания не тревожат
SPEND_SPIKE_RATIO = 1.8
SPEND_DROP_RATIO = 0.3
CPA_SPIKE_RATIO = 2.0
CPA_MIN_BASE_CONVERSIONS = 5
NO_CONV_COST_RATIO = 2.0
NO_CONV_MIN_BASE_CONVERSIONS = 3

_TOKEN_ERRORS = ("token-expired", "unauthorized")


def rub(kopecks: float | None) -> str:
    """12345600 → «123 456 ₽» (неразрывные пробелы — в Telegram число не переносится)."""
    if kopecks is None:
        return "—"
    return f"{round(kopecks / 100):,}".replace(",", " ") + " ₽"


# --- Чистые расчёты (покрыты юнит-тестами) ---------------------------------------------------


def kpi_state(
    kpi_type: str, target: float | None, cost: int, conversions: int, revenue: int
) -> dict:
    """KPI клиента за период против цели.

    value — CPA в копейках или ДРР в процентах; ratio — во сколько раз хуже цели (≤ 1 — в цели).
    Без конверсий CPA не определён, но если расход уже больше цели, итоговый CPA будет не лучше
    расхода — им и оцениваем.
    """
    value: float | None = None
    ratio: float | None = None
    if kpi_type == "cpa":
        value = round(cost / conversions) if conversions else None
        if target and target > 0:
            target_kopecks = target * 100
            if conversions:
                ratio = value / target_kopecks
            elif cost > target_kopecks:
                ratio = cost / target_kopecks
    elif kpi_type == "drr":
        value = round(cost / revenue * 100, 1) if revenue else None
        if target and target > 0 and value is not None:
            ratio = value / target
    if ratio is None:
        status = "none"
    elif ratio <= 1:
        status = "ok"
    elif ratio <= KPI_WARN_RATIO:
        status = "warn"
    else:
        status = "bad"
    return {
        "type": kpi_type,
        "target": target,
        "value": value,
        "ratio": round(ratio, 3) if ratio is not None else None,
        "status": status,
    }


def budget_pace(budget_kopecks: int | None, spent_mtd: int, spent_before_today: int, today: date) -> dict | None:
    """Темп месячного бюджета: прогноз на конец месяца по полным прошедшим дням."""
    if not budget_kopecks:
        return None
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day - 1
    days_left = days_in_month - elapsed  # включая сегодня
    forecast = round(spent_before_today / elapsed * days_in_month) if elapsed else None
    pace_pct = round(forecast / budget_kopecks * 100) if forecast is not None else None
    remaining = budget_kopecks - spent_mtd
    if remaining <= 0:
        status = "exhausted"
    elif pace_pct is not None and pace_pct > PACE_OVER_PCT:
        status = "over"
    elif pace_pct is not None and pace_pct < PACE_UNDER_PCT and today.day >= PACE_UNDER_MIN_DAY:
        status = "under"
    else:
        status = "ok"
    return {
        "budget_kopecks": budget_kopecks,
        "spent_kopecks": spent_mtd,
        "forecast_kopecks": forecast,
        "pace_pct": pace_pct,
        "remaining_kopecks": remaining,
        # Сколько можно тратить в день до конца месяца, чтобы уложиться в бюджет.
        "daily_allowed_kopecks": max(0, round(remaining / days_left)),
        "status": status,
    }


def days_on_balance(balance_kopecks: int | None, cost_7d: int) -> float | None:
    if balance_kopecks is None or cost_7d <= 0:
        return None
    return round(max(0, balance_kopecks) / (cost_7d / 7), 1)


def campaign_anomalies(
    history: dict[date, tuple[int, int]],
    day: date,
    *,
    day_loaded: bool,
    cpa_target_kopecks: float | None = None,
) -> list[dict]:
    """Аномалии кампании за day против предыдущих ANOMALY_BASE_DAYS дней.

    history: день → (расход в копейках, конверсии); дней без показов в отчёте нет — это нули.
    day_loaded: статистика кабинета за day загружена — иначе «упал расход» был бы ложным.
    Базу считаем по дням с расходом: только что запущенная кампания не даёт медиану 0.
    """
    cost, conversions = history.get(day, (0, 0))
    base = [history[d] for d in (day - timedelta(days=i) for i in range(1, ANOMALY_BASE_DAYS + 1)) if d in history]
    active = [c for c, _ in base if c > 0]
    if len(active) < ANOMALY_MIN_ACTIVE_DAYS:
        return []
    typical = median(active)
    base_cost = sum(c for c, _ in base)
    base_conversions = sum(n for _, n in base)
    base_cpa = base_cost / base_conversions if base_conversions else None
    found: list[dict] = []

    if cost >= typical * SPEND_SPIKE_RATIO and cost - typical >= ANOMALY_MIN_KOPECKS:
        found.append(
            {
                "kind": "spend_spike",
                "title": f"Расход вырос в {round(cost / typical, 1):g} раза",
                "detail": f"Вчера {rub(cost)} при обычных {rub(typical)} в день",
            }
        )
    elif day_loaded and typical >= ANOMALY_MIN_KOPECKS and cost <= typical * SPEND_DROP_RATIO:
        found.append(
            {
                "kind": "spend_drop",
                "title": "Расход упал",
                "detail": f"Вчера {rub(cost)} при обычных {rub(typical)} в день",
            }
        )

    if (
        conversions
        and base_cpa
        and base_conversions >= CPA_MIN_BASE_CONVERSIONS
        and cost >= ANOMALY_MIN_KOPECKS
        and cost / conversions >= base_cpa * CPA_SPIKE_RATIO
    ):
        found.append(
            {
                "kind": "cpa_spike",
                "title": f"CPA вырос в {round(cost / conversions / base_cpa, 1):g} раза",
                "detail": f"Вчера {rub(cost / conversions)} при обычных {rub(base_cpa)}",
            }
        )

    if not conversions:
        threshold = cpa_target_kopecks
        if not threshold and base_cpa and base_conversions >= NO_CONV_MIN_BASE_CONVERSIONS:
            threshold = base_cpa
        if threshold and cost >= threshold * NO_CONV_COST_RATIO and cost >= ANOMALY_MIN_KOPECKS:
            found.append(
                {
                    "kind": "no_conversions",
                    "title": "Расход без конверсий",
                    "detail": f"Вчера {rub(cost)} и ни одной конверсии (CPA-ориентир {rub(threshold)})",
                }
            )
    return found


# --- Сборка сводки ---------------------------------------------------------------------------


def _problem(severity: str, kind: str, title: str, detail: str = "", **where) -> dict:
    return {
        "severity": severity,
        "kind": kind,
        "title": title,
        "detail": detail,
        "client_id": where.get("client_id"),
        "client_name": where.get("client_name", ""),
        "account_id": where.get("account_id"),
        "campaign_row_id": where.get("campaign_row_id"),
        "object_name": where.get("object_name", ""),
        # Клиенты, которых задевает проблема подключения, — для счётчиков в строках сводки.
        "client_ids": where.get("client_ids") or [where.get("client_id")],
    }


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _load(user_id: int, date_from: date, date_to: date) -> dict:
    with pool.connection() as conn:
        clients = conn.execute(
            """
            SELECT id, name, kpi_type, kpi_target, monthly_budget_kopecks
            FROM clients WHERE user_id = %s AND archived_at IS NULL
            ORDER BY lower(name), id
            """,
            (user_id,),
        ).fetchall()
        accounts = conn.execute(
            """
            SELECT id, connection_id, client_row_id,
                   COALESCE(NULLIF(name, ''), client_login) AS name,
                   last_sync_status, last_sync_error,
                   balance_kopecks, balance_currency, balance_synced_at, balance_error,
                   stats_synced_at, stats_error
            FROM direct_accounts
            WHERE user_id = %s AND enabled = TRUE
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
        stats = conn.execute(
            """
            SELECT account_id, campaign_id, day, cost_kopecks, clicks, conversions,
                   revenue_kopecks, updated_at
            FROM stats_daily
            WHERE user_id = %s AND source = 'direct' AND day >= %s AND day <= %s
            """,
            (user_id, date_from, date_to),
        ).fetchall()
        campaigns = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.state, c.status,
                   (
                     SELECT COUNT(*) FROM direct_ads ad
                     JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
                     WHERE g.campaign_row_id = c.id AND ad.status = 'REJECTED'
                       AND ad.state <> 'ARCHIVED'
                   ) AS rejected_ads
            FROM direct_campaigns c
            WHERE c.user_id = %s AND c.state NOT IN ('ARCHIVED', 'ENDED')
            ORDER BY c.id
            """,
            (user_id,),
        ).fetchall()
        connections = conn.execute(
            """
            SELECT id, yandex_login, expires_at, refresh_token_enc <> '' AS has_refresh,
                   last_sync_status, last_sync_error
            FROM yandex_connections WHERE user_id = %s
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
    return {
        "clients": clients,
        "accounts": accounts,
        "stats": stats,
        "campaigns": campaigns,
        "connections": connections,
    }


def build_overview(user_id: int, today: date | None = None, now: datetime | None = None) -> dict:
    """Строки по клиентам + отсортированный список проблем. today — московская дата."""
    today = today or today_msk()
    now = now or datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    week_from = today - timedelta(days=7)
    month_start = today.replace(day=1)
    history_from = min(month_start, yesterday - timedelta(days=ANOMALY_BASE_DAYS))
    data = _load(user_id, history_from, today)

    clients_by_id = {c["id"]: c for c in data["clients"]}
    # Кабинеты архивных клиентов в сводку не попадают; без клиента — отдельной строкой.
    accounts = [a for a in data["accounts"] if a["client_row_id"] is None or a["client_row_id"] in clients_by_id]
    owner = {a["id"]: a["client_row_id"] for a in accounts}
    accounts_by_id = {a["id"]: a for a in accounts}

    def client_name(client_id: int | None) -> str:
        return clients_by_id[client_id]["name"] if client_id is not None else "Без клиента"

    sums: dict[int | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    campaign_history: dict[tuple[int, int], dict[date, tuple[int, int]]] = defaultdict(dict)
    loaded_days: dict[int, set[date]] = defaultdict(set)
    campaign_week_cost: dict[tuple[int, int], int] = defaultdict(int)
    today_updated: datetime | None = None
    for r in data["stats"]:
        account_id = r["account_id"]
        if account_id not in owner:
            continue
        s = sums[owner[account_id]]
        day, cost = r["day"], r["cost_kopecks"]
        loaded_days[account_id].add(day)
        if day == today:
            s["today"] += cost
            updated = _aware(r["updated_at"])
            if updated and (today_updated is None or updated > today_updated):
                today_updated = updated
        if day == yesterday:
            s["yesterday"] += cost
            s["conversions_yesterday"] += r["conversions"]
        if week_from <= day <= yesterday:
            s["cost_7d"] += cost
            s["clicks_7d"] += r["clicks"]
            s["conversions_7d"] += r["conversions"]
            s["revenue_7d"] += r["revenue_kopecks"]
        if day >= month_start:
            s["mtd"] += cost
            if day < today:
                s["mtd_before_today"] += cost
        if r["campaign_id"] and day <= yesterday:
            key = (account_id, r["campaign_id"])
            prev_cost, prev_conv = campaign_history[key].get(day, (0, 0))
            campaign_history[key][day] = (prev_cost + cost, prev_conv + r["conversions"])
            if day >= week_from:
                campaign_week_cost[key] += cost

    problems: list[dict] = []
    rows: dict[int | None, dict] = {}
    client_ids: list[int | None] = list(clients_by_id)
    if any(a["client_row_id"] is None for a in accounts):
        client_ids.append(None)

    for client_id in client_ids:
        client = clients_by_id.get(client_id) or {}
        own_accounts = [a for a in accounts if a["client_row_id"] == client_id]
        s = sums[client_id]
        kpi_type = client.get("kpi_type") or ""
        target = float(client["kpi_target"]) if client.get("kpi_target") is not None else None
        kpi = kpi_state(kpi_type, target, s["cost_7d"], s["conversions_7d"], s["revenue_7d"])
        budget = budget_pace(client.get("monthly_budget_kopecks"), s["mtd"], s["mtd_before_today"], today)
        balances = [a["balance_kopecks"] for a in own_accounts if a["balance_kopecks"] is not None]
        balance = sum(balances) if balances else None
        currencies = {a["balance_currency"] for a in own_accounts if a["balance_kopecks"] is not None}
        balance_days = days_on_balance(balance, s["cost_7d"])
        synced = [_aware(a["stats_synced_at"]) for a in own_accounts]
        rows[client_id] = {
            "id": client_id,
            "name": client_name(client_id),
            "accounts": len(own_accounts),
            "spend_today": s["today"],
            "spend_yesterday": s["yesterday"],
            "conversions_yesterday": s["conversions_yesterday"],
            "spend_7d": s["cost_7d"],
            "clicks_7d": s["clicks_7d"],
            "conversions_7d": s["conversions_7d"],
            "revenue_7d": s["revenue_7d"],
            "cpa_7d": round(s["cost_7d"] / s["conversions_7d"]) if s["conversions_7d"] else None,
            "drr_7d": round(s["cost_7d"] / s["revenue_7d"] * 100, 1) if s["revenue_7d"] else None,
            "kpi": kpi,
            "budget": budget,
            "balance_kopecks": balance,
            "balance_currency": currencies.pop() if len(currencies) == 1 else "",
            "days_on_balance": balance_days,
            # Самый старый синк статистики среди кабинетов клиента: устарел один — устарела сводка.
            "stats_synced_at": None if not synced or None in synced else min(synced).isoformat(),
        }
        where = {"client_id": client_id, "client_name": client_name(client_id)}

        if balance is not None and s["cost_7d"] > 0:
            if balance <= 0:
                problems.append(_problem("critical", "balance", "Деньги на счёте закончились", rub(balance), **where))
            elif balance_days is not None and balance_days < BALANCE_WARNING_DAYS:
                severity = "critical" if balance_days < BALANCE_CRITICAL_DAYS else "warning"
                problems.append(
                    _problem(
                        severity,
                        "balance",
                        f"Баланса хватит на {balance_days:g} дн.",
                        f"{rub(balance)} при расходе ≈{rub(s['cost_7d'] / 7)} в день",
                        **where,
                    )
                )

        if budget:
            plan = f"бюджет {rub(budget['budget_kopecks'])}, потрачено {rub(budget['spent_kopecks'])}"
            if budget["status"] == "exhausted":
                problems.append(_problem("warning", "budget", "Месячный бюджет исчерпан", plan, **where))
            elif budget["status"] == "over":
                problems.append(
                    _problem(
                        "warning",
                        "budget",
                        f"Перерасход: прогноз {budget['pace_pct']}% бюджета",
                        f"{plan}; чтобы уложиться — до {rub(budget['daily_allowed_kopecks'])} в день",
                        **where,
                    )
                )
            elif budget["status"] == "under":
                problems.append(
                    _problem(
                        "info",
                        "budget",
                        f"Недорасход: прогноз {budget['pace_pct']}% бюджета",
                        f"{plan}; можно тратить до {rub(budget['daily_allowed_kopecks'])} в день",
                        **where,
                    )
                )

        if kpi["status"] in ("warn", "bad"):
            over = f"+{round((kpi['ratio'] - 1) * 100)}%"
            if kpi_type == "cpa":
                title = (
                    f"CPA за 7 дней {rub(kpi['value'])} при цели {rub(target * 100)} ({over})"
                    if kpi["value"] is not None
                    else f"{rub(s['cost_7d'])} за 7 дней без конверсий при цели CPA {rub(target * 100)}"
                )
            else:
                title = f"ДРР за 7 дней {kpi['value']:g}% при цели {target:g}% ({over})"
            # Плохой KPI — не пожар (деньги не стоят), но первое, что разбирать после пожаров.
            problems.append(_problem("warning" if kpi["status"] == "bad" else "info", "kpi", title, **where))
        elif kpi_type == "drr" and s["cost_7d"] > 0 and not s["revenue_7d"]:
            problems.append(
                _problem(
                    "info",
                    "kpi",
                    "Цель по ДРР задана, но дохода в отчётах Директа нет",
                    "Проверьте ценность ключевых целей в кампаниях или передачу дохода из Метрики",
                    **where,
                )
            )

    for a in accounts:
        where = {
            "client_id": owner[a["id"]],
            "client_name": client_name(owner[a["id"]]),
            "account_id": a["id"],
            "object_name": a["name"],
        }
        if a["stats_error"]:
            severity = "critical" if a["stats_error"] in _TOKEN_ERRORS else "warning"
            problems.append(
                _problem(severity, "stats_error", "Не загружается статистика кабинета", a["stats_error"], **where)
            )
        else:
            synced_at = _aware(a["stats_synced_at"])
            if synced_at is not None and now - synced_at > timedelta(hours=STATS_STALE_HOURS):
                problems.append(
                    _problem(
                        "warning",
                        "stats_stale",
                        "Статистика давно не обновлялась",
                        f"Последний раз {synced_at.astimezone(MSK):%d.%m %H:%M} МСК",
                        **where,
                    )
                )
        if a["last_sync_status"] == "error":
            problems.append(
                _problem(
                    "warning",
                    "account_sync",
                    "Не обновляются кампании кабинета",
                    a["last_sync_error"],
                    **where,
                )
            )

    for c in data["connections"]:
        affected = sorted(
            {owner[a["id"]] for a in accounts if a["connection_id"] == c["id"]},
            key=lambda cid: (cid is None, cid or 0),
        )
        if not affected:
            continue
        where = {
            "client_ids": affected,
            "client_name": ", ".join(client_name(cid) for cid in affected[:3])
            + (f" и ещё {len(affected) - 3}" if len(affected) > 3 else ""),
            "object_name": f"Яндекс-логин {c['yandex_login']}",
        }
        expires = _aware(c["expires_at"])
        error = c["last_sync_error"] or ""
        if any(code in error for code in _TOKEN_ERRORS) or (
            expires is not None and expires <= now and not c["has_refresh"]
        ):
            problems.append(
                _problem("critical", "token", "Нет доступа к Яндексу — переподключите логин", error, **where)
            )
        elif expires is not None and not c["has_refresh"] and expires - now < timedelta(days=TOKEN_WARN_DAYS):
            problems.append(
                _problem(
                    "warning",
                    "token",
                    f"Доступ к Яндексу истекает {expires.astimezone(MSK):%d.%m}",
                    "Переподключите логин, чтобы синк не остановился",
                    **where,
                )
            )
        elif c["last_sync_status"] in ("error", "partial"):
            problems.append(_problem("warning", "connection_sync", "Ошибка синхронизации логина", error, **where))

    for c in data["campaigns"]:
        account_id = c["account_id"]
        if account_id not in owner:
            continue
        key = (account_id, c["campaign_id"])
        where = {
            "client_id": owner[account_id],
            "client_name": client_name(owner[account_id]),
            "account_id": account_id,
            "campaign_row_id": c["id"],
            "object_name": c["name"] or str(c["campaign_id"]),
        }
        if c["status"] == "REJECTED":
            problems.append(_problem("critical", "moderation", "Кампания отклонена модерацией", **where))
        elif c["status"] == "MODERATION":
            problems.append(_problem("info", "moderation", "Кампания на модерации", **where))
        if c["state"] == "OFF" and campaign_week_cost.get(key, 0) > 0:
            problems.append(
                _problem(
                    "warning",
                    "stopped",
                    "Кампания перестала показываться",
                    f"За 7 дней потратила {rub(campaign_week_cost[key])}, сейчас не активна",
                    **where,
                )
            )
        if c["rejected_ads"] and c["state"] == "ON":
            problems.append(
                _problem("warning", "ads_rejected", f"Отклонено объявлений: {c['rejected_ads']}", **where)
            )
        if c["state"] == "ON" and key in campaign_history:
            client = clients_by_id.get(owner[account_id]) or {}
            cpa_target = (
                float(client["kpi_target"]) * 100
                if client.get("kpi_type") == "cpa" and client.get("kpi_target")
                else None
            )
            for anomaly in campaign_anomalies(
                campaign_history[key],
                yesterday,
                day_loaded=yesterday in loaded_days[account_id],
                cpa_target_kopecks=cpa_target,
            ):
                problems.append(
                    _problem("warning", anomaly["kind"], anomaly["title"], anomaly["detail"], **where)
                )

    problems.sort(key=lambda p: (SEVERITY_ORDER[p["severity"]], p["client_name"].lower(), p["object_name"].lower()))
    for row in rows.values():
        row["problems"] = {"critical": 0, "warning": 0, "info": 0}
    for p in problems:
        for client_id in p["client_ids"]:
            if client_id in rows:
                rows[client_id]["problems"][p["severity"]] += 1
    for row in rows.values():
        counts = row["problems"]
        row["severity"] = next((sev for sev in ("critical", "warning", "info") if counts[sev]), "")

    totals = defaultdict(int)
    for row in rows.values():
        for key in ("spend_today", "spend_yesterday", "conversions_yesterday", "spend_7d", "conversions_7d"):
            totals[key] += row[key]
    return {
        "today": today.isoformat(),
        "yesterday": yesterday.isoformat(),
        "today_updated_at": today_updated.isoformat() if today_updated else None,
        "clients": list(rows.values()),
        "totals": dict(totals),
        "problems": problems,
    }
