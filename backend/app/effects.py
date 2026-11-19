"""Помогла ли правка: итог через 7 и 14 дней (фаза 11 ux-plan-3, I34).

Для каждой применённой правки из журнала (ставки, бюджеты, стратегия, минус-фразы, остановка и запуск, регионы,
объявления — не чтение) сравниваем одинаковые отрезки до и после по затронутым кампаниям: N дней до дня правки
и N дней после него, сам день правки не считаем. Окна — 7 и 14 дней. Данные — stats_daily (source = 'direct'),
те же, что у сводки (overview._load). Окно считаем, только когда статистика всех кабинетов загружена до его конца.

Вердикт (judge) — правила, не ИИ:
- после правки нет ни расхода, ни кликов — «мало данных» (кампания стояла, сравнивать нечего);
- хотя бы с одной стороны 3+ заявки (MIN_CONVERSIONS) — судим по цене заявки: снизилась на 15 % и больше —
  «лучше», выросла на 15 % и больше — «хуже», иначе «без изменений». Заявки были (3+), а после правки ни одной
  и потрачено не меньше прежней цены заявки — «хуже»; не было ни одной, а стало 3+ — «лучше» (если до правки
  был расход или клики; у запуска — и без них); заявки без расхода — «мало данных»;
- с обеих сторон меньше 3 заявок: у минус-фраз и запрета площадок при расходе до правки от 1 000 ₽ (COST_FLOOR)
  судим по расходу (заявок не меньше, расход −15 % — «лучше», +15 % — «хуже»), у остального — «мало данных».

Итог совпадает по времени с правкой, но доказать причину нельзя: в подсказке так и пишем, а если в эти же дни
те же кампании правили ещё раз — называем, сколько было других правок.
Правка стала хуже — запись в колокольчике «После правки в «Окна» стало хуже» с «Вернуть как было», одна на запись.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from app.clock import MSK, today_msk
from app.db import pool

log = logging.getLogger(__name__)

WINDOWS = (7, 14)
# Что меняет показ рекламы. Чтение, копии, создание, архив, модерация, ссылки и сами откаты — нет.
QUALIFYING = (
    "set_budget",
    "budget",
    "bid_adjust",
    "settings",
    "update",
    "negatives",
    "negative_list_apply",
    "queries",
    "exclude_sites",
    "pause",
    "resume",
    "keywords",
    "ads",
    "adgroups",
)
# Отсекают лишний трафик: без заявок их можно судить по расходу.
NEGATIVE_ACTIONS = ("negatives", "negative_list_apply", "queries", "exclude_sites")
MIN_CONVERSIONS = 3
THRESHOLD_PCT = 15.0
COST_FLOOR_KOPECKS = 100_000  # 1 000 ₽ за окно до правки
# Старше — не досчитываем (после включения функции не пересчитываем всю историю).
LOOKBACK_DAYS = 45
VERDICTS = ("better", "worse", "neutral", "low_data")
NOTIFY_KIND = "effect"

_NBSP = " "


# --- Чистые расчёты --------------------------------------------------------------------------------


def _side(cost: int = 0, clicks: int = 0, conversions: int = 0) -> dict:
    return {"cost_kopecks": int(cost or 0), "clicks": int(clicks or 0), "conversions": int(conversions or 0)}


def cpa(side: dict) -> float | None:
    return side["cost_kopecks"] / side["conversions"] if side["conversions"] else None


def _pct(new: float, old: float) -> float | None:
    return (new - old) / old * 100 if old else None


def _by_pct(pct: float | None, *, down_is_good: bool = True) -> str:
    if pct is None:
        return "low_data"
    if abs(pct) < THRESHOLD_PCT:
        return "neutral"
    return "better" if (pct < 0) == down_is_good else "worse"


def judge(before: dict, after: dict, *, negatives: bool = False, zero_baseline_ok: bool = False) -> dict:
    """{verdict, metric, change_pct} по сумме кампаний до и после правки (правила — в начале модуля).

    zero_baseline_ok — до правки кампания и должна была стоять (запуск): «0 → 3+ заявки» засчитываем и без
    расхода и кликов до правки. У остальных правок пустое «до» значит, что кампании ещё не было, — «мало данных»."""
    out = {"verdict": "low_data", "metric": "none", "change_pct": None}
    if not after["cost_kopecks"] and not after["clicks"]:
        return out
    bc, ac = before["conversions"], after["conversions"]
    before_cpa = cpa(before)
    if bc >= MIN_CONVERSIONS or ac >= MIN_CONVERSIONS:
        if bc and ac:
            after_cpa = cpa(after)
            if not before_cpa or not after_cpa:
                return out  # заявки без расхода (бонусы, поздняя атрибуция) — цену заявки не сравнить
            pct = _pct(after_cpa, before_cpa)
            return {"verdict": _by_pct(pct), "metric": "cpa", "change_pct": round(pct, 1)}
        if not bc:
            # Заявок не было, а стало 3+ — правка помогла, хотя процент не посчитать.
            if not zero_baseline_ok and not before["cost_kopecks"] and not before["clicks"]:
                return out  # до правки кампания не работала — сравнивать не с чем
            return {"verdict": "better", "metric": "conversions", "change_pct": None}
        # Было 3+, после правки ни одной: хуже, если потратили хотя бы прежнюю цену заявки.
        if before_cpa and after["cost_kopecks"] >= before_cpa:
            return {"verdict": "worse", "metric": "conversions", "change_pct": None}
        return out
    if negatives and before["cost_kopecks"] >= COST_FLOOR_KOPECKS:
        pct = _pct(after["cost_kopecks"], before["cost_kopecks"])
        verdict = _by_pct(pct)
        if verdict == "better" and ac < bc:
            verdict = "neutral"  # расход упал вместе с заявками — не заслуга
        return {"verdict": verdict, "metric": "cost", "change_pct": round(pct, 1)}
    return out


def _rub(kopecks: float | None) -> str:
    if kopecks is None:
        return "—"
    return f"{round(kopecks / 100):,}".replace(",", _NBSP) + f"{_NBSP}₽"


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


_LEADS = ("заявка", "заявки", "заявок")
_DAYS = ("день", "дня", "дней")
_EDITS = ("правка", "правки", "правок")


def _signed_pct(pct: float) -> str:
    n = round(pct)
    if n == 0:
        return f"0{_NBSP}%"
    return f"{'+' if n > 0 else '−'}{abs(n)}{_NBSP}%"


def _side_text(side: dict) -> str:
    n = side["conversions"]
    text = f"{_rub(side['cost_kopecks'])}, {n} {_plural(n, _LEADS)}"
    if n:
        text += f", цена заявки {_rub(cpa(side))}"
    return text


def describe(row: dict) -> dict:
    """Строка для истории, главной проекта и кампании: {verdict, tone, text, hint, window_days}."""
    days = int(row["window_days"])
    span = f"за {days} {_plural(days, _DAYS)}"
    before = _side(row["before_cost_kopecks"], row["before_clicks"], row["before_conversions"])
    after = _side(row["after_cost_kopecks"], row["after_clicks"], row["after_conversions"])
    verdict, metric, pct = row["verdict"], row.get("metric") or "none", row.get("change_pct")
    if verdict == "low_data":
        text = f"Мало данных {span}"
    elif metric == "cpa" and pct is not None:
        text = f"Цена заявки {_signed_pct(pct)} {span}"
    elif metric == "cost" and pct is not None:
        text = f"Расход {_signed_pct(pct)} {span}"
    elif metric == "conversions":
        text = f"Заявки: {before['conversions']} → {after['conversions']} {span}"
    else:
        text = f"Без изменений {span}"
    parts = [
        f"До правки: {_side_text(before)}.",
        f"После: {_side_text(after)}.",
        f"Сравниваем {days} {_plural(days, _DAYS)} до и после, день правки не считаем.",
    ]
    if verdict == "low_data":
        parts.append("Заявок и расхода слишком мало, чтобы судить.")
    else:
        parts.append("Итог совпал по времени с правкой — на него влияют и сезон, и конкуренты.")
    cut = row.get("cut_cost_kopecks")
    if cut:
        parts.append(f"На отсечённые запросы до правки уходило около {_rub(cut)} {span}.")
    others = int(row.get("others") or 0)
    if others:
        parts.append(f"В эти же дни у этих кампаний были ещё {others} {_plural(others, _EDITS)}.")
    tone = {"better": "ok", "worse": "warn"}.get(verdict, "muted")
    return {
        "verdict": verdict,
        "tone": tone,
        "text": text,
        "hint": " ".join(parts),
        "window_days": days,
        "others": others,
    }


def windows_for(applied_day: date, days: int) -> tuple[tuple[date, date], tuple[date, date]]:
    """(до, после): N дней до дня правки и N дней после, сам день правки — ни там, ни там."""
    before = (applied_day - timedelta(days=days), applied_day - timedelta(days=1))
    after = (applied_day + timedelta(days=1), applied_day + timedelta(days=days))
    return before, after


def _day_of(value: datetime) -> date:
    return value.astimezone(MSK).date() if value.tzinfo else value.date()


def loaded_to(synced_at: datetime | None) -> date | None:
    """Последний день, за который статистика кабинета точно загружена (как insights._loaded_to)."""
    if synced_at is None:
        return None
    return _day_of(synced_at) - timedelta(days=1)


# --- Минус-фразы: сколько стоили отсечённые запросы --------------------------------------------

_WORD_RE = re.compile(r"[0-9a-zа-яё]+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower().replace("ё", "е"))


def _stem(word: str) -> str:
    # Грубо, без морфологии: «окна» и «окон» — разные, «пластиковые» и «пластиковых» — одно.
    return word[: max(4, len(word) - 2)] if len(word) > 4 else word


def phrase_matches(phrase: str, query: str) -> bool:
    """Минус-фраза отсекла бы запрос: все её слова (без операторов) есть в запросе с точностью до окончания."""
    pw = [_stem(w) for w in _words(phrase)]
    if not pw:
        return False
    qw = [_stem(w) for w in _words(query)]
    return all(any(q.startswith(p) or p.startswith(q) for q in qw) for p in pw)


def added_phrases(undo) -> dict[str, list[str]]:
    """Добавленные минус-фразы по шагам отката: {"campaigns": [...], "adgroups": [...]} — что откат уберёт."""
    out: dict[str, list[str]] = {"campaigns": [], "adgroups": []}
    steps = undo.get("steps") if isinstance(undo, dict) else None
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict) or step.get("op") != "negatives" or not isinstance(step.get("args"), dict):
            continue
        for kind in ("campaigns", "adgroups"):
            part = step["args"].get(kind)
            for change in (part.values() if isinstance(part, dict) else []):
                if isinstance(change, dict) and isinstance(change.get("remove"), list):
                    out[kind].extend(p for p in change["remove"] if isinstance(p, str) and p.strip())
    return out


def cut_cost(phrases: list[str], queries: list[dict], applied_day: date, days: int) -> int | None:
    """Оценка расхода на отсечённые запросы за days дней до правки: доля периода отчёта о запросах до правки."""
    if not phrases or not queries:
        return None
    total = 0
    span_days = None
    for q in queries:
        start, end = q.get("date_from"), q.get("date_to")
        if not start or not end or start >= applied_day:
            continue
        covered = (min(end, applied_day - timedelta(days=1)) - start).days + 1
        if covered <= 0:
            continue
        span_days = max(span_days or 0, covered)
        if any(phrase_matches(p, q.get("query") or "") for p in phrases):
            total += int(q.get("cost_kopecks") or 0)
    if not span_days or not total:
        return None
    return round(total / span_days * days)


# --- Какие кампании задела запись -----------------------------------------------------------------


def _ai_action_of(targets) -> int | None:
    from app.journal import AI_ACTION_TARGET

    for t in targets or []:
        if isinstance(t, str) and t.startswith(AI_ACTION_TARGET):
            try:
                return int(t[len(AI_ACTION_TARGET):])
            except ValueError:
                return None
    return None


def campaigns_of_row(user_id: int, row: dict) -> list[int]:
    """Кампании записи: из параметров, целей, ответа и через группы / объявления / фразы в целях."""
    from app.journal import _parse, _row_campaigns, campaigns_of

    payload, _c1 = _parse(row.get("payload"))
    result, _c2 = _parse(row.get("result"))
    ids = set(_row_campaigns(row, payload, result))
    by_kind: dict[str, list[int]] = defaultdict(list)
    for t in row.get("targets") or []:
        kind, _, rid = str(t).partition(":")
        if kind in ("adgroup", "keyword", "ad") and rid.isdigit():
            by_kind[kind].append(int(rid))
    for kind, rids in by_kind.items():
        ids.update(campaigns_of(user_id, kind, rids))
    return sorted(ids)


# --- Расчёт --------------------------------------------------------------------------------------


def _candidates(user_id: int, today: date) -> list[dict]:
    """Записи, у которых ещё не посчитано окно 14 дней и прошло хотя бы 7 дней после дня правки."""
    since = today - timedelta(days=LOOKBACK_DAYS)
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT l.id, l.action, l.title, l.workspace_id, l.payload, l.result, l.undo, l.targets,
                   l.created_at, l.undone_at, l.status,
                   COALESCE(ARRAY(SELECT e.window_days FROM action_effects e WHERE e.log_id = l.id), '{}') AS done
            FROM action_logs l
            WHERE l.user_id = %s AND l.undo_of IS NULL AND l.status IN ('ok', 'partial')
              AND l.action = ANY(%s)
              AND l.created_at >= %s AND l.created_at < %s
              AND NOT EXISTS (SELECT 1 FROM action_effects e WHERE e.log_id = l.id AND e.window_days = 14)
            ORDER BY l.id
            """,
            (
                user_id,
                list(QUALIFYING),
                datetime.combine(since, datetime.min.time(), MSK),
                datetime.combine(today - timedelta(days=min(WINDOWS)), datetime.min.time(), MSK),
            ),
        ).fetchall()


def _neighbours(user_id: int, date_from: date) -> list[dict]:
    """Все правки пользователя с date_from: для подсчёта «в эти же дни были ещё правки»."""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id, action, payload, result, targets, created_at FROM action_logs
            WHERE user_id = %s AND undo_of IS NULL AND status IN ('ok', 'partial') AND action = ANY(%s)
              AND created_at >= %s
            """,
            (user_id, list(QUALIFYING), datetime.combine(date_from, datetime.min.time(), MSK)),
        ).fetchall()


def compute_for_user(user_id: int, today: date | None = None) -> dict:
    """Досчитать окна 7 и 14 дней, у которых кончились дни и загружена статистика. Возвращает счётчики."""
    today = today or today_msk()
    rows = _candidates(user_id, today)
    if not rows:
        return {"computed": 0, "notified": 0}
    campaigns_by_log = {int(r["id"]): campaigns_of_row(user_id, r) for r in rows}
    all_ids = sorted({i for ids in campaigns_by_log.values() for i in ids})
    if not all_ids:
        return {"computed": 0, "notified": 0}
    first_day = min(_day_of(r["created_at"]) for r in rows) - timedelta(days=max(WINDOWS))
    with pool.connection() as conn:
        campaigns = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, a.stats_synced_at
            FROM direct_campaigns c JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.user_id = %s AND c.id = ANY(%s) AND c.campaign_id IS NOT NULL
            """,
            (user_id, all_ids),
        ).fetchall()
        camp = {int(c["id"]): c for c in campaigns}
        stats = (
            conn.execute(
                """
                SELECT account_id, campaign_id, day, cost_kopecks, clicks, conversions
                FROM stats_daily
                WHERE user_id = %s AND source = 'direct' AND account_id = ANY(%s) AND campaign_id = ANY(%s)
                  AND day >= %s AND day < %s
                """,
                (
                    user_id,
                    sorted({int(c["account_id"]) for c in campaigns}),
                    sorted({int(c["campaign_id"]) for c in campaigns}),
                    first_day,
                    today,
                ),
            ).fetchall()
            if campaigns
            else []
        )
    days: dict[tuple[int, int], dict[date, dict]] = defaultdict(dict)
    for s in stats:
        days[(int(s["account_id"]), int(s["campaign_id"]))][s["day"]] = s

    neighbours = [
        (int(n["id"]), _day_of(n["created_at"]), set(campaigns_of_row(user_id, n)), _ai_action_of(n["targets"]))
        for n in _neighbours(user_id, min(_day_of(r["created_at"]) for r in rows))
    ]

    computed = notified = 0
    for r in rows:
        log_id = int(r["id"])
        ids = [i for i in campaigns_by_log[log_id] if i in camp]
        if not ids:
            continue
        applied_day = _day_of(r["created_at"])
        loaded = [loaded_to(camp[i]["stats_synced_at"]) for i in ids]
        if any(d is None for d in loaded):
            continue
        ready_to = min(min(loaded), today - timedelta(days=1))
        ai_action = _ai_action_of(r["targets"])
        negatives = r["action"] in NEGATIVE_ACTIONS
        try:
            for window in WINDOWS:
                if window in (r["done"] or []):
                    continue
                (b_from, b_to), (a_from, a_to) = windows_for(applied_day, window)
                if a_to > ready_to:
                    continue
                if r["undone_at"] is not None and _day_of(r["undone_at"]) <= a_to:
                    continue  # вернули раньше, чем кончилось окно: «после» уже не про эту правку
                before, after = _side(), _side()
                for i in ids:
                    key = (int(camp[i]["account_id"]), int(camp[i]["campaign_id"]))
                    for day, s in days.get(key, {}).items():
                        side = before if b_from <= day <= b_to else after if a_from <= day <= a_to else None
                        if side is not None:
                            side["cost_kopecks"] += int(s["cost_kopecks"] or 0)
                            side["clicks"] += int(s["clicks"] or 0)
                            side["conversions"] += int(s["conversions"] or 0)
                res = judge(before, after, negatives=negatives, zero_baseline_ok=r["action"] == "resume")
                others = sum(
                    1
                    for nid, nday, nids, nai in neighbours
                    if nid != log_id
                    and applied_day <= nday <= a_to
                    and nids & set(ids)
                    and not (ai_action and nai == ai_action)
                )
                cut = _cut_for(user_id, r, ids, applied_day, window) if negatives else None
                inserted = _store(
                    user_id, r, ids, ai_action, window, before, after, res, cut, others
                )
                if inserted:
                    computed += 1
                    if res["verdict"] == "worse" and _notify_worse(user_id, r, ids, camp, inserted):
                        notified += 1
        except Exception:
            # Одна сбойная запись не должна держать итоги остальных: пропускаем её до следующего прогона.
            log.warning("effect for log %s failed", log_id, exc_info=True)
    return {"computed": computed, "notified": notified}


def _cut_for(user_id: int, row: dict, ids: list[int], applied_day: date, window: int) -> int | None:
    phrases = added_phrases(row.get("undo"))
    flat = [*phrases["campaigns"], *phrases["adgroups"]]
    if not flat:
        return None
    with pool.connection() as conn:
        queries = conn.execute(
            """
            SELECT query, cost_kopecks, date_from, date_to FROM direct_search_queries
            WHERE user_id = %s AND campaign_row_id = ANY(%s)
            """,
            (user_id, ids),
        ).fetchall()
    return cut_cost(flat, queries, applied_day, window)


def _store(user_id, row, ids, ai_action, window, before, after, res, cut, others) -> dict | None:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO action_effects (
                user_id, log_id, ai_action_id, workspace_id, campaign_row_ids, applied_at, window_days,
                before_cost_kopecks, before_clicks, before_conversions,
                after_cost_kopecks, after_clicks, after_conversions,
                metric, change_pct, verdict, cut_cost_kopecks, others
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (log_id, window_days) DO NOTHING
            RETURNING *
            """,
            (
                user_id,
                int(row["id"]),
                ai_action,
                row["workspace_id"],
                ids,
                row["created_at"],
                window,
                before["cost_kopecks"],
                before["clicks"],
                before["conversions"],
                after["cost_kopecks"],
                after["clicks"],
                after["conversions"],
                res["metric"],
                res["change_pct"],
                res["verdict"],
                cut,
                others,
            ),
        ).fetchone()


def _notify_worse(user_id: int, row: dict, ids: list[int], camp: dict, effect: dict) -> bool:
    """«После правки в «Окна» стало хуже» — одна запись на правку (dedup по записи журнала)."""
    from app.journal import can_undo
    from app.notifications import notify

    ws_id = row["workspace_id"]
    key = f"effect:{int(row['id'])}"
    name = ""
    with pool.connection() as conn:
        # Уже сообщали (например, по окну 7 дней) — окно 14 дней вторую запись не шлёт и старую не поднимает.
        if conn.execute(
            "SELECT 1 FROM notifications WHERE user_id = %s AND dedup_key = %s LIMIT 1", (user_id, key)
        ).fetchone():
            return False
        if ws_id:
            ws = conn.execute("SELECT name FROM workspaces WHERE id = %s", (ws_id,)).fetchone()
            name = (ws or {}).get("name") or ""
    if not name and len(ids) == 1:
        name = camp[ids[0]].get("name") or ""
    where = f" в «{name.strip('«»')}»" if name else ""
    text = describe(effect)["text"]
    base = f"/w/{ws_id}/history" if ws_id else "/cabinet/history"
    row_id = notify(
        user_id,
        NOTIFY_KIND,
        f"После правки{where} стало хуже",
        body=f"{text}. {row['title']}".strip(),
        link=f"{base}?log={int(row['id'])}",
        workspace_id=ws_id,
        severity="warn",
        dedup_key=key,
    )
    if row_id is None:
        return False
    if can_undo(row):
        with pool.connection() as conn:
            conn.execute("UPDATE notifications SET undo_log_id = %s WHERE id = %s", (int(row["id"]), row_id))
    return True


# --- Показ ---------------------------------------------------------------------------------------


def effects_for(user_id: int, log_ids: list[int]) -> dict[int, dict]:
    """{id записи: итог словами} — берём самое длинное посчитанное окно (14, иначе 7)."""
    ids = sorted({int(i) for i in log_ids or [] if i})
    if not ids:
        return {}
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (log_id) *
            FROM action_effects
            WHERE user_id = %s AND log_id = ANY(%s)
            ORDER BY log_id, window_days DESC
            """,
            (user_id, ids),
        ).fetchall()
    return {int(r["log_id"]): describe(r) for r in rows}


def attach(user_id: int, items: list[dict]) -> list[dict]:
    """Строки истории (journal.describe_logs) с полем effect; сбой — просто без итога."""
    try:
        found = effects_for(user_id, [i.get("id") for i in items])
    except Exception:
        log.warning("effects for history failed", exc_info=True)
        found = {}
    for item in items:
        item["effect"] = found.get(int(item["id"])) if item.get("id") else None
    return items


_PROJECTS = ("проекта", "проектов", "проектов")


def week_text(better: int, worse: int) -> str:
    if better and worse:
        return f"За неделю правки помогли у {better} {_plural(better, _PROJECTS)}, хуже — у {worse}"
    if better:
        return f"За неделю правки помогли у {better} {_plural(better, _PROJECTS)}"
    if worse:
        return f"За неделю после правок стало хуже у {worse} {_plural(worse, _PROJECTS)}"
    return ""


def week_summary(user_id: int) -> dict | None:
    """Общая главная: у скольких проектов правки помогли и у скольких стало хуже (итоги, посчитанные за 7 дней).

    У правки берём самое длинное окно; проект без правки «лучше/хуже» не считаем. Нет данных — None."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (log_id) log_id, workspace_id, verdict
            FROM action_effects
            WHERE user_id = %s AND computed_at >= NOW() - INTERVAL '7 days'
              -- Правку уже вернули — ни «помогла», ни «хуже» про неё не говорим.
              AND NOT EXISTS (
                  SELECT 1 FROM action_logs l WHERE l.id = action_effects.log_id AND l.undone_at IS NOT NULL
              )
            ORDER BY log_id, window_days DESC
            """,
            (user_id,),
        ).fetchall()
    better = {r["workspace_id"] or f"log:{r['log_id']}" for r in rows if r["verdict"] == "better"}
    worse = {r["workspace_id"] or f"log:{r['log_id']}" for r in rows if r["verdict"] == "worse"}
    text = week_text(len(better), len(worse))
    if not text:
        return None
    return {"text": text, "better": len(better), "worse": len(worse)}


def users_to_check() -> list[int]:
    """Кому есть что считать: правки за LOOKBACK_DAYS без итога за 14 дней."""
    since = today_msk() - timedelta(days=LOOKBACK_DAYS)
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT l.user_id FROM action_logs l
            WHERE l.undo_of IS NULL AND l.status IN ('ok', 'partial') AND l.action = ANY(%s)
              AND l.created_at >= %s
              AND NOT EXISTS (SELECT 1 FROM action_effects e WHERE e.log_id = l.id AND e.window_days = 14)
            """,
            (list(QUALIFYING), datetime.combine(since, datetime.min.time(), MSK)),
        ).fetchall()
    return [int(r["user_id"]) for r in rows]
