"""Оценки ответов ИИ: «палец вверх / вниз» под ответом, причины и комментарий у «вниз».

Одна оценка на сообщение — повторная заменяет прежнюю, rating 0 снимает. Админка видит список с фильтром
по модели и причине, читает чат целиком и долю «вниз» по классам моделей за месяц (плитка в «Расходе»).
"""

from __future__ import annotations

import re

from app.db import pool
from app.yandex import YandexError

REASONS = {
    "wrong_data": "Неверные данные",
    "not_asked": "Не то, что просил",
    "unclear": "Непонятно",
    "too_long": "Слишком длинно",
    "other": "Другое",
}
COMMENT_LEN = 1000
EXCERPT_LEN = 400


def feedback_out(row: dict | None) -> dict | None:
    if not row or not row.get("rating"):
        return None
    return {"rating": int(row["rating"]), "reasons": list(row.get("reasons") or []), "comment": row.get("comment") or ""}


def save(user_id: int, message_id: int, rating: int, reasons: list[str] | None = None, comment: str = "") -> dict | None:
    """Оценка ответа ИИ своего чата; rating 0 — снять. Причины и комментарий — только у «вниз»."""
    if rating not in (-1, 0, 1):
        raise YandexError("rating", "Оценка — 1, -1 или 0")
    with pool.connection() as conn:
        msg = conn.execute(
            """
            SELECT m.id, m.chat_id, m.model FROM ai_messages m JOIN ai_chats ch ON ch.id = m.chat_id
            WHERE m.id = %s AND ch.user_id = %s AND m.role = 'assistant' AND m.content <> ''
            """,
            (message_id, user_id),
        ).fetchone()
        if msg is None:
            raise YandexError("not-found")
        if rating == 0:
            conn.execute("DELETE FROM ai_feedback WHERE message_id = %s", (message_id,))
            return None
        picked = list(dict.fromkeys(r for r in (reasons or []) if r in REASONS)) if rating < 0 else []
        text = (comment or "").strip()[:COMMENT_LEN] if rating < 0 else ""
        row = conn.execute(
            """
            INSERT INTO ai_feedback (user_id, chat_id, message_id, rating, reasons, comment, model)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (message_id) DO UPDATE
            SET rating = EXCLUDED.rating, reasons = EXCLUDED.reasons, comment = EXCLUDED.comment, updated_at = NOW()
            RETURNING *
            """,
            (user_id, msg["chat_id"], message_id, rating, picked, text, msg["model"] or ""),
        ).fetchone()
    return feedback_out(row)


def _excerpt(text: str | None) -> str:
    """Отрывок в одну строку: без разметки Markdown и переносов — в списке оценок он только для узнавания."""
    text = re.sub(r"\*\*|__|`|^#+\s*|^\s*[-*]\s+", "", text or "", flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= EXCERPT_LEN else text[:EXCERPT_LEN].rstrip() + "…"


def admin_list(rating: int = 0, model: str = "", reason: str = "", limit: int = 100, offset: int = 0) -> dict:
    """Оценки для админки, новые сверху: ответ и вопрос перед ним — коротко, чат — по ссылке."""
    where, params = ["TRUE"], []
    if rating in (-1, 1):
        where.append("f.rating = %s")
        params.append(rating)
    if model:
        where.append("f.model = %s")
        params.append(model)
    if reason:
        where.append("%s = ANY(f.reasons)")
        params.append(reason)
    cond = " AND ".join(where)
    limit = max(1, min(int(limit), 300))
    with pool.connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM ai_feedback f WHERE {cond}", params).fetchone()["n"]
        rows = conn.execute(
            f"""
            SELECT f.*, u.email, u.name AS user_name, ch.title AS chat_title, m.content AS answer,
                   (SELECT q.content FROM ai_messages q
                    WHERE q.chat_id = f.chat_id AND q.role = 'user' AND q.id < f.message_id
                    ORDER BY q.id DESC LIMIT 1) AS question
            FROM ai_feedback f
            JOIN users u ON u.id = f.user_id
            JOIN ai_chats ch ON ch.id = f.chat_id
            JOIN ai_messages m ON m.id = f.message_id
            WHERE {cond}
            ORDER BY f.updated_at DESC, f.id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, max(0, int(offset))],
        ).fetchall()
        models = conn.execute("SELECT DISTINCT model FROM ai_feedback WHERE model <> '' ORDER BY model").fetchall()
    return {
        "total": int(total),
        "items": [
            {
                "id": r["id"],
                "rating": int(r["rating"]),
                "reasons": [{"id": x, "label": REASONS.get(x, x)} for x in r["reasons"] or []],
                "comment": r["comment"],
                "model": r["model"],
                "user": {"id": r["user_id"], "email": r["email"], "name": r["user_name"] or ""},
                "chat_id": r["chat_id"],
                "chat_title": r["chat_title"] or "",
                "message_id": r["message_id"],
                "question": _excerpt(r["question"]),
                "answer": _excerpt(r["answer"]),
                "created_at": r["updated_at"].isoformat(),
            }
            for r in rows
        ],
        "models": [r["model"] for r in models],
        "reasons": [{"id": k, "label": v} for k, v in REASONS.items()],
    }


def admin_chat(chat_id: int) -> dict:
    """Чат для чтения в админке: вопросы и ответы без шагов инструментов, с оценками."""
    with pool.connection() as conn:
        chat = conn.execute(
            """
            SELECT ch.id, ch.title, ch.workspace_id, ch.created_at, u.email
            FROM ai_chats ch JOIN users u ON u.id = ch.user_id WHERE ch.id = %s
            """,
            (chat_id,),
        ).fetchone()
        if chat is None:
            raise YandexError("not-found")
        rows = conn.execute(
            """
            SELECT m.id, m.role, m.content, m.model, m.created_at, f.rating, f.reasons, f.comment
            FROM ai_messages m LEFT JOIN ai_feedback f ON f.message_id = m.id
            WHERE m.chat_id = %s AND m.role IN ('user', 'assistant') AND m.content <> ''
            ORDER BY m.id
            """,
            (chat_id,),
        ).fetchall()
    return {
        "chat": {
            "id": chat["id"],
            "title": chat["title"] or "",
            "email": chat["email"],
            "general": chat["workspace_id"] is None,
            "created_at": chat["created_at"].isoformat(),
        },
        "messages": [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "model": r["model"] or "",
                "created_at": r["created_at"].isoformat(),
                "feedback": feedback_out(r),
            }
            for r in rows
        ],
    }


def stats(start, end) -> list[dict]:
    """Доля «вниз» по классам моделей за период: [{model, total, down, down_share}], больше оценок — выше."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT model, COUNT(*) AS n, COUNT(*) FILTER (WHERE rating < 0) AS down
            FROM ai_feedback WHERE updated_at >= %s AND updated_at < %s
            GROUP BY model ORDER BY COUNT(*) DESC, model
            """,
            (start, end),
        ).fetchall()
    return [
        {
            "model": r["model"],
            "total": int(r["n"]),
            "down": int(r["down"]),
            "down_share": round(int(r["down"]) / int(r["n"]), 3) if r["n"] else 0.0,
        }
        for r in rows
    ]
