"""Сводка по сайтам (проектам) и папкам-клиентам: расход, KPI, темп бюджета, баланс, «где горит».

Всё считается из уже загруженных данных (stats_daily, direct_campaigns, балансы кабинетов) —
сводка открывается мгновенно и не ходит в API. Свежесть данных — задачи stats_today / stats_day.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median
from urllib.parse import quote

from app.clock import MSK, today_msk
from app.db import pool
from app.plain_errors import plain_error

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# Пороги пока константами: подбираются на живых кабинетах, потом станут настройками.
KPI_WARN_RATIO = 1.3
BALANCE_CRITICAL_DAYS = 2
BALANCE_WARNING_DAYS = 5
PACE_OVER_PCT = 110
PACE_UNDER_PCT = 85
PACE_UNDER_MIN_DAY = 8  # недорасход в первую неделю месяца — норма
# Прогноз с учётом дней недели: по последним 4–8 полным неделям; истории меньше — делим поровну.
FORECAST_WEEKS = 8
FORECAST_MIN_WEEKS = 4
STATS_STALE_HOURS = 36
TOKEN_WARN_DAYS = 7
# «Проект не настроен» — только для новых проектов: старые уже работают, им хватает карточки в сводке проекта.
SETUP_FRESH_DAYS = 30

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
BALANCE_OUT_TITLE = "Деньги на счёте закончились"

# Строка «как дела»: заметное изменение к прошлому периоду и минимальная база, чтобы 2 → 1 заявка не тревожила.
HEADLINE_SHIFT_PCT = 30
HEADLINE_MIN_CONVERSIONS = 5
HEADLINE_MIN_COST = 100_000  # 1 000 ₽
DIRECT_URL = "https://direct.yandex.ru/"
# Кабинет конкретного логина (агентский доступ открывает клиента по ulogin). Адрес на живом
# кабинете с оплатой не проверен — если Директ его сменит, ведём на общую главную.
DIRECT_ACCOUNT_URL = "https://direct.yandex.ru/dna/grid/campaigns?ulogin={login}"


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


def weekday_profile(daily: dict[date, int], today: date) -> list[float] | None:
    """Средний расход по дням недели (пн = 0) за последние полные недели перед сегодня, не больше
    FORECAST_WEEKS. Неделей считаем 7 дней от первого дня с расходом: истории меньше
    FORECAST_MIN_WEEKS недель или расхода не было — None (прогноз делится поровну)."""
    yesterday = today - timedelta(days=1)
    spent_days = [d for d, cost in daily.items() if cost > 0 and d <= yesterday]
    if not spent_days:
        return None
    weeks = min(FORECAST_WEEKS, ((yesterday - min(spent_days)).days + 1) // 7)
    if weeks < FORECAST_MIN_WEEKS:
        return None
    sums = [0] * 7
    start = today - timedelta(days=7 * weeks)
    for d, cost in daily.items():
        if start <= d <= yesterday:
            sums[d.weekday()] += cost
    if not any(sums):
        return None
    return [s / weeks for s in sums]


def weekday_forecast(profile: list[float], spent_before_today: int, today: date) -> int | None:
    """Расход к концу месяца с учётом дней недели.

    Уровень берём из этого месяца: потраченное за прошедшие дни делим на «обычный» расход тех же
    дней недели и тем же множителем досчитываем остаток месяца (включая сегодня). В первый день месяца
    прошедших дней нет — остаток считаем по средним прошлых недель как есть.
    """
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    month_start = today.replace(day=1)
    elapsed = [month_start + timedelta(days=i) for i in range(today.day - 1)]
    rest = [today + timedelta(days=i) for i in range(days_in_month - today.day + 1)]
    usual_elapsed = sum(profile[d.weekday()] for d in elapsed)
    usual_rest = sum(profile[d.weekday()] for d in rest)
    if not elapsed:
        return round(usual_rest)
    if usual_elapsed <= 0:
        return None  # прошли только «пустые» дни недели — множитель не определён
    return round(spent_before_today + spent_before_today / usual_elapsed * usual_rest)


def budget_pace(
    budget_kopecks: int | None,
    spent_mtd: int,
    spent_before_today: int,
    today: date,
    *,
    profile: list[float] | None = None,
) -> dict | None:
    """Темп месячного бюджета: прогноз на конец месяца по полным прошедшим дням.

    profile — средний расход по дням недели (weekday_profile): с ним прогноз учитывает выходные,
    без него (истории мало) — делит поровну.
    """
    if not budget_kopecks:
        return None
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day - 1
    days_left = days_in_month - elapsed  # включая сегодня
    forecast = weekday_forecast(profile, spent_before_today, today) if profile else None
    method = "weekdays" if forecast is not None else "even"
    if forecast is None:
        forecast = round(spent_before_today / elapsed * days_in_month) if elapsed else None
    elif forecast < spent_mtd:
        forecast = spent_mtd  # сегодня уже потратили больше «обычного» — меньше уже не уйдёт
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
        # Как считали прогноз и где «сегодня» на полосе месяца.
        "forecast_method": method,
        "day": today.day,
        "days_in_month": days_in_month,
    }


def days_on_balance(balance_kopecks: int | None, cost_7d: int) -> float | None:
    if balance_kopecks is None or cost_7d <= 0:
        return None
    return round(max(0, balance_kopecks) / (cost_7d / 7), 1)


def direct_account_url(login: str | None) -> str:
    """Директ сразу на кабинете логина — пополнять счёт там. Логина нет — общая главная Директа."""
    return DIRECT_ACCOUNT_URL.format(login=quote(login, safe="")) if login else DIRECT_URL


def runs_out_on(balance_kopecks: int | None, days: float | None, today: date) -> date | None:
    """Дата, когда деньги кончатся при расходе как за 7 дней: 6,4 дня — в течение седьмого дня от сегодня."""
    if balance_kopecks is not None and balance_kopecks <= 0:
        return today
    if days is None:
        return None
    return today + timedelta(days=int(days))


def balance_order(entry: dict) -> tuple:
    """Порядок блока «Деньги»: кончились → меньше всего дней → без расхода → баланса не знаем."""
    balance, days = entry["balance_kopecks"], entry["days_on_balance"]
    if balance is not None and balance <= 0:
        group, value = 0, 0.0
    elif days is not None:
        group, value = 1, days
    elif balance is not None:
        group, value = 2, 0.0
    else:
        group, value = 3, 0.0
    return group, value, (entry["name"] or "").lower(), entry["account_id"]


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
                "title": f"Цена заявки выросла в {round(cost / conversions / base_cpa, 1):g} раза",
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
                    "title": "Расход без заявок",
                    "detail": f"Вчера {rub(cost)} и ни одной заявки (ориентир цены заявки {rub(threshold)})",
                }
            )
    return found


# --- Сборка сводки ---------------------------------------------------------------------------

NO_WORKSPACE = "Без проекта"
NO_CLIENT = "Без клиента"


def rejected_detail(rows: list[dict], limit: int = 3) -> str:
    """Что отклонила модерация и почему — коротко: «„Окна от 990 ₽“ — нарушены требования…; Быстрые ссылки — …»."""
    parts: list[str] = []
    for r in rows:
        title = str(r.get("title") or "").strip()
        name = f"«{title[:40]}{'…' if len(title) > 40 else ''}»" if title else "Объявление"
        if r.get("status") == "REJECTED":
            reason = str(r.get("reason") or "").strip()
            parts.append(f"{name} — {reason}" if reason else name)
        for extra in r.get("extras") or []:
            reason = str(extra.get("reason") or "").strip()
            what = f"{extra.get('what') or 'Дополнение'} {f'в {name}' if title else 'объявления'}"
            parts.append(f"{what} — {reason}" if reason else what)
    shown = parts[:limit]
    more = len(parts) - len(shown)
    return "; ".join(shown) + (f" и ещё {more}" if more > 0 else "")


def rejected_reasons(rows: list[dict]) -> list[str]:
    """Причины отказа модерации без повторов — снимок для скрытия «так задумано» (новая причина → покажем)."""
    reasons: list[str] = []
    for r in rows:
        if r.get("status") == "REJECTED":
            reasons.append(str(r.get("reason") or "").strip())
        reasons.extend(str(extra.get("reason") or "").strip() for extra in r.get("extras") or [])
    return sorted({x for x in reasons if x})


def _problem(severity: str, kind: str, title: str, detail: str = "", **where) -> dict:
    workspace_ids = where.get("workspace_ids") or [where.get("workspace_id")]
    return {
        "severity": severity,
        "kind": kind,
        "title": title,
        "detail": detail,
        "workspace_id": where.get("workspace_id"),
        "workspace_name": where.get("workspace_name", ""),
        "client_id": where.get("client_id"),
        "client_name": where.get("client_name", ""),
        "account_id": where.get("account_id"),
        "campaign_row_id": where.get("campaign_row_id"),
        "object_name": where.get("object_name", ""),
        # Проекта, которые задевает проблема кабинета или логина, — для счётчиков в строках.
        "workspace_ids": workspace_ids,
    }


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _load(user_id: int, date_from: date, date_to: date, forecast_from: date | None = None) -> dict:
    with pool.connection() as conn:
        clients = conn.execute(
            """
            SELECT id, name, notes, kpi_type, kpi_target, monthly_budget_kopecks, archived_at
            FROM clients WHERE user_id = %s
            ORDER BY lower(name), id
            """,
            (user_id,),
        ).fetchall()
        workspaces = conn.execute(
            """
            SELECT w.id, w.name, w.site, w.client_row_id, w.kpi_type, w.kpi_target,
                   w.monthly_budget_kopecks, w.counter_id, w.counter_row_id,
                   w.goal_id, w.url_template, w.created_at,
                   m.enabled AS counter_enabled, m.connection_id AS counter_connection_id
            FROM workspaces w
            LEFT JOIN metrika_counters m ON m.id = w.counter_row_id
            WHERE w.user_id = %s AND w.archived_at IS NULL
            ORDER BY lower(w.name), w.id
            """,
            (user_id,),
        ).fetchall()
        ws_accounts = conn.execute(
            "SELECT workspace_id, account_id FROM workspace_accounts WHERE user_id = %s",
            (user_id,),
        ).fetchall()
        ws_campaigns = conn.execute(
            "SELECT workspace_id, campaign_row_id FROM workspace_campaigns WHERE user_id = %s",
            (user_id,),
        ).fetchall()
        accounts = conn.execute(
            """
            SELECT id, connection_id, client_login,
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
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.state, c.status, c.status_clarification,
                   (
                     SELECT COUNT(*) FROM direct_ads ad
                     JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
                     WHERE g.campaign_row_id = c.id AND ad.status = 'REJECTED'
                       AND ad.state <> 'ARCHIVED'
                   ) AS rejected_ads,
                   -- Что отклонено и почему — первые несколько, для пояснения в «Где горит».
                   (
                     SELECT COALESCE(jsonb_agg(r), '[]'::jsonb) FROM (
                       SELECT ad.title, ad.status, ad.status_clarification AS reason, ad.moderation AS extras
                       FROM direct_ads ad
                       JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
                       WHERE g.campaign_row_id = c.id AND ad.state <> 'ARCHIVED'
                         AND (ad.status = 'REJECTED' OR ad.moderation <> '[]'::jsonb)
                       ORDER BY ad.id
                       LIMIT 5
                     ) r
                   ) AS rejected_list
            FROM direct_campaigns c
            WHERE c.user_id = %s
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
        # Расход по дням за последние недели для прогноза бюджета по дням недели — одним сгруппированным
        # запросом и только для проектов, у которых (или у их клиента) задан бюджет месяца.
        ws_daily = (
            conn.execute(
                """
                SELECT wc.workspace_id, s.day, SUM(s.cost_kopecks) AS cost
                FROM stats_daily s
                JOIN direct_accounts a ON a.id = s.account_id AND a.enabled = TRUE
                JOIN direct_campaigns c ON c.account_id = s.account_id AND c.campaign_id = s.campaign_id
                JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
                JOIN workspaces w ON w.id = wc.workspace_id AND w.user_id = s.user_id
                LEFT JOIN clients cl ON cl.id = w.client_row_id
                WHERE s.user_id = %s AND s.source = 'direct' AND s.day >= %s AND s.day <= %s
                  AND (w.monthly_budget_kopecks > 0 OR cl.monthly_budget_kopecks > 0)
                GROUP BY wc.workspace_id, s.day
                """,
                (user_id, forecast_from, date_to),
            ).fetchall()
            if forecast_from
            else []
        )
    return {
        "ws_daily": ws_daily,
        "clients": clients,
        "workspaces": workspaces,
        "ws_accounts": ws_accounts,
        "ws_campaigns": ws_campaigns,
        "accounts": accounts,
        "stats": stats,
        "campaigns": campaigns,
        "connections": connections,
    }


def _new_sums() -> dict[str, int]:
    return defaultdict(int)


def _add_day(s: dict[str, int], r: dict, *, today: date, yesterday: date, week_from: date, month_start: date) -> None:
    day, cost = r["day"], r["cost_kopecks"]
    if day == today:
        s["today"] += cost
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


def _spend_fields(s: dict[str, int]) -> dict:
    return {
        "spend_today": s["today"],
        "spend_yesterday": s["yesterday"],
        "conversions_yesterday": s["conversions_yesterday"],
        "spend_7d": s["cost_7d"],
        "clicks_7d": s["clicks_7d"],
        "conversions_7d": s["conversions_7d"],
        "revenue_7d": s["revenue_7d"],
        "spend_mtd": s["mtd"],
        "cpa_7d": round(s["cost_7d"] / s["conversions_7d"]) if s["conversions_7d"] else None,
        "drr_7d": round(s["cost_7d"] / s["revenue_7d"] * 100, 1) if s["revenue_7d"] else None,
    }


def _approx_rub(kopecks: int) -> str:
    """Прогноз — не точная сумма: «~322 000 ₽», до тысяч рублей (до сотен — у маленьких сумм)."""
    step = 100_000 if kopecks >= 1_000_000 else 10_000
    return "~" + rub(round(kopecks / step) * step)


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def budget_texts(budget: dict) -> tuple[str, str] | None:
    """Заголовок и пояснение проблемы бюджета человеческими словами — в рублях, а не в процентах темпа."""
    spent = f"С начала месяца потрачено {rub(budget['spent_kopecks'])}"
    if budget["status"] == "exhausted":
        return "месячный бюджет исчерпан", f"{spent} при бюджете {rub(budget['budget_kopecks'])}"
    forecast = budget.get("forecast_kopecks")
    if forecast is None:
        return None
    daily = rub(budget["daily_allowed_kopecks"])
    if budget["status"] == "over":
        return (
            f"к концу месяца уйдёт {_approx_rub(forecast)} при бюджете {rub(budget['budget_kopecks'])}",
            f"{spent}; чтобы уложиться, тратьте до {daily} в день",
        )
    if budget["status"] == "under":
        return (
            f"к концу месяца уйдёт лишь {_approx_rub(forecast)} из бюджета {rub(budget['budget_kopecks'])}",
            f"{spent}; можно тратить до {daily} в день",
        )
    return None


def setup_missing(ws: dict) -> list[str]:
    """Чего не хватает проекту из главного на экране «Подготовим проект»: цели и шаблона ссылки."""
    missing = []
    if not ws.get("goal_id"):
        missing.append("цели")
    if not str(ws.get("url_template") or "").strip():
        missing.append("шаблона ссылки")
    return missing


def _setup_problem(ws: dict, now: datetime, where: dict) -> dict | None:
    """Новый проект (моложе SETUP_FRESH_DAYS) без цели или шаблона ссылки — тихая проблема со ссылкой на настройку."""
    created = _aware(ws.get("created_at"))
    missing = setup_missing(ws)
    if not missing or created is None or now - created > timedelta(days=SETUP_FRESH_DAYS):
        return None
    why = []
    if "цели" in missing:
        why.append("без цели не посчитать заявки и их цену")
    if "шаблона ссылки" in missing:
        why.append("без шаблона не проверить ссылки объявлений")
    detail = "; ".join(why)
    return _problem("info", "setup", f"Проект не настроен: нет {' и '.join(missing)}", detail[:1].upper() + detail[1:], **where)


def _budget_problems(budget: dict | None, where: dict, *, label: str = "") -> list[dict]:
    texts = budget_texts(budget) if budget else None
    if not texts:
        return []
    title, detail = texts
    title = f"{label}: {title}" if label else _sentence(title)
    severity = "info" if budget["status"] == "under" else "warning"
    problem = _problem(severity, "budget", title, detail, **where)
    # Для скрытия «так задумано» (snoozes): смена статуса бюджета — новая причина.
    problem["metric"] = {"reasons": [budget["status"]]}
    if label:
        # Бюджет папки-клиента — свой ключ скрытия, не тот же, что у бюджета проекта.
        problem["level"] = "client"
    return [problem]


# --- Строка «как дела» ----------------------------------------------------------------------


def _int(n: float) -> str:
    return f"{round(n):,}".replace(",", " ")


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def _leads(n: int) -> str:
    return f"{_int(n)} {_plural(n, 'заявка', 'заявки', 'заявок')}"


def _dec(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _days(value: float) -> str:
    word = _plural(int(value), "день", "дня", "дней") if float(value).is_integer() else "дня"
    return f"{_dec(value)} {word}"


def _quoted(name: str) -> str:
    return f"«{name}»"


def _names(items: list[dict]) -> str:
    first = _quoted(items[0]["name"])
    return first + (f" и ещё {len(items) - 1}" if len(items) > 1 else "")


def _pct_change(cur: float, prev: float) -> float | None:
    return (cur - prev) / prev * 100 if prev else None


def headline(f: dict) -> dict:
    """
    Одна строка о том, как идут дела, — человеческим языком, без терминов. Считает код, не ИИ.

    f — факты за период: days (7 или 30); accounts — подключено кабинетов; stats_loaded; token_lost;
    money_out; money_days — на сколько дней хватит денег (money_project — чьих, на главной); money_url — кабинет
    Директа, который пополнять; rejected / stopped — [{name, to}] кампаний;
    kpi — kpi_state за период или None; project — проект с плохим KPI (на главной); cost, conversions,
    prev_cost, prev_conversions; worst — {name, to} самая дорогая кампания; all_in_goal — на главной
    все проекты с целью укладываются в неё; goal_link — где задать цель; chat — путь чата;
    campaigns — путь списка кампаний.

    Правила по порядку, первое сработавшее побеждает: данные → реклама не идёт → цена заявки выше цели →
    заметное изменение к прошлому периоду → всё по плану. Возвращает {tone: ok|warn|bad, text, link}.
    """
    days = f["days"]
    period = f"за {days} дней"
    before = f"за прошлые {days} дней"

    def out(tone: str, text: str, link: dict | None = None) -> dict:
        return {"tone": tone, "text": text, "link": link}

    def ask(question: str) -> dict:
        return {"label": "Что с этим делать", "to": f["chat"], "ask": question}

    connections = {"label": "Открыть подключения", "to": "/cabinet/connections"}
    top_up = {"label": "Пополнить в Директе", "to": f.get("money_url") or DIRECT_URL, "external": True}

    # 1. Данные.
    if not f["accounts"]:
        return out("warn", "Директ ещё не подключён — сводке пока не из чего считать", connections)
    if f["token_lost"]:
        return out("bad", "Нет доступа к Яндексу: цифры не обновляются, пока вы не переподключите логин", connections)
    if not f["stats_loaded"]:
        return out("ok", "Загружаем статистику из Директа — цифры появятся в течение нескольких минут")

    # 2. Реклама не идёт.
    if f["money_out"]:
        return out("bad", "Реклама не показывается: на счёте закончились деньги", top_up)
    money_days = f["money_days"]
    if money_days is not None and money_days < BALANCE_CRITICAL_DAYS:
        when = "меньше чем через день" if money_days < 1 else f"примерно через {_days(money_days)}"
        whose = f"В {_quoted(f['money_project'])} деньги" if f.get("money_project") else "Деньги"
        return out("bad", f"{whose} на счёте закончатся {when}", top_up)
    if f["rejected"]:
        what = "кампания" if len(f["rejected"]) == 1 else "кампании"
        verb = "отклонена" if len(f["rejected"]) == 1 else "отклонены"
        return out(
            "bad",
            f"Реклама не показывается: {what} {_names(f['rejected'])} {verb} модерацией",
            {"label": "Открыть кампанию", "to": f["rejected"][0]["to"]},
        )
    if f["stopped"]:
        one = len(f["stopped"]) == 1
        return out(
            "warn",
            f"{'Кампания' if one else 'Кампании'} {_names(f['stopped'])} "
            f"{'перестала' if one else 'перестали'} показываться",
            {"label": "Открыть кампанию", "to": f["stopped"][0]["to"]},
        )
    cost, conv = f["cost"], f["conversions"]
    if not cost:
        return out("warn", f"Реклама не показывается: {period} расхода не было", {"label": "Открыть кампании", "to": f["campaigns"]})

    # 3. Цена заявки выше цели.
    kpi = f["kpi"]
    where = f" в {_quoted(f['project'])}" if f.get("project") else ""
    if kpi and kpi["status"] in ("warn", "bad"):
        tone = "bad" if kpi["status"] == "bad" else "warn"
        worst = f". Дороже всего — {_quoted(f['worst']['name'])}" if f.get("worst") else ""
        if kpi["type"] == "cpa":
            goal = rub(kpi["target"] * 100)
            if kpi["value"] is not None:
                text = f"Заявки{where} подорожали: {rub(kpi['value'])} при цели {goal}{worst}"
            else:
                text = f"{_sentence(period)}{where} потрачено {rub(cost)}, а заявок нет — при цели {goal} за заявку{worst}"
        else:
            text = f"Реклама{where} обходится дороже цели: {_dec(kpi['value'])}% от выручки при цели {_dec(kpi['target'])}%"
        return out(tone, text, ask(f"{text}. Почему так и что сделать?"))

    # 4. Заметное изменение к прошлому периоду.
    prev_cost, prev_conv = f["prev_cost"], f["prev_conversions"]
    conv_change = _pct_change(conv, prev_conv) if prev_conv >= HEADLINE_MIN_CONVERSIONS else None
    if conv_change is not None and abs(conv_change) >= HEADLINE_SHIFT_PCT:
        if conv_change < 0:
            text = f"Заявок стало меньше: {_leads(conv)} {period} против {_int(prev_conv)} {before}"
            return out("warn", text, ask(f"{text}. Почему так и что сделать?"))
        return out("ok", f"Заявок стало больше: {_leads(conv)} {period} против {_int(prev_conv)} {before}")
    cost_change = _pct_change(cost, prev_cost) if prev_cost >= HEADLINE_MIN_COST else None
    if cost_change is not None and abs(cost_change) >= HEADLINE_SHIFT_PCT:
        word = "вырос" if cost_change > 0 else "упал"
        text = f"Расход {word}: {rub(cost)} {period} против {rub(prev_cost)} {before}"
        return out("warn", text, ask(f"{text}. Почему так и что сделать?"))

    # 5. Всё по плану.
    if kpi and kpi["status"] == "ok":
        if kpi["type"] == "cpa":
            return out(
                "ok",
                f"Всё идёт по плану: {period} {_leads(conv)} по {rub(kpi['value'])}, цель — {rub(kpi['target'] * 100)}",
            )
        return out(
            "ok",
            f"Всё идёт по плану: {period} реклама обошлась в {_dec(kpi['value'])}% от выручки, цель — {_dec(kpi['target'])}%",
        )
    if not conv:
        text = f"{_sentence(period)} потрачено {rub(cost)}, а заявок пока нет"
        return out("warn", text, ask(f"{text}. Почему так и что сделать?"))
    summary = f"{period} {_leads(conv)} по {rub(cost / conv)}"
    if f.get("all_in_goal"):
        return out("ok", f"Всё идёт по плану: {summary}, все проекты укладываются в цель")
    if f.get("goal_link"):
        return out(
            "ok",
            f"{_sentence(summary)}. Задайте цель — подскажем, когда заявки подорожают",
            {"label": "Задать цель", "to": f["goal_link"]},
        )
    return out("ok", _sentence(summary))


def build_overview(
    user_id: int, today: date | None = None, now: datetime | None = None, *, review: bool = False
) -> dict:
    """
    Строки по проектам (сайтам), папки-клиенты с общим бюджетом и список «где горит».

    Расход кампании идёт в проект, где она лежит; кампании вне проектов — в строку
    «Без проекта». Проекта архивных клиентов и их кабинеты в сводку не попадают.
    today — московская дата. review — утренний разбор (снимок проблем за день и сравнение
    с прошлым): только для экрана обзора, фоновые задачи снимок не трогают.
    """
    today = today or today_msk()
    now = now or datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    week_from = today - timedelta(days=7)
    month_start = today.replace(day=1)
    history_from = min(month_start, yesterday - timedelta(days=ANOMALY_BASE_DAYS))
    data = _load(user_id, history_from, today, forecast_from=today - timedelta(days=7 * FORECAST_WEEKS))
    period = {"today": today, "yesterday": yesterday, "week_from": week_from, "month_start": month_start}

    clients_by_id = {c["id"]: c for c in data["clients"]}
    archived_clients = {c["id"] for c in data["clients"] if c["archived_at"] is not None}
    workspaces = [w for w in data["workspaces"] if w["client_row_id"] not in archived_clients]
    hidden_ws = {w["id"] for w in data["workspaces"]} - {w["id"] for w in workspaces}
    ws_by_id = {w["id"]: w for w in workspaces}

    account_ws: dict[int, list[int]] = defaultdict(list)
    account_hidden_ws: set[int] = set()
    for link in data["ws_accounts"]:
        if link["workspace_id"] in ws_by_id:
            account_ws[link["account_id"]].append(link["workspace_id"])
        elif link["workspace_id"] in hidden_ws:
            account_hidden_ws.add(link["account_id"])
    campaign_ws = {link["campaign_row_id"]: link["workspace_id"] for link in data["ws_campaigns"]}

    accounts = [
        a for a in data["accounts"] if a["id"] in account_ws or a["id"] not in account_hidden_ws
    ]  # кабинет только архивных клиентов — скрыт
    accounts_by_id = {a["id"]: a for a in accounts}

    campaign_row = {(c["account_id"], c["campaign_id"]): c for c in data["campaigns"]}

    def ws_of_campaign(account_id: int, campaign_id: int | None) -> tuple[bool, int | None]:
        """(видно ли, проект). Кампания скрытого проекта не видна."""
        row = campaign_row.get((account_id, campaign_id)) if campaign_id else None
        ws_id = campaign_ws.get(row["id"]) if row else None
        if ws_id in hidden_ws:
            return False, None
        return True, ws_id

    def ws_name(ws_id: int | None) -> str:
        return ws_by_id[ws_id]["name"] if ws_id in ws_by_id else NO_WORKSPACE

    def ws_client(ws_id: int | None) -> int | None:
        return ws_by_id[ws_id]["client_row_id"] if ws_id in ws_by_id else None

    def client_name(client_id: int | None) -> str:
        return clients_by_id[client_id]["name"] if client_id in clients_by_id else ""

    def where_ws(ws_id: int | None, **extra) -> dict:
        cid = ws_client(ws_id)
        return {
            "workspace_id": ws_id,
            "workspace_name": ws_name(ws_id),
            "client_id": cid,
            "client_name": client_name(cid),
            **extra,
        }

    def where_many(ws_ids: list[int | None], **extra) -> dict:
        ws_ids = sorted(set(ws_ids), key=lambda w: (w is None, ws_name(w).lower(), w or 0))
        names = [ws_name(w) for w in ws_ids]
        label = ", ".join(names[:3]) + (f" и ещё {len(names) - 3}" if len(names) > 3 else "")
        if len(ws_ids) == 1:
            return where_ws(ws_ids[0], **extra)
        return {"workspace_ids": ws_ids, "workspace_name": label, "client_name": "", **extra}

    ws_sums: dict[int | None, dict[str, int]] = defaultdict(_new_sums)
    account_cost_7d: dict[int, int] = defaultdict(int)
    campaign_history: dict[tuple[int, int], dict[date, tuple[int, int]]] = defaultdict(dict)
    loaded_days: dict[int, set[date]] = defaultdict(set)
    campaign_week_cost: dict[tuple[int, int], int] = defaultdict(int)
    has_unassigned_spend = False
    today_updated: datetime | None = None
    day_totals: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    for r in data["stats"]:
        account_id = r["account_id"]
        if account_id not in accounts_by_id:
            continue
        visible, ws_id = ws_of_campaign(account_id, r["campaign_id"])
        if not visible:
            continue
        day, cost = r["day"], r["cost_kopecks"]
        loaded_days[account_id].add(day)
        _add_day(ws_sums[ws_id], r, **period)
        day_totals[day][0] += cost
        day_totals[day][1] += r["conversions"]
        if ws_id is None and cost:
            has_unassigned_spend = True
        if week_from <= day <= yesterday:
            account_cost_7d[account_id] += cost
        if day == today:
            updated = _aware(r["updated_at"])
            if updated and (today_updated is None or updated > today_updated):
                today_updated = updated
        if r["campaign_id"] and day <= yesterday:
            key = (account_id, r["campaign_id"])
            prev_cost, prev_conv = campaign_history[key].get(day, (0, 0))
            campaign_history[key][day] = (prev_cost + cost, prev_conv + r["conversions"])
            if day >= week_from:
                campaign_week_cost[key] += cost

    # Прогноз бюджета по дням недели: один и тот же для сигналов, строки «как дела», срочных
    # оповещений и блока «Бюджет на месяц» на главной проекта.
    ws_daily: dict[int, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for r in data.get("ws_daily") or []:
        ws_daily[r["workspace_id"]][r["day"]] += int(r["cost"] or 0)

    def spend_profile(ws_ids: list[int]) -> list[float] | None:
        daily: dict[date, int] = defaultdict(int)
        for w in ws_ids:
            for d, cost in ws_daily.get(w, {}).items():
                daily[d] += cost
        return weekday_profile(daily, today) if daily else None

    ws_campaign_count: dict[int, int] = defaultdict(int)
    for c in data["campaigns"]:
        ws_id = campaign_ws.get(c["id"])
        if ws_id in ws_by_id and c["state"] not in ("ARCHIVED",):
            ws_campaign_count[ws_id] += 1

    unlinked_accounts = [a for a in accounts if a["id"] not in account_ws]
    row_ids: list[int | None] = [w["id"] for w in workspaces]
    if unlinked_accounts or has_unassigned_spend:
        row_ids.append(None)

    problems: list[dict] = []
    rows: dict[int | None, dict] = {}
    for ws_id in row_ids:
        ws = ws_by_id.get(ws_id) or {}
        client = clients_by_id.get(ws.get("client_row_id")) or {}
        s = ws_sums[ws_id]
        # KPI проекта; не задан — берём KPI папки-клиента.
        kpi_type = ws.get("kpi_type") or client.get("kpi_type") or ""
        raw_target = ws.get("kpi_target") if ws.get("kpi_type") else client.get("kpi_target")
        target = float(raw_target) if raw_target is not None else None
        kpi = kpi_state(kpi_type, target, s["cost_7d"], s["conversions_7d"], s["revenue_7d"])
        budget = budget_pace(
            ws.get("monthly_budget_kopecks"),
            s["mtd"],
            s["mtd_before_today"],
            today,
            profile=spend_profile([ws_id]) if ws_id is not None and ws.get("monthly_budget_kopecks") else None,
        )
        own_accounts = (
            [accounts_by_id[a] for a, ws_ids in account_ws.items() if ws_id in ws_ids and a in accounts_by_id]
            if ws_id is not None
            else unlinked_accounts
        )
        balances = [a["balance_kopecks"] for a in own_accounts if a["balance_kopecks"] is not None]
        balance = sum(balances) if balances else None
        currencies = {a["balance_currency"] for a in own_accounts if a["balance_kopecks"] is not None}
        # Баланс кабинета общий для всех его кампаний — дни считаем по расходу всего кабинета. У каждого
        # логина свой счёт: реклама встанет, когда кончится первый, — поэтому запас проекта по самому короткому.
        runways = [
            d
            for a in own_accounts
            if (d := days_on_balance(a["balance_kopecks"], account_cost_7d[a["id"]])) is not None
        ]
        balance_days = min(runways) if runways else None
        synced = [_aware(a["stats_synced_at"]) for a in own_accounts]
        rows[ws_id] = {
            "id": ws_id,
            "name": ws_name(ws_id),
            "site": ws.get("site") or "",
            "client_id": ws.get("client_row_id"),
            "client_name": client_name(ws.get("client_row_id")),
            "counter_id": ws.get("counter_id"),
            "counter_ok": bool(ws.get("counter_row_id")) and bool(ws.get("counter_enabled")) if ws_id else True,
            "accounts": len(own_accounts),
            "campaigns": ws_campaign_count.get(ws_id, 0) if ws_id is not None else 0,
            **_spend_fields(s),
            "kpi": kpi,
            "budget": budget,
            "balance_kopecks": balance,
            "balance_currency": currencies.pop() if len(currencies) == 1 else "",
            "days_on_balance": balance_days,
            # Самый старый синк статистики среди кабинетов: устарел один — устарела строка.
            "stats_synced_at": None if not synced or None in synced else min(synced).isoformat(),
        }
        where = where_ws(ws_id)

        if ws_id is not None and not rows[ws_id]["counter_ok"] and (own_accounts or rows[ws_id]["campaigns"]):
            problems.append(
                _problem(
                    "warning",
                    "counter",
                    "Счётчик Метрики недоступен",
                    f"Счётчик {ws.get('counter_id')} не виден ни одному подключению — визиты и цели не обновляются",
                    **where,
                )
            )

        if ws_id is not None and (setup := _setup_problem(ws, now, where)):
            problems.append(setup)
            rows[ws_id]["setup_missing"] = setup_missing(ws)

        problems.extend(_budget_problems(budget, where))

        if kpi["status"] in ("warn", "bad"):
            over = f"+{round((kpi['ratio'] - 1) * 100)}%"
            if kpi_type == "cpa":
                title = (
                    f"Цена заявки за 7 дней {rub(kpi['value'])} при цели {rub(target * 100)} ({over})"
                    if kpi["value"] is not None
                    else f"{rub(s['cost_7d'])} за 7 дней без заявок при цели {rub(target * 100)} за заявку"
                )
            else:
                title = f"Доля расходов на рекламу за 7 дней {kpi['value']:g}% при цели {target:g}% ({over})"
            # Плохой KPI — не пожар (деньги не стоят), но первое, что разбирать после пожаров.
            problem = _problem("warning" if kpi["status"] == "bad" else "info", "kpi", title, **where)
            # Для пачки одинаковых в «Где горит»: цена заявки и доля расходов — разные проблемы.
            problem["subkind"] = f"kpi_{kpi_type}"
            problems.append(problem)
        elif kpi_type == "drr" and s["cost_7d"] > 0 and not s["revenue_7d"]:
            problem = _problem(
                "info",
                "kpi",
                "Цель по доле расходов на рекламу задана, но дохода в отчётах Директа нет",
                "Проверьте ценность ключевых целей в кампаниях или передачу дохода из Метрики",
                **where,
            )
            problem["subkind"] = "kpi_no_revenue"
            problems.append(problem)

    # Папки-клиенты: общий месячный бюджет на все сайты клиента.
    folders: dict[int | None, dict] = {}
    for ws_id, row in rows.items():
        if ws_id is None:
            continue
        folder = folders.get(row["client_id"])
        if folder is None:
            client = clients_by_id.get(row["client_id"]) or {}
            folder = folders[row["client_id"]] = {
                "id": row["client_id"],
                "name": client.get("name") or NO_CLIENT,
                "notes": client.get("notes") or "",
                "workspace_ids": [],
                "_sums": _new_sums(),
                "_budget": client.get("monthly_budget_kopecks"),
            }
        folder["workspace_ids"].append(ws_id)
        for key, value in ws_sums[ws_id].items():
            folder["_sums"][key] += value
    for folder in folders.values():
        s = folder.pop("_sums")
        folder_budget = folder.pop("_budget")
        budget = budget_pace(
            folder_budget,
            s["mtd"],
            s["mtd_before_today"],
            today,
            profile=spend_profile(folder["workspace_ids"]) if folder_budget else None,
        )
        folder.update(_spend_fields(s))
        folder["budget"] = budget
        if folder["id"] is not None and len(folder["workspace_ids"]) > 1:
            problems.extend(
                _budget_problems(
                    budget,
                    {**where_many(folder["workspace_ids"]), "client_id": folder["id"], "client_name": folder["name"]},
                    label="Общий бюджет клиента",
                )
            )
        elif folder["id"] is not None and budget:
            # Один сайт у клиента — общий бюджет и есть бюджет сайта.
            problems.extend(_budget_problems(budget, where_ws(folder["workspace_ids"][0]), label="Бюджет клиента"))

    balances: list[dict] = []
    for a in accounts:
        ws_ids: list[int | None] = list(account_ws.get(a["id"]) or [None])
        where = where_many(ws_ids, account_id=a["id"], object_name=a["name"])
        balance = a["balance_kopecks"]
        cost_7d = account_cost_7d[a["id"]]
        balance_days = days_on_balance(balance, cost_7d)
        direct_url = direct_account_url(a["client_login"])
        ends = runs_out_on(balance, balance_days, today)
        # Блок «Деньги» на главной: каждый логин со своим счётом, даже если сигнала нет.
        balances.append(
            {
                "account_id": a["id"],
                "name": a["name"],
                "login": a["client_login"],
                "workspace_ids": [w for w in ws_ids if w is not None],
                "workspace_name": where["workspace_name"],
                "balance_kopecks": balance,
                "balance_currency": a["balance_currency"] or "",
                "balance_synced_at": _aware(a["balance_synced_at"]).isoformat() if a["balance_synced_at"] else None,
                "balance_error": a["balance_error"] or "",
                "spend_per_day_kopecks": round(cost_7d / 7),
                "days_on_balance": balance_days,
                "runs_out_on": ends.isoformat() if ends else None,
                "direct_url": direct_url,
            }
        )
        if balance is not None and cost_7d > 0:
            balance_problem = None
            if balance <= 0:
                balance_problem = _problem("critical", "balance", BALANCE_OUT_TITLE, rub(balance), **where)
            elif balance_days is not None and balance_days < BALANCE_WARNING_DAYS:
                severity = "critical" if balance_days < BALANCE_CRITICAL_DAYS else "warning"
                balance_problem = _problem(
                    severity,
                    "balance",
                    "Баланса хватит на " + f"{balance_days:g}".replace(".", ",") + " дн.",
                    f"{rub(balance)} при расходе ≈{rub(cost_7d / 7)} в день",
                    **where,
                )
            if balance_problem:
                # Кнопка сигнала «Пополнить в Директе» открывает кабинет именно этого логина.
                balance_problem["direct_url"] = direct_url
                balance_problem["days_on_balance"] = balance_days
                balance_problem["metric"] = {"balance": balance, "days": balance_days}  # снимок для snoozes
                problems.append(balance_problem)
        if a["stats_error"]:
            severity = "critical" if a["stats_error"] in _TOKEN_ERRORS else "warning"
            problems.append(
                _problem(
                    severity, "stats_error", "Не загружается статистика кабинета", plain_error(a["stats_error"]), **where
                )
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
                    "warning", "account_sync", "Не обновляются кампании кабинета", plain_error(a["last_sync_error"]), **where
                )
            )

    for c in data["connections"]:
        affected: set[int | None] = set()
        for a in accounts:
            if a["connection_id"] == c["id"]:
                affected.update(account_ws.get(a["id"]) or [None])
        affected.update(w["id"] for w in workspaces if w["counter_connection_id"] == c["id"])
        if not affected:
            continue
        where = where_many(list(affected), object_name=f"Яндекс-логин {c['yandex_login']}")
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
        if account_id not in accounts_by_id or c["state"] in ("ARCHIVED", "ENDED"):
            continue
        visible, ws_id = ws_of_campaign(account_id, c["campaign_id"])
        if not visible:
            continue
        key = (account_id, c["campaign_id"])
        where = where_ws(
            ws_id,
            account_id=account_id,
            campaign_row_id=c["id"],
            object_name=c["name"] or str(c["campaign_id"]),
        )
        if c["status"] == "REJECTED":
            problems.append(
                _problem(
                    "critical", "moderation", "Кампания отклонена модерацией", c.get("status_clarification") or "", **where
                )
            )
            problems[-1]["metric"] = {"reasons": [c.get("status_clarification") or ""]}
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
        rejected_list = list(c.get("rejected_list") or [])
        if c["rejected_ads"] and c["state"] == "ON":
            # В пояснении могут быть и быстрые ссылки или картинки принятых объявлений — заголовок о них тоже говорит.
            extras = " и дополнения" if any(r.get("extras") for r in rejected_list) else ""
            problems.append(
                _problem(
                    "warning",
                    "ads_rejected",
                    f"Отклонено объявлений: {c['rejected_ads']}{extras}",
                    rejected_detail(rejected_list),
                    **where,
                )
            )
            problems[-1]["metric"] = {"count": c["rejected_ads"], "reasons": rejected_reasons(rejected_list)}
        elif c["state"] == "ON" and any(r.get("extras") for r in rejected_list):
            # Объявление показывается, а быстрые ссылки или картинка — нет.
            problems.append(
                _problem("info", "ads_rejected", "Отклонены дополнения объявлений", rejected_detail(rejected_list), **where)
            )
            problems[-1]["metric"] = {"count": 0, "reasons": rejected_reasons(rejected_list)}
        if c["state"] == "ON" and key in campaign_history:
            row = rows.get(ws_id) or {}
            cpa_target = (
                row["kpi"]["target"] * 100
                if row and row["kpi"]["type"] == "cpa" and row["kpi"]["target"]
                else None
            )
            for anomaly in campaign_anomalies(
                campaign_history[key],
                yesterday,
                day_loaded=yesterday in loaded_days[account_id],
                cpa_target_kopecks=cpa_target,
            ):
                problems.append(_problem("warning", anomaly["kind"], anomaly["title"], anomaly["detail"], **where))

    problems.sort(
        key=lambda p: (SEVERITY_ORDER[p["severity"]], p["workspace_name"].lower(), p["object_name"].lower())
    )
    # Скрытое пользователем («⋯» у сигнала) — вне списков и счётчиков; строки «Скрыто» — отдельно.
    from app import problem_review, snoozes

    # Для утреннего разбора: скрытое — не решённое; серьёзность в записи — ухудшение тоже «новое».
    all_keys = {problem_review.entry(snoozes.problem_key(p), p["severity"]) for p in problems}
    problems, hidden_problems = snoozes.apply(
        user_id,
        problems,
        ws_client={w["id"]: w["client_row_id"] for w in workspaces},
        ws_names={w["id"]: w["name"] for w in workspaces},
        client_names={c["id"]: c["name"] for c in data["clients"]},
        now=now,
    )
    morning = None
    if review:
        morning = problem_review.apply(user_id, problems, all_keys, today)
    for row in [*rows.values(), *folders.values()]:
        row["problems"] = {"critical": 0, "warning": 0, "info": 0}
    for p in problems:
        for ws_id in p["workspace_ids"]:
            if ws_id in rows:
                rows[ws_id]["problems"][p["severity"]] += 1
    for folder in folders.values():
        for ws_id in folder["workspace_ids"]:
            for sev, n in rows[ws_id]["problems"].items():
                folder["problems"][sev] += n
    for row in [*rows.values(), *folders.values()]:
        counts = row["problems"]
        row["severity"] = next((sev for sev in ("critical", "warning", "info") if counts[sev]), "")
    # «Не настроен» на карточке простого обзора — пока виден сигнал «Проект не настроен» (свежий и не скрыт).
    shown_setup = {p["workspace_id"] for p in problems if p["kind"] == "setup"}
    for ws_id, row in rows.items():
        row["setup_missing"] = row.get("setup_missing", []) if ws_id in shown_setup else []

    totals = defaultdict(int)
    for row in rows.values():
        for key in ("spend_today", "spend_yesterday", "conversions_yesterday", "spend_7d", "conversions_7d"):
            totals[key] += row[key]
    ordered_folders = sorted(folders.values(), key=lambda f: (f["id"] is None, f["name"].lower()))
    # Две недели по дням до вчера — спарклайны карточек обзора (первые 7 дней — «было»).
    by_day = []
    for i in range(13, -1, -1):
        d = yesterday - timedelta(days=i)
        cost, conv = day_totals.get(d, (0, 0))
        by_day.append({"day": d.isoformat(), "cost_kopecks": cost, "conversions": conv})
    return {
        "today": today.isoformat(),
        "yesterday": yesterday.isoformat(),
        "today_updated_at": today_updated.isoformat() if today_updated else None,
        "workspaces": list(rows.values()),
        "folders": ordered_folders,
        "totals": dict(totals),
        "by_day": by_day,
        "problems": problems,
        "hidden": hidden_problems,
        # Утренний разбор: {since, new, resolved}; None — снимка за прошлые дни нет (или не просили).
        "review": morning,
        # Деньги на счетах по логинам: кончились и меньше всего дней — сверху.
        "balances": sorted(balances, key=balance_order),
    }
