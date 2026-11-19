"""Бюджет одной кампании: настоящая сумма (в неделю или в день), сколько из неё уже потрачено, и правка одним числом.

Сумма берётся из сохранённой стратегии кампании (синк кладёт BiddingStrategy как есть), без запроса в Директ.
Правка идёт тем же путём, что массовая смена бюджетов: настройки кампании в Директе + одна запись
в «Истории» с откатом.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app import actions
from app.budgets import MIN_DAILY_RUB, MIN_WEEKLY_RUB, budget_kind, bulk_budget
from app.clock import MSK, today_msk
from app.db import pool
from app.yandex import YandexError

# НДС: расход в отчётах мы берём с НДС (IncludeVAT=YES), а лимит стратегии в Директе задаётся без НДС.
# Долю бюджета считаем по расходу без НДС — иначе при полностью потраченном лимите вышло бы 122 %.
VAT_RATE = 0.22


def net_of_vat(kopecks: int | None) -> int | None:
    """Сумма без НДС из суммы с НДС (в копейках)."""
    return None if kopecks is None else round(kopecks / (1 + VAT_RATE))


def budget_state(row: dict) -> dict:
    """Вид бюджета по строке direct_campaigns: kind (weekly/daily/None), сумма в ₽, можно ли менять, минимум."""
    ctype = str(row.get("campaign_type") or "")
    strategy = actions.strategy_from_api(row.get("bidding_strategy") or {})
    daily_kopecks = row.get("daily_budget_kopecks")
    daily_rub = daily_kopecks / 100 if daily_kopecks else None
    kind = budget_kind({"strategy": strategy, "type": ctype, "daily_budget_rub": daily_rub}) or None
    if kind == "weekly":
        amount = strategy.get("weekly_budget_rub") or None
    elif kind == "daily":
        amount = daily_rub
    else:
        amount = None
    archived = str(row.get("state") or "").upper() == "ARCHIVED"
    return {
        "kind": kind,
        "amount_rub": amount,
        "editable": bool(kind) and ctype in actions._TYPE_BLOCK and not archived,
        "min_rub": MIN_WEEKLY_RUB if kind == "weekly" else MIN_DAILY_RUB,
        "strategy_label": actions.strategy_label(strategy) if strategy.get("search") else "",
    }


def budget_period(kind: str | None, today: date) -> tuple[date, date]:
    """Период расхода: недельный бюджет Директа считается с понедельника по воскресенье, дневной — за сегодня."""
    if kind == "weekly":
        return today - timedelta(days=today.weekday()), today
    return today, today


def _campaign_row(user_id: int, campaign_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, account_id, campaign_id, name, campaign_type, state,
                   daily_budget_kopecks, bidding_strategy
            FROM direct_campaigns
            WHERE id = %s AND user_id = %s
            """,
            (campaign_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return row


def budget_info(user_id: int, campaign_row_id: int, today: date | None = None) -> dict:
    """Бюджет кампании и расход за текущую неделю (или сегодня, если бюджет дневной).

    has_data — по кабинету есть статистика за период или её сегодня загружали без ошибки (строк нет —
    кабинет не тратил, честные 0 ₽). Иначе процент не показываем: утром понедельника до загрузки
    «потрачено 0 %» было бы неправдой. spent_kopecks — с НДС, как везде в сервисе; spent_net_kopecks —
    без НДС, для сравнения с лимитом Директа.
    """
    today = today or today_msk()
    row = _campaign_row(user_id, campaign_row_id)
    state = budget_state(row)
    date_from, date_to = budget_period(state["kind"], today)
    with pool.connection() as conn:
        stats = conn.execute(
            """
            SELECT COALESCE(SUM(s.cost_kopecks) FILTER (WHERE s.campaign_id = %(campaign_id)s), 0) AS spent,
                   COUNT(*) AS account_rows,
                   MAX(s.updated_at) AS updated_at,
                   (SELECT a.stats_synced_at FROM direct_accounts a
                     WHERE a.id = %(account_id)s AND a.user_id = %(user_id)s) AS synced_at
            FROM stats_daily s
            WHERE s.user_id = %(user_id)s AND s.source = 'direct' AND s.account_id = %(account_id)s
              AND s.day >= %(date_from)s AND s.day <= %(date_to)s
            """,
            {
                "user_id": user_id,
                "account_id": row["account_id"],
                "campaign_id": row["campaign_id"],
                "date_from": date_from,
                "date_to": date_to,
            },
        ).fetchone()
    synced_at = stats["synced_at"] if stats else None
    # Отчёт Директа не отдаёт строк кампаниям без показов: после сегодняшней загрузки пустой период — это 0 ₽.
    fresh = synced_at is not None and synced_at.astimezone(MSK).date() >= today
    has_data = bool(stats and stats["account_rows"]) or fresh
    spent = int(stats["spent"]) if has_data else None
    updated = (stats["updated_at"] or synced_at) if has_data else None
    return {
        "campaign_row_id": row["id"],
        "name": row["name"],
        **state,
        "period": "week" if state["kind"] == "weekly" else "day",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "spent_kopecks": spent,
        "spent_net_kopecks": net_of_vat(spent),
        "has_data": has_data,
        "updated_at": updated.isoformat() if updated else None,
    }


def budget_change_args(kind: str | None, amount_rub) -> dict:
    """Аргументы bulk_budget для одной суммы в единице кампании. Меньше минимума Директа — понятная ошибка."""
    if kind not in ("weekly", "daily"):
        raise YandexError("strategy", "Бюджет этой кампании задаётся в Директе")
    try:
        amount = float(amount_rub)
    except (TypeError, ValueError):
        raise YandexError("budget", "Укажите сумму") from None
    if not math.isfinite(amount):
        raise YandexError("budget", "Укажите сумму")
    if kind == "weekly":
        if amount < MIN_WEEKLY_RUB:
            raise YandexError("budget", f"Директ не примет недельный бюджет меньше {MIN_WEEKLY_RUB} ₽")
        return {"weekly_budget_rub": amount}
    if amount < MIN_DAILY_RUB:
        raise YandexError("budget", f"Директ не примет дневной бюджет меньше {MIN_DAILY_RUB} ₽")
    return {"daily_budget_rub": amount}


def change_budget(
    user_id: int,
    workspace_id: int | None,
    campaign_row_id: int,
    *,
    weekly_budget_rub: float | None = None,
    daily_budget_rub: float | None = None,
) -> dict:
    """Новый бюджет одной кампании. Запись в «Истории» с откатом пишет bulk_budget (если сумма поменялась)."""
    if (weekly_budget_rub is None) == (daily_budget_rub is None):
        raise YandexError("budget", "Укажите одну сумму: в неделю или в день")
    row = _campaign_row(user_id, campaign_row_id)
    state = budget_state(row)
    if not state["editable"]:
        raise YandexError("unsupported", "Бюджет этой кампании меняется только в Директе")
    if weekly_budget_rub is not None:
        args = budget_change_args("weekly", weekly_budget_rub)
    else:
        args = budget_change_args("daily", daily_budget_rub)
    result = bulk_budget(user_id, campaign_ids=[campaign_row_id], workspace_id=workspace_id, **args)
    item = (result.get("items") or [None])[0]
    return {"item": item, "changed": bool(result.get("applied")), "info": budget_info(user_id, campaign_row_id)}
