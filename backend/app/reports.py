"""Отчёт клиенту за месяц: одна страница с итогами, динамикой, кампаниями, «что сделано», планом и комментарием.

Период — календарный месяц (по умолчанию прошлый), сравнение — с месяцем до него. Цифры — те же, что в
«Отчётах» проекта (analytics.workspace_analytics), «что сделано» — записи журнала за месяц словами, как в
«Истории». План и комментарий специалист пишет сам (или «Написать комментарий с ИИ» — задачей, лимит тратится
только по нажатию). PDF — печатью браузера; готовым отчёт отмечает человек («Готово» или кнопка в подсказке после печати).

Оформление (логотип, название, контакты) — одно на пользователя (report_brands), набор разделов — на проект
(report_settings). Ничего не отправляется клиенту само и публичных ссылок нет.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import date, datetime, time, timedelta

from psycopg.types.json import Jsonb

from app import ai
from app.clock import MSK, today_msk
from app.db import pool
from app.overview import rub
from app.yandex import YandexError

# Разделы отчёта по порядку на странице.
SECTIONS = (
    ("totals", "Итоги месяца"),
    ("dynamics", "Динамика по дням"),
    ("campaigns", "Кампании"),
    ("done", "Что сделано"),
    ("plan", "План на следующий месяц"),
    ("comment", "Комментарий специалиста"),
)
SECTION_KEYS = tuple(k for k, _ in SECTIONS)

MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

TEXT_MAX = 5000
NAME_MAX = 120
CONTACTS_MAX = 300
LOGO_MAX_BYTES = 1_000_000
LOGO_TYPES = ("image/png", "image/jpeg", "image/svg+xml")
_ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DONE_LIMIT = 200
DONE_CHANGES = 3
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _bad(code: str, message: str) -> YandexError:
    return YandexError(code, message)


# --- Период ----------------------------------------------------------------------------------------


def _first(d: date) -> date:
    return d.replace(day=1)


def _prev_month(first: date) -> date:
    return _first(first - timedelta(days=1))


def _last_day(first: date) -> date:
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def default_month(today: date | None = None) -> date:
    """Прошлый календарный месяц: отчёт за октябрь делают в начале ноября."""
    return _prev_month(_first(today or today_msk()))


def parse_month(raw: str | None, today: date | None = None) -> date:
    """«2026-10» → 1 октября 2026; пусто — прошлый месяц. Будущие месяцы — ошибка."""
    today = today or today_msk()
    if not raw:
        return default_month(today)
    m = _MONTH_RE.match(str(raw).strip())
    if not m:
        raise _bad("month", "Месяц — в виде 2026-10")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12 or year < 2000:
        raise _bad("month", "Месяц — в виде 2026-10")
    first = date(year, month, 1)
    if first > _first(today):
        raise _bad("month", "Отчёт за месяц, который ещё не начался, не собрать")
    return first


def month_label(first: date) -> str:
    """1 октября 2026 → «октябрь 2026»."""
    return f"{MONTHS[first.month - 1]} {first.year}"


def month_key(first: date) -> str:
    return f"{first.year:04d}-{first.month:02d}"


def periods(first: date, today: date | None = None) -> dict:
    """Месяц отчёта (текущий — по вчера) и полный месяц до него."""
    today = today or today_msk()
    last = _last_day(first)
    if first == _first(today):
        last = max(first, min(last, today - timedelta(days=1)))
    prev_first = _prev_month(first)
    return {
        "date_from": first,
        "date_to": last,
        "prev_from": prev_first,
        "prev_to": _last_day(prev_first),
        "partial": last < _last_day(first),
    }


# --- Чистые расчёты (покрыты юнит-тестами) ---------------------------------------------------------


def delta(cur, prev) -> float | None:
    """Изменение в процентах; к нулю или без значения — None (не «+∞ %»)."""
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / prev * 100, 1)


TOTAL_KEYS = ("cost_kopecks", "clicks", "impressions", "ctr", "conversions", "cpa_kopecks")


def totals_block(cur: dict, prev: dict) -> list[dict]:
    """Итоги месяца: значение, прошлый месяц и изменение по каждой метрике."""
    return [
        {"key": key, "value": cur.get(key), "prev": prev.get(key), "delta": delta(cur.get(key), prev.get(key))}
        for key in TOTAL_KEYS
    ]


def campaigns_block(cur_rows: list[dict], prev_rows: list[dict]) -> list[dict]:
    """Кампании с показами за месяц или за прошлый месяц: расход, клики, CTR, заявки, цена заявки."""
    prev_by = {int(r["campaign_row_id"]): r for r in prev_rows}
    out = []
    for r in cur_rows:
        p = prev_by.get(int(r["campaign_row_id"])) or {}
        if not (r.get("impressions") or r.get("cost_kopecks") or p.get("cost_kopecks")):
            continue
        out.append(
            {
                "campaign_row_id": r["campaign_row_id"],
                "name": r.get("name") or "",
                "cost_kopecks": int(r.get("cost_kopecks") or 0),
                "clicks": int(r.get("clicks") or 0),
                "ctr": r.get("ctr"),
                "conversions": int(r.get("conversions") or 0),
                "cpa_kopecks": r.get("cpa_kopecks"),
                "prev_cost_kopecks": int(p.get("cost_kopecks") or 0),
                "prev_conversions": int(p.get("conversions") or 0),
                "cost_delta": delta(int(r.get("cost_kopecks") or 0), int(p.get("cost_kopecks") or 0)),
            }
        )
    out.sort(key=lambda r: (-r["cost_kopecks"], r["name"]))
    return out


def done_items(logs: list[dict]) -> list[dict]:
    """Записи журнала за месяц для клиента: что сделали, словами. Откаты и откатанное не показываем —
    для клиента итог «ничего не поменялось»."""
    out = []
    for item in logs:
        if item.get("undo_of") or item.get("undone_at") or item.get("action") == "undo":
            continue
        if item.get("status") not in ("ok", "partial"):
            continue
        changes = [str(c) for c in item.get("changes") or [] if c][:DONE_CHANGES]
        out.append(
            {
                "id": item["id"],
                "day": _msk_day(item.get("created_at")),
                "title": item.get("title") or "",
                "changes": changes,
                "source": item.get("source") or "user",
            }
        )
    return out


def _msk_day(value) -> str:
    """День записи по Москве — те же границы, что у месяца отчёта (_journal), а не часовой пояс сессии БД."""
    if not value:
        return ""
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)[:10]
    if moment.tzinfo is not None:
        moment = moment.astimezone(MSK)
    return moment.date().isoformat()


def normalize_sections(raw) -> dict[str, bool]:
    """Набор разделов: незнакомые ключи отбрасываются, недостающие — включены."""
    raw = raw if isinstance(raw, dict) else {}
    return {key: bool(raw.get(key, True)) for key in SECTION_KEYS}


def default_plan(ws: dict) -> str:
    """План по умолчанию — из целей проекта: цена заявки (или ДРР) и бюджет на месяц. Нет целей — пусто."""
    lines = []
    kpi_type = ws.get("kpi_type") or ws.get("client_kpi_type") or ""
    target = ws.get("kpi_target") if ws.get("kpi_type") else ws.get("client_kpi_target")
    if kpi_type and target is not None:
        if kpi_type == "drr":
            lines.append(f"Держать долю рекламных расходов не выше {float(target):g} %.")
        else:
            lines.append(f"Держать цену заявки не выше {rub(float(target) * 100)}.")
    budget = ws.get("monthly_budget_kopecks")
    if budget:
        lines.append(f"Бюджет на месяц — {rub(budget)}.")
    return "\n".join(lines)


def _clean_text(value, limit: int = TEXT_MAX) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) > limit:
        raise _bad("too-long", f"Текст длиннее {limit} знаков — сократите")
    return text


# --- Данные отчёта -----------------------------------------------------------------------------------


def _journal(user_id: int, workspace_id: int, date_from: date, date_to: date) -> list[dict]:
    from app.journal import describe_logs

    start = datetime.combine(date_from, time.min, MSK)
    end = datetime.combine(date_to + timedelta(days=1), time.min, MSK)
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.action, l.title, l.source, l.workspace_id, w.name AS workspace_name,
                   l.payload, l.result, l.status, l.created_at, l.undo, l.undone_at, l.undone_by, l.undo_of,
                   l.targets
            FROM action_logs l
            LEFT JOIN workspaces w ON w.id = l.workspace_id
            WHERE l.user_id = %s AND l.workspace_id = %s AND l.created_at >= %s AND l.created_at < %s
              AND l.status IN ('ok', 'partial') AND l.undo_of IS NULL AND l.undone_at IS NULL
            ORDER BY l.id
            LIMIT %s
            """,
            (user_id, workspace_id, start, end, DONE_LIMIT),
        ).fetchall()
    return describe_logs(user_id, rows)


def _saved(user_id: int, workspace_id: int, first: date) -> tuple[dict | None, dict]:
    with pool.connection() as conn:
        report = conn.execute(
            "SELECT plan_text, comment_text, done_at FROM client_reports WHERE workspace_id = %s AND month = %s "
            "AND user_id = %s",
            (workspace_id, first, user_id),
        ).fetchone()
        settings = conn.execute(
            "SELECT sections FROM report_settings WHERE workspace_id = %s AND user_id = %s",
            (workspace_id, user_id),
        ).fetchone()
    return report, normalize_sections(settings["sections"] if settings else None)


def build_report(user_id: int, workspace_id: int, month: str | None = None) -> dict:
    """Всё для страницы отчёта за месяц. Ничего не меняет."""
    from app.analytics import workspace_analytics
    from app.workspaces import require_workspace

    ws = require_workspace(user_id, workspace_id)
    first = parse_month(month)
    p = periods(first)
    cur = workspace_analytics(user_id, workspace_id, date_from=p["date_from"], date_to=p["date_to"], with_tops=False)
    prev = workspace_analytics(user_id, workspace_id, date_from=p["prev_from"], date_to=p["prev_to"], with_tops=False)
    report, sections = _saved(user_id, workspace_id, first)
    plan = report["plan_text"] if report and report["plan_text"] is not None else default_plan(ws)
    return {
        "workspace_id": workspace_id,
        "project": ws.get("name") or "",
        "client": ws.get("client_name") or "",
        "site": ws.get("site") or ws.get("counter_site") or "",
        "month": month_key(first),
        "month_label": month_label(first),
        "prev_label": month_label(p["prev_from"]),
        "date_from": p["date_from"].isoformat(),
        "date_to": p["date_to"].isoformat(),
        "prev_from": p["prev_from"].isoformat(),
        "prev_to": p["prev_to"].isoformat(),
        "partial": p["partial"],
        "conversions_source": cur.get("conversions_source") or "direct",
        "goal_name": cur.get("goal_name") or "",
        "totals": totals_block(cur.get("totals") or {}, prev.get("totals") or {}),
        "by_day": cur.get("by_day") or [],
        "prev_by_day": prev.get("by_day") or [],
        "campaigns": campaigns_block(cur.get("by_campaign") or [], prev.get("by_campaign") or []),
        "done": done_items(_journal(user_id, workspace_id, p["date_from"], p["date_to"])),
        "plan": plan,
        "comment": report["comment_text"] if report else "",
        "sections": sections,
        "section_list": [{"key": k, "label": label} for k, label in SECTIONS],
        "done_at": report["done_at"].isoformat() if report and report["done_at"] else None,
        "brand": get_brand(user_id),
    }


def save_report(
    user_id: int,
    workspace_id: int,
    month: str | None,
    *,
    plan: str | None = None,
    comment: str | None = None,
    sections: dict | None = None,
) -> dict:
    """План и комментарий — за месяц; набор разделов — на проект (в следующем месяце тот же)."""
    from app.workspaces import require_workspace

    require_workspace(user_id, workspace_id)
    first = parse_month(month)
    plan_text = _clean_text(plan) if plan is not None else None
    comment_text = _clean_text(comment) if comment is not None else None
    with pool.connection() as conn:
        if plan_text is not None or comment_text is not None:
            conn.execute(
                """
                INSERT INTO client_reports (workspace_id, month, user_id, plan_text, comment_text)
                VALUES (%s, %s, %s, %s, COALESCE(%s, ''))
                ON CONFLICT (workspace_id, month) DO UPDATE SET
                    plan_text = COALESCE(EXCLUDED.plan_text, client_reports.plan_text),
                    comment_text = CASE WHEN %s THEN EXCLUDED.comment_text ELSE client_reports.comment_text END,
                    updated_at = now()
                """,
                (workspace_id, first, user_id, plan_text, comment_text, comment_text is not None),
            )
        if sections is not None:
            clean = normalize_sections(sections)
            conn.execute(
                """
                INSERT INTO report_settings (workspace_id, user_id, sections) VALUES (%s, %s, %s)
                ON CONFLICT (workspace_id) DO UPDATE SET sections = EXCLUDED.sections, updated_at = now()
                """,
                (workspace_id, user_id, Jsonb(clean)),
            )
    report, saved_sections = _saved(user_id, workspace_id, first)
    return {
        "month": month_key(first),
        "plan": report["plan_text"] if report and report["plan_text"] is not None else None,
        "comment": report["comment_text"] if report else "",
        "sections": saved_sections,
    }


def mark_done(user_id: int, workspace_id: int, month: str | None, done: bool = True) -> dict:
    """«Готово» (или распечатали): отчёт за месяц отмечен; повторная отметка время не сдвигает."""
    from app.workspaces import require_workspace

    require_workspace(user_id, workspace_id)
    first = parse_month(month)
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO client_reports (workspace_id, month, user_id, done_at)
            VALUES (%s, %s, %s, CASE WHEN %s THEN now() END)
            ON CONFLICT (workspace_id, month) DO UPDATE SET
                done_at = CASE WHEN %s THEN COALESCE(client_reports.done_at, now()) END,
                updated_at = now()
            RETURNING done_at
            """,
            (workspace_id, first, user_id, done, done),
        ).fetchone()
    return {"month": month_key(first), "done_at": row["done_at"].isoformat() if row["done_at"] else None}


def reports_status(user_id: int, month: str | None = None) -> dict:
    """Для общего обзора: «Отчёты за октябрь: готово 3 из 9» — по рабочим (не архивным, не под замком) проектам."""
    from app.workspaces import list_workspaces

    first = parse_month(month)
    projects = [w for w in list_workspaces(user_id) if not w.get("archived") and not w.get("locked")]
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT workspace_id FROM client_reports WHERE user_id = %s AND month = %s AND done_at IS NOT NULL",
            (user_id, first),
        ).fetchall()
    done_ids = {int(r["workspace_id"]) for r in rows}
    items = [{"workspace_id": w["id"], "name": w["name"], "done": int(w["id"]) in done_ids} for w in projects]
    return {
        "month": month_key(first),
        "month_name": MONTHS[first.month - 1],
        "month_label": month_label(first),
        "done": sum(1 for i in items if i["done"]),
        "total": len(items),
        "items": items,
    }


# --- Оформление ------------------------------------------------------------------------------------


def _logo_url(row: dict) -> str:
    if not row or not row.get("logo"):
        return ""
    return f"data:{row['logo_mime']};base64,{base64.b64encode(bytes(row['logo'])).decode('ascii')}"


def get_brand(user_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT name, contacts, accent, logo, logo_mime FROM report_brands WHERE user_id = %s", (user_id,)
        ).fetchone()
    row = row or {}
    return {
        "name": row.get("name") or "",
        "contacts": row.get("contacts") or "",
        "accent": row.get("accent") or "",
        "logo": _logo_url(row),
    }


def save_brand(user_id: int, *, name: str = "", contacts: str = "", accent: str = "") -> dict:
    name = _clean_text(name, NAME_MAX)
    contacts = _clean_text(contacts, CONTACTS_MAX)
    accent = (accent or "").strip()
    if accent and not _ACCENT_RE.match(accent):
        raise _bad("accent", "Цвет — в виде #A3E635")
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO report_brands (user_id, name, contacts, accent) VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                name = EXCLUDED.name, contacts = EXCLUDED.contacts, accent = EXCLUDED.accent, updated_at = now()
            """,
            (user_id, name, contacts, accent),
        )
    return get_brand(user_id)


def decode_logo(data_url: str) -> tuple[bytes, str]:
    """«data:image/png;base64,…» → байты и тип. PNG, JPEG или SVG до 1 МБ."""
    head, _, payload = (data_url or "").partition(",")
    mime = head.removeprefix("data:").removesuffix(";base64")
    if not head.startswith("data:") or not head.endswith(";base64") or mime not in LOGO_TYPES:
        raise _bad("logo", "Логотип — картинка PNG, JPEG или SVG")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _bad("logo", "Не удалось прочитать картинку") from exc
    if not raw:
        raise _bad("logo", "Файл пустой")
    if len(raw) > LOGO_MAX_BYTES:
        raise _bad("logo", "Логотип больше 1 МБ — уменьшите картинку")
    if mime == "image/png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _bad("logo", "Файл не похож на PNG")
    if mime == "image/jpeg" and not raw.startswith(b"\xff\xd8\xff"):
        raise _bad("logo", "Файл не похож на JPEG")
    if mime == "image/svg+xml":
        text = raw.decode("utf-8", "ignore").lower()
        if "<svg" not in text:
            raise _bad("logo", "Файл не похож на SVG")
        # В <img> скрипты SVG не выполняются, но файл со скриптом — подозрительный: просим PNG.
        if "<script" in text or "javascript:" in text:
            raise _bad("logo", "В SVG есть скрипты — сохраните логотип как PNG")
    return raw, mime


def set_logo(user_id: int, data_url: str | None) -> dict:
    """Новый логотип; None — убрать."""
    raw, mime = decode_logo(data_url) if data_url else (None, "")
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO report_brands (user_id, logo, logo_mime) VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET logo = EXCLUDED.logo, logo_mime = EXCLUDED.logo_mime, updated_at = now()
            """,
            (user_id, raw, mime),
        )
    return get_brand(user_id)


# --- Комментарий с ИИ ----------------------------------------------------------------------------------

_COMMENT_SYSTEM = """Ты — специалист по рекламе в Яндекс Директе. Напиши комментарий к месячному отчёту для клиента — владельца бизнеса, не маркетолога.
- 4–7 предложений, по-русски, спокойно и по делу, от первого лица специалиста («мы»).
- Что получилось за месяц и почему: заявки, их цена, расход — сравни с прошлым месяцем, называй цифры из данных.
- Что сделали и зачем — коротко, своими словами, без технических терминов (никаких CTR, CPA, РСЯ, ставок; пиши «цена заявки», «доля кликов», «показы на сайтах-партнёрах»).
- Чем закончить: что планируем дальше.
- Не выдумывай фактов и цифр, которых нет в данных. Если данных мало — так и скажи.
Ответ — только текст комментария, без заголовка и списков."""


def _num(value) -> str:
    return f"{value:,}".replace(",", " ") if isinstance(value, int) else str(value)


def _pct(value) -> str:
    return "—" if value is None else f"{value:+.0f} %"


def comment_prompt(report: dict, brief: str) -> str:
    """Данные отчёта для ИИ: итоги с изменениями, главные кампании, что сделано, план."""
    labels = {
        "cost_kopecks": "Расход",
        "clicks": "Клики",
        "impressions": "Показы",
        "ctr": "Доля кликов от показов, %",
        "conversions": "Заявки",
        "cpa_kopecks": "Цена заявки",
    }
    lines = [brief, "", f"Отчёт за месяц: {report['month_label']}. Сравниваем с прошлым месяцем: {report['prev_label']}."]
    if report.get("partial"):
        lines.append(f"Месяц ещё идёт: данные по {report['date_to']}.")
    for t in report["totals"]:
        money = t["key"].endswith("_kopecks")
        fmt = (lambda v: rub(v) if v is not None else "—") if money else (lambda v: "—" if v is None else _num(v))
        lines.append(f"- {labels[t['key']]}: {fmt(t['value'])} (было {fmt(t['prev'])}, изменение {_pct(t['delta'])})")
    if report["campaigns"]:
        lines.append("Кампании (по расходу):")
        for c in report["campaigns"][:8]:
            cpa = rub(c["cpa_kopecks"]) if c["cpa_kopecks"] is not None else "—"
            lines.append(
                f"- «{c['name']}»: расход {rub(c['cost_kopecks'])}, заявок {c['conversions']}, цена заявки {cpa}"
            )
    if report["done"]:
        lines.append("Что сделано за месяц:")
        for d in report["done"][:30]:
            extra = "; ".join(d["changes"][:2])
            lines.append(f"- {d['day']}: {d['title']}" + (f" — {extra}" if extra else ""))
    else:
        lines.append("Изменений в рекламе за месяц не записано.")
    if (report.get("plan") or "").strip():
        lines.append("План на следующий месяц: " + report["plan"].strip())
    return "\n".join(lines)


def ai_comment(user_id: int, payload: dict, progress=None) -> dict:
    """Задача: черновик комментария специалиста. Ничего не сохраняет — текст попадает в поле, правит человек."""
    from app import ai_memory
    from app.brief import brief_prompt
    from app.workspaces import require_workspace

    progress = progress or (lambda *_a: None)
    workspace_id = int(payload.get("workspace_id") or 0)
    ws = require_workspace(user_id, workspace_id)
    progress(0, 2, "Собираю данные отчёта")
    report = build_report(user_id, workspace_id, payload.get("month") or None)
    cfg = ai.load_config(user_id, "report", workspace_id)
    memory = "\n".join(ai_memory.memory_lines(ws, ai_memory.account_prefs(user_id))).strip()
    system = _COMMENT_SYSTEM + ("\n\n" + memory if memory else "")
    progress(1, 2, "ИИ пишет комментарий")
    with ai.meter() as spent:
        reply = ai.complete(
            cfg,
            [{"role": "system", "content": system}, {"role": "user", "content": comment_prompt(report, brief_prompt(ws))}],
        )
    text = (reply.get("content") or "").strip()
    if not text:
        raise _bad("ai-empty", "ИИ не написал комментарий — попробуйте ещё раз")
    progress(2, 2, "Готово")
    return {"comment": text[:TEXT_MAX], "usage": ai.usage_out(spent, user_id)}
