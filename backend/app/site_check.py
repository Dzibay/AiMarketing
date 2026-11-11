"""Живая проверка сайта на лендинге без регистрации (docs/landing-plan.md, раздел 5).

Посетитель вставляет адрес → POST /api/site/check создаёт проверку и запускает её в фоне → лендинг опрашивает
GET /api/site/check/{id}. Проверка читает главную страницу (brief.fetch_site — с защитой от SSRF) и просит ИИ
описать нишу и оффер, предложить заголовки и минус-слова.

ИИ — общий ключ и адрес API из админки. Модель для проверки задаётся отдельно (site_check_model); пусто —
модель класса задачи «Проверка сайта на лендинге». Цены и себестоимость — того же класса; расход пишется в
ai_usage без пользователя (task = site_check).

Защита от злоупотреблений: выключатель, лимит на IP и общий дневной лимит, кэш по домену (повтор того же
сайта бесплатен и в лимиты не входит). IP не храним — только хэш с секретом сервера.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from urllib.parse import urlsplit

from app import ai, brief
from app.config import settings
from app.db import pool
from app.settings_store import get_setting, set_setting
from app.yandex import YandexError

log = logging.getLogger(__name__)

TASK = "site_check"
URL_MAX = 300
HEADLINE_MAX = 56
HEADLINES_SHOWN = 3
MINUS_SHOWN = 10
MINUS_MAX = 60
GROUPS_MAX = 8
STALE_MINUTES = 3

DEFAULTS = {"enabled": True, "model": "", "ip_daily": 3, "daily": 300, "cache_days": 7}
LIMITS = {"ip_daily": (1, 100), "daily": (1, 100_000), "cache_days": (0, 90)}

LIMIT_TEXT = "Сегодня бесплатные проверки закончились. В кабинете проверка входит в пробный период"
OFF_TEXT = "Проверка сайта сейчас выключена. В кабинете она работает — и бесплатна в пробный период"
FAILED_TEXT = "Не получилось проверить сайт — попробуйте ещё раз чуть позже"

_HOST_RE = re.compile(r"^([a-zа-яё0-9-]+\.)+[a-zа-яё]{2,}$", re.I)


# --- Настройки (админка) ------------------------------------------------------------------------


def _int_setting(key: str) -> int:
    lo, hi = LIMITS[key]
    try:
        value = int(get_setting(f"site_check_{key}", str(DEFAULTS[key])))
    except ValueError:
        value = DEFAULTS[key]
    return max(lo, min(value, hi))


def get_settings() -> dict:
    return {
        "enabled": get_setting("site_check_enabled", "1") == "1",
        "model": get_setting("site_check_model", ""),
        "ip_daily": _int_setting("ip_daily"),
        "daily": _int_setting("daily"),
        "cache_days": _int_setting("cache_days"),
    }


def save_settings(
    *,
    enabled: bool,
    model: str = "",
    ip_daily: int | None = None,
    daily: int | None = None,
    cache_days: int | None = None,
) -> dict:
    values = {"ip_daily": ip_daily, "daily": daily, "cache_days": cache_days}
    labels = {"ip_daily": "Лимит на IP", "daily": "Лимит в сутки", "cache_days": "Кэш по домену"}
    for key, value in values.items():
        if value is None:
            continue
        lo, hi = LIMITS[key]
        if not lo <= int(value) <= hi:
            raise YandexError("site-check-settings", f"{labels[key]}: от {lo} до {hi}")
    set_setting("site_check_enabled", "1" if enabled else "0")
    set_setting("site_check_model", " ".join(str(model or "").split())[:200])
    for key, value in values.items():
        if value is not None:
            set_setting(f"site_check_{key}", str(int(value)))
    return admin_overview()


def _config() -> dict:
    return ai.load_config(None, TASK, model=get_settings()["model"] or None)


def admin_overview() -> dict:
    """Настройки, какая модель на самом деле ответит и счётчики за сегодня и неделю."""
    data = get_settings()
    try:
        cfg = _config()
        data["effective_model"] = cfg["model_name"]
        data["class_name"] = cfg["class_name"]
        data["class_model"] = ai.load_config(None, TASK)["model_name"]
    except YandexError:
        data["effective_model"] = data["class_name"] = data["class_model"] = ""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE created_at >= date_trunc('day', NOW())) AS today,
                COUNT(*) FILTER (WHERE created_at >= date_trunc('day', NOW()) AND status = 'error') AS today_errors,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS week,
                COUNT(DISTINCT domain) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS week_domains
            FROM site_checks
            """
        ).fetchone()
    data["stats"] = {k: int(row[k] or 0) for k in ("today", "today_errors", "week", "week_domains")}
    return data


def test_model() -> dict:
    """Пробный вызов модели проверки — без пользователя и без списания."""
    cfg = ai.load_config(None, "test", model=get_settings()["model"] or None, model_class=_config()["model_class"])
    reply = ai.complete(cfg, [{"role": "user", "content": "Ответь одним словом: готов"}])
    return {"model": cfg["model_name"], "reply": (reply["content"] or "").strip()[:200], "answered_by": reply.get("model") or ""}


# --- Проверка ------------------------------------------------------------------------------------


def normalize_url(raw: str) -> tuple[str, str]:
    """→ (адрес для загрузки, домен для кэша). Только адрес сайта, без логинов и портов."""
    text = " ".join(str(raw or "").split())
    if not text or len(text) > URL_MAX:
        raise YandexError("site", "Вставьте адрес сайта, например okna-msk.ru")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not _HOST_RE.match(host) or parts.username or parts.password:
        raise YandexError("site", "Вставьте адрес сайта, например okna-msk.ru")
    try:
        host_idna = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise YandexError("site", "Вставьте адрес сайта, например okna-msk.ru") from exc
    path = parts.path or "/"
    url = f"{parts.scheme}://{host_idna}{path}"
    domain = host[4:] if host.startswith("www.") else host
    return url, domain


def ip_hash(ip: str) -> str:
    secret = settings.web_session_secret or settings.token_encrypt_secret or "site-check"
    return hashlib.sha256(f"{secret}:{ip or ''}".encode()).hexdigest()[:32]


def start(raw_url: str, ip: str) -> dict:
    """Новая проверка или готовая из кэша → {"id", "cached"}. Запуск — run(id) в фоне, если cached = False."""
    cfg = get_settings()
    if not cfg["enabled"]:
        raise YandexError("site-check-off", OFF_TEXT)
    url, domain = normalize_url(raw_url)
    who = ip_hash(ip)
    with pool.connection() as conn:
        # Лимиты считаем под общим замком: два параллельных запроса не проскочат последнее место.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('site_check'))")
        if cfg["cache_days"]:
            row = conn.execute(
                """
                SELECT id FROM site_checks
                WHERE domain = %s AND status = 'done' AND created_at > NOW() - make_interval(days => %s)
                ORDER BY created_at DESC LIMIT 1
                """,
                (domain, cfg["cache_days"]),
            ).fetchone()
            if row:
                return {"id": row["id"], "cached": True}
        row = conn.execute(
            f"""
            SELECT id FROM site_checks
            WHERE domain = %s AND status = 'running' AND created_at > NOW() - INTERVAL '{STALE_MINUTES} minutes'
            ORDER BY created_at DESC LIMIT 1
            """,
            (domain,),
        ).fetchone()
        if row:
            return {"id": row["id"], "cached": True}
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE ip_hash = %s) AS mine
            FROM site_checks WHERE created_at >= date_trunc('day', NOW())
            """,
            (who,),
        ).fetchone()
        if int(counts["mine"]) >= cfg["ip_daily"] or int(counts["total"]) >= cfg["daily"]:
            raise YandexError("too-soon", LIMIT_TEXT)
        check_id = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO site_checks (id, url, domain, ip_hash) VALUES (%s, %s, %s, %s)",
            (check_id, url, domain, who),
        )
    return {"id": check_id, "cached": False}


_SYSTEM = """Ты — Толк, помощник по рекламе в Яндекс Директе. По главной странице сайта подготовь стартовые материалы для рекламы:
- niche — ниша и город, если он виден: 3–8 слов, например «стоматология в Казани»;
- offer — что и кому продают, чем выделяются: 1–2 коротких предложения;
- headlines — 6 заголовков объявлений, каждый не длиннее 56 символов с пробелами, без восклицательных знаков и без капслока;
- minus_words — 30–50 минус-слов: запросы, по которым кликнут, но не купят (бесплатно, своими руками, вакансии, б/у, отзывы о работе, смежные товары и услуги, которых на сайте нет). Только то, что действительно не подходит этому бизнесу, по одному слову или короткой фразе;
- groups — 3–6 групп объявлений: {"name": "...", "phrases": ["...", "..."]}, в каждой 3–6 ключевых фраз.
Пиши по-русски. Ответ — только JSON-объект: {"niche": "...", "offer": "...", "headlines": ["..."], "minus_words": ["..."], "groups": [{"name": "...", "phrases": ["..."]}]}"""


def _clean_list(items, limit: int, item_max: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        text = " ".join(str(item or "").split()).strip(" .,;")[:item_max]
        key = text.lower().replace("ё", "е")
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _cut(text: str, limit: int) -> str:
    """Обрезать по слову до limit символов."""
    if len(text) <= limit:
        return text
    head = text[: limit + 1].rsplit(" ", 1)[0]
    return (head if len(head) >= limit // 2 else text[:limit]).rstrip(" ,.;:—-")


def normalize_result(data: dict) -> dict:
    """Лимиты длины — кодом: модель не всегда их соблюдает."""
    data = data if isinstance(data, dict) else {}
    raw_headlines = _clean_list(data.get("headlines"), 20, 200)
    headlines = [h for h in raw_headlines if len(h) <= HEADLINE_MAX]
    for h in raw_headlines:
        if len(headlines) >= HEADLINES_SHOWN * 2:
            break
        if len(h) > HEADLINE_MAX:
            cut = _cut(h, HEADLINE_MAX)
            if cut and cut not in headlines:
                headlines.append(cut)
    groups = []
    for g in data.get("groups") if isinstance(data.get("groups"), list) else []:
        if not isinstance(g, dict):
            continue
        name = " ".join(str(g.get("name") or "").split())[:80]
        phrases = _clean_list(g.get("phrases"), 10, 80)
        if name and phrases:
            groups.append({"name": name, "phrases": phrases})
        if len(groups) >= GROUPS_MAX:
            break
    return {
        "niche": " ".join(str(data.get("niche") or "").split())[:120],
        "offer": " ".join(str(data.get("offer") or "").split())[:400],
        "headlines": headlines[: HEADLINES_SHOWN * 2],
        "minus_words": _clean_list(data.get("minus_words"), MINUS_MAX, 60),
        "groups": groups,
    }


def run(check_id: str) -> None:
    """Фоновая часть: страница → ИИ → результат. Любая ошибка — понятный текст в проверке."""
    with pool.connection() as conn:
        row = conn.execute("SELECT * FROM site_checks WHERE id = %s", (check_id,)).fetchone()
    if not row or row["status"] != "running":
        return
    status, result, error, model = "error", None, "", ""
    try:
        cfg = _config()
        model = cfg["model_name"]
        url, html = brief.fetch_site(row["url"])
        page = brief.extract_page(html)
        if not (page["title"] or page["text"]):
            raise YandexError("site", "На странице не нашлось текста — похоже, сайт собирается скриптами")
        content = "\n".join(
            part
            for part in (
                f"Адрес: {url}",
                f"Заголовок: {page['title']}" if page["title"] else "",
                f"Описание: {page['description']}" if page["description"] else "",
                "Заголовки: " + " | ".join(page["headings"]) if page["headings"] else "",
                f"Текст: {page['text']}",
            )
            if part
        )
        data = ai.complete_json(cfg, [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": content}])
        result = normalize_result(data)
        if not (result["headlines"] or result["minus_words"]):
            raise YandexError("ai-json", FAILED_TEXT)
        status = "done"
    except YandexError as exc:
        # Ошибки сайта посетителю понятны как есть; сбои ИИ — общим текстом, подробности — в журнал.
        error = exc.message if exc.code == "site" and exc.message else FAILED_TEXT
        if exc.code != "site":
            log.warning("Проверка сайта %s: %s %s", row["domain"], exc.code, exc.message)
    except Exception:  # noqa: BLE001 — фоновая задача не должна падать молча
        log.exception("Проверка сайта %s упала", row["domain"])
        error = FAILED_TEXT
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE site_checks SET status = %s, result = %s, error = %s, model = %s, finished_at = NOW()
            WHERE id = %s
            """,
            (status, _json(result), error, model, check_id),
        )


def _json(value):
    from psycopg.types.json import Jsonb

    return Jsonb(value) if value is not None else None


def public(check_id: str) -> dict:
    """Статус и открытая часть результата. Полный набор — после регистрации, здесь только счётчики."""
    with pool.connection() as conn:
        row = conn.execute(
            f"""
            SELECT id, domain, status, result, error,
                   status = 'running' AND created_at < NOW() - INTERVAL '{STALE_MINUTES} minutes' AS stale
            FROM site_checks WHERE id = %s
            """,
            (str(check_id or "")[:40],),
        ).fetchone()
    if not row:
        raise YandexError("not-found", "Проверка не найдена")
    out = {"id": row["id"], "domain": row["domain"], "status": row["status"]}
    if row["stale"]:
        out.update(status="error", error=FAILED_TEXT)
    elif row["status"] == "error":
        out["error"] = row["error"] or FAILED_TEXT
    elif row["status"] == "done":
        r = row["result"] or {}
        minus = r.get("minus_words") or []
        groups = r.get("groups") or []
        out["result"] = {
            "niche": r.get("niche") or "",
            "offer": r.get("offer") or "",
            "headlines": (r.get("headlines") or [])[:HEADLINES_SHOWN],
            "minus_words": minus[:MINUS_SHOWN],
            "more": {
                "minus_words": max(0, len(minus) - MINUS_SHOWN),
                "headlines": max(0, len(r.get("headlines") or []) - HEADLINES_SHOWN),
                "groups": len(groups),
            },
        }
    return out
