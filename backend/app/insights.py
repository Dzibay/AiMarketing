"""Сигналы пространства без ИИ: конверсии за 0 ₽, затухание показов, перезапуски копиями.

Считаются кодом из уже загруженных данных — бесплатно и сразу. По сигналам интерфейс сам
предлагает помощь, а «Разбор» отдаёт ИИ короткую выжимку вместо сырых таблиц: токены уходят
на выводы и вопросы, а не на чтение статистики.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median

from app.clock import MSK, today_msk
from app.db import pool
from app.overview import days_on_balance, rub
from app.strategy_info import goal_label, is_metrika_goal, strategy_brief
from app.workspaces import SCHEME_FACT, normalize_ai_facts
from app.yandex import YandexError

log = logging.getLogger(__name__)

PERIOD_DAYS = 30
WEEK = 7
# Затухание: показы за последние 7 дней хотя бы вдвое ниже лучших 7 дней периода.
DECAY_MIN_PEAK_IMPRESSIONS = 100
DECAY_RATIO = 0.5
# День спада — первый после начала пика, с которого средние показы за 3 дня ниже половины пиковых.
DROP_WINDOW = 3
# Изменения видны только после синка, поэтому причину ищем шире до спада, чем после.
CAUSE_DAYS_BEFORE = 7
CAUSE_DAYS_AFTER = 2
# Спады кампаний одного логина в пределах ±2 дней — похоже на общую причину.
CLUSTER_SPREAD_DAYS = 2
CLUSTER_MIN_CAMPAIGNS = 3
# Успешные кампании: в верхней пятой части по конверсиям или CR заметно выше среднего.
WINNER_MIN_CONVERSIONS = 5
WINNER_TOP_SHARE = 0.2
WINNER_CR_RATIO = 1.5
WINNER_MIN_CLICKS = 30
WINNER_MIN_CR_CONVERSIONS = 3
LIST_LIMIT = 15
DIGEST_LIMIT = 9000

FIELDS = ("impressions", "clicks", "cost_kopecks", "conversions", "revenue_kopecks")
KEY_GOALS = 13  # служебная цель стратегии «ключевые цели кампании»

_STATE_LABELS = {
    "ON": "идёт",
    "OFF": "не идёт",
    "SUSPENDED": "остановлена",
    "ENDED": "закончилась",
    "CONVERTED": "переведена",
    "ARCHIVED": "в архиве",
}
_STATUS_LABELS = {"ACCEPTED": "принята", "DRAFT": "черновик", "MODERATION": "на модерации", "REJECTED": "отклонена"}
_SOURCE_LABELS = {"user": "вы", "ai": "ИИ", "rule": "правило"}


def _num(value) -> str:
    return f"{int(round(value or 0)):,}".replace(",", " ")


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Число со словом: 1 кампания, 2 кампании, 5 кампаний, 11 кампаний."""
    if 11 <= n % 100 <= 14 or n % 10 == 0 or n % 10 >= 5:
        word = many
    elif n % 10 == 1:
        word = one
    else:
        word = few
    return f"{_num(n)} {word}"


def _campaigns(n: int) -> str:
    return _plural(n, "кампания", "кампании", "кампаний")


def _rub_text(value: float) -> str:
    """Рубли из стратегии: 500 → «500 ₽», 12.5 → «12,5 ₽», 150000 → «150 000 ₽»."""
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", " ").replace(".", ",") + " ₽"


def _day(value: date | str | None) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return value.strftime("%d.%m")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _days(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]


# --- Чистые расчёты (покрыты юнит-тестами) ---------------------------------------------------


def decay_info(impressions: list[int]) -> dict | None:
    """Затухание по дневным показам (по порядку дней) или None.

    Лучшие 7 дней периода против последних 7: упали хотя бы вдвое — затухание. Индексы —
    позиции в списке: peak_index — первый день лучшей недели, drop_index — день спада.
    """
    n = len(impressions)
    if n < 2 * WEEK:
        return None
    weeks = [sum(impressions[i : i + WEEK]) for i in range(n - WEEK + 1)]
    peak, last = max(weeks), weeks[-1]
    if peak < DECAY_MIN_PEAK_IMPRESSIONS or last > peak * DECAY_RATIO:
        return None
    peak_index = weeks.index(peak)
    threshold = peak / WEEK * DECAY_RATIO
    drop_index = n - WEEK
    for i in range(peak_index + 1, n):
        window = impressions[i : i + DROP_WINDOW]
        # Сам день тоже ниже порога: иначе спадом станет последний хороший день перед ним.
        if impressions[i] < threshold and sum(window) / len(window) < threshold:
            drop_index = i
            break
    return {
        "peak_index": peak_index,
        "drop_index": drop_index,
        "peak_week": peak,
        "last_week": last,
        "drop_pct": round(100 * (1 - last / peak)),
    }


def drop_cluster(days: list[date], spread: int = CLUSTER_SPREAD_DAYS) -> tuple[date | None, int]:
    """Сколько спадов пришлись на одни дни: (день, число спадов в пределах ±spread дней от него)."""
    best: tuple[date | None, int] = (None, 0)
    for d in sorted(days):
        count = sum(1 for other in days if abs((other - d).days) <= spread)
        if count > best[1]:
            best = (d, count)
    return best


_COPY_PREFIX = re.compile(r"^\s*(?:копия|copy(?:\s+of)?)\b[\s:\-–—]*", re.I)
_COPY_SUFFIX = re.compile(r"(?:[\s\-–—_:]+|\s*[(\[])\s*(?:копия|copy)\s*\d*\s*[)\]]?\s*$", re.I)
_NUMBER_SUFFIX = re.compile(r"\s*[(\[]\s*\d{1,3}\s*[)\]]\s*$")
_QUOTES = " «»\"'„“”"


def copy_base_name(name: str) -> str:
    """Название без пометок копии: «Ремонт (копия)», «Копия «Ремонт»», «Ремонт (2)» → «ремонт».

    Голое число в конце не трогаем: «Квартиры 2» — скорее отдельная кампания, чем копия.
    """
    text = " ".join(str(name or "").replace("ё", "е").replace("Ё", "Е").split())
    while True:
        stripped = _COPY_PREFIX.sub("", text)
        stripped = _COPY_SUFFIX.sub("", stripped)
        stripped = _NUMBER_SUFFIX.sub("", stripped)
        stripped = stripped.strip(_QUOTES)
        if stripped == text:
            return text.lower()
        text = stripped


def copy_groups(campaigns: list[dict]) -> list[list[dict]]:
    """Серии перезапусков: копии, сделанные сервисом (source_row_id), и кампании с одним
    названием без пометок копии. Внутри серии — по порядку создания (номер кампании растёт)."""
    parent = {c["id"]: c["id"] for c in campaigns}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_name: dict[str, list[int]] = defaultdict(list)
    for c in campaigns:
        base = copy_base_name(c["name"])
        if base:
            by_name[base].append(c["id"])
    for ids in by_name.values():
        for other in ids[1:]:
            union(ids[0], other)
    for c in campaigns:
        source = c.get("source_row_id")
        if source in parent:
            union(c["id"], source)
    groups: dict[int, list[dict]] = defaultdict(list)
    for c in campaigns:
        groups[find(c["id"])].append(c)
    out = [sorted(g, key=lambda c: int(c["campaign_id"])) for g in groups.values() if len(g) > 1]
    out.sort(key=lambda g: -len(g))
    return out


def strategy_text(brief: dict, names: dict[int, str] | None = None) -> str:
    """Стратегия одной строкой: «Оплата за конверсии (цель 123 «Заказ», CPA 500 ₽, неделя 3000 ₽)»."""
    if not brief:
        return "—"
    bits: list[str] = []
    for side in ("search", "network"):
        s = brief.get(side) or {}
        if s.get("goal_id") is not None:
            bits.append("цель " + goal_label(s["goal_id"], names))
        for key, label in (("cpa_rub", "CPA"), ("avg_cpc_rub", "клик"), ("cpi_rub", "установка")):
            if s.get(key) is not None:
                bits.append(f"{label} {_rub_text(s[key])}")
        if s.get("crr") is not None:
            bits.append(f"ДРР {s['crr']}%")
        if s.get("weekly_rub") is not None:
            bits.append(f"неделя {_rub_text(s['weekly_rub'])}")
        if s.get("period_rub") is not None:
            bits.append(f"на период {_rub_text(s['period_rub'])}")
    unique = list(dict.fromkeys(bits))
    return brief.get("label", "") + (f" ({', '.join(unique)})" if unique else "")


def paid_goal_ids(brief: dict, priority_ids: list[int]) -> list[int]:
    """Цели Метрики, за конверсии по которым кампания платит. «Ключевые цели» — это ключевые цели кампании."""
    out: list[int] = []
    for goal_id in brief.get("paid_goal_ids") or []:
        if goal_id == KEY_GOALS:
            out.extend(priority_ids)
        elif is_metrika_goal(goal_id):
            out.append(int(goal_id))
    return list(dict.fromkeys(out))


def launch_date(start: date | None, first_day: date | None, date_from: date) -> date | None:
    """С какого дня считать возраст кампании: дата старта, а если показы начались заметно позже
    (копия с датой старта оригинала, долгая модерация) — первый день показов в периоде."""
    if first_day and first_day > date_from and (start is None or (first_day - start).days > 3):
        return first_day
    return start


def change_text(field: str, old, new, names: dict[int, str] | None = None) -> str:
    """Изменение настройки кампании из campaign_changes по-человечески."""
    if field == "name":
        return f"название «{old}» → «{new}»"
    if field == "state":
        return f"состояние: {_STATE_LABELS.get(old, old)} → {_STATE_LABELS.get(new, new)}"
    if field == "status":
        return f"статус: {_STATUS_LABELS.get(old, old)} → {_STATUS_LABELS.get(new, new)}"
    if field == "daily_budget_kopecks":
        return f"дневной бюджет: {rub(old) if old else 'нет'} → {rub(new) if new else 'нет'}"
    if field == "strategy":
        return f"стратегия: {strategy_text(old or {}, names)} → {strategy_text(new or {}, names)}"
    if field == "priority_goals":
        def goals(items) -> str:
            return ", ".join(goal_label(g.get("goal_id"), names) for g in items or []) or "нет"

        return f"ключевые цели: {goals(old)} → {goals(new)}"
    if field == "attribution_model":
        return f"атрибуция: {old or '—'} → {new or '—'}"
    return f"{field}: {old} → {new}"


def winner_reasons(campaigns: list[dict]) -> dict[int, list[str]]:
    """Кто успешен и почему: free — конверсии без расхода, many — много конверсий, high_cr — CR выше среднего."""
    clicks = sum(c["clicks"] for c in campaigns)
    conversions = sum(c["conversions"] for c in campaigns)
    avg_cr = conversions / clicks if clicks else 0
    with_conv = sorted((c["conversions"] for c in campaigns if c["conversions"] > 0), reverse=True)
    top_n = max(1, math.ceil(len(with_conv) * WINNER_TOP_SHARE)) if with_conv else 0
    many_threshold = max(WINNER_MIN_CONVERSIONS, with_conv[top_n - 1]) if with_conv else None
    out: dict[int, list[str]] = {}
    for c in campaigns:
        reasons = []
        if c["conversions"] > 0 and c["cost_kopecks"] == 0:
            reasons.append("free")
        if many_threshold is not None and c["conversions"] >= many_threshold:
            reasons.append("many")
        if (
            avg_cr
            and c["clicks"] >= WINNER_MIN_CLICKS
            and c["conversions"] >= WINNER_MIN_CR_CONVERSIONS
            and c["conversions"] / c["clicks"] >= avg_cr * WINNER_CR_RATIO
        ):
            reasons.append("high_cr")
        if reasons:
            out[c["id"]] = reasons
    return out


# --- Загрузка --------------------------------------------------------------------------------


def _period(today: date | None = None) -> tuple[date, date]:
    date_to = (today or today_msk()) - timedelta(days=1)
    return date_to - timedelta(days=PERIOD_DAYS - 1), date_to


def _load(user_id: int, ws_id: int, date_from: date, date_to: date) -> dict:
    since = datetime.combine(date_from, time.min, tzinfo=MSK)
    with pool.connection() as conn:
        ws = conn.execute(
            "SELECT id, name, goal_id, ai_facts FROM workspaces WHERE id = %s AND user_id = %s",
            (ws_id, user_id),
        ).fetchone()
        if ws is None:
            raise YandexError("not-found")
        campaigns = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, c.campaign_type, c.state, c.status,
                   c.daily_budget_kopecks, c.start_date, c.bidding_strategy, c.priority_goals,
                   c.attribution_model, c.source_row_id
            FROM workspace_campaigns wc
            JOIN direct_campaigns c ON c.id = wc.campaign_row_id
            WHERE wc.workspace_id = %s AND wc.user_id = %s
            ORDER BY c.campaign_id
            """,
            (ws_id, user_id),
        ).fetchall()
        account_ids = sorted({int(c["account_id"]) for c in campaigns})
        row_ids = [int(c["id"]) for c in campaigns]
        empty = not account_ids
        accounts = [] if empty else conn.execute(
            """
            SELECT id, COALESCE(NULLIF(name, ''), client_login) AS name, client_login,
                   balance_kopecks, balance_currency, stats_synced_at, stats_error,
                   load_state -> 'stats' AS stats_stage
            FROM direct_accounts
            WHERE user_id = %s AND id = ANY(%s)
            """,
            (user_id, account_ids),
        ).fetchall()
        # Ряды всего кабинета, не только кампаний пространства: по ним видно падение логина целиком.
        stats = [] if empty else conn.execute(
            """
            SELECT account_id, campaign_id, day, impressions, clicks, cost_kopecks, conversions, revenue_kopecks
            FROM stats_daily
            WHERE user_id = %s AND source = 'direct' AND account_id = ANY(%s) AND day BETWEEN %s AND %s
            """,
            (user_id, account_ids, date_from, date_to),
        ).fetchall()
        goal_stats = [] if empty else conn.execute(
            """
            SELECT account_id, campaign_id, day, goal_id, conversions, revenue_kopecks
            FROM stats_daily
            WHERE user_id = %s AND source = 'direct_goal' AND account_id = ANY(%s) AND day BETWEEN %s AND %s
            """,
            (user_id, account_ids, date_from, date_to),
        ).fetchall()
        changes = [] if empty else conn.execute(
            """
            SELECT campaign_row_id, field, old_value, new_value, changed_at
            FROM campaign_changes
            WHERE user_id = %s AND campaign_row_id = ANY(%s) AND changed_at >= %s
            ORDER BY changed_at, id
            """,
            (user_id, row_ids, since),
        ).fetchall()
        actions = [] if empty else conn.execute(
            """
            SELECT id, action, title, source, status, created_at, targets
            FROM action_logs
            WHERE user_id = %s AND created_at >= %s AND targets && %s::text[]
            ORDER BY id
            """,
            (user_id, since, [f"campaign:{i}" for i in row_ids]),
        ).fetchall()
        goal_ids = {int(r["goal_id"]) for r in goal_stats if r["goal_id"] is not None}
        if ws["goal_id"]:
            goal_ids.add(int(ws["goal_id"]))
        for c in campaigns:
            goal_ids.update(int(g["goal_id"]) for g in c["priority_goals"] or [] if g.get("goal_id") is not None)
            goal_ids.update(strategy_brief(c["bidding_strategy"]).get("paid_goal_ids") or [])
        names = {
            int(r["goal_id"]): r["name"]
            for r in conn.execute(
                "SELECT DISTINCT ON (goal_id) goal_id, name FROM metrika_goals WHERE user_id = %s AND goal_id = ANY(%s)",
                (user_id, sorted(g for g in goal_ids if is_metrika_goal(g))),
            ).fetchall()
            if r["name"]
        }
    return {
        "workspace": ws,
        "campaigns": campaigns,
        "accounts": accounts,
        "stats": stats,
        "goal_stats": goal_stats,
        "changes": changes,
        "actions": actions,
        "goal_names": names,
    }


def _loaded_to(account: dict, date_to: date) -> date | None:
    """Последний день, за который статистика кабинета точно загружена."""
    synced = account.get("stats_synced_at")
    if synced is None:
        return None
    synced_day = synced.astimezone(MSK).date() if synced.tzinfo else synced.date()
    return min(date_to, synced_day - timedelta(days=1))


# --- Сборка ----------------------------------------------------------------------------------


def _event_day(value: datetime) -> date:
    return value.astimezone(MSK).date() if value.tzinfo else value.date()


def build_signals(data: dict, *, date_from: date, date_to: date) -> dict:
    """Сигналы по загруженным данным (см. _load). Только расчёты — без запросов к базе и API."""
    ws = data["workspace"]
    names: dict[int, str] = data["goal_names"]
    accounts = {int(a["id"]): a for a in data["accounts"]}
    ends = {aid: _loaded_to(a, date_to) for aid, a in accounts.items()}
    week_from = date_to - timedelta(days=WEEK - 1)

    day_rows: dict[tuple[int, int], dict[date, dict]] = defaultdict(dict)
    account_impressions: dict[int, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    account_cost_7d: dict[int, int] = defaultdict(int)
    for r in data["stats"]:
        aid = int(r["account_id"])
        day_rows[(aid, int(r["campaign_id"] or 0))][r["day"]] = r
        account_impressions[aid][r["day"]] += int(r["impressions"] or 0)
        if r["day"] >= week_from:
            account_cost_7d[aid] += int(r["cost_kopecks"] or 0)
    goal_rows: dict[tuple[int, int], dict[int, dict[date, int]]] = defaultdict(lambda: defaultdict(dict))
    for r in data["goal_stats"]:
        goal_rows[(int(r["account_id"]), int(r["campaign_id"] or 0))][int(r["goal_id"])][r["day"]] = int(
            r["conversions"] or 0
        )
    loaded_goals = {
        aid: {int(g) for g in ((a.get("stats_stage") or {}).get("goal_ids") or [])} for aid, a in accounts.items()
    }
    changes: dict[int, list[dict]] = defaultdict(list)
    for r in data["changes"]:
        changes[int(r["campaign_row_id"])].append(
            {
                "day": _event_day(r["changed_at"]),
                "text": change_text(r["field"], r["old_value"], r["new_value"], names),
            }
        )
    actions: dict[int, list[dict]] = defaultdict(list)
    for r in data["actions"]:
        for target in r["targets"] or []:
            kind, _, raw_id = str(target).partition(":")
            if kind == "campaign" and raw_id.isdigit():
                actions[int(raw_id)].append(
                    {
                        "day": _event_day(r["created_at"]),
                        "text": f"{_SOURCE_LABELS.get(r['source'], r['source'])}: {r['title'] or r['action']}"
                        + ("" if r["status"] in ("ok", "") else f" ({r['status']})"),
                    }
                )

    infos: list[dict] = []
    for c in data["campaigns"]:
        aid = int(c["account_id"])
        key = (aid, int(c["campaign_id"]))
        end = ends.get(aid)
        days = _days(date_from, end) if end and end >= date_from else []
        rows = day_rows.get(key, {})
        series = {f: [int((rows.get(d) or {}).get(f) or 0) for d in days] for f in FIELDS}
        totals = {f: sum(v) for f, v in series.items()}
        brief = strategy_brief(c["bidding_strategy"])
        priority_ids = [int(g["goal_id"]) for g in c["priority_goals"] or [] if g.get("goal_id") is not None]
        paid_ids = paid_goal_ids(brief, priority_ids)
        goals_by_day = goal_rows.get(key, {})
        goal_totals = {g: sum(v.get(d, 0) for d in days) for g, v in goals_by_day.items()}
        paid_known = bool(paid_ids) and set(paid_ids) <= loaded_goals.get(aid, set())
        start = c["start_date"]
        first_day = next((d for d, v in zip(days, series["impressions"]) if v), None)
        launch = launch_date(start, first_day, date_from)
        info = {
            "id": int(c["id"]),
            "campaign_id": int(c["campaign_id"]),
            "name": c["name"],
            "account_id": aid,
            "account": (accounts.get(aid) or {}).get("name") or "",
            "type": c["campaign_type"],
            "state": c["state"],
            "status": c["status"],
            "strategy": strategy_text(brief, names),
            "strategy_known": bool(brief),
            "pay_for_conversion": bool(brief.get("pay_for_conversion")),
            "paid_goal_ids": paid_ids,
            "priority_goal_ids": priority_ids,
            "daily_budget_kopecks": c["daily_budget_kopecks"],
            "start_date": _iso(start),
            "launch_date": _iso(launch),
            "age_days": (date_to - launch).days + 1 if launch and launch <= date_to else None,
            "has_stats": bool(days),
            "source_row_id": c["source_row_id"],
            **totals,
            "cr_pct": round(100 * totals["conversions"] / totals["clicks"], 2) if totals["clicks"] else None,
            "paid_conversions": sum(goal_totals.get(g, 0) for g in paid_ids) if paid_known else None,
            "goal_conversions": {g: n for g, n in sorted(goal_totals.items(), key=lambda x: -x[1]) if n},
            "last_week": {f: sum(v[-WEEK:]) for f, v in series.items()},
            "decay": None,
        }
        # Затухание считаем и у остановленных: по ним видно, сколько жили прошлые копии.
        decay = decay_info(series["impressions"])
        if decay:
            peak = slice(decay["peak_index"], decay["peak_index"] + WEEK)
            drop_day = days[decay["drop_index"]]

            def window(part: slice) -> dict:
                out = {f: sum(v[part]) for f, v in series.items()}
                if paid_known:
                    out["paid_conversions"] = sum(
                        goals_by_day.get(g, {}).get(d, 0) for g in paid_ids for d in days[part]
                    )
                return out

            near = [
                e
                for e in [*changes.get(info["id"], []), *actions.get(info["id"], [])]
                if -CAUSE_DAYS_BEFORE <= (e["day"] - drop_day).days <= CAUSE_DAYS_AFTER
            ]
            near.sort(key=lambda e: e["day"])
            info["decay"] = {
                "peak_from": days[decay["peak_index"]].isoformat(),
                "drop_day": drop_day.isoformat(),
                "drop_pct": decay["drop_pct"],
                "peak": window(peak),
                "last": window(slice(-WEEK, None)),
                "age_at_drop": (drop_day - launch).days + 1 if launch and launch <= drop_day else None,
                "events": [{"day": e["day"].isoformat(), "text": e["text"]} for e in near],
            }
        info["events"] = [
            {"day": e["day"].isoformat(), "text": e["text"]}
            for e in sorted([*changes.get(info["id"], []), *actions.get(info["id"], [])], key=lambda e: e["day"])
        ]
        infos.append(info)

    by_id = {c["id"]: c for c in infos}
    live = [c for c in infos if c["state"] != "ARCHIVED"]
    free = [c for c in live if c["conversions"] > 0 and c["cost_kopecks"] == 0]
    paid = [c for c in live if c["cost_kopecks"] > 0]
    decaying = [c for c in live if c["state"] == "ON" and c["decay"]]
    idle = [
        c
        for c in live
        if c["state"] == "ON" and c["has_stats"] and not c["decay"] and c["last_week"]["impressions"] == 0
    ]

    # Схема «0 ₽»: что общего у кампаний с конверсиями без расхода.
    scheme = None
    if free:
        goal_sum: Counter[int] = Counter()
        for c in free:
            goal_sum.update(c["goal_conversions"])
        paid_in: Counter[int] = Counter(g for c in free for g in c["paid_goal_ids"])
        key_in: Counter[int] = Counter(g for c in free for g in c["priority_goal_ids"])
        goal_ids = list(dict.fromkeys([*paid_in, *goal_sum]))
        scheme = {
            "campaigns": len(free),
            "impressions": sum(c["impressions"] for c in free),
            "clicks": sum(c["clicks"] for c in free),
            "conversions": sum(c["conversions"] for c in free),
            "pay_for_conversion": sum(1 for c in free if c["pay_for_conversion"]),
            "strategy_unknown": sum(1 for c in free if not c["strategy_known"]),
            "strategies": Counter(c["strategy"].split(" (")[0] for c in free if c["strategy_known"]).most_common(),
            "types": Counter(c["type"] for c in free).most_common(),
            "goals": [
                {
                    "goal_id": g,
                    "label": goal_label(g, names),
                    "conversions": goal_sum.get(g, 0),
                    "paid_in": paid_in.get(g, 0),
                    "key_in": key_in.get(g, 0),
                }
                for g in sorted(goal_ids, key=lambda g: (-paid_in.get(g, 0), -goal_sum.get(g, 0)))
            ],
            "decaying": sum(1 for c in free if c["state"] == "ON" and c["decay"]),
            "free_traffic": sum(1 for c in live if c["impressions"] > 0 and c["cost_kopecks"] == 0),
            "paid_campaigns": len(paid),
            "paid_conversions": sum(c["conversions"] for c in paid),
            "paid_cost_kopecks": sum(c["cost_kopecks"] for c in paid),
        }

    # Логины: баланс и падение показов всего кабинета.
    logins = []
    for aid, account in sorted(accounts.items(), key=lambda x: str(x[1]["name"]).lower()):
        end = ends.get(aid)
        days = _days(date_from, end) if end and end >= date_from else []
        total = decay_info([account_impressions[aid].get(d, 0) for d in days]) if days else None
        own_active = [c for c in live if c["account_id"] == aid and c["state"] == "ON"]
        drops = [date.fromisoformat(c["decay"]["drop_day"]) for c in own_active if c["decay"]]
        cluster_day, cluster_n = drop_cluster(drops)
        balance = account.get("balance_kopecks")
        logins.append(
            {
                "id": aid,
                "name": account["name"],
                "balance_kopecks": balance,
                "currency": account.get("balance_currency") or "",
                "days_on_balance": days_on_balance(balance, account_cost_7d[aid]),
                "stats_to": _iso(end),
                "decay": (
                    {"drop_day": days[total["drop_index"]].isoformat(), "drop_pct": total["drop_pct"],
                     "peak_week": total["peak_week"], "last_week": total["last_week"]}
                    if total
                    else None
                ),
                "active": len(own_active),
                "decaying": len(drops),
                "cluster": (
                    {"day": cluster_day.isoformat(), "campaigns": cluster_n}
                    if cluster_day and cluster_n >= CLUSTER_MIN_CAMPAIGNS
                    else None
                ),
            }
        )

    # Серии перезапусков копиями.
    copies = []
    for group in copy_groups(infos):
        starts = sorted(date.fromisoformat(c["launch_date"]) for c in group if c["launch_date"])
        gaps = [(b - a).days for a, b in zip(starts, starts[1:]) if (b - a).days > 0]
        lifetimes = [c["decay"]["age_at_drop"] for c in group if c["decay"] and c["decay"]["age_at_drop"]]
        copies.append(
            {
                "name": group[0]["name"],
                "members": [c["id"] for c in group],
                "active": sum(1 for c in group if c["state"] == "ON"),
                "restart_every_days": round(median(gaps)) if gaps else None,
                "lifetime_days": round(median(lifetimes)) if lifetimes else None,
            }
        )

    reasons = winner_reasons([c for c in live if c["impressions"] > 0])
    winners = sorted(
        (c for c in live if c["id"] in reasons),
        key=lambda c: (-len(reasons[c["id"]]), -c["conversions"]),
    )
    for c in infos:
        c["winner"] = reasons.get(c["id"], [])

    notes = _data_notes(infos, accounts, ends, date_to)
    facts = normalize_ai_facts(ws.get("ai_facts"))
    clicks = sum(c["clicks"] for c in live)
    conversions = sum(c["conversions"] for c in live)
    out = {
        "workspace": {"id": int(ws["id"]), "name": ws["name"], "goal_id": ws["goal_id"],
                      "goal": goal_label(ws["goal_id"], names) if ws["goal_id"] else ""},
        "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "days": PERIOD_DAYS},
        "totals": {
            "campaigns": len(live),
            "active": sum(1 for c in live if c["state"] == "ON"),
            "impressions": sum(c["impressions"] for c in live),
            "clicks": clicks,
            "cost_kopecks": sum(c["cost_kopecks"] for c in live),
            "conversions": conversions,
            "cr_pct": round(100 * conversions / clicks, 2) if clicks else None,
        },
        "scheme": scheme,
        "free": [c["id"] for c in sorted(free, key=lambda c: -c["conversions"])],
        "decaying": [c["id"] for c in sorted(decaying, key=lambda c: -c["decay"]["peak"]["impressions"])],
        "idle": [c["id"] for c in idle],
        "winners": [c["id"] for c in winners],
        "logins": logins,
        "copies": copies,
        "notes": notes,
        "facts": facts,
        "goal_names": names,
        "campaigns": by_id,
    }
    out["suggestions"] = suggestions(out)
    return out


def _data_notes(infos: list[dict], accounts: dict[int, dict], ends: dict[int, date | None], date_to: date) -> list[str]:
    notes = []
    for aid, account in accounts.items():
        name = account["name"]
        if account.get("stats_error"):
            notes.append(f"Логин {name}: статистика не обновляется ({account['stats_error']})")
        if ends.get(aid) is None:
            notes.append(f"Логин {name}: статистика ещё не загружена")
        elif ends[aid] < date_to:
            notes.append(f"Логин {name}: статистика только по {_day(ends[aid])}")
        stage = account.get("stats_stage") or {}
        wants_goals = any(c["paid_goal_ids"] or c["priority_goal_ids"] for c in infos if c["account_id"] == aid)
        if stage.get("goal_error"):
            notes.append(f"Логин {name}: отчёт по целям с ошибкой ({stage['goal_error']})")
        elif wants_goals and not stage.get("goal_ids"):
            notes.append(f"Логин {name}: конверсии по целям ещё не загружены — обновите кабинет")
    unknown = sum(1 for c in infos if c["state"] != "ARCHIVED" and not c["strategy_known"])
    if unknown:
        notes.append(f"Стратегия загружена не целиком: {_campaigns(unknown)} — обновите кабинет")
    return notes


def suggestions(signals: dict) -> list[dict]:
    """Карточки «чем помочь» по сигналам — без ИИ. prompt — запрос для чата по кнопке."""
    out: list[dict] = []
    scheme = signals["scheme"]
    known = any(f.get("key") == SCHEME_FACT for f in signals["facts"])
    if scheme:
        out.append(
            {
                "kind": "scheme",
                "title": f"Конверсии за 0 ₽: {_campaigns(scheme['campaigns'])}",
                "detail": f"{_plural(scheme['conversions'], 'конверсия', 'конверсии', 'конверсий')} без расхода "
                f"за {signals['period']['days']} дней. "
                + (
                    "Механика уже разобрана — ИИ проверит, работает ли она сейчас"
                    if known
                    else "ИИ разберёт механику, сверит вывод с вами и запомнит"
                ),
                "prompt": "Проверь, работает ли схема конверсий за 0 ₽ сейчас"
                if known
                else "Разбери, как устроена схема конверсий за 0 ₽, и сверь вывод со мной",
                "campaign_row_ids": signals["free"][:LIST_LIMIT],
            }
        )
    falling = [lg for lg in signals["logins"] if lg["decay"] or lg["cluster"]]
    for lg in falling:
        day = (lg["decay"] or {}).get("drop_day") or lg["cluster"]["day"]
        pct = (lg["decay"] or {}).get("drop_pct")
        out.append(
            {
                "kind": "login_decay",
                "title": f"Логин {lg['name']}: " + (f"показы упали на {pct}%" if pct else "кампании затухли разом"),
                "detail": f"Около {_day(day)}. Похоже на общую причину — ИИ проверит баланс, изменения и модерацию",
                "prompt": f"Разбери, почему около {_day(day)} упали показы по логину {lg['name']}",
                "account_id": lg["id"],
            }
        )
    if signals["decaying"]:
        ids = signals["decaying"]
        free_ids = set(signals["free"])
        free_n = sum(1 for i in ids if i in free_ids)
        out.append(
            {
                "kind": "decay",
                "title": f"Затухание показов: {_campaigns(len(ids))}",
                "detail": "Показы упали вдвое и больше"
                + (f", из них {free_n} — с конверсиями за 0 ₽" if free_n else "")
                + ". ИИ найдёт причину и предложит перезапуск копией",
                "prompt": "Разбери затухающие кампании: почему упали показы и что делать",
                "campaign_row_ids": ids[:LIST_LIMIT],
            }
        )
    if signals["copies"]:
        lifetimes = [g["lifetime_days"] for g in signals["copies"] if g["lifetime_days"]]
        out.append(
            {
                "kind": "copies",
                "title": f"Перезапуски копиями: {_plural(len(signals['copies']), 'серия', 'серии', 'серий')}",
                "detail": (f"Копии затухают примерно на {round(median(lifetimes))}-й день. " if lifetimes else "")
                + "ИИ сравнит копии и найдёт, от чего зависит срок жизни",
                "prompt": "Разбери серии перезапусков копиями: сколько живут копии и почему затухают",
            }
        )
    if signals["idle"]:
        out.append(
            {
                "kind": "idle",
                "title": f"Включены, но без показов: {len(signals['idle'])}",
                "detail": "Неделю ни одного показа. ИИ проверит статус, стратегию и ограничения",
                "prompt": "Почему включённые кампании неделю без показов?",
                "campaign_row_ids": signals["idle"][:LIST_LIMIT],
            }
        )
    if signals["winners"]:
        out.append(
            {
                "kind": "winners",
                "title": f"Успешные кампании: {len(signals['winners'])}",
                "detail": "Конверсии за 0 ₽, много конверсий или высокий CR. ИИ найдёт, что у них общего",
                "prompt": "Что общего у успешных кампаний и что из этого повторить?",
                "campaign_row_ids": signals["winners"][:LIST_LIMIT],
            }
        )
    for note in signals["notes"]:
        if "обновите кабинет" in note:
            out.append({"kind": "refresh", "title": "Не хватает данных", "detail": note, "prompt": ""})
            break
    return out


def get_signals(user_id: int, ws_id: int, today: date | None = None) -> dict:
    date_from, date_to = _period(today)
    return build_signals(_load(user_id, ws_id, date_from, date_to), date_from=date_from, date_to=date_to)


def _links_card(user_id: int, ws_id: int) -> dict | None:
    """Ссылки не по шаблону сайта — косяк, который стоит исправить до новых тестов."""
    from app.links import workspace_link_issues

    try:
        issues = workspace_link_issues(user_id, ws_id)
    except Exception:
        log.warning("link issues failed for workspace %s", ws_id, exc_info=True)
        return None
    bad = issues["campaigns"]
    if not bad:
        return None
    ads = sum(c["ads_total"] for c in bad)
    tracking = sum(1 for c in bad if c["campaign_problems"])
    parts = []
    if tracking:
        parts.append(f"метки не по шаблону в {_plural(tracking, 'кампании', 'кампаниях', 'кампаниях')}")
    if ads:
        parts.append(f"{_plural(ads, 'объявление', 'объявления', 'объявлений')} с неверной ссылкой")
    return {
        "kind": "links",
        "title": f"Ссылки не по шаблону: {_plural(len(bad), 'кампания', 'кампании', 'кампаний')}",
        "detail": (", ".join(parts) or "ссылки отличаются от шаблона").capitalize()
        + ". ИИ покажет, что не так, и предложит исправить",
        "prompt": "Проверь ссылки кампаний по шаблону сайта и предложи исправить косяки",
        "campaign_row_ids": [c["campaign_row_id"] for c in bad],
        "campaigns": [{"id": c["campaign_row_id"], "name": c["name"]} for c in bad[:5]],
        "campaigns_total": len(bad),
    }


def signals_overview(user_id: int, ws_id: int, today: date | None = None) -> dict:
    """Для чата: карточки «чем помочь» с названиями кампаний и заметки о данных.

    Первыми — эксперименты, по которым пора подвести итоги.
    """
    from app.experiments import chat_cards  # лениво: experiments сам импортирует insights

    signals = get_signals(user_id, ws_id, today)
    campaigns = signals["campaigns"]
    cards = [{**c, "campaigns": [], "campaigns_total": 0} for c in chat_cards(user_id, ws_id, today)]
    links = _links_card(user_id, ws_id)
    if links:
        cards.append(links)
    for s in signals["suggestions"]:
        ids = s.get("campaign_row_ids") or []
        cards.append({**s, "campaigns": [{"id": i, "name": campaigns[i]["name"]} for i in ids[:5]], "campaigns_total": len(ids)})
    return {"period": signals["period"], "totals": signals["totals"], "suggestions": cards, "notes": signals["notes"]}


# --- Выжимка для ИИ --------------------------------------------------------------------------


def _campaign_line(c: dict, names: dict[int, str] | None = None) -> str:
    parts = [f"[{c['id']}] «{c['name']}»"]
    if c["account"]:
        parts.append(c["account"])
    parts.append(_STATE_LABELS.get(c["state"], c["state"]))
    if c["age_days"]:
        parts.append(f"{c['age_days']} дн. с запуска")
    parts.append(c["strategy"])
    stats = f"показы {_num(c['impressions'])}, клики {_num(c['clicks'])}, расход {rub(c['cost_kopecks'])}, конв. {_num(c['conversions'])}"
    if c["cr_pct"] is not None:
        stats += f", CR {c['cr_pct']:g}%"
    if c["paid_conversions"] is not None:
        stats += f", из них платных {_num(c['paid_conversions'])}"
    parts.append(stats)
    if c["goal_conversions"]:
        parts.append(
            "по целям: " + ", ".join(f"{goal_label(g, names)} {n}" for g, n in list(c["goal_conversions"].items())[:4])
        )
    return " · ".join(parts)


def _window_text(w: dict) -> str:
    text = f"показы {_num(w['impressions'])}, клики {_num(w['clicks'])}, расход {rub(w['cost_kopecks'])}, конв. {_num(w['conversions'])}"
    if "paid_conversions" in w:
        text += f" (платных {_num(w['paid_conversions'])})"
    return text


def signals_digest(signals: dict) -> str:
    """Сигналы текстом для ИИ: компактно, со списками не длиннее LIST_LIMIT."""
    names = signals["goal_names"]
    campaigns = signals["campaigns"]
    period = signals["period"]
    totals = signals["totals"]
    lines = [
        f"Сигналы пространства «{signals['workspace']['name']}» за {_day(period['date_from'])}–{_day(period['date_to'])} "
        f"({period['days']} дн.). Номера в [скобках] — campaign_row_id. Конверсии — из отчёта Директа; "
        "«по целям» и «платных» — из отчёта по оплачиваемым и ключевым целям.",
        f"Итого: {_campaigns(totals['campaigns'])} ({totals['active']} идут), показы {_num(totals['impressions'])}, "
        f"клики {_num(totals['clicks'])}, расход {rub(totals['cost_kopecks'])}, конверсии {_num(totals['conversions'])}"
        + (f", CR {totals['cr_pct']:g}%" if totals["cr_pct"] is not None else "")
        + ".",
    ]
    if signals["workspace"]["goal"]:
        lines.append(f"Главная цель пространства: {signals['workspace']['goal']}.")
    # Ответы маркетолога (ai_facts) — в системных правилах чата, здесь не повторяем.

    scheme = signals["scheme"]
    if scheme:
        lines.append("")
        lines.append(
            f"КОНВЕРСИИ ЗА 0 ₽: {_campaigns(scheme['campaigns'])} — {_num(scheme['conversions'])} конв., "
            f"{_num(scheme['impressions'])} показов, {_num(scheme['clicks'])} кликов, расход 0 ₽. "
            f"Показы без расхода у {_plural(scheme['free_traffic'], 'кампании', 'кампаний', 'кампаний')}. "
            f"Для сравнения платные: {_campaigns(scheme['paid_campaigns'])}, "
            f"{_num(scheme['paid_conversions'])} конв. за {rub(scheme['paid_cost_kopecks'])}."
        )
        if scheme["strategies"]:
            lines.append("Стратегии: " + ", ".join(f"{label} — {n}" for label, n in scheme["strategies"]) + ".")
        if scheme["strategy_unknown"]:
            lines.append(f"Стратегия не загружена у {scheme['strategy_unknown']}.")
        if scheme["goals"]:
            lines.append(
                "Цели (конверсии в этих кампаниях; где цель оплачивается / ключевая): "
                + "; ".join(
                    f"{g['label']} — {_num(g['conversions'])}"
                    + (f", оплачивается в {g['paid_in']}" if g["paid_in"] else "")
                    + (f", ключевая в {g['key_in']}" if g["key_in"] else "")
                    for g in scheme["goals"][:8]
                )
                + "."
            )
        if scheme["decaying"]:
            lines.append(f"Из них затухают: {scheme['decaying']}.")
        lines += [f"- {_campaign_line(campaigns[i], names)}" for i in signals["free"][:LIST_LIMIT]]
        if len(signals["free"]) > LIST_LIMIT:
            lines.append(f"- … ещё {len(signals['free']) - LIST_LIMIT}")

    if signals["logins"]:
        lines.append("")
        lines.append("ЛОГИНЫ:")
        for lg in signals["logins"]:
            bits = [lg["name"]]
            if lg["balance_kopecks"] is not None:
                bits.append(
                    f"баланс {rub(lg['balance_kopecks'])}"
                    + (f" (~{lg['days_on_balance']:g} дн.)" if lg["days_on_balance"] is not None else "")
                )
            if lg["decay"]:
                bits.append(f"показы всего логина −{lg['decay']['drop_pct']}% с {_day(lg['decay']['drop_day'])}")
            if lg["cluster"]:
                bits.append(f"{lg['cluster']['campaigns']} кампаний затухли около {_day(lg['cluster']['day'])}")
            bits.append(f"идут {lg['active']}, затухают {lg['decaying']}")
            lines.append("- " + ", ".join(bits))

    if signals["decaying"]:
        lines.append("")
        lines.append("ЗАТУХАНИЕ (показы за последние 7 дн. против лучших 7 дн. периода):")
        for i in signals["decaying"][:LIST_LIMIT]:
            c = campaigns[i]
            d = c["decay"]
            head = (
                f"- [{c['id']}] «{c['name']}» ({c['account']}): {_num(d['peak']['impressions'])} → "
                f"{_num(d['last']['impressions'])} (−{d['drop_pct']}%), спад с {_day(d['drop_day'])}"
                + (f", на {d['age_at_drop']}-й день" if d["age_at_drop"] else "")
                + f"; {c['strategy']}"
            )
            lines.append(head)
            lines.append(f"  лучшая неделя с {_day(d['peak_from'])}: {_window_text(d['peak'])}; последняя: {_window_text(d['last'])}")
            if d["events"]:
                lines.append("  рядом со спадом: " + "; ".join(f"{_day(e['day'])} {e['text']}" for e in d["events"][:6]))
        if len(signals["decaying"]) > LIST_LIMIT:
            lines.append(f"- … ещё {len(signals['decaying']) - LIST_LIMIT}")

    if signals["idle"]:
        lines.append("")
        lines.append("ВКЛЮЧЕНЫ, НО НЕДЕЛЮ БЕЗ ПОКАЗОВ:")
        for i in signals["idle"][:LIST_LIMIT]:
            c = campaigns[i]
            lines.append(
                f"- [{c['id']}] «{c['name']}» ({c['account']}): статус {_STATUS_LABELS.get(c['status'], c['status'])}, "
                f"{c['strategy']}, за период показы {_num(c['impressions'])}"
            )

    if signals["copies"]:
        lines.append("")
        lines.append("СЕРИИ ПЕРЕЗАПУСКОВ КОПИЯМИ (по порядку создания):")
        for g in signals["copies"][:LIST_LIMIT]:
            members = []
            for i in g["members"]:
                c = campaigns[i]
                bit = f"[{c['id']}] с {_day(c['launch_date'])} {_STATE_LABELS.get(c['state'], c['state'])}"
                if c["decay"]:
                    bit += f", спад {_day(c['decay']['drop_day'])}"
                bit += f", конв. {_num(c['conversions'])}, расход {rub(c['cost_kopecks'])}"
                members.append(bit)
            tail = []
            if g["restart_every_days"]:
                tail.append(f"перезапуск ~ раз в {g['restart_every_days']} дн.")
            if g["lifetime_days"]:
                tail.append(f"затухают ~ на {g['lifetime_days']}-й день")
            lines.append(f"- «{g['name']}»: " + "; ".join(members) + (f" — {', '.join(tail)}" if tail else ""))

    if signals["winners"]:
        lines.append("")
        lines.append("УСПЕШНЫЕ (free — конверсии за 0 ₽, many — много конверсий, high_cr — CR выше среднего):")
        for i in signals["winners"][:LIST_LIMIT]:
            c = campaigns[i]
            lines.append(f"- {', '.join(c['winner'])}: {_campaign_line(c, names)}")

    if signals["notes"]:
        lines.append("")
        lines.append("ДАННЫЕ: " + "; ".join(signals["notes"]) + ".")
    text = "\n".join(lines)
    if len(text) > DIGEST_LIMIT:
        text = text[:DIGEST_LIMIT] + "\n… выжимка обрезана — подробности по кампании: get_campaign_history"
    return text


# --- История кампании по дням ----------------------------------------------------------------


def campaign_history(user_id: int, ws_id: int, campaign_row_id: int, today: date | None = None) -> dict:
    """По дням за 30 дней: показы, клики, расход, конверсии (всего и по целям) — и что менялось.
    Нужна ИИ, чтобы проверить гипотезу о причине затухания на конкретной кампании."""
    date_from, date_to = _period(today)
    data = _load(user_id, ws_id, date_from, date_to)
    rows = [c for c in data["campaigns"] if int(c["id"]) == campaign_row_id]
    if not rows:
        raise YandexError("scope", f"Кампания {campaign_row_id} не входит в это пространство")
    data["campaigns"] = rows
    c = rows[0]
    key = (int(c["account_id"]), int(c["campaign_id"]))
    data["stats"] = [r for r in data["stats"] if (int(r["account_id"]), int(r["campaign_id"] or 0)) == key]
    data["goal_stats"] = [r for r in data["goal_stats"] if (int(r["account_id"]), int(r["campaign_id"] or 0)) == key]
    signals = build_signals(data, date_from=date_from, date_to=date_to)
    info = signals["campaigns"][campaign_row_id]
    names = data["goal_names"]
    by_day = {r["day"]: r for r in data["stats"]}
    goals: dict[date, dict[str, int]] = defaultdict(dict)
    for r in data["goal_stats"]:
        if r["conversions"]:
            goals[r["day"]][goal_label(r["goal_id"], names)] = int(r["conversions"])
    days = []
    for d in _days(date_from, date_to):
        r = by_day.get(d)
        if r is None and not goals.get(d):
            days.append({"day": d.isoformat()})
            continue
        row = {
            "day": d.isoformat(),
            "impressions": int((r or {}).get("impressions") or 0),
            "clicks": int((r or {}).get("clicks") or 0),
            "cost_rub": round(int((r or {}).get("cost_kopecks") or 0) / 100, 2),
            "conversions": int((r or {}).get("conversions") or 0),
        }
        if goals.get(d):
            row["goals"] = goals[d]
        days.append(row)
    return {
        "campaign": {
            k: info[k]
            for k in (
                "id", "campaign_id", "name", "account", "type", "state", "status", "strategy",
                "start_date", "launch_date", "age_days", "source_row_id", "paid_goal_ids", "priority_goal_ids",
            )
        },
        "stats_to": next((lg["stats_to"] for lg in signals["logins"] if lg["id"] == key[0]), None),
        "decay": info["decay"],
        "days": days,
        "events": info["events"],
        "notes": signals["notes"],
    }
