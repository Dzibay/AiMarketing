"""Сайт tolk-ads.ru: контакты из админки, SmartCaptcha, лист ожидания и ИИ-режим генераторов (docs/landing-plan.md).

Контакты (почта поддержки, строка реквизитов, Telegram) задаются в админке и отдаются сайту через
GET /api/site/public — сайт берёт их при сборке и обновляет в браузере, пересобирать его не нужно. Почта
поддержки — та же legal_email, что подставляется в оферту и политику.

SmartCaptcha (Яндекс Облако) защищает формы сайта, которые тратят деньги или пишут в базу: живую проверку,
лист ожидания и ИИ-генераторы. Ключи — в админке; без серверного ключа капча выключена и формы работают по
одним лимитам. Сбой самого сервиса капчи посетителя не блокирует (так советует документация Яндекса).

Лист ожидания — почта и канал со страниц «скоро». IP не храним — только хэш, как в проверке сайта.

ИИ-генераторы минус-слов и объявлений — та же модель, что у проверки сайта, но свои выключатель и лимиты.
Расход пишется в ai_usage без пользователя (task = site_tools).
"""

from __future__ import annotations

import logging
import re

import httpx

from app import ai, site_check
from app.db import pool
from app.settings_store import get_setting, set_setting
from app.token_crypto import decrypt_token, encrypt_token
from app.yandex import YandexError

log = logging.getLogger(__name__)

# --- Контакты ------------------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
TG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
LEGAL_MAX = 400


def contacts() -> dict:
    return {
        "email": get_setting("legal_email").strip(),
        "legal": get_setting("site_legal_line").strip(),
        "telegram": get_setting("site_support_telegram").strip(),
    }


def save_contacts(*, email: str = "", legal: str = "", telegram: str = "") -> dict:
    email = (email or "").strip()
    legal = " ".join((legal or "").split())
    telegram = (telegram or "").strip().removeprefix("https://t.me/").lstrip("@")
    if email and not EMAIL_RE.match(email):
        raise YandexError("site-contacts", "Почта поддержки: похоже, адрес с опечаткой")
    if len(legal) > LEGAL_MAX:
        raise YandexError("site-contacts", f"Реквизиты: не длиннее {LEGAL_MAX} символов")
    if telegram and not TG_RE.match(telegram):
        raise YandexError("site-contacts", "Telegram: имя пользователя или канала, например tolk_support")
    set_setting("legal_email", email)
    set_setting("site_legal_line", legal)
    set_setting("site_support_telegram", telegram)
    return contacts()


# --- SmartCaptcha --------------------------------------------------------------------------------

CAPTCHA_URL = "https://smartcaptcha.yandexcloud.net/validate"
CAPTCHA_TEXT = "Подтвердите, что вы не робот, и попробуйте ещё раз"


def _captcha_server_key() -> str:
    cipher = get_setting("smartcaptcha_server_key_enc")
    if not cipher:
        return ""
    try:
        return decrypt_token(cipher)
    except RuntimeError:
        return ""


def captcha_client_key() -> str:
    """Ключ клиента отдаём сайту, только когда есть и серверный: иначе проверять нечем."""
    client = get_setting("smartcaptcha_client_key").strip()
    return client if client and _captcha_server_key() else ""


def captcha_settings() -> dict:
    return {
        "client_key": get_setting("smartcaptcha_client_key").strip(),
        "server_key_set": bool(_captcha_server_key()),
        "enabled": bool(captcha_client_key()),
    }


def save_captcha(*, client_key: str = "", server_key: str | None = None) -> dict:
    """server_key: None — не менять, "" — стереть, иначе — новый ключ (храним зашифрованным)."""
    set_setting("smartcaptcha_client_key", (client_key or "").strip()[:200])
    if server_key is not None:
        key = server_key.strip()[:200]
        set_setting("smartcaptcha_server_key_enc", encrypt_token(key) if key else "")
    return captcha_settings()


def verify_captcha(token: str, ip: str) -> None:
    """Капча выключена — пропускаем. Неверный токен — ошибка. Сервис капчи недоступен — пропускаем."""
    secret = _captcha_server_key()
    if not secret or not get_setting("smartcaptcha_client_key").strip():
        return
    token = (token or "").strip()
    if not token:
        raise YandexError("captcha", CAPTCHA_TEXT)
    try:
        resp = httpx.post(CAPTCHA_URL, data={"secret": secret, "token": token[:4000], "ip": ip or ""}, timeout=5)
    except httpx.HTTPError as exc:
        log.warning("SmartCaptcha недоступна: %s", exc)
        return
    if resp.status_code != 200:
        log.warning("SmartCaptcha ответила %s", resp.status_code)
        return
    try:
        status = resp.json().get("status")
    except ValueError:
        log.warning("SmartCaptcha: ответ не JSON")
        return
    if status != "ok":
        raise YandexError("captcha", CAPTCHA_TEXT)


# --- Лист ожидания -------------------------------------------------------------------------------

WAITLIST_CHANNELS = {"vk-reklama": "VK Реклама", "ozon": "Ozon", "wildberries": "Wildberries"}
WAITLIST_IP_DAILY = 10
WAITLIST_DONE = "Готово — напишем, как только запустим"


def waitlist_add(channel: str, email: str, ip: str) -> dict:
    channel = (channel or "").strip()
    email = (email or "").strip().lower()[:200]
    if channel not in WAITLIST_CHANNELS:
        raise YandexError("waitlist", "Такого раздела нет")
    if not EMAIL_RE.match(email):
        raise YandexError("waitlist", "Похоже, в адресе почты опечатка")
    who = site_check.ip_hash(ip)
    with pool.connection() as conn:
        mine = conn.execute(
            "SELECT COUNT(*) AS n FROM site_waitlist WHERE ip_hash = %s AND created_at >= date_trunc('day', NOW())",
            (who,),
        ).fetchone()
        if int(mine["n"]) >= WAITLIST_IP_DAILY:
            raise YandexError("too-soon", "Слишком много заявок с одного адреса — попробуйте завтра")
        conn.execute(
            """
            INSERT INTO site_waitlist (channel, email, ip_hash) VALUES (%s, %s, %s)
            ON CONFLICT (channel, email) DO NOTHING
            """,
            (channel, email, who),
        )
    return {"ok": True, "message": WAITLIST_DONE}


def waitlist_overview(limit: int = 200) -> dict:
    with pool.connection() as conn:
        counts = conn.execute("SELECT channel, COUNT(*) AS n FROM site_waitlist GROUP BY channel").fetchall()
        rows = conn.execute(
            "SELECT id, channel, email, created_at FROM site_waitlist ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
    by = {r["channel"]: int(r["n"]) for r in counts}
    return {
        "channels": [{"id": k, "label": v, "count": by.get(k, 0)} for k, v in WAITLIST_CHANNELS.items()],
        "items": [
            {
                "id": int(r["id"]),
                "channel": r["channel"],
                "label": WAITLIST_CHANNELS.get(r["channel"], r["channel"]),
                "email": r["email"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ],
    }


def waitlist_delete(item_id: int) -> dict:
    with pool.connection() as conn:
        conn.execute("DELETE FROM site_waitlist WHERE id = %s", (int(item_id),))
    return waitlist_overview()


# --- ИИ-генераторы -------------------------------------------------------------------------------

TOOLS_TASK = "site_tools"
TOOLS = ("minus", "ads")
TOOLS_DEFAULTS = {"ip_daily": 10, "daily": 500}
TOOLS_LIMITS = {"ip_daily": (1, 200), "daily": (1, 100_000)}
TOOLS_LIMIT_TEXT = "Сегодня бесплатные ИИ-подборки закончились — шаблонный режим работает без ограничений"
TOOLS_OFF_TEXT = "ИИ-режим сейчас выключен — шаблонный режим работает как обычно"
TOOLS_FAILED = "ИИ не ответил — попробуйте ещё раз чуть позже"
INPUT_MAX = 600
TITLE_MAX, TITLE2_MAX, TEXT_MAX = 56, 30, 81


def _tools_int(key: str) -> int:
    lo, hi = TOOLS_LIMITS[key]
    try:
        value = int(get_setting(f"site_tools_{key}", str(TOOLS_DEFAULTS[key])))
    except ValueError:
        value = TOOLS_DEFAULTS[key]
    return max(lo, min(value, hi))


def tools_settings() -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FILTER (WHERE created_at >= date_trunc('day', NOW())) AS today,
                   COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS week
            FROM site_tool_runs
            """
        ).fetchone()
    return {
        "enabled": get_setting("site_tools_enabled", "1") == "1",
        "ip_daily": _tools_int("ip_daily"),
        "daily": _tools_int("daily"),
        "stats": {"today": int(row["today"] or 0), "week": int(row["week"] or 0)},
    }


def save_tools(*, enabled: bool, ip_daily: int, daily: int) -> dict:
    labels = {"ip_daily": "Лимит на IP", "daily": "Лимит в сутки"}
    for key, value in (("ip_daily", ip_daily), ("daily", daily)):
        lo, hi = TOOLS_LIMITS[key]
        if not lo <= int(value) <= hi:
            raise YandexError("site-tools-settings", f"{labels[key]}: от {lo} до {hi}")
    set_setting("site_tools_enabled", "1" if enabled else "0")
    set_setting("site_tools_ip_daily", str(int(ip_daily)))
    set_setting("site_tools_daily", str(int(daily)))
    return tools_settings()


def _take_slot(tool: str, ip: str) -> None:
    if get_setting("site_tools_enabled", "1") != "1":
        raise YandexError("site-tools-off", TOOLS_OFF_TEXT)
    who = site_check.ip_hash(ip)
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('site_tools'))")
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE ip_hash = %s) AS mine
            FROM site_tool_runs WHERE created_at >= date_trunc('day', NOW())
            """,
            (who,),
        ).fetchone()
        if int(counts["mine"]) >= _tools_int("ip_daily") or int(counts["total"]) >= _tools_int("daily"):
            raise YandexError("too-soon", TOOLS_LIMIT_TEXT)
        conn.execute("INSERT INTO site_tool_runs (tool, ip_hash) VALUES (%s, %s)", (tool, who))


def _clean(text: str, limit: int = INPUT_MAX) -> str:
    return " ".join(str(text or "").split())[:limit]


_MINUS_SYSTEM = """Ты — специалист по Яндекс Директу. По описанию бизнеса предложи минус-слова: запросы, по которым кликнут, но не купят у этого бизнеса.
Группы: «Не покупают» (бесплатно, своими руками, скачать…), «Работа и учёба», «Не тот товар или услуга» (смежное, чего у бизнеса нет), «Не тот город или формат», «Информационные».
Только то, что точно не подходит именно этому бизнесу; не минусуй то, что он продаёт. По одному слову или короткой фразе (до 3 слов), в нижнем регистре, без операторов.
Ответ — только JSON: {"groups": [{"name": "...", "words": ["...", "..."]}]}, 3–5 групп, в каждой 5–15 слов."""

_ADS_SYSTEM = f"""Ты — специалист по Яндекс Директу. По описанию товара и преимуществам напиши варианты объявления.
- titles — 7 заголовков, каждый не длиннее {TITLE_MAX} символов с пробелами;
- titles2 — 3 дополнительных заголовка, не длиннее {TITLE2_MAX} символов;
- texts — 3 текста, не длиннее {TEXT_MAX} символов, с призывом к действию.
Используй только факты из описания, ничего не придумывай (цены, сроки, гарантии — только если они даны). Без восклицательных знаков подряд и без капслока.
Ответ — только JSON: {{"titles": ["..."], "titles2": ["..."], "texts": ["..."]}}"""


def _fit(items, limit: int, count: int) -> list[str]:
    raw = site_check._clean_list(items, 30, 300)
    out = [x for x in raw if len(x) <= limit]
    for x in raw:
        if len(out) >= count:
            break
        if len(x) > limit:
            cut = site_check._cut(x, limit)
            if cut and cut not in out:
                out.append(cut)
    return out[:count]


def normalize_minus(data: dict) -> dict:
    groups = []
    seen: set[str] = set()
    for g in (data or {}).get("groups") if isinstance((data or {}).get("groups"), list) else []:
        if not isinstance(g, dict):
            continue
        name = _clean(g.get("name"), 60)
        words = []
        for w in site_check._clean_list(g.get("words"), 30, 40):
            key = w.lower().replace("ё", "е")
            if key not in seen and len(w.split()) <= 3:
                seen.add(key)
                words.append(w.lower())
        if name and words:
            groups.append({"name": name, "words": words})
        if len(groups) >= 6:
            break
    return {"groups": groups}


def normalize_ads(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}
    return {
        "titles": _fit(data.get("titles"), TITLE_MAX, 7),
        "titles2": _fit(data.get("titles2"), TITLE2_MAX, 3),
        "texts": _fit(data.get("texts"), TEXT_MAX, 3),
    }


def generate(tool: str, fields: dict, ip: str) -> dict:
    if tool not in TOOLS:
        raise YandexError("not-found", "Такого инструмента нет")
    fields = fields if isinstance(fields, dict) else {}
    if tool == "minus":
        about = _clean(fields.get("about"))
        if len(about) < 3:
            raise YandexError("site-tools", "Опишите бизнес хотя бы парой слов: что продаёте и где")
        system, user = _MINUS_SYSTEM, f"Бизнес: {about}"
    else:
        product = _clean(fields.get("product"), 120)
        if len(product) < 2:
            raise YandexError("site-tools", "Напишите, что рекламируете")
        parts = [f"Товар или услуга: {product}"]
        for key, label in (("where", "Где"), ("pros", "Преимущества"), ("cta", "Призыв")):
            if _clean(fields.get(key)):
                parts.append(f"{label}: {_clean(fields.get(key))}")
        system, user = _ADS_SYSTEM, "\n".join(parts)
    _take_slot(tool, ip)
    try:
        cfg = ai.load_config(None, TOOLS_TASK, model=site_check.get_settings()["model"] or None)
        data = ai.complete_json(cfg, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    except YandexError as exc:
        log.warning("ИИ-генератор %s: %s %s", tool, exc.code, exc.message)
        raise YandexError("site-tools-failed", TOOLS_FAILED) from exc
    result = normalize_minus(data) if tool == "minus" else normalize_ads(data)
    if not any(result.values()) or (tool == "minus" and not result["groups"]):
        raise YandexError("site-tools-failed", TOOLS_FAILED)
    return result


# --- Для сайта -----------------------------------------------------------------------------------


def public() -> dict:
    """Всё, что статичному сайту нужно знать из админки, одним запросом."""
    return {
        **contacts(),
        "captcha_key": captcha_client_key(),
        "ai_tools": get_setting("site_tools_enabled", "1") == "1",
    }
