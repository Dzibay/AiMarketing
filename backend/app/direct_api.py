"""Общий клиент API Директа: боевой и песочница.

Песочница — тот же OAuth-токен, но другие адреса API. Чтобы не протаскивать флаг через
все вызовы, токен подключения-песочницы — это строка с признаком ``sandbox``: модули
просто передают его дальше, а адрес выбирает ``v5_url``.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("app.direct_api")

V5 = "https://api.direct.yandex.com/json/v5/"
V5_SANDBOX = "https://api-sandbox.direct.yandex.com/json/v5/"
LIVE4 = "https://api.direct.yandex.ru/live/v4/json/"
LIVE4_SANDBOX = "https://api-sandbox.direct.yandex.ru/live/v4/json/"


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


def call(token, client_login: str | None, service: str, method: str, params: dict, *, timeout: float = 60.0) -> dict:
    """POST в сервис API v5; ошибка запроса целиком — YandexError, результат — payload["result"]."""
    from app.yandex import YandexError, direct_api_error

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                v5_url(token, service),
                headers=headers(token, client_login),
                json={"method": method, "params": params},
            )
    except httpx.HTTPError as exc:
        raise YandexError("direct") from exc
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if response.status_code >= 400 or payload.get("error"):
        err = payload.get("error") or {}
        log.warning("%s.%s %s %s", service, method, response.status_code, err or payload)
        raise direct_api_error(payload, response.status_code)
    return payload.get("result") or {}


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
