"""Утренний разбор «Где горит»: что нового со вчера и что решилось.

При открытии обзора запоминаем ключи проблем за сегодня (snoozes.problem_key — тот же ключ, что у
«⋯ Скрыть») и сравниваем с последним снимком за прошлые дни (обычно вчерашним; не открывали вчера —
с более ранним, но не старше REVIEW_MAX_DAYS). Снимок — одна строка на день, перезаписывается
upsert'ом и только когда набор ключей изменился.

В снимок идут все проблемы, включая скрытые: скрыть сигнал — не значит его решить, а вернувшийся
после ухудшения сигнал — не новый. Первый день без снимка — без меток «новое» и без строки «Со вчера».

Запись снимка — «ключ|серьёзность»: у одного ключа бывает разная серьёзность (кампания «на модерации»
и «отклонена модерацией» — оба moderation:c<id>; бюджет месяца «отстаёт» и «перерасход»). Проблема,
ставшая серьёзнее, чем в снимке, считается новой; ставшая мягче — нет. Старые записи без серьёзности
сравниваются только по ключу.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.db import pool

REVIEW_MAX_DAYS = 7  # сравнивать с недельной давностью уже не «утренний разбор»
KEEP_DAYS = 14


SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def entry(key: str, severity: str | None) -> str:
    """Запись снимка: «ключ|серьёзность»."""
    return f"{key}|{severity or ''}"


def _split(item: str) -> tuple[str, int | None]:
    """«ключ|серьёзность» → (ключ, ранг серьёзности или None для старых записей без неё)."""
    key, sep, severity = item.rpartition("|")
    if not sep or severity not in SEVERITY_RANK:
        return item, None
    return key, SEVERITY_RANK[severity]


def compare(prev_keys: list[str] | set[str] | None, visible: list[dict], all_keys: set[str]) -> dict | None:
    """Чистое сравнение. Отмечает видимым проблемам is_new и возвращает {new, resolved} (None — снимка нет).

    prev_keys и all_keys — записи «ключ|серьёзность» (см. entry). new — видимые проблемы, которых не было
    в снимке или которые стали серьёзнее; resolved — ключи из снимка, которых сейчас нет совсем
    (скрытые не считаются решёнными).
    """
    if prev_keys is None:
        for p in visible:
            p["is_new"] = False
        return None
    prev: dict[str, int | None] = {}  # ключ → самая серьёзная из прошлых (меньший ранг)
    for item in prev_keys:
        key, rank = _split(item)
        if key not in prev:
            prev[key] = rank
        elif rank is not None and (prev[key] is None or rank < prev[key]):
            prev[key] = rank
    new = 0
    for p in visible:
        key = p.get("key")
        if key not in prev:
            p["is_new"] = True
        else:
            was = prev[key]
            now = SEVERITY_RANK.get(p.get("severity"))
            p["is_new"] = was is not None and now is not None and now < was
        new += p["is_new"]
    current = {_split(item)[0] for item in all_keys}
    return {"new": new, "resolved": len(set(prev) - current)}


def apply(user_id: int, visible: list[dict], all_keys: set[str], today: date) -> dict | None:
    """Сравнить со снимком прошлого дня и запомнить сегодняшний. → {since, new, resolved} или None."""
    keys = sorted(all_keys)
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT day, keys FROM problem_snapshots
            WHERE user_id = %s AND day >= %s AND day <= %s
            ORDER BY day DESC
            """,
            (user_id, today - timedelta(days=REVIEW_MAX_DAYS), today),
        ).fetchall()
        current = next((r for r in rows if r["day"] == today), None)
        prev = next((r for r in rows if r["day"] < today), None)
        if current is None or sorted(current["keys"]) != keys:
            conn.execute(
                """
                INSERT INTO problem_snapshots (user_id, day, keys) VALUES (%s, %s, %s)
                ON CONFLICT (user_id, day) DO UPDATE SET keys = EXCLUDED.keys, updated_at = NOW()
                """,
                (user_id, today, keys),
            )
        if current is None:
            # Первый снимок за день — заодно убираем старые.
            conn.execute(
                "DELETE FROM problem_snapshots WHERE user_id = %s AND day < %s",
                (user_id, today - timedelta(days=KEEP_DAYS)),
            )
    review = compare(prev["keys"] if prev else None, visible, all_keys)
    if review is None:
        return None
    return {"since": prev["day"].isoformat(), **review}
