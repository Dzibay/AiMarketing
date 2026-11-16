"""Визиты сайта: лендинг и открытые страницы кабинета (вход, регистрация) — для воронки и колонки «Источник».

Браузер шлёт короткое сообщение о каждом просмотре (POST /api/site/visit): случайный идентификатор
посетителя из localStorage, страница, адрес, с которого пришёл, UTM-метки и реф-код из адреса. Визит — как
в Метрике: новый после VISIT_GAP_MINUTES тишины; просмотры внутри визита увеличивают pages. Источник
визита — то, с чем пришёл первый просмотр.

IP не хранится: он нужен только для ограничения частоты в памяти процесса. Боты отсекаются по User-Agent.
При регистрации идентификатор посетителя уходит на сервер вместе с реф-кодом: визиты этого браузера
получают user_id, а в users пишется первый источник — из самого раннего визита.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from app.clock import today_msk
from app.db import pool

log = logging.getLogger(__name__)

VISIT_GAP_MINUTES = 30
VISITOR_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
FIELD_MAX = 200

# Просмотров с одного адреса в минуту — больше похоже на скрипт, лишнее молча отбрасываем.
IP_PER_MINUTE = 60
_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|scrap|fetch|monitor|headless|phantom|lighthouse|pagespeed|preview|"
    r"curl|wget|python|httpx|java/|go-http|okhttp|axios|node-fetch|facebookexternalhit|whatsapp|telegram",
    re.IGNORECASE,
)
_TABLET_RE = re.compile(r"ipad|tablet|kindle|silk|playbook|android(?!.*mobile)", re.IGNORECASE)
_MOBILE_RE = re.compile(r"mobi|iphone|ipod|android|blackberry|opera mini|iemobile", re.IGNORECASE)


def is_bot(user_agent: str) -> bool:
    ua = (user_agent or "").strip()
    return not ua or bool(_BOT_RE.search(ua))


def device(user_agent: str) -> str:
    ua = user_agent or ""
    if _TABLET_RE.search(ua):
        return "tablet"
    if _MOBILE_RE.search(ua):
        return "mobile"
    return "desktop"


def _too_often(ip: str) -> bool:
    if not ip:
        return False
    now = time.monotonic()
    with _hits_lock:
        recent = [t for t in _hits.get(ip, ()) if now - t < 60]
        if len(recent) >= IP_PER_MINUTE:
            _hits[ip] = recent
            return True
        recent.append(now)
        _hits[ip] = recent
        if len(_hits) > 10_000:
            # Старые адреса — прочь, чтобы словарь не рос бесконечно.
            for key in [k for k, v in _hits.items() if not v or now - v[-1] >= 60]:
                del _hits[key]
    return False


def _clean(value, limit: int = FIELD_MAX) -> str:
    return " ".join(str(value or "").split())[:limit]


def _path(raw: str) -> str:
    """Только путь страницы, без адреса сайта, меток и якоря."""
    path = urlsplit(_clean(raw, 500)).path or "/"
    return path[:FIELD_MAX] if path.startswith("/") else "/"


def _referrer_host(raw: str, own_host: str) -> str:
    """Хост сайта-источника; свой сайт и пустое — ''."""
    try:
        host = (urlsplit(_clean(raw, 500)).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    own = (own_host or "").lower().split(":")[0]
    if own.startswith("www."):
        own = own[4:]
    if not host or host == own:
        return ""
    return host[:FIELD_MAX]


def visitor_ok(visitor_id: str) -> bool:
    return bool(VISITOR_RE.match(visitor_id or ""))


def record(
    *,
    visitor_id: str,
    path: str,
    referrer: str = "",
    utm: dict | None = None,
    ref: str = "",
    user_agent: str = "",
    ip: str = "",
    own_host: str = "",
) -> str:
    """Просмотр страницы → "new" (начат визит), "page" (продолжение визита) или "skip" (бот, мусор, частота)."""
    visitor_id = (visitor_id or "").strip()
    if not visitor_ok(visitor_id) or is_bot(user_agent) or _too_often(ip):
        return "skip"
    try:
        with pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE site_visits SET pages = pages + 1, last_at = NOW()
                WHERE id = (
                    SELECT id FROM site_visits
                    WHERE visitor_id = %s AND last_at > NOW() - make_interval(mins => %s)
                    ORDER BY last_at DESC LIMIT 1
                )
                RETURNING id
                """,
                (visitor_id, VISIT_GAP_MINUTES),
            ).fetchone()
            if row is not None:
                return "page"
            utm = utm or {}
            from app.referrals import CODE_RE, normalize

            code = normalize(ref)
            conn.execute(
                """
                INSERT INTO site_visits (visitor_id, day, landing_page, referrer_host, utm_source, utm_medium,
                                         utm_campaign, utm_content, utm_term, ref_code, device, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        (SELECT user_id FROM site_visits WHERE visitor_id = %s AND user_id IS NOT NULL LIMIT 1))
                """,
                (
                    visitor_id,
                    today_msk(),
                    _path(path),
                    _referrer_host(referrer, own_host),
                    *(_clean(utm.get(k)) for k in UTM_KEYS),
                    code if CODE_RE.match(code) else "",
                    device(user_agent),
                    visitor_id,
                ),
            )
            return "new"
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать визит сайта")
        return "skip"


def attach_user(user_id: int, visitor_id: str) -> bool:
    """Регистрация: визиты этого браузера — пользователю, первый источник — из самого раннего визита."""
    visitor_id = (visitor_id or "").strip()
    if not user_id or not visitor_ok(visitor_id):
        return False
    try:
        with pool.connection() as conn:
            conn.execute(
                "UPDATE site_visits SET user_id = %s WHERE visitor_id = %s AND user_id IS NULL", (user_id, visitor_id)
            )
            first = conn.execute(
                """
                SELECT landing_page, referrer_host, utm_source, utm_medium, utm_campaign, utm_content, utm_term
                FROM site_visits WHERE visitor_id = %s ORDER BY started_at, id LIMIT 1
                """,
                (visitor_id,),
            ).fetchone()
            utm = {k: first[k] for k in UTM_KEYS if first and first[k]}
            row = conn.execute(
                """
                UPDATE users SET visitor_id = %s, first_landing = %s, first_referrer = %s, first_utm = %s
                WHERE id = %s AND visitor_id = ''
                RETURNING id
                """,
                (
                    visitor_id,
                    first["landing_page"] if first else "",
                    first["referrer_host"] if first else "",
                    Jsonb(utm),
                    user_id,
                ),
            ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        log.exception("Не удалось привязать визиты к пользователю %s", user_id)
        return False
