"""Проекты: счётчик Метрики (сайт) + кампании Директа из любых кабинетов.

Проект создаётся на каждый номер счётчика, который видят подключения с ролью Метрики.
Кабинеты Директа подключаются к проекту явно; кампания живёт не больше чем в одном
проекте — её статистика и цели считаются по одному сайту. Новые кампании, в настройках
которых указан счётчик проекта, попадают в него сами.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from psycopg.types.json import Jsonb

from app.brief import normalize_brief
from app.db import pool
from app.yandex import YandexError

log = logging.getLogger(__name__)

# Категории действий для настроек доступа ИИ. Чтение доступно всегда.
AI_CATEGORIES = [
    ("pause_resume", "Остановка и запуск кампаний, групп, ключей"),
    ("budget", "Дневные бюджеты"),
    ("bids", "Ставки ключевых фраз"),
    ("negatives", "Минус-фразы"),
    ("keywords", "Добавление ключевых фраз"),
    ("ads", "Объявления: создание и правка"),
    ("campaigns", "Создание, копирование и настройки кампаний"),
    ("destructive", "Удаление и архивирование"),
]
DEFAULT_AI_ACCESS = {
    "full_access": False,
    "categories": {key: False for key, _ in AI_CATEGORIES},
    # Предел разового изменения бюджета/ставки без согласования, %.
    "max_change_pct": 30,
}
KPI_TYPES = ("", "cpa", "drr")
# Ответов маркетолога, которые ИИ помнит по проекту; старые вытесняются.
MAX_AI_FACTS = 30
# Ключ ответа о механике конверсий за 0 ₽.
SCHEME_FACT = "zero_cost_scheme"
# Как ИИ пишет объявления и ответы в проекте: тон и обращение — подсказкой модели.
AI_TONES = {
    "calm": "спокойный: без давления, восклицаний и громких обещаний",
    "sell": "продающий: выгода клиента и чёткий призыв к действию, без преувеличений",
    "expert": "экспертный: факты, цифры, точные формулировки",
}
AI_ADDRESS = {"vy": "на «вы»", "ty": "на «ты»"}
DEFAULT_AI_STYLE = {"tone": "calm", "address": "vy", "model_class": ""}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def normalize_ai_access(raw: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_AI_ACCESS)
    if not isinstance(raw, dict):
        return out
    out["full_access"] = bool(raw.get("full_access"))
    cats = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    for key in out["categories"]:
        if key in cats:
            out["categories"][key] = bool(cats[key])
    try:
        pct = int(raw.get("max_change_pct", out["max_change_pct"]))
    except (TypeError, ValueError):
        pct = out["max_change_pct"]
    out["max_change_pct"] = max(0, min(pct, 500))
    return out


def normalize_ai_style(raw: dict | None) -> dict:
    """Тон, обращение и модель проекта; неизвестные значения — по умолчанию."""
    out = dict(DEFAULT_AI_STYLE)
    if not isinstance(raw, dict):
        return out
    if raw.get("tone") in AI_TONES:
        out["tone"] = raw["tone"]
    if raw.get("address") in AI_ADDRESS:
        out["address"] = raw["address"]
    out["model_class"] = str(raw.get("model_class") or "").strip()[:40]
    return out


def ai_style_prompt(ws: dict) -> str:
    """Тон и обращение проекта — строкой для подсказки ИИ."""
    style = normalize_ai_style(ws.get("ai_style"))
    return (
        f"Тон текстов для клиентов — {AI_TONES[style['tone']]}. "
        f"К клиентам в объявлениях обращаемся {AI_ADDRESS[style['address']]}."
    )


def _clean_ai_style(current: dict, patch: dict) -> dict:
    merged = normalize_ai_style({**normalize_ai_style(current), **patch})
    if "model_class" in patch and merged["model_class"]:
        from app import ai

        if merged["model_class"] not in {c["id"] for c in ai.chat_classes()["items"]}:
            raise YandexError("model-class", "Эта модель сейчас недоступна")
    return merged


def fact_key(raw) -> str:
    return "_".join(str(raw or "").lower().split())[:60]


def normalize_ai_facts(raw) -> list[dict]:
    """Что ИИ запомнил со слов маркетолога: [{key, question, answer, at}], без пустых ответов."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key = fact_key(item.get("key"))
        answer = str(item.get("answer") or "").strip()[:1000]
        if key and answer:
            question = str(item.get("question") or "").strip()[:300]
            out.append({"key": key, "question": question, "answer": answer, "at": item.get("at") or None})
    return out


# --- Создание и раскладка -------------------------------------------------------------------


_CANDIDATES = """
    SELECT DISTINCT ON (m.counter_id)
        m.id, m.counter_id, m.client_row_id, m.name, m.site, y.yandex_login,
        EXISTS (SELECT 1 FROM workspaces o
                WHERE o.counter_id = m.counter_id AND o.user_id <> m.user_id AND o.archived_at IS NULL) AS taken
    FROM metrika_counters m
    JOIN yandex_connections y ON y.id = m.connection_id
    WHERE m.user_id = %s AND m.enabled = TRUE AND y.use_metrika = TRUE
    ORDER BY m.counter_id, (m.client_row_id IS NULL), m.id
"""

_INSERT_FOR_COUNTERS = """
    INSERT INTO workspaces (user_id, client_row_id, counter_id, counter_row_id, name, site)
    SELECT m.user_id, m.client_row_id, m.counter_id, m.id,
           COALESCE(NULLIF(m.name, ''), NULLIF(m.site, ''), 'Счётчик ' || m.counter_id),
           m.site
    FROM metrika_counters m
    WHERE m.user_id = %s AND m.id = ANY(%s)
    ON CONFLICT (user_id, counter_id) DO NOTHING
    RETURNING id, counter_id
"""


def projects_limit(user_id: int) -> int | None:
    from app.plans import subscription

    return subscription(user_id)["projects_limit"]


def active_projects(user_id: int) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM workspaces WHERE user_id = %s AND archived_at IS NULL", (user_id,)
        ).fetchone()
    return int(row["n"])


def ensure_workspaces(user_id: int) -> list[int]:
    """
    Проект (проект) на каждый новый видимый счётчик — если все они помещаются в тариф.

    Не помещаются — проекты не создаются: пользователь сам выбирает счётчики (counter_choices /
    create_project_for_counter). Счётчик, который уже стал проектом в другом аккаунте, пропускается.
    Счётчик может приходить через несколько логинов; читаем его через включённую строку
    подключения с ролью Метрики. Возвращает id созданных проектов — в них сразу
    раскладываются кампании, у которых в настройках этот счётчик.
    """
    with pool.connection() as conn:
        rows = conn.execute(_CANDIDATES, (user_id,)).fetchall()
        have = {
            int(r["counter_id"])
            for r in conn.execute("SELECT counter_id FROM workspaces WHERE user_id = %s", (user_id,))
        }
    fresh = [r for r in rows if int(r["counter_id"]) not in have and not r["taken"]]
    limit = projects_limit(user_id) if fresh else None
    if limit is not None and len(fresh) > limit - active_projects(user_id):
        fresh = []
    with pool.connection() as conn:
        created = conn.execute(_INSERT_FOR_COUNTERS, (user_id, [int(r["id"]) for r in fresh])).fetchall() if fresh else []
        conn.execute(
            """
            UPDATE workspaces w SET counter_row_id = best.id, updated_at = NOW()
            FROM (
                SELECT DISTINCT ON (m.counter_id) m.counter_id, m.id
                FROM metrika_counters m
                JOIN yandex_connections y ON y.id = m.connection_id
                WHERE m.user_id = %s
                ORDER BY m.counter_id, (m.enabled AND y.use_metrika) DESC, m.id
            ) best
            WHERE w.user_id = %s AND w.counter_id = best.counter_id
              AND w.counter_row_id IS DISTINCT FROM best.id
              AND NOT EXISTS (
                SELECT 1 FROM metrika_counters cur
                JOIN yandex_connections cy ON cy.id = cur.connection_id
                WHERE cur.id = w.counter_row_id AND cur.enabled AND cy.use_metrika
              )
            """,
            (user_id, user_id),
        )
    ids = [int(r["id"]) for r in created]
    if ids:
        from app import events

        for r in created:
            events.record(user_id, "project_created", workspace_id=int(r["id"]), counter_id=int(r["counter_id"]), auto=True)
        _attach_loose(user_id)
        _carry_site_checks(user_id)
    return ids


def _carry_site_checks(user_id: int) -> None:
    """Проверка сайта с лендинга — в чат появившегося проекта этого сайта. Сбой не мешает создать проект."""
    from app import site_check

    try:
        site_check.carry_to_projects(user_id)
    except Exception:  # noqa: BLE001
        log.exception("Перенос проверки сайта в проект пользователя %s", user_id)


def _attach_loose(user_id: int) -> None:
    """Кампании без проекта — по счётчикам из их настроек (после появления новых проектов)."""
    with pool.connection() as conn:
        loose = conn.execute(
            """
            SELECT c.id FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.user_id = %s AND c.counter_ids IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM workspace_campaigns wc WHERE wc.campaign_row_id = c.id)
            """,
            (user_id,),
        ).fetchall()
    auto_attach_campaigns(user_id, [int(r["id"]) for r in loose])


def auto_attach_campaigns(user_id: int, campaign_row_ids: list[int]) -> int:
    """
    Раскладывает кампании без проекта по счётчику из их настроек.

    Если счётчиков в кампании несколько, выигрывает проект, к которому уже подключён
    её кабинет; иначе первое по номеру счётчика из настроек. Кабинет подключается к
    проекту автоматически. Возвращает число разложенных кампаний.
    """
    if not campaign_row_ids:
        return 0
    attached = 0
    with pool.connection() as conn:
        workspaces = conn.execute(
            "SELECT id, counter_id FROM workspaces WHERE user_id = %s AND archived_at IS NULL",
            (user_id,),
        ).fetchall()
        by_counter = {int(w["counter_id"]): int(w["id"]) for w in workspaces}
        if not by_counter:
            return 0
        linked = {
            (int(r["workspace_id"]), int(r["account_id"]))
            for r in conn.execute("SELECT workspace_id, account_id FROM workspace_accounts WHERE user_id = %s", (user_id,))
        }
        rows = conn.execute(
            """
            SELECT c.id, c.account_id, c.counter_ids FROM direct_campaigns c
            WHERE c.user_id = %s AND c.id = ANY(%s)
              AND NOT EXISTS (SELECT 1 FROM workspace_campaigns wc WHERE wc.campaign_row_id = c.id)
            """,
            (user_id, list(campaign_row_ids)),
        ).fetchall()
        for row in rows:
            candidates = [by_counter[int(cid)] for cid in (row["counter_ids"] or []) if int(cid) in by_counter]
            if not candidates:
                continue
            account_id = int(row["account_id"])
            ws_id = next((w for w in candidates if (w, account_id) in linked), candidates[0])
            conn.execute(
                """
                INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (ws_id, row["id"], user_id),
            )
            if (ws_id, account_id) not in linked:
                conn.execute(
                    """
                    INSERT INTO workspace_accounts (workspace_id, account_id, user_id)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    (ws_id, account_id, user_id),
                )
                linked.add((ws_id, account_id))
            attached += 1
    return attached


# --- Чтение ---------------------------------------------------------------------------------

_WS_COLUMNS = """
    w.id, w.user_id, w.client_row_id, w.counter_id, w.counter_row_id, w.name, w.site, w.url_template,
    w.kpi_type, w.kpi_target, w.monthly_budget_kopecks, w.goal_id, w.ai_notes, w.ai_access, w.brief,
    w.ai_facts, w.ai_style, w.archived_at, w.created_at, w.updated_at,
    cl.name AS client_name, cl.kpi_type AS client_kpi_type, cl.kpi_target AS client_kpi_target,
    m.name AS counter_name, m.site AS counter_site, m.enabled AS counter_enabled,
    y.yandex_login AS counter_login,
    (SELECT COUNT(*) FROM workspace_campaigns wc WHERE wc.workspace_id = w.id) AS campaigns_count,
    (SELECT COUNT(*) FROM workspace_accounts wa WHERE wa.workspace_id = w.id) AS accounts_count,
    (SELECT g.name FROM metrika_goals g
       WHERE g.counter_row_id = w.counter_row_id AND g.goal_id = w.goal_id LIMIT 1) AS goal_name,
    -- Сколько действующих проектов создано раньше: проекты сверх лимита тарифа (новые) закрыты.
    (SELECT COUNT(*) FROM workspaces o
       WHERE o.user_id = w.user_id AND o.archived_at IS NULL
         AND (o.created_at, o.id) < (w.created_at, w.id)) AS active_rank
"""
_WS_FROM = """
    FROM workspaces w
    LEFT JOIN clients cl ON cl.id = w.client_row_id
    LEFT JOIN metrika_counters m ON m.id = w.counter_row_id
    LEFT JOIN yandex_connections y ON y.id = m.connection_id
"""


def _ws_out(row: dict) -> dict:
    budget = row.get("monthly_budget_kopecks")
    target = row.get("kpi_target")
    return {
        "id": row["id"],
        "name": row["name"],
        "site": row["site"] or row.get("counter_site") or "",
        "url_template": row.get("url_template") or "",
        "client_id": row["client_row_id"],
        "client_name": row.get("client_name") or "",
        "counter_id": row["counter_id"],
        "counter_row_id": row["counter_row_id"],
        "counter_name": row.get("counter_name") or "",
        "counter_login": row.get("counter_login") or "",
        # Счётчик пропал из Метрики или выключен — статистика сайта не обновляется.
        "counter_ok": bool(row["counter_row_id"]) and bool(row.get("counter_enabled")),
        "kpi_type": row["kpi_type"],
        "kpi_target": float(target) if target is not None else None,
        "monthly_budget_rub": budget / 100 if budget is not None else None,
        "goal_id": row["goal_id"],
        "goal_name": row.get("goal_name") or "",
        "ai_notes": row["ai_notes"],
        "brief": normalize_brief(row.get("brief")),
        "ai_access": normalize_ai_access(row.get("ai_access")),
        "ai_style": normalize_ai_style(row.get("ai_style")),
        "ai_facts": normalize_ai_facts(row.get("ai_facts")),
        "archived": row.get("archived_at") is not None,
        "locked": bool(row.get("_locked")),
        "campaigns_count": int(row.get("campaigns_count") or 0),
        "accounts_count": int(row.get("accounts_count") or 0),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def list_workspaces(user_id: int, *, include_archived: bool = False) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_WS_COLUMNS} {_WS_FROM}
            WHERE w.user_id = %s AND (%s OR w.archived_at IS NULL)
            ORDER BY w.archived_at IS NOT NULL, cl.name IS NULL, lower(cl.name), lower(w.name), w.id
            """,
            (user_id, include_archived),
        ).fetchall()
    limit = projects_limit(user_id) if rows else None
    return [_ws_out({**r, "_locked": _locked(r, limit)}) for r in rows]


def _locked(row: dict, limit: int | None) -> bool:
    """Проект сверх лимита тарифа: открыты первые по времени создания, остальные — под замком."""
    return limit is not None and row.get("archived_at") is None and int(row.get("active_rank") or 0) >= limit


def require_workspace(user_id: int, workspace_id: int, *, allow_locked: bool = False) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT {_WS_COLUMNS} {_WS_FROM} WHERE w.id = %s AND w.user_id = %s",
            (workspace_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    locked = row["archived_at"] is None and _locked(row, projects_limit(user_id))
    if locked and not allow_locked:
        from app.plans import PROJECT_LOCKED

        raise YandexError("project-locked", PROJECT_LOCKED)
    return {**row, "_locked": locked}


def get_workspace(user_id: int, workspace_id: int, *, allow_locked: bool = False) -> dict:
    out = _ws_out(require_workspace(user_id, workspace_id, allow_locked=allow_locked))
    out["accounts"] = list_workspace_accounts(user_id, workspace_id)
    out["goals"] = workspace_goals(user_id, workspace_id)
    out["ai_categories"] = [{"key": k, "label": v} for k, v in AI_CATEGORIES]
    return out


def workspace_goals(user_id: int, workspace_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT g.id, g.goal_id, g.name, g.goal_type
            FROM workspaces w
            JOIN metrika_goals g ON g.counter_row_id = w.counter_row_id
            WHERE w.id = %s AND w.user_id = %s
            ORDER BY g.id
            """,
            (workspace_id, user_id),
        ).fetchall()
    return [{"row_id": r["id"], "goal_id": r["goal_id"], "name": r["name"], "type": r["goal_type"]} for r in rows]


def workspace_campaign_row_ids(user_id: int, workspace_id: int) -> list[int]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT campaign_row_id FROM workspace_campaigns WHERE workspace_id = %s AND user_id = %s ORDER BY campaign_row_id",
            (workspace_id, user_id),
        ).fetchall()
    return [int(r["campaign_row_id"]) for r in rows]


def campaign_workspace_id(user_id: int, campaign_row_id: int) -> int | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT workspace_id FROM workspace_campaigns WHERE campaign_row_id = %s AND user_id = %s",
            (campaign_row_id, user_id),
        ).fetchone()
    return int(row["workspace_id"]) if row else None


def require_workspace_campaign(user_id: int, workspace_id: int, campaign_row_id: int) -> None:
    if campaign_workspace_id(user_id, campaign_row_id) != workspace_id:
        raise YandexError("not-found")


# --- Правка ---------------------------------------------------------------------------------


def _clean_kpi_target(raw) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise YandexError("kpi-target", "Целевое значение KPI — положительное число") from exc
    if not value.is_finite() or value <= 0:
        raise YandexError("kpi-target", "Целевое значение KPI — положительное число")
    return value.quantize(Decimal("0.0001"))


def _clean_budget(raw) -> int | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise YandexError("budget", "Бюджет — неотрицательное число") from exc
    if not value.is_finite() or value < 0:
        raise YandexError("budget", "Бюджет — неотрицательное число")
    return int((value * 100).to_integral_value())


def update_workspace(user_id: int, workspace_id: int, **fields) -> dict:
    """Частичное обновление; None у kpi_target / monthly_budget_rub / goal_id / client_id очищает поле.

    Закрытый тарифом проект можно только отправить в архив; вернуть из архива — если есть место в тарифе.
    """
    from app.plans import PROJECT_LOCKED, PROJECTS_LIMIT

    ws = require_workspace(user_id, workspace_id, allow_locked=True)
    if ws["_locked"] and set(fields) - {"archived"}:
        raise YandexError("project-locked", PROJECT_LOCKED)
    if fields.get("archived") is False and ws["archived_at"] is not None:
        limit = projects_limit(user_id)
        if limit is not None and active_projects(user_id) >= limit:
            raise YandexError("projects-limit", PROJECTS_LIMIT)
    sets: list[str] = []
    params: list = []
    if "name" in fields:
        name = (fields["name"] or "").strip()[:120]
        if not name:
            raise YandexError("name", "Укажите название проекта")
        sets.append("name = %s")
        params.append(name)
    if "site" in fields:
        sets.append("site = %s")
        params.append((fields["site"] or "").strip()[:300])
    if "url_template" in fields:
        from app.links import clean_template

        sets.append("url_template = %s")
        params.append(clean_template(fields["url_template"]))
    if "client_id" in fields:
        client_id = fields["client_id"]
        if client_id is not None:
            with pool.connection() as conn:
                owned = conn.execute(
                    "SELECT 1 FROM clients WHERE id = %s AND user_id = %s", (client_id, user_id)
                ).fetchone()
            if owned is None:
                raise YandexError("not-found")
        sets.append("client_row_id = %s")
        params.append(client_id)
    if "kpi_type" in fields:
        kpi_type = (fields["kpi_type"] or "").strip().lower()
        if kpi_type not in KPI_TYPES:
            raise YandexError("kpi-type", "KPI — CPA или ДРР")
        sets.append("kpi_type = %s")
        params.append(kpi_type)
    if "kpi_target" in fields:
        sets.append("kpi_target = %s")
        params.append(_clean_kpi_target(fields["kpi_target"]))
    if "monthly_budget_rub" in fields:
        sets.append("monthly_budget_kopecks = %s")
        params.append(_clean_budget(fields["monthly_budget_rub"]))
    if "goal_id" in fields:
        goal_id = fields["goal_id"]
        if goal_id is not None:
            known = {g["goal_id"] for g in workspace_goals(user_id, workspace_id)}
            if int(goal_id) not in known:
                raise YandexError("goal", "Цель не найдена у счётчика — обновите цели")
        sets.append("goal_id = %s")
        params.append(goal_id)
    if "ai_notes" in fields:
        sets.append("ai_notes = %s")
        params.append((fields["ai_notes"] or "").strip()[:8000])
    if "brief" in fields and fields["brief"] is not None:
        sets.append("brief = %s")
        params.append(Jsonb(normalize_brief(fields["brief"])))
    if "ai_access" in fields and fields["ai_access"] is not None:
        current = normalize_ai_access(ws.get("ai_access"))
        patch = dict(fields["ai_access"])
        if isinstance(patch.get("categories"), dict):
            # Частичный словарь категорий не сбрасывает остальные.
            patch["categories"] = {**current["categories"], **patch["categories"]}
        merged = normalize_ai_access({**current, **patch})
        sets.append("ai_access = %s")
        params.append(Jsonb(merged))
    if "ai_style" in fields and fields["ai_style"] is not None:
        sets.append("ai_style = %s")
        params.append(Jsonb(_clean_ai_style(ws.get("ai_style"), dict(fields["ai_style"]))))
    if fields.get("archived") is not None:
        sets.append("archived_at = CASE WHEN %s THEN COALESCE(archived_at, NOW()) END")
        params.append(bool(fields["archived"]))
    if sets:
        with pool.connection() as conn:
            conn.execute(
                f"UPDATE workspaces SET {', '.join(sets)}, updated_at = NOW() WHERE id = %s AND user_id = %s",
                (*params, workspace_id, user_id),
            )
    return get_workspace(user_id, workspace_id, allow_locked=True)


def counter_choices(user_id: int) -> dict:
    """Счётчики пользователя для выбора проектов: что уже проект, что можно добавить, что — на тарифе выше.

    state: project | archived | available | locked (нет места в тарифе) | taken (проект в другом аккаунте).
    """
    from app.yandex import TAKEN

    with pool.connection() as conn:
        rows = conn.execute(_CANDIDATES, (user_id,)).fetchall()
        own = {
            int(r["counter_id"]): r
            for r in conn.execute(
                "SELECT id, counter_id, name, archived_at FROM workspaces WHERE user_id = %s", (user_id,)
            )
        }
    limit = projects_limit(user_id)
    used = active_projects(user_id)
    free = None if limit is None else max(0, limit - used)
    items = []
    for r in rows:
        counter_id = int(r["counter_id"])
        ws = own.get(counter_id)
        if ws is not None and ws["archived_at"] is None:
            state = "project"
        elif r["taken"]:
            state = "taken"
        elif free is not None and free <= 0:
            state = "locked"
        else:
            state = "archived" if ws is not None else "available"
        items.append(
            {
                "counter_id": counter_id,
                "name": r["name"] or "",
                "site": r["site"] or "",
                "login": r["yandex_login"] or "",
                "state": state,
                "workspace_id": int(ws["id"]) if ws is not None else None,
                "note": f"Счётчик {TAKEN}" if state == "taken" else "",
            }
        )
    return {"items": items, "projects_limit": limit, "projects_used": used}


def create_project_for_counter(user_id: int, counter_id: int) -> dict:
    """Проект для выбранного счётчика: в пределах тарифа; архивный проект этого счётчика возвращается."""
    from app import events
    from app.plans import PROJECTS_LIMIT
    from app.yandex import TAKEN

    with pool.connection() as conn:
        rows = conn.execute(_CANDIDATES, (user_id,)).fetchall()
        existing = conn.execute(
            "SELECT id, archived_at FROM workspaces WHERE user_id = %s AND counter_id = %s", (user_id, counter_id)
        ).fetchone()
    row = next((r for r in rows if int(r["counter_id"]) == int(counter_id)), None)
    if existing is not None and existing["archived_at"] is None:
        return get_workspace(user_id, int(existing["id"]), allow_locked=True)
    if row is None:
        raise YandexError("not-found", "Счётчик не найден — обновите подключения")
    if row["taken"]:
        raise YandexError("counter-taken", f"Счётчик {counter_id} {TAKEN}")
    limit = projects_limit(user_id)
    if limit is not None and active_projects(user_id) >= limit:
        raise YandexError("projects-limit", PROJECTS_LIMIT)
    if existing is not None:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE workspaces SET archived_at = NULL, updated_at = NOW() WHERE id = %s AND user_id = %s",
                (existing["id"], user_id),
            )
        _carry_site_checks(user_id)
        return get_workspace(user_id, int(existing["id"]))
    with pool.connection() as conn:
        created = conn.execute(_INSERT_FOR_COUNTERS, (user_id, [int(row["id"])])).fetchone()
    if created is None:
        raise YandexError("exists", "Проект для этого счётчика уже есть")
    events.record(user_id, "project_created", workspace_id=int(created["id"]), counter_id=int(counter_id), auto=False)
    _attach_loose(user_id)
    _carry_site_checks(user_id)
    return get_workspace(user_id, int(created["id"]))


def _edit_facts(user_id: int, workspace_id: int, edit) -> list[dict]:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT ai_facts FROM workspaces WHERE id = %s AND user_id = %s FOR UPDATE", (workspace_id, user_id)
        ).fetchone()
        if row is None:
            raise YandexError("not-found")
        facts = edit(normalize_ai_facts(row["ai_facts"]))[-MAX_AI_FACTS:]
        conn.execute(
            "UPDATE workspaces SET ai_facts = %s, updated_at = NOW() WHERE id = %s", (Jsonb(facts), workspace_id)
        )
    return facts


def remember_ai_facts(user_id: int, workspace_id: int, items: list[dict]) -> list[dict]:
    """Запомнить ответы маркетолога разом: [{key, question, answer}]; тот же key заменяет прежний ответ."""
    at = datetime.now(timezone.utc).isoformat()
    new = {f["key"]: {**f, "at": at} for f in normalize_ai_facts(items)}
    if not new:
        raise YandexError("args", "Нужны key и answer")
    return _edit_facts(user_id, workspace_id, lambda facts: [*(f for f in facts if f["key"] not in new), *new.values()])


def remember_ai_fact(user_id: int, workspace_id: int, *, key: str, question: str, answer: str) -> list[dict]:
    """Запомнить ответ маркетолога; тот же key заменяет прежний ответ."""
    return remember_ai_facts(user_id, workspace_id, [{"key": key, "question": question, "answer": answer}])


def forget_ai_fact(user_id: int, workspace_id: int, key: str) -> dict:
    key = fact_key(key)
    _edit_facts(user_id, workspace_id, lambda facts: [f for f in facts if f["key"] != key])
    return get_workspace(user_id, workspace_id)


# --- Кабинеты Директа -----------------------------------------------------------------------


def _counter_logins(user_id: int, counter_id: int) -> set[str]:
    """Логины подключений, которые видят счётчик (значит, у них есть к нему доступ)."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT lower(y.yandex_login) AS login
            FROM metrika_counters m JOIN yandex_connections y ON y.id = m.connection_id
            WHERE m.user_id = %s AND m.counter_id = %s
            """,
            (user_id, counter_id),
        ).fetchall()
    return {r["login"] for r in rows if r["login"]}


def counter_access(user_id: int, workspace_id: int, account: dict) -> dict:
    """
    Подсказка про доступ кабинета к счётчику проекта.

    Директ принимает номер любого счётчика, но цели для стратегий и отчётов доступны, только
    если у логина кабинета есть доступ к счётчику в Метрике. Мы знаем это наверняка, когда
    логин кабинета сам подключён и видит счётчик; иначе — предупреждаем.
    """
    ws = require_workspace(user_id, workspace_id)
    login = (account.get("client_login") or "").lower()
    if login and login in _counter_logins(user_id, int(ws["counter_id"])):
        return {"status": "ok", "hint": ""}
    return {
        "status": "unknown",
        "hint": (
            f"Проверьте, что у логина {account.get('client_login') or '—'} есть доступ к счётчику "
            f"{ws['counter_id']} (Метрика → Настройки → Доступ). Без доступа Директ не увидит цели "
            "для стратегий и ключевых целей."
        ),
    }


def list_workspace_accounts(user_id: int, workspace_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.client_login, COALESCE(NULLIF(a.name, ''), a.client_login) AS name,
                   a.enabled, a.currency, a.balance_kopecks, a.balance_currency,
                   a.last_sync_status, a.last_sync_error, a.last_sync_at,
                   y.yandex_login AS connection_login,
                   (SELECT COUNT(*) FROM workspace_campaigns wc
                      JOIN direct_campaigns c ON c.id = wc.campaign_row_id
                      WHERE wc.workspace_id = wa.workspace_id AND c.account_id = a.id) AS campaigns_in_workspace,
                   (SELECT COUNT(*) FROM direct_campaigns c WHERE c.account_id = a.id) AS campaigns_total
            FROM workspace_accounts wa
            JOIN direct_accounts a ON a.id = wa.account_id
            JOIN yandex_connections y ON y.id = a.connection_id
            WHERE wa.workspace_id = %s AND wa.user_id = %s
            ORDER BY lower(a.name), a.id
            """,
            (workspace_id, user_id),
        ).fetchall()
    out = []
    for r in rows:
        item = {
            "id": r["id"],
            "client_login": r["client_login"],
            "name": r["name"],
            "enabled": r["enabled"],
            "connection_login": r["connection_login"],
            "currency": r["currency"] or r["balance_currency"] or "",
            "balance_kopecks": r["balance_kopecks"],
            "last_sync_status": r["last_sync_status"],
            "last_sync_error": r["last_sync_error"],
            "last_sync_at": _iso(r["last_sync_at"]),
            "campaigns_in_workspace": int(r["campaigns_in_workspace"] or 0),
            "campaigns_total": int(r["campaigns_total"] or 0),
        }
        item["counter_access"] = counter_access(user_id, workspace_id, item)
        out.append(item)
    return out


def _require_account(user_id: int, account_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT a.id, a.client_login, a.enabled
            FROM direct_accounts a
            WHERE a.id = %s AND a.user_id = %s
            """,
            (account_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return row


def add_workspace_account(user_id: int, workspace_id: int, account_id: int, *, campaigns: str = "matching") -> dict:
    """
    Подключает кабинет к проекту. campaigns: matching — забрать кампании со счётчиком
    проекта, all — все кампании кабинета, которые ещё ни в каком проекте, none — ничего.
    """
    ws = require_workspace(user_id, workspace_id)
    _require_account(user_id, account_id)
    if campaigns not in ("matching", "all", "none"):
        raise YandexError("payload")
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO workspace_accounts (workspace_id, account_id, user_id)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (workspace_id, account_id, user_id),
        )
        added = 0
        if campaigns != "none":
            rows = conn.execute(
                """
                SELECT c.id, c.counter_ids FROM direct_campaigns c
                WHERE c.account_id = %s AND c.user_id = %s
                  AND NOT EXISTS (SELECT 1 FROM workspace_campaigns wc WHERE wc.campaign_row_id = c.id)
                """,
                (account_id, user_id),
            ).fetchall()
            for r in rows:
                if campaigns == "matching" and int(ws["counter_id"]) not in [int(x) for x in (r["counter_ids"] or [])]:
                    continue
                conn.execute(
                    "INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id) VALUES (%s, %s, %s)",
                    (workspace_id, r["id"], user_id),
                )
                added += 1
    return {"account_id": account_id, "campaigns_added": added}


def remove_workspace_account(user_id: int, workspace_id: int, account_id: int) -> dict:
    """Отключает кабинет; его кампании уходят из проекта (в Директе ничего не меняется)."""
    require_workspace(user_id, workspace_id)
    with pool.connection() as conn:
        removed = conn.execute(
            """
            DELETE FROM workspace_campaigns wc USING direct_campaigns c
            WHERE wc.campaign_row_id = c.id AND wc.workspace_id = %s AND c.account_id = %s
            """,
            (workspace_id, account_id),
        ).rowcount
        row = conn.execute(
            "DELETE FROM workspace_accounts WHERE workspace_id = %s AND account_id = %s AND user_id = %s RETURNING 1",
            (workspace_id, account_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return {"account_id": account_id, "campaigns_removed": removed}


def account_candidates(user_id: int, workspace_id: int) -> list[dict]:
    """Все кабинеты пользователя для выбора в настройках проекта."""
    ws = require_workspace(user_id, workspace_id)
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.client_login, COALESCE(NULLIF(a.name, ''), a.client_login) AS name,
                   a.enabled, y.yandex_login AS connection_login,
                   EXISTS (SELECT 1 FROM workspace_accounts wa
                           WHERE wa.workspace_id = %s AND wa.account_id = a.id) AS linked,
                   (SELECT COUNT(*) FROM direct_campaigns c WHERE c.account_id = a.id) AS campaigns_total,
                   (SELECT COUNT(*) FROM direct_campaigns c
                     WHERE c.account_id = a.id AND c.counter_ids @> to_jsonb(%s::bigint)) AS campaigns_matching
            FROM direct_accounts a
            JOIN yandex_connections y ON y.id = a.connection_id
            WHERE a.user_id = %s AND y.use_direct = TRUE
            ORDER BY linked DESC, campaigns_matching DESC, lower(a.name), a.id
            """,
            (workspace_id, int(ws["counter_id"]), user_id),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "client_login": r["client_login"],
            "name": r["name"],
            "enabled": r["enabled"],
            "connection_login": r["connection_login"],
            "linked": r["linked"],
            "campaigns_total": int(r["campaigns_total"] or 0),
            "campaigns_matching": int(r["campaigns_matching"] or 0),
        }
        for r in rows
    ]


# --- Кампании проекта ------------------------------------------------------------------


def campaign_candidates(user_id: int, workspace_id: int) -> list[dict]:
    """Кампании всех кабинетов пользователя, сгруппированные по кабинетам, с отметкой проекта."""
    ws = require_workspace(user_id, workspace_id)
    counter_id = int(ws["counter_id"])
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.campaign_id, c.name, c.campaign_type, c.state, c.status, c.counter_ids,
                   a.id AS account_id, COALESCE(NULLIF(a.name, ''), a.client_login) AS account_name,
                   a.client_login,
                   wc.workspace_id, w.name AS workspace_name,
                   EXISTS (SELECT 1 FROM workspace_accounts wa
                           WHERE wa.workspace_id = %s AND wa.account_id = a.id) AS account_linked
            FROM direct_campaigns c
            JOIN direct_accounts a ON a.id = c.account_id
            JOIN yandex_connections y ON y.id = a.connection_id
            LEFT JOIN workspace_campaigns wc ON wc.campaign_row_id = c.id
            LEFT JOIN workspaces w ON w.id = wc.workspace_id
            WHERE c.user_id = %s AND y.use_direct = TRUE
            ORDER BY account_linked DESC, lower(a.name), a.id, c.state = 'ARCHIVED', lower(c.name), c.id
            """,
            (workspace_id, user_id),
        ).fetchall()
    groups: dict[int, dict] = {}
    for r in rows:
        group = groups.get(r["account_id"])
        if group is None:
            group = groups[r["account_id"]] = {
                "account_id": r["account_id"],
                "account_name": r["account_name"],
                "client_login": r["client_login"],
                "linked": r["account_linked"],
                "campaigns": [],
            }
        counters = [int(x) for x in (r["counter_ids"] or [])]
        group["campaigns"].append(
            {
                "id": r["id"],
                "campaign_id": r["campaign_id"],
                "name": r["name"],
                "type": r["campaign_type"],
                "state": r["state"],
                "status": r["status"],
                "counter_ids": counters,
                "matches_counter": counter_id in counters,
                "workspace_id": r["workspace_id"],
                "workspace_name": r["workspace_name"] or "",
                "in_this": r["workspace_id"] == workspace_id,
            }
        )
    return list(groups.values())


def set_workspace_campaigns(
    user_id: int, workspace_id: int, *, add: list[int] | None = None, remove: list[int] | None = None
) -> dict:
    """Добавить (с переносом из другого проекта) и убрать кампании; кабинет подключается сам."""
    require_workspace(user_id, workspace_id)
    add = [int(x) for x in (add or [])]
    remove = [int(x) for x in (remove or [])]
    with pool.connection() as conn:
        moved = 0
        if add:
            rows = conn.execute(
                "SELECT id, account_id FROM direct_campaigns WHERE user_id = %s AND id = ANY(%s)",
                (user_id, add),
            ).fetchall()
            if len(rows) != len(set(add)):
                raise YandexError("not-found")
            for r in rows:
                prev = conn.execute(
                    "DELETE FROM workspace_campaigns WHERE campaign_row_id = %s AND workspace_id <> %s RETURNING 1",
                    (r["id"], workspace_id),
                ).fetchone()
                moved += 1 if prev else 0
                conn.execute(
                    """
                    INSERT INTO workspace_campaigns (workspace_id, campaign_row_id, user_id)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    (workspace_id, r["id"], user_id),
                )
                conn.execute(
                    """
                    INSERT INTO workspace_accounts (workspace_id, account_id, user_id)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    (workspace_id, r["account_id"], user_id),
                )
        removed = 0
        if remove:
            removed = conn.execute(
                "DELETE FROM workspace_campaigns WHERE workspace_id = %s AND user_id = %s AND campaign_row_id = ANY(%s)",
                (workspace_id, user_id, remove),
            ).rowcount
    return {"added": len(add), "moved": moved, "removed": removed}


def attach_campaign(user_id: int, workspace_id: int, campaign_row_id: int) -> None:
    """Новая кампания, созданная из проекта, сразу в нём."""
    set_workspace_campaigns(user_id, workspace_id, add=[campaign_row_id])
