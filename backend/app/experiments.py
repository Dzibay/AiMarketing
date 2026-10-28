"""Эксперименты ИИ: гипотеза, тестовые кампании, лимит ₽/нед., срок и критерий успеха.

Эксперимент запускает ИИ по согласованию маркетолога (инструмент start_experiment). Деньги ограничивает
только недельный бюджет кампаний в Директе — сервис сам ничего не останавливает. Прогресс считает код по
уже загруженной статистике, бесплатно; итоги ИИ разбирает только по кнопке.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from psycopg.types.json import Jsonb

from app import actions
from app.clock import MSK, today_msk
from app.db import pool
from app.insights import _day, _loaded_to, _num, _plural, paid_goal_ids
from app.overview import rub
from app.strategy_info import STRATEGY_LABELS, strategy_brief
from app.workspaces import require_workspace
from app.yandex import YandexError

log = logging.getLogger(__name__)

KINDS = {
    "restart": "перезапуск копией",
    "geo": "новое гео",
    "keywords": "новые ключи",
    "type": "другой тип кампании",
    "strategy": "другая стратегия",
    "other": "другое",
}
CRITERIA = {
    "zero_cost": "конверсии за ~0 ₽",
    "conversions": "много конверсий",
    "cr": "высокий CR",
    "custom": "свой критерий",
}
VERDICTS = {"success": "подтвердилась", "fail": "не подтвердилась", "unclear": "непонятно"}
STATUS_LABELS = {"running": "идёт", "finished": "завершён", "cancelled": "отменён"}
PROGRESS_LABELS = {
    "no_data": "нет статистики",
    "waiting": "ждёт показов",
    "collecting": "идёт",
    "success": "критерий выполнен",
    "fail": "критерий не выполнен",
    "stalled": "не было показов",
    "ready": "срок вышел",
}
# Исходы, по которым чат предлагает разобрать итоги.
REVIEW_STATUSES = ("success", "fail", "stalled", "ready")
MIN_DAYS = 3
MAX_DAYS = 90
DEFAULT_DAYS = 14
# Меньше 300 ₽ Директ не примет ни недельный, ни дневной бюджет.
MIN_WEEKLY_RUB = 300
MIN_DAILY_RUB = 300
MAX_CAMPAIGNS = 10
# Сколько конверсий за весь срок нужно, если маркетолог не назвал число.
DEFAULT_TARGETS = {"zero_cost": 1, "conversions": 5}
# CR по меньшему числу кликов — шум.
CR_MIN_CLICKS = 30
# «Почти бесплатно» — не дороже рубля за конверсию.
ZERO_COST_RUB = 1
# С какой доли недельного лимита он считается почти израсходованным.
LIMIT_WARN_SHARE = 0.9
LIST_LIMIT = 50
DIGEST_PAST = 5
FIELDS = ("impressions", "clicks", "cost_kopecks", "conversions")
_STATE_LABELS = {"ON": "идёт", "OFF": "не идёт", "SUSPENDED": "остановлена", "ENDED": "закончилась", "ARCHIVED": "в архиве"}
_STATUS_LABELS = {"DRAFT": "черновик", "MODERATION": "на модерации", "REJECTED": "отклонена"}


# --- Текст -----------------------------------------------------------------------------------


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _text(value, limit: int) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def _para(value, limit: int) -> str:
    """Текст с переносами строк: без лишних пробелов и пустых строк подряд."""
    out: list[str] = []
    for line in (" ".join(part.split()) for part in str(value if value is not None else "").splitlines()):
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()[:limit]


def _rub(value: float | None) -> str:
    """Рубли → «2 000 ₽»."""
    return rub(None if value is None else round(float(value) * 100))


def _pct(value: float) -> str:
    return f"{round(float(value), 2):g}".replace(".", ",") + "%"


def _conversions(n: int) -> str:
    return _plural(n, "конверсия", "конверсии", "конверсий")


def _state_text(c: dict) -> str:
    return _STATUS_LABELS.get(c.get("status") or "") or _STATE_LABELS.get(c.get("state") or "", c.get("state") or "—")


def _msk_day(value: datetime | None) -> date | None:
    if value is None:
        return None
    return value.astimezone(MSK).date() if value.tzinfo else value.date()


# --- Определение эксперимента ----------------------------------------------------------------


def _number(value, key: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise YandexError("args", f"{key} — число") from exc
    if not math.isfinite(number):
        raise YandexError("args", f"{key} — число")
    return number


def normalize_definition(args: dict) -> dict:
    """Аргументы ИИ → поля эксперимента. Ошибки — текстом, по которому модель поправит вызов."""
    title = _text(args.get("title"), 120)
    if not title:
        raise YandexError("args", "Укажите title — короткое название эксперимента")
    hypothesis = _para(args.get("hypothesis"), 1000)
    if not hypothesis:
        raise YandexError("args", "Укажите hypothesis — что проверяем и на каких данных это основано")
    kind = str(args.get("kind") or "other").strip().lower()
    if kind not in KINDS:
        raise YandexError("args", f"kind — одно из {list(KINDS)}")
    criterion = str(args.get("criterion") or "").strip().lower()
    if criterion not in CRITERIA:
        raise YandexError("args", f"criterion — одно из {list(CRITERIA)}")
    target = _number(args.get("target"), "target")
    criterion_text = _para(args.get("criterion_text"), 500)
    if criterion == "cr":
        if target is None or not 0 < target <= 100:
            raise YandexError("args", "Для criterion=cr укажите target — CR в процентах, больше 0 и не больше 100")
        target = round(target, 2)
    elif criterion == "custom":
        if not criterion_text:
            raise YandexError("args", "Для criterion=custom опишите критерий словами в criterion_text")
        target = None
    else:
        target = float(DEFAULT_TARGETS[criterion]) if target is None else float(math.ceil(target))
        if target < 1:
            raise YandexError("args", "target — сколько конверсий нужно за весь срок, не меньше 1")
    days = _number(args.get("days"), "days")
    days = DEFAULT_DAYS if days is None else int(days)
    if not MIN_DAYS <= days <= MAX_DAYS:
        raise YandexError("args", f"days — срок теста от {MIN_DAYS} до {MAX_DAYS} дней")
    weekly = _number(args.get("weekly_budget_rub"), "weekly_budget_rub")
    if weekly is None:
        raise YandexError("args", "Укажите weekly_budget_rub — лимит ₽ в неделю на кампанию из ответа маркетолога")
    if weekly < MIN_WEEKLY_RUB:
        raise YandexError("args", f"Недельный бюджет в Директе — не меньше {MIN_WEEKLY_RUB} ₽")
    raw = args.get("campaign_row_ids")
    if not isinstance(raw, list) or not raw:
        raise YandexError("args", "Укажите campaign_row_ids — тестовые кампании (непустой список id)")
    try:
        ids = list(dict.fromkeys(int(v) for v in raw))
    except (TypeError, ValueError) as exc:
        raise YandexError("args", "campaign_row_ids: id должны быть числами") from exc
    if len(ids) > MAX_CAMPAIGNS:
        raise YandexError("args", f"Не больше {MAX_CAMPAIGNS} тестовых кампаний в одном эксперименте")
    source = _number(args.get("source_campaign_row_id"), "source_campaign_row_id")
    return {
        "title": title,
        "hypothesis": hypothesis,
        "kind": kind,
        "campaign_row_ids": ids,
        "source_campaign_row_id": int(source) if source else None,
        "weekly_budget_rub": round(weekly, 2),
        "days": days,
        "criterion": criterion,
        "target": target,
        "criterion_text": criterion_text,
    }


def criterion_text(exp: dict) -> str:
    """Критерий успеха словами — для карточки, выжимки и страницы экспериментов."""
    criterion = exp.get("criterion")
    target = exp.get("target")
    note = exp.get("criterion_text") or ""
    if criterion == "zero_cost":
        base = f"конверсии за ~0 ₽ — от {_num(target)} конв. за весь срок, не дороже {ZERO_COST_RUB} ₽ за конверсию"
    elif criterion == "conversions":
        base = f"от {_num(target)} конв. за весь срок"
    elif criterion == "cr" and target is not None:
        base = f"CR от {_pct(target)} (считаем от {CR_MIN_CLICKS} кликов)"
    else:
        return note or CRITERIA["custom"]
    return f"{base}; {note}" if note else base


def _goal_short(exp: dict) -> str:
    target = exp.get("target")
    if exp.get("criterion") == "zero_cost":
        return f"от {_num(target)} конв. за ~0 ₽"
    if exp.get("criterion") == "conversions":
        return f"от {_num(target)} конв."
    if exp.get("criterion") == "cr" and target is not None:
        return f"CR от {_pct(target)}"
    return criterion_text(exp)


# --- Лимит расхода в Директе -----------------------------------------------------------------


def limit_plan(settings: dict, weekly_rub: float) -> dict:
    """Как удержать расход кампании в лимите ₽/нед.: {kind, before, after, changes, note}.

    settings — get_campaign_settings. Автостратегии и ручные ставки ЕПК — недельным бюджетом стратегии;
    у ручных ставок ТГО недельного бюджета нет — там дневной не больше лимита/7. Меньший прежний бюджет
    не поднимаем: лимит — потолок, а не цель расхода.
    """
    name = settings.get("name") or ""
    who = f"«{name}»: " if name else ""
    manual_hint = "попроси маркетолога задать недельный бюджет в Директе вручную и запусти эксперимент с set_limit=false"
    ctype = str(settings.get("type") or "")
    if ctype not in ("TEXT_CAMPAIGN", "UNIFIED_CAMPAIGN"):
        raise YandexError("unsupported", f"{who}у этого типа кампаний лимит ставится только в Директе — {manual_hint}")
    flat = settings.get("strategy") or {}
    search = str(flat.get("search") or "").upper()
    network = str(flat.get("network") or "").upper()
    auto = actions.AUTO_STRATEGIES
    weekly = flat.get("weekly_budget_rub") or None
    daily = settings.get("daily_budget_rub") or None
    unified_manual = ctype == "UNIFIED_CAMPAIGN" and search == "HIGHEST_POSITION"

    if search in auto or (search == "SERVING_OFF" and network in auto) or unified_manual:
        if weekly:
            before = f"{_rub(weekly)}/нед."
            if weekly <= weekly_rub:
                return {"kind": "weekly", "before": before, "after": f"{before} — не больше лимита, не меняем",
                        "changes": {}, "note": ""}
        elif unified_manual and daily:
            before = f"{_rub(daily)}/день"
            if daily * 7 <= weekly_rub:
                return {"kind": "daily", "before": before, "after": f"{before} — за неделю не больше лимита, не меняем",
                        "changes": {}, "note": ""}
        else:
            before = "без недельного бюджета"
        strategy = {k: v for k, v in flat.items() if k != "manual"}
        strategy["weekly_budget_rub"] = weekly_rub
        note = "дневной бюджет снимется: у ручных ставок ЕПК с недельным бюджетом его нет" if unified_manual and daily and not weekly else ""
        return {"kind": "weekly", "before": before, "after": f"{_rub(weekly_rub)}/нед.",
                "changes": {"strategy": strategy}, "note": note}

    if ctype == "TEXT_CAMPAIGN" and (search == "HIGHEST_POSITION" or (search == "SERVING_OFF" and network == "MAXIMUM_COVERAGE")):
        cap = math.floor(weekly_rub / 7)
        before = f"{_rub(daily)}/день" if daily else "без дневного бюджета"
        if daily and daily <= cap:
            return {"kind": "daily", "before": before, "after": f"{before} — за неделю не больше лимита, не меняем",
                    "changes": {}, "note": ""}
        if cap < MIN_DAILY_RUB:
            floor = MIN_DAILY_RUB * 7
            raise YandexError(
                "limit",
                f"{who}у ручных ставок нет недельного бюджета, а дневной — от {MIN_DAILY_RUB} ₽, то есть до "
                f"{_rub(floor)} в неделю: больше лимита. Предложи лимит от {_rub(floor)}/нед., стратегию с недельным "
                f"бюджетом или set_limit=false, если маркетолог ограничит расход сам",
            )
        return {"kind": "daily", "before": before, "after": f"{_rub(cap)}/день",
                "changes": {"daily_budget_rub": cap}, "note": "у ручных ставок нет недельного бюджета — лимит задан дневным"}

    label = STRATEGY_LABELS.get(search if search != "SERVING_OFF" else network) or "не загружена"
    raise YandexError("strategy", f"{who}стратегию «{label}» сервис не меняет — {manual_hint}")


def apply_limits(user_id: int, rows: list[dict], weekly_rub: float) -> list[dict]:
    """Ставит тестовым кампаниям лимит ₽/нед. Сначала проверяет все: ошибка до первого изменения ничего не
    меняет. Поставленные лимиты — в журнале с откатом."""
    plans = []
    for row in rows:
        settings = actions.get_campaign_settings(user_id, int(row["id"]))
        plans.append((row, limit_plan({**settings, "name": row["name"]}, weekly_rub)))
    out: list[dict] = []
    done: list[str] = []
    for row, plan in plans:
        if plan["changes"]:
            try:
                actions.update_campaign_settings(user_id, int(row["id"]), plan["changes"])
            except YandexError as exc:
                already = f" Уже поставлен: {', '.join(done)} — откатить можно в журнале." if done else ""
                raise YandexError(
                    exc.code,
                    f"Лимит «{row['name']}» не поставлен: {exc.message or exc.code}.{already} Эксперимент не записан.",
                ) from exc
            done.append(f"«{row['name']}»")
        out.append(
            {
                "campaign_row_id": int(row["id"]),
                "name": row["name"],
                "kind": plan["kind"],
                "before": plan["before"],
                "after": plan["after"],
                "changed": bool(plan["changes"]),
                "note": plan["note"],
            }
        )
    return out


# --- Хранение --------------------------------------------------------------------------------


def create_experiment(
    user_id: int, ws_id: int, d: dict, *, chat_id: int | None = None, action_id: int | None = None,
    started_on: date | None = None,
) -> dict:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO ai_experiments (user_id, workspace_id, chat_id, action_id, title, hypothesis, kind,
                                        source_campaign_row_id, campaign_row_ids, weekly_limit_kopecks,
                                        started_on, days, criterion, target, criterion_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                user_id,
                ws_id,
                chat_id,
                action_id,
                d["title"],
                d["hypothesis"],
                d["kind"],
                d.get("source_campaign_row_id"),
                Jsonb(d["campaign_row_ids"]),
                round(d["weekly_budget_rub"] * 100) if d.get("weekly_budget_rub") else None,
                started_on or today_msk(),
                d["days"],
                d["criterion"],
                d.get("target"),
                d.get("criterion_text") or "",
            ),
        ).fetchone()


def list_experiments(user_id: int, ws_id: int, limit: int = LIST_LIMIT) -> list[dict]:
    """Идущие — первыми, затем завершённые: недавно закрытые выше, даже если запущены раньше других."""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT * FROM ai_experiments
            WHERE user_id = %s AND workspace_id = %s
            ORDER BY (status = 'running') DESC, finished_at DESC NULLS FIRST, id DESC
            LIMIT %s
            """,
            (user_id, ws_id, limit),
        ).fetchall()


def running_experiments(user_id: int, ws_id: int) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM ai_experiments WHERE user_id = %s AND workspace_id = %s AND status = 'running' ORDER BY id",
            (user_id, ws_id),
        ).fetchall()


def busy_campaigns(user_id: int, ws_id: int) -> dict[int, dict]:
    """Кампании идущих экспериментов → эксперимент: две проверки на одной кампании смешали бы результаты."""
    out: dict[int, dict] = {}
    for exp in running_experiments(user_id, ws_id):
        for cid in exp["campaign_row_ids"] or []:
            out.setdefault(int(cid), exp)
    return out


def get_experiment(user_id: int, exp_id: int, *, workspace_id: int | None = None) -> dict:
    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM ai_experiments WHERE id = %s AND user_id = %s", (exp_id, user_id)).fetchone()
    if row is None or (workspace_id is not None and int(row["workspace_id"]) != int(workspace_id)):
        raise YandexError("not-found", f"Эксперимента #{exp_id} нет в этом пространстве")
    return row


def normalize_finish(verdict, conclusion, cancel: bool) -> tuple[str, str]:
    """Вердикт и вывод; при отмене вердикт не обязателен."""
    verdict = str(verdict or "").strip().lower()
    conclusion = _para(conclusion, 2000)
    if cancel:
        return (verdict if verdict in VERDICTS else ""), conclusion
    if verdict not in VERDICTS:
        raise YandexError("args", f"verdict — одно из {list(VERDICTS)}")
    return verdict, conclusion


def finish_experiment(
    user_id: int, exp_id: int, *, verdict: str = "", conclusion: str = "", cancel: bool = False,
    workspace_id: int | None = None,
) -> dict:
    """Итог эксперимента. Кампании не трогает: остановить или масштабировать — отдельными действиями."""
    verdict, conclusion = normalize_finish(verdict, conclusion, cancel)
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, workspace_id, title, status FROM ai_experiments WHERE id = %s AND user_id = %s FOR UPDATE",
            (exp_id, user_id),
        ).fetchone()
        if row is None or (workspace_id is not None and int(row["workspace_id"]) != int(workspace_id)):
            raise YandexError("not-found", f"Эксперимента #{exp_id} нет в этом пространстве")
        if row["status"] != "running":
            raise YandexError("finished", f"Эксперимент «{row['title']}» уже {STATUS_LABELS.get(row['status'], row['status'])}")
        return conn.execute(
            """
            UPDATE ai_experiments SET status = %s, verdict = %s, conclusion = %s, finished_at = NOW()
            WHERE id = %s RETURNING *
            """,
            ("cancelled" if cancel else "finished", verdict, conclusion, exp_id),
        ).fetchone()


# --- Прогресс: считает код по загруженной статистике -----------------------------------------


def prepare_data(campaigns: list[dict], accounts: list[dict], stats: list[dict], goal_stats: list[dict]) -> dict:
    """Строки из базы → словари для progress: ряды по (кабинет, кампания Директа) и дню."""
    days: dict[tuple[int, int], dict[date, dict]] = defaultdict(dict)
    for r in stats:
        days[(int(r["account_id"]), int(r["campaign_id"] or 0))][r["day"]] = r
    goals: dict[tuple[int, int], dict[int, dict[date, int]]] = defaultdict(lambda: defaultdict(dict))
    for r in goal_stats:
        if r.get("goal_id") is not None:
            goals[(int(r["account_id"]), int(r["campaign_id"] or 0))][int(r["goal_id"])][r["day"]] = int(
                r["conversions"] or 0
            )
    by_id = {int(a["id"]): a for a in accounts}
    return {
        "campaigns": {int(c["id"]): c for c in campaigns},
        "accounts": by_id,
        "loaded_goals": {
            aid: {int(g) for g in ((a.get("stats_stage") or {}).get("goal_ids") or [])} for aid, a in by_id.items()
        },
        "days": days,
        "goals": goals,
    }


def _load(user_id: int, rows: list[dict], today: date) -> dict:
    """Кампании экспериментов, их кабинеты и статистика по дням — с запасом на сравнение «до старта»."""
    row_ids = sorted(
        {int(i) for e in rows for i in [*(e["campaign_row_ids"] or []), e["source_campaign_row_id"]] if i}
    )
    if not row_ids:
        return prepare_data([], [], [], [])
    date_from = min(e["started_on"] - timedelta(days=int(e["days"])) for e in rows)
    date_to = today - timedelta(days=1)
    with pool.connection() as conn:
        campaigns = conn.execute(
            """
            SELECT id, account_id, campaign_id, name, state, status, campaign_type, bidding_strategy, priority_goals
            FROM direct_campaigns WHERE user_id = %s AND id = ANY(%s)
            """,
            (user_id, row_ids),
        ).fetchall()
        account_ids = sorted({int(c["account_id"]) for c in campaigns})
        direct_ids = sorted({int(c["campaign_id"]) for c in campaigns if c["campaign_id"]})
        if not account_ids:
            return prepare_data(campaigns, [], [], [])
        accounts = conn.execute(
            "SELECT id, stats_synced_at, load_state -> 'stats' AS stats_stage FROM direct_accounts "
            "WHERE user_id = %s AND id = ANY(%s)",
            (user_id, account_ids),
        ).fetchall()
        params = (user_id, account_ids, direct_ids, date_from, date_to)
        stats = conn.execute(
            """
            SELECT account_id, campaign_id, day, impressions, clicks, cost_kopecks, conversions
            FROM stats_daily
            WHERE user_id = %s AND source = 'direct' AND account_id = ANY(%s) AND campaign_id = ANY(%s)
              AND day BETWEEN %s AND %s
            """,
            params,
        ).fetchall()
        goal_stats = conn.execute(
            """
            SELECT account_id, campaign_id, day, goal_id, conversions
            FROM stats_daily
            WHERE user_id = %s AND source = 'direct_goal' AND account_id = ANY(%s) AND campaign_id = ANY(%s)
              AND day BETWEEN %s AND %s
            """,
            params,
        ).fetchall()
    return prepare_data(campaigns, accounts, stats, goal_stats)


def _key(c: dict) -> tuple[int, int]:
    return int(c["account_id"]), int(c["campaign_id"] or 0)


def _zeros() -> dict:
    return dict.fromkeys(FIELDS, 0)


def _sum(rows: dict[date, dict], date_from: date, date_to: date | None) -> dict:
    out = _zeros()
    if date_to is None or date_to < date_from:
        return out
    for day, r in rows.items():
        if date_from <= day <= date_to:
            for f in FIELDS:
                out[f] += int(r.get(f) or 0)
    return out


def _metrics(s: dict) -> dict:
    return {
        **s,
        "cr_pct": round(100 * s["conversions"] / s["clicks"], 2) if s["clicks"] else None,
        "cpa_kopecks": round(s["cost_kopecks"] / s["conversions"]) if s["conversions"] else None,
    }


def _paid(data: dict, c: dict, date_from: date, date_to: date | None) -> int | None:
    """Конверсии по целям, за которые кампания платит; None — цели стратегии не загружены."""
    brief = strategy_brief(c.get("bidding_strategy"))
    priority = [int(g["goal_id"]) for g in c.get("priority_goals") or [] if g.get("goal_id") is not None]
    ids = paid_goal_ids(brief, priority)
    if not ids or not set(ids) <= data["loaded_goals"].get(int(c["account_id"]), set()):
        return None
    if date_to is None or date_to < date_from:
        return 0
    goals = data["goals"].get(_key(c), {})
    return sum(n for g in ids for day, n in goals.get(g, {}).items() if date_from <= day <= date_to)


def _account_loaded(data: dict, c: dict, date_to: date) -> date | None:
    account = data["accounts"].get(int(c["account_id"]))
    return _loaded_to(account, date_to) if account else None


def _met(exp: dict, t: dict) -> bool | None:
    """Выполнен ли критерий по сумме тестовых кампаний; None — код судить не может."""
    criterion = exp.get("criterion")
    target = exp.get("target") or 0
    if criterion == "zero_cost":
        return t["conversions"] >= max(target, 1) and t["cost_kopecks"] <= t["conversions"] * ZERO_COST_RUB * 100
    if criterion == "conversions":
        return t["conversions"] >= max(target, 1)
    if criterion == "cr":
        if t["clicks"] < CR_MIN_CLICKS:
            return None
        return 100 * t["conversions"] / t["clicks"] >= target
    return None


def _fact(exp: dict, t: dict) -> str:
    criterion = exp.get("criterion")
    if criterion == "zero_cost":
        return f"{_conversions(t['conversions'])} при расходе {rub(t['cost_kopecks'])}"
    if criterion == "conversions":
        return _conversions(t["conversions"])
    if criterion == "cr":
        if not t["clicks"]:
            return "кликов пока нет"
        return f"CR {_pct(100 * t['conversions'] / t['clicks'])} на {_plural(t['clicks'], 'клике', 'кликах', 'кликах')}"
    return (
        f"{_conversions(t['conversions'])}, {_plural(t['clicks'], 'клик', 'клика', 'кликов')}, "
        f"расход {rub(t['cost_kopecks'])}"
    )


def progress(exp: dict, data: dict, today: date) -> dict:
    """Прогресс эксперимента по статистике (см. prepare_data) — только расчёты, без базы и API.

    Срок идёт с первого дня показов тестовых кампаний: копии сначала проходят модерацию. Статус
    пересчитывается при каждом открытии — ранний успех виден сразу.
    """
    days = int(exp["days"])
    started: date = exp["started_on"]
    yesterday = today - timedelta(days=1)
    test_ids = [int(i) for i in exp.get("campaign_row_ids") or []]
    found = [data["campaigns"][i] for i in test_ids if i in data["campaigns"]]
    ends = [_account_loaded(data, c, yesterday) for c in found]
    loaded_to = min(ends) if ends and None not in ends else None

    first = None
    for c in found:
        for day, r in data["days"].get(_key(c), {}).items():
            if day >= started and int(r.get("impressions") or 0) > 0 and (first is None or day < first):
                first = day
    start = first or started
    end = start + timedelta(days=days - 1)
    finished_day = _msk_day(exp.get("finished_at"))
    cut = min(end, finished_day) if finished_day else end
    upto = min(cut, loaded_to) if loaded_to else None
    running = (exp.get("status") or "running") == "running"
    limit = exp.get("weekly_limit_kopecks")
    week_from = today - timedelta(days=today.weekday())  # неделя Директа — с понедельника

    totals = _zeros()
    paid_total: int | None = 0
    items = []
    for cid in test_ids:
        c = data["campaigns"].get(cid)
        if c is None:
            items.append({"id": cid, "name": f"[{cid}]", "missing": True, **_metrics(_zeros()),
                          "paid_conversions": None, "week_cost_kopecks": None, "week_share_pct": None, "limit_hit": False})
            continue
        rows = data["days"].get(_key(c), {})
        sums = _sum(rows, start, upto)
        for f in FIELDS:
            totals[f] += sums[f]
        paid = _paid(data, c, start, upto)
        paid_total = None if paid is None or paid_total is None else paid_total + paid
        week_cost = None
        if running and limit and loaded_to is not None and loaded_to >= week_from:
            week_cost = _sum(rows, week_from, loaded_to)["cost_kopecks"]
        items.append(
            {
                "id": cid,
                "name": c["name"],
                "state": c.get("state"),
                "status": c.get("status"),
                "missing": False,
                **_metrics(sums),
                "paid_conversions": paid,
                "week_cost_kopecks": week_cost,
                "week_share_pct": round(100 * week_cost / limit) if week_cost is not None else None,
                "limit_hit": week_cost is not None and week_cost >= LIMIT_WARN_SHARE * limit,
            }
        )
    if not found:
        paid_total = None

    met = _met(exp, totals)
    over = upto is not None and upto >= cut
    if not found or loaded_to is None:
        status = "no_data"
        reason = "статистика кабинета ещё не загружалась" if found else "тестовых кампаний нет в базе"
    elif totals["impressions"] == 0:
        status = "stalled" if over else "waiting"
        reason = (
            f"за {days} дн. у тестовых кампаний не было показов — проверьте модерацию, ставки и баланс"
            if over
            else "показов пока нет: кампании на модерации или ещё не стартовали"
        )
    elif met:
        status = "success"
        reason = f"{_fact(exp, totals)}, цель — {_goal_short(exp)}"
    elif over:
        if met is None and exp.get("criterion") == "cr":
            status, reason = "ready", f"срок вышел, но для CR мало кликов: {totals['clicks']} из {CR_MIN_CLICKS}"
        elif met is None:
            status, reason = "ready", f"срок вышел: {_fact(exp, totals)} — оцените критерий «{criterion_text(exp)}»"
        else:
            status, reason = "fail", f"срок вышел: {_fact(exp, totals)}, а цель — {_goal_short(exp)}"
    else:
        status = "collecting"
        reason = f"день {min(max((today - start).days + 1, 1), days)} из {days}: {_fact(exp, totals)}"

    source = None
    sid = exp.get("source_campaign_row_id")
    if sid:
        c = data["campaigns"].get(int(sid))
        if c is None:
            source = {"id": int(sid), "name": f"[{sid}]", "missing": True}
        else:
            src_loaded = _account_loaded(data, c, yesterday)
            rows = data["days"].get(_key(c), {})
            before_from = start - timedelta(days=days)
            before_to = start - timedelta(days=1)
            source = {
                "id": int(sid),
                "name": c["name"],
                "state": c.get("state"),
                "status": c.get("status"),
                "missing": False,
                "during": _metrics(_sum(rows, start, min(cut, src_loaded) if src_loaded else None)),
                "before": _metrics(_sum(rows, before_from, min(before_to, src_loaded) if src_loaded else None)),
                "before_from": before_from.isoformat(),
                "before_to": before_to.isoformat(),
            }

    return {
        "status": status,
        "label": PROGRESS_LABELS[status],
        "reason": reason,
        "met": met,
        "showing_since": _iso(first),
        "window_start": start.isoformat(),
        "ends_on": end.isoformat(),
        "loaded_to": _iso(loaded_to),
        "day": min(max((today - start).days + 1, 0), days),
        "days_left": max((end - today).days + 1, 0),
        "totals": {**_metrics(totals), "paid_conversions": paid_total},
        "campaigns": items,
        "week_from": week_from.isoformat(),
        "limit_hit": any(i["limit_hit"] for i in items),
        "source": source,
    }


def experiment_out(row: dict, prog: dict | None = None) -> dict:
    limit = row.get("weekly_limit_kopecks")
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "hypothesis": row.get("hypothesis") or "",
        "kind": row["kind"],
        "kind_label": KINDS.get(row["kind"], row["kind"]),
        "campaign_row_ids": [int(i) for i in row.get("campaign_row_ids") or []],
        "source_campaign_row_id": row.get("source_campaign_row_id"),
        "weekly_limit_rub": limit / 100 if limit is not None else None,
        "started_on": _iso(row["started_on"]),
        "days": int(row["days"]),
        "criterion": row["criterion"],
        "criterion_label": CRITERIA.get(row["criterion"], row["criterion"]),
        "target": row.get("target"),
        "criterion_text": row.get("criterion_text") or "",
        "criterion_summary": criterion_text(row),
        "status": row["status"],
        "status_label": STATUS_LABELS.get(row["status"], row["status"]),
        "verdict": row.get("verdict") or "",
        "verdict_label": VERDICTS.get(row.get("verdict") or "", ""),
        "conclusion": row.get("conclusion") or "",
        "chat_id": row.get("chat_id"),
        "created_at": _iso(row.get("created_at")),
        "finished_at": _iso(row.get("finished_at")),
        "progress": prog,
    }


def experiments_overview(user_id: int, ws_id: int, today: date | None = None) -> dict:
    """Эксперименты пространства с прогрессом — для страницы, карточек чата и выжимки ИИ."""
    require_workspace(user_id, ws_id)
    today = today or today_msk()
    rows = list_experiments(user_id, ws_id)
    data = _load(user_id, rows, today)
    return {"items": [experiment_out(r, progress(r, data, today)) for r in rows], "today": today.isoformat()}


def finish_and_list(
    user_id: int, ws_id: int, exp_id: int, *, verdict: str = "", conclusion: str = "", cancel: bool = False
) -> dict:
    """Итог со страницы экспериментов → обновлённый список."""
    require_workspace(user_id, ws_id)
    finish_experiment(user_id, exp_id, verdict=verdict, conclusion=conclusion, cancel=cancel, workspace_id=ws_id)
    return experiments_overview(user_id, ws_id)


# --- Для чата и ИИ ---------------------------------------------------------------------------


def review_cards(items: list[dict]) -> list[dict]:
    """Карточки «пора подвести итоги» — по идущим экспериментам с понятным исходом."""
    out = []
    for e in items:
        p = e.get("progress") or {}
        if e["status"] != "running" or p.get("status") not in REVIEW_STATUSES:
            continue
        out.append(
            {
                "kind": "experiment",
                "title": f"Эксперимент «{e['title']}»: {p['label']}",
                "detail": f"{p['reason'][:1].upper()}{p['reason'][1:]}. ИИ разберёт итоги и предложит, что дальше",
                "prompt": f"Разбери итоги эксперимента «{e['title']}»",
                "experiment_id": e["id"],
            }
        )
    return out


def chat_cards(user_id: int, ws_id: int, today: date | None = None) -> list[dict]:
    """Карточки экспериментов для чата. Сбой здесь не должен ломать сигналы — тогда без них."""
    try:
        return review_cards(experiments_overview(user_id, ws_id, today)["items"])
    except Exception:
        log.warning("Карточки экспериментов пространства %s не собраны", ws_id, exc_info=True)
        return []


def _metrics_text(m: dict) -> str:
    parts = [
        f"показы {_num(m['impressions'])}",
        f"клики {_num(m['clicks'])}",
        f"расход {rub(m['cost_kopecks'])}",
        f"конверсии {_num(m['conversions'])}",
    ]
    if m.get("paid_conversions") is not None:
        parts.append(f"платных {_num(m['paid_conversions'])}")
    if m.get("cr_pct") is not None:
        parts.append(f"CR {_pct(m['cr_pct'])}")
    if m.get("cpa_kopecks") is not None:
        parts.append(f"CPA {rub(m['cpa_kopecks'])}")
    return ", ".join(parts)


def _full_lines(e: dict) -> list[str]:
    p = e.get("progress") or {}
    head = f"#{e['id']} «{e['title']}» — {e['kind_label']}, {e['status_label']}"
    if p:
        head += f"; {e['days']} дн. с {_day(p['window_start'])} по {_day(p['ends_on'])}"
        if not p["showing_since"]:
            head += " (показов ещё не было — срок пойдёт с первого дня показов)"
        elif e["status"] == "running":
            head += f", день {p['day']} из {e['days']}"
        head += f"; статистика по {_day(p['loaded_to'])}" if p["loaded_to"] else "; статистики нет"
    lines = [head + ".", f"Гипотеза: {e['hypothesis']}", f"Критерий успеха: {e['criterion_summary']}."]
    if p:
        lines.append(f"Сейчас: {p['label']} — {p['reason']}.")
    if e.get("weekly_limit_rub"):
        lines.append(f"Лимит: {_rub(e['weekly_limit_rub'])}/нед. на кампанию.")
    if p:
        lines.append(f"Итого тестовых: {_metrics_text(p['totals'])}.")
        for c in p["campaigns"]:
            if c.get("missing"):
                lines.append(f"- [{c['id']}] нет в базе — удалена или не загружена")
                continue
            line = f"- [{c['id']}] «{c['name']}» ({_state_text(c)}): {_metrics_text(c)}"
            if c.get("week_cost_kopecks") is not None and e.get("weekly_limit_rub"):
                line += (
                    f"; за эту неделю {rub(c['week_cost_kopecks'])} из {_rub(e['weekly_limit_rub'])}"
                    f" ({c['week_share_pct']}%)"
                )
            lines.append(line)
        src = p.get("source")
        if src and src.get("missing"):
            lines.append(f"Образец [{src['id']}] — нет в базе.")
        elif src:
            lines.append(
                f"Образец [{src['id']}] «{src['name']}» ({_state_text(src)}): за те же дни — "
                f"{_metrics_text(src['during'])}; за {e['days']} дн. до старта ({_day(src['before_from'])}–"
                f"{_day(src['before_to'])}) — {_metrics_text(src['before'])}."
            )
    if e["status"] != "running":
        lines.append(
            f"Итог: {e['verdict_label'] or e['status_label']}" + (f". Вывод: {e['conclusion']}" if e["conclusion"] else "") + "."
        )
    return lines


def digest(items: list[dict], full_ids: set[int] | None = None) -> str:
    """Эксперименты текстом для ИИ: идущие (или full_ids) — подробно, прочие — строкой, прошлые — с выводами."""
    if not items:
        return "Экспериментов в этом пространстве ещё не было."
    full = {e["id"] for e in items if e["status"] == "running"} if full_ids is None else set(full_ids)
    lines = [
        "Эксперименты пространства. #N — experiment_id, номера в [скобках] — campaign_row_id. Деньги ограничивает "
        "только недельный бюджет кампаний в Директе: сервис сам ничего не останавливает. Статус посчитан кодом по "
        "загруженной статистике; конверсии — из отчёта Директа, «платных» — по целям, за которые платит стратегия."
    ]
    for e in items:
        if e["id"] in full:
            lines += ["", *_full_lines(e)]
    others = [e for e in items if e["id"] not in full and e["status"] == "running"]
    if others:
        lines += [
            "",
            "Ещё идут: "
            + "; ".join(f"#{e['id']} «{e['title']}» — {(e.get('progress') or {}).get('label', 'идёт')}" for e in others)
            + ".",
        ]
    past = [e for e in items if e["id"] not in full and e["status"] != "running"][:DIGEST_PAST]
    if past:
        lines += ["", "Прошлые эксперименты:"]
        for e in past:
            line = f"- #{e['id']} «{e['title']}» ({e['kind_label']}, с {_day(e['started_on'])}, {e['days']} дн.): "
            line += e["verdict_label"] or e["status_label"]
            if e["conclusion"]:
                line += f" — {_text(e['conclusion'], 300)}"
            lines.append(line)
    return "\n".join(lines)
