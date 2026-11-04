"""Мост Cloudflare AI Gateway: доступ к зарубежным моделям с сервера в РФ.

OpenAI, Anthropic и Google не принимают запросы с российских IP. Шлюз Cloudflare принимает запрос от нас и
отправляет его провайдеру со своих адресов. Ключ провайдера остаётся ключом пользователя (Authorization),
токен шлюза — отдельный заголовок cf-aig-authorization.

- Чат идёт в OpenAI-совместимый /compat/chat/completions: модель — «провайдер/модель» (anthropic/claude-…),
  шлюз сам переводит запрос в формат провайдера. Модель без префикса дополняем по имени (claude-… → anthropic/).
- Картинки идут в провайдерский путь …/openai: /images/generations как у OpenAI.

Мост работает только для провайдеров «OpenAI-совместимый» — YandexGPT и YandexART доступны напрямую.
"""

from __future__ import annotations

import re

from app.db import pool
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

GATEWAY_HOST = "https://gateway.ai.cloudflare.com/v1"
TOKEN_HEADER = "cf-aig-authorization"
# Account ID — 32 hex-символа; имя шлюза — латиница, цифры, дефис и подчёркивание, до 64 символов.
_ACCOUNT_RE = re.compile(r"^[0-9a-f]{32}$")
_GATEWAY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Префиксы провайдеров в compat-эндпоинте по началу имени модели.
_MODEL_PREFIXES = (
    ("claude", "anthropic"),
    ("gemini", "google-ai-studio"),
    ("grok", "grok"),
    ("mistral", "mistral"),
    ("deepseek", "deepseek"),
)
_KEEP = object()


def _row(user_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM ai_bridge WHERE user_id = %s", (user_id,)).fetchone()


def _ready(row: dict | None) -> bool:
    row = row or {}
    return bool(row.get("enabled") and row.get("account_id") and row.get("gateway_id"))


def _public(row: dict | None) -> dict:
    row = row or {}
    token_hint = ""
    if row.get("token_enc"):
        try:
            plain = decrypt_token(row["token_enc"])
            token_hint = f"…{plain[-4:]}" if len(plain) > 8 else "…"
        except RuntimeError:
            token_hint = ""
    return {
        "enabled": bool(row.get("enabled")),
        "active": _ready(row),
        "account_id": row.get("account_id") or "",
        "gateway_id": row.get("gateway_id") or "",
        "has_token": bool(row.get("token_enc")),
        "token_hint": token_hint,
        "use_chat": row.get("use_chat", True) is not False,
        "use_images": row.get("use_images", True) is not False,
        "chat_url": _base(row, "compat") if _ready(row) else "",
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def get_settings(user_id: int) -> dict:
    return _public(_row(user_id))


def save_settings(
    user_id: int,
    *,
    enabled: bool = False,
    account_id: str = "",
    gateway_id: str = "",
    token: str | None = None,
    use_chat: bool = True,
    use_images: bool = True,
) -> dict:
    """token: None — оставить прежний, "" — удалить. Включить можно только с Account ID и именем шлюза."""
    account_id = (account_id or "").strip().lower()
    gateway_id = (gateway_id or "").strip()
    if account_id and not _ACCOUNT_RE.match(account_id):
        raise YandexError("bridge-account", "Account ID Cloudflare — 32 символа из цифр и букв a–f")
    if gateway_id and not _GATEWAY_RE.match(gateway_id):
        raise YandexError("bridge-gateway", "Имя шлюза — латиница, цифры, дефис и подчёркивание, до 64 символов")
    if enabled and not (account_id and gateway_id):
        raise YandexError("bridge-incomplete", "Чтобы включить мост, укажите Account ID и имя шлюза (Gateway ID)")
    current = _row(user_id) or {}
    if token is None:
        token_enc = current.get("token_enc") or ""
    else:
        token_enc = encrypt_token(token.strip()) if token.strip() else ""
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO ai_bridge (user_id, enabled, account_id, gateway_id, token_enc, use_chat, use_images, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                enabled = EXCLUDED.enabled, account_id = EXCLUDED.account_id, gateway_id = EXCLUDED.gateway_id,
                token_enc = EXCLUDED.token_enc, use_chat = EXCLUDED.use_chat, use_images = EXCLUDED.use_images,
                updated_at = NOW()
            """,
            (user_id, bool(enabled), account_id, gateway_id, token_enc, bool(use_chat), bool(use_images)),
        )
    return get_settings(user_id)


def _base(row: dict, path: str) -> str:
    return f"{GATEWAY_HOST}/{row['account_id']}/{row['gateway_id']}/{path}"


def _headers(row: dict) -> dict:
    if not row.get("token_enc"):
        return {}
    try:
        return {TOKEN_HEADER: f"Bearer {decrypt_token(row['token_enc'])}"}
    except RuntimeError as exc:
        raise YandexError("bridge-token", "Токен моста Cloudflare не расшифровывается — сохраните его заново") from exc


def gateway_model(model: str) -> str:
    """Модель для compat-эндпоинта: «провайдер/модель». Уже с префиксом — как есть, иначе по имени, по умолчанию openai."""
    model = (model or "").strip()
    if "/" in model:
        return model
    lower = model.lower()
    for start, provider in _MODEL_PREFIXES:
        if lower.startswith(start):
            return f"{provider}/{model}"
    return f"openai/{model}"


def target(user_id: int, use: str) -> dict | None:
    """Куда слать запрос через мост: {"base_url", "headers"}; None — мост выключен или не для этой задачи.

    use: "chat" — compat chat/completions, "images" — провайдерский путь OpenAI.
    """
    row = _row(user_id)
    if not _ready(row) or not row.get("use_chat" if use == "chat" else "use_images"):
        return None
    return {"base_url": _base(row, "compat" if use == "chat" else "openai"), "headers": _headers(row)}
