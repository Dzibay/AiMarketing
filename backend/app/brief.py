"""Бриф проекта: что продаём, география, что точно «не наше», заметки.

Бриф читают ИИ (чистка запросов, чат) и правила чистки: «не наше» сразу предлагается в минус.
Черновик брифа ИИ собирает по главной странице сайта; сохраняет его пользователь сам.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpcore
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
    """Бриф и KPI проекта — блоком для промпта ИИ."""
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
        raise YandexError("site", "Укажите адрес сайта в настройках проекта")
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
    _public_ips(host, port or (443 if parts.scheme == "https" else 80))


# NAT64: в адресе IPv6 лежит IPv4 — проверяем его.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _is_public_ip(raw: str) -> bool:
    """Адрес в интернете, а не во внутренней сети; IPv4 внутри IPv6 (::ffff:, 6to4, Teredo, NAT64) — тоже."""
    try:
        addr = ipaddress.ip_address(str(raw).split("%", 1)[0])
    except ValueError:
        return False
    if addr.version == 6:
        inner = addr.ipv4_mapped or addr.sixtofour or (addr.teredo[1] if addr.teredo else None)
        if inner is None and addr in _NAT64:
            inner = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if inner is not None and not inner.is_global:
            return False
    return addr.is_global


def _public_ips(host: str, port: int) -> list[str]:
    """Адреса сайта; хоть один внутренний — отказ."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise YandexError("site", f"Не удалось найти сайт {host}") from exc
    ips = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise YandexError("site", "Адрес сайта указывает во внутреннюю сеть")
    return ips


class _PinnedBackend(httpcore.SyncBackend):
    """Сеть fetch_site: адрес сайта узнаём и проверяем сами и подключаемся ровно к проверенному IP.

    Иначе между проверкой и подключением DNS мог бы ответить уже внутренним адресом (DNS rebinding). Host и SNI
    остаются именем сайта — их httpcore берёт из адреса запроса. Каждый редирект — новое подключение и новая проверка.
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last: Exception | None = None
        for ip in _public_ips(host, port):
            try:
                return super().connect_tcp(
                    ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last  # type: ignore[misc]


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False), network_backend=_PinnedBackend()
        )


def _charset(content_type: str, head: bytes) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I) or re.search(
        rb"<meta[^>]+charset=[\"']?([\w-]+)", head[:4096], re.I
    )
    if match:
        value = match.group(1)
        return value.decode("ascii", "ignore") if isinstance(value, bytes) else value
    return "utf-8"


def fetch_site(raw_url: str) -> tuple[str, str]:
    """→ (итоговый адрес, html). Редиректы проверяем по одному: каждый — на публичный адрес.

    Подключаемся только к проверенному IP (_PinnedBackend); прокси из окружения не берём — имя резолвим сами.
    """
    url = _site_url(raw_url)
    headers = {"User-Agent": "TolkBot/1.0 (+https://tolk-ads.ru/bot/)", "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(
        timeout=SITE_TIMEOUT, follow_redirects=False, headers=headers, transport=_PinnedTransport(), trust_env=False
    ) as client:
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

    def __init__(self, text_cap: int = SITE_TEXT_CHARS * 2) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.headings: list[str] = []
        self.text: list[str] = []
        self._cap = text_cap
        self._size = 0
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
        if self._size < self._cap:
            self.text.append(text)
            self._size += len(text)


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


# --- Страница для ИИ-чата -----------------------------------------------------------------------
#
# ИИ читает сайт проекта и посадочные страницы сам: заголовок, описание, подзаголовки, цены и видимый текст —
# всего около PAGE_CHARS знаков. Цены ищем по всей странице: на длинном лендинге они бывают ниже обрезки текста.

PAGE_CHARS = 6000
PAGE_SCAN_CHARS = 60_000
PAGE_HEADINGS, PAGE_PRICES, PAGE_ITEM_CHARS = 20, 15, 120
PAGE_THIN_CHARS = 100  # меньше — страница почти пустая: содержимое, скорее всего, рисует скрипт
_PRICE_RE = re.compile(r"\d[\d   ]*(?:[.,]\d{1,2})?\s*(?:₽|руб(?:\.|л[а-я]*)?|р\.)", re.I)


def _prices(pieces: list[str]) -> list[str]:
    """Куски текста с ценой; короткая цена — вместе с соседним текстом: название товара часто стоит рядом."""
    out: list[str] = []
    for index, piece in enumerate(pieces):
        if not _PRICE_RE.search(piece):
            continue
        snippet = piece
        if len(piece) < 40 and index > 0:
            snippet = f"{pieces[index - 1][-60:]} {piece}"
        snippet = snippet[:PAGE_ITEM_CHARS]
        if snippet not in out:
            out.append(snippet)
        if len(out) >= PAGE_PRICES:
            break
    return out


def _plain_reason(message: str) -> str:
    """Причина из fetch_site — простыми словами, без технических подробностей."""
    text = message or ""
    status = re.search(r"ошибкой (\d{3})", text)
    if status:
        code = int(status.group(1))
        if code in (404, 410):
            return "такой страницы нет"
        if code in (401, 403):
            return "сайт не пускает к этой странице"
        if code >= 500:
            return "сайт сейчас не работает — попробуйте позже"
        return "сайт ответил ошибкой"
    for marker, reason in (
        ("не ответил", "сайт не ответил за 10 секунд"),
        ("не HTML", "по адресу файл, а не страница"),
        ("Не удалось найти", "такого адреса нет"),
        ("Слишком много перенаправлений", "сайт перенаправляет по кругу"),
        ("в никуда", "сайт перенаправляет в никуда"),
        ("http:// или https://", "адрес должен начинаться с http:// или https://"),
        ("Укажите адрес", "адрес сайта не указан"),
    ):
        if marker in text:
            return reason
    if any(marker in text for marker in ("публичным", "внутреннюю сеть", "порт", "логина")):
        return "этот адрес закрыт для чтения"
    return "не удалось подключиться"


def read_page(raw_url: str, limit: int = PAGE_CHARS) -> dict:
    """Страница для ИИ: {url, title, description, headings, prices, text, note?} — всего около limit знаков.

    Не открылась — YandexError("site", «Сайт не открылся: …»): причина простыми словами.
    """
    try:
        url, html = fetch_site(raw_url)
    except YandexError as exc:
        raise YandexError("site", f"Сайт не открылся: {_plain_reason(exc.message or '')}") from exc
    parser = _Extractor(text_cap=PAGE_SCAN_CHARS)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # битая разметка — берём, что успели разобрать
        pass
    title = parser.title
    description = parser.meta.get("description") or parser.meta.get("og:description") or ""
    headings = [h[:PAGE_ITEM_CHARS] for h in dict.fromkeys(parser.headings)][:PAGE_HEADINGS]
    prices = _prices(parser.text)
    text = " ".join(parser.text)
    used = len(title) + len(description) + sum(len(h) for h in headings) + sum(len(p) for p in prices)
    room = max(1000, limit - used)
    out = {"url": url, "title": title, "description": description, "headings": headings, "prices": prices}
    out["text"] = text[:room]
    # Заголовок есть почти у любого сайта на скриптах — считаем только текст страницы без него.
    if len(text) - len(title) < PAGE_THIN_CHARS:
        out["note"] = (
            "На странице почти нет текста — похоже, сайт собирается скриптами, и его содержимое не видно. Не делай "
            "вывода, что цен или оффера нет, — опирайся на бриф"
        )
    elif len(text) > room:
        out["note"] = f"Страница длинная — текст обрезан до {room} знаков; цены собраны со всей страницы"
    return out


_DRAFT_SYSTEM = """Ты помогаешь маркетологу заполнить бриф клиента для рекламы в Яндекс Директе. По тексту главной страницы сайта составь черновик:
- product — что продаём: товары или услуги, для кого, 1–3 предложения;
- geo — география продаж или доставки, если она видна на сайте; иначе пустая строка;
- not_ours — 5–15 слов и коротких фраз, которые точно не наши и которые стоит сразу минусовать: смежные товары и услуги, которых нет, «б/у», «вакансии», «своими руками» и т. п. — только то, что действительно не подходит этому бизнесу;
- notes — важное для рекламы: цены, акции, особенности, ограничения; кратко.
Пиши по-русски. Ответ — только JSON-объект: {"product": "...", "geo": "...", "not_ours": ["..."], "notes": "..."}"""


def draft_brief(user_id: int, payload: dict, progress=None) -> dict:
    """Задача: черновик брифа по сайту проекта. Не сохраняет — пользователь правит и сохраняет сам."""
    from app.workspaces import _ws_out, require_workspace

    progress = progress or (lambda *_a: None)
    ws = _ws_out(require_workspace(user_id, int(payload.get("workspace_id") or 0)))
    cfg = ai.load_config(user_id, "brief", ws["id"])
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
