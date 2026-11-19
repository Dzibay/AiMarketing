"""Разбор недели по расписанию: ИИ сам разбирает все проекты (или выбранные) раз в неделю.

Включает только сам пользователь (Настройки → ИИ), по умолчанию выключено. В назначенный день и час (по
Москве: часового пояса пользователя сервис не хранит) задача ai_scheduled_review запускает в каждом проекте
обычный «Разбор» (ai_agent.start_review) в новом чате «Разбор недели · 17 ноября». ИИ только предлагает:
находки — карточками, решает человек; в Директе разбор ничего не меняет.

Лимит ИИ: перед каждым проектом проверяем остаток с запасом на типичный разбор — не хватает, проект
пропускаем и пишем об этом в сводке. Когда все чаты ответили — одна сводка в колокольчик (и в Telegram по
настройкам уведомлений): у кого что нашлось, со ссылками на чаты.

Одна неделя — один запуск: ai_review_runs UNIQUE (user_id, week_start).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app import billing, plans
from app.clock import MSK, now_msk
from app.db import pool
from app.yandex import YandexError

log = logging.getLogger(__name__)

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS_GEN = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
DEFAULT_WEEKDAY = 0
DEFAULT_HOUR = 9
# Пропустили назначенное время больше чем на сутки (сервис стоял, подписка была на паузе) — не догоняем,
# а ждём следующей недели: разбор в неожиданный день удивит больше, чем пропуск.
CATCH_UP = timedelta(hours=24)
# Чаты отвечают задачами; дольше этого сводку не ждём — пишем, что успело.
SUMMARY_WAIT = timedelta(hours=3)
# Типичный разбор — средний расход первых ответов последних разборов (без продолжения разговора в чате).
FIRST_TURN = timedelta(hours=1)
SAMPLE_OWN, SAMPLE_ALL = 20, 50
REVIEW_PROMPT = (
    "Разбор недели: что работает, что затухает и почему, что стоит поправить. Изменения — только предложениями"
)
# Ошибки, после которых дальше запускать бессмысленно: у всех остальных проектов будет то же самое.
STOP_CODES = ("ai-not-configured", "units-exhausted", "subscription-expired", "email-unverified")
LIMIT_REASON = "Не хватило лимита ИИ"


# --- Время ----------------------------------------------------------------------------------------


def next_run(weekday: int, hour: int, after: datetime) -> datetime:
    """Ближайший момент «день недели, час» по Москве строго позже after."""
    local = after.astimezone(MSK)
    day = local.date() + timedelta(days=(weekday - local.weekday()) % 7)
    at = datetime(day.year, day.month, day.day, hour, tzinfo=MSK)
    if at <= local:
        at += timedelta(days=7)
    return at


def week_of(moment: datetime) -> date:
    """Понедельник недели (по Москве), к которой относится момент."""
    day = moment.astimezone(MSK).date()
    return day - timedelta(days=day.weekday())


def chat_title(day: date) -> str:
    return f"Разбор недели · {day.day} {MONTHS_GEN[day.month - 1]}"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


# --- Настройка ------------------------------------------------------------------------------------


def _projects(user_id: int) -> list[dict]:
    from app.workspaces import list_workspaces

    return list_workspaces(user_id)


def _row(conn, user_id: int, *, lock: bool = False) -> dict | None:
    sql = "SELECT * FROM ai_schedules WHERE user_id = %s" + (" FOR UPDATE" if lock else "")
    return conn.execute(sql, (user_id,)).fetchone()


def review_units(user_id: int) -> float | None:
    """Сколько единиц обычно уходит на разбор одного проекта: свои прошлые разборы, иначе — по сервису.

    Разбор — чат из запуска по расписанию или чат кнопки «Разбор» (заголовок «Разбор …»); считаем только
    первый час чата, чтобы продолжение разговора не раздувало оценку. Разборов ещё не было — None.
    """
    sql = """
        SELECT AVG(s) AS avg FROM (
            SELECT SUM(u.charged) AS s
            FROM ai_chats ch
            JOIN ai_usage u ON u.chat_id = ch.id AND u.created_at < ch.created_at + %s
            WHERE (%s::bigint IS NULL OR ch.user_id = %s)
              AND (ch.title LIKE 'Разбор%%' OR EXISTS (SELECT 1 FROM ai_review_chats rc WHERE rc.chat_id = ch.id))
            GROUP BY ch.id
            ORDER BY ch.id DESC
            LIMIT %s
        ) t WHERE s > 0
    """
    with pool.connection() as conn:
        for who, size in ((user_id, SAMPLE_OWN), (None, SAMPLE_ALL)):
            row = conn.execute(sql, (FIRST_TURN, who, who, size)).fetchone()
            if row and row["avg"]:
                return float(row["avg"])
    return None


def estimate(user_id: int) -> dict:
    """Доля лимита на разбор одного проекта — в процентах, без единиц и денег.

    {"unlimited": True} — лимита нет; per_project_percent None — разборов ещё не было, оценить не из чего.
    """
    limit = billing.units_limit(user_id)
    if not limit:
        return {"unlimited": True, "per_project_percent": None}
    per = review_units(user_id)
    return {"unlimited": False, "per_project_percent": round(per * 100 / limit, 2) if per else None}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _scope_ids(row: dict, projects: list[dict]) -> list[int]:
    """Проекты запуска: все открытые или выбранные (которые ещё есть и не в архиве)."""
    ids = [int(p["id"]) for p in projects]
    if row["scope"] == "all":
        return ids
    chosen = {int(i) for i in row["workspace_ids"] or []}
    return [i for i in ids if i in chosen]


def get_schedule(user_id: int) -> dict:
    from app import ai

    with pool.connection() as conn:
        row = _row(conn, user_id)
        last = conn.execute(
            "SELECT * FROM ai_review_runs WHERE user_id = %s ORDER BY week_start DESC LIMIT 1", (user_id,)
        ).fetchone()
    row = row or {
        "enabled": False, "weekday": DEFAULT_WEEKDAY, "hour": DEFAULT_HOUR, "scope": "all", "workspace_ids": [],
        "last_run_at": None, "next_run_at": None,
    }
    projects = [p for p in _projects(user_id) if not p["locked"]]
    return {
        "enabled": bool(row["enabled"]),
        "weekday": int(row["weekday"]),
        "hour": int(row["hour"]),
        "scope": row["scope"],
        "workspace_ids": [int(i) for i in row["workspace_ids"] or []],
        "next_run_at": _iso(row["next_run_at"]) if row["enabled"] else None,
        "last_run_at": _iso(row["last_run_at"]),
        "last_week": last["week_start"].isoformat() if last else None,
        "projects": [{"id": int(p["id"]), "name": p["name"], "client_name": p.get("client_name") or ""} for p in projects],
        "ai_ready": ai.is_configured(),
        **estimate(user_id),
    }


def save_schedule(
    user_id: int,
    *,
    enabled: bool,
    weekday: int | None = None,
    hour: int | None = None,
    scope: str | None = None,
    workspace_ids: list[int] | None = None,
) -> dict:
    """Сохранить настройку; следующий запуск пересчитывается от «сейчас» (прошедшая неделя не повторится)."""
    with pool.connection() as conn:
        row = _row(conn, user_id, lock=True) or {}
        weekday = int(row.get("weekday", DEFAULT_WEEKDAY) if weekday is None else weekday)
        hour = int(row.get("hour", DEFAULT_HOUR) if hour is None else hour)
        scope = (row.get("scope") or "all") if scope is None else scope
        if not 0 <= weekday <= 6:
            raise YandexError("bad-weekday", "Выберите день недели")
        if not 0 <= hour <= 23:
            raise YandexError("bad-hour", "Выберите час")
        if scope not in ("all", "selected"):
            raise YandexError("bad-scope", "Выберите, какие проекты разбирать")
        if workspace_ids is None:
            ids = [int(i) for i in row.get("workspace_ids") or []]
        else:
            own = {
                int(r["id"])
                for r in conn.execute(
                    "SELECT id FROM workspaces WHERE user_id = %s AND id = ANY(%s)",
                    (user_id, [int(i) for i in workspace_ids]),
                ).fetchall()
            }
            ids = sorted(own)
        if enabled and scope == "selected" and not ids:
            raise YandexError("bad-scope", "Отметьте хотя бы один проект")
        nxt = _next_after_save(conn, user_id, row, enabled, weekday, hour)
        conn.execute(
            """
            INSERT INTO ai_schedules (user_id, enabled, weekday, hour, scope, workspace_ids, next_run_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET enabled = EXCLUDED.enabled, weekday = EXCLUDED.weekday,
                hour = EXCLUDED.hour, scope = EXCLUDED.scope, workspace_ids = EXCLUDED.workspace_ids,
                next_run_at = EXCLUDED.next_run_at, updated_at = NOW()
            """,
            (user_id, bool(enabled), weekday, hour, scope, ids, nxt),
        )
    return get_schedule(user_id)


def _next_after_save(conn, user_id: int, row: dict, enabled: bool, weekday: int, hour: int) -> datetime | None:
    """Следующий запуск после сохранения.

    Срок уже наступил, а планировщик ещё не занял неделю, и день с часом не менялись — срок остаётся (иначе
    отметка ещё одного проекта отменила бы разбор этой недели). Неделя уже разобрана — следующая.
    """
    if not enabled:
        return None
    now = now_msk()
    kept = row.get("next_run_at")
    if (
        row.get("enabled")
        and kept is not None
        and kept <= now
        and int(row.get("weekday", -1)) == weekday
        and int(row.get("hour", -1)) == hour
    ):
        nxt = kept
    else:
        nxt = next_run(weekday, hour, now)
    done = {
        r["week_start"]
        for r in conn.execute(
            "SELECT week_start FROM ai_review_runs WHERE user_id = %s AND week_start >= %s",
            (user_id, week_of(nxt)),
        ).fetchall()
    }
    while week_of(nxt) in done:
        monday = week_of(nxt) + timedelta(days=7)
        nxt = next_run(weekday, hour, datetime(monday.year, monday.month, monday.day, tzinfo=MSK) - timedelta(seconds=1))
    return nxt


# --- Планировщик ----------------------------------------------------------------------------------


def due(now: datetime | None = None) -> list[tuple[int, str]]:
    """Кому пора: (user_id, неделя ISO). Пропущенное больше чем на сутки — переносится на следующую неделю."""
    now = now or now_msk()
    with pool.connection() as conn:
        stale = conn.execute(
            "SELECT user_id, weekday, hour FROM ai_schedules WHERE enabled AND next_run_at <= %s",
            (now - CATCH_UP,),
        ).fetchall()
        for r in stale:
            conn.execute(
                "UPDATE ai_schedules SET next_run_at = %s WHERE user_id = %s",
                (next_run(int(r["weekday"]), int(r["hour"]), now), r["user_id"]),
            )
        rows = conn.execute(
            "SELECT user_id, next_run_at FROM ai_schedules WHERE enabled AND next_run_at <= %s ORDER BY user_id",
            (now,),
        ).fetchall()
    return [(int(r["user_id"]), week_of(r["next_run_at"]).isoformat()) for r in rows]


def open_runs() -> list[tuple[int, str]]:
    """Запуски, по которым сводка ещё не ушла: (user_id, неделя ISO)."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT user_id, week_start FROM ai_review_runs WHERE notified_at IS NULL ORDER BY id"
        ).fetchall()
    return [(int(r["user_id"]), r["week_start"].isoformat()) for r in rows]


def pending_summary_chat(chat_id: int) -> bool:
    """Чат разбора по расписанию, сводка по которому ещё не ушла: отдельное «ИИ ответил» о нём не нужно."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM ai_review_chats rc JOIN ai_review_runs r ON r.id = rc.run_id
            WHERE rc.chat_id = %s AND r.notified_at IS NULL LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
    return row is not None


def is_review_chat(chat_id: int | None) -> bool:
    """Чат разбора по расписанию: ИИ в нём только предлагает — действия ждут решения при любых правах ИИ."""
    if chat_id is None:
        return False
    with pool.connection() as conn:
        row = conn.execute("SELECT 1 FROM ai_review_chats WHERE chat_id = %s LIMIT 1", (chat_id,)).fetchone()
    return row is not None


# --- Запуск ---------------------------------------------------------------------------------------


def _claim(user_id: int, week: date, now: datetime) -> int | None:
    """Занять неделю: id нового запуска или None (не пора, выключено или неделя уже разобрана)."""
    with pool.connection() as conn:
        row = _row(conn, user_id, lock=True)
        if row is None or not row["enabled"] or row["next_run_at"] is None or row["next_run_at"] > now:
            return None
        if week_of(row["next_run_at"]) != week:
            return None
        conn.execute(
            "UPDATE ai_schedules SET last_run_at = %s, next_run_at = %s WHERE user_id = %s",
            (now, next_run(int(row["weekday"]), int(row["hour"]), now), user_id),
        )
        run = conn.execute(
            """
            INSERT INTO ai_review_runs (user_id, week_start) VALUES (%s, %s)
            ON CONFLICT (user_id, week_start) DO NOTHING RETURNING id
            """,
            (user_id, week),
        ).fetchone()
        return int(run["id"]) if run else None


def _mark(run_id: int, ws: dict, *, chat_id: int | None = None, reason: str = "") -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO ai_review_chats (run_id, workspace_id, chat_id, name, status, reason)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (run_id, ws["id"], chat_id, ws["name"], "started" if chat_id else "skipped", reason),
        )


def _start_projects(user_id: int, run_id: int, now: datetime) -> dict:
    from app import ai_agent
    from app.plain_errors import plain_error

    with pool.connection() as conn:
        row = _row(conn, user_id)
    all_projects = _projects(user_id)
    chosen = set(_scope_ids(row, all_projects))
    projects = [p for p in all_projects if int(p["id"]) in chosen]
    sub = plans.subscription(user_id)
    stop = "" if sub["state"] == "ok" else "Подписка не действует"
    per = review_units(user_id)
    left = billing.units_left(user_id)
    reserved = 0.0
    title = chat_title(now.astimezone(MSK).date())
    started = skipped = 0
    for ws in projects:
        reason = stop
        if not reason and ws["locked"]:
            reason = "Проект не входит в тариф"
        elif not reason and not ws.get("campaigns_count"):
            reason = "В проекте нет кампаний"
        elif not reason and left is not None and (left - reserved <= 0 or (per and reserved + per > left)):
            reason = LIMIT_REASON
        if reason:
            _mark(run_id, ws, reason=reason)
            skipped += 1
            continue
        try:
            res = ai_agent.start_review(user_id, int(ws["id"]), prompt=REVIEW_PROMPT, title=title)
        except YandexError as exc:
            reason = LIMIT_REASON if exc.code == "units-exhausted" else plain_error(exc.code, exc.message)
            if exc.code in STOP_CODES:
                stop = reason
            _mark(run_id, ws, reason=reason)
            skipped += 1
            continue
        except Exception:  # noqa: BLE001
            log.exception("Разбор по расписанию: проект %s", ws["id"])
            _mark(run_id, ws, reason=plain_error("internal"))
            skipped += 1
            continue
        _mark(run_id, ws, chat_id=int(res["chat"]["id"]))
        reserved += per or 0.0
        started += 1
    with pool.connection() as conn:
        conn.execute("UPDATE ai_review_runs SET finished_at = NOW() WHERE id = %s", (run_id,))
    return {"started": started, "skipped": skipped}


def _summary(rows: list[dict]) -> dict:
    """Текст сводки: что нашлось, где без замечаний, что пропущено и почему."""
    from app.settings_store import public_base_url

    found = [r for r in rows if r["chat_id"] and r["pending"]]
    # Ход ИИ сорвался или не успел — это не «без замечаний»: проект не проверен.
    failed = [r for r in rows if r["chat_id"] and not r["pending"] and r.get("status") == "error"]
    late = [r for r in rows if r["chat_id"] and not r["pending"] and r.get("status") == "running"]
    clean = [r for r in rows if r["chat_id"] and not r["pending"] and r.get("status") not in ("error", "running")]
    skipped = [r for r in rows if not r["chat_id"]]
    parts = []
    if found:
        n = len(found)
        title = f"Разбор недели: у {n} {_plural(n, 'клиента', 'клиентов', 'клиентов')} нашлось что поправить"
        parts.append(
            ", ".join(
                f"{r['name']} ({r['pending']} {_plural(r['pending'], 'предложение', 'предложения', 'предложений')})"
                for r in found
            )
        )
        severity = "info"
    elif clean and not (failed or late):
        title = "Разбор недели: замечаний нет"
        severity = "ok"
    elif clean:
        title = "Разбор недели: замечаний нет, но не все проекты проверены"
        severity = "warn"
    else:
        title = "Разбор недели не прошёл"
        severity = "warn"
    if clean and found:
        parts.append(f"Без замечаний: {', '.join(r['name'] for r in clean)}")
    elif clean:
        parts.append(f"Проверили: {', '.join(r['name'] for r in clean)}")
    if failed:
        parts.append(f"Разбор не прошёл: {', '.join(r['name'] for r in failed)}")
    if late:
        parts.append(f"Не успел ответить: {', '.join(r['name'] for r in late)}")
    by_reason: dict[str, list[str]] = {}
    for r in skipped:
        by_reason.setdefault(r["reason"] or "Не удалось запустить", []).append(r["name"])
    for reason, names in by_reason.items():
        parts.append(f"{reason}: {', '.join(names)}")
    if len(found) == 1:
        link = f"/w/{found[0]['workspace_id']}/chat/{found[0]['chat_id']}"
    elif found:
        link = "/cabinet/pending"
    elif clean:
        link = f"/w/{clean[0]['workspace_id']}/chat/{clean[0]['chat_id']}" if len(clean) == 1 else ""
    elif failed or late:
        r = (failed or late)[0]
        link = f"/w/{r['workspace_id']}/chat/{r['chat_id']}" if len(failed) + len(late) == 1 else "/cabinet/settings/ai"
    else:
        link = "/cabinet/settings/ai"
    base = public_base_url().rstrip("/")
    tg = [title]
    for r in found:
        tg.append(
            f"{r['name']} — {r['pending']} {_plural(r['pending'], 'предложение', 'предложения', 'предложений')}: "
            f"{base}/w/{r['workspace_id']}/chat/{r['chat_id']}"
        )
    tg.extend(parts[1:] if found else parts)
    if not found and link:
        tg.append(f"{base}{link}")
    return {
        "title": title,
        "body": ". ".join(parts),
        "link": link,
        "severity": severity,
        "telegram": "\n".join(tg),
        "found": len(found),
    }


def _summarize(user_id: int, week: date, now: datetime) -> dict | None:
    """Все чаты запуска ответили (или ждать дольше нет смысла) — одна сводка; иначе None."""
    from app.ai_agent import STALE_RUNNING
    from app.notifications import notify

    with pool.connection() as conn:
        run = conn.execute(
            "SELECT * FROM ai_review_runs WHERE user_id = %s AND week_start = %s", (user_id, week)
        ).fetchone()
        if run is None or run["notified_at"] is not None:
            return None
        timed_out = now - run["started_at"] > SUMMARY_WAIT
        if run["finished_at"] is None and not timed_out:
            return None  # запуск ещё раздаёт проекты
        rows = conn.execute(
            """
            SELECT rc.workspace_id, rc.chat_id, rc.name, rc.reason, ch.status, ch.updated_at,
                   EXISTS (
                       SELECT 1 FROM jobs j
                       WHERE j.kind = 'ai_chat' AND j.status IN ('queued', 'running')
                         AND j.payload->>'chat_id' = rc.chat_id::text
                   ) AS queued,
                   (SELECT COUNT(*) FROM ai_actions a WHERE a.chat_id = rc.chat_id AND a.status = 'pending') AS pending
            FROM ai_review_chats rc LEFT JOIN ai_chats ch ON ch.id = rc.chat_id
            WHERE rc.run_id = %s ORDER BY rc.id
            """,
            (run["id"],),
        ).fetchall()
        # Задача чата может долго стоять в очереди (воркеров мало, разборы у всех в понедельник 9:00), и
        # updated_at всё это время не обновляется: занят тот чат, у которого задача ещё в очереди или идёт.
        busy = [
            r for r in rows
            if r["chat_id"] and r["status"] == "running"
            and (r["queued"] or (r["updated_at"] and now - r["updated_at"] <= STALE_RUNNING))
        ]
        if busy and not timed_out:
            return None
        claimed = conn.execute(
            "UPDATE ai_review_runs SET notified_at = NOW() WHERE id = %s AND notified_at IS NULL RETURNING id",
            (run["id"],),
        ).fetchone()
    if claimed is None:
        return None
    # «Работает», но задачи уже нет и чат давно не обновлялся — ответ прервался, как и показывает чат.
    items = [
        {
            **r,
            "pending": int(r["pending"] or 0),
            "status": "error" if r["status"] == "running" and r not in busy else r["status"],
        }
        for r in rows
    ]
    if not items:
        return {"found": 0, "sent": False}
    text = _summary(items)
    notify(
        user_id,
        "ai_review",
        text["title"],
        body=text["body"],
        link=text["link"],
        severity=text["severity"],
        dedup_key=f"ai_review:{run['id']}",
        telegram_text=text["telegram"],
    )
    return {"found": text["found"], "sent": True}


def run_scheduled(user_id: int, week_iso: str, now: datetime | None = None) -> dict:
    """Задача ai_scheduled_review: занять неделю и запустить разборы, затем — сводка, когда чаты ответят.

    Планировщик ставит задачу и для пора начинать, и для ждущих сводки запусков; повтор безопасен.
    """
    now = now or now_msk()
    week = date.fromisoformat(week_iso)
    out: dict = {"week": week_iso}
    run_id = _claim(user_id, week, now)
    if run_id is not None:
        out.update(_start_projects(user_id, run_id, now))
    summary = _summarize(user_id, week, now)
    if summary is not None:
        out["summary"] = summary
    return out
