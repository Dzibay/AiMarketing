"""Журнал событий пользователя: из него админка считает воронку и активность.

Запись событий не должна ломать действие пользователя: ошибка пишется в лог и глотается.
"""

from __future__ import annotations

import json
import logging

from app.db import pool

log = logging.getLogger(__name__)

KINDS = {
    "register": "Регистрация",
    "email_verified": "Почта подтверждена",
    "login": "Вход",
    "yandex_connected": "Подключён Яндекс",
    "project_created": "Создан проект",
    "ai_question": "Вопрос ИИ",
    "action_applied": "Выполнено предложение ИИ",
    "limit_exhausted": "Лимит исчерпан",
    "payment": "Оплата",
    "plan_changed": "Смена тарифа",
}


def record(user_id: int | None, kind: str, **meta) -> None:
    if not user_id:
        return
    try:
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO user_events (user_id, kind, meta) VALUES (%s, %s, %s)",
                (user_id, kind, json.dumps(meta, ensure_ascii=False, default=str)),
            )
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать событие %s пользователя %s", kind, user_id)


def recorded_since(user_id: int, kind: str, since) -> bool:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_events WHERE user_id = %s AND kind = %s AND created_at >= %s LIMIT 1",
            (user_id, kind, since),
        ).fetchone()
    return row is not None


def list_events(user_id: int, limit: int = 50) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT kind, meta, created_at FROM user_events WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (user_id, limit),
        ).fetchall()
    return [
        {
            "kind": r["kind"],
            "label": KINDS.get(r["kind"], r["kind"]),
            "meta": r["meta"] or {},
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
