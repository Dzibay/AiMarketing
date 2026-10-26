"""Бриф пространства: что продаём, география, что точно «не наше», заметки.

Бриф читают ИИ (чистка запросов, чат) и правила чистки: «не наше» сразу предлагается в минус.
Черновик брифа ИИ собирает по главной странице сайта; сохраняет его пользователь сам.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app import ai
from app.yandex import YandexError

PRODUCT_MAX = 2000
GEO_MAX = 300
NOTES_MAX = 2000
NOT_OURS_MAX = 100
NOT_OURS_ITEM_MAX = 100

SITE_TIMEOUT = 10.0
SITE_MAX_BYTES = 1_500_000
SITE_MAX_REDIRECTS = 3
SITE_TEXT_CHARS = 4000


def _key(text: str) -> str:
    return " ".join(str(text).lower().replace("ё", "е").split())


def _text(value, limit: int) -> str:
    return str(value or "").replace("\r", "").strip()[:limit]


def normalize_brief(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    items = raw.get("not_ours") or []
    if isinstance(items, str):
        items = re.split(r"[\n,;]", items)
    not_ours: list[str] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        text = " ".join(str(item or "").split())[:NOT_OURS_ITEM_MAX]
        key = _key(text)
        if text and key not in seen:
            seen.add(key)
            not_ours.append(text)
        if len(not_ours) >= NOT_OURS_MAX:
            break
    return {
        "product": _text(raw.get("product"), PRODUCT_MAX),
        "geo": _text(raw.get("geo"), GEO_MAX),
        "not_ours": not_ours,
        "notes": _text(raw.get("notes"), NOTES_MAX),
    }


def brief_filled(brief) -> bool:
    b = normalize_brief(brief)
    return bool(b["product"] or b["geo"] or b["not_ours"] or b["notes"])


def brief_prompt(ws: dict) -> str:
    """Бриф и KPI пространства — блоком для промпта ИИ."""
    b = normalize_brief(ws.get("brief"))
    lines = [f"Клиент: {ws.get('name') or ''}"]
    site = ws.get("site") or ws.get("counter_site") or ""
    if site:
        lines.append(f"Сайт: {site}")
    if b["product"]:
        lines.append(f"Что продаём: {b['product']}")
    if b["geo"]:
        lines.append(f"География: {b['geo']}")
    if b["not_ours"]:
        lines.append("Точно не наше: " + ", ".join(b["not_ours"]))
    if b["notes"]:
        lines.append(f"Заметки: {b['notes']}")
    target = ws.get("kpi_target")
    if target is not None:
        if (ws.get("kpi_type") or "cpa") == "drr":
            lines.append(f"KPI: ДРР не выше {float(target):g} %")
        else:
            lines.append(f"KPI: цена конверсии не выше {float(target):g} ₽")
    if not brief_filled(b):
        lines.append("Бриф не заполнен — ориентируйся на ключевые фразы и название клиента.")
    return "О бизнесе:\n" + "\n".join(lines)


# --- Черновик по сайту --------------------------------------------------------------------------


def _site_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        raise YandexError("site", "Укажите адрес сайта в настройках пространства")
    if "://" not in url:
        url = "https://" + url
    return url


def _check_public(url: str) -> None:
    """Защита от SSRF: только http(s) на стандартных портах и только публичные адреса."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise YandexError("site", "Адрес сайта должен начинаться с http:// или https://")
    if parts.username or parts.password:
        raise YandexError("site", "Адрес сайта без логина и пароля")
    try:
        port = parts.port
    except ValueError as exc:
        raise YandexError("site", "Неверный порт в адресе сайта") from exc
    if port not in (None, 80, 443):
        raise YandexError("site", "Сайт должен открываться на стандартном порту")
    host = parts.hostname
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise YandexError("site", "Адрес сайта должен быть публичным")
    try:
        infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise YandexError("site", f"Не удалось найти сайт {host}") from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise YandexError("site", "Адрес сайта указывает во внутреннюю сеть")


def _charset(content_type: str, head: bytes) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(
        rb"<meta[^>]+charset=[\"']?([\w-]+)", head[:4096], re.I
    )
    if match:
        value = match.group(1)
        return value.decode("ascii", "ignore") if isinstance(value, bytes) else value
    return "utf-8"


def fetch_site(raw_url: str) -> tuple[str, str]:
    """→ (итоговый адрес, html). Редиректы проверяем по одному: каждый — на публичный адрес."""
    url = _site_url(raw_url)
    headers = {"User-Agent": "AiMarketingBriefBot/1.0", "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(timeout=SITE_TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _hop in range(SITE_MAX_REDIRECTS + 1):
            _check_public(url)
            try:
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location") or ""
                        if not location:
                            raise YandexError("site", "Сайт перенаправляет в никуда")
                        url = urljoin(url, location)
                        continue
                    if response.status_code >= 400:
                        raise YandexError("site", f"Сайт ответил ошибкой {response.status_code}")
                    content_type = response.headers.get("content-type") or ""
                    if content_type and "html" not in content_type.lower():
                        raise YandexError("site", "По адресу не HTML-страница")
                    buf = bytearray()
                    for chunk in response.iter_bytes():
                        buf += chunk
                        if len(buf) >= SITE_MAX_BYTES:
                            break
                    body = bytes(buf[:SITE_MAX_BYTES])
            except httpx.TimeoutException as exc:
                raise YandexError("site", "Сайт не ответил за 10 секунд") from exc
            except httpx.HTTPError as exc:
                raise YandexError("site", f"Не удалось открыть сайт: {exc.__class__.__name__}") from exc
            charset = _charset(content_type, body)
            try:
                return url, body.decode(charset, errors="replace")
            except LookupError:
                return url, body.decode("utf-8", errors="replace")
    raise YandexError("site", "Слишком много перенаправлений")


class _Extractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.headings: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._tag = ""
        self._heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        self._tag = tag
        if tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "keywords", "og:title", "og:description") and a.get("content"):
                self.meta[name] = " ".join(a["content"].split())[:500]
        if tag in ("h1", "h2", "h3"):
            self._heading = []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in ("h1", "h2", "h3") and self._heading is not None:
            text = " ".join(" ".join(self._heading).split())
            if text and len(self.headings) < 40:
                self.headings.append(text[:200])
            self._heading = None

    def handle_data(self, data):
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._tag == "title" and not self.title:
            self.title = text[:300]
        if self._heading is not None:
            self._heading.append(text)
        if sum(len(t) for t in self.text) < SITE_TEXT_CHARS * 2:
            self.text.append(text)


def extract_page(html: str) -> dict:
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # битая разметка — берём, что успели разобрать
        pass
    return {
        "title": parser.title,
        "description": parser.meta.get("description") or parser.meta.get("og:description") or "",
        "keywords": parser.meta.get("keywords") or "",
        "headings": parser.headings,
        "text": " ".join(parser.text)[:SITE_TEXT_CHARS],
    }


_DRAFT_SYSTEM = """Ты помогаешь маркетологу заполнить бриф клиента для рекламы в Яндекс Директе. По тексту главной страницы сайта составь черновик:
- product — что продаём: товары или услуги, для кого, 1–3 предложения;
- geo — география продаж или доставки, если она видна на сайте; иначе пустая строка;
- not_ours — 5–15 слов и коротких фраз, которые точно не наши и которые стоит сразу минусовать: смежные товары и услуги, которых нет, «б/у», «вакансии», «своими руками» и т. п. — только то, что действительно не подходит этому бизнесу;
- notes — важное для рекламы: цены, акции, особенности, ограничения; кратко.
Пиши по-русски. Ответ — только JSON-объект: {"product": "...", "geo": "...", "not_ours": ["..."], "notes": "..."}"""


def draft_brief(user_id: int, payload: dict, progress=None) -> dict:
    """Задача: черновик брифа по сайту пространства. Не сохраняет — пользователь правит и сохраняет сам."""
    from app.workspaces import _ws_out, require_workspace

    progress = progress or (lambda *_a: None)
    ws = _ws_out(require_workspace(user_id, int(payload.get("workspace_id") or 0)))
    cfg = ai.load_config(user_id)
    progress(0, 2, "Читаю сайт")
    url, html = fetch_site(payload.get("site") or ws.get("site") or "")
    page = extract_page(html)
    if not (page["title"] or page["text"]):
        raise YandexError("site", "На странице не нашлось текста — возможно, сайт собирается скриптами")
    progress(1, 2, "ИИ составляет бриф")
    content = "\n".join(
        part
        for part in (
            f"Адрес: {url}",
            f"Заголовок: {page['title']}" if page["title"] else "",
            f"Описание: {page['description']}" if page["description"] else "",
            f"Ключевые слова страницы: {page['keywords']}" if page["keywords"] else "",
            "Заголовки: " + " | ".join(page["headings"]) if page["headings"] else "",
            f"Текст: {page['text']}",
        )
        if part
    )
    data = ai.complete_json(
        cfg, [{"role": "system", "content": _DRAFT_SYSTEM}, {"role": "user", "content": content}]
    )
    progress(2, 2, "Готово")
    return {"brief": normalize_brief(data), "site": url, "title": page["title"]}
