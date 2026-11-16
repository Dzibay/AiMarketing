"""«Кому написать»: готовые списки людей, которым стоит написать лично, — со счётчиками.

Рассылки из админки нет: список даёт почту и Telegram, пишет владелец сам. Заблокированные не попадают.
Подписка «кончается» по ends_at (не включая); «платная» — тариф не пробный.
"""

from __future__ import annotations

from app.clock import now_msk
from app.db import pool

LIMIT = 500

# Ключ → (название, пояснение, условие на u/s/pl, причина для строки).
LISTS: dict[str, dict] = {
    "limit": {
        "label": "Упёрлись в лимит",
        "hint": "За последние 14 дней израсходовали все единицы тарифа. Повод предложить тариф побольше.",
        "where": """EXISTS (SELECT 1 FROM user_events e WHERE e.user_id = u.id AND e.kind = 'limit_exhausted'
                            AND e.created_at > %(now)s - INTERVAL '14 days')""",
        "reason": """'лимит исчерпан ' || to_char((SELECT MAX(e.created_at) FROM user_events e
                      WHERE e.user_id = u.id AND e.kind = 'limit_exhausted') AT TIME ZONE 'Europe/Moscow', 'DD.MM')""",
    },
    "expiring": {
        "label": "Подписка кончается за 7 дней",
        "hint": "Платный тариф, последний день — в ближайшую неделю. Напомнить продлить.",
        "where": "NOT pl.is_trial AND s.ends_at > %(now)s AND s.ends_at <= %(now)s + INTERVAL '7 days'",
        "reason": "pl.name || ' до ' || to_char((s.ends_at - INTERVAL '1 microsecond') AT TIME ZONE 'Europe/Moscow', 'DD.MM')",
    },
    "lapsed": {
        "label": "Не продлили платный",
        "hint": "Платная подписка кончилась за последние 30 дней и не продлена. Спросить, что не так.",
        "where": """NOT pl.is_trial AND s.ends_at <= %(now)s AND s.ends_at > %(now)s - INTERVAL '30 days'
                    AND EXISTS (SELECT 1 FROM payments p WHERE p.user_id = u.id AND p.status = 'succeeded' AND NOT p.test)""",
        "reason": "pl.name || ' кончился ' || to_char((s.ends_at - INTERVAL '1 microsecond') AT TIME ZONE 'Europe/Moscow', 'DD.MM')",
    },
    "idle_paid": {
        "label": "Платят, но не заходят",
        "hint": "Платный тариф действует, а в кабинет не заходили больше 7 дней. Риск, что не продлят.",
        "where": """NOT pl.is_trial AND (s.ends_at IS NULL OR s.ends_at > %(now)s)
                    AND COALESCE(u.last_seen_at, u.created_at) < %(now)s - INTERVAL '7 days'""",
        "reason": "'не заходил ' || (%(now)s::date - COALESCE(u.last_seen_at, u.created_at)::date) || ' дн.'",
    },
    "no_direct": {
        "label": "Не подключили Директ",
        "hint": "Зарегистрировались больше 3 дней назад (за последние 60 дней), а Яндекс так и не подключили.",
        "where": """u.created_at < %(now)s - INTERVAL '3 days' AND u.created_at > %(now)s - INTERVAL '60 days'
                    AND NOT EXISTS (SELECT 1 FROM yandex_connections c WHERE c.user_id = u.id)""",
        "reason": "'зарегистрировался ' || to_char(u.created_at AT TIME ZONE 'Europe/Moscow', 'DD.MM')",
    },
    "no_ai": {
        "label": "Подключили, но не спросили ИИ",
        "hint": "Яндекс подключён, а ни одного вопроса ИИ-помощнику не было. Подсказать, с чего начать.",
        "where": """EXISTS (SELECT 1 FROM yandex_connections c WHERE c.user_id = u.id)
                    AND NOT EXISTS (SELECT 1 FROM user_events e WHERE e.user_id = u.id AND e.kind = 'ai_question')
                    AND NOT EXISTS (SELECT 1 FROM ai_chats c JOIN ai_messages m ON m.chat_id = c.id
                                    WHERE c.user_id = u.id AND m.role = 'user')""",
        "reason": """'Яндекс подключён ' || to_char((SELECT MIN(c.created_at) FROM yandex_connections c
                      WHERE c.user_id = u.id) AT TIME ZONE 'Europe/Moscow', 'DD.MM')""",
    },
    "trial_ends": {
        "label": "Пробный кончается завтра",
        "hint": "Последний день пробного тарифа — завтра. Самое время предложить оплату.",
        "where": """pl.is_trial AND ((s.ends_at - INTERVAL '1 microsecond') AT TIME ZONE 'Europe/Moscow')::date
                    = (%(now)s AT TIME ZONE 'Europe/Moscow')::date + 1""",
        "reason": "'пробный до ' || to_char((s.ends_at - INTERVAL '1 microsecond') AT TIME ZONE 'Europe/Moscow', 'DD.MM')",
    },
}

_FROM = """
FROM users u
LEFT JOIN subscriptions s ON s.user_id = u.id
LEFT JOIN plans pl ON pl.id = s.plan_id
WHERE u.blocked_at IS NULL AND ({where})
"""


def counts(now=None) -> list[dict]:
    now = now or now_msk()
    parts = ", ".join(f"(SELECT COUNT(*) {_FROM.format(where=d['where'])}) AS {k}" for k, d in LISTS.items())
    with pool.connection() as conn:
        row = conn.execute(f"SELECT {parts}", {"now": now}).fetchone()
    return [{"key": k, "label": d["label"], "hint": d["hint"], "count": int(row[k])} for k, d in LISTS.items()]


def items(key: str, now=None) -> dict:
    if key not in LISTS:
        return {"key": key, "items": [], "total": 0}
    now = now or now_msk()
    d = LISTS[key]
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT u.id, u.email, u.name, u.telegram_chat_id, u.last_seen_at, u.created_at,
                   pl.name AS plan_name, {d['reason']} AS reason
            {_FROM.format(where=d['where'])}
            ORDER BY COALESCE(u.last_seen_at, u.created_at) DESC, u.id DESC
            LIMIT %(lim)s
            """,
            {"now": now, "lim": LIMIT},
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) AS n {_FROM.format(where=d['where'])}", {"now": now}).fetchone()["n"]
    return {
        "key": key,
        "label": d["label"],
        "hint": d["hint"],
        "total": int(total),
        "items": [
            {
                "user_id": r["id"],
                "email": r["email"] or "",
                "name": r["name"] or "",
                "telegram": bool(r["telegram_chat_id"]),
                "plan": r["plan_name"] or "",
                "reason": r["reason"] or "",
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }
