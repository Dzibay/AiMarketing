"""Вордстат: популярные и похожие запросы с частотой за 30 дней.

Методы Wordstat в API Директа (v4 и Live 4) отключены: «Метод недоступен в данной версии API».
Отдельный api.wordstat.yandex.net Яндекс перенёс в Search API Yandex Cloud
(https://searchapi.api.cloud.yandex.net/v2/wordstat/). OAuth-токен Директа туда не подходит:
нужен API-ключ сервисного аккаунта с ролью search-api.webSearch.user и каталог (folderId).

Ключ общий для сервиса: администратор подключает его в админке (service_wordstat, ключ зашифрован)
и задаёт цену запроса в единицах лимита пользователя. Каждый запрос списывает единицы (billing).
Yandex Cloud берёт 20 ₽ за 1000 запросов; квота по умолчанию — 100 запросов в час, 10 в секунду.
"""

from __future__ import annotations

import logging

import httpx

from app import billing
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

NOT_CONFIGURED = "Вордстат временно недоступен: администратор ещё не подключил его. Напишите в поддержку"
DENIED = (
    "Yandex Cloud не пустил к Вордстату: у сервисного аккаунта нужна роль search-api.webSearch.user, "
    "у API-ключа — область yc.search-api.execute, у облака — активный платёжный аккаунт"
)
QUOTA = "Квота Вордстата исчерпана (по умолчанию 100 запросов в час) — повторите позже"


# --- Подключение сервиса (админка) --------------------------------------------------------------

_KEEP = object()


def _row() -> dict:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM service_wordstat WHERE id = 1").fetchone() or {}


def _ready(row: dict) -> bool:
    return bool(row.get("api_key_enc") and row.get("folder_id"))


def _num(value) -> float | None:
    return None if value is None else float(value)


def is_configured() -> bool:
    return _ready(_row())


def get_settings() -> dict:
    from app.ai import _key_hint

    row = _row()
    return {
        "configured": _ready(row),
        "folder_id": row.get("folder_id") or "",
        "has_key": bool(row.get("api_key_enc")),
        "key_hint": _key_hint(row.get("api_key_enc") or ""),
        "checked_at": row["checked_at"].isoformat() if row.get("checked_at") else None,
        "check_error": row.get("check_error") or "",
        "units_per_request": _num(row.get("units_per_request")),
        "cost_per_request": _num(row.get("cost_per_request")),
        "price_rub_per_1000": PRICE_RUB_PER_1000,
        "hourly_quota": HOURLY_QUOTA,
    }


def save_settings(
    *, folder_id: str = "", api_key: str | None = None, units_per_request=_KEEP, cost_per_request=_KEEP
) -> dict:
    """api_key: None — оставить прежний, "" — удалить. Новый ключ или каталог сбрасывает итог проверки."""
    from app.ai import amount

    folder_id = (folder_id or "").strip()
    if len(folder_id) > FOLDER_MAX_LEN or any(ch.isspace() for ch in folder_id):
        raise YandexError("wordstat-folder", "id каталога Yandex Cloud — без пробелов, до 50 символов (b1g…)")
    current = _row()
    if api_key is None:
        key_enc = current.get("api_key_enc") or ""
    else:
        key_enc = encrypt_token(api_key.strip()) if api_key.strip() else ""
    if key_enc and not folder_id:
        raise YandexError("wordstat-folder", "Укажите id каталога Yandex Cloud, в котором создан ключ")
    units = _num(current.get("units_per_request")) if units_per_request is _KEEP else amount(units_per_request, "Цена запроса в единицах")
    cost = _num(current.get("cost_per_request")) if cost_per_request is _KEEP else amount(cost_per_request, "Себестоимость запроса, ₽")
    changed = key_enc != (current.get("api_key_enc") or "") or folder_id != (current.get("folder_id") or "")
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE service_wordstat SET folder_id = %s, api_key_enc = %s, units_per_request = %s,
                   cost_per_request = %s,
                   checked_at = CASE WHEN %s THEN NULL ELSE checked_at END,
                   check_error = CASE WHEN %s THEN '' ELSE check_error END,
                   updated_at = NOW()
            WHERE id = 1
            """,
            (folder_id, key_enc, units, cost, changed, changed),
        )
    return get_settings()


def credentials() -> tuple[str, str]:
    """(api_key, folder_id) ключа сервиса."""
    row = _row()
    if not _ready(row):
        raise YandexError("wordstat-not-configured", NOT_CONFIGURED)
    try:
        return decrypt_token(row["api_key_enc"]), row["folder_id"]
    except RuntimeError as exc:
        raise YandexError("wordstat-not-configured", NOT_CONFIGURED) from exc


def _remember_check(error: str) -> None:
    with pool.connection() as conn:
        conn.execute("UPDATE service_wordstat SET checked_at = NOW(), check_error = %s WHERE id = 1", (error[:500],))


def check_connection() -> dict:
    """Пробный запрос к Вордстату (стоит как обычный, 0,02 ₽), без списания единиц; итог запоминается."""
    api_key, folder_id = credentials()
    try:
        found = top_requests(api_key, folder_id, CHECK_PHRASE, num_phrases=3)
    except YandexError as exc:
        _remember_check(exc.message or exc.code)
        raise
    _remember_check("")
    return {"ok": True, "phrase": found["phrase"], "total": found["total"], "sample": found["results"][:3]}


class Client:
    """Запросы пользователя к Вордстату: перед каждым — проверка лимита, после — списание единиц."""

    def __init__(self, user_id: int, workspace_id: int | None = None):
        row = _row()
        self.api_key, self.folder_id = credentials()
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.units = _num(row.get("units_per_request")) or 0.0
        self.cost = _num(row.get("cost_per_request"))

    def top_requests(self, phrase: str, **kwargs) -> dict:
        billing.check(self.user_id, self.units)
        found = top_requests(self.api_key, self.folder_id, phrase, **kwargs)
        billing.record(
            self.user_id,
            kind="wordstat",
            task="wordstat",
            workspace_id=self.workspace_id,
            units=1,
            charged=self.units,
            cost_rub=self.cost,
        )
        return found


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
