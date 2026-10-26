"""Журнал действий: кто и что поменял в Директе, с шагами отката."""

from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from app.db import pool

ACTION_TITLES = {
    "pause": "Остановка кампаний",
    "resume": "Запуск кампаний",
    "set_budget": "Изменение дневного бюджета",
    "archive": "Архивация кампаний",
    "unarchive": "Возврат из архива",
    "delete": "Удаление кампаний",
    "create": "Создание кампании",
    "update": "Изменение кампании",
    "settings": "Изменение настроек кампании",
    "duplicate": "Копия кампании",
    "moderate": "Отправка на модерацию",
    "negative_list_apply": "Применение списка минус-фраз",
    "keywords": "Изменение ключевых фраз",
    "ads": "Изменение объявлений",
    "adgroups": "Изменение групп",
    "queries": "Поисковые запросы",
    "negatives": "Минус-фразы",
    "undo": "Откат",
}

# --- Кто действует -----------------------------------------------------------------------------
# Журнал показывает источник действия: пользователь, ИИ-агент или правило автоматизации.
# Агент и правила оборачивают свои вызовы в acting_as(...), сами функции действий об этом не знают.

_source: contextvars.ContextVar[str] = contextvars.ContextVar("action_source", default="user")
# Составное действие (откат, первая группа новой кампании) пишет одну запись сам,
# вложенные вызовы в это время журнал не трогают.
_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("journal_quiet", default=False)


@contextmanager
def acting_as(source: str):
    token = _source.set(source if source in ("user", "ai", "rule") else "user")
    try:
        yield
    finally:
        _source.reset(token)


@contextmanager
def journal_quiet():
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


def current_source() -> str:
    return _source.get()


# --- Запись ------------------------------------------------------------------------------------


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:4000]


def _load(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _ids(payload: dict) -> set[int]:
    ids: set[int] = set()
    for key in ("campaign_row_ids", "campaign_ids"):
        for value in payload.get(key) or []:
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
    if payload.get("campaign_row_id"):
        ids.add(int(payload["campaign_row_id"]))
    return ids


def _workspace_for(user_id: int, payload: dict) -> int | None:
    """Пространство действия — если все затронутые кампании лежат в одном."""
    ids = _ids(payload)
    if not ids:
        return None
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT workspace_id FROM workspace_campaigns
            WHERE user_id = %s AND campaign_row_id = ANY(%s)
            """,
            (user_id, list(ids)),
        ).fetchall()
    return int(rows[0]["workspace_id"]) if len(rows) == 1 else None


_CAMPAIGNS_OF = {
    "adgroup": "SELECT DISTINCT campaign_row_id AS id FROM direct_adgroups WHERE user_id = %s AND id = ANY(%s)",
    "keyword": """
        SELECT DISTINCT g.campaign_row_id AS id FROM direct_keywords k
        JOIN direct_adgroups g ON g.id = k.adgroup_row_id
        WHERE k.user_id = %s AND k.id = ANY(%s)
    """,
    "ad": """
        SELECT DISTINCT g.campaign_row_id AS id FROM direct_ads ad
        JOIN direct_adgroups g ON g.id = ad.adgroup_row_id
        WHERE ad.user_id = %s AND ad.id = ANY(%s)
    """,
}


def campaigns_of(user_id: int, kind: str, ids: list[int]) -> list[int]:
    """Кампании, в которых лежат группы / ключи / объявления — для пространства записи."""
    if not ids:
        return []
    with pool.connection() as conn:
        rows = conn.execute(_CAMPAIGNS_OF[kind], (user_id, list(ids))).fetchall()
    return sorted(int(r["id"]) for r in rows if r["id"] is not None)


def log_action(
    user_id: int,
    action: str,
    payload: dict,
    result: dict,
    status: str,
    *,
    workspace_id: int | None = None,
    title: str | None = None,
    undo: dict | None = None,
    targets: list[str] | None = None,
    undo_of: int | None = None,
) -> int | None:
    """
    undo — {"label": "Вернуть бюджеты", "danger": bool, "steps": [{"op": ..., "args": {...}}]};
    targets — затронутые объекты («campaign:12», «keyword:5»); по умолчанию кампании из payload.
    """
    if _quiet.get():
        return None
    payload = payload or {}
    if workspace_id is None:
        workspace_id = _workspace_for(user_id, payload)
    if targets is None:
        targets = [f"campaign:{i}" for i in sorted(_ids(payload))]
    if undo is not None and not undo.get("steps"):
        undo = None
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO action_logs (
                user_id, action, payload, result, status, workspace_id, source, title, undo, targets, undo_of
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id,
                action,
                _dump(payload),
                _dump(result),
                status,
                workspace_id,
                _source.get(),
                (title or ACTION_TITLES.get(action) or action)[:300],
                Jsonb(undo) if undo is not None else None,
                sorted(set(targets)),
                undo_of,
            ),
        ).fetchone()
    return int(row["id"])


def list_action_logs(
    user_id: int,
    limit: int = 50,
    *,
    workspace_id: int | None = None,
    source: str | None = None,
    before_id: int | None = None,
) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    where = ["l.user_id = %s"]
    params: list = [user_id]
    if workspace_id is not None:
        where.append("l.workspace_id = %s")
        params.append(workspace_id)
    if source in ("user", "ai", "rule"):
        where.append("l.source = %s")
        params.append(source)
    if before_id:
        where.append("l.id < %s")
        params.append(before_id)
    params.append(limit)
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT l.id, l.action, l.title, l.source, l.workspace_id, w.name AS workspace_name,
                   l.payload, l.result, l.status, l.created_at, l.undo, l.undone_at, l.undone_by, l.undo_of
            FROM action_logs l
            LEFT JOIN workspaces w ON w.id = l.workspace_id
            WHERE {' AND '.join(where)}
            ORDER BY l.id DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
    return [_log_out(r) for r in rows]


def _log_out(r: dict) -> dict:
    undo = r.get("undo") or None
    return {
        "id": r["id"],
        "action": r["action"],
        "title": r["title"] or ACTION_TITLES.get(r["action"]) or r["action"],
        "source": r["source"],
        "workspace_id": r["workspace_id"],
        "workspace_name": r.get("workspace_name"),
        "payload": _load(r["payload"]),
        "result": _load(r["result"]),
        "status": r["status"],
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        # Откатить можно один раз и только то, что хотя бы частично выполнилось.
        "undo": (
            {"label": undo.get("label") or "Откатить", "danger": bool(undo.get("danger"))}
            if undo and r["status"] in ("ok", "partial") and not r.get("undone_at")
            else None
        ),
        "undone_at": r["undone_at"].isoformat() if r.get("undone_at") else None,
        "undone_by": r.get("undone_by"),
        "undo_of": r.get("undo_of"),
    }
