"""Клиенты фрилансера: карточка с KPI и привязка кабинетов Директа и счётчиков Метрики."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.db import pool
from app.yandex import YandexError

KPI_TYPES = ("", "cpa", "drr")

_CLIENT_COLUMNS = """
    c.id, c.name, c.site, c.notes, c.kpi_type, c.kpi_target, c.monthly_budget_kopecks,
    c.archived_at, c.created_at, c.updated_at,
    (SELECT COUNT(*) FROM direct_accounts d WHERE d.client_row_id = c.id) AS direct_count,
    (SELECT COUNT(*) FROM metrika_counters m WHERE m.client_row_id = c.id) AS metrika_count
"""


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _client_out(row: dict) -> dict:
    budget = row.get("monthly_budget_kopecks")
    target = row.get("kpi_target")
    return {
        "id": row["id"],
        "name": row["name"],
        "site": row["site"],
        "notes": row["notes"],
        "kpi_type": row["kpi_type"],
        "kpi_target": float(target) if target is not None else None,
        "monthly_budget_rub": budget / 100 if budget is not None else None,
        "archived": row.get("archived_at") is not None,
        "archived_at": _iso(row.get("archived_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "direct_count": int(row.get("direct_count") or 0),
        "metrika_count": int(row.get("metrika_count") or 0),
    }


def _clean_name(name: str | None) -> str:
    value = (name or "").strip()[:120]
    if not value:
        raise YandexError("client-name", "Укажите название клиента")
    return value


def _clean_kpi_type(kpi_type: str | None) -> str:
    value = (kpi_type or "").strip().lower()
    if value not in KPI_TYPES:
        raise YandexError("kpi-type", "KPI — CPA или ДРР")
    return value


def _clean_kpi_target(raw: float | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise YandexError("kpi-target", "Целевое значение KPI — положительное число") from exc
    if not value.is_finite() or value <= 0:
        raise YandexError("kpi-target", "Целевое значение KPI — положительное число")
    return value.quantize(Decimal("0.0001"))


def _clean_budget(raw: float | None) -> int | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise YandexError("budget", "Бюджет — неотрицательное число") from exc
    if not value.is_finite() or value < 0:
        raise YandexError("budget", "Бюджет — неотрицательное число")
    return int((value * 100).to_integral_value())


def list_clients(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {_CLIENT_COLUMNS}
            FROM clients c
            WHERE c.user_id = %s
            ORDER BY c.archived_at IS NOT NULL, lower(c.name), c.id
            """,
            (user_id,),
        ).fetchall()
    return [_client_out(row) for row in rows]


def get_client(user_id: int, client_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT {_CLIENT_COLUMNS} FROM clients c WHERE c.id = %s AND c.user_id = %s",
            (client_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return _client_out(row)


def create_client(
    user_id: int,
    *,
    name: str,
    site: str = "",
    notes: str = "",
    kpi_type: str = "",
    kpi_target: float | None = None,
    monthly_budget_rub: float | None = None,
) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO clients (user_id, name, site, notes, kpi_type, kpi_target, monthly_budget_kopecks)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id,
                _clean_name(name),
                (site or "").strip()[:300],
                (notes or "").strip()[:5000],
                _clean_kpi_type(kpi_type),
                _clean_kpi_target(kpi_target),
                _clean_budget(monthly_budget_rub),
            ),
        ).fetchone()
    return get_client(user_id, row["id"])


def update_client(user_id: int, client_id: int, **fields) -> dict:
    """Частичное обновление: переданный None у kpi_target / monthly_budget_rub очищает поле."""
    sets: list[str] = []
    params: list = []
    if "name" in fields:
        sets.append("name = %s")
        params.append(_clean_name(fields["name"]))
    if "site" in fields:
        sets.append("site = %s")
        params.append((fields["site"] or "").strip()[:300])
    if "notes" in fields:
        sets.append("notes = %s")
        params.append((fields["notes"] or "").strip()[:5000])
    if "kpi_type" in fields:
        sets.append("kpi_type = %s")
        params.append(_clean_kpi_type(fields["kpi_type"]))
    if "kpi_target" in fields:
        sets.append("kpi_target = %s")
        params.append(_clean_kpi_target(fields["kpi_target"]))
    if "monthly_budget_rub" in fields:
        sets.append("monthly_budget_kopecks = %s")
        params.append(_clean_budget(fields["monthly_budget_rub"]))
    if fields.get("archived") is not None:
        sets.append("archived_at = CASE WHEN %s THEN COALESCE(archived_at, NOW()) END")
        params.append(bool(fields["archived"]))
    if sets:
        with pool.connection() as conn:
            row = conn.execute(
                f"""
                UPDATE clients SET {", ".join(sets)}, updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (*params, client_id, user_id),
            ).fetchone()
        if row is None:
            raise YandexError("not-found")
    return get_client(user_id, client_id)


def delete_client(user_id: int, client_id: int) -> None:
    """Кабинеты и счётчики остаются, просто без клиента (ON DELETE SET NULL)."""
    with pool.connection() as conn:
        row = conn.execute(
            "DELETE FROM clients WHERE id = %s AND user_id = %s RETURNING id",
            (client_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")


def _assign(table: str, user_id: int, item_id: int, client_id: int | None) -> dict:
    with pool.connection() as conn:
        if client_id is not None:
            owned = conn.execute(
                "SELECT id FROM clients WHERE id = %s AND user_id = %s",
                (client_id, user_id),
            ).fetchone()
            if owned is None:
                raise YandexError("not-found")
        row = conn.execute(
            f"""
            UPDATE {table} SET client_row_id = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, client_row_id
            """,
            (client_id, item_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return {"id": row["id"], "client_row_id": row["client_row_id"]}


def assign_direct_account(user_id: int, account_id: int, client_id: int | None) -> dict:
    return _assign("direct_accounts", user_id, account_id, client_id)


def assign_metrika_counter(user_id: int, counter_row_id: int, client_id: int | None) -> dict:
    return _assign("metrika_counters", user_id, counter_row_id, client_id)


def assign_new_items(
    user_id: int,
    connection_id: int,
    *,
    new_account_ids: list[int],
    new_counter_ids: list[int],
) -> None:
    """
    Раскладка только что появившихся объектов подключения по клиентам.

    Новый кабинет Директа без клиента получает собственного клиента. Новые счётчики
    отходят клиенту, только если у логина ровно один кабинет (типичный «логин клиента»);
    иначе их распределяет пользователь. Уже существующие объекты не трогаем, чтобы
    ручная раскладка не откатывалась при каждом синке.
    """
    with pool.connection() as conn:
        for account_id in new_account_ids:
            acc = conn.execute(
                """
                SELECT id, name, client_login, client_row_id FROM direct_accounts
                WHERE id = %s AND user_id = %s
                """,
                (account_id, user_id),
            ).fetchone()
            if acc is None or acc["client_row_id"] is not None:
                continue
            client = conn.execute(
                "INSERT INTO clients (user_id, name) VALUES (%s, %s) RETURNING id",
                (user_id, (acc["name"] or acc["client_login"] or "Клиент")[:120]),
            ).fetchone()
            conn.execute(
                "UPDATE direct_accounts SET client_row_id = %s WHERE id = %s",
                (client["id"], account_id),
            )

        if not new_counter_ids:
            return
        accounts = conn.execute(
            "SELECT client_row_id FROM direct_accounts WHERE connection_id = %s AND user_id = %s",
            (connection_id, user_id),
        ).fetchall()
        if len(accounts) != 1 or accounts[0]["client_row_id"] is None:
            return
        client_id = accounts[0]["client_row_id"]
        assigned = conn.execute(
            """
            UPDATE metrika_counters SET client_row_id = %s
            WHERE id = ANY(%s) AND user_id = %s AND client_row_id IS NULL
            RETURNING site
            """,
            (client_id, list(new_counter_ids), user_id),
        ).fetchall()
        site = next((r["site"] for r in assigned if r["site"]), "")
        if site:
            conn.execute(
                "UPDATE clients SET site = %s, updated_at = NOW() WHERE id = %s AND site = ''",
                (site[:300], client_id),
            )
