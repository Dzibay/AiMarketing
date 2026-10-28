"""Вордстат: популярные и похожие запросы с частотой за 30 дней.

Методы Wordstat в API Директа (v4 и Live 4) отключены: «Метод недоступен в данной версии API».
Отдельный api.wordstat.yandex.net Яндекс перенёс в Search API Yandex Cloud
(https://searchapi.api.cloud.yandex.net/v2/wordstat/). OAuth-токен Директа туда не подходит:
нужен API-ключ сервисного аккаунта с ролью search-api.webSearch.user и каталог (folderId).

Ключ пользователь подключает в настройках (wordstat_settings, ключ зашифрован); без него — ключ
сервиса из WORDSTAT_API_KEY и WORDSTAT_FOLDER_ID, если он задан.
Платно: 20 ₽ за 1000 запросов; квота по умолчанию — 100 запросов в час, 10 в секунду.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.db import pool
from app.direct_api import post_json
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

log = logging.getLogger("app.wordstat")

TOP_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
TIMEOUT_SECONDS = 30.0
PHRASE_MAX_LEN = 400
NUM_PHRASES_MAX = 2000
REGIONS_MAX = 100
DEVICES = {"all": "DEVICE_ALL", "desktop": "DEVICE_DESKTOP", "phone": "DEVICE_PHONE", "tablet": "DEVICE_TABLET"}

FOLDER_MAX_LEN = 50
PRICE_RUB_PER_1000 = 20
HOURLY_QUOTA = 100
# Проверка подключения — один настоящий запрос: так видно и роль, и платёжный аккаунт.
CHECK_PHRASE = "купить"

NOT_CONFIGURED = "Вордстат не подключён: Настройки → Вордстат — API-ключ Yandex Cloud и id каталога"
DENIED = (
    "Yandex Cloud не пустил к Вордстату: у сервисного аккаунта нужна роль search-api.webSearch.user, "
    "у API-ключа — область yc.search-api.execute, у облака — активный платёжный аккаунт"
)
QUOTA = "Квота Вордстата исчерпана (по умолчанию 100 запросов в час) — повторите позже"


# --- Подключение пользователя -------------------------------------------------------------------


def _row(user_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM wordstat_settings WHERE user_id = %s", (user_id,)).fetchone()


def _service_key() -> tuple[str, str] | None:
    if settings.wordstat_api_key and settings.wordstat_folder_id:
        return settings.wordstat_api_key, settings.wordstat_folder_id
    return None


def _own(row: dict | None) -> bool:
    return bool(row and row.get("api_key_enc") and row.get("folder_id"))


def _public(row: dict | None) -> dict:
    from app.ai import _key_hint

    row = row or {}
    own = _own(row)
    service = _service_key() is not None
    return {
        "configured": own or service,
        # own — ключ пользователя, service — ключ сервиса из .env, "" — не подключён.
        "source": "own" if own else "service" if service else "",
        "folder_id": row.get("folder_id") or "",
        "has_key": bool(row.get("api_key_enc")),
        "key_hint": _key_hint(row.get("api_key_enc") or ""),
        "checked_at": row["checked_at"].isoformat() if row.get("checked_at") else None,
        "check_error": row.get("check_error") or "",
        "price_rub_per_1000": PRICE_RUB_PER_1000,
        "hourly_quota": HOURLY_QUOTA,
    }


def get_settings(user_id: int) -> dict:
    return _public(_row(user_id))


def save_settings(user_id: int, *, folder_id: str = "", api_key: str | None = None) -> dict:
    """api_key: None — оставить прежний, "" — удалить. Новый ключ или каталог сбрасывает итог проверки."""
    folder_id = (folder_id or "").strip()
    if len(folder_id) > FOLDER_MAX_LEN or any(ch.isspace() for ch in folder_id):
        raise YandexError("wordstat-folder", "id каталога Yandex Cloud — без пробелов, до 50 символов (b1g…)")
    current = _row(user_id) or {}
    if api_key is None:
        key_enc = current.get("api_key_enc") or ""
    else:
        key_enc = encrypt_token(api_key.strip()) if api_key.strip() else ""
    if key_enc and not folder_id:
        raise YandexError("wordstat-folder", "Укажите id каталога Yandex Cloud, в котором создан ключ")
    changed = key_enc != (current.get("api_key_enc") or "") or folder_id != (current.get("folder_id") or "")
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO wordstat_settings (user_id, folder_id, api_key_enc)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                folder_id = EXCLUDED.folder_id,
                api_key_enc = EXCLUDED.api_key_enc,
                checked_at = CASE WHEN %s THEN NULL ELSE wordstat_settings.checked_at END,
                check_error = CASE WHEN %s THEN '' ELSE wordstat_settings.check_error END,
                updated_at = NOW()
            """,
            (user_id, folder_id, key_enc, changed, changed),
        )
    return get_settings(user_id)


def credentials(user_id: int) -> tuple[str, str]:
    """(api_key, folder_id): ключ пользователя из настроек, иначе ключ сервиса."""
    row = _row(user_id)
    if _own(row):
        try:
            return decrypt_token(row["api_key_enc"]), row["folder_id"]
        except RuntimeError as exc:
            raise YandexError("wordstat-not-configured", "Ключ Вордстата не расшифровывается — сохраните его заново") from exc
    found = _service_key()
    if found:
        return found
    raise YandexError("wordstat-not-configured", NOT_CONFIGURED)


def _remember_check(user_id: int, error: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE wordstat_settings SET checked_at = NOW(), check_error = %s WHERE user_id = %s",
            (error[:500], user_id),
        )


def check_connection(user_id: int) -> dict:
    """Пробный запрос к Вордстату (стоит как обычный, 0,02 ₽); итог запоминается у ключа пользователя."""
    api_key, folder_id = credentials(user_id)
    try:
        found = top_requests(api_key, folder_id, CHECK_PHRASE, num_phrases=3)
    except YandexError as exc:
        _remember_check(user_id, exc.message or exc.code)
        raise
    _remember_check(user_id, "")
    return {"ok": True, "phrase": found["phrase"], "total": found["total"], "sample": found["results"][:3]}


# --- Запросы ------------------------------------------------------------------------------------


def _count(value) -> int:
    # int64 в JSON Yandex Cloud приходит строкой.
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _phrases(items) -> list[dict]:
    out = []
    for item in items or []:
        phrase = str((item or {}).get("phrase") or "").strip()
        if phrase:
            out.append({"phrase": phrase, "count": _count(item.get("count"))})
    return out


def _error(response: httpx.Response) -> YandexError:
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    detail = str((payload or {}).get("message") or "").strip() if isinstance(payload, dict) else ""
    log.warning("wordstat HTTP %s %s", response.status_code, detail or response.text[:300])
    if response.status_code in (401, 403):
        return YandexError("wordstat-denied", f"{DENIED}. {detail}" if detail else DENIED)
    if response.status_code == 429:
        return YandexError("wordstat-quota", QUOTA)
    if response.status_code == 400:
        return YandexError("args", f"Вордстат отклонил запрос: {detail}" if detail else "Вордстат отклонил запрос")
    return YandexError("wordstat", f"Вордстат недоступен (HTTP {response.status_code})")


def top_requests(
    api_key: str,
    folder_id: str,
    phrase: str,
    *,
    regions: list[int] | None = None,
    device: str = "all",
    num_phrases: int = 50,
) -> dict:
    """Запросы с фразой и похожие запросы за 30 дней: {"phrase", "total", "results", "associations"}."""
    phrase = " ".join(str(phrase or "").split())
    if not phrase:
        raise YandexError("args", "Укажите фразу для Вордстата")
    if len(phrase) > PHRASE_MAX_LEN:
        raise YandexError("args", f"Фраза для Вордстата длиннее {PHRASE_MAX_LEN} символов")
    if device not in DEVICES:
        raise YandexError("args", f"device — одно из: {', '.join(DEVICES)}")
    body: dict = {
        "phrase": phrase,
        "numPhrases": max(1, min(int(num_phrases), NUM_PHRASES_MAX)),
        "folderId": folder_id,
    }
    if regions:
        body["regions"] = [str(int(r)) for r in regions[:REGIONS_MAX]]
    if device != "all":
        body["devices"] = [DEVICES[device]]
    try:
        # Чтение: 502/503/504 и обрыв соединения повторяем.
        response = post_json(
            TOP_URL,
            headers={"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"},
            body=body,
            timeout=TIMEOUT_SECONDS,
            attempts=3,
        )
    except httpx.HTTPError as exc:
        raise YandexError("wordstat", "Вордстат не ответил — повторите позже") from exc
    if response.status_code >= 400:
        raise _error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise YandexError("wordstat", "Вордстат вернул не JSON") from exc
    return {
        "phrase": phrase,
        "total": _count(payload.get("totalCount")),
        "results": _phrases(payload.get("results")),
        "associations": _phrases(payload.get("associations")),
    }
