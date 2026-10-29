"""Баланс общего счёта Директа. API v5 его не отдаёт — только Live 4, метод AccountManagement."""

from __future__ import annotations

import logging
from collections import defaultdict

import httpx

from app import direct_api
from app.db import pool
from app.stats import _rub_to_kopecks
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.direct_balance")

LOGINS_PER_REQUEST = 50

# Коды ошибок Live 4: 53 — токен не принят, 54 — нет прав на логин.
_LIVE4_ERRORS = {53: "token-expired", 54: "direct-denied"}


def fetch_balances(access_token: str, logins: list[str]) -> dict[str, dict]:
    """login → {amount_kopecks, currency}. Логина без общего счёта в ответе нет."""
    body = {
        "method": "AccountManagement",
        "token": access_token,
        "locale": "ru",
        "param": {"Action": "Get", "SelectionCriteria": {"Logins": logins}},
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(direct_api.live4_url(access_token), json=body)
    except httpx.HTTPError as exc:
        log.warning("balance: %s", exc)
        raise YandexError("direct") from exc
    if response.status_code >= 400:
        log.warning("balance HTTP %s %s", response.status_code, response.text[:300])
        raise YandexError("direct")
    try:
        data = response.json()
    except ValueError as exc:
        raise YandexError("direct") from exc
    if "error_code" in data:
        code = int(data.get("error_code") or 0)
        log.warning("balance error %s %s %s", code, data.get("error_str"), data.get("error_detail"))
        raise YandexError(_LIVE4_ERRORS.get(code, "direct"))

    result: dict[str, dict] = {}
    for account in (data.get("data") or {}).get("Accounts") or []:
        login = str(account.get("Login") or "").lower()
        if login:
            result[login] = {
                "amount_kopecks": _rub_to_kopecks(account.get("Amount")),
                "currency": str(account.get("Currency") or ""),
            }
    return result


def _save_balance(account_id: int, balance: dict | None, error: str) -> None:
    with pool.connection() as conn:
        if error:
            # Последний известный баланс не затираем: «нет данных» хуже, чем «было вчера».
            conn.execute(
                "UPDATE direct_accounts SET balance_error = %s WHERE id = %s",
                (error, account_id),
            )
        else:
            conn.execute(
                """
                UPDATE direct_accounts
                SET balance_kopecks = %s, balance_currency = %s,
                    balance_synced_at = NOW(), balance_error = ''
                WHERE id = %s
                """,
                (
                    balance["amount_kopecks"] if balance else None,
                    balance["currency"] if balance else "",
                    account_id,
                ),
            )


def sync_balances_for_user(user_id: int, account_ids: list[int] | None = None) -> dict:
    """Балансы включённых кабинетов (всех или указанных); логины одного подключения — одним запросом."""
    with pool.connection() as conn:
        accounts = conn.execute(
            """
            SELECT id, connection_id, client_login
            FROM direct_accounts
            WHERE user_id = %s AND enabled = TRUE AND client_login <> ''
              AND (%s::bigint[] IS NULL OR id = ANY(%s::bigint[]))
            """,
            (user_id, account_ids, account_ids),
        ).fetchall()
    by_connection: dict[int, list[dict]] = defaultdict(list)
    for account in accounts:
        by_connection[account["connection_id"]].append(account)

    synced = 0
    errors = 0
    for connection_id, items in by_connection.items():
        try:
            token = get_connection_access_token(connection_id, user_id)
        except YandexError as exc:
            for account in items:
                _save_balance(account["id"], None, exc.code)
            errors += len(items)
            continue
        for start in range(0, len(items), LOGINS_PER_REQUEST):
            chunk = items[start : start + LOGINS_PER_REQUEST]
            try:
                balances = fetch_balances(token, [a["client_login"] for a in chunk])
            except YandexError as exc:
                for account in chunk:
                    _save_balance(account["id"], None, exc.code)
                errors += len(chunk)
                continue
            for account in chunk:
                # Нет в ответе — у кабинета не подключён общий счёт: баланса нет, это не ошибка.
                _save_balance(account["id"], balances.get(account["client_login"].lower()), "")
                synced += 1
    return {"synced": synced, "errors": errors}
