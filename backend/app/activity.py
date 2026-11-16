"""Заходы и действия пользователей по дням (по Москве) — для меток активности и графиков админки.

Заход — любой запрос вошедшего пользователя к кабинету, не чаще раза в VISIT_GAP_SECONDS: пишется из
require_web_identity. Действие — вопрос ИИ, выполненное предложение, созданный вручную проект, правка
кампании самим пользователем. Фоновые задачи (дайджесты, автопилот, правила, ИИ по расписанию) сюда
не попадают: они не зовут ни require_web_identity, ни action().

Учёт не должен ломать запрос пользователя: ошибка пишется в лог и глотается.
"""

from __future__ import annotations

import logging
import threading
import time

from app.clock import today_msk
from app.db import pool

log = logging.getLogger(__name__)

VISIT_GAP_SECONDS = 300

# Когда этот процесс последний раз писал заход пользователя: чтобы не ходить в базу на каждый запрос.
# Между процессами повтор отсекает условие на users.last_seen_at.
_seen: dict[int, float] = {}
_seen_lock = threading.Lock()

_UPSERT = """
    INSERT INTO user_activity_days (user_id, day, visits, actions, first_at, last_at)
    VALUES (%(user_id)s, %(day)s, %(visits)s, %(actions)s, NOW(), NOW())
    ON CONFLICT (user_id, day) DO UPDATE SET
        visits = user_activity_days.visits + EXCLUDED.visits,
        actions = user_activity_days.actions + EXCLUDED.actions,
        last_at = NOW()
"""


def touch(user_id: int) -> None:
    """Запрос кабинета от вошедшего пользователя: заход, если прошлый был больше 5 минут назад."""
    now = time.monotonic()
    with _seen_lock:
        last = _seen.get(user_id)
        if last is not None and now - last < VISIT_GAP_SECONDS:
            return
        _seen[user_id] = now
    try:
        with pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE users SET last_seen_at = NOW()
                WHERE id = %s AND (last_seen_at IS NULL OR last_seen_at < NOW() - make_interval(secs => %s))
                RETURNING id
                """,
                (user_id, VISIT_GAP_SECONDS),
            ).fetchone()
            if row is None:
                return
            conn.execute(_UPSERT, {"user_id": user_id, "day": today_msk(), "visits": 1, "actions": 0})
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать заход пользователя %s", user_id)


def action(user_id: int | None) -> None:
    """Действие пользователя: +1 к действиям дня. Заход при этом не считается — его пишет touch()."""
    if not user_id:
        return
    try:
        with pool.connection() as conn:
            conn.execute(_UPSERT, {"user_id": user_id, "day": today_msk(), "visits": 0, "actions": 1})
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать действие пользователя %s", user_id)


def forget() -> None:
    """Для тестов: забыть, кого этот процесс уже видел."""
    with _seen_lock:
        _seen.clear()
