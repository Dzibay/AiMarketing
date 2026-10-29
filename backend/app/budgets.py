"""Бюджеты и цена цели кампаний: недельный бюджет стратегии или дневной ручных ставок, массово.

Автостратегии и ручные ставки ЕПК живут на недельном бюджете, ручные ставки ТГО — на дневном.
Пользователь задаёт сумму в удобной ему единице, а каждая кампания получает её в своей:
недельные 21 000 ₽ для ручных ставок ТГО — это 3 000 ₽ в день.
"""

from __future__ import annotations

import logging
import math

from app import actions
from app.journal import _workspace_for, journal_quiet, log_action
from app.overview import rub
from app.yandex import YandexError

log = logging.getLogger("app.budgets")

# Меньше 300 ₽ Директ не примет ни недельный, ни дневной бюджет.
MIN_WEEKLY_RUB = 300
MIN_DAILY_RUB = 300
BULK_LIMIT = 100
# Цена цели: AverageCpa/Cpa → average_cpa_rub, AverageCpc → average_cpc_rub.
_TARGET_KEYS = {
    "AVERAGE_CPA": "average_cpa_rub",
    "PAY_FOR_CONVERSION": "average_cpa_rub",
    "AVERAGE_CPC": "average_cpc_rub",
}


def _rub(value: float | None) -> str:
    return rub(None if value is None else round(float(value) * 100))


def _price(value: float) -> str:
    """Цена цели до 100 ₽ — с копейками: CPC 0,90 → 1,08 ₽, а не «1 ₽ → 1 ₽»."""
    return _rub(value) if value >= 100 else f"{float(value):.2f}".replace(".", ",") + " ₽"


def _scaled(value: float, pct: float) -> float:
    return float(value) * (1 + float(pct) / 100)


def _money(value: float) -> float:
    """Бюджеты — целыми рублями, цена цели — до копеек."""
    return float(math.floor(value)) if value >= 100 else round(value, 2)


def budget_kind(settings: dict) -> str:
    """weekly — бюджет стратегии на неделю; daily — дневной бюджет ручных ставок; '' — менять нечем."""
    flat = settings.get("strategy") or {}
    search = str(flat.get("search") or "").upper()
    network = str(flat.get("network") or "").upper()
    ctype = str(settings.get("type") or "")
    auto = actions.AUTO_STRATEGIES
    if search in auto or (search == "SERVING_OFF" and network in auto):
        return "weekly"
    if search == "HIGHEST_POSITION":
        if ctype == "UNIFIED_CAMPAIGN" and (flat.get("weekly_budget_rub") or not settings.get("daily_budget_rub")):
            return "weekly"
        return "daily"
    return ""


def budget_plan(
    settings: dict,
    *,
    weekly_rub: float | None = None,
    daily_rub: float | None = None,
    budget_pct: float | None = None,
    target_pct: float | None = None,
    cap_weekly_rub: float | None = None,
) -> dict:
    """Изменения одной кампании: {changes, before, after, note}. Невозможное — YandexError с объяснением.

    cap_weekly_rub — потолок (при росте) или пол (при снижении) бюджета в ₽/нед.: правило,
    которое раз в сутки поднимает бюджет на 20 %, дальше потолка его не поведёт.
    """
    flat = settings.get("strategy") or {}
    search = str(flat.get("search") or "").upper()
    network = str(flat.get("network") or "").upper()
    kind = budget_kind(settings)
    if not kind:
        label = actions.strategy_label(flat) if search else "не загружена"
        raise YandexError("strategy", f"стратегию «{label}» сервис не меняет — бюджет задаётся в Директе")
    weekly = flat.get("weekly_budget_rub") or None
    daily = settings.get("daily_budget_rub") or None
    strategy = {k: v for k, v in flat.items() if k != "manual"}
    changes: dict = {}
    before: list[str] = []
    after: list[str] = []
    notes: list[str] = []

    if weekly_rub is not None or daily_rub is not None or budget_pct is not None:
        if kind == "weekly":
            current = weekly
            before.append(f"{_rub(weekly)}/нед." if weekly else "без недельного бюджета")
            if weekly_rub is not None:
                new = float(weekly_rub)
            elif daily_rub is not None:
                new = float(daily_rub) * 7
                notes.append("у стратегии бюджет недельный — дневной × 7")
            else:
                if not current:
                    raise YandexError("budget", "недельного бюджета нет — задайте сумму")
                new = _scaled(current, budget_pct)
                if cap_weekly_rub:
                    new = min(new, cap_weekly_rub) if budget_pct > 0 else max(new, cap_weekly_rub)
            new = _money(new)
            if new < MIN_WEEKLY_RUB:
                raise YandexError("budget", f"недельный бюджет меньше {MIN_WEEKLY_RUB} ₽ Директ не примет")
            if new != current:
                strategy["weekly_budget_rub"] = new
                changes["strategy"] = strategy
            after.append(f"{_rub(new)}/нед.")
        else:
            current = daily
            before.append(f"{_rub(daily)}/день" if daily else "без дневного бюджета")
            if daily_rub is not None:
                new = float(daily_rub)
            elif weekly_rub is not None:
                new = float(weekly_rub) / 7
                notes.append("у ручных ставок бюджет дневной — недельный ÷ 7")
            else:
                if not current:
                    raise YandexError("budget", "дневного бюджета нет — задайте сумму")
                new = _scaled(current, budget_pct)
                if cap_weekly_rub:
                    cap = cap_weekly_rub / 7
                    new = min(new, cap) if budget_pct > 0 else max(new, cap)
            new = _money(new)
            if new < MIN_DAILY_RUB:
                raise YandexError("budget", f"дневной бюджет меньше {MIN_DAILY_RUB} ₽ Директ не примет")
            if new != current:
                changes["daily_budget_rub"] = new
            after.append(f"{_rub(new)}/день")

    if target_pct is not None:
        side = network if search == "SERVING_OFF" else search
        key = _TARGET_KEYS.get(side)
        current = strategy.get(key) if key else None
        if not current:
            label = actions.strategy_label(flat)
            raise YandexError("strategy", f"у стратегии «{label}» нет цены цели — менять нечего")
        new = round(_scaled(current, target_pct), 2)
        if new <= 0:
            raise YandexError("strategy", "цена цели должна быть больше нуля")
        what = "CPC" if key == "average_cpc_rub" else "CPA"
        before.append(f"{what} {_price(current)}")
        after.append(f"{what} {_price(new)}")
        if new != current:
            strategy[key] = new
            changes["strategy"] = strategy

    return {"changes": changes, "before": ", ".join(before), "after": ", ".join(after), "note": "; ".join(notes)}


def _previous(settings: dict, changes: dict) -> dict:
    """Прежние значения — шаг отката campaign_settings."""
    previous: dict = {}
    if "strategy" in changes:
        previous["strategy"] = {k: v for k, v in (settings.get("strategy") or {}).items() if k != "manual"}
    if "daily_budget_rub" in changes or ("strategy" in changes and settings.get("daily_budget_rub")):
        previous["daily_budget_rub"] = settings.get("daily_budget_rub") or 0
    return previous


def validate_args(weekly_budget_rub, daily_budget_rub, budget_change_pct, target_change_pct) -> None:
    budget_args = [v for v in (weekly_budget_rub, daily_budget_rub, budget_change_pct) if v is not None]
    if len(budget_args) > 1:
        raise YandexError("budget", "Задайте что-то одно: недельный бюджет, дневной или изменение в %")
    if not budget_args and target_change_pct is None:
        raise YandexError("budget", "Нечего менять")
    for value in (weekly_budget_rub, daily_budget_rub):
        if value is not None and float(value) <= 0:
            raise YandexError("budget")
    for value in (budget_change_pct, target_change_pct):
        if value is not None and not -90 <= float(value) <= 500:
            raise YandexError("budget", "Изменение — от −90 % до +500 %")


def bulk_budget(
    user_id: int,
    *,
    campaign_ids: list[int],
    weekly_budget_rub: float | None = None,
    daily_budget_rub: float | None = None,
    budget_change_pct: float | None = None,
    target_change_pct: float | None = None,
    cap_weekly_rub: float | None = None,
    workspace_id: int | None = None,
    progress=None,
) -> dict:
    """Бюджет и/или цена цели для набора кампаний (id строк). Одна запись в журнале с откатом всех."""
    validate_args(weekly_budget_rub, daily_budget_rub, budget_change_pct, target_change_pct)
    ids = list(dict.fromkeys(int(i) for i in campaign_ids or []))
    if not ids:
        raise YandexError("empty")
    if len(ids) > BULK_LIMIT:
        raise YandexError("limit", f"За раз — до {BULK_LIMIT} кампаний")
    if workspace_id is None:
        workspace_id = _workspace_for(user_id, {"campaign_ids": ids})

    items: list[dict] = []
    errors: list[dict] = []
    undo_steps: list[dict] = []
    for n, row_id in enumerate(ids):
        name = ""
        if progress:
            progress(n, len(ids), "")
        try:
            settings = actions.get_campaign_settings(user_id, row_id)
            name = settings.get("name") or ""
            plan = budget_plan(
                settings,
                weekly_rub=weekly_budget_rub,
                daily_rub=daily_budget_rub,
                budget_pct=budget_change_pct,
                target_pct=target_change_pct,
                cap_weekly_rub=cap_weekly_rub,
            )
            if plan["changes"]:
                with journal_quiet():
                    actions.update_campaign_settings(user_id, row_id, plan["changes"])
                previous = _previous(settings, plan["changes"])
                if previous:
                    undo_steps.append({"op": "campaign_settings", "args": {"campaign_row_id": row_id, "changes": previous}})
            items.append({"campaign_row_id": row_id, "name": name, "changed": bool(plan["changes"]), **plan})
        except YandexError as exc:
            errors.append({"campaign_row_id": row_id, "name": name, "error": exc.code, "message": exc.message or ""})
        except Exception:
            log.exception("bulk budget %s", row_id)
            errors.append({"campaign_row_id": row_id, "name": name, "error": "direct", "message": ""})

    applied = sum(1 for i in items if i["changed"])
    status = "ok" if not errors else ("partial" if items else "error")
    result = {"applied": applied, "unchanged": len(items) - applied, "items": items, "errors": errors, "status": status}
    if applied or errors:
        parts = []
        if weekly_budget_rub is not None:
            parts.append(f"бюджет {_rub(weekly_budget_rub)}/нед.")
        elif daily_budget_rub is not None:
            parts.append(f"бюджет {_rub(daily_budget_rub)}/день")
        elif budget_change_pct is not None:
            parts.append(f"бюджет {budget_change_pct:+g} %")
        if target_change_pct is not None:
            parts.append(f"цена цели {target_change_pct:+g} %")
        names = [f"«{i['name']}»" for i in items if i["changed"]][:3]
        more = applied - len(names)
        log_action(
            user_id,
            "budget",
            {
                "campaign_ids": ids,
                "weekly_budget_rub": weekly_budget_rub,
                "daily_budget_rub": daily_budget_rub,
                "budget_change_pct": budget_change_pct,
                "target_change_pct": target_change_pct,
            },
            result,
            status,
            workspace_id=workspace_id,
            title=f"{', '.join(parts).capitalize()}: {', '.join(names) or 'без изменений'}"
            + (f" и ещё {more}" if more > 0 else ""),
            undo={"label": "Вернуть прежние бюджеты", "steps": undo_steps} if undo_steps else None,
        )
    if status == "error":
        first = errors[0]
        raise YandexError(first["error"], f"«{first['name']}»: {first['message']}" if first["message"] else None)
    return result
