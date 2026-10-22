"""OAuth Яндекса и синхронизация Метрики / Директа.

Один клик: один код → токен со скоупами login + metrika + direct →
автоподтягивание счётчиков и кабинетов.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.db import pool
from app.settings_store import public_base_url
from app.token_crypto import decrypt_token, encrypt_token
from app.users import UsersError, upsert_yandex_user

log = logging.getLogger("app.yandex")

YANDEX_AUTHORIZE = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN = "https://oauth.yandex.ru/token"
YANDEX_INFO = "https://login.yandex.ru/info"
METRIKA_COUNTERS = "https://api-metrika.yandex.net/management/v1/counters"
DIRECT_API = "https://api.direct.yandex.com/json/v5/clients"
DIRECT_AGENCY = "https://api.direct.yandex.com/json/v5/agencyclients"


class YandexError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def yandex_configured() -> bool:
    return bool(settings.yandex_client_id and settings.yandex_client_secret and settings.token_encrypt_secret)


def callback_url() -> str:
    base = public_base_url()
    if not base:
        raise YandexError("base-url")
    return f"{base}/api/web/yandex/callback"


def _purge_expired_states() -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at < NOW()")


def create_oauth_state(*, purpose: str, user_id: int | None = None) -> str:
    if purpose not in ("login", "connect"):
        raise YandexError("purpose")
    if purpose == "connect" and user_id is None:
        raise YandexError("unauthorized")
    _purge_expired_states()
    state = secrets.token_urlsafe(24)
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_states (state, user_id, purpose, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (state, user_id, purpose, datetime.now(timezone.utc) + timedelta(minutes=15)),
        )
    return state


def consume_oauth_state(state: str) -> dict:
    if not state:
        raise YandexError("state")
    with pool.connection() as conn:
        row = conn.execute(
            """
            DELETE FROM oauth_states
            WHERE state = %s AND expires_at >= NOW()
            RETURNING state, user_id, purpose
            """,
            (state,),
        ).fetchone()
    if row is None:
        raise YandexError("state")
    return row


def build_authorize_url(state: str) -> str:
    if not yandex_configured():
        raise YandexError("yandex-config")
    params = {
        "response_type": "code",
        "client_id": settings.yandex_client_id,
        "redirect_uri": callback_url(),
        "scope": settings.yandex_oauth_scopes,
        "state": state,
        "force_confirm": "yes",
    }
    return f"{YANDEX_AUTHORIZE}?{urlencode(params)}"


def start_oauth(*, purpose: str, user_id: int | None = None) -> str:
    state = create_oauth_state(purpose=purpose, user_id=user_id)
    return build_authorize_url(state)


def exchange_code(code: str) -> dict:
    if not code:
        raise YandexError("code")
    if not yandex_configured():
        raise YandexError("yandex-config")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.yandex_client_id,
        "client_secret": settings.yandex_client_secret,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(YANDEX_TOKEN, data=data)
    except httpx.HTTPError as exc:
        log.warning("обмен кода Яндекса: %s", exc)
        raise YandexError("yandex") from exc
    payload = response.json() if response.content else {}
    if response.status_code >= 400 or "access_token" not in payload:
        log.warning("обмен кода Яндекса отказ: %s %s", response.status_code, payload)
        raise YandexError("yandex")
    return payload


def fetch_yandex_profile(access_token: str) -> dict:
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                YANDEX_INFO,
                params={"format": "json"},
                headers={"Authorization": f"OAuth {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise YandexError("yandex") from exc
    if response.status_code >= 400:
        raise YandexError("yandex")
    data = response.json()
    uid = str(data.get("id") or data.get("uid") or "")
    if not uid:
        raise YandexError("yandex")
    email = (data.get("default_email") or "").strip()
    if not email:
        emails = data.get("emails") or []
        email = emails[0] if emails else ""
    name = (data.get("real_name") or data.get("display_name") or data.get("login") or "").strip()
    login = (data.get("login") or "").strip()
    return {"uid": uid, "email": email, "name": name, "login": login}


def _expires_at(token_payload: dict) -> datetime | None:
    seconds = token_payload.get("expires_in")
    if not seconds:
        return None
    try:
        return datetime.now(timezone.utc) + timedelta(seconds=int(seconds))
    except (TypeError, ValueError):
        return None


def save_connection(
    *,
    user_id: int,
    profile: dict,
    token_payload: dict,
) -> dict:
    access = token_payload.get("access_token") or ""
    refresh = token_payload.get("refresh_token") or ""
    scopes = token_payload.get("scope") or settings.yandex_oauth_scopes
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO yandex_connections (
                user_id, yandex_uid, yandex_login, display_name,
                access_token_enc, refresh_token_enc, expires_at, scopes, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, yandex_uid) DO UPDATE SET
                yandex_login = EXCLUDED.yandex_login,
                display_name = EXCLUDED.display_name,
                access_token_enc = EXCLUDED.access_token_enc,
                refresh_token_enc = EXCLUDED.refresh_token_enc,
                expires_at = EXCLUDED.expires_at,
                scopes = EXCLUDED.scopes,
                updated_at = NOW()
            RETURNING id, user_id, yandex_uid, yandex_login, display_name, scopes,
                      expires_at, created_at, updated_at
            """,
            (
                user_id,
                profile["uid"],
                profile.get("login") or "",
                profile.get("name") or "",
                encrypt_token(access),
                encrypt_token(refresh) if refresh else "",
                _expires_at(token_payload),
                scopes,
            ),
        ).fetchone()
    return row


def get_connection_access_token(connection_id: int, user_id: int) -> str:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT access_token_enc FROM yandex_connections
            WHERE id = %s AND user_id = %s
            """,
            (connection_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return decrypt_token(row["access_token_enc"])


def list_connections(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, yandex_uid, yandex_login, display_name, scopes, expires_at, created_at, updated_at
            FROM yandex_connections
            WHERE user_id = %s
            ORDER BY created_at ASC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "yandex_uid": row["yandex_uid"],
            "yandex_login": row["yandex_login"],
            "display_name": row["display_name"],
            "scopes": row["scopes"],
            "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }
        for row in rows
    ]


def delete_connection(user_id: int, connection_id: int) -> None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            DELETE FROM yandex_connections
            WHERE id = %s AND user_id = %s
            RETURNING id
            """,
            (connection_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")


def fetch_metrika_counters(access_token: str) -> list[dict]:
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                METRIKA_COUNTERS,
                headers={"Authorization": f"OAuth {access_token}"},
            )
    except httpx.HTTPError as exc:
        log.warning("Метрика counters: %s", exc)
        return []
    if response.status_code >= 400:
        log.warning("Метрика counters отказ: %s %s", response.status_code, response.text[:300])
        return []
    data = response.json()
    counters = data.get("counters") or []
    result = []
    for item in counters:
        counter_id = item.get("id")
        if counter_id is None:
            continue
        result.append(
            {
                "counter_id": int(counter_id),
                "name": str(item.get("name") or ""),
                "site": str(item.get("site") or item.get("site2", {}).get("site") or ""),
            }
        )
    return result


def fetch_direct_clients(access_token: str) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "method": "get",
        "params": {
            "SelectionCriteria": {},
            "FieldNames": ["ClientId", "ClientInfo", "Login", "Currency"],
        },
    }
    clients: list[dict] = []
    for url in (DIRECT_API, DIRECT_AGENCY):
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.warning("Директ %s: %s", url, exc)
            continue
        if response.status_code >= 400:
            log.warning("Директ отказ %s: %s %s", url, response.status_code, response.text[:300])
            continue
        payload = response.json()
        if payload.get("error"):
            log.info("Директ error %s: %s", url, payload.get("error"))
            continue
        result = (payload.get("result") or {}).get("Clients") or (payload.get("result") or {}).get(
            "AgencyClients"
        ) or []
        for item in result:
            login = str(item.get("Login") or "").strip()
            if not login:
                continue
            clients.append(
                {
                    "client_login": login,
                    "client_id": int(item["ClientId"]) if item.get("ClientId") is not None else None,
                    "name": str(item.get("ClientInfo") or login),
                    "currency": str(item.get("Currency") or ""),
                }
            )
        if clients:
            break
    # Аккаунт без агентства: один кабинет — логин из профиля, если API пустой.
    return clients


def upsert_metrika(user_id: int, connection_id: int, counters: list[dict]) -> int:
    if not counters:
        return 0
    count = 0
    with pool.connection() as conn:
        for item in counters:
            conn.execute(
                """
                INSERT INTO metrika_counters (
                    user_id, connection_id, counter_id, name, site, enabled, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, TRUE, NOW())
                ON CONFLICT (connection_id, counter_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    site = EXCLUDED.site,
                    synced_at = NOW()
                """,
                (
                    user_id,
                    connection_id,
                    item["counter_id"],
                    item.get("name") or "",
                    item.get("site") or "",
                ),
            )
            count += 1
    return count


def upsert_direct(user_id: int, connection_id: int, accounts: list[dict]) -> int:
    if not accounts:
        return 0
    count = 0
    with pool.connection() as conn:
        for item in accounts:
            conn.execute(
                """
                INSERT INTO direct_accounts (
                    user_id, connection_id, client_login, client_id, name, currency, enabled, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, NOW())
                ON CONFLICT (connection_id, client_login) DO UPDATE SET
                    client_id = EXCLUDED.client_id,
                    name = EXCLUDED.name,
                    currency = EXCLUDED.currency,
                    synced_at = NOW()
                """,
                (
                    user_id,
                    connection_id,
                    item["client_login"],
                    item.get("client_id"),
                    item.get("name") or "",
                    item.get("currency") or "",
                ),
            )
            count += 1
    return count


def sync_connection(user_id: int, connection_id: int) -> dict:
    token = get_connection_access_token(connection_id, user_id)
    counters = fetch_metrika_counters(token)
    accounts = fetch_direct_clients(token)
    if not accounts:
        # Фоллбек: сам логин Яндекса как кабинет Директа.
        with pool.connection() as conn:
            conn_row = conn.execute(
                "SELECT yandex_login, display_name FROM yandex_connections WHERE id = %s AND user_id = %s",
                (connection_id, user_id),
            ).fetchone()
        if conn_row and conn_row.get("yandex_login"):
            accounts = [
                {
                    "client_login": conn_row["yandex_login"],
                    "client_id": None,
                    "name": conn_row.get("display_name") or conn_row["yandex_login"],
                    "currency": "",
                }
            ]
    metrika_n = upsert_metrika(user_id, connection_id, counters)
    direct_n = upsert_direct(user_id, connection_id, accounts)
    return {"metrika": metrika_n, "direct": direct_n}


def list_metrika(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, connection_id, counter_id, name, site, enabled, synced_at
            FROM metrika_counters
            WHERE user_id = %s
            ORDER BY name ASC, counter_id ASC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "connection_id": row["connection_id"],
            "counter_id": row["counter_id"],
            "name": row["name"],
            "site": row["site"],
            "enabled": row["enabled"],
            "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
        }
        for row in rows
    ]


def list_direct(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, connection_id, client_login, client_id, name, currency, enabled, synced_at
            FROM direct_accounts
            WHERE user_id = %s
            ORDER BY name ASC, client_login ASC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "connection_id": row["connection_id"],
            "client_login": row["client_login"],
            "client_id": row["client_id"],
            "name": row["name"],
            "currency": row["currency"],
            "enabled": row["enabled"],
            "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
        }
        for row in rows
    ]


def set_metrika_enabled(user_id: int, item_id: int, enabled: bool) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            UPDATE metrika_counters SET enabled = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, connection_id, counter_id, name, site, enabled, synced_at
            """,
            (enabled, item_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "counter_id": row["counter_id"],
        "name": row["name"],
        "site": row["site"],
        "enabled": row["enabled"],
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
    }


def set_direct_enabled(user_id: int, item_id: int, enabled: bool) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            UPDATE direct_accounts SET enabled = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, connection_id, client_login, client_id, name, currency, enabled, synced_at
            """,
            (enabled, item_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "client_login": row["client_login"],
        "client_id": row["client_id"],
        "name": row["name"],
        "currency": row["currency"],
        "enabled": row["enabled"],
        "synced_at": row["synced_at"].isoformat() if row.get("synced_at") else None,
    }


def complete_oauth(*, code: str, state: str) -> tuple[dict, dict]:
    """Обмен кода → пользователь + подключение + автосинк. Возвращает (user, sync_stats)."""
    oauth_state = consume_oauth_state(state)
    token_payload = exchange_code(code)
    access = token_payload["access_token"]
    profile = fetch_yandex_profile(access)

    existing_user_id = oauth_state.get("user_id") if oauth_state["purpose"] == "connect" else None
    try:
        user = upsert_yandex_user(
            yandex_uid=profile["uid"],
            email=profile.get("email") or "",
            name=profile.get("name") or "",
            existing_user_id=existing_user_id,
        )
    except UsersError as exc:
        raise YandexError(exc.code) from exc

    connection = save_connection(user_id=user["id"], profile=profile, token_payload=token_payload)
    try:
        stats = sync_connection(user["id"], connection["id"])
    except Exception:
        log.exception("автосинк после OAuth")
        stats = {"metrika": 0, "direct": 0, "sync_error": True}
    return user, stats
