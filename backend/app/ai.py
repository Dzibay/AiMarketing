"""ИИ пользователя (BYOK): настройки провайдера и вызов OpenAI-совместимого chat/completions.

Поддерживаются OpenAI и любые совместимые API (прокси, локальные шлюзы с публичным https-адресом)
и YandexGPT через его OpenAI-совместимый эндпоинт Yandex Cloud.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from urllib.parse import urlsplit

import httpx

from app.db import pool
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

log = logging.getLogger(__name__)

OPENAI_BASE = "https://api.openai.com/v1"
YANDEX_BASE = "https://llm.api.cloud.yandex.net/v1"
PROVIDERS = {
    "openai": {"label": "OpenAI-совместимый", "default_model": "gpt-4o-mini"},
    "yandexgpt": {"label": "YandexGPT", "default_model": "yandexgpt/latest"},
}
TIMEOUT_SECONDS = 90


def _key_hint(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        plain = decrypt_token(cipher)
    except RuntimeError:
        return ""
    return f"…{plain[-4:]}" if len(plain) > 8 else "…"


def _public(row: dict | None) -> dict:
    row = row or {}
    provider = row.get("provider") or "openai"
    has_key = bool(row.get("api_key_enc"))
    configured = has_key and (provider != "yandexgpt" or bool(row.get("folder_id")))
    return {
        "provider": provider,
        "base_url": row.get("base_url") or "",
        "model": row.get("model") or "",
        "folder_id": row.get("folder_id") or "",
        "temperature": float(row["temperature"]) if row.get("temperature") is not None else 0.3,
        "has_key": has_key,
        "key_hint": _key_hint(row.get("api_key_enc") or ""),
        "configured": configured,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "providers": [{"id": k, "label": v["label"], "default_model": v["default_model"]} for k, v in PROVIDERS.items()],
        "default_base_url": OPENAI_BASE,
    }


def _row(user_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM ai_settings WHERE user_id = %s", (user_id,)).fetchone()


def get_settings(user_id: int) -> dict:
    return _public(_row(user_id))


def _check_url_syntax(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise YandexError("ai-url", "Адрес API должен начинаться с https://")
    if parts.username or parts.password:
        raise YandexError("ai-url", "Адрес API без логина и пароля")
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise YandexError("ai-url", "Адрес API должен быть публичным")
    if parts.hostname == "localhost" or parts.hostname.endswith(".localhost"):
        raise YandexError("ai-url", "Адрес API должен быть публичным")
    return url


def _check_url_resolves_public(url: str) -> None:
    """Защита от SSRF: сервер не ходит по пользовательскому адресу во внутреннюю сеть."""
    parts = urlsplit(url)
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise YandexError("ai-url", f"Не удалось найти хост {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise YandexError("ai-url", "Адрес API указывает во внутреннюю сеть")


def save_settings(
    user_id: int,
    *,
    provider: str,
    base_url: str = "",
    model: str = "",
    folder_id: str = "",
    api_key: str | None = None,
    temperature: float | None = None,
) -> dict:
    """api_key: None — оставить прежний, "" — удалить."""
    provider = (provider or "openai").strip().lower()
    if provider not in PROVIDERS:
        raise YandexError("ai-provider", "Неизвестный провайдер ИИ")
    base_url = _check_url_syntax(base_url) if provider == "openai" else ""
    model = (model or "").strip()[:200]
    folder_id = (folder_id or "").strip()[:100]
    if provider == "yandexgpt" and not folder_id:
        raise YandexError("ai-folder", "Для YandexGPT укажите идентификатор каталога Yandex Cloud")
    try:
        temp = 0.3 if temperature is None else float(temperature)
    except (TypeError, ValueError) as exc:
        raise YandexError("ai-temperature", "Температура — число от 0 до 1") from exc
    temp = max(0.0, min(temp, 1.0))
    current = _row(user_id)
    if api_key is None:
        key_enc = (current or {}).get("api_key_enc") or ""
    else:
        key_enc = encrypt_token(api_key.strip()) if api_key.strip() else ""
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO ai_settings (user_id, provider, base_url, model, folder_id, api_key_enc, temperature, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                provider = EXCLUDED.provider, base_url = EXCLUDED.base_url, model = EXCLUDED.model,
                folder_id = EXCLUDED.folder_id, api_key_enc = EXCLUDED.api_key_enc,
                temperature = EXCLUDED.temperature, updated_at = NOW()
            """,
            (user_id, provider, base_url, model, folder_id, key_enc, temp),
        )
    return get_settings(user_id)


def load_config(user_id: int) -> dict:
    """Готовая конфигурация вызова: адрес, модель, заголовки. Ошибка, если ИИ не подключён."""
    row = _row(user_id)
    if not row or not row.get("api_key_enc"):
        raise YandexError("ai-not-configured", "Подключите ИИ: Настройки → ИИ-ассистент")
    try:
        key = decrypt_token(row["api_key_enc"])
    except RuntimeError as exc:
        raise YandexError("ai-not-configured", "Ключ ИИ не расшифровывается — сохраните его заново") from exc
    provider = row["provider"]
    model = row.get("model") or PROVIDERS[provider]["default_model"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "yandexgpt":
        folder = row.get("folder_id") or ""
        if not folder:
            raise YandexError("ai-not-configured", "Для YandexGPT укажите каталог Yandex Cloud")
        base = YANDEX_BASE
        if not model.startswith("gpt://"):
            model = f"gpt://{folder}/{model.lstrip('/')}"
        headers["OpenAI-Project"] = folder
    else:
        base = row.get("base_url") or OPENAI_BASE
    return {
        "provider": provider,
        "base_url": base,
        "model": model,
        "headers": headers,
        "temperature": float(row.get("temperature") or 0.3),
        "check_host": base != OPENAI_BASE,
    }


def _error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])[:300]
    return str(data)[:300]


def complete(cfg: dict, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Один вызов chat/completions → {"content": str, "tool_calls": [{"id", "name", "arguments"}]}."""
    if cfg.get("check_host"):
        _check_url_resolves_public(cfg["base_url"])
    body: dict = {"model": cfg["model"], "messages": messages, "temperature": cfg.get("temperature", 0.3)}
    if tools:
        body["tools"] = tools
    try:
        response = httpx.post(
            f"{cfg['base_url']}/chat/completions",
            json=body,
            headers=cfg["headers"],
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise YandexError("ai-timeout", "ИИ не ответил за отведённое время") from exc
    except httpx.HTTPError as exc:
        raise YandexError("ai-network", f"Не удалось связаться с ИИ: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise YandexError("ai-auth", f"Провайдер ИИ отклонил ключ: {_error_text(response)}")
    if response.status_code == 429:
        raise YandexError("ai-limit", f"Лимит запросов к ИИ: {_error_text(response)}")
    if response.status_code >= 400:
        raise YandexError("ai-error", f"Ошибка ИИ ({response.status_code}): {_error_text(response)}")
    try:
        data = response.json()
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise YandexError("ai-error", "Непонятный ответ ИИ") from exc
    calls = []
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False)
        calls.append({"id": call.get("id") or f"call_{i}", "name": fn.get("name") or "", "arguments": args})
    return {"content": message.get("content") or "", "tool_calls": calls}


def parse_json_reply(text: str):
    """JSON из ответа модели: без обёртки ```json и пояснений вокруг. None — не разобрать."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def complete_json(cfg: dict, messages: list[dict], *, retries: int = 1) -> dict:
    """Ответ строго объектом JSON; на неразборчивый ответ один раз просим исправиться."""
    history = list(messages)
    for attempt in range(retries + 1):
        reply = complete(cfg, history)
        data = parse_json_reply(reply["content"])
        if isinstance(data, dict):
            return data
        if attempt < retries:
            history += [
                {"role": "assistant", "content": reply["content"][:4000]},
                {"role": "user", "content": "Ответ не разобрался. Верни только JSON-объект по заданной схеме, без текста вокруг."},
            ]
    raise YandexError("ai-json", "ИИ вернул ответ не в том формате — попробуйте ещё раз или смените модель")


def check_connection(user_id: int) -> dict:
    cfg = load_config(user_id)
    reply = complete(cfg, [{"role": "user", "content": "Ответь одним словом: готов"}])
    return {"ok": True, "model": cfg["model"], "reply": (reply["content"] or "").strip()[:200]}
