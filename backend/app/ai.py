"""ИИ пользователя (BYOK): настройки провайдера и вызов OpenAI-совместимого chat/completions.

Поддерживаются OpenAI и любые совместимые API (прокси, локальные шлюзы с публичным https-адресом)
и YandexGPT через его OpenAI-совместимый эндпоинт Yandex Cloud.

Модель на задачу: умная — чат с инструментами, быстрая — массовые задачи (разметка запросов, бриф).
Каждый вызов пишется в ai_usage (токены от провайдера или оценка по символам, стоимость по ценам
из настроек); месячный лимит в рублях останавливает вызовы, когда он исчерпан.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import socket
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.clock import MSK, today_msk
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

# Какая модель на какую задачу: smart — основная, fast — «быстрая» из настроек (пусто — та же).
TASKS = {
    "chat": {"label": "ИИ-чат", "tier": "smart"},
    "classify": {"label": "Разметка запросов", "tier": "fast"},
    "brief": {"label": "Черновик брифа", "tier": "fast"},
    "placements": {"label": "Разбор площадок РСЯ", "tier": "fast"},
    "ads": {"label": "Тексты объявлений", "tier": "smart"},
    "test": {"label": "Проверка подключения", "tier": "smart"},
    # Картинки рисует свой провайдер (image_gen); здесь — только подпись в расходе.
    "images": {"label": "Картинки объявлений", "tier": "smart"},
}
# Проверку подключения лимит не останавливает: иначе после исчерпания не проверить новый ключ.
UNLIMITED_TASKS = {"test"}
# Русский текст у большинства токенизаторов — 2–3 символа на токен; дальше калибруемся по факту.
CHARS_PER_TOKEN = 2.5
PRICE_MAX = 1_000_000
_KEEP = object()


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
    text = f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return (text[:-3] if text.endswith(",00") else text) + "\u00a0₽"


def _public(row: dict | None) -> dict:
    row = row or {}
    provider = row.get("provider") or "openai"
    has_key = bool(row.get("api_key_enc"))
    configured = has_key and (provider != "yandexgpt" or bool(row.get("folder_id")))
    return {
        "provider": provider,
        "base_url": row.get("base_url") or "",
        "model": row.get("model") or "",
        "model_fast": row.get("model_fast") or "",
        "folder_id": row.get("folder_id") or "",
        "temperature": float(row["temperature"]) if row.get("temperature") is not None else 0.3,
        "price_in": _num(row.get("price_in")),
        "price_out": _num(row.get("price_out")),
        "fast_price_in": _num(row.get("fast_price_in")),
        "fast_price_out": _num(row.get("fast_price_out")),
        "monthly_limit_rub": _num(row.get("monthly_limit_rub")),
        "has_key": has_key,
        "key_hint": _key_hint(row.get("api_key_enc") or ""),
        "configured": configured,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "providers": [{"id": k, "label": v["label"], "default_model": v["default_model"]} for k, v in PROVIDERS.items()],
        "tasks": [{"id": k, "label": v["label"], "tier": v["tier"]} for k, v in TASKS.items() if k not in ("test", "images")],
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


def _money(value, what: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        amount = float(str(value).replace(",", ".").replace(" ", ""))
    except ValueError as exc:
        raise YandexError("ai-price", f"{what} — число в рублях") from exc
    if math.isnan(amount) or not 0 <= amount <= PRICE_MAX:
        raise YandexError("ai-price", f"{what} — от 0 до {PRICE_MAX:,} ₽".replace(",", " "))
    return round(amount, 2)


def save_settings(
    user_id: int,
    *,
    provider: str,
    base_url: str = "",
    model: str = "",
    folder_id: str = "",
    api_key: str | None = None,
    temperature: float | None = None,
    model_fast=_KEEP,
    price_in=_KEEP,
    price_out=_KEEP,
    fast_price_in=_KEEP,
    fast_price_out=_KEEP,
    monthly_limit_rub=_KEEP,
) -> dict:
    """api_key: None — оставить прежний, "" — удалить. Быстрая модель, цены и лимит: не переданы — как были.

    Цены — рубли за 1 млн токенов (вход и выход), у быстрой модели пусто — как у основной.
    """
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
    current = _row(user_id) or {}
    if api_key is None:
        key_enc = current.get("api_key_enc") or ""
    else:
        key_enc = encrypt_token(api_key.strip()) if api_key.strip() else ""

    def pick(name: str, value, what: str):
        return _num(current.get(name)) if value is _KEEP else _money(value, what)

    fast = (current.get("model_fast") or "") if model_fast is _KEEP else (model_fast or "").strip()[:200]
    if fast == model:
        fast = ""
    p_in, p_out = pick("price_in", price_in, "Цена входа"), pick("price_out", price_out, "Цена выхода")
    f_in = pick("fast_price_in", fast_price_in, "Цена входа быстрой модели")
    f_out = pick("fast_price_out", fast_price_out, "Цена выхода быстрой модели")
    limit = pick("monthly_limit_rub", monthly_limit_rub, "Месячный лимит")
    for a, b, who in ((p_in, p_out, "модели"), (f_in, f_out, "быстрой модели")):
        if (a is None) != (b is None):
            raise YandexError("ai-price", f"Укажите обе цены {who}: за входные и за выходные токены")
    if limit is not None and limit <= 0:
        raise YandexError("ai-price", "Месячный лимит — сумма больше нуля или пусто")
    if limit is not None and p_in is None:
        raise YandexError("ai-price", "Чтобы считать месячный лимит, укажите цены модели")
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO ai_settings (user_id, provider, base_url, model, model_fast, folder_id, api_key_enc,
                                     temperature, price_in, price_out, fast_price_in, fast_price_out,
                                     monthly_limit_rub, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                provider = EXCLUDED.provider, base_url = EXCLUDED.base_url, model = EXCLUDED.model,
                model_fast = EXCLUDED.model_fast, folder_id = EXCLUDED.folder_id,
                api_key_enc = EXCLUDED.api_key_enc, temperature = EXCLUDED.temperature,
                price_in = EXCLUDED.price_in, price_out = EXCLUDED.price_out,
                fast_price_in = EXCLUDED.fast_price_in, fast_price_out = EXCLUDED.fast_price_out,
                monthly_limit_rub = EXCLUDED.monthly_limit_rub, updated_at = NOW()
            """,
            (user_id, provider, base_url, model, fast, folder_id, key_enc, temp, p_in, p_out, f_in, f_out, limit),
        )
    return get_settings(user_id)


def load_config(user_id: int, task: str = "chat", workspace_id: int | None = None, *, tier: str | None = None) -> dict:
    """Готовая конфигурация вызова задачи: адрес, модель её уровня, заголовки, цены и лимит.

    Ошибка, если ИИ не подключён. tier — принудительно "smart" / "fast" (проверка обеих моделей).
    """
    row = _row(user_id)
    if not row or not row.get("api_key_enc"):
        raise YandexError("ai-not-configured", "Подключите ИИ: Настройки → ИИ-ассистент")
    try:
        key = decrypt_token(row["api_key_enc"])
    except RuntimeError as exc:
        raise YandexError("ai-not-configured", "Ключ ИИ не расшифровывается — сохраните его заново") from exc
    provider = row["provider"]
    tier = tier or (TASKS.get(task) or TASKS["chat"])["tier"]
    fast = tier == "fast" and bool(row.get("model_fast"))
    model = (row.get("model_fast") if fast else row.get("model")) or PROVIDERS[provider]["default_model"]
    model_name = model
    # Без своих цен быстрая модель считается по ценам основной — оценка с запасом, а не «бесплатно».
    has_fast_prices = fast and row.get("fast_price_in") is not None
    prices = ("fast_price_in", "fast_price_out") if has_fast_prices else ("price_in", "price_out")
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
        "user_id": user_id,
        "workspace_id": workspace_id,
        "task": task,
        "tier": "fast" if fast else "smart",
        "model_name": model_name,
        "price_in": _num(row.get(prices[0])),
        "price_out": _num(row.get(prices[1])),
        "limit_rub": None if task in UNLIMITED_TASKS else _num(row.get("monthly_limit_rub")),
    }


# --- Учёт расхода и лимит -----------------------------------------------------------------------

_meters: ContextVar[tuple] = ContextVar("ai_meters", default=())


@contextmanager
def meter():
    """Сколько потратили вызовы внутри блока: {"calls", "tokens_in", "tokens_out", "cost_rub", "priced"}."""
    acc = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0, "priced": True, "estimated": False}
    token = _meters.set((*_meters.get(), acc))
    try:
        yield acc
    finally:
        _meters.reset(token)


def cost_rub(cfg: dict, tokens_in: float, tokens_out: float) -> float | None:
    if cfg.get("price_in") is None or cfg.get("price_out") is None:
        return None
    return (tokens_in * cfg["price_in"] + tokens_out * cfg["price_out"]) / 1_000_000


def month_start(year: int | None = None, month: int | None = None) -> datetime:
    today = today_msk()
    return datetime(year or today.year, month or today.month, 1, tzinfo=MSK)


def spent_this_month(user_id: int) -> float:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_rub), 0) AS s FROM ai_usage WHERE user_id = %s AND created_at >= %s",
            (user_id, month_start()),
        ).fetchone()
    return float(row["s"])


def budget_left(cfg: dict) -> float | None:
    """Остаток месячного лимита в рублях; None — лимита нет."""
    if cfg.get("limit_rub") is None or not cfg.get("user_id"):
        return None
    return cfg["limit_rub"] - spent_this_month(cfg["user_id"])


def check_budget(cfg: dict) -> None:
    left = budget_left(cfg)
    if left is not None and left <= 0:
        raise YandexError(
            "ai-budget",
            f"Месячный лимит на ИИ исчерпан ({format_rub(cfg['limit_rub'])}). Поднять его — Настройки → ИИ-ассистент",
        )


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
        "cost_rub": cost_rub(cfg, tokens_in, tokens_out),
    }


def _record(cfg: dict, usage: dict) -> None:
    """Запись вызова в ai_usage и в открытые meter(). Ошибка учёта не роняет ответ модели."""
    for acc in _meters.get():
        acc["calls"] += 1
        acc["tokens_in"] += usage["tokens_in"]
        acc["tokens_out"] += usage["tokens_out"]
        acc["estimated"] = acc["estimated"] or usage["estimated"]
        if usage["cost_rub"] is None:
            acc["priced"] = False
        else:
            acc["cost_rub"] += usage["cost_rub"]
    if not cfg.get("user_id"):
        return
    try:
        with pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO ai_usage (user_id, workspace_id, task, model, tokens_in, tokens_out,
                                      chars_in, chars_out, units, estimated, cost_rub)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    cfg["user_id"],
                    cfg.get("workspace_id"),
                    cfg.get("task") or "chat",
                    cfg.get("model_name") or cfg.get("model") or "",
                    usage["tokens_in"],
                    usage["tokens_out"],
                    usage["chars_in"],
                    usage["chars_out"],
                    int(cfg.get("units") or 0),
                    usage["estimated"],
                    usage["cost_rub"],
                ),
            )
    except Exception:  # noqa: BLE001 — учёт вторичен, ответ модели уже получен
        log.exception("Не удалось записать расход ИИ")


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
    """Один вызов chat/completions → {"content", "tool_calls": [{"id", "name", "arguments"}], "usage"}.

    До вызова — проверка месячного лимита, после — запись расхода (cfg["units"] — объектов в пачке).
    """
    check_budget(cfg)
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
    content = message.get("content") or ""
    usage = _usage(cfg, data, messages, tools, content, calls)
    _record(cfg, usage)
    return {"content": content, "tool_calls": calls, "usage": usage}


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


def check_connection(user_id: int) -> dict:
    """Пробный вызов основной модели и, если задана отдельно, быстрой."""
    ping = [{"role": "user", "content": "Ответь одним словом: готов"}]
    cfg = load_config(user_id, task="test")
    reply = complete(cfg, ping)
    out = {"ok": True, "model": cfg["model"], "reply": (reply["content"] or "").strip()[:200]}
    fast = load_config(user_id, task="test", tier="fast")
    if fast["model"] != cfg["model"]:
        try:
            out["fast"] = {"model": fast["model"], "reply": (complete(fast, ping)["content"] or "").strip()[:200]}
        except YandexError as exc:
            out["fast"] = {"model": fast["model"], "error": exc.message or exc.code}
    return out


# --- Сводка и оценка ----------------------------------------------------------------------------


def _month_bounds(month: str | None) -> tuple[str, datetime, datetime]:
    today = today_msk()
    year, mon = today.year, today.month
    if month:
        try:
            year, mon = (int(x) for x in month.split("-", 1))
            if not (2000 <= year <= 2100 and 1 <= mon <= 12):
                raise ValueError
        except ValueError as exc:
            raise YandexError("ai-month", "Месяц в формате ГГГГ-ММ") from exc
    start = month_start(year, mon)
    end = month_start(year + mon // 12, mon % 12 + 1)
    return f"{year:04d}-{mon:02d}", start, end


def _usage_item(row: dict) -> dict:
    return {
        "calls": int(row["calls"] or 0),
        "tokens_in": int(row["tokens_in"] or 0),
        "tokens_out": int(row["tokens_out"] or 0),
        "cost_rub": _num(row["cost_rub"]),
    }


def usage_summary(user_id: int, month: str | None = None) -> dict:
    """Расход за календарный месяц по Москве: итог, по задачам, пространствам и моделям."""
    key, start, end = _month_bounds(month)
    this_month = _month_bounds(None)[0]
    agg = """COUNT(*) AS calls, SUM(u.tokens_in) AS tokens_in, SUM(u.tokens_out) AS tokens_out,
             SUM(u.cost_rub) AS cost_rub"""
    where = "u.user_id = %s AND u.created_at >= %s AND u.created_at < %s"
    order = "ORDER BY SUM(u.tokens_in + u.tokens_out) DESC"
    params = (user_id, start, end)
    with pool.connection() as conn:
        total = conn.execute(
            f"""
            SELECT {agg}, COUNT(*) FILTER (WHERE u.estimated) AS estimated,
                   COUNT(*) FILTER (WHERE u.cost_rub IS NULL) AS unpriced
            FROM ai_usage u WHERE {where}
            """,
            params,
        ).fetchone()
        by_task = conn.execute(f"SELECT u.task, {agg} FROM ai_usage u WHERE {where} GROUP BY u.task {order}", params).fetchall()
        by_ws = conn.execute(
            f"""
            SELECT u.workspace_id, w.name, {agg}
            FROM ai_usage u LEFT JOIN workspaces w ON w.id = u.workspace_id
            WHERE {where} GROUP BY u.workspace_id, w.name {order}
            """,
            params,
        ).fetchall()
        by_model = conn.execute(f"SELECT u.model, {agg} FROM ai_usage u WHERE {where} GROUP BY u.model {order}", params).fetchall()
        months = conn.execute(
            """
            SELECT DISTINCT to_char(created_at AT TIME ZONE 'Europe/Moscow', 'YYYY-MM') AS m
            FROM ai_usage WHERE user_id = %s ORDER BY m DESC LIMIT 12
            """,
            (user_id,),
        ).fetchall()
    settings = _row(user_id) or {}
    limit = _num(settings.get("monthly_limit_rub"))
    current = key == this_month
    spent = _num(total["cost_rub"]) or 0.0
    return {
        "month": key,
        "months": sorted({key, this_month, *(r["m"] for r in months)}, reverse=True),
        "current": current,
        "total": {**_usage_item(total), "estimated": int(total["estimated"]), "unpriced": int(total["unpriced"])},
        "limit_rub": limit,
        "spent_rub": round(spent, 2),
        "remaining_rub": round(limit - spent, 2) if limit is not None and current else None,
        "priced": settings.get("price_in") is not None,
        "by_task": [
            {"task": r["task"], "label": (TASKS.get(r["task"]) or {}).get("label") or r["task"], **_usage_item(r)}
            for r in by_task
        ],
        "by_workspace": [
            {
                "workspace_id": r["workspace_id"],
                "name": r["name"] or ("Без пространства" if r["workspace_id"] is None else "Удалённое пространство"),
                **_usage_item(r),
            }
            for r in by_ws
        ],
        "by_model": [{"model": r["model"] or "—", **_usage_item(r)} for r in by_model],
    }


def chars_per_token(cfg: dict) -> float:
    """Сколько символов запроса приходится на токен у этой модели — по последним точным вызовам."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT SUM(chars_in) AS c, SUM(tokens_in) AS t FROM (
                SELECT chars_in, tokens_in FROM ai_usage
                WHERE user_id = %s AND model = %s AND NOT estimated AND tokens_in > 0 AND chars_in > 0
                ORDER BY id DESC LIMIT 100
            ) x
            """,
            (cfg.get("user_id"), cfg.get("model_name") or cfg.get("model")),
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
                WHERE user_id = %s AND model = %s AND task = %s AND units > 0
                ORDER BY id DESC LIMIT 50
            ) x
            """,
            (cfg.get("user_id"), cfg.get("model_name") or cfg.get("model"), task),
        ).fetchone()
    if not row or not row["u"] or int(row["u"]) < 20:
        return default
    return max(5.0, float(row["t"]) / float(row["u"]))
