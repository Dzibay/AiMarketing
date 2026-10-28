"""Правила автоматизации кампаний и ключевых фраз."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.actions import bulk_campaign_action
from app.budgets import BULK_LIMIT as BUDGET_BULK_LIMIT
from app.budgets import bulk_budget
from app.clock import today_msk, yesterday_msk
from app.db import pool
from app.journal import acting_as
from app.keywords import bulk_keyword_action
from app.search_queries import bulk_search_query_action
from app.strategy_info import budget_brief
from app.telegram_notify import format_rule_alert, notify_user
from app.yandex import YandexError


def _rub(kopecks) -> str:
    """Копейки → «3 240 ₽» (до копеек только если они есть и сумма меньше 100 ₽)."""
    value = float(kopecks) / 100
    text = f"{value:,.2f}" if value < 100 and round(value, 2) != round(value) else f"{value:,.0f}"
    return text.replace(",", "\u00a0").replace(".", ",") + "\u00a0₽"


_CONV_NOTE = {"цель": "по цели", "цель~": "по цели, оценка", "конв.": "по конверсиям Директа"}

log = logging.getLogger("app.rules")

RULE_TYPES = {"cpa_above", "cpa_below", "spend_no_conv", "daily_budget_pct", "weekly_budget_pct", "cpc_above"}
RULE_ACTIONS = {
    "pause",
    "resume",
    "set_budget",
    "set_weekly_budget",
    "change_budget_pct",
    "set_bid",
    "notify",
    "negative_adgroup",
    "negative_campaign",
}
RULE_TARGETS = {"campaign", "keyword", "query"}
CAMPAIGN_ONLY_TYPES = {"daily_budget_pct", "weekly_budget_pct"}
CAMPAIGN_ONLY_ACTIONS = {"set_budget", "set_weekly_budget", "change_budget_pct"}
# Сумма в action_budget_rub обязательна: дневной или недельный бюджет.
BUDGET_ACTIONS = {"set_budget", "set_weekly_budget"}
KEYWORD_ONLY_ACTIONS = {"set_bid"}
QUERY_ONLY_ACTIONS = {"negative_adgroup", "negative_campaign"}
QUERY_ALLOWED_ACTIONS = {"negative_adgroup", "negative_campaign", "notify"}
QUERY_ALLOWED_TYPES = {"spend_no_conv", "cpc_above", "cpa_above"}


def create_rule(
    user_id: int,
    *,
    name: str,
    rule_type: str,
    threshold_value: float,
    lookback_days: int = 7,
    action: str = "pause",
    dry_run: bool = True,
    scope_account_id: int | None = None,
    scope_workspace_id: int | None = None,
    cooldown_hours: int = 24,
    goal_row_id: int | None = None,
    action_budget_rub: float | None = None,
    action_bid_rub: float | None = None,
    target: str = "campaign",
    action_pct: float | None = None,
) -> dict:
    target_n = (target or "campaign").strip() or "campaign"
    if target_n not in RULE_TARGETS:
        raise YandexError("rule-type")
    if rule_type not in RULE_TYPES:
        raise YandexError("rule-type")
    if action not in RULE_ACTIONS:
        raise YandexError("action")
    _validate_target_combo(target_n, rule_type, action)
    lookback_days = max(1, min(int(lookback_days), 30))
    cooldown_hours = max(1, min(int(cooldown_hours), 168))
    goal_row_id = _validate_goal(user_id, goal_row_id) if target_n == "campaign" else None
    if target_n == "query" and action not in QUERY_ALLOWED_ACTIONS:
        raise YandexError("action")
    budget = _validate_budget(action, action_budget_rub)
    bid = _validate_bid(action, action_bid_rub)
    pct = _validate_pct(action, action_pct)
    scope_workspace_id = _validate_workspace(user_id, scope_workspace_id)
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO automation_rules (
                user_id, name, enabled, dry_run, scope_account_id,
                rule_type, threshold_value, lookback_days, action, cooldown_hours,
                goal_row_id, action_budget_rub, target, action_bid_rub, scope_workspace_id, action_pct
            )
            VALUES (%s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                user_id,
                (name or rule_type)[:120],
                dry_run,
                scope_account_id,
                rule_type,
                Decimal(str(threshold_value)),
                lookback_days,
                action,
                cooldown_hours,
                goal_row_id,
                budget,
                target_n,
                bid,
                scope_workspace_id,
                pct,
            ),
        ).fetchone()
    return _public_rule(row)


def _validate_workspace(user_id: int, workspace_id: int | None) -> int | None:
    if not workspace_id:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id FROM workspaces WHERE id = %s AND user_id = %s",
            (workspace_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return int(row["id"])


def list_rules(user_id: int, *, workspace_id: int | None = None) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT r.*, g.name AS goal_name, g.goal_id AS yandex_goal_id, w.name AS workspace_name,
                   COALESCE(NULLIF(a.name, ''), a.client_login) AS account_name
            FROM automation_rules r
            LEFT JOIN metrika_goals g ON g.id = r.goal_row_id
            LEFT JOIN workspaces w ON w.id = r.scope_workspace_id
            LEFT JOIN direct_accounts a ON a.id = r.scope_account_id
            WHERE r.user_id = %s
              AND (%s::bigint IS NULL OR r.scope_workspace_id = %s)
            ORDER BY r.id DESC
            """,
            (user_id, workspace_id, workspace_id),
        ).fetchall()
    return [_public_rule(r) for r in rows]


def update_rule(user_id: int, rule_id: int, **fields) -> dict:
    allowed = {
        "name",
        "enabled",
        "dry_run",
        "threshold_value",
        "lookback_days",
        "cooldown_hours",
        "action",
        "goal_row_id",
        "action_budget_rub",
        "action_bid_rub",
        "action_pct",
        "scope_account_id",
        "scope_workspace_id",
        "target",
        "rule_type",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        raise YandexError("empty")
    with pool.connection() as conn:
        current = conn.execute(
            "SELECT * FROM automation_rules WHERE id = %s AND user_id = %s",
            (rule_id, user_id),
        ).fetchone()
    if current is None:
        raise YandexError("not-found")
    if "action" in updates and updates["action"] is not None:
        if updates["action"] not in RULE_ACTIONS:
            raise YandexError("action")
    if "rule_type" in updates and updates["rule_type"] is not None:
        if updates["rule_type"] not in RULE_TYPES:
            raise YandexError("rule-type")
    if "target" in updates and updates["target"] is not None:
        if updates["target"] not in RULE_TARGETS:
            raise YandexError("rule-type")
    if "scope_workspace_id" in updates:
        updates["scope_workspace_id"] = _validate_workspace(user_id, updates["scope_workspace_id"])
    target_n = updates.get("target") or current.get("target") or "campaign"
    rule_type_n = updates.get("rule_type") or current.get("rule_type")
    action_n = updates.get("action") or current.get("action") or "pause"
    _validate_target_combo(target_n, rule_type_n, action_n)
    if "goal_row_id" in updates:
        updates["goal_row_id"] = (
            None
            if target_n in ("keyword", "query")
            else _validate_goal(user_id, updates["goal_row_id"])
        )
    if "action" in updates or "action_budget_rub" in updates:
        action = updates.get("action")
        if action is None:
            action = current.get("action") or "pause"
        if "action_budget_rub" in updates:
            updates["action_budget_rub"] = _validate_budget(action, updates["action_budget_rub"])
        elif action in BUDGET_ACTIONS:
            with pool.connection() as conn:
                cur = conn.execute(
                    "SELECT action_budget_rub FROM automation_rules WHERE id = %s AND user_id = %s",
                    (rule_id, user_id),
                ).fetchone()
            if not cur or cur.get("action_budget_rub") is None:
                raise YandexError("budget")
    if "action" in updates or "action_bid_rub" in updates:
        action = updates.get("action") or current.get("action") or "pause"
        if "action_bid_rub" in updates:
            updates["action_bid_rub"] = _validate_bid(action, updates["action_bid_rub"])
        elif action == "set_bid" and current.get("action_bid_rub") is None:
            raise YandexError("budget")
    if "action" in updates or "action_pct" in updates:
        action = updates.get("action") or current.get("action") or "pause"
        updates["action_pct"] = _validate_pct(
            action, updates["action_pct"] if "action_pct" in updates else current.get("action_pct")
        )
    sets = []
    params: list = []
    for key, value in updates.items():
        sets.append(f"{key} = %s")
        params.append(value)
    sets.append("updated_at = NOW()")
    params.extend([rule_id, user_id])
    with pool.connection() as conn:
        row = conn.execute(
            f"""
            UPDATE automation_rules SET {', '.join(sets)}
            WHERE id = %s AND user_id = %s
            RETURNING *
            """,
            params,
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return _public_rule(row)


def delete_rule(user_id: int, rule_id: int) -> None:
    with pool.connection() as conn:
        row = conn.execute(
            "DELETE FROM automation_rules WHERE id = %s AND user_id = %s RETURNING id",
            (rule_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")


def list_rule_runs(user_id: int, limit: int = 30) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, rule_id, dry_run, matched, applied, details, error, created_at
            FROM rule_runs
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, max(1, min(limit, 100))),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "rule_id": r["rule_id"],
            "dry_run": r["dry_run"],
            "matched": r["matched"],
            "applied": r["applied"],
            "details": r["details"],
            "error": r.get("error") or "",
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]


def _validate_goal(user_id: int, goal_row_id: int | None) -> int | None:
    if goal_row_id is None or goal_row_id == 0:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id FROM metrika_goals WHERE id = %s AND user_id = %s",
            (goal_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return int(goal_row_id)


def _validate_target_combo(target: str, rule_type: str, action: str) -> None:
    if target == "keyword" and rule_type in CAMPAIGN_ONLY_TYPES:
        raise YandexError("rule-type")
    if target == "keyword" and action in CAMPAIGN_ONLY_ACTIONS | QUERY_ONLY_ACTIONS:
        raise YandexError("action")
    if target == "campaign" and action in KEYWORD_ONLY_ACTIONS | QUERY_ONLY_ACTIONS:
        raise YandexError("action")
    if target == "query":
        if rule_type not in QUERY_ALLOWED_TYPES:
            raise YandexError("rule-type")
        if action not in QUERY_ALLOWED_ACTIONS:
            raise YandexError("action")


def _validate_budget(action: str, action_budget_rub: float | None) -> Decimal | None:
    if action not in BUDGET_ACTIONS:
        return None if action_budget_rub in (None, "") else Decimal(str(action_budget_rub))
    if action_budget_rub is None or float(action_budget_rub) <= 0:
        raise YandexError("budget")
    if float(action_budget_rub) < 300:
        raise YandexError("budget", "Бюджет меньше 300 ₽ Директ не примет")
    return Decimal(str(action_budget_rub))


def _validate_pct(action: str, action_pct: float | None) -> Decimal | None:
    """Изменение бюджета в %: от −90 до +500, не ноль. У других действий процента нет."""
    if action != "change_budget_pct":
        return None
    try:
        value = float(action_pct)
    except (TypeError, ValueError) as exc:
        raise YandexError("budget", "Укажите, на сколько процентов менять бюджет") from exc
    if value == 0 or not -90 <= value <= 500:
        raise YandexError("budget", "Изменение бюджета — от −90 % до +500 %, не ноль")
    return Decimal(str(value))


def _validate_bid(action: str, action_bid_rub: float | None) -> Decimal | None:
    if action != "set_bid":
        return None if action_bid_rub in (None, "") else Decimal(str(action_bid_rub))
    if action_bid_rub is None or float(action_bid_rub) <= 0:
        raise YandexError("budget")
    return Decimal(str(action_bid_rub))


def _public_rule(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": row["enabled"],
        "dry_run": row["dry_run"],
        "target": row.get("target") or "campaign",
        "scope_account_id": row["scope_account_id"],
        "scope_workspace_id": row.get("scope_workspace_id"),
        "workspace_name": row.get("workspace_name") or "",
        "account_name": row.get("account_name") or "",
        "rule_type": row["rule_type"],
        "threshold_value": float(row["threshold_value"] or 0),
        "lookback_days": row["lookback_days"],
        "action": row["action"],
        "cooldown_hours": row["cooldown_hours"],
        "goal_row_id": row.get("goal_row_id"),
        "goal_name": row.get("goal_name") or "",
        "yandex_goal_id": row.get("yandex_goal_id"),
        "action_budget_rub": float(row["action_budget_rub"])
        if row.get("action_budget_rub") is not None
        else None,
        "action_bid_rub": float(row["action_bid_rub"])
        if row.get("action_bid_rub") is not None
        else None,
        "action_pct": float(row["action_pct"]) if row.get("action_pct") is not None else None,
        "last_run_at": row["last_run_at"].isoformat() if row.get("last_run_at") else None,
    }


def _goal_meta(user_id: int, goal_row_id: int | None) -> dict | None:
    if not goal_row_id:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT g.id, g.goal_id, g.counter_row_id, g.name
            FROM metrika_goals g
            WHERE g.id = %s AND g.user_id = %s
            """,
            (goal_row_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def _matching_campaigns(user_id: int, rule: dict) -> list[dict]:
    lookback = int(rule["lookback_days"])
    date_from = today_msk() - timedelta(days=lookback)
    date_to = yesterday_msk()
    threshold = Decimal(str(rule["threshold_value"]))
    account_id = rule.get("scope_account_id")
    workspace_id = rule.get("scope_workspace_id")
    goal = _goal_meta(user_id, rule.get("goal_row_id"))
    yandex_goal_id = int(goal["goal_id"]) if goal else None

    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id AS row_id, c.campaign_id, c.name, c.account_id, c.state, c.bidding_strategy,
                   COALESCE(SUM(s.cost_kopecks), 0) AS cost_kopecks,
                   COALESCE(SUM(s.conversions), 0) AS conversions,
                   COALESCE(SUM(s.clicks), 0) AS clicks
            FROM direct_campaigns c
            LEFT JOIN stats_daily s
              ON s.source = 'direct'
             AND s.account_id = c.account_id
             AND s.campaign_id = c.campaign_id
             AND s.user_id = c.user_id
             AND s.day >= %s AND s.day <= %s
            WHERE c.user_id = %s
              AND (%s::bigint IS NULL OR c.account_id = %s)
              AND (%s::bigint IS NULL OR c.id IN (
                    SELECT campaign_row_id FROM workspace_campaigns WHERE workspace_id = %s))
              AND (
                (%s = 'resume' AND c.state = 'SUSPENDED')
                OR (%s <> 'resume' AND c.state = 'ON')
              )
            GROUP BY c.id, c.campaign_id, c.name, c.account_id, c.state, c.bidding_strategy
            """,
            (
                date_from,
                date_to,
                user_id,
                account_id,
                account_id,
                workspace_id,
                workspace_id,
                rule["action"],
                rule["action"],
            ),
        ).fetchall()

        goal_by_campaign: dict[tuple[int, int], int] = {}
        goal_by_account: dict[int, int] = {}
        if yandex_goal_id is not None:
            # Достижения цели по кампании — из счётчика пространства, где лежит кампания.
            g_rows = conn.execute(
                """
                SELECT c.campaign_id, s.goal_reaches, c.account_id
                FROM stats_daily s
                JOIN workspaces w ON w.counter_row_id = s.counter_row_id AND w.user_id = s.user_id
                JOIN workspace_campaigns wc ON wc.workspace_id = w.id
                JOIN direct_campaigns c ON c.id = wc.campaign_row_id AND c.campaign_id = s.campaign_id
                WHERE s.user_id = %s AND s.source = 'metrika_goal'
                  AND s.goal_id = %s
                  AND s.day >= %s AND s.day <= %s
                  AND s.campaign_id IS NOT NULL
                  AND (%s::bigint IS NULL OR c.account_id = %s)
                """,
                (user_id, yandex_goal_id, date_from, date_to, account_id, account_id),
            ).fetchall()
            for g in g_rows:
                key = (int(g["account_id"]), int(g["campaign_id"]))
                goal_by_campaign[key] = goal_by_campaign.get(key, 0) + int(g["goal_reaches"] or 0)

            a_rows = conn.execute(
                """
                SELECT wa.account_id, COALESCE(SUM(s.goal_reaches), 0) AS goal_reaches
                FROM stats_daily s
                JOIN workspaces w ON w.counter_row_id = s.counter_row_id AND w.user_id = s.user_id
                JOIN workspace_accounts wa ON wa.workspace_id = w.id
                WHERE s.user_id = %s AND s.source = 'metrika_goal'
                  AND s.goal_id = %s
                  AND s.day >= %s AND s.day <= %s
                  AND s.campaign_id IS NULL
                  AND (%s::bigint IS NULL OR wa.account_id = %s)
                GROUP BY wa.account_id
                """,
                (user_id, yandex_goal_id, date_from, date_to, account_id, account_id),
            ).fetchall()
            for a in a_rows:
                goal_by_account[int(a["account_id"])] = int(a["goal_reaches"] or 0)

    week_spend = _week_spend(user_id, [int(r["row_id"]) for r in rows]) if rule["rule_type"] == "weekly_budget_pct" else {}

    # Если по кампаниям цели нет — распределяем account total пропорционально расходу
    account_costs: dict[int, Decimal] = {}
    for row in rows:
        aid = int(row["account_id"])
        account_costs[aid] = account_costs.get(aid, Decimal(0)) + Decimal(row["cost_kopecks"] or 0)

    matched = []
    for row in rows:
        cost = Decimal(row["cost_kopecks"] or 0)
        if yandex_goal_id is not None:
            key = (int(row["account_id"]), int(row["campaign_id"]))
            if key in goal_by_campaign:
                conv = Decimal(goal_by_campaign[key])
                conv_label = "цель"
            else:
                acct_goals = Decimal(goal_by_account.get(int(row["account_id"]), 0))
                acct_cost = account_costs.get(int(row["account_id"]), Decimal(0))
                if acct_cost > 0 and acct_goals > 0:
                    conv = (acct_goals * cost / acct_cost).quantize(Decimal("0.01"))
                    conv_label = "цель~"
                else:
                    conv = Decimal(0)
                    conv_label = "цель"
        else:
            conv = Decimal(row["conversions"] or 0)
            conv_label = "конв."

        cpa = (cost / conv) if conv > 0 else None
        rule_type = rule["rule_type"]
        hit = False
        reason = ""
        if rule_type == "cpa_above":
            if cpa is not None and cpa >= threshold * 100:
                hit = True
                reason = f"CPA {_rub(cpa)} — порог {_rub(threshold * 100)} ({_CONV_NOTE[conv_label]})"
        elif rule_type == "cpa_below":
            if cpa is not None and cpa <= threshold * 100:
                hit = True
                reason = f"CPA {_rub(cpa)} — не выше {_rub(threshold * 100)} ({_CONV_NOTE[conv_label]})"
        elif rule_type == "spend_no_conv":
            if conv == 0 and cost >= threshold * 100:
                hit = True
                reason = f"расход {_rub(cost)} без конверсий ({_CONV_NOTE[conv_label]})"
        elif rule_type == "weekly_budget_pct":
            weekly = budget_brief(row.get("bidding_strategy"))["weekly_budget_rub"]
            spent = week_spend.get(int(row["row_id"]), Decimal(0))
            # Копейки на рубли бюджета — это и есть проценты.
            share = spent / Decimal(str(weekly)) if weekly else Decimal(0)
            if weekly and share >= threshold:
                hit = True
                reason = f"с понедельника израсходовано {_rub(spent)} — {float(share):.0f}% недельного бюджета {_rub(weekly * 100)}"
        elif rule_type == "daily_budget_pct":
            with pool.connection() as conn:
                camp = conn.execute(
                    "SELECT daily_budget_kopecks FROM direct_campaigns WHERE id = %s",
                    (row["row_id"],),
                ).fetchone()
                yday = conn.execute(
                    """
                    SELECT COALESCE(SUM(cost_kopecks), 0) AS cost
                    FROM stats_daily
                    WHERE user_id = %s AND source = 'direct'
                      AND account_id = %s AND campaign_id = %s AND day = %s
                    """,
                    (user_id, row["account_id"], row["campaign_id"], date_to),
                ).fetchone()
            budget = Decimal((camp or {}).get("daily_budget_kopecks") or 0)
            ycost = Decimal((yday or {}).get("cost") or 0)
            if budget > 0 and (ycost / budget) * 100 >= threshold:
                hit = True
                reason = f"расход {float(ycost/budget*100):.0f}% дневного бюджета"
        elif rule_type == "cpc_above":
            clicks = Decimal(row["clicks"] or 0)
            if clicks > 0:
                cpc = cost / clicks
                if cpc >= threshold * 100:
                    hit = True
                    reason = f"CPC {_rub(cpc)} — порог {_rub(threshold * 100)}"
        if hit:
            matched.append(
                {
                    "row_id": row["row_id"],
                    "campaign_id": row["campaign_id"],
                    "name": row["name"],
                    "account_id": row["account_id"],
                    "reason": reason,
                    "cost_kopecks": int(cost),
                    "conversions": float(conv) if conv != int(conv) else int(conv),
                    "entity": "campaign",
                }
            )
    return matched


def _week_spend(user_id: int, row_ids: list[int]) -> dict[int, Decimal]:
    """Расход кампаний с понедельника текущей недели по вчера (МСК), копейки. В понедельник — пусто:
    недельный бюджет Директа считается с понедельника, а сегодняшнего расхода в статистике нет."""
    today = today_msk()
    monday = today - timedelta(days=today.weekday())
    if not row_ids or monday >= today:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, COALESCE(SUM(s.cost_kopecks), 0) AS cost
            FROM direct_campaigns c
            JOIN stats_daily s ON s.source = 'direct' AND s.user_id = c.user_id
             AND s.account_id = c.account_id AND s.campaign_id = c.campaign_id
            WHERE c.user_id = %s AND c.id = ANY(%s) AND s.day >= %s AND s.day < %s
            GROUP BY c.id
            """,
            (user_id, row_ids, monday, today),
        ).fetchall()
    return {int(r["id"]): Decimal(r["cost"] or 0) for r in rows}


def _matching_keywords(user_id: int, rule: dict) -> list[dict]:
    """Совпадения по period_* статистики ключей (после синка на странице кампании)."""
    threshold = Decimal(str(rule["threshold_value"]))
    account_id = rule.get("scope_account_id")
    workspace_id = rule.get("scope_workspace_id")
    action = rule.get("action") or "pause"
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT k.id AS row_id, k.keyword_id, k.keyword, k.account_id, k.state,
                   k.period_cost_kopecks AS cost_kopecks,
                   k.period_conversions AS conversions,
                   k.period_clicks AS clicks,
                   k.period_impressions AS impressions,
                   g.name AS adgroup_name, c.name AS campaign_name
            FROM direct_keywords k
            JOIN direct_adgroups g ON g.id = k.adgroup_row_id
            JOIN direct_campaigns c ON c.id = g.campaign_row_id
            WHERE k.user_id = %s
              AND (%s::bigint IS NULL OR k.account_id = %s)
              AND (%s::bigint IS NULL OR g.campaign_row_id IN (
                    SELECT campaign_row_id FROM workspace_campaigns WHERE workspace_id = %s))
              AND (
                (%s = 'resume' AND k.state = 'SUSPENDED')
                OR (%s <> 'resume' AND (k.state = 'ON' OR k.state = '' OR k.state IS NULL))
              )
              AND COALESCE(k.period_impressions, 0) + COALESCE(k.period_clicks, 0)
                  + COALESCE(k.period_cost_kopecks, 0) > 0
            ORDER BY k.period_cost_kopecks DESC NULLS LAST
            LIMIT 500
            """,
            (user_id, account_id, account_id, workspace_id, workspace_id, action, action),
        ).fetchall()

    matched = []
    rule_type = rule["rule_type"]
    for row in rows:
        cost = Decimal(row["cost_kopecks"] or 0)
        conv = Decimal(row["conversions"] or 0)
        clicks = Decimal(row["clicks"] or 0)
        hit = False
        reason = ""
        if rule_type == "cpa_above":
            if conv > 0:
                cpa = cost / conv
                if cpa >= threshold * 100:
                    hit = True
                    reason = f"CPA {_rub(cpa)} — порог {_rub(threshold * 100)}"
        elif rule_type == "cpa_below":
            if conv > 0:
                cpa = cost / conv
                if cpa <= threshold * 100:
                    hit = True
                    reason = f"CPA {_rub(cpa)} — не выше {_rub(threshold * 100)}"
        elif rule_type == "spend_no_conv":
            if conv == 0 and cost >= threshold * 100:
                hit = True
                reason = f"расход {_rub(cost)} без конверсий"
        elif rule_type == "cpc_above":
            if clicks > 0:
                cpc = cost / clicks
                if cpc >= threshold * 100:
                    hit = True
                    reason = f"CPC {_rub(cpc)} — порог {_rub(threshold * 100)}"
        if hit:
            matched.append(
                {
                    "row_id": row["row_id"],
                    "keyword_id": row["keyword_id"],
                    "name": row["keyword"] or f"#{row['keyword_id']}",
                    "account_id": row["account_id"],
                    "reason": reason,
                    "cost_kopecks": int(cost),
                    "conversions": int(conv),
                    "adgroup_name": row.get("adgroup_name") or "",
                    "campaign_name": row.get("campaign_name") or "",
                    "entity": "keyword",
                }
            )
    return matched


def _matching_queries(user_id: int, rule: dict) -> list[dict]:
    """Совпадения по синхронизированным поисковым запросам."""
    threshold = Decimal(str(rule["threshold_value"]))
    account_id = rule.get("scope_account_id")
    workspace_id = rule.get("scope_workspace_id")
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT q.id AS row_id, q.query, q.account_id, q.adgroup_row_id,
                   q.clicks, q.cost_kopecks, q.conversions,
                   g.name AS adgroup_name, c.name AS campaign_name
            FROM direct_search_queries q
            LEFT JOIN direct_adgroups g ON g.id = q.adgroup_row_id
            JOIN direct_campaigns c ON c.id = q.campaign_row_id
            WHERE q.user_id = %s
              AND (%s::bigint IS NULL OR q.account_id = %s)
              AND (%s::bigint IS NULL OR q.campaign_row_id IN (
                    SELECT campaign_row_id FROM workspace_campaigns WHERE workspace_id = %s))
              AND COALESCE(q.clicks, 0) + COALESCE(q.cost_kopecks, 0) > 0
            ORDER BY q.cost_kopecks DESC NULLS LAST
            LIMIT 500
            """,
            (user_id, account_id, account_id, workspace_id, workspace_id),
        ).fetchall()

    matched = []
    rule_type = rule["rule_type"]
    for row in rows:
        cost = Decimal(row["cost_kopecks"] or 0)
        conv = Decimal(row["conversions"] or 0)
        clicks = Decimal(row["clicks"] or 0)
        hit = False
        reason = ""
        if rule_type == "spend_no_conv":
            if conv == 0 and cost >= threshold * 100:
                hit = True
                reason = f"расход {_rub(cost)} без конверсий"
        elif rule_type == "cpc_above":
            if clicks > 0:
                cpc = cost / clicks
                if cpc >= threshold * 100:
                    hit = True
                    reason = f"CPC {_rub(cpc)} — порог {_rub(threshold * 100)}"
        elif rule_type == "cpa_above":
            if conv > 0:
                cpa = cost / conv
                if cpa >= threshold * 100:
                    hit = True
                    reason = f"CPA {_rub(cpa)} — порог {_rub(threshold * 100)}"
        if hit:
            matched.append(
                {
                    "row_id": row["row_id"],
                    "name": row["query"] or f"#{row['row_id']}",
                    "account_id": row["account_id"],
                    "reason": reason,
                    "cost_kopecks": int(cost),
                    "conversions": int(conv),
                    "adgroup_name": row.get("adgroup_name") or "",
                    "campaign_name": row.get("campaign_name") or "",
                    "adgroup_row_id": row.get("adgroup_row_id"),
                    "entity": "query",
                }
            )
    return matched


# После таких ошибок остальные пачки тоже не пройдут.
_STOP_CODES = frozenset({"units", "token-expired", "unauthorized", "direct-denied", "oauth-unavailable"})


def _result_error(result: dict) -> str:
    """Первая ошибка частично применённой пачки: bulk-функции не бросают, если что-то прошло."""
    errors = result.get("errors")
    if isinstance(errors, int):  # поисковые запросы отдают только число неудач
        return f"не применено: {errors}" if errors else ""
    for item in errors or []:
        if isinstance(item, dict):
            text = item.get("message") or item.get("error") or ""
            name = item.get("name") or item.get("query") or ""
            return f"«{name}»: {text}" if name and text else text
        if item:
            return str(item)
    return str(result.get("error") or "")


def _in_chunks(ids: list[int], size: int, run) -> tuple[int, str]:
    """Пачки по size: применённое по успешным пачкам суммируется, первая ошибка сохраняется."""
    applied, error = 0, ""
    for i in range(0, len(ids), size):
        try:
            result = run(ids[i : i + size])
        except YandexError as exc:
            error = error or exc.message or exc.code
            if exc.code in _STOP_CODES:
                break
            continue
        applied += int(result.get("applied") or 0)
        error = error or _result_error(result)
    return applied, error


def _apply(user_id: int, rule: dict, target: str, action: str, matched: list[dict]) -> tuple[int, str]:
    """Действие правила в Директе → (применено, ошибка_или_пусто)."""
    ids = [m["row_id"] for m in matched]
    if action == "notify":
        return len(ids), ""
    if target == "query" and action in ("negative_adgroup", "negative_campaign"):
        if action == "negative_adgroup":
            ids = [m["row_id"] for m in matched if m.get("adgroup_row_id")]
        return _in_chunks(ids, 100, lambda chunk: bulk_search_query_action(user_id, query_ids=chunk, action=action))
    if target == "keyword" and action in ("pause", "resume", "set_bid"):
        kw_action = "suspend" if action == "pause" else action
        bid = None
        if action == "set_bid":
            bid = float(rule.get("action_bid_rub") or 0)
            if bid <= 0:
                raise YandexError("budget")
        return _in_chunks(
            ids, 100,
            lambda chunk: bulk_keyword_action(user_id, keyword_row_ids=chunk, action=kw_action, bid_rub=bid),
        )
    if target == "campaign" and action in ("set_weekly_budget", "change_budget_pct"):
        pct = float(rule["action_pct"]) if rule.get("action_pct") is not None else None
        cap = float(rule["action_budget_rub"]) if rule.get("action_budget_rub") is not None else None
        return _in_chunks(
            ids[:BUDGET_BULK_LIMIT], BUDGET_BULK_LIMIT,
            lambda chunk: bulk_budget(
                user_id,
                campaign_ids=chunk,
                weekly_budget_rub=cap if action == "set_weekly_budget" else None,
                budget_change_pct=pct if action == "change_budget_pct" else None,
                cap_weekly_rub=cap if action == "change_budget_pct" else None,
            ),
        )
    if target == "campaign" and action in ("pause", "resume", "set_budget"):
        budget = None
        if action == "set_budget":
            budget = float(rule["action_budget_rub"] or 0)
            if budget <= 0:
                raise YandexError("budget")
        return _in_chunks(
            ids, max(1, len(ids)),
            lambda chunk: bulk_campaign_action(user_id, campaign_ids=chunk, action=action, daily_budget_rub=budget),
        )
    return 0, ""


def run_rule(user_id: int, rule_id: int, *, force_dry_run: bool | None = None) -> dict:
    # Действия правила попадают в журнал с источником «правило».
    with acting_as("rule"):
        return _run_rule(user_id, rule_id, force_dry_run=force_dry_run)


def _run_rule(user_id: int, rule_id: int, *, force_dry_run: bool | None = None) -> dict:
    with pool.connection() as conn:
        rule = conn.execute(
            "SELECT * FROM automation_rules WHERE id = %s AND user_id = %s",
            (rule_id, user_id),
        ).fetchone()
    if rule is None:
        raise YandexError("not-found")

    dry = rule["dry_run"] if force_dry_run is None else force_dry_run
    target = rule.get("target") or "campaign"
    if target == "keyword":
        matched = _matching_keywords(user_id, rule)
    elif target == "query":
        matched = _matching_queries(user_id, rule)
    else:
        matched = _matching_campaigns(user_id, rule)
    action = rule["action"] or "pause"
    applied, error = 0, ""
    if matched and not dry:
        applied, error = _apply(user_id, rule, target, action, matched)

    details = json.dumps(
        {"matched": matched, "action": action, "target": target},
        ensure_ascii=False,
    )[:8000]
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO rule_runs (user_id, rule_id, dry_run, matched, applied, details, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, rule_id, dry, len(matched), applied, details, error[:500]),
        )
        conn.execute(
            "UPDATE automation_rules SET last_run_at = NOW(), updated_at = NOW() WHERE id = %s",
            (rule_id,),
        )

    # Алерты: при совпадениях вне dry-run, либо всегда для action=notify
    if matched and (not dry or action == "notify"):
        try:
            notify_user(
                user_id,
                format_rule_alert(
                    rule_name=rule.get("name") or str(rule_id),
                    action=action,
                    dry_run=dry,
                    matched=matched,
                    applied=applied,
                    error=error,
                ),
            )
        except Exception:
            log.exception("telegram notify rule %s", rule_id)

    return {
        "rule_id": rule_id,
        "dry_run": dry,
        "action": action,
        "target": target,
        "matched": len(matched),
        "applied": applied,
        "error": error,
        "items": matched,
    }


def run_enabled_rules_for_user(user_id: int) -> int:
    with pool.connection() as conn:
        rules = conn.execute(
            """
            SELECT * FROM automation_rules
            WHERE user_id = %s AND enabled = TRUE
            """,
            (user_id,),
        ).fetchall()
    ran = 0
    now = datetime.now(timezone.utc)
    for rule in rules:
        last = rule.get("last_run_at")
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(hours=int(rule["cooldown_hours"] or 24)):
                continue
        try:
            run_rule(user_id, rule["id"])
            ran += 1
        except Exception:
            log.exception("rule %s", rule["id"])
    return ran


def list_users_with_rules() -> list[int]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM automation_rules WHERE enabled = TRUE"
        ).fetchall()
    return [int(r["user_id"]) for r in rows]
