"""Мост Cloudflare: доступ к зарубежным моделям с сервера в РФ.

OpenAI, Anthropic и Google не принимают запросы с российских IP. Мост — посредник в Cloudflare, который
отправляет запрос провайдеру со своих адресов. Ключ провайдера остаётся ключом пользователя (Authorization).

Два вида моста (kind):
- worker — свой Cloudflare Worker, закреплённый в регионе США (placement). Основной: Anthropic отвечает
  403 «Request not allowed», если запрос пришёл из дата-центра Cloudflare рядом с РФ, а AI Gateway выходит
  именно из ближайшего. Worker пересылает /anthropic/… /openai/… /google/… в OpenAI-совместимые эндпоинты
  провайдеров; пускает по секрету в заголовке x-bridge-token. Разворачиваем его сами по API Cloudflare.
- gateway — Cloudflare AI Gateway: чат в /compat/chat/completions (модель «провайдер/модель»), картинки
  в …/openai; токен шлюза — заголовок cf-aig-authorization.

Мост работает только для провайдеров «OpenAI-совместимый» — YandexGPT и YandexART доступны напрямую.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

import httpx

from app.db import pool
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

GATEWAY_HOST = "https://gateway.ai.cloudflare.com/v1"
CF_API = "https://api.cloudflare.com/client/v4"
TOKEN_HEADER = "cf-aig-authorization"
WORKER_TOKEN_HEADER = "x-bridge-token"
KINDS = ("worker", "gateway")
WORKER_NAME = "aimarketing-bridge"
# Регион, где запускается Worker: оттуда он и ходит к провайдеру. Восток США — все провайдеры работают.
WORKER_REGION = "aws:us-east-1"
WORKER_COMPAT_DATE = "2025-09-01"
WORKER_SCRIPT = (Path(__file__).with_name("bridge_worker.js")).read_text(encoding="utf-8")
CF_TIMEOUT = 30
# Account ID — 32 hex-символа; имя шлюза — латиница, цифры, дефис и подчёркивание, до 64 символов.
_ACCOUNT_RE = re.compile(r"^[0-9a-f]{32}$")
_GATEWAY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Провайдер по началу имени модели: префикс в compat-эндпоинте AI Gateway и маршрут в Worker'е.
_MODEL_PREFIXES = (
    ("claude", "anthropic"),
    ("gemini", "google-ai-studio"),
    ("grok", "grok"),
    ("mistral", "mistral"),
    ("deepseek", "deepseek"),
)
# Маршруты Worker'а: OpenAI-совместимый базовый путь провайдера.
_WORKER_ROUTES = {
    "anthropic": "anthropic/v1",
    "google-ai-studio": "google/v1beta/openai",
    "openai": "openai/v1",
}


def _row(user_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM ai_bridge WHERE user_id = %s", (user_id,)).fetchone()


def _kind(row: dict) -> str:
    return row.get("kind") if row.get("kind") in KINDS else "gateway"


def _ready(row: dict | None) -> bool:
    row = row or {}
    if not row.get("enabled"):
        return False
    if _kind(row) == "worker":
        return bool(row.get("worker_url") and row.get("worker_token_enc"))
    return bool(row.get("account_id") and row.get("gateway_id"))


def _hint(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        plain = decrypt_token(cipher)
    except RuntimeError:
        return ""
    return f"…{plain[-4:]}" if len(plain) > 8 else "…"


def _public(row: dict | None) -> dict:
    row = row or {}
    return {
        "enabled": bool(row.get("enabled")),
        "active": _ready(row),
        "kind": _kind(row) if row else "worker",
        "account_id": row.get("account_id") or "",
        "gateway_id": row.get("gateway_id") or "",
        "has_token": bool(row.get("token_enc")),
        "token_hint": _hint(row.get("token_enc") or ""),
        "worker_url": row.get("worker_url") or "",
        "has_worker_token": bool(row.get("worker_token_enc")),
        "worker_token_hint": _hint(row.get("worker_token_enc") or ""),
        "use_chat": row.get("use_chat", True) is not False,
        "use_images": row.get("use_images", True) is not False,
        "chat_url": _base(row, "compat") if row.get("account_id") and row.get("gateway_id") else "",
        "worker_name": WORKER_NAME,
        "worker_region": WORKER_REGION,
        "worker_script": WORKER_SCRIPT,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def get_settings(user_id: int) -> dict:
    return _public(_row(user_id))


def _check_account(account_id: str) -> str:
    account_id = (account_id or "").strip().lower()
    if account_id and not _ACCOUNT_RE.match(account_id):
        raise YandexError("bridge-account", "Account ID Cloudflare — 32 символа из цифр и букв a–f")
    return account_id


def _check_worker_url(url: str) -> str:
    # ai импортирует этот модуль — проверка адреса нужна только при сохранении.
    from app import ai

    url = (url or "").strip().rstrip("/")
    return ai._check_url_syntax(url) if url else ""


def _secret(value: str | None, current: str) -> str:
    if value is None:
        return current or ""
    return encrypt_token(value.strip()) if value.strip() else ""


def save_settings(
    user_id: int,
    *,
    enabled: bool = False,
    kind: str = "worker",
    account_id: str = "",
    gateway_id: str = "",
    token: str | None = None,
    worker_url: str = "",
    worker_token: str | None = None,
    use_chat: bool = True,
    use_images: bool = True,
) -> dict:
    """token, worker_token: None — оставить прежний, "" — удалить. Включить можно только заполненный мост."""
    kind = kind if kind in KINDS else "worker"
    account_id = _check_account(account_id)
    gateway_id = (gateway_id or "").strip()
    if gateway_id and not _GATEWAY_RE.match(gateway_id):
        raise YandexError("bridge-gateway", "Имя шлюза — латиница, цифры, дефис и подчёркивание, до 64 символов")
    worker_url = _check_worker_url(worker_url)
    current = _row(user_id) or {}
    token_enc = _secret(token, current.get("token_enc"))
    worker_token_enc = _secret(worker_token, current.get("worker_token_enc"))
    if enabled and kind == "gateway" and not (account_id and gateway_id):
        raise YandexError("bridge-incomplete", "Чтобы включить мост, укажите Account ID и имя шлюза (Gateway ID)")
    if enabled and kind == "worker" and not (worker_url and worker_token_enc):
        raise YandexError("bridge-incomplete", "Чтобы включить мост, разверните Worker или укажите его адрес и секрет")
    _store(user_id, enabled, kind, account_id, gateway_id, token_enc, worker_url, worker_token_enc, use_chat, use_images)
    return get_settings(user_id)


def _store(user_id, enabled, kind, account_id, gateway_id, token_enc, worker_url, worker_token_enc, use_chat, use_images):
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO ai_bridge (user_id, enabled, kind, account_id, gateway_id, token_enc, worker_url,
                                   worker_token_enc, use_chat, use_images, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                enabled = EXCLUDED.enabled, kind = EXCLUDED.kind, account_id = EXCLUDED.account_id,
                gateway_id = EXCLUDED.gateway_id, token_enc = EXCLUDED.token_enc, worker_url = EXCLUDED.worker_url,
                worker_token_enc = EXCLUDED.worker_token_enc, use_chat = EXCLUDED.use_chat,
                use_images = EXCLUDED.use_images, updated_at = NOW()
            """,
            (
                user_id,
                bool(enabled),
                kind,
                account_id,
                gateway_id,
                token_enc,
                worker_url,
                worker_token_enc,
                bool(use_chat),
                bool(use_images),
            ),
        )


def _base(row: dict, path: str) -> str:
    return f"{GATEWAY_HOST}/{row['account_id']}/{row['gateway_id']}/{path}"


def _decrypt(cipher: str, what: str) -> str:
    try:
        return decrypt_token(cipher)
    except RuntimeError as exc:
        raise YandexError("bridge-token", f"{what} моста Cloudflare не расшифровывается — сохраните его заново") from exc


def model_provider(model: str) -> str:
    """Провайдер модели в терминах AI Gateway: anthropic, google-ai-studio, openai…"""
    model = (model or "").strip()
    if "/" in model:
        return model.split("/", 1)[0]
    lower = model.lower()
    for start, provider in _MODEL_PREFIXES:
        if lower.startswith(start):
            return provider
    return "openai"


def gateway_model(model: str) -> str:
    """Модель для compat-эндпоинта: «провайдер/модель». Уже с префиксом — как есть, иначе по имени, по умолчанию openai."""
    model = (model or "").strip()
    return model if "/" in model else f"{model_provider(model)}/{model}"


def target(user_id: int, use: str, model: str = "") -> dict | None:
    """Куда слать запрос через мост: {"base_url", "headers", "model", "anthropic"}; None — мост не для этой задачи.

    use: "chat" — chat/completions, "images" — /images/generations OpenAI. anthropic — модель Claude: её
    OpenAI-совместимый слой отвергает temperature ниже 1, её не передаём.
    """
    row = _row(user_id)
    if not _ready(row) or not row.get("use_chat" if use == "chat" else "use_images"):
        return None
    provider = model_provider(model) if use == "chat" else "openai"
    if _kind(row) == "worker":
        route = _WORKER_ROUTES.get(provider)
        if route is None:
            raise YandexError(
                "bridge-provider",
                f"Мост через Worker умеет Anthropic, OpenAI и Google; «{provider}» — через AI Gateway или напрямую",
            )
        bare = model.split("/", 1)[1] if "/" in model else model
        return {
            "base_url": f"{row['worker_url']}/{route}",
            "headers": {WORKER_TOKEN_HEADER: _decrypt(row["worker_token_enc"], "Секрет Worker'а")},
            "model": bare,
            "anthropic": provider == "anthropic",
        }
    headers = {TOKEN_HEADER: f"Bearer {_decrypt(row['token_enc'], 'Токен шлюза')}"} if row.get("token_enc") else {}
    return {
        "base_url": _base(row, "compat" if use == "chat" else "openai"),
        "headers": headers,
        "model": gateway_model(model) if use == "chat" else model,
        "anthropic": provider == "anthropic",
    }


# --- Worker: развёртывание по API Cloudflare и проверка ------------------------------------------


def _cf_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    errors = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errors, list) and errors:
        return "; ".join(f"{e.get('message')} ({e.get('code')})" if isinstance(e, dict) else str(e) for e in errors)[:400]
    return f"HTTP {response.status_code}"


def _cf(method: str, path: str, api_token: str, **kwargs) -> dict:
    try:
        response = httpx.request(
            method,
            f"{CF_API}{path}",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=CF_TIMEOUT,
            follow_redirects=False,
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise YandexError("bridge-network", f"Не удалось связаться с API Cloudflare: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise YandexError(
            "bridge-cf-auth",
            f"Cloudflare отклонил API-токен: {_cf_error(response)}. Нужен токен по шаблону «Edit Cloudflare Workers»",
        )
    if response.status_code >= 400:
        raise YandexError("bridge-cf", f"Ошибка API Cloudflare ({response.status_code}): {_cf_error(response)}")
    try:
        return response.json().get("result") or {}
    except ValueError:
        return {}


def deploy_worker(user_id: int, *, account_id: str, api_token: str) -> dict:
    """Загружает Worker моста в аккаунт пользователя, включает адрес workers.dev и включает мост.

    API-токен Cloudflare нужен только на время развёртывания и не сохраняется.
    """
    account_id = _check_account(account_id)
    api_token = (api_token or "").strip()
    if not account_id or not api_token:
        raise YandexError("bridge-incomplete", "Для развёртывания укажите Account ID и API-токен Cloudflare")
    current = _row(user_id) or {}
    secret = secrets.token_urlsafe(32)
    metadata = {
        "main_module": "worker.js",
        "compatibility_date": WORKER_COMPAT_DATE,
        "bindings": [{"type": "secret_text", "name": "BRIDGE_TOKEN", "text": secret}],
        "placement": {"region": WORKER_REGION},
    }
    base = f"/accounts/{account_id}/workers"
    _cf(
        "PUT",
        f"{base}/scripts/{WORKER_NAME}",
        api_token,
        files={
            "metadata": (None, json.dumps(metadata), "application/json"),
            "worker.js": ("worker.js", WORKER_SCRIPT.encode("utf-8"), "application/javascript+module"),
        },
    )
    _cf("POST", f"{base}/scripts/{WORKER_NAME}/subdomain", api_token, json={"enabled": True, "previews_enabled": False})
    subdomain = (_cf("GET", f"{base}/subdomain", api_token) or {}).get("subdomain")
    if not subdomain:
        raise YandexError(
            "bridge-subdomain",
            "У аккаунта нет адреса workers.dev: откройте в Cloudflare Compute → Workers & Pages, "
            "чтобы он создался, и нажмите «Развернуть» ещё раз",
        )
    _store(
        user_id,
        True,
        "worker",
        account_id,
        current.get("gateway_id") or "",
        current.get("token_enc") or "",
        f"https://{WORKER_NAME}.{subdomain}.workers.dev",
        encrypt_token(secret),
        current.get("use_chat", True) is not False,
        current.get("use_images", True) is not False,
    )
    return get_settings(user_id)


def check_worker(user_id: int) -> dict:
    """Откуда Worker выходит в интернет: {"ok", "country", "colo"} по /cdn-cgi/trace из самого Worker'а."""
    row = _row(user_id) or {}
    if not (row.get("worker_url") and row.get("worker_token_enc")):
        raise YandexError("bridge-incomplete", "Worker не настроен: разверните его или укажите адрес и секрет")
    secret = _decrypt(row["worker_token_enc"], "Секрет Worker'а")
    try:
        response = httpx.get(
            f"{row['worker_url']}/trace", headers={WORKER_TOKEN_HEADER: secret}, timeout=CF_TIMEOUT, follow_redirects=False
        )
    except httpx.TimeoutException as exc:
        raise YandexError("bridge-network", "Worker не ответил: соединение с Cloudflare зависло или режется") from exc
    except httpx.HTTPError as exc:
        raise YandexError("bridge-network", f"Не удалось связаться с Worker'ом: {exc.__class__.__name__}") from exc
    if response.status_code == 401:
        raise YandexError("bridge-worker-auth", "Worker не принял секрет: он отличается от BRIDGE_TOKEN в Cloudflare")
    if response.status_code == 404 and "workers.dev" in row["worker_url"]:
        raise YandexError("bridge-worker", "Worker не найден по адресу — через минуту после развёртывания попробуйте ещё раз")
    if response.status_code >= 400:
        raise YandexError("bridge-worker", f"Worker ответил ошибкой {response.status_code}: {response.text[:200]}")
    trace = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line)
    country = trace.get("loc", "")
    return {"ok": True, "country": country, "colo": trace.get("colo", ""), "blocked": country in ("RU", "BY", "CN", "HK")}
