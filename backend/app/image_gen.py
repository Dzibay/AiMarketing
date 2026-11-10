"""Генерация картинок для объявлений по общему ключу сервиса (настраивает администратор).

Оба провайдера говорят на OpenAI-совместимом POST /images/generations:
- OpenAI и совместимые (gpt-image-1, dall-e-3) — Bearer-ключ, в ответе b64_json или url;
- YandexART в Yandex AI Studio — https://ai.api.cloud.yandex.net/v1, заголовки Authorization: Api-Key и
  OpenAI-Project: <каталог>, модель art://<каталог>/aliceai-image-art-3.0, промпт до 500 символов.
  У сервисного аккаунта нужна роль ai.imageGeneration.user.

Квадрат 1024×1024 подходит Директу (1:1, от 450 px, до 5 МБ). Готовую картинку загружают в библиотеку
кабинета (adimages) — в объявление идёт её хэш.
Каждая картинка списывает единицы лимита пользователя по цене из админки и пишется в ai_usage
(вид image, себестоимость — из админки).
"""

from __future__ import annotations

import base64
import logging
import math
from concurrent.futures import ThreadPoolExecutor

import httpx

from app import ai, ai_bridge, billing, plans
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
FOLDER_MAX_LEN = 50
CHECK_PROMPT = "Чашка кофе на светлом деревянном столе, мягкий дневной свет, минимализм"

NOT_CONFIGURED = "Генерация картинок временно недоступна: администратор ещё не подключил её. Напишите в поддержку"
_KEEP = object()


# --- Подключение сервиса (админка) --------------------------------------------------------------


def _row() -> dict:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM service_images WHERE id = 1").fetchone() or {}


def _configured(row: dict | None) -> bool:
    row = row or {}
    return bool(row.get("api_key_enc")) and (row.get("provider") != "yandexart" or bool(row.get("folder_id")))


def get_settings() -> dict:
    row = _row()
    return {
        "configured": _configured(row),
        "provider": row.get("provider") or "yandexart",
        "base_url": row.get("base_url") or "",
        "model": row.get("model") or "",
        "folder_id": row.get("folder_id") or "",
        "units_per_image": ai._num(row.get("units_per_image")),
        "cost_per_image": ai._num(row.get("cost_per_image")),
        "has_key": bool(row.get("api_key_enc")),
        "key_hint": ai._key_hint(row.get("api_key_enc") or ""),
        "checked_at": row["checked_at"].isoformat() if row.get("checked_at") else None,
        "check_error": row.get("check_error") or "",
        "providers": [{"id": k, "label": v["label"], "default_model": v["default_model"]} for k, v in PROVIDERS.items()],
        "default_base_url": OPENAI_BASE,
        "size": SIZE,
        "count_max": COUNT_MAX,
    }


def is_configured() -> bool:
    return _configured(_row())


def save_settings(
    *,
    provider: str = "yandexart",
    base_url: str = "",
    model: str = "",
    folder_id: str = "",
    api_key: str | None = None,
    units_per_image=_KEEP,
    cost_per_image=_KEEP,
) -> dict:
    """api_key: None — оставить прежний, "" — удалить; цены не переданы — как были.

    Новый ключ, провайдер, адрес, модель или каталог сбрасывают итог проверки.
    """
    provider = (provider or "yandexart").strip().lower()
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
    current = _row()
    if api_key is None:
        key_enc = current.get("api_key_enc") or ""
    else:
        key_enc = encrypt_token(api_key.strip()) if api_key.strip() else ""
    if units_per_image is _KEEP:
        units = ai._num(current.get("units_per_image"))
    else:
        units = ai.amount(units_per_image, "Цена картинки в единицах")
    if cost_per_image is _KEEP:
        cost = ai._num(current.get("cost_per_image"))
    else:
        cost = ai.amount(cost_per_image, "Себестоимость картинки, ₽", digits=2)
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
            UPDATE service_images SET provider = %s, base_url = %s, model = %s, folder_id = %s, api_key_enc = %s,
                   units_per_image = %s, cost_per_image = %s,
                   checked_at = CASE WHEN %s THEN NULL ELSE checked_at END,
                   check_error = CASE WHEN %s THEN '' ELSE check_error END,
                   updated_at = NOW()
            WHERE id = 1
            """,
            (provider, base_url, model, folder_id, key_enc, units, cost, changed, changed),
        )
    return get_settings()


def load_config(user_id: int | None, workspace_id: int | None = None) -> dict:
    """Готовая конфигурация вызова: адрес, модель, заголовки и цена картинки. user_id None — без списания."""
    row = _row()
    if not _configured(row):
        raise YandexError("image-not-configured", NOT_CONFIGURED)
    try:
        key = decrypt_token(row["api_key_enc"])
    except RuntimeError as exc:
        raise YandexError("image-not-configured", NOT_CONFIGURED) from exc
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
    bridge = ai_bridge.target("images", model) if provider == "openai" else None
    if bridge:
        base = bridge["base_url"]
        headers.update(bridge["headers"])
    return {
        "provider": provider,
        "base_url": base,
        "model": model,
        "model_name": model_name,
        "headers": {**headers, "Content-Type": "application/json"},
        "check_host": base not in (OPENAI_BASE, YANDEX_BASE),
        "bridge": bool(bridge),
        "user_id": user_id,
        "workspace_id": workspace_id,
        "task": "images",
        "units": ai._num(row.get("units_per_image")) or 0.0,
        "cost": ai._num(row.get("cost_per_image")),
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
    """Списание за картинку: строка ai_usage и открытые ai.meter() (расход ответа чата)."""
    for acc in ai._meters.get():
        acc["calls"] += 1
        acc["units"] += cfg["units"]
    billing.record(
        cfg.get("user_id"),
        kind="image",
        task="images",
        workspace_id=cfg.get("workspace_id"),
        model=cfg.get("model_name") or "",
        chars_in=len(prompt),
        units=1,
        charged=cfg["units"],
        cost_rub=cfg.get("cost"),
    )


def clean_prompt(prompt: str, provider: str = "openai") -> str:
    text = " ".join(str(prompt or "").split())
    if not text:
        raise YandexError("image-prompt", "Опишите, что должно быть на картинке")
    return text[: PROMPT_MAX.get(provider, 500)]


def generate(user_id: int | None, prompt: str, *, count: int = 1, workspace_id: int | None = None) -> dict:
    """count картинок по одному описанию (вызовы параллельно, n=1 — так умеют все модели).

    → {"images": [bytes…], "errors": [текст…], "model"}. Ошибка, если не вышло ни одной.
    """
    plans.require_feature(user_id, "images")
    cfg = load_config(user_id, workspace_id)
    prompt = clean_prompt(prompt, cfg["provider"])
    count = max(1, min(int(count or 1), COUNT_MAX))
    billing.check(user_id, cfg["units"])
    left = billing.units_left(user_id) if user_id else None
    if left is not None and cfg["units"]:
        count = min(count, max(1, math.floor(left / cfg["units"])))
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


def _remember_check(error: str) -> None:
    with pool.connection() as conn:
        conn.execute("UPDATE service_images SET checked_at = NOW(), check_error = %s WHERE id = 1", (error[:500],))


def check_connection() -> dict:
    """Одна настоящая картинка (платно для сервиса, без списания единиц); итог запоминается, картинка — в ответе."""
    try:
        result = generate(None, CHECK_PROMPT, count=1)
    except YandexError as exc:
        if exc.code != "image-not-configured":
            _remember_check(exc.message or exc.code)
        raise
    _remember_check("")
    data = result["images"][0]
    return {"ok": True, "model": result["model"], "bytes": len(data), "image": to_data_url(data)}
