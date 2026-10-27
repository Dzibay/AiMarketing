"""Общий клиент API Директа.

Токен подключения-песочницы — строка с признаком ``sandbox``. Песочницу Яндекс отключил
15.07.2026: вызовы с таким токеном сразу падают с понятной ошибкой, а не уходят на мёртвый адрес.
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("app.direct_api")

# Шлюз Директа (особенно песочница) часто отвечает так на живой запрос. Повтор уместен
# только для чтения: add при 504 мог уже создать объект.
TRANSIENT_STATUS = frozenset({502, 503, 504})

V5 = "https://api.direct.yandex.com/json/v5/"
V5_SANDBOX = "https://api-sandbox.direct.yandex.com/json/v5/"
LIVE4 = "https://api.direct.yandex.ru/live/v4/json/"
LIVE4_SANDBOX = "https://api-sandbox.direct.yandex.ru/live/v4/json/"

# Наибольшая страница get в API v5.
PAGE_LIMIT = 10000

SANDBOX_CLOSED = "Песочница API Директа отключена Яндексом 15.07.2026 — подключите боевой кабинет"

# Баллы из заголовка Units последнего ответа по логину клиента: (потрачено, остаток, лимит).
_units: dict[str, tuple[int, int, int]] = {}


class AccessToken(str):
    """OAuth-токен подключения; sandbox=True — запросы Директа идут в песочницу."""

    sandbox: bool = False

    def __new__(cls, value: str, sandbox: bool = False) -> "AccessToken":
        obj = super().__new__(cls, value)
        obj.sandbox = bool(sandbox)
        return obj


def is_sandbox(token) -> bool:
    return bool(getattr(token, "sandbox", False))


def v5_url(token, service: str) -> str:
    return (V5_SANDBOX if is_sandbox(token) else V5) + service


def live4_url(token) -> str:
    return LIVE4_SANDBOX if is_sandbox(token) else LIVE4


def headers(token, client_login: str | None = None) -> dict:
    out = {
        "Authorization": f"Bearer {token}",
        "Accept-Language": "ru",
        "Content-Type": "application/json; charset=utf-8",
    }
    if client_login:
        out["Client-Login"] = client_login
    return out


def post_json(
    url: str,
    *,
    headers: dict | None,
    body: dict,
    timeout: float,
    attempts: int = 1,
) -> httpx.Response:
    """POST JSON. attempts>1 повторяет 502/503/504 и обрыв соединения."""
    last_error: Exception | None = None
    response: httpx.Response | None = None
    attempts = max(1, attempts)
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            last_error = exc
            response = None
            log.warning("Директ %s: %s (попытка %s/%s)", url, exc, attempt + 1, attempts)
        else:
            if response.status_code not in TRANSIENT_STATUS:
                return response
            last_error = None
            log.warning("Директ %s HTTP %s (попытка %s/%s)", url, response.status_code, attempt + 1, attempts)
        if attempt + 1 < attempts:
            time.sleep(0.5 * (attempt + 1))
    if response is not None:
        return response
    assert last_error is not None
    raise last_error


def _remember_units(client_login: str | None, response: httpx.Response) -> None:
    headers = getattr(response, "headers", None) or {}
    parts = str(headers.get("Units") or "").split("/")
    if len(parts) != 3:
        return
    try:
        spent, left, limit = (int(p) for p in parts)
    except ValueError:
        return
    _units[(client_login or "").lower()] = (spent, left, limit)


def last_units(client_login: str | None) -> dict | None:
    """Баллы клиента по последнему ответу API в этом процессе: {"spent", "left", "limit"}."""
    found = _units.get((client_login or "").lower())
    if found is None:
        return None
    return {"spent": found[0], "left": found[1], "limit": found[2]}


def call(token, client_login: str | None, service: str, method: str, params: dict, *, timeout: float = 60.0) -> dict:
    """POST в сервис API v5; ошибка запроса целиком — YandexError, результат — payload["result"]."""
    from app.yandex import YandexError, direct_api_error

    if is_sandbox(token):
        raise YandexError("sandbox", SANDBOX_CLOSED)
    # get идемпотентен. add/update при 504 могли уже пройти на стороне Директа — не повторяем.
    try:
        response = post_json(
            v5_url(token, service),
            headers=headers(token, client_login),
            body={"method": method, "params": params},
            timeout=timeout,
            attempts=3 if method == "get" else 1,
        )
    except httpx.HTTPError as exc:
        raise YandexError("direct") from exc
    _remember_units(client_login, response)
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if response.status_code >= 400 or payload.get("error"):
        err = payload.get("error") or {}
        log.warning("%s.%s %s %s", service, method, response.status_code, err or payload)
        raise direct_api_error(payload, response.status_code)
    return payload.get("result") or {}


def get_all(token, client_login: str | None, service: str, params: dict, key: str) -> list[dict]:
    """get со всеми страницами: Директ ставит LimitedBy, если ответ обрезан лимитом страницы."""
    items: list[dict] = []
    offset = 0
    while True:
        result = call(token, client_login, service, "get", {**params, "Page": {"Limit": PAGE_LIMIT, "Offset": offset}})
        items.extend(result.get(key) or [])
        limited = result.get("LimitedBy")
        if not limited or int(limited) <= offset:
            return items
        offset = int(limited)


def item_errors(result: dict, key: str) -> list[tuple[int, str]]:
    """Ошибки отдельных объектов пакетного ответа: [(индекс, сообщение)]."""
    out = []
    for index, item in enumerate(result.get(key) or []):
        for err in item.get("Errors") or []:
            message = str(err.get("Message") or "").strip()
            details = str(err.get("Details") or "").strip()
            out.append((index, f"{message}: {details}" if message and details else message or details or "Ошибка Директа"))
            break
    return out


def item_warnings(result: dict, key: str) -> list[tuple[int, str]]:
    out = []
    for index, item in enumerate(result.get(key) or []):
        for warn in item.get("Warnings") or []:
            text = str(warn.get("Details") or warn.get("Message") or "").strip()
            if text:
                out.append((index, text))
    return out


def chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]
