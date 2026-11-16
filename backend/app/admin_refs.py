"""Страница «Реф-ссылки» админки: группы ссылок, таблица за период, действия над несколькими ссылками.

Период — тот же, что у дашборда (admin_dashboard.period), по умолчанию «всё время». По дате события:
клики — по дням из ref_link_clicks_daily («всё время» — общий счётчик visits, он старше учёта по дням),
регистрации — по users.created_at, оплаты — по дате оплаты.

Оплата — успешный нетестовый платёж, не возвращённый целиком; доход — сумма минус возвраты.
Конверсия — доля зарегистрированных за период, кто хоть раз оплатил (converted ÷ registrations):
делить оплативших за период на зарегистрированных за период нельзя — выйдет больше 100 %.
«Повтор. опл.» — платежи за период, которые у этого пользователя не первые за всё время.
Каскад — на один уровень: пришёл по ссылке → завёл личную ссылку → по ней зарегистрировались другие;
«Доп. доход» — их оплаты за период. Итоги групп сервер не считает: это сумма строк.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app import admin_dashboard, referrals
from app.clock import MSK, now_msk
from app.db import pool
from app.yandex import YandexError

COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
GROUP_NOT_FOUND = "Группа не найдена"
SPARK_DAYS = 7
BULK_ACTIONS = ("move", "activate", "deactivate", "delete")
MAX_BULK = 1000

_TABLE = """
WITH paid AS (
    SELECT p.user_id, p.amount_rub - p.refunded_rub AS rub, COALESCE(p.paid_at, p.created_at) AS at,
           ROW_NUMBER() OVER (PARTITION BY p.user_id ORDER BY COALESCE(p.paid_at, p.created_at), p.id) AS nth
    FROM payments p
    WHERE p.status = 'succeeded' AND NOT p.test AND p.refunded_rub < p.amount_rub AND p.user_id IS NOT NULL
),
paid_in AS (
    SELECT * FROM paid WHERE at >= %(s)s AND at < %(e)s
),
cascade AS (
    SELECT o.ref_link_id AS link_id, c.id AS user_id, c.created_at
    FROM users c
    JOIN ref_links pl ON pl.id = c.ref_link_id AND pl.owner_user_id IS NOT NULL
    JOIN users o ON o.id = pl.owner_user_id
    WHERE o.ref_link_id IS NOT NULL AND c.id <> o.id
),
regs AS (
    SELECT u.ref_link_id AS link_id, COUNT(*) AS n,
           COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM paid WHERE paid.user_id = u.id)) AS converted
    FROM users u
    WHERE u.ref_link_id IS NOT NULL AND u.created_at >= %(s)s AND u.created_at < %(e)s
    GROUP BY 1
),
pays AS (
    SELECT u.ref_link_id AS link_id, COUNT(DISTINCT pi.user_id) AS payers, COUNT(*) AS payments,
           COUNT(*) FILTER (WHERE pi.nth > 1) AS repeat, SUM(pi.rub) AS rub
    FROM paid_in pi JOIN users u ON u.id = pi.user_id
    WHERE u.ref_link_id IS NOT NULL
    GROUP BY 1
),
casc AS (
    SELECT link_id, COUNT(*) AS n FROM cascade WHERE created_at >= %(s)s AND created_at < %(e)s GROUP BY 1
),
casc_rub AS (
    SELECT c.link_id, SUM(pi.rub) AS rub FROM cascade c JOIN paid_in pi ON pi.user_id = c.user_id GROUP BY 1
),
clicks AS (
    SELECT link_id, SUM(clicks) AS n FROM ref_link_clicks_daily WHERE day BETWEEN %(d1)s AND %(d2)s GROUP BY 1
)
SELECT l.id, l.code, l.name, l.active, l.created_at, l.group_id, l.owner_user_id, ow.email AS owner_email,
       CASE WHEN %(all)s THEN l.visits ELSE COALESCE(cl.n, 0) END AS clicks,
       COALESCE(r.n, 0) AS registrations,
       COALESCE(r.converted, 0) AS converted,
       COALESCE(p.payers, 0) AS payers,
       COALESCE(p.payments, 0) AS payments,
       COALESCE(p.repeat, 0) AS repeat_payments,
       COALESCE(p.rub, 0) AS revenue_rub,
       COALESCE(c.n, 0) AS cascade,
       COALESCE(cr.rub, 0) AS cascade_rub
FROM ref_links l
LEFT JOIN users ow ON ow.id = l.owner_user_id
LEFT JOIN clicks cl ON cl.link_id = l.id
LEFT JOIN regs r ON r.link_id = l.id
LEFT JOIN pays p ON p.link_id = l.id
LEFT JOIN casc c ON c.link_id = l.id
LEFT JOIN casc_rub cr ON cr.link_id = l.id
WHERE l.owner_user_id IS NULL OR %(personal)s
ORDER BY l.created_at DESC, l.id DESC
"""


def _group_out(row: dict) -> dict:
    return {"id": int(row["id"]), "name": row["name"], "color": row["color"] or "", "position": int(row["position"])}


def list_groups() -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute("SELECT id, name, color, position FROM ref_link_groups ORDER BY position, id").fetchall()
    return [_group_out(r) for r in rows]


def _row_out(row: dict, base: str, spark: dict[int, list[int]]) -> dict:
    lid = int(row["id"])
    return {
        "id": lid,
        "code": row["code"],
        "name": row["name"] or "",
        "active": bool(row["active"]),
        "created_at": row["created_at"].isoformat(),
        "group_id": row["group_id"],
        "personal": row["owner_user_id"] is not None,
        "owner_id": row["owner_user_id"],
        "owner_email": row["owner_email"] or "",
        "url": referrals.link_url(row["code"], base),
        "clicks": int(row["clicks"]),
        "spark": spark.get(lid) or [0] * SPARK_DAYS,
        "registrations": int(row["registrations"]),
        "converted": int(row["converted"]),
        "payers": int(row["payers"]),
        "payments": int(row["payments"]),
        "repeat_payments": int(row["repeat_payments"]),
        "revenue_rub": float(row["revenue_rub"]),
        "cascade": int(row["cascade"]),
        "cascade_rub": float(row["cascade_rub"]),
    }


def build(p: str = "all", date_from: str | None = None, date_to: str | None = None, personal: bool = False, now=None) -> dict:
    """Таблица ссылок за период. Личные ссылки — только по personal=True; их число отдаётся всегда."""
    now = now or now_msk()
    per = admin_dashboard.period(p or "all", date_from, date_to, "", now)
    d1, d2 = date.fromisoformat(per["from"]), date.fromisoformat(per["to"])
    today = now.astimezone(MSK).date()
    spark_from = today - timedelta(days=SPARK_DAYS - 1)
    params = {
        "s": per["start"],
        "e": per["end"],
        "d1": d1,
        "d2": d2,
        "all": per["preset"] == "all",
        "personal": bool(personal),
    }
    with pool.connection() as conn:
        rows = conn.execute(_TABLE, params).fetchall()
        clicks = conn.execute(
            "SELECT link_id, day, clicks FROM ref_link_clicks_daily WHERE day BETWEEN %s AND %s",
            (spark_from, today),
        ).fetchall()
        personal_count = conn.execute("SELECT COUNT(*) AS n FROM ref_links WHERE owner_user_id IS NOT NULL").fetchone()["n"]
    spark: dict[int, list[int]] = {}
    for c in clicks:
        idx = (c["day"] - spark_from).days
        if 0 <= idx < SPARK_DAYS:
            spark.setdefault(int(c["link_id"]), [0] * SPARK_DAYS)[idx] = int(c["clicks"])
    base = referrals.public_base_url()
    return {
        "period": {"preset": per["preset"], "from": per["from"], "to": per["to"], "days": per["days"]},
        "spark_from": spark_from.isoformat(),
        "groups": list_groups(),
        "items": [_row_out(r, base, spark) for r in rows],
        "personal_count": int(personal_count),
    }


# --- Группы -----------------------------------------------------------------------------------


def _clean_group(data: dict) -> tuple[str, str]:
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise YandexError("ref-field", "Название группы — от 1 до 60 символов")
    color = str(data.get("color") or "").strip().lower()
    if color and not COLOR_RE.match(color):
        raise YandexError("ref-field", "Цвет группы — в виде #rrggbb")
    return name, color


def save_group(gid: int | None, data: dict) -> dict:
    name, color = _clean_group(data)
    with pool.connection() as conn:
        if gid is None:
            row = conn.execute(
                """
                INSERT INTO ref_link_groups (name, color, position)
                VALUES (%s, %s, (SELECT COALESCE(MAX(position), 0) + 1 FROM ref_link_groups))
                RETURNING id, name, color, position
                """,
                (name, color),
            ).fetchone()
        else:
            row = conn.execute(
                "UPDATE ref_link_groups SET name = %s, color = %s WHERE id = %s RETURNING id, name, color, position",
                (name, color, gid),
            ).fetchone()
    if row is None:
        raise YandexError("not-found", GROUP_NOT_FOUND)
    return _group_out(row)


def delete_group(gid: int) -> None:
    """Ссылки группы остаются и переезжают в «Без группы»."""
    with pool.connection() as conn:
        row = conn.execute("DELETE FROM ref_link_groups WHERE id = %s RETURNING id", (gid,)).fetchone()
    if row is None:
        raise YandexError("not-found", GROUP_NOT_FOUND)


def order_groups(ids: list[int]) -> list[dict]:
    """Новый порядок групп: переданные — по очереди, остальные — после них в прежнем порядке."""
    with pool.connection() as conn:
        current = [int(r["id"]) for r in conn.execute("SELECT id FROM ref_link_groups ORDER BY position, id").fetchall()]
        wanted = [i for i in dict.fromkeys(int(x) for x in ids) if i in current]
        rest = [i for i in current if i not in wanted]
        for pos, gid in enumerate(wanted + rest, start=1):
            conn.execute("UPDATE ref_link_groups SET position = %s WHERE id = %s", (pos, gid))
    return list_groups()


# --- Действия над несколькими ссылками -----------------------------------------------------------


def bulk(ids: list[int], action: str, group_id: int | None = None) -> dict:
    """Перенести в группу, включить, выключить или удалить сразу несколько ссылок.

    Переносить и удалять можно только ссылки из админки; личные — только включать и выключать
    (удалённую личную ссылку пользователь получил бы заново, уже с другим кодом).
    """
    if action not in BULK_ACTIONS:
        raise YandexError("ref-field", "Неизвестное действие")
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_BULK]
    if not ids:
        raise YandexError("ref-field", "Не выбрано ни одной ссылки")
    with pool.connection() as conn:
        if action == "move":
            gid = referrals._group_id(conn, group_id)
            rows = conn.execute(
                "UPDATE ref_links SET group_id = %s WHERE id = ANY(%s) AND owner_user_id IS NULL RETURNING id",
                (gid, ids),
            ).fetchall()
        elif action == "delete":
            rows = conn.execute(
                "DELETE FROM ref_links WHERE id = ANY(%s) AND owner_user_id IS NULL RETURNING id", (ids,)
            ).fetchall()
        else:
            rows = conn.execute(
                "UPDATE ref_links SET active = %s WHERE id = ANY(%s) RETURNING id", (action == "activate", ids)
            ).fetchall()
    return {"done": len(rows)}

