"""Генерация картинок для объявлений по API пользователя (BYOK).

Оба провайдера говорят на OpenAI-совместимом POST /images/generations:
- OpenAI и совместимые (gpt-image-1, dall-e-3) — Bearer-ключ, в ответе b64_json или url;
- YandexART в Yandex AI Studio — https://ai.api.cloud.yandex.net/v1, заголовки Authorization: Api-Key и
  OpenAI-Project: <каталог>, модель art://<каталог>/aliceai-image-art-3.0, промпт до 500 символов.
  У сервисного аккаунта нужна роль ai.imageGeneration.user.

Квадрат 1024×1024 подходит Директу (1:1, от 450 px, до 5 МБ). Готовую картинку загружают в библиотеку
кабинета (adimages) — в объявление идёт её хэш.
Каждая картинка пишется в ai_usage (задача images, units = 1, стоимость — цена из настроек) и укладывается
в месячный лимит ИИ из Настройки → ИИ-ассистент.
"""

from __future__ import annotations

import base64
import logging
import math
from concurrent.futures import ThreadPoolExecutor

import httpx

from app import ai
from app.ad_extras import IMAGE_MAX_BYTES, IMAGE_TYPES
from app.db import pool
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

log = logging.getLogger("app.image_gen")

OPENAI_BASE = ai.OPENAI_BASE
YANDEX_BASE = "https://ai.api.cloud.yandex.net/v1"
PROVIDERS = {
    "openai": {"label": "OpenAI-совместимый", "default_model": "gpt-image-1"},
    "yandexart": {"label": "YandexART (Yandex AI Studio)", "default_model": "aliceai-image-art-3.0"},
}
SIZE = "1024x1024"
PROMPT_MAX = {"openai": 4000, "yandexart": 500}
COUNT_MAX = 4
TIMEOUT_SECONDS = 180
PRICE_MAX = 10_000
FOLDER_MAX_LEN = 50
CHECK_PROMPT = "Чашка кофе на светлом деревянном столе, мягкий дневной свет, минимализм"

NOT_CONFIGURED = "Генерация картинок не подключена: Настройки → Генерация картинок"
_KEEP = object()


# --- Подключение пользователя -------------------------------------------------------------------


def _row(user_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM image_gen_settings WHERE user_id = %s", (user_id,)).fetchone()


def _configured(row: dict | None) -> bool:
    row = row or {}
    return bool(row.get("api_key_enc")) and (row.get("provider") != "yandexart" or bool(row.get("folder_id")))


def _public(row: dict | None) -> dict:
    row = row or {}
    return {
        "configured": _configured(row),
        "provider": row.get("provider") or "openai",
        "base_url": row.get("base_url") or "",
        "model": row.get("model") or "",
        "folder_id": row.get("folder_id") or "",
        "price_rub": ai._num(row.get("price_rub")),
        "has_key": bool(row.get("api_key_enc")),
        "key_hint": ai._key_hint(row.get("api_key_enc") or ""),
        "checked_at": row["checked_at"].isoformat() if row.get("checked_at") else None,
        "check_error": row.get("check_error") or "",
        "providers": [{"id": k, "label": v["label"], "default_model": v["default_model"]} for k, v in PROVIDERS.items()],
        "default_base_url": OPENAI_BASE,
        "size": SIZE,
        "count_max": COUNT_MAX,
    }


def get_settings(user_id: int) -> dict:
    return _public(_row(user_id))


def is_configured(user_id: int) -> bool:
    return _configured(_row(user_id))


def save_settings(
    user_id: int,
    *,
    provider: str = "openai",
    base_url: str = "",
    model: str = "",
    folder_id: str = "",
    api_key: str | None = None,
    price_rub=_KEEP,
) -> dict:
    """api_key: None — оставить прежний, "" — удалить; price_rub не передана — как была.

    Новый ключ, провайдер, адрес, модель или каталог сбрасывают итог проверки.
    """
    provider = (provider or "openai").strip().lower()
    if provider not in PROVIDERS:
        raise YandexError("image-provider", "Неизвестный провайдер генерации картинок")
    base_url = ai._check_url_syntax(base_url) if provider == "openai" else ""
    model = (model or "").strip()[:200]
    folder_id = (folder_id or "").strip()
    if len(folder_id) > FOLDER_MAX_LEN or any(ch.isspace() for ch in folder_id):
        raise YandexError("image-folder", "id каталога Yandex Cloud — без пробелов, до 50 символов (b1g…)")
    if provider == "yandexart" and not folder_id:
        raise YandexError("image-folder", "Для YandexART укажите id каталога Yandex Cloud, в котором создан ключ")
    if provider != "yandexart":
        folder_id = ""
    current = _row(user_id) or {}
    if api_key is None:
        key_enc = current.get("api_key_enc") or ""
    else:
        key_enc = encrypt_token(api_key.strip()) if api_key.strip() else ""
    if price_rub is _KEEP:
        price = ai._num(current.get("price_rub"))
    else:
        price = ai._money(price_rub, "Цена картинки")
        if price is not None and price > PRICE_MAX:
            raise YandexError("ai-price", f"Цена картинки — до {PRICE_MAX:,} ₽".replace(",", " "))
    changed = any(
        new != (current.get(name) or "")
        for name, new in (
            ("provider", provider),
            ("base_url", base_url),
            ("model", model),
            ("folder_id", folder_id),
            ("api_key_enc", key_enc),
        )
    )
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO image_gen_settings (user_id, provider, base_url, model, folder_id, api_key_enc, price_rub)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                provider = EXCLUDED.provider, base_url = EXCLUDED.base_url, model = EXCLUDED.model,
                folder_id = EXCLUDED.folder_id, api_key_enc = EXCLUDED.api_key_enc, price_rub = EXCLUDED.price_rub,
                checked_at = CASE WHEN %s THEN NULL ELSE image_gen_settings.checked_at END,
                check_error = CASE WHEN %s THEN '' ELSE image_gen_settings.check_error END,
                updated_at = NOW()
            """,
            (user_id, provider, base_url, model, folder_id, key_enc, price, changed, changed),
        )
    return get_settings(user_id)


def load_config(user_id: int, workspace_id: int | None = None) -> dict:
    """Готовая конфигурация вызова: адрес, модель, заголовки, цена картинки и месячный лимит ИИ."""
    row = _row(user_id)
    if not _configured(row):
        raise YandexError("image-not-configured", NOT_CONFIGURED)
    try:
        key = decrypt_token(row["api_key_enc"])
    except RuntimeError as exc:
        raise YandexError("image-not-configured", "Ключ генерации картинок не расшифровывается — сохраните его заново") from exc
    provider = row["provider"]
    model = row.get("model") or PROVIDERS[provider]["default_model"]
    model_name = model
    if provider == "yandexart":
        folder = row["folder_id"]
        base = YANDEX_BASE
        if not model.startswith("art://"):
            model = f"art://{folder}/{model.lstrip('/')}"
        headers = {"Authorization": f"Api-Key {key}", "OpenAI-Project": folder}
    else:
        base = row.get("base_url") or OPENAI_BASE
        headers = {"Authorization": f"Bearer {key}"}
    settings_row = ai._row(user_id) or {}
    return {
        "provider": provider,
        "base_url": base,
        "model": model,
        "model_name": model_name,
        "headers": {**headers, "Content-Type": "application/json"},
        "check_host": base not in (OPENAI_BASE, YANDEX_BASE),
        "user_id": user_id,
        "workspace_id": workspace_id,
        "task": "images",
        "price": ai._num(row.get("price_rub")),
        "limit_rub": ai._num(settings_row.get("monthly_limit_rub")),
    }


# --- Генерация ----------------------------------------------------------------------------------


def _post(cfg: dict, body: dict) -> httpx.Response:
    return httpx.post(
        f"{cfg['base_url']}/images/generations",
        json=body,
        headers=cfg["headers"],
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
    )


def _download(url: str) -> bytes:
    """Картинка по адресу из ответа (dall-e отдаёт ссылку): только публичный https и не больше лимита Директа."""
    url = ai._check_url_syntax(url)
    ai._check_url_resolves_public(url)
    with httpx.stream("GET", url, timeout=60, follow_redirects=False) as response:
        if response.status_code >= 400:
            raise YandexError("image-error", f"Не удалось скачать картинку ({response.status_code})")
        data = b""
        for chunk in response.iter_bytes():
            data += chunk
            if len(data) > IMAGE_MAX_BYTES:
                raise YandexError("image-error", "Картинка больше 5 МБ — Директ её не примет")
    return data


def _request(cfg: dict, prompt: str) -> bytes:
    body: dict = {"model": cfg["model"], "prompt": prompt, "n": 1, "size": SIZE}
    if cfg["provider"] == "openai" and str(cfg["model_name"]).startswith("gpt-image"):
        # PNG 1024×1024 у gpt-image бывает больше 5 МБ — лимита Директа; JPEG заметно меньше.
        body["output_format"] = "jpeg"
    if cfg.get("check_host"):
        ai._check_url_resolves_public(cfg["base_url"])
    try:
        response = _post(cfg, body)
    except httpx.TimeoutException as exc:
        raise YandexError("image-timeout", "Генератор картинок не ответил за отведённое время") from exc
    except httpx.HTTPError as exc:
        raise YandexError("image-network", f"Не удалось связаться с генератором: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise YandexError("image-auth", f"Генератор картинок отклонил ключ: {ai._error_text(response)}")
    if response.status_code == 429:
        raise YandexError("image-limit", f"Лимит запросов к генератору: {ai._error_text(response)}")
    if response.status_code >= 400:
        raise YandexError("image-error", f"Ошибка генератора ({response.status_code}): {ai._error_text(response)}")
    try:
        item = response.json()["data"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise YandexError("image-error", "Непонятный ответ генератора картинок") from exc
    if item.get("b64_json"):
        try:
            data = base64.b64decode(item["b64_json"])
        except ValueError as exc:
            raise YandexError("image-error", "Генератор вернул повреждённую картинку") from exc
    elif item.get("url"):
        data = _download(str(item["url"]))
    else:
        raise YandexError("image-error", "Генератор не вернул картинку")
    if len(data) > IMAGE_MAX_BYTES:
        raise YandexError("image-error", "Картинка больше 5 МБ — Директ её не примет")
    if not data.startswith(IMAGE_TYPES):
        raise YandexError("image-error", "Генератор вернул не JPG/PNG/GIF — Директ такую не примет")
    return data


def _record(cfg: dict, prompt: str) -> None:
    usage = {
        "tokens_in": 0,
        "tokens_out": 0,
        "chars_in": len(prompt),
        "chars_out": 0,
        "estimated": False,
        "cost_rub": cfg.get("price"),
    }
    ai._record({**cfg, "units": 1}, usage)


def clean_prompt(prompt: str, provider: str = "openai") -> str:
    text = " ".join(str(prompt or "").split())
    if not text:
        raise YandexError("image-prompt", "Опишите, что должно быть на картинке")
    return text[: PROMPT_MAX.get(provider, 500)]


def generate(user_id: int, prompt: str, *, count: int = 1, workspace_id: int | None = None) -> dict:
    """count картинок по одному описанию (вызовы параллельно, n=1 — так умеют все модели).

    → {"images": [bytes…], "errors": [текст…], "model"}. Ошибка, если не вышло ни одной.
    """
    cfg = load_config(user_id, workspace_id)
    prompt = clean_prompt(prompt, cfg["provider"])
    count = max(1, min(int(count or 1), COUNT_MAX))
    ai.check_budget(cfg)
    left = ai.budget_left(cfg)
    if left is not None and cfg.get("price"):
        count = min(count, max(1, math.floor(left / cfg["price"])))
    with ThreadPoolExecutor(max_workers=count) as pool_:
        futures = [pool_.submit(_request, cfg, prompt) for _ in range(count)]
    images: list[bytes] = []
    errors: list[YandexError] = []
    for future in futures:
        try:
            images.append(future.result())
        except YandexError as exc:
            errors.append(exc)
    # Учёт в основном потоке: meter() чата живёт в его контексте.
    for _ in images:
        _record(cfg, prompt)
    if not images:
        raise errors[0]
    return {"images": images, "errors": [e.message or e.code for e in errors], "model": cfg["model_name"]}


def to_data_url(data: bytes) -> str:
    mime = "image/png" if data.startswith(b"\x89PNG") else "image/gif" if data.startswith(b"GIF8") else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def upload(token, client_login: str, images: list[bytes], *, name: str) -> tuple[list[dict], list[str]]:
    """Сгенерированные картинки → библиотека кабинета: ([{hash, preview_url}], отказы Директа).

    Картинка уже оплачена — отказ по одной не должен стоить остальных."""
    from app.ad_extras import upload_image

    out: list[dict] = []
    errors: list[str] = []
    for i, data in enumerate(images, 1):
        label = f"{name[:200]} {i}" if len(images) > 1 else name[:200]
        try:
            out.append(upload_image(token, client_login, name=label or "ai-image", data_b64=base64.b64encode(data).decode("ascii")))
        except YandexError as exc:
            errors.append(exc.message or exc.code)
    return out, errors


def _remember_check(user_id: int, error: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE image_gen_settings SET checked_at = NOW(), check_error = %s WHERE user_id = %s",
            (error[:500], user_id),
        )


def check_connection(user_id: int) -> dict:
    """Одна настоящая картинка (стоит как обычная); итог запоминается, картинка — в ответе для показа."""
    try:
        result = generate(user_id, CHECK_PROMPT, count=1)
    except YandexError as exc:
        if exc.code != "image-not-configured":
            _remember_check(user_id, exc.message or exc.code)
        raise
    _remember_check(user_id, "")
    data = result["images"][0]
    return {"ok": True, "model": result["model"], "bytes": len(data), "image": to_data_url(data)}
