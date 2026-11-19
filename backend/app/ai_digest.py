"""Выжимка прошлых решений проекта для нового чата ИИ.

На первом ходе нового чата проекта ИИ получает короткий список за 30 дней, новые сверху: что маркетолог одобрил
и что сделано, что отклонил и почему, что ещё ждёт решения в других чатах, что поменял руками и что откатил.
Выжимка снимается один раз и хранится в ai_chats.digest: системный промпт чата от хода к ходу не меняется
(кэш начала запроса), а прошлые решения не теряются между чатами. Общий чат выжимки не получает.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.clock import MSK
from app.db import pool

log = logging.getLogger(__name__)

DIGEST_DAYS = 30
DIGEST_LINES = 15
_TEXT_MAX = 160
_REASON_MAX = 160


def _short(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _at(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _action_rows(user_id: int, workspace_id: int, tools: list[str], exclude_chat: int | None, days: int) -> list[dict]:
    """Предложения ИИ по проекту: из его чатов и из общего чата (там проект — в args.project_id).

    Отклонённые — только решением маркетолога: пропущенные сервисом (новое сообщение закрыло карточку) пишут
    причину в error и в выжимку не идут. Ждущие — только в других чатах: свои чат и так видит.
    """
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT a.id, a.summary, a.status, a.auto, a.decline_reason, a.snoozed_at,
                   COALESCE(a.decided_at, a.created_at) AS at
            FROM ai_actions a
            JOIN ai_chats c ON c.id = a.chat_id
            WHERE a.user_id = %(user)s
              AND (c.workspace_id = %(ws)s OR (c.workspace_id IS NULL AND a.args->>'project_id' = %(ws_text)s))
              AND a.tool = ANY(%(tools)s)
              AND (
                    a.status = 'done'
                 OR (a.status = 'rejected' AND a.error = '')
                 OR (a.status = 'pending' AND a.chat_id IS DISTINCT FROM %(chat)s::bigint)
              )
              AND COALESCE(a.decided_at, a.created_at) >= NOW() - make_interval(days => %(days)s)
            ORDER BY at DESC, a.id DESC
            LIMIT 60
            """,
            {
                "user": user_id,
                "ws": workspace_id,
                "ws_text": str(workspace_id),
                "tools": list(tools),
                "chat": exclude_chat,
                "days": days,
            },
        ).fetchall()


def _log_rows(user_id: int, workspace_id: int, days: int) -> list[dict]:
    """Ручные правки маркетолога и откаты (свои и ИИ) из журнала проекта."""
    from app.journal import list_action_logs

    out = []
    for item in list_action_logs(user_id, 60, workspace_id=workspace_id):
        if item.get("undo_of"):
            continue  # сама запись отката: её выражает «откатили» у исходной
        undone = _at(item.get("undone_at"))
        manual = item.get("source") == "user" and item.get("status") in ("ok", "partial")
        if not undone and not manual:
            continue
        at = undone or _at(item.get("created_at"))
        if at is None or (datetime.now(MSK) - at).days >= days:
            continue
        out.append({**item, "_at": at, "_undone": bool(undone)})
    return out


def _action_line(row: dict) -> tuple[datetime, str]:
    summary = _short(row["summary"], _TEXT_MAX) or "действие без описания"
    status = row["status"]
    if status == "done":
        text = ("сделано сразу (по доступу ИИ)" if row.get("auto") else "одобрено и сделано") + f": {summary}"
    elif status == "rejected":
        reason = _short(row.get("decline_reason"), _REASON_MAX)
        text = f"отклонено: {summary} — " + (f"причина: «{reason}»" if reason else "причину не назвал")
    else:
        text = ("отложено, ждёт решения" if row.get("snoozed_at") else "ждёт решения в другом чате") + f": {summary}"
    return row["at"], text


def _log_line(item: dict) -> tuple[datetime, str]:
    title = _short(item.get("title"), 80)
    detail = next((c for c in item.get("changes") or [] if c), "")
    names = ", ".join(f"«{c['name']}»" for c in item.get("campaigns") or [] if c.get("name"))
    text = title + (f" — {_short(detail, 90)}" if detail else "") + (f" ({_short(names, 80)})" if names else "")
    if item["_undone"]:
        who = "ИИ" if item.get("source") == "ai" else "маркетолога" if item.get("source") == "user" else "правила"
        return item["_at"], f"откатили изменение {who}: {text}"
    return item["_at"], f"маркетолог сам изменил: {text}"


def decisions_digest(
    user_id: int,
    workspace_id: int,
    *,
    tools: list[str],
    exclude_chat: int | None = None,
    days: int = DIGEST_DAYS,
    limit: int = DIGEST_LINES,
) -> list[str]:
    """Строки выжимки «дд.мм что было», новые сверху, не больше limit. tools — инструменты действий ИИ."""
    items = [_action_line(r) for r in _action_rows(user_id, workspace_id, tools, exclude_chat, days)]
    try:
        items += [_log_line(i) for i in _log_rows(user_id, workspace_id, days)]
    except Exception:  # журнал — дополнение: без него выжимка всё равно нужна
        log.warning("Журнал проекта %s не попал в выжимку решений", workspace_id, exc_info=True)
    items.sort(key=lambda pair: pair[0], reverse=True)
    return [f"- {at.astimezone(MSK):%d.%m} {text}" for at, text in items[:limit]]
