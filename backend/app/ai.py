"""ИИ сервиса: общее подключение (настраивает администратор) и вызов OpenAI-совместимого chat/completions.

Поддерживаются YandexGPT (OpenAI-совместимый эндпоинт Yandex Cloud) и любые OpenAI-совместимые API
с публичным https-адресом — к зарубежным через мост Cloudflare (ai_bridge).

Модели — три класса: быстрая, стандартная, думающая (ai_model_classes). Чат работает классом, который
выбрал пользователь; фоновые задачи (разметка запросов, бриф, площадки) — классом из настроек сервиса.
Каждый вызов списывает единицы по цене класса (за 1 млн токенов входа и выхода) и пишется в ai_usage
вместе с себестоимостью в рублях; перед вызовом — проверка лимита единиц пользователя (billing).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
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
}
# Подписи задач в журнале расхода, включая проверку подключения и картинки.
TASK_LABELS = {**{k: v["label"] for k, v in TASKS.items()}, "chat": "ИИ-чат", "test": "Проверка подключения", "images": "Картинки"}
# Русский текст у большинства токенизаторов — 2–3 символа на токен; дальше калибруемся по факту.
CHARS_PER_TOKEN = 2.5
PRICE_MAX = 1_000_000
NOT_CONFIGURED = "ИИ временно недоступен: администратор ещё не подключил модель. Напишите в поддержку"


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
        "enabled": bool(row.get("enabled")),
        "dots": CLASS_DOTS.get(row["id"], 2),
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


def _resolve_class(wanted: str | None, task: str, classes: dict[str, dict], row: dict) -> dict | None:
    """Класс для вызова: выбранный, если включён и с моделью, иначе класс задачи, иначе ближайший рабочий."""
    order = [wanted, _task_classes(row).get(task) or TASKS["chat"]["class"], "standard", "fast", "thinking"]
    for cid in order:
        if cid and _usable(classes.get(cid)):
            return classes[cid]
    return None


def chat_classes() -> dict:
    """Для выбора модели в чате: включённые классы без моделей и цен и класс по умолчанию."""
    row = _service()
    classes = {c["id"]: c for c in model_classes()}
    default = _resolve_class(None, "chat", classes, row)
    return {
        "configured": _has_key(row) and default is not None,
        "default": default["id"] if default else "",
        "items": [{"id": c["id"], "name": c["name"], "dots": c["dots"]} for c in classes.values() if _usable(c)],
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
        enabled = bool(item.get("enabled"))
        for a, b, what in ((units_in, units_out, "цены в единицах"), (cost_in, cost_out, "себестоимости")):
            if (a is None) != (b is None):
                raise YandexError("ai-price", f"{name}: укажите обе {what} — за вход и за выход")
        if enabled and not model:
            raise YandexError("ai-class", f"{name}: укажите модель или выключите класс")
        conn.execute(
            """
            UPDATE ai_model_classes SET name = %s, model = %s, units_in = %s, units_out = %s, cost_in = %s,
                   cost_out = %s, enabled = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (name, model, units_in, units_out, cost_in, cost_out, enabled, cid),
        )


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
    return get_service_settings()


def load_config(
    user_id: int | None, task: str = "chat", workspace_id: int | None = None, *, model_class: str | None = None
) -> dict:
    """Готовая конфигурация вызова: адрес, модель класса, заголовки и цены класса.

    user_id — с кого списывать единицы (None — проверка в админке, без списания). model_class — выбор
    пользователя в чате; выключенный или пустой класс заменяется классом задачи.
    """
    row = _service()
    if not _has_key(row):
        raise YandexError("ai-not-configured", NOT_CONFIGURED)
    try:
        key = decrypt_token(row["api_key_enc"])
    except RuntimeError as exc:
        raise YandexError("ai-not-configured", NOT_CONFIGURED) from exc
    classes = {c["id"]: c for c in model_classes()}
    cls = _resolve_class(model_class, task, classes, row)
    if cls is None:
        raise YandexError("ai-not-configured", NOT_CONFIGURED)
    provider = row["provider"]
    model = cls["model"]
    model_name = model
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "yandexgpt":
        folder = row["folder_id"]
        base = YANDEX_BASE
        if not model.startswith("gpt://"):
            model = f"gpt://{folder}/{model.lstrip('/')}"
        headers["OpenAI-Project"] = folder
    else:
        base = row.get("base_url") or OPENAI_BASE
    bridge = ai_bridge.target("chat", model) if provider == "openai" else None
    temperature = float(row["temperature"]) if row.get("temperature") is not None else 0.3
    if bridge:
        # Через мост Cloudflare: свой адрес API не используется, модель — в виде, понятном мосту.
        base = bridge["base_url"]
        headers.update(bridge["headers"])
        model = bridge["model"]
        if bridge["anthropic"]:
            # OpenAI-совместимый слой Anthropic отвечает 400 на temperature ниже 1 у новых Claude.
            temperature = None
    return {
        "provider": provider,
        "base_url": base,
        "model": model,
        "headers": headers,
        "bridge": bool(bridge),
        "bridge_kind": bridge["kind"] if bridge else "",
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


def units_for(cfg: dict, tokens_in: float, tokens_out: float) -> float:
    """Единиц за вызов по цене класса модели; цена не задана — 0."""
    if cfg.get("units_in") is None or cfg.get("units_out") is None:
        return 0.0
    return (tokens_in * cfg["units_in"] + tokens_out * cfg["units_out"]) / 1_000_000


def cost_rub(cfg: dict, tokens_in: float, tokens_out: float) -> float | None:
    """Себестоимость вызова в рублях; None — не задана."""
    if cfg.get("cost_in") is None or cfg.get("cost_out") is None:
        return None
    return (tokens_in * cfg["cost_in"] + tokens_out * cfg["cost_out"]) / 1_000_000


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


def estimate_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """Размер запроса в токенах по символам — для сообщений об ошибке, когда ответа с usage нет."""
    chars = len(json.dumps(messages, ensure_ascii=False))
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return math.ceil(chars / CHARS_PER_TOKEN)


def _usage(cfg: dict, data: dict, messages: list[dict], tools, content: str, calls: list[dict]) -> dict:
    chars_in = len(json.dumps(messages, ensure_ascii=False))
    if tools:
        chars_in += len(json.dumps(tools, ensure_ascii=False))
    chars_out = len(content) + sum(len(c["name"]) + len(c["arguments"]) for c in calls)
    raw = data.get("usage") if isinstance(data, dict) else None
    try:
        tokens_in, tokens_out = int(raw["prompt_tokens"]), int(raw.get("completion_tokens") or 0)
        estimated = False
    except (TypeError, KeyError, ValueError, AttributeError):
        tokens_in = math.ceil(chars_in / CHARS_PER_TOKEN)
        tokens_out = math.ceil(chars_out / CHARS_PER_TOKEN)
        estimated = True
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "chars_in": chars_in,
        "chars_out": chars_out,
        "estimated": estimated,
        "charged": units_for(cfg, tokens_in, tokens_out),
        "cost_rub": cost_rub(cfg, tokens_in, tokens_out),
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
        chars_in=usage["chars_in"],
        chars_out=usage["chars_out"],
        units=int(cfg.get("units") or 0),
        estimated=usage["estimated"],
        charged=usage["charged"],
        cost_rub=usage["cost_rub"],
    )


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
                raise (_transient if overloaded else YandexError)("ai-error", f"Ошибка ИИ: {text}")
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


def _raise_for_status(cfg: dict, response: httpx.Response) -> None:
    status = response.status_code
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


def _send(cfg: dict, url: str, body: dict, timeout: float) -> dict:
    """Одна попытка chat/completions → тело ответа. Ошибки — YandexError, временные помечены transient."""
    try:
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
    _raise_for_status(cfg, response)
    if data is None:
        try:
            data = response.json()
        except ValueError as exc:
            raise YandexError("ai-error", "Непонятный ответ ИИ") from exc
    return data


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


def complete(cfg: dict, messages: list[dict], tools: list[dict] | None = None) -> dict:
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
    url = f"{cfg['base_url']}/chat/completions"
    timeout = cfg.get("timeout") or TIMEOUT_SECONDS
    pauses = list(RETRY_PAUSES)
    attempt = 1
    while True:
        try:
            data = _send(cfg, url, body, timeout)
            break
        except YandexError as exc:
            wait = _retry_wait(exc, pauses)
            if wait is None:
                if attempt > 1 and exc.message:
                    exc.message = f"{exc.message} (попыток: {attempt})"
                raise
            log.warning("ИИ: временный сбой %s (%s), повтор через %s с", exc.code, exc.message, wait)
            time.sleep(wait)
            attempt += 1
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
        data = parse_json_reply(reply["content"])
        if isinstance(data, dict):
            return data
        if attempt < retries:
            history += [
                {"role": "assistant", "content": reply["content"][:4000]},
                {"role": "user", "content": "Ответ не разобрался. Верни только JSON-объект по заданной схеме, без текста вокруг."},
            ]
    raise YandexError("ai-json", "ИИ вернул ответ не в том формате — попробуйте ещё раз или смените модель")


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
    for cls in usable:
        cfg = load_config(None, task="test", model_class=cls["id"])
        bridge = bridge or cfg["bridge"]
        item = {"id": cls["id"], "label": cls["name"], "model": cfg["model_name"], "sent": cfg["model"]}
        try:
            reply = complete(cfg, ping)
            item.update(reply=(reply["content"] or "").strip()[:200], answered_by=reply.get("model") or "")
        except YandexError as exc:
            item["error"] = exc.message or exc.code
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
