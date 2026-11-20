"""ИИ сервиса: общее подключение (настраивает администратор) и вызов OpenAI-совместимого chat/completions.

Поддерживаются YandexGPT (OpenAI-совместимый эндпоинт Yandex Cloud) и любые OpenAI-совместимые API
с публичным https-адресом — к зарубежным через мост Cloudflare (ai_bridge).

Модели — три класса: быстрая, стандартная, думающая (ai_model_classes). Чат работает классом, который
выбрал пользователь; фоновые задачи (разметка запросов, бриф, площадки) — классом из настроек сервиса.
Каждый вызов списывает единицы по цене класса (за 1 млн токенов входа и выхода) и пишется в ai_usage
вместе с себестоимостью в рублях; перед вызовом — проверка лимита единиц пользователя (billing).
"""

from __future__ import annotations

import difflib
import ipaddress
import json
import logging
import math
import re
import socket
import time
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

import httpx

from app import ai_bridge, billing
from app.db import pool
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

log = logging.getLogger(__name__)

OPENAI_BASE = "https://api.openai.com/v1"
# Claude идёт родным Messages API: OpenAI-совместимый слой Anthropic не кэширует начало запроса.
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 32000
YANDEX_BASE = "https://llm.api.cloud.yandex.net/v1"
PROVIDERS = {
    "openai": {"label": "OpenAI-совместимый", "default_model": "gpt-4o-mini"},
    "yandexgpt": {"label": "YandexGPT", "default_model": "yandexgpt/latest"},
}
TIMEOUT_SECONDS = 90
# Потоковый ответ: timeout вызова — молчание между кусками, а на весь ответ потолок свой.
STREAM_TOTAL_SECONDS = 600
# Временные сбои по дороге к модели (регион моста, перегрузка провайдера, 5xx шлюза, обрыв связи) обычно
# проходят со второго раза: повторяем сами с этими паузами, прежде чем показать ошибку. Таймаут не повторяем —
# ожидание удвоится; его повторяет пользователь кнопкой в чате.
RETRY_PAUSES = (2, 5)
# 429 с Retry-After дольше этого не ждём — сразу ошибка.
RETRY_AFTER_MAX = 20
# 524 — шлюз не дождался модели, тот же таймаут: не повторяем.
RETRY_STATUSES = {500, 502, 503, 504, 520, 521, 522, 523, 529}

# Классы моделей: точки — относительный расход для пользователя (●○○ … ●●●).
CLASSES = ("fast", "standard", "thinking")
CLASS_DOTS = {"fast": 1, "standard": 2, "thinking": 3}
# Задачи и класс модели по умолчанию; администратор переназначает их в настройках сервиса.
TASKS = {
    "chat": {"label": "ИИ-чат (пока пользователь не выбрал модель)", "class": "standard"},
    "classify": {"label": "Разметка поисковых запросов", "class": "fast"},
    "brief": {"label": "Черновик брифа проекта", "class": "fast"},
    "placements": {"label": "Разбор площадок РСЯ", "class": "fast"},
    "ads": {"label": "Тексты объявлений", "class": "standard"},
    "report": {"label": "Комментарий к отчёту клиенту", "class": "standard"},
    # Модель можно задать отдельно (site_check.py); класс даёт цены и запасную модель.
    "site_check": {"label": "Проверка сайта на лендинге", "class": "fast"},
    # Генераторы минус-слов и объявлений на сайте; модель та же, что у проверки сайта (site_extras.py).
    "site_tools": {"label": "ИИ-генераторы на сайте", "class": "fast"},
    "board_regen": {"label": "Перегенерация строк на доске", "class": "fast"},
}
# Подписи задач в журнале расхода, включая проверку подключения и картинки.
TASK_LABELS = {**{k: v["label"] for k, v in TASKS.items()}, "chat": "ИИ-чат", "test": "Проверка подключения", "images": "Картинки"}
# Русский текст у большинства токенизаторов — 2–3 символа на токен; дальше калибруемся по факту.
CHARS_PER_TOKEN = 2.5
# Картинка до 2000 px — около 1600 токенов входа у GPT и Claude. Точное число провайдер вернёт в usage;
# это — для оценки, когда usage нет, и для калибровки символов на токен.
IMAGE_TOKENS = 1600
PRICE_MAX = 1_000_000
NOT_CONFIGURED = "ИИ временно недоступен: администратор ещё не подключил модель. Напишите в поддержку"
NO_VISION = "Подключённые модели не видят картинки — отметьте их «В объявления» или уберите"


def _key_hint(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        plain = decrypt_token(cipher)
    except RuntimeError:
        return ""
    return f"…{plain[-4:]}" if len(plain) > 8 else "…"


def _num(value) -> float | None:
    return None if value is None else float(value)


def format_rub(value: float) -> str:
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return (text[:-3] if text.endswith(",00") else text) + " ₽"


def amount(value, what: str, *, maximum: float = PRICE_MAX, digits: int = 4) -> float | None:
    """Цена или лимит из формы: пусто — None, иначе неотрицательное число не больше maximum."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(str(value).replace(",", ".").replace(" ", ""))
    except ValueError as exc:
        raise YandexError("ai-price", f"{what} — число") from exc
    if math.isnan(number) or not 0 <= number <= maximum:
        raise YandexError("ai-price", f"{what} — от 0 до {maximum:,.0f}".replace(",", " "))
    return round(number, digits)


# --- Настройки сервиса (админка) ----------------------------------------------------------------


def _service() -> dict:
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM service_ai WHERE id = 1").fetchone() or {}


def _class_public(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "model": row.get("model") or "",
        "units_in": _num(row.get("units_in")),
        "units_out": _num(row.get("units_out")),
        "cost_in": _num(row.get("cost_in")),
        "cost_out": _num(row.get("cost_out")),
        "units_cache": _num(row.get("units_cache")),
        "cost_cache": _num(row.get("cost_cache")),
        "enabled": bool(row.get("enabled")),
        "vision": bool(row.get("vision")),
        "dots": CLASS_DOTS.get(row["id"], 2),
        "reserves": [
            {
                "model": str(r.get("model") or ""),
                "vision": bool(r.get("vision")),
                "cost_in": _num(r.get("cost_in")),
                "cost_out": _num(r.get("cost_out")),
                "cost_cache": _num(r.get("cost_cache")),
            }
            for r in row.get("reserves") or []
            if isinstance(r, dict) and r.get("model")
        ],
    }


def model_classes() -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute("SELECT * FROM ai_model_classes ORDER BY position").fetchall()
    return [_class_public(r) for r in rows]


def _usable(cls: dict | None) -> bool:
    return bool(cls and cls["enabled"] and cls["model"])


def _has_key(row: dict) -> bool:
    return bool(row.get("api_key_enc")) and (row.get("provider") != "yandexgpt" or bool(row.get("folder_id")))


def is_configured() -> bool:
    return _has_key(_service()) and any(_usable(c) for c in model_classes())


def _task_classes(row: dict) -> dict:
    stored = row.get("task_classes") or {}
    return {task: stored.get(task) if stored.get(task) in CLASSES else spec["class"] for task, spec in TASKS.items()}


CLASS_FALLBACK_ORDER = ("standard", "fast", "thinking")


def _resolve_class(
    wanted: str | None, task: str, classes: dict[str, dict], row: dict, *, vision: bool = False
) -> dict | None:
    """Класс для вызова: выбранный, если включён и с моделью, иначе класс задачи, иначе ближайший рабочий.
    vision — в запросе картинки: только класс, модель которого их видит."""
    order = [wanted, _task_classes(row).get(task) or TASKS["chat"]["class"], *CLASS_FALLBACK_ORDER]
    for cid in order:
        if cid and _usable(classes.get(cid)) and (not vision or classes[cid]["vision"]):
            return classes[cid]
    return None


def sees_images() -> bool:
    """Хоть одна включённая модель понимает картинки — можно «Показать ИИ»."""
    return any(_usable(c) and c["vision"] for c in model_classes())


def chat_classes() -> dict:
    """Для выбора модели в чате: включённые классы без моделей и цен и класс по умолчанию."""
    row = _service()
    classes = {c["id"]: c for c in model_classes()}
    default = _resolve_class(None, "chat", classes, row)
    return {
        "configured": _has_key(row) and default is not None,
        "default": default["id"] if default else "",
        "items": [
            {"id": c["id"], "name": c["name"], "dots": c["dots"], "vision": c["vision"]}
            for c in classes.values()
            if _usable(c)
        ],
        "vision": any(_usable(c) and c["vision"] for c in classes.values()),
    }


def class_names() -> dict[str, str]:
    return {c["id"]: c["name"] for c in model_classes()}


def get_service_settings() -> dict:
    row = _service()
    classes = model_classes()
    return {
        "provider": row.get("provider") or "yandexgpt",
        "base_url": row.get("base_url") or "",
        "folder_id": row.get("folder_id") or "",
        "temperature": float(row["temperature"]) if row.get("temperature") is not None else 0.3,
        "has_key": bool(row.get("api_key_enc")),
        "key_hint": _key_hint(row.get("api_key_enc") or ""),
        "configured": _has_key(row) and any(_usable(c) for c in classes),
        "classes": classes,
        "task_classes": _task_classes(row),
        "tasks": [{"id": k, "label": v["label"], "default": v["class"]} for k, v in TASKS.items()],
        "providers": [{"id": k, "label": v["label"], "default_model": v["default_model"]} for k, v in PROVIDERS.items()],
        "default_base_url": OPENAI_BASE,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


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
    """Защита от SSRF: сервер не ходит по заданному адресу во внутреннюю сеть."""
    parts = urlsplit(url)
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise YandexError("ai-url", f"Не удалось найти хост {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise YandexError("ai-url", "Адрес API указывает во внутреннюю сеть")


def _save_classes(conn, classes: list[dict]) -> None:
    known = {r["id"]: r for r in conn.execute("SELECT * FROM ai_model_classes").fetchall()}
    for item in classes or []:
        cid = str(item.get("id") or "")
        if cid not in known:
            raise YandexError("ai-class", "Неизвестный класс модели")
        name = (str(item.get("name") or "").strip() or known[cid]["name"])[:40]
        model = str(item.get("model") or "").strip()[:200]
        units_in = amount(item.get("units_in"), f"{name}: единиц за 1 млн токенов входа")
        units_out = amount(item.get("units_out"), f"{name}: единиц за 1 млн токенов выхода")
        cost_in = amount(item.get("cost_in"), f"{name}: себестоимость входа, ₽", digits=2)
        cost_out = amount(item.get("cost_out"), f"{name}: себестоимость выхода, ₽", digits=2)
        # Пусто — кэшированный вход стоит как обычный.
        units_cache = amount(item.get("units_cache"), f"{name}: единиц за 1 млн токенов кэша")
        cost_cache = amount(item.get("cost_cache"), f"{name}: себестоимость кэша, ₽", digits=2)
        enabled = bool(item.get("enabled"))
        vision = bool(item.get("vision"))
        for a, b, what in ((units_in, units_out, "цены в единицах"), (cost_in, cost_out, "себестоимости")):
            if (a is None) != (b is None):
                raise YandexError("ai-price", f"{name}: укажите обе {what} — за вход и за выход")
        if enabled and not model:
            raise YandexError("ai-class", f"{name}: укажите модель или выключите класс")
        if item.get("reserves") is None:  # форма без резервов — прежние остаются
            reserves = known[cid].get("reserves") or []
        else:
            reserves = _clean_reserves(name, model, item["reserves"])
        conn.execute(
            """
            UPDATE ai_model_classes SET name = %s, model = %s, units_in = %s, units_out = %s, cost_in = %s,
                   cost_out = %s, units_cache = %s, cost_cache = %s, enabled = %s, vision = %s, reserves = %s,
                   updated_at = NOW()
            WHERE id = %s
            """,
            (name, model, units_in, units_out, cost_in, cost_out, units_cache, cost_cache, enabled, vision,
             json.dumps(reserves), cid),
        )


MAX_RESERVES = 5


def _clean_reserves(name: str, model: str, items) -> list[dict]:
    """Резервные модели класса из формы: по порядку, без пустых и повторов. Себестоимость — своя у каждой."""
    out: list[dict] = []
    seen = {model}
    for n, item in enumerate(items or [], 1):
        item = item or {}
        rmodel = str(item.get("model") or "").strip()[:200]
        if not rmodel:
            continue
        what = f"{name}, резерв {n}"
        if rmodel in seen:
            raise YandexError("ai-class", f"{what}: модель {rmodel} уже есть в классе")
        seen.add(rmodel)
        cost_in = amount(item.get("cost_in"), f"{what}: себестоимость входа, ₽", digits=2)
        cost_out = amount(item.get("cost_out"), f"{what}: себестоимость выхода, ₽", digits=2)
        if (cost_in is None) != (cost_out is None):
            raise YandexError("ai-price", f"{what}: укажите обе себестоимости — за вход и за выход")
        out.append({
            "model": rmodel,
            "vision": bool(item.get("vision")),
            "cost_in": cost_in,
            "cost_out": cost_out,
            "cost_cache": amount(item.get("cost_cache"), f"{what}: себестоимость кэша, ₽", digits=2),
        })
    if len(out) > MAX_RESERVES:
        raise YandexError("ai-class", f"{name}: не больше {MAX_RESERVES} резервных моделей")
    return out


def save_service_settings(
    *,
    provider: str,
    base_url: str = "",
    folder_id: str = "",
    api_key: str | None = None,
    temperature: float | None = None,
    classes: list[dict] | None = None,
    task_classes: dict | None = None,
) -> dict:
    """api_key: None — оставить прежний, "" — удалить. classes — [{id, name, model, units_*, cost_*, enabled}]."""
    provider = (provider or "yandexgpt").strip().lower()
    if provider not in PROVIDERS:
        raise YandexError("ai-provider", "Неизвестный провайдер ИИ")
    base_url = _check_url_syntax(base_url) if provider == "openai" else ""
    folder_id = (folder_id or "").strip()[:100]
    if provider == "yandexgpt" and not folder_id:
        raise YandexError("ai-folder", "Для YandexGPT укажите идентификатор каталога Yandex Cloud")
    try:
        temp = 0.3 if temperature is None else float(temperature)
    except (TypeError, ValueError) as exc:
        raise YandexError("ai-temperature", "Температура — число от 0 до 1") from exc
    temp = max(0.0, min(temp, 1.0))
    current = _service()
    if api_key is None:
        key_enc = current.get("api_key_enc") or ""
    else:
        key_enc = encrypt_token(api_key.strip()) if api_key.strip() else ""
    stored = dict(current.get("task_classes") or {})
    for task, cid in (task_classes or {}).items():
        if task in TASKS and cid in CLASSES:
            stored[task] = cid
    with pool.connection() as conn:
        _save_classes(conn, classes or [])
        conn.execute(
            """
            UPDATE service_ai SET provider = %s, base_url = %s, folder_id = %s, api_key_enc = %s,
                   temperature = %s, task_classes = %s, updated_at = NOW()
            WHERE id = 1
            """,
            (provider, base_url, folder_id, key_enc, temp, json.dumps(stored)),
        )
    _down.clear()  # модели или ключ могли смениться — прошлые отказы не в счёт
    return get_service_settings()


def _class_cost(c: dict) -> float | None:
    if c.get("cost_in") is None or c.get("cost_out") is None:
        return None
    return c["cost_in"] + c["cost_out"]


def _fallback_chain(cls: dict, classes: dict[str, dict], primary: str, vision: bool) -> list[dict]:
    """Запасные модели по порядку: резервы класса, затем классы дешевле — от дорогих к самому дешёвому
    (каждый со своими резервами), затем классы дороже — от дешёвых. Дешевле — по себестоимости модели класса
    (вход + выход); у какого-то класса она не задана — по точкам ●○○…●●●. vision — только видящие картинки.
    → [{"id": класс, "model", "vision", "cost_in", "cost_out", "cost_cache"}]."""
    others = [c for c in classes.values() if c["id"] != cls["id"] and _usable(c)]
    if all(_class_cost(c) is not None for c in [cls, *others]):
        price = _class_cost
    else:
        price = lambda c: CLASS_DOTS.get(c["id"], 2)  # noqa: E731
    base = price(cls)
    cheaper = sorted((c for c in others if price(c) <= base), key=price, reverse=True)
    dearer = sorted((c for c in others if price(c) > base), key=price)
    out, seen = [], {primary}
    for c in [cls, *cheaper, *dearer]:
        main = {k: c.get(k) for k in ("model", "vision", "cost_in", "cost_out", "cost_cache")}
        for option in [main, *(c.get("reserves") or [])]:
            if option["model"] and option["model"] not in seen and (not vision or option["vision"]):
                seen.add(option["model"])
                out.append({"id": c["id"], **option})
    return out


def load_config(
    user_id: int | None,
    task: str = "chat",
    workspace_id: int | None = None,
    *,
    model_class: str | None = None,
    model: str | None = None,
    vision: bool = False,
) -> dict:
    """Готовая конфигурация вызова: адрес, модель класса, заголовки и цены класса.

    user_id — с кого списывать единицы (None — проверка в админке, без списания). model_class — выбор
    пользователя в чате; выключенный или пустой класс заменяется классом задачи. model — своя модель
    вместо модели класса (тот же адрес API и ключ, цены — класса). vision — в запросе будут картинки:
    класс без зрения заменяется ближайшим, который их видит; такого нет — ошибка.
    """
    row = _service()
    if not _has_key(row):
        raise YandexError("ai-not-configured", NOT_CONFIGURED)
    try:
        key = decrypt_token(row["api_key_enc"])
    except RuntimeError as exc:
        raise YandexError("ai-not-configured", NOT_CONFIGURED) from exc
    classes = {c["id"]: c for c in model_classes()}
    cls = _resolve_class(model_class, task, classes, row, vision=vision)
    if cls is None and vision:
        raise YandexError("ai-vision", NO_VISION)
    if cls is None:
        raise YandexError("ai-not-configured", NOT_CONFIGURED)
    provider = row["provider"]
    model = (model or "").strip() or cls["model"]
    model_name = model
    # Запасные модели — на случай, если провайдер эту не отдаст. Проверка подключения проверяет каждую саму.
    fallbacks = _fallback_chain(cls, classes, model_name, vision) if task != "test" else []
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "yandexgpt":
        folder = row["folder_id"]
        base = YANDEX_BASE
        if not model.startswith("gpt://"):
            model = f"gpt://{folder}/{model.lstrip('/')}"
        headers["OpenAI-Project"] = folder
    else:
        base = row.get("base_url") or OPENAI_BASE
    try:
        bridge = ai_bridge.target("chat", model) if provider == "openai" else None
    except YandexError as exc:
        # Мост не настроен или не знает модель: до провайдера вызов не дошёл, но пользователь без ответа — в журнал.
        # Запасную модель внутри complete() запишет цепочка; проверки из админки (user_id None) не пишем.
        if user_id and _attempts.get() is None:
            _note_failure({"model_name": model_name, "model_class": cls["id"], "user_id": user_id, "task": task}, exc)
        raise
    temperature = float(row["temperature"]) if row.get("temperature") is not None else 0.3
    if bridge:
        # Через мост Cloudflare: свой адрес API не используется, модель — в виде, понятном мосту.
        base = bridge["base_url"]
        headers.update(bridge["headers"])
        model = bridge["model"]
        if bridge["anthropic"]:
            # OpenAI-совместимый слой Anthropic отвечает 400 на temperature ниже 1 у новых Claude.
            temperature = None
    anthropic = None
    if provider == "openai" and (bridge or {}).get("messages_url"):
        anthropic = {"url": bridge["messages_url"], "model": bridge.get("native_model") or model}
    elif provider == "openai" and not bridge and urlsplit(base).hostname == "api.anthropic.com":
        anthropic = {"url": f"{base.rstrip('/')}/messages", "model": model}
    if anthropic:
        # Родной API Claude: ключ — в x-api-key (Bearer он понял бы как OAuth-токен).
        anthropic["headers"] = {
            **{k: v for k, v in headers.items() if k not in ("Authorization", "OpenAI-Project")},
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    # prompt_cache_key понимает сам OpenAI; чужие OpenAI-совместимые API могут его не знать.
    openai_cache = (
        provider == "openai"
        and ai_bridge.model_provider(model_name) == "openai"
        and ((bridge or {}).get("kind") == "worker" or (not bridge and base.rstrip("/") == OPENAI_BASE))
    )
    return {
        "provider": provider,
        "base_url": base,
        "model": model,
        "headers": headers,
        "bridge": bool(bridge),
        "bridge_kind": bridge["kind"] if bridge else "",
        "anthropic": anthropic,
        "openai_cache": openai_cache,
        "temperature": temperature,
        "check_host": base != OPENAI_BASE,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "task": task,
        "model_class": cls["id"],
        "class_name": cls["name"],
        "model_name": model_name,
        "units_in": cls["units_in"],
        "units_out": cls["units_out"],
        "cost_in": cls["cost_in"],
        "cost_out": cls["cost_out"],
        "units_cache": cls.get("units_cache"),
        "cost_cache": cls.get("cost_cache"),
        "vision": vision,
        "fallbacks": fallbacks,
    }


# --- Учёт расхода и лимит -----------------------------------------------------------------------

_meters: ContextVar[tuple] = ContextVar("ai_meters", default=())


@contextmanager
def meter():
    """Сколько списали вызовы внутри блока: {"calls", "tokens_in", "tokens_out", "units", "estimated"}."""
    acc = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "units": 0.0, "estimated": False}
    token = _meters.set((*_meters.get(), acc))
    try:
        yield acc
    finally:
        _meters.reset(token)


def usage_out(acc: dict, user_id: int | None) -> dict | None:
    """Расход операции для пользователя: без токенов и денег — доля месячного лимита в процентах."""
    if not acc["calls"]:
        return None
    return {
        "calls": acc["calls"],
        "percent": billing.share(user_id, acc["units"]) if user_id else None,
        "estimated": acc["estimated"],
    }


def _priced(cfg: dict, prefix: str, tokens_in: float, tokens_out: float, cached: float) -> float | None:
    """Цена вызова по ставкам {prefix}_in/_out/_cache за 1 млн токенов; ставки входа или выхода нет — None.

    tokens_in включает кэшированные: они идут по ставке кэша, а без неё — как обычный вход.
    """
    rate_in, rate_out = cfg.get(f"{prefix}_in"), cfg.get(f"{prefix}_out")
    if rate_in is None or rate_out is None:
        return None
    rate_cache = cfg.get(f"{prefix}_cache")
    if rate_cache is None:
        rate_cache = rate_in
    cached = max(0.0, min(float(cached or 0), float(tokens_in)))
    return ((tokens_in - cached) * rate_in + cached * rate_cache + tokens_out * rate_out) / 1_000_000


def units_for(cfg: dict, tokens_in: float, tokens_out: float, cached: float = 0, images: int = 0) -> float:
    """Единиц за вызов по цене класса модели; цена не задана — 0.

    images — картинок в запросе, если tokens_in их не включает (оценка без usage провайдера): каждая
    идёт как IMAGE_TOKENS токенов входа. В usage провайдера картинки уже посчитаны в prompt_tokens.
    """
    return _priced(cfg, "units", tokens_in + images * IMAGE_TOKENS, tokens_out, cached) or 0.0


def cost_rub(cfg: dict, tokens_in: float, tokens_out: float, cached: float = 0) -> float | None:
    """Себестоимость вызова в рублях; None — не задана."""
    return _priced(cfg, "cost", tokens_in, tokens_out, cached)


def cached_tokens(raw: dict) -> int:
    """Сколько токенов входа провайдер взял из кэша.

    OpenAI-совместимый ответ (OpenAI, OpenRouter, прокси) — usage.prompt_tokens_details.cached_tokens; они уже
    входят в prompt_tokens. Поля нет или не число — 0: тогда весь вход идёт по полной цене.
    """
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    value = details.get("cached_tokens") if isinstance(details, dict) else None
    if value is None:
        value = raw.get("cached_tokens")
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def budget_left(cfg: dict) -> float | None:
    """Остаток единиц пользователя в периоде; None — без лимита или вызов без списания."""
    if not cfg.get("user_id"):
        return None
    return billing.units_left(cfg["user_id"])


def check_budget(cfg: dict, need: float = 0.0) -> None:
    billing.check(cfg.get("user_id"), need)


def require_ready(user_id: int, task: str = "chat") -> dict:
    """Перед постановкой задачи: ИИ подключён и лимит не исчерпан — иначе сразу понятная ошибка."""
    cfg = load_config(user_id, task)
    check_budget(cfg)
    return cfg


def _text_size(messages: list[dict]) -> tuple[int, int]:
    """Символов текста в запросе и картинок в нём: base64 картинки — не текст, его не считаем."""
    images = 0
    plain = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    images += 1
                else:
                    parts.append(part)
            m = {**m, "content": parts}
        plain.append(m)
    return len(json.dumps(plain, ensure_ascii=False)), images


def estimate_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """Размер запроса в токенах по символам — для сообщений об ошибке, когда ответа с usage нет."""
    chars, images = _text_size(messages)
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return math.ceil(chars / CHARS_PER_TOKEN) + images * IMAGE_TOKENS


def _usage(cfg: dict, data: dict, messages: list[dict], tools, content: str, calls: list[dict]) -> dict:
    chars_in, images = _text_size(messages)
    if tools:
        chars_in += len(json.dumps(tools, ensure_ascii=False))
    chars_out = len(content) + sum(len(c["name"]) + len(c["arguments"]) for c in calls)
    raw = data.get("usage") if isinstance(data, dict) else None
    try:
        tokens_in, tokens_out = int(raw["prompt_tokens"]), int(raw.get("completion_tokens") or 0)
        cached = min(cached_tokens(raw), tokens_in)
        estimated = False
    except (TypeError, KeyError, ValueError, AttributeError):
        tokens_in = math.ceil(chars_in / CHARS_PER_TOKEN) + images * IMAGE_TOKENS
        tokens_out = math.ceil(chars_out / CHARS_PER_TOKEN)
        cached = 0
        estimated = True
    # Для калибровки «символов на токен»: картинка идёт как IMAGE_TOKENS токенов текста.
    chars_in += math.ceil(images * IMAGE_TOKENS * CHARS_PER_TOKEN)
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_cached": cached,
        "chars_in": chars_in,
        "chars_out": chars_out,
        "estimated": estimated,
        "images": images,
        "charged": units_for(cfg, tokens_in, tokens_out, cached),
        "cost_rub": cost_rub(cfg, tokens_in, tokens_out, cached),
    }


def _record(cfg: dict, usage: dict) -> None:
    """Списание за вызов в ai_usage и в открытые meter(). Ошибка учёта не роняет ответ модели."""
    for acc in _meters.get():
        acc["calls"] += 1
        acc["tokens_in"] += usage["tokens_in"]
        acc["tokens_out"] += usage["tokens_out"]
        acc["units"] += usage["charged"]
        acc["estimated"] = acc["estimated"] or usage["estimated"]
    billing.record(
        cfg.get("user_id"),
        kind="model",
        task=cfg.get("task") or "chat",
        workspace_id=cfg.get("workspace_id"),
        model=cfg.get("model_name") or cfg.get("model") or "",
        model_class=cfg.get("model_class") or "",
        tokens_in=usage["tokens_in"],
        tokens_out=usage["tokens_out"],
        tokens_cached=usage.get("tokens_cached", 0),
        chars_in=usage["chars_in"],
        chars_out=usage["chars_out"],
        units=int(cfg.get("units") or 0),
        estimated=usage["estimated"],
        charged=usage["charged"],
        cost_rub=usage["cost_rub"],
        fallback_from=cfg.get("fallback_from") or "",
        fallback_reason=cfg.get("fallback_reason") or "",
        chat_id=cfg.get("chat_id"),
        duration_ms=usage.get("duration_ms"),
    )


# --- Журнал ошибок вызова (ai_errors) -----------------------------------------------------------
# complete() открывает список попыток; _send_retrying дописывает в него каждую неудачную; в конце цепочки
# повторов и подмен попытки уходят в ai_errors вместе с итогом: ответил ли кто-то и кто.

_attempts: ContextVar[list | None] = ContextVar("ai_attempts", default=None)
ERRORS_KEEP_DAYS = 90


def _attempt_row(cfg: dict, exc: YandexError, *, retried: bool) -> dict:
    return {
        "model": cfg.get("model_name") or cfg.get("model") or "",
        "model_class": cfg.get("model_class") or "",
        "code": exc.code or "",
        "message": (exc.message or "")[:500],
        "retried": retried,
        "fallback": False,
    }


def _note_attempt(cfg: dict, exc: YandexError, *, retried: bool) -> None:
    attempts = _attempts.get()
    if attempts is None:
        return
    exc.noted = True
    attempts.append(_attempt_row(cfg, exc, retried=retried))


def _note_failure(cfg: dict, exc: YandexError) -> None:
    """Сбой провайдера мимо _send_retrying (адрес API, непонятный ответ, мост, JSON не по схеме) — тоже в ai_errors.
    Внутри complete() — в его список попыток (запишется с итогом вызова), вне — сразу, без ответа.
    Уже записанный (exc.noted) и не провайдерский (тариф, «ИИ не подключён») не пишем."""
    if getattr(exc, "noted", False) or not is_provider_error(exc.code):
        return
    if _attempts.get() is not None:
        _note_attempt(cfg, exc, retried=False)
        return
    exc.noted = True
    _save_attempts(cfg, [_attempt_row(cfg, exc, retried=False)], "")


def _save_attempts(cfg: dict, attempts: list[dict], answered_by: str) -> None:
    """Неудачные попытки одного вызова → ai_errors. Проверки в админке не пишутся. Ошибка учёта глотается."""
    if not attempts:
        return
    source = billing.usage_source(cfg.get("user_id"), cfg.get("task") or "chat")
    if source is None:
        return
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO ai_errors (user_id, source, task, model, model_class, code, message, retried,
                                           fallback, answered, answered_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            cfg.get("user_id") or None,
                            source,
                            cfg.get("task") or "chat",
                            a["model"],
                            a["model_class"],
                            a["code"],
                            a["message"],
                            a["retried"],
                            a["fallback"],
                            bool(answered_by),
                            answered_by,
                        )
                        for a in attempts
                    ],
                )
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать ошибки ИИ")


def purge_errors() -> int:
    """Ошибки ИИ старше ERRORS_KEEP_DAYS — прочь (раз в сутки из планировщика)."""
    with pool.connection() as conn:
        cur = conn.execute(
            "DELETE FROM ai_errors WHERE created_at < NOW() - make_interval(days => %s)", (ERRORS_KEEP_DAYS,)
        )
        return cur.rowcount or 0


_GATEWAY_TEXT = {
    502: "шлюз провайдера получил ошибку от модели",
    503: "провайдер временно недоступен",
    504: "шлюз провайдера не дождался ответа модели",
    520: "шлюз провайдера получил от модели неверный ответ",
    521: "модель за шлюзом провайдера не принимает соединения",
    522: "шлюз провайдера не смог соединиться с моделью",
    523: "шлюз провайдера не нашёл путь к модели",
    524: "шлюз провайдера (обычно Cloudflare) оборвал ожидание: модель отвечала дольше ~100 с",
}


def _error_text(response: httpx.Response) -> str:
    text = _error_text_raw(response)
    # HTML-страница шлюза может прийти и внутри JSON-поля: разметку в чат не пускаем.
    if re.search(r"<!doctype|<html", text, re.I):
        return _GATEWAY_TEXT.get(response.status_code, "вместо ответа API пришла html-страница ошибки")
    return text


def _error_text_raw(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        # Страница ошибки шлюза вместо JSON: показываем смысл, а не разметку.
        if response.text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            return _GATEWAY_TEXT.get(response.status_code, "вместо ответа API пришла html-страница ошибки")
        return response.text[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])[:300]
    # Ошибки самого шлюза Cloudflare: {"success": false, "errors": [{"code": …, "message": …}]}.
    errors = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errors, list) and errors:
        return "; ".join(str(e.get("message") if isinstance(e, dict) else e) for e in errors)[:300]
    return str(data)[:300]


def _stream_error(text: str, overloaded: bool) -> YandexError:
    """Ошибка посреди потока: деньги и модель — как у ответа с кодом, перегрузка — временная."""
    if _NO_BALANCE.search(text):
        return YandexError("ai-balance", f"У провайдера ИИ кончились деньги или квота: {text}")
    if _MODEL_MISSING.search(text):
        return YandexError("ai-model", f"Провайдер ИИ не отдаёт модель: {text}")
    return (_transient if overloaded else YandexError)("ai-error", f"Ошибка ИИ: {text}")


def _post_stream(url: str, body: dict, headers: dict, timeout: float) -> tuple[httpx.Response, dict | None]:
    """chat/completions c stream=true → (ответ, собранное тело в виде обычного ответа).

    Куски идут сразу, поэтому шлюз провайдера (Cloudflare рвёт молчащее соединение на ~100 с) не режет
    долгий ответ на большой запрос. Тело None — стрима нет: ошибка провайдера или он проигнорировал
    stream и ответил обычным JSON; тогда его читают как обычно.
    """
    body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    deadline = time.monotonic() + STREAM_TOTAL_SECONDS
    content: list[str] = []
    calls: dict[int, dict] = {}
    usage = None
    answered_by = ""
    with httpx.stream(
        "POST", url, json=body, headers=headers, timeout=httpx.Timeout(timeout, connect=30.0), follow_redirects=False
    ) as response:
        if response.status_code >= 400 or "text/event-stream" not in response.headers.get("content-type", ""):
            response.read()
            return response, None
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise httpx.ReadTimeout("ответ идёт дольше предела")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(chunk, dict):
                continue
            if chunk.get("error"):
                err = chunk["error"]
                text = str(err.get("message") if isinstance(err, dict) else err)[:300]
                kind = str(err.get("type") or "") if isinstance(err, dict) else ""
                # Перегрузка провайдера посреди ответа (Anthropic: overloaded_error) — временная.
                overloaded = kind in ("overloaded_error", "api_error") or "overloaded" in text.lower()
                raise _stream_error(text, overloaded)
            answered_by = answered_by or str(chunk.get("model") or "")
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                for part in delta.get("tool_calls") or []:
                    index = part.get("index")
                    if index is None:
                        index = len(calls) if part.get("id") or not calls else len(calls) - 1
                    slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    fn = part.get("function") or {}
                    slot["id"] = slot["id"] or part.get("id") or ""
                    slot["name"] = slot["name"] or fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
    message = {
        "content": "".join(content),
        "tool_calls": [
            {"id": c["id"], "function": {"name": c["name"], "arguments": c["arguments"]}} for _, c in sorted(calls.items())
        ],
    }
    return response, {"choices": [{"message": message}], "usage": usage, "model": answered_by}


# --- Родной Messages API Claude ---------------------------------------------------------------------
# OpenAI-совместимый слой Anthropic кэш не поддерживает: системный промпт и схемы инструментов шли по полной
# цене в каждом шаге хода. Родной API кэширует начало запроса до точек cache_control — инструменты, системный
# промпт, последнее сообщение (следующий шаг хода читает всё это из кэша).

_EPHEMERAL = {"type": "ephemeral"}


def _tool_id(value) -> str:
    """id вызова для Anthropic: только буквы, цифры, _ и - (у других моделей в истории бывают другие)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value or "")) or "call"


def _anthropic_parts(content) -> list[dict]:
    """content OpenAI (строка или части text / image_url) → блоки Anthropic, без пустого текста."""
    if isinstance(content, str) or content is None:
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("text"):
            blocks.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url") or "")
            match = re.match(r"data:([^;,]+);base64,(.*)", url, re.S)
            if match:
                source = {"type": "base64", "media_type": match.group(1), "data": match.group(2)}
            else:
                source = {"type": "url", "url": url}
            blocks.append({"type": "image", "source": source})
    return blocks


def anthropic_body(messages: list[dict], tools: list[dict] | None, model: str, *, volatile_tail: int = 0) -> dict:
    """Запрос chat/completions (сообщения и инструменты OpenAI) → тело /v1/messages с точками кэша.

    volatile_tail — сколько последних сообщений меняются от шага к шагу (служебная сводка): точка кэша
    ставится перед ними, иначе следующий шаг не нашёл бы в кэше начало запроса.
    """
    system = []
    out: list[dict] = []
    stable = len(messages) - max(0, volatile_tail)
    mark: tuple[int, int] | None = None
    for index, m in enumerate(messages):
        role = m.get("role")
        if role in ("system", "developer"):
            system.extend(b["text"] for b in _anthropic_parts(m.get("content")) if b["type"] == "text")
            continue
        if role == "tool":
            text = m.get("content")
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False)
            blocks = [{"type": "tool_result", "tool_use_id": _tool_id(m.get("tool_call_id")), "content": text or "(пусто)"}]
            role = "user"
        elif role == "assistant":
            blocks = _anthropic_parts(m.get("content"))
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except ValueError:
                        args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": _tool_id(call.get("id")),
                    "name": fn.get("name") or "",
                    "input": args if isinstance(args, dict) else {},
                })
        else:
            blocks = _anthropic_parts(m.get("content"))
            role = "user"
        if not blocks:
            continue
        # Подряд идущие сообщения одной роли — одно сообщение (ответы инструментов шага — одним user).
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
        if index < stable:
            mark = (len(out) - 1, len(out[-1]["content"]) - 1)
    body: dict = {"model": model, "max_tokens": ANTHROPIC_MAX_TOKENS, "messages": out}
    if system:
        body["system"] = [{"type": "text", "text": "\n\n".join(system), "cache_control": _EPHEMERAL}]
    if tools:
        converted = []
        for tool in tools:
            fn = tool.get("function") or tool
            converted.append({
                "name": fn.get("name") or "",
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        converted[-1] = {**converted[-1], "cache_control": _EPHEMERAL}
        body["tools"] = converted
    if out:
        at, block = mark if mark is not None else (len(out) - 1, len(out[-1]["content"]) - 1)
        out[at]["content"][block] = {**out[at]["content"][block], "cache_control": _EPHEMERAL}
    return body


REFUSAL_TEXT = "Модель отказалась отвечать на этот запрос."


def _from_anthropic(blocks: list[dict], usage: dict | None, model: str, stop_reason: str = "") -> dict:
    """Ответ /v1/messages → тот же вид, что у chat/completions (choices[0].message, usage, model)."""
    text = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
    calls = []
    for b in blocks:
        if b.get("type") != "tool_use":
            continue
        args = b.get("input")
        if not isinstance(args, str):
            args = json.dumps(args if args is not None else {}, ensure_ascii=False)
        calls.append({"id": b.get("id") or "", "function": {"name": b.get("name") or "", "arguments": args or "{}"}})
    if stop_reason == "refusal" and not text and not calls:
        text = REFUSAL_TEXT
    converted = None
    if isinstance(usage, dict):
        try:
            read = int(usage.get("cache_read_input_tokens") or 0)
            written = int(usage.get("cache_creation_input_tokens") or 0)
            converted = {
                # Как у OpenAI: prompt_tokens — весь вход, из кэша — его часть.
                "prompt_tokens": int(usage.get("input_tokens") or 0) + read + written,
                "completion_tokens": int(usage.get("output_tokens") or 0),
                "prompt_tokens_details": {"cached_tokens": read},
            }
        except (TypeError, ValueError):
            converted = None
    return {"choices": [{"message": {"content": text, "tool_calls": calls}}], "usage": converted, "model": model}


def _post_anthropic(url: str, body: dict, headers: dict, timeout: float, stream: bool) -> tuple[httpx.Response, dict | None]:
    """Запрос в /v1/messages → (ответ, тело в виде chat/completions); тело None — ответ не поток, его читают JSON."""
    if not stream:
        return httpx.post(url, json=body, headers=headers, timeout=timeout, follow_redirects=False), None
    deadline = time.monotonic() + STREAM_TOTAL_SECONDS
    blocks: dict[int, dict] = {}
    usage: dict = {}
    model = ""
    stop_reason = ""
    with httpx.stream(
        "POST", url, json={**body, "stream": True}, headers=headers,
        timeout=httpx.Timeout(timeout, connect=30.0), follow_redirects=False,
    ) as response:
        if response.status_code >= 400 or "text/event-stream" not in response.headers.get("content-type", ""):
            response.read()
            return response, None
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise httpx.ReadTimeout("ответ идёт дольше предела")
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "error":
                err = event.get("error") or {}
                text = str(err.get("message") if isinstance(err, dict) else err)[:300]
                err_type = str(err.get("type") or "") if isinstance(err, dict) else ""
                overloaded = err_type in ("overloaded_error", "api_error") or "overloaded" in text.lower()
                raise _stream_error(text, overloaded)
            if kind == "message_start":
                message = event.get("message") or {}
                model = str(message.get("model") or "")
                usage.update(message.get("usage") or {})
            elif kind == "content_block_start":
                block = dict(event.get("content_block") or {})
                if block.get("type") == "tool_use":
                    block["input"] = ""  # приходит кусками partial_json
                blocks[event.get("index", len(blocks))] = block
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                block = blocks.setdefault(event.get("index", 0), {"type": "text", "text": ""})
                if delta.get("type") == "text_delta":
                    block["text"] = (block.get("text") or "") + (delta.get("text") or "")
                elif delta.get("type") == "input_json_delta":
                    block["input"] = (block.get("input") or "") + (delta.get("partial_json") or "")
            elif kind == "message_delta":
                stop_reason = str((event.get("delta") or {}).get("stop_reason") or "")
                usage.update({k: v for k, v in (event.get("usage") or {}).items() if v is not None})
            elif kind == "message_stop":
                break
    ordered = [b for _, b in sorted(blocks.items())]
    return response, _from_anthropic(ordered, usage or None, model, stop_reason)


# --- Сбой ИИ словами для пользователя -----------------------------------------------------------------
# Пользователю — коротко и что делать. Технические подробности (код, модель, ответ провайдера) — в журнале
# ошибок ИИ в админке (ai_errors) и в оповещении «ИИ не отвечает» владельцу.
ERROR_BUSY = "ИИ сейчас перегружен — повторите через минуту."
ERROR_LONG = (
    "Разговор стал слишком длинным для ИИ. Начните новый чат — данные проекта и запомненные факты ИИ увидит и там."
)
# Провайдер отклонил историю разговора (_BROKEN_HISTORY): повтор не поможет, новый чат — да. Вид «long».
ERROR_RESTART = (
    "ИИ не может продолжить этот разговор. Начните новый чат — данные проекта и запомненные факты ИИ увидит и там."
)
ERROR_DOWN = "ИИ временно недоступен — сбой на нашей стороне, кампании и данные в порядке. Повторите через несколько минут."
# «Слишком длинно» вне чата (разметка запросов, площадки, объявления, задачи): новый чат там не поможет.
ERROR_TOO_MUCH = "Слишком много данных для ИИ за один раз. Сократите объём и повторите."
# Модель ответила, но не JSON по схеме и после просьбы исправиться — обычно проходит со второго раза.
ERROR_FORMAT = "ИИ ответил не по формату — повторите ещё раз."
# Ошибки тарифа — вид «plan» (тексты уже написаны для пользователя, как и все не провайдерские).
PLAN_ERRORS = {"units-exhausted", "subscription-expired", "feature-locked", "project-locked"}
# Генератор картинок (image_gen): временные сбои и отказы провайдера. image-error (картинка не та: битая,
# больше 5 МБ, не JPG/PNG/GIF), image-prompt, image-not-configured — уже словами для пользователя, не здесь.
IMAGE_BUSY_ERRORS = {"image-limit", "image-network", "image-timeout"}
IMAGE_ERRORS = IMAGE_BUSY_ERRORS | {"image-auth", "image-balance", "image-api"}
# Временные сбои: перегрузка, лимит запросов, сеть, таймаут. bridge-gateway — неверное имя шлюза в настройках
# моста, само не пройдёт: «недоступен», как и прочие bridge-* кроме сети.
BUSY_ERRORS = {"ai-limit", "ai-error", "ai-network", "ai-timeout", "bridge-network"} | IMAGE_BUSY_ERRORS
# Ошибки провайдера, моста и настроек модели — в кабинете их текст заменяется понятным (human_error).
PROVIDER_ERRORS = BUSY_ERRORS | IMAGE_ERRORS | {
    "ai-balance", "ai-auth", "ai-model", "ai-url", "ai-provider", "ai-folder", "ai-class", "ai-price", "ai-temperature",
    "ai-region", "ai-json",
}
# Запрос такого размера, не дождавшийся ответа, — скорее всего, дело в длине разговора.
LONG_REQUEST_TOKENS = 120_000
_TOO_LONG = re.compile(
    r"context.{0,20}(length|window|limit)|maximum context|prompt is too long|too many (input )?tokens"
    r"|input.{0,30}tokens.{0,40}(exceed|no more than|limit)|token limit|request too large|(payload|entity) too large"
    # Gemini: «The input token count (…) exceeds the maximum number of tokens allowed», «payload size exceeds».
    r"|input token count.{0,40}exceed|exceeds the maximum number of tokens|payload size exceeds"
    r"|\(413\)|превыш.{0,40}(токен|контекст)",
    re.I,
)
# Лимит запросов или токенов в минуту (429 «Rate limit … tokens per min (TPM)») — это перегрузка, а не длина,
# хотя слова те же. Исключение — «Request too large … must be reduced»: один запрос больше минутного лимита.
_RATE_LIMIT = re.compile(r"per min|в минуту|\bTPM\b|\bRPM\b|rate.?limit|лимит запросов", re.I)
_MUST_REDUCE = re.compile(r"must be reduced", re.I)
# HTTP-статус в тексте ai-error: «Ошибка ИИ (400): …», «Ошибка ИИ через мост Cloudflare (404): …» (_raise_for_status).
_AI_STATUS = re.compile(r"Ошибка ИИ[^:(]{0,40}\((\d{3})\)")
# Провайдер отклонил историю разговора: вызовы инструментов без ответов, роли не по очереди. Повтор упрётся
# в то же, а новый чат начнётся с чистой истории.
_BROKEN_HISTORY = re.compile(
    r"tool_calls?\b|tool_call_id|tool_use|tool_result|role.{0,3}tool\b|function (call|response) turn|roles must alternate",
    re.I,
)


def is_provider_error(code: str) -> bool:
    return code in PROVIDER_ERRORS or (code or "").startswith("bridge-")


def _too_long(code: str, message: str) -> bool:
    if code in IMAGE_ERRORS or not _TOO_LONG.search(message):
        return False
    return not _RATE_LIMIT.search(message) or bool(_MUST_REDUCE.search(message))


def _rejected(message: str) -> bool:
    """ai-error — отказ в самом запросе (4xx, кроме таймаута и лимита в минуту): сам не пройдёт, в отличие
    от 5xx, таймаута шлюза (524) и перегрузки. Статус берётся из текста: в задачах флага transient нет."""
    found = _AI_STATUS.search(message)
    status = int(found.group(1)) if found else 0
    return 400 <= status < 500 and status not in (408, 429) and not _RATE_LIMIT.search(message)


def human_error(
    code: str, message: str, request: tuple[int, int] | None = None, transient: bool = False, *, chat: bool = True
) -> tuple[str, str]:
    """Сбой вызова ИИ → (текст для пользователя, вид: busy / long / down / plan). request — (токенов, сообщений)
    запроса; вид «long» — в чате рядом с «Повторить» кнопка «Новый чат» (разговор слишком длинный или провайдер
    отклонил его историю). chat=False — вызов не из чата: «long» другим текстом (ERROR_TOO_MUCH), отказ истории —
    «down». Ошибки не провайдера (тариф, проект закрыт, «ИИ не подключён») уже написаны для пользователя — как
    есть; сбой кода (crash) — общим текстом."""
    if not is_provider_error(code):
        if message and code != "crash":
            return message, "plan" if code in PLAN_ERRORS else "down"
        return ERROR_DOWN, "down"
    message = message or ""
    if _too_long(code, message) or (code == "ai-timeout" and request and request[0] >= LONG_REQUEST_TOKENS):
        return (ERROR_LONG if chat else ERROR_TOO_MUCH), "long"
    if code == "ai-json":
        return ERROR_FORMAT, "busy"
    # Отказ в самом запросе (400/404/422…): повтор через минуту упрётся в то же.
    if code == "ai-error" and not transient and _rejected(message):
        if chat and _BROKEN_HISTORY.search(message):
            return ERROR_RESTART, "long"
        return ERROR_DOWN, "down"
    # Лимит без повтора — это квота или деньги провайдера, а не перегрузка: сам не пройдёт.
    if code in BUSY_ERRORS and (transient or code != "ai-limit"):
        return ERROR_BUSY, "busy"
    return ERROR_DOWN, "down"


def user_error(exc: YandexError, *, chat: bool = False) -> str:
    """Текст сбоя ИИ для кабинета: ошибка провайдера — понятными словами, остальные — как есть (human_error)."""
    return human_error(exc.code, exc.message or "", transient=getattr(exc, "transient", False), chat=chat)[0]


def _transient(code: str, message: str, retry_after: float | None = None) -> YandexError:
    """Ошибка, которая обычно проходит сама: complete() повторит запрос, прежде чем её показать."""
    exc = YandexError(code, message)
    exc.transient = True
    exc.retry_after = retry_after
    return exc


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers.get("retry-after") or ""))
    except ValueError:
        return None


def _region_error(cfg: dict, response: httpx.Response) -> YandexError:
    """403 «Request not allowed»: провайдер (Anthropic) увидел запрос из дата-центра рядом с РФ."""
    if cfg.get("bridge_kind") == "worker":
        # Worker закреплён за США, но Cloudflare иногда выполняет его в ближайшем дата-центре.
        # cf-placement (если Cloudflare его прислал) — где он выполнился: local-… / remote-….
        placement = response.headers.get("cf-placement") or ""
        where = f" (cf-placement: {placement[:40]})" if placement else ""
        return _transient(
            "ai-region",
            f"Провайдер отклонил запрос по региону: Cloudflare выполнил Worker моста не в США{where}. Обычно это "
            "разовый сбой — повторите запрос. Если повторяется часто, проверьте мост: админка → ИИ и сервисы → "
            "Мост Cloudflare",
        )
    # AI Gateway выходит из ближайшего дата-центра — помогает свой Worker в США.
    return _transient(
        "ai-region",
        "Провайдер не принимает запросы из региона, через который прошёл мост. Переключите мост на "
        "«Свой Worker» в США: админка → ИИ и сервисы → Мост Cloudflare",
    )


def _error_code(response: httpx.Response) -> str:
    """Код ошибки из тела ответа (error.code или error.type), если он есть."""
    try:
        data = response.json()
    except ValueError:
        return ""
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return ""
    return str(err.get("code") or err.get("type") or "")


# Провайдер не отдаёт модель: неверный id, модель не подключена у источника или не открыта ключу.
_MODEL_MISSING = re.compile(
    r"model_not_found|no such model|unknown model|invalid model|model\b[^.]{0,80}\b(not found|does not exist|"
    r"not available|not supported|is not served)|модел[ьи]\b[^.]{0,80}(недоступн|не обслуживается|не найден|не поддерживается)",
    re.I,
)
# Кончились деньги или квота у провайдера: повтор не поможет, пока их не пополнят.
_NO_BALANCE = re.compile(
    r"credit balance is too low|insufficient[_ ](quota|balance|funds|credits)|quota is not enough|"
    r"not enough (credits|balance|quota)|недостаточно (средств|баланса|квоты)|пополните баланс",
    re.I,
)


def _raise_for_status(cfg: dict, response: httpx.Response) -> None:
    status = response.status_code
    if status >= 400:
        text = _error_text(response)
        name = cfg.get("model_name") or cfg.get("model") or ""
        if _NO_BALANCE.search(text):
            raise YandexError("ai-balance", f"У провайдера ИИ кончились деньги или квота (модель {name}): {text}")
        if status != 429 and (_error_code(response) == "model_not_found" or _MODEL_MISSING.search(text)):
            raise YandexError("ai-model", f"Провайдер ИИ не отдаёт модель {name}: {text}")
    if status in (401, 403):
        text = _error_text(response)
        if cfg.get("bridge") and "request not allowed" in text.lower():
            raise _region_error(cfg, response)
        who = "Провайдер ИИ или мост Cloudflare (ключ провайдера или токен моста)" if cfg.get("bridge") else "Провайдер ИИ"
        raise YandexError("ai-auth", f"{who} отклонил ключ: {text}")
    if status == 429:
        text = _error_text(response)
        message = f"Лимит запросов к ИИ: {text}"
        # «Закончились деньги» у OpenAI — тоже 429, но сам он не пройдёт.
        if any(word in text.lower() for word in ("quota", "billing", "balance")):
            raise YandexError("ai-limit", message)
        raise _transient("ai-limit", message, _retry_after(response))
    if status >= 400:
        where = " через мост Cloudflare" if cfg.get("bridge") else ""
        message = f"Ошибка ИИ{where} ({status}): {_error_text(response)}"
        raise _transient("ai-error", message) if status in RETRY_STATUSES else YandexError("ai-error", message)


# Родной API отклонил сам запрос (не ключ и не лимит): тот же запрос уходит OpenAI-совместимым путём.
NATIVE_REJECTED = {400, 404, 405, 422}


def _send(cfg: dict, url: str, body: dict, timeout: float, native: bool = False) -> dict:
    """Одна попытка → тело ответа в виде chat/completions. Ошибки — YandexError, временные помечены transient.

    native — родной /v1/messages Claude (url и body уже для него); его отказ в самом запросе помечен native_rejected.
    """
    try:
        if native:
            response, data = _post_anthropic(url, body, cfg["anthropic"]["headers"], timeout, bool(cfg.get("stream")))
        else:
            response, data = _post_stream(url, body, cfg["headers"], timeout) if cfg.get("stream") else (None, None)
            # Провайдер не знает stream / stream_options — спрашиваем обычным способом.
            if response is None or (response.status_code == 400 and "stream" in response.text.lower()):
                response = httpx.post(url, json=body, headers=cfg["headers"], timeout=timeout, follow_redirects=False)
                data = None
    except httpx.TimeoutException as exc:
        raise YandexError("ai-timeout", "ИИ не ответил за отведённое время") from exc
    except httpx.HTTPError as exc:
        # Соединение не установилось или оборвалось посреди ответа.
        raise _transient("ai-network", f"Не удалось связаться с ИИ: {exc.__class__.__name__}") from exc
    try:
        _raise_for_status(cfg, response)
    except YandexError as exc:
        if native and response.status_code in NATIVE_REJECTED:
            exc.native_rejected = True
        raise
    if data is None:
        try:
            data = response.json()
        except ValueError as exc:
            raise YandexError("ai-error", "Непонятный ответ ИИ") from exc
        if native:
            if not isinstance(data, dict) or not isinstance(data.get("content"), list):
                raise YandexError("ai-error", "Непонятный ответ ИИ")
            data = _from_anthropic(data["content"], data.get("usage"), str(data.get("model") or ""), str(data.get("stop_reason") or ""))
    return data


def _send_retrying(cfg: dict, url: str, body: dict, timeout: float, native: bool = False) -> dict:
    """_send с повтором временных сбоев (паузы RETRY_PAUSES)."""
    pauses = list(RETRY_PAUSES)
    attempt = 1
    while True:
        try:
            return _send(cfg, url, body, timeout, native)
        except YandexError as exc:
            wait = _retry_wait(exc, pauses)
            # Отказ родного API Claude в самом запросе тоже повторяется — через совместимый слой.
            rejected = bool(native and getattr(exc, "native_rejected", False))
            _note_attempt(cfg, exc, retried=wait is not None or rejected)
            if wait is None:
                if attempt > 1 and exc.message:
                    exc.message = f"{exc.message} (попыток: {attempt})"
                raise
            log.warning("ИИ: временный сбой %s (%s), повтор через %s с", exc.code, exc.message, wait)
            time.sleep(wait)
            attempt += 1


def _retry_wait(exc: YandexError, pauses: list) -> float | None:
    """Сколько ждать перед повтором; None — не повторять (ошибка не временная или попытки кончились)."""
    if not getattr(exc, "transient", False) or not pauses:
        return None
    wait = pauses.pop(0)
    after = getattr(exc, "retry_after", None)
    if after is not None:
        if after > RETRY_AFTER_MAX:
            return None
        wait = max(wait, after)
    return wait


# Модель не ответила по причине, которую повтор не исправит, — ход переходит на запасную.
FALLBACK_CODES = {"ai-model", "ai-balance", "ai-region", "ai-limit"}
# Сколько секунд не звать модель первой после такого отказа (до перезапуска процесса или удачного ответа).
MODEL_DOWN_SECONDS = 600
_down: dict[str, float] = {}


def _is_down(model: str) -> bool:
    until = _down.get(model)
    return bool(until and until > time.monotonic())


def _fallback_cfg(cfg: dict, entry: dict, *, reason: str = "", rest: list[dict] | None = None) -> dict:
    """cfg запасной модели entry (из cfg["fallbacks"]). Адрес, заголовки и мост — под её модель; настройки
    вызова (поток, таймаут, ключ кэша, пачка), класс и цена в единицах — исходного класса: пользователь
    платит за выбранный класс. Себестоимость — запасной модели. rest — что пробовать, если откажет и она."""
    fresh = load_config(
        cfg.get("user_id"), cfg.get("task") or "chat", cfg.get("workspace_id"),
        model_class=entry["id"], model=entry["model"],
    )
    keep = ("model_class", "class_name", "units_in", "units_out", "units_cache", "vision")
    return {
        **cfg,
        **fresh,
        **{k: cfg.get(k) for k in keep},
        "cost_in": entry.get("cost_in"),
        "cost_out": entry.get("cost_out"),
        "cost_cache": entry.get("cost_cache"),
        "fallbacks": list(rest or []),
        # Назначенная модель и причина подмены — для журнала расхода в админке.
        "fallback_from": cfg.get("fallback_from") or cfg.get("model_name") or "",
        "fallback_reason": reason[:300],
    }


def complete(cfg: dict, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Вызов модели с переходом на запасные (cfg["fallbacks"]), если провайдер её не отдаёт.

    Причины перехода — FALLBACK_CODES: неверный id или модель не подключена у источника, кончились деньги
    или квота, регион. Модель, которая так отказала, следующие MODEL_DOWN_SECONDS зовётся последней.
    Ответила запасная — в ответе "fallback": её cfg (для следующих шагов хода, с оставшимися запасными)
    и "fallback_from". В чате подмену не видно: класс и цена в единицах — выбранного класса.
    """
    attempts: list[dict] = []
    token = _attempts.set(attempts)
    answered_by = ""
    try:
        reply = _complete_chain(cfg, messages, tools, attempts)
        answered_by = (reply.get("fallback") or cfg).get("model_name") or cfg.get("model") or ""
        return reply
    finally:
        _attempts.reset(token)
        _save_attempts(cfg, attempts, answered_by)


def _complete_chain(cfg: dict, messages: list[dict], tools: list[dict] | None, attempts: list[dict]) -> dict:
    chain: list[dict | None] = [None, *(cfg.get("fallbacks") or [])]
    names = [cfg.get("model_name") or cfg.get("model") or "", *(f["model"] for f in chain[1:])]
    order = [i for i in range(len(chain)) if not _is_down(names[i])] + [i for i in range(len(chain)) if _is_down(names[i])]
    first: YandexError | None = None
    for n, i in enumerate(order):
        current = None
        try:
            if chain[i] is None:
                current = cfg
            else:
                why = first or YandexError("ai-model", f"модель {names[0]} недавно не отвечала")
                rest = [chain[k] for k in order[n + 1 :] if chain[k] is not None]
                current = _fallback_cfg(cfg, chain[i], reason=f"{names[0]}: {why.message or why.code}", rest=rest)
            reply = _complete_once(current, messages, tools)
        except YandexError as exc:
            # Отказ мимо _send_retrying (адрес API, непонятный ответ, мост запасной) — тоже в журнал попыток.
            _note_failure(current or {**cfg, "model_name": names[i]}, exc)
            final = exc.code not in FALLBACK_CODES or (exc.code == "ai-limit" and getattr(exc, "transient", False))
            if exc.code in ("ai-model", "ai-balance") or (exc.code == "ai-limit" and not final):
                _down[names[i]] = time.monotonic() + MODEL_DOWN_SECONDS
            if final or n == len(order) - 1:
                if first is not None:
                    raise YandexError(
                        first.code,
                        f"{first.message or first.code}. Запасная модель {names[i]} тоже не ответила: {exc.message or exc.code}",
                    ) from exc
                raise
            first = first or exc
            if attempts:
                attempts[-1]["fallback"] = True
            log.warning("ИИ: модель %s не ответила (%s), пробую запасную", names[i], exc.message or exc.code)
            continue
        _down.pop(names[i], None)
        if current is not cfg:
            reply["fallback"] = current
            reply["fallback_from"] = cfg.get("class_name") or ""
        return reply
    raise first or YandexError("ai-not-configured", NOT_CONFIGURED)


def _complete_once(cfg: dict, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Один вызов chat/completions → {"content", "tool_calls": [{"id", "name", "arguments"}], "usage"}.

    До вызова — проверка месячного лимита, после — запись расхода (cfg["units"] — объектов в пачке).
    cfg["stream"] — читать ответ потоком (чат: запросы большие, ответ долгий). Временные сбои повторяются
    с паузами RETRY_PAUSES; ответ модели при этом нигде не сохранён, поэтому повтор ничего не задваивает.
    """
    check_budget(cfg)
    if cfg.get("check_host"):
        _check_url_resolves_public(cfg["base_url"])
    body: dict = {"model": cfg["model"], "messages": messages}
    if cfg.get("temperature", 0.3) is not None:
        body["temperature"] = cfg.get("temperature", 0.3)
    if tools:
        body["tools"] = tools
    if cfg.get("openai_cache") and cfg.get("cache_key"):
        # Запросы одного чата — на один сервер OpenAI: там уже лежит кэш их общего начала.
        body["prompt_cache_key"] = cfg["cache_key"]
    url = f"{cfg['base_url']}/chat/completions"
    timeout = cfg.get("timeout") or TIMEOUT_SECONDS
    started = time.monotonic()
    data = None
    native = cfg.get("anthropic")
    if native:
        try:
            native_body = anthropic_body(
                messages, tools, native["model"], volatile_tail=int(cfg.get("volatile_tail") or 0)
            )
            data = _send_retrying(cfg, native["url"], native_body, timeout, native=True)
        except YandexError as exc:
            if not getattr(exc, "native_rejected", False):
                raise
            log.warning("ИИ: родной API Claude отклонил запрос (%s), повтор через совместимый", exc.message)
    if data is None:
        data = _send_retrying(cfg, url, body, timeout)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise YandexError("ai-error", "Непонятный ответ ИИ") from exc
    calls = []
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False)
        calls.append({"id": call.get("id") or f"call_{i}", "name": fn.get("name") or "", "arguments": args})
    content = message.get("content") or ""
    usage = _usage(cfg, data, messages, tools, content, calls)
    usage["duration_ms"] = int((time.monotonic() - started) * 1000)
    _record(cfg, usage)
    # Какая модель ответила по словам провайдера: бывает точнее настроенной (алиас → версия).
    answered_by = str(data.get("model") or "") if isinstance(data, dict) else ""
    return {"content": content, "tool_calls": calls, "usage": usage, "model": answered_by}


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
        # Повтор — та же пачка: объекты считаем один раз, чтобы не сбить калибровку оценки.
        reply = complete(cfg if attempt == 0 else {**cfg, "units": 0}, history)
        if reply.get("fallback"):
            cfg = reply["fallback"]
        data = parse_json_reply(reply["content"])
        if isinstance(data, dict):
            return data
        if attempt < retries:
            history += [
                {"role": "assistant", "content": reply["content"][:4000]},
                {"role": "user", "content": "Ответ не разобрался. Верни только JSON-объект по заданной схеме, без текста вокруг."},
            ]
    # Модель ответила (расход записан), но не по схеме: в ai_errors — без ответа; в кабинете — ERROR_FORMAT.
    exc = YandexError("ai-json", "ИИ вернул ответ не в том формате — попробуйте ещё раз или смените модель")
    _note_failure(cfg, exc)
    raise exc


def provider_models(cfg: dict) -> list[str]:
    """id моделей, которые отдаёт OpenAI-совместимый API (GET /models); не вышло — пусто."""
    if cfg.get("provider") != "openai":
        return []
    try:
        if cfg.get("check_host"):
            _check_url_resolves_public(cfg["base_url"])
        response = httpx.get(f"{cfg['base_url']}/models", headers=cfg["headers"], timeout=15, follow_redirects=False)
        data = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError, YandexError):
        return []
    items = data.get("data") if isinstance(data, dict) else None
    return [str(m["id"]) for m in items or [] if isinstance(m, dict) and m.get("id")]


def similar_models(cfg: dict, model: str, limit: int = 3) -> list[str]:
    """Похожие id из списка провайдера: claude-opus-5.5 → claude-opus-5-5."""
    ids = provider_models(cfg)
    if not ids or model in ids:
        return []

    def key(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower().rsplit("/", 1)[-1]).strip("-")

    by_key: dict[str, str] = {}
    for mid in ids:
        by_key.setdefault(key(mid), mid)
    exact = by_key.get(key(model))
    close = difflib.get_close_matches(key(model), list(by_key), n=limit, cutoff=0.75)
    out = [exact] if exact else []
    out += [by_key[k] for k in close if by_key[k] not in out]
    return out[:limit]


def check_connection() -> dict:
    """Пробный вызов каждого включённого класса — каждый со своим итогом. Без списания единиц.

    → {"ok": все ответили, "bridge", "models": [{"id", "label", "model", "sent", "answered_by", "reply" | "error"}]}.
    model — как в настройках, sent — что ушло провайдеру (через мост — с префиксом), answered_by — что он вернул.
    """
    ping = [{"role": "user", "content": "Ответь одним словом: готов"}]
    usable = [c for c in model_classes() if _usable(c)]
    if not usable:
        raise YandexError("ai-not-configured", "Включите хотя бы один класс модели и укажите модель")
    models, bridge = [], False
    # Модели классов, потом их резервы: (ключ, подпись, класс, модель; None — модель класса).
    tries = [(cls["id"], cls["name"], cls["id"], None) for cls in usable]
    tries += [
        (f"{cls['id']}:{n}", f"{cls['name']} · резерв {n}", cls["id"], r["model"])
        for cls in usable
        for n, r in enumerate(cls.get("reserves") or [], 1)
    ]
    for key, label, class_id, model in tries:
        cfg = load_config(None, task="test", model_class=class_id, model=model)
        bridge = bridge or cfg["bridge"]
        item = {"id": key, "label": label, "model": cfg["model_name"], "sent": cfg["model"]}
        try:
            reply = complete(cfg, ping)
            item.update(reply=(reply["content"] or "").strip()[:200], answered_by=reply.get("model") or "")
        except YandexError as exc:
            item["error"] = exc.message or exc.code
            if exc.code == "ai-model":
                similar = similar_models(cfg, cfg["model_name"])
                if similar:
                    item["similar"] = similar
                    item["error"] += f". Похожие модели у провайдера: {', '.join(similar)}"
        models.append(item)
    return {"ok": all("error" not in m for m in models), "bridge": bridge, "models": models}


# --- Калибровка оценок по прошлым вызовам всего сервиса ------------------------------------------


def chars_per_token(cfg: dict) -> float:
    """Сколько символов запроса приходится на токен у этой модели — по последним точным вызовам."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT SUM(chars_in) AS c, SUM(tokens_in) AS t FROM (
                SELECT chars_in, tokens_in FROM ai_usage
                WHERE kind = 'model' AND model = %s AND NOT estimated AND tokens_in > 0 AND chars_in > 0
                ORDER BY id DESC LIMIT 100
            ) x
            """,
            (cfg.get("model_name") or cfg.get("model"),),
        ).fetchone()
    if not row or not row["t"] or int(row["t"]) < 2000:
        return CHARS_PER_TOKEN
    return max(1.0, min(float(row["c"]) / float(row["t"]), 6.0))


def out_tokens_per_unit(cfg: dict, task: str, default: float) -> float:
    """Сколько токенов ответа уходит на один объект пачки (запрос в разметке) — по прошлым пачкам."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT SUM(tokens_out) AS t, SUM(units) AS u FROM (
                SELECT tokens_out, units FROM ai_usage
                WHERE kind = 'model' AND model = %s AND task = %s AND units > 0
                ORDER BY id DESC LIMIT 50
            ) x
            """,
            (cfg.get("model_name") or cfg.get("model"), task),
        ).fetchone()
    if not row or not row["u"] or int(row["u"]) < 20:
        return default
    return max(5.0, float(row["t"]) / float(row["u"]))
