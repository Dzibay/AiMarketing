"""OAuth Яндекса и базовый синк подключений (счётчики / кабинеты)."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app import direct_api
from app.config import settings
from app.db import pool
from app.settings_store import public_base_url
from app.token_crypto import decrypt_token, encrypt_token
from app.users import UsersError, get_user, upsert_yandex_user

log = logging.getLogger("app.yandex")

YANDEX_AUTHORIZE = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN = "https://oauth.yandex.ru/token"
YANDEX_INFO = "https://login.yandex.ru/info"
METRIKA_COUNTERS = "https://api-metrika.yandex.net/management/v1/counters"

# Режим входа «подключить»: что берём из этого логина.
OAUTH_MODES = ("", "metrika", "direct", "sandbox")
# login / connect — основное приложение (вход, Метрика, Директ); audience — второе, Яндекс Аудитории.
OAUTH_PURPOSES = ("login", "connect", "audience")

# Коды API Директа, которые повтор не исправит: заявка на доступ, права, логин без Директа.
DIRECT_DENIED_CODES = frozenset({54, 58, 513, 3000, 3001})
# Токен отозван или недействителен — нужен повторный вход.
DIRECT_AUTH_CODES = frozenset({53})
UNITS_CODE = 152


class YandexError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = (message or "").strip() or None
        super().__init__(code)


def direct_api_error(payload: dict | None, status_code: int) -> YandexError:
    """Собрать YandexError из ответа API Директа (error или AddResults)."""
    payload = payload or {}
    err = payload.get("error") or {}
    detail = str(err.get("error_detail") or err.get("error_string") or "").strip()
    if not detail:
        for key in ("AddResults", "UpdateResults", "DeleteResults"):
            for item in payload.get("result", {}).get(key) or payload.get(key) or []:
                for e in item.get("Errors") or []:
                    detail = str(e.get("Message") or e.get("Details") or "").strip()
                    if detail:
                        break
                if detail:
                    break
            if detail:
                break
    try:
        number = int(err.get("error_code"))
    except (TypeError, ValueError):
        number = None
    # Решаем по коду ошибки Директа: HTTP 400 приходит и на обычную ошибку в запросе.
    if number == UNITS_CODE:
        # Кончились баллы: восстанавливаются в течение суток, повтор позже пройдёт.
        code = "units"
    elif number in DIRECT_AUTH_CODES:
        code = "token-expired"
    elif number in DIRECT_DENIED_CODES or (number is None and status_code == 403):
        code = "direct-denied"
    else:
        code = "direct"
    return YandexError(code, detail or None)


def direct_sync_error(err: dict | None, status_code: int) -> str:
    """Ошибка синка логина: код и через «|» ответ Директа как есть.

    «direct-denied|Незавершенная регистрация (ошибка 58). Необходимо создать заявку…» —
    по одному нашему коду не понять, что именно не так с доступом.
    """
    err = err or {}
    try:
        number = int(err.get("error_code"))
    except (TypeError, ValueError):
        number = None
    title = str(err.get("error_string") or "").strip()
    detail = str(err.get("error_detail") or "").strip()
    if number is None:
        text = title
    else:
        text = f"{title} (ошибка {number})" if title else f"Ошибка {number}"
    if detail and detail != title:
        text = f"{text}. {detail}" if text else detail
    if not text and status_code in (502, 503, 504):
        text = f"сервис не ответил (HTTP {status_code})"
    elif not text and status_code >= 400:
        text = f"HTTP {status_code}"
    # «;» и «|» разделяют части строки статуса синка.
    text = text.replace(";", ",").replace("|", "/")
    code = "direct-denied" if number in DIRECT_DENIED_CODES else "direct"
    return f"{code}|{text}" if text else code


def yandex_configured() -> bool:
    return bool(settings.yandex_client_id and settings.yandex_client_secret and settings.token_encrypt_secret)


def audience_configured() -> bool:
    """Задано второе приложение — Яндекс Аудитории (новые LAL). Нет — функции просто нет."""
    return bool(
        settings.yandex_audience_client_id and settings.yandex_audience_client_secret and settings.token_encrypt_secret
    )


def _client(audience: bool = False) -> tuple[str, str]:
    """(client_id, client_secret) основного приложения или приложения Аудиторий."""
    if audience:
        if not audience_configured():
            raise YandexError("audience-config")
        return settings.yandex_audience_client_id, settings.yandex_audience_client_secret
    if not yandex_configured():
        raise YandexError("yandex-config")
    return settings.yandex_client_id, settings.yandex_client_secret


def callback_url() -> str:
    base = public_base_url()
    if not base:
        raise YandexError("base-url")
    return f"{base}/api/web/yandex/callback"


def _purge_expired_states() -> None:
    with pool.connection() as conn:
        conn.execute("DELETE FROM oauth_states WHERE expires_at < NOW()")


def create_oauth_state(
    *, purpose: str, user_id: int | None = None, mode: str = "", connection_id: int | None = None
) -> str:
    """purpose: login / connect — основное приложение; audience — Аудитории логина connection_id."""
    if purpose not in OAUTH_PURPOSES:
        raise YandexError("purpose")
    if mode not in OAUTH_MODES or (mode and purpose == "audience"):
        raise YandexError("mode")
    if mode == "sandbox":
        raise YandexError("sandbox", direct_api.SANDBOX_CLOSED)
    if purpose in ("connect", "audience") and user_id is None:
        raise YandexError("unauthorized")
    if purpose == "audience" and connection_id is None:
        raise YandexError("not-found")
    _purge_expired_states()
    state = secrets.token_urlsafe(24)
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_states (state, user_id, purpose, mode, connection_id, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                state,
                user_id,
                purpose,
                mode,
                connection_id if purpose == "audience" else None,
                datetime.now(timezone.utc) + timedelta(minutes=15),
            ),
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
            RETURNING state, user_id, purpose, mode, connection_id
            """,
            (state,),
        ).fetchone()
    if row is None:
        raise YandexError("state")
    return row


def build_authorize_url(state: str, *, audience: bool = False, login_hint: str = "") -> str:
    client_id, _secret = _client(audience)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback_url(),
        "scope": settings.yandex_audience_scopes if audience else settings.yandex_oauth_scopes,
        "state": state,
        "force_confirm": "yes",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{YANDEX_AUTHORIZE}?{urlencode(params)}"


def start_oauth(
    *, purpose: str, user_id: int | None = None, mode: str = "", connection_id: int | None = None
) -> str:
    if purpose == "audience":
        _client(audience=True)
        if user_id is None:
            raise YandexError("unauthorized")
        row = _audience_connection(user_id, connection_id)
        state = create_oauth_state(purpose=purpose, user_id=user_id, connection_id=row["id"])
        return build_authorize_url(state, audience=True, login_hint=row["yandex_login"] or "")
    state = create_oauth_state(purpose=purpose, user_id=user_id, mode=mode)
    return build_authorize_url(state)


def _audience_connection(user_id: int, connection_id: int | None) -> dict:
    """Логин, которому подключают Аудитории: свой и не песочница."""
    if connection_id is None:
        raise YandexError("not-found")
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, yandex_uid, yandex_login, sandbox FROM yandex_connections WHERE id = %s AND user_id = %s",
            (connection_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if row["sandbox"]:
        raise YandexError("sandbox", "У песочницы Директа нет Яндекс Аудиторий")
    return row


def exchange_code(code: str, *, audience: bool = False) -> dict:
    if not code:
        raise YandexError("code")
    client_id, client_secret = _client(audience)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
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


def refresh_access_token(refresh_token: str, *, audience: bool = False) -> dict:
    if not refresh_token:
        raise YandexError("token-expired")
    client_id, client_secret = _client(audience)
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(YANDEX_TOKEN, data=data)
    except httpx.HTTPError as exc:
        # Сбой сети — не повод переподключать аккаунт: задача повторится позже.
        log.warning("refresh Яндекса: %s", exc)
        raise YandexError("oauth-unavailable") from exc
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if response.status_code >= 500 or response.status_code == 429:
        log.warning("refresh Яндекса HTTP %s", response.status_code)
        raise YandexError("oauth-unavailable")
    if response.status_code >= 400 or "access_token" not in payload:
        log.warning("refresh Яндекса отказ: %s %s", response.status_code, payload)
        raise YandexError("token-expired")
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


def save_connection(*, user_id: int, profile: dict, token_payload: dict, mode: str = "") -> dict:
    """
    Новое подключение получает роль режима входа (без режима — Метрику и Директ).
    Повторный вход того же логина добавляет роль режима и не снимает уже включённые.
    """
    access = token_payload.get("access_token") or ""
    sandbox = mode == "sandbox"
    new_metrika = mode in ("", "metrika")
    new_direct = mode in ("", "direct", "sandbox")
    refresh = token_payload.get("refresh_token") or ""
    scopes = token_payload.get("scope") or settings.yandex_oauth_scopes
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO yandex_connections (
                user_id, yandex_uid, yandex_login, display_name,
                access_token_enc, refresh_token_enc, expires_at, scopes,
                use_metrika, use_direct, sandbox, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, yandex_uid, sandbox) DO UPDATE SET
                use_metrika = yandex_connections.use_metrika OR %s,
                use_direct = yandex_connections.use_direct OR %s,
                yandex_login = EXCLUDED.yandex_login,
                display_name = EXCLUDED.display_name,
                access_token_enc = EXCLUDED.access_token_enc,
                refresh_token_enc = CASE
                    WHEN EXCLUDED.refresh_token_enc <> '' THEN EXCLUDED.refresh_token_enc
                    ELSE yandex_connections.refresh_token_enc
                END,
                expires_at = EXCLUDED.expires_at,
                scopes = EXCLUDED.scopes,
                updated_at = NOW()
            RETURNING id, user_id, yandex_uid, yandex_login, display_name, scopes,
                      expires_at, last_sync_at, last_sync_status, last_sync_error,
                      use_metrika, use_direct, sandbox, created_at, updated_at
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
                new_metrika and not sandbox,
                new_direct,
                sandbox,
                mode == "metrika",
                mode in ("direct", "sandbox"),
            ),
        ).fetchone()
    return row


def _update_connection_tokens(connection_id: int, token_payload: dict) -> None:
    refresh = token_payload.get("refresh_token") or ""
    with pool.connection() as conn:
        if refresh:
            conn.execute(
                """
                UPDATE yandex_connections
                SET access_token_enc = %s,
                    refresh_token_enc = %s,
                    expires_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    encrypt_token(token_payload["access_token"]),
                    encrypt_token(refresh),
                    _expires_at(token_payload),
                    connection_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE yandex_connections
                SET access_token_enc = %s,
                    expires_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    encrypt_token(token_payload["access_token"]),
                    _expires_at(token_payload),
                    connection_id,
                ),
            )


def get_connection_access_token(connection_id: int, user_id: int | None = None) -> direct_api.AccessToken:
    """Возвращает валидный access token; при необходимости обновляет через refresh.

    Токен подключения-песочницы помечен — запросы Директа с ним уходят в песочницу.
    """
    with pool.connection() as conn:
        if user_id is None:
            row = conn.execute(
                """
                SELECT id, access_token_enc, refresh_token_enc, expires_at, sandbox
                FROM yandex_connections WHERE id = %s
                """,
                (connection_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, access_token_enc, refresh_token_enc, expires_at, sandbox
                FROM yandex_connections WHERE id = %s AND user_id = %s
                """,
                (connection_id, user_id),
            ).fetchone()
    if row is None:
        raise YandexError("not-found")

    expires = row.get("expires_at")
    need_refresh = False
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        need_refresh = expires <= datetime.now(timezone.utc) + timedelta(minutes=5)

    sandbox = bool(row.get("sandbox"))
    if not need_refresh:
        return direct_api.AccessToken(decrypt_token(row["access_token_enc"]), sandbox)

    refresh_plain = decrypt_token(row["refresh_token_enc"]) if row.get("refresh_token_enc") else ""
    if not refresh_plain:
        # Токен без срока / без refresh — пробуем старый
        if expires is None:
            return direct_api.AccessToken(decrypt_token(row["access_token_enc"]), sandbox)
        raise YandexError("token-expired")

    payload = refresh_access_token(refresh_plain)
    _update_connection_tokens(connection_id, payload)
    return direct_api.AccessToken(payload["access_token"], sandbox)


def save_audience_token(connection_id: int, token_payload: dict) -> None:
    """Токен второго приложения — у того же подключения; повторный вход заменяет его."""
    refresh = token_payload.get("refresh_token") or ""
    scopes = token_payload.get("scope") or settings.yandex_audience_scopes
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE yandex_connections
            SET audience_token_enc = %s,
                audience_refresh_enc = CASE WHEN %s <> '' THEN %s ELSE audience_refresh_enc END,
                audience_expires_at = %s,
                audience_scopes = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                encrypt_token(token_payload["access_token"]),
                refresh,
                encrypt_token(refresh) if refresh else "",
                _expires_at(token_payload),
                scopes,
                connection_id,
            ),
        )


def get_audience_access_token(connection_id: int, user_id: int | None = None) -> str:
    """Валидный токен Яндекс Аудиторий логина; нет или отозван — YandexError audience-scope."""
    if not audience_configured():
        raise YandexError("audience-config")
    sql = "SELECT audience_token_enc, audience_refresh_enc, audience_expires_at FROM yandex_connections WHERE id = %s"
    params: tuple = (connection_id,)
    if user_id is not None:
        sql += " AND user_id = %s"
        params = (connection_id, user_id)
    with pool.connection() as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row["audience_token_enc"]:
        raise YandexError("audience-scope")
    expires = row.get("audience_expires_at")
    if expires is None:
        return decrypt_token(row["audience_token_enc"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires > datetime.now(timezone.utc) + timedelta(minutes=5):
        return decrypt_token(row["audience_token_enc"])
    refresh = decrypt_token(row["audience_refresh_enc"]) if row.get("audience_refresh_enc") else ""
    try:
        payload = refresh_access_token(refresh, audience=True)
    except YandexError as exc:
        if exc.code == "token-expired":
            raise YandexError("audience-scope") from exc
        raise
    save_audience_token(connection_id, payload)
    return payload["access_token"]


def set_connection_sync_status(
    connection_id: int,
    *,
    status: str,
    error: str = "",
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE yandex_connections
            SET last_sync_at = NOW(),
                last_sync_status = %s,
                last_sync_error = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (status, error[:500], connection_id),
        )


def list_connections(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.yandex_uid, c.yandex_login, c.display_name, c.scopes, c.expires_at,
                   c.last_sync_at, c.last_sync_status, c.last_sync_error, c.created_at, c.updated_at,
                   c.use_metrika, c.use_direct, c.sandbox, c.audience_token_enc <> '' AS audience,
                   (SELECT COUNT(*) FROM metrika_counters m WHERE m.connection_id = c.id) AS counters,
                   (SELECT COUNT(*) FROM direct_accounts d WHERE d.connection_id = c.id) AS accounts,
                   (SELECT COUNT(*) FROM direct_campaigns dc
                      JOIN direct_accounts d ON d.id = dc.account_id
                     WHERE d.connection_id = c.id) AS campaigns
            FROM yandex_connections c
            WHERE c.user_id = %s
            ORDER BY c.sandbox, c.created_at ASC
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
            "use_metrika": row["use_metrika"],
            "use_direct": row["use_direct"],
            "sandbox": row["sandbox"],
            "audience": bool(row["audience"]),
            "counters": int(row["counters"] or 0),
            "accounts": int(row["accounts"] or 0),
            "campaigns": int(row["campaigns"] or 0),
            "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
            "last_sync_at": row["last_sync_at"].isoformat() if row.get("last_sync_at") else None,
            "last_sync_status": row.get("last_sync_status") or "",
            "last_sync_error": row.get("last_sync_error") or "",
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }
        for row in rows
    ]


def set_connection_roles(
    user_id: int, connection_id: int, *, use_metrika: bool | None = None, use_direct: bool | None = None
) -> dict:
    """
    Роль выключается обратимо: счётчики/кабинеты логина отключаются (enabled = FALSE),
    данные и привязки к пространствам остаются; включение роли возвращает их.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id, use_metrika, use_direct, sandbox FROM yandex_connections WHERE id = %s AND user_id = %s",
            (connection_id, user_id),
        ).fetchone()
        if row is None:
            raise YandexError("not-found")
        metrika = row["use_metrika"] if use_metrika is None else bool(use_metrika)
        direct = row["use_direct"] if use_direct is None else bool(use_direct)
        if row["sandbox"]:
            metrika = False
            if not direct:
                raise YandexError("roles", "Песочница — только Директ")
        if not metrika and not direct:
            raise YandexError("roles", "Оставьте Метрику или Директ — или удалите подключение")
        conn.execute(
            "UPDATE yandex_connections SET use_metrika = %s, use_direct = %s, updated_at = NOW() WHERE id = %s",
            (metrika, direct, connection_id),
        )
        if metrika != row["use_metrika"]:
            conn.execute("UPDATE metrika_counters SET enabled = %s WHERE connection_id = %s", (metrika, connection_id))
        if direct != row["use_direct"]:
            conn.execute("UPDATE direct_accounts SET enabled = %s WHERE connection_id = %s", (direct, connection_id))
    return {"id": connection_id, "use_metrika": metrika, "use_direct": direct, "sandbox": row["sandbox"]}


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
        raise YandexError("metrika") from exc
    if response.status_code >= 400:
        log.warning("Метрика counters отказ: %s %s", response.status_code, response.text[:300])
        raise YandexError("metrika")
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


CLIENT_FIELDS = ["ClientId", "ClientInfo", "Login", "Currency", "Type"]


def _direct_get(access_token: str, service: str, params: dict) -> tuple[list[dict], str]:
    """get в clients / agencyclients: (элементы, ошибка_или_пусто) — ошибка в формате ``direct_sync_error``."""
    if direct_api.is_sandbox(access_token):
        return [], f"direct-denied|{direct_api.SANDBOX_CLOSED}"
    url = direct_api.v5_url(access_token, service)
    try:
        response = direct_api.post_json(
            url,
            headers=direct_api.headers(access_token),
            body={"method": "get", "params": params},
            timeout=30.0,
            attempts=3,
        )
    except httpx.HTTPError as exc:
        log.warning("Директ %s: %s", url, exc)
        return [], "direct"
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    err = payload.get("error") if isinstance(payload, dict) else None
    if err or response.status_code >= 400:
        log.warning("Директ отказ %s: %s %s", url, response.status_code, err or response.text[:300])
        return [], direct_sync_error(err, response.status_code)
    result = payload.get("result") or {}
    return list(result.get("Clients") or result.get("AgencyClients") or []), ""


def fetch_direct_clients(access_token: str) -> tuple[list[dict], str]:
    """Кабинеты логина: у рекламодателя — он сам, у агентства — его клиенты.

    Возвращает (кабинеты, ошибка_или_пусто) — ошибка в формате ``direct_sync_error``.
    """
    # clients.get отдаёт параметры самого логина (у агентства — агентства) и SelectionCriteria
    # не принимает: с ним Директ отвечает ошибкой 8000.
    items, error = _direct_get(access_token, "clients", {"FieldNames": CLIENT_FIELDS})
    if error:
        return [], error
    if any(str(item.get("Type") or "").upper() == "AGENCY" for item in items):
        # Пустой SelectionCriteria — все клиенты агентства, включая архивных.
        items, error = _direct_get(
            access_token, "agencyclients", {"SelectionCriteria": {}, "FieldNames": CLIENT_FIELDS}
        )
        if error:
            return [], error
    clients: list[dict] = []
    for item in items:
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
    return clients, ""


def upsert_metrika(user_id: int, connection_id: int, counters: list[dict]) -> tuple[int, list[int]]:
    """Возвращает (сколько записано, id впервые появившихся счётчиков)."""
    if not counters:
        return 0, []
    count = 0
    new_ids: list[int] = []
    with pool.connection() as conn:
        for item in counters:
            row = conn.execute(
                """
                INSERT INTO metrika_counters (
                    user_id, connection_id, counter_id, name, site, enabled, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, TRUE, NOW())
                ON CONFLICT (connection_id, counter_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    site = EXCLUDED.site,
                    synced_at = NOW()
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    user_id,
                    connection_id,
                    item["counter_id"],
                    item.get("name") or "",
                    item.get("site") or "",
                ),
            ).fetchone()
            count += 1
            if row["inserted"]:
                new_ids.append(int(row["id"]))
    return count, new_ids


def upsert_direct(user_id: int, connection_id: int, accounts: list[dict]) -> tuple[int, list[int]]:
    """Возвращает (сколько записано, id впервые появившихся кабинетов)."""
    if not accounts:
        return 0, []
    count = 0
    new_ids: list[int] = []
    with pool.connection() as conn:
        for item in accounts:
            row = conn.execute(
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
                RETURNING id, (xmax = 0) AS inserted
                """,
                (
                    user_id,
                    connection_id,
                    item["client_login"],
                    item.get("client_id"),
                    item.get("name") or "",
                    item.get("currency") or "",
                ),
            ).fetchone()
            count += 1
            if row["inserted"]:
                new_ids.append(int(row["id"]))
    return count, new_ids


def sync_connection(user_id: int, connection_id: int) -> dict:
    from app.sync_runs import finish_run, start_run
    from app.workspaces import ensure_workspaces

    run_id = start_run(user_id=user_id, kind="connection", target_id=connection_id)
    try:
        token = get_connection_access_token(connection_id, user_id)
    except YandexError as exc:
        set_connection_sync_status(connection_id, status="error", error=exc.code)
        finish_run(run_id, status="error", error=exc.code)
        raise

    metrika_error = ""
    direct_error = ""
    counters: list[dict] = []
    accounts: list[dict] = []
    with pool.connection() as conn:
        conn_row = conn.execute(
            """
            SELECT yandex_login, display_name, use_metrika, use_direct, sandbox
            FROM yandex_connections WHERE id = %s AND user_id = %s
            """,
            (connection_id, user_id),
        ).fetchone()
    if conn_row is None:
        finish_run(run_id, status="error", error="not-found")
        raise YandexError("not-found")

    if conn_row["use_metrika"] and not conn_row["sandbox"]:
        try:
            counters = fetch_metrika_counters(token)
        except YandexError as exc:
            metrika_error = exc.code

    if conn_row["use_direct"]:
        accounts, direct_error = fetch_direct_clients(token)
    if conn_row["use_direct"] and not accounts:
        if conn_row.get("yandex_login") and not direct_error:
            accounts = [
                {
                    "client_login": conn_row["yandex_login"],
                    "client_id": None,
                    "name": conn_row.get("display_name") or conn_row["yandex_login"],
                    "currency": "",
                }
            ]

    metrika_n, new_counter_ids = upsert_metrika(user_id, connection_id, counters)
    direct_n, new_account_ids = upsert_direct(user_id, connection_id, accounts)
    ensure_workspaces(user_id)

    status = "ok"
    error_parts = []
    if metrika_error:
        error_parts.append(f"metrika:{metrika_error}")
    if direct_error:
        error_parts.append(f"direct:{direct_error}")
    if error_parts:
        status = "partial" if (metrika_n or direct_n) else "error"
    set_connection_sync_status(connection_id, status=status, error=";".join(error_parts))
    result = {
        "metrika": metrika_n,
        "direct": direct_n,
        "status": status,
        "error": ";".join(error_parts),
        "metrika_error": metrika_error,
        "direct_error": direct_error,
        "new_counters": len(new_counter_ids),
        "new_accounts": len(new_account_ids),
    }
    finish_run(run_id, status=status, error=";".join(error_parts), meta=result)
    return result


def list_all_connection_ids() -> list[tuple[int, int]]:
    with pool.connection() as conn:
        rows = conn.execute("SELECT id, user_id FROM yandex_connections").fetchall()
    return [(int(r["id"]), int(r["user_id"])) for r in rows]


def list_enabled_direct_account_ids() -> list[tuple[int, int]]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, user_id FROM direct_accounts WHERE enabled = TRUE ORDER BY id"
        ).fetchall()
    return [(int(r["id"]), int(r["user_id"])) for r in rows]


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _metrika_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "client_row_id": row.get("client_row_id"),
        "counter_id": row["counter_id"],
        "name": row["name"],
        "site": row["site"],
        "enabled": row["enabled"],
        "synced_at": _iso(row.get("synced_at")),
        "load": _counter_load(row),
    }


def _counter_load(row: dict) -> dict | None:
    if "load_state" not in row:
        return None
    from app.account_load import counter_load_out

    return counter_load_out(row)


def _direct_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "client_row_id": row.get("client_row_id"),
        "client_login": row["client_login"],
        "client_id": row["client_id"],
        "name": row["name"],
        "currency": row["currency"],
        "enabled": row["enabled"],
        "last_sync_at": _iso(row.get("last_sync_at")),
        "last_sync_status": row.get("last_sync_status") or "",
        "last_sync_error": row.get("last_sync_error") or "",
        "synced_at": _iso(row.get("synced_at")),
        "campaigns": int(row.get("campaigns") or 0),
        "workspaces": int(row.get("workspaces") or 0),
        "load": _account_load(row),
    }


def _account_load(row: dict) -> dict | None:
    if "load_state" not in row:
        return None
    from app.account_load import account_load_out

    return account_load_out(row)


def list_metrika(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, connection_id, client_row_id, counter_id, name, site, enabled, synced_at,
                   load_state, loaded_at
            FROM metrika_counters
            WHERE user_id = %s
            ORDER BY name ASC, counter_id ASC
            """,
            (user_id,),
        ).fetchall()
    return [_metrika_out(row) for row in rows]


def list_direct(user_id: int) -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, connection_id, client_row_id, client_login, client_id, name, currency,
                   enabled, last_sync_at, last_sync_status, last_sync_error, synced_at,
                   load_state, loaded_at, units_left, units_limit, units_at,
                   (SELECT COUNT(*) FROM direct_campaigns c WHERE c.account_id = direct_accounts.id) AS campaigns,
                   (SELECT COUNT(*) FROM workspace_accounts wa WHERE wa.account_id = direct_accounts.id) AS workspaces
            FROM direct_accounts
            WHERE user_id = %s
            ORDER BY name ASC, client_login ASC
            """,
            (user_id,),
        ).fetchall()
    return [_direct_out(row) for row in rows]


def set_metrika_enabled(user_id: int, item_id: int, enabled: bool) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            UPDATE metrika_counters SET enabled = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, connection_id, client_row_id, counter_id, name, site, enabled, synced_at,
                      load_state, loaded_at
            """,
            (enabled, item_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return _metrika_out(row)


def set_direct_enabled(user_id: int, item_id: int, enabled: bool) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            UPDATE direct_accounts SET enabled = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, connection_id, client_row_id, client_login, client_id, name, currency,
                      enabled, last_sync_at, last_sync_status, last_sync_error, synced_at,
                      load_state, loaded_at, units_left, units_limit, units_at
            """,
            (enabled, item_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    return _direct_out(row)


def _complete_audience(oauth_state: dict, code: str) -> dict:
    """Аудитории подключены тому логину, у которого нажали кнопку: другой логин — отказ."""
    user_id = oauth_state.get("user_id")
    if user_id is None:
        raise YandexError("unauthorized")
    row = _audience_connection(user_id, oauth_state.get("connection_id"))
    token_payload = exchange_code(code, audience=True)
    # login.yandex.ru/info отдаёт id и логин с токеном любого сервиса.
    profile = fetch_yandex_profile(token_payload["access_token"])
    if profile["uid"] != str(row["yandex_uid"]):
        raise YandexError("audience-login")
    save_audience_token(row["id"], token_payload)
    user = get_user(user_id)
    if user is None:
        raise YandexError("unauthorized")
    return user


def complete_oauth(*, code: str, state: str) -> tuple[dict, dict | None]:
    oauth_state = consume_oauth_state(state)
    if oauth_state["purpose"] == "audience":
        return _complete_audience(oauth_state, code), None
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

    connection = save_connection(
        user_id=user["id"], profile=profile, token_payload=token_payload, mode=oauth_state.get("mode") or ""
    )
    # Синк — задачей в очереди, чтобы редирект после согласия не ждал API Яндекса.
    from app.job_handlers import submit
    from app.jobs import PRIORITY_USER

    # deep — сразу подтянуть кампании кабинетов и цели счётчиков.
    job = submit(
        "sync_connection",
        user["id"],
        {"connection_id": connection["id"], "deep": True},
        priority=PRIORITY_USER,
    )
    return user, job
