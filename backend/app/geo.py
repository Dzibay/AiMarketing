"""Регионы Директа: справочник GeoRegions, поиск по названию, подписи и замена регионов кампании.

Справочник общий для всех: dictionaries.get отдаёт его любому токену Директа. Держим его в памяти
процесса сутки — регионы меняются редко, а ответ тяжёлый. Минус перед id исключает регион:
[225, -1] — Россия без Москвы и области.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time

from app import direct_api
from app.db import pool
from app.journal import log_action
from app.yandex import YandexError, get_connection_access_token

log = logging.getLogger("app.geo")

CACHE_TTL = 24 * 3600
RETRY_AFTER = 300
SEARCH_LIMIT = 20
HINT = "Минус перед id исключает регион: [225, -1] — Россия без Москвы и области"

# Подписи частых регионов, если справочник Директа недоступен.
KNOWN_REGIONS = {
    225: "Россия",
    1: "Москва и Московская область",
    213: "Москва",
    10174: "Санкт-Петербург и Ленинградская область",
    2: "Санкт-Петербург",
    54: "Екатеринбург",
    65: "Новосибирск",
    43: "Казань",
    47: "Нижний Новгород",
    35: "Краснодар",
}
_TYPE_ORDER = {"Country": 0, "Region": 1, "Administrative area": 1, "City": 2}
# Типы регионов Директа по-русски; неизвестный тип — без подписи, английское слово пользователю не показываем.
_TYPE_LABELS = {
    "Country": "страна",
    "Region": "округ",
    "Administrative area": "область",
    "City": "город",
    "Village": "населённый пункт",
    "City district": "район города",
    "District": "район",
    "Federal district": "федеральный округ",
    "Continent": "континент",
    "World": "весь мир",
}

_lock = threading.Lock()
_cache: dict = {"regions": {}, "next": 0.0}


def _any_token(user_id: int):
    """Токен любого включённого кабинета пользователя; None — кабинетов нет."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT a.connection_id
            FROM direct_accounts a JOIN yandex_connections y ON y.id = a.connection_id
            WHERE a.user_id = %s AND a.enabled
            ORDER BY a.connection_id
            """,
            (user_id,),
        ).fetchall()
    for row in rows:
        try:
            return get_connection_access_token(row["connection_id"], user_id)
        except YandexError:
            continue
    return None


def _parse(result: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in result.get("GeoRegions") or []:
        try:
            rid = int(item["GeoRegionId"])
        except (KeyError, TypeError, ValueError):
            continue
        parent = item.get("ParentId")
        out[rid] = {
            "id": rid,
            "name": str(item.get("GeoRegionName") or rid),
            "type": str(item.get("GeoRegionType") or ""),
            "parent": int(parent) if str(parent or "").lstrip("-").isdigit() else None,
        }
    return out


def load_regions(user_id: int) -> dict[int, dict]:
    """Справочник {id: {id, name, type, parent}}; пустой — Директ недоступен."""
    with _lock:
        if time.monotonic() < _cache["next"]:
            return _cache["regions"]
        token = _any_token(user_id)
        if token is None:
            return _cache["regions"]
        try:
            fresh = _parse(direct_api.call(token, None, "dictionaries", "get", {"DictionaryNames": ["GeoRegions"]}))
        except Exception:
            log.warning("Справочник регионов Директа недоступен", exc_info=True)
            fresh = {}
        if fresh:
            _cache.update(regions=fresh, next=time.monotonic() + CACHE_TTL)
        else:
            # Прежний справочник лучше никакого; повторим попытку через несколько минут.
            _cache["next"] = time.monotonic() + RETRY_AFTER
        return _cache["regions"]


def _norm(text) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def _name(regions: dict[int, dict], rid: int) -> str:
    region = regions.get(rid)
    return region["name"] if region else KNOWN_REGIONS.get(rid, str(rid))


def _path(regions: dict[int, dict], region: dict) -> str:
    """Родители региона сверху вниз без «весь мир» и континентов: «Россия › Приволжский ФО»."""
    names: list[str] = []
    parent = region.get("parent")
    seen = {region["id"]}
    while parent and parent in regions and parent not in seen and len(names) < 4:
        seen.add(parent)
        item = regions[parent]
        if item["type"] not in ("World", "Continent"):
            names.append(item["name"])
        parent = item.get("parent")
    return " › ".join(reversed(names))


def search_regions(user_id: int, query: str, limit: int = SEARCH_LIMIT) -> dict:
    """Регионы по названию: точное совпадение, затем начало названия, затем вхождение."""
    q = _norm(query)
    if not q:
        raise YandexError("args", "Укажите название региона или города")
    regions = load_regions(user_id)
    source = regions or {
        rid: {"id": rid, "name": name, "type": "", "parent": None} for rid, name in KNOWN_REGIONS.items()
    }
    ranked = []
    for region in source.values():
        name = _norm(region["name"])
        if name == q:
            rank = 0
        elif name.startswith(q):
            rank = 1
        elif q in name:
            rank = 2
        else:
            continue
        ranked.append((rank, _TYPE_ORDER.get(region["type"], 3), len(name), region["id"], region))
    ranked.sort(key=lambda item: item[:4])
    out: dict = {
        "regions": [
            {
                "id": region["id"],
                "name": region["name"],
                "type": _TYPE_LABELS.get(region["type"], ""),
                "path": _path(source, region),
            }
            for *_, region in ranked[: max(1, min(limit, 50))]
        ],
        "hint": HINT,
    }
    if not regions:
        out["note"] = "Справочник регионов Директа недоступен — искал только среди основных регионов"
    elif not out["regions"]:
        out["note"] = "Ничего не нашлось — попробуй другое написание или регион крупнее"
    return out


def parse_region_ids(raw) -> list[int]:
    """Список id регионов из аргументов: числа без повторов, 0 отбрасывается."""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for value in raw:
        try:
            rid = int(value)
        except (TypeError, ValueError):
            continue
        if rid and rid not in out:
            out.append(rid)
    return out


def unknown_regions(user_id: int, region_ids: list[int]) -> list[int]:
    """id, которых нет в справочнике; справочник недоступен — проверить нечем, пусто."""
    regions = load_regions(user_id)
    if not regions:
        return []
    return [rid for rid in region_ids if abs(rid) not in regions]


def region_label(region_ids: list[int], regions: dict[int, dict] | None = None, limit: int = 6) -> str:
    """«Москва, Санкт-Петербург, кроме: Зеленоград»; пустой список — «—»."""
    regions = regions or {}
    plus = [rid for rid in region_ids if rid > 0]
    minus = [-rid for rid in region_ids if rid < 0]

    def names(ids: list[int]) -> str:
        text = ", ".join(_name(regions, rid) for rid in ids[:limit])
        return text + (f" и ещё {len(ids) - limit}" if len(ids) > limit else "")

    text = names(plus)
    if minus:
        text = (text + ", " if text else "") + "кроме: " + names(minus)
    return text or "—"


def cached_regions() -> dict[int, dict]:
    """Справочник из памяти процесса, без похода в Директ; пустой — его ещё не загружали."""
    return _cache["regions"]


# --- Регион словами → регионы справочника -----------------------------------------------------------------
#
# В настройках регион пишут словами: «Екб и область», «Россия, кроме Москвы и МО», «Москва и МО — исключить».
# Текст режем на фразы: до «кроме» — где показывать, после — что исключить; слова фразы — на основы без падежных
# окончаний. Кандидаты фразы — регионы, в названии которых есть все её основы: «по Казани» → Казань, «Ростов» →
# Ростов и Ростов-на-Дону; у места показа без «область/край» — сперва регионы без них в названии: «Москва» — город,
# а не «Москва и Московская область». Основы сравниваем целиком: «Казань» — не «Казахстан», «Москва» — не
# «Московская». Не нашлось место показа или первое место после «кроме» — уверенности нет, и сверка молчит: лучше
# пропустить расхождение, чем остановить сборку зря.

# Без справочника — родители и типы основных регионов; прочие города KNOWN_REGIONS — сразу в России.
_KNOWN_PARENTS = {225: None, 1: 225, 213: 1, 10174: 225, 2: 10174}
_KNOWN_TYPES = {225: "Country", 1: "Administrative area", 10174: "Administrative area"}
_AREA_TYPES = ("Country", "Region", "Administrative area")
_ENDINGS = sorted(
    "ами ями ого его ому ему ой ей ом ем ам ям ах ях ая яя ое ее ий ый ие ые ую юю а я ы и е у ю о ь".split(),
    key=len,
    reverse=True,
)


def _stem(word: str) -> str:
    """Основа слова — без самого длинного падежного окончания, если останется хотя бы две буквы."""
    for end in _ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 2:
            return word[: -len(end)]
    return word


def _stem_set(words: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in words.split())


# Тип региона: в сравнении слово не участвует, но выбирает область, а не город («Московская область»).
_TYPE_WORDS = _stem_set(
    "область области областью обл край края крае краю краем республика республики республике республикой респ "
    "округ округа округе ао"
)
# Фраза только из этих слов сразу после города — его область: «Екб и область», «Казань и республика». «Регион» —
# только в единственном числе: «Москва и регионы» — Москва и остальная страна, а не Подмосковье.
_OWN_AREA = _stem_set("область областью обл край краем республика республикой")
_OWN_WORDS = frozenset("регион региона региону регионом регионе".split())
# Фраза только из этих слов после места — и остальная страна: «Москва и регионы», «Казань и другие города».
_REST_WORDS = frozenset(
    "регионы регионов регионам регионами регионах другие других остальные остальных прочие прочих".split()
)
_STOP = (
    _TYPE_WORDS
    | _OWN_AREA
    | _stem_set(" ".join(_OWN_WORDS | _REST_WORDS))
    | _stem_set(
        "по в во на за для из от до около только все вся всей весь всю всего всем всех целиком полностью "
        "региональный город города городов городской гор радиус км гео территория район районы остальная "
        "остальной остальную районов федеральный федерального фо автономный автономная автономного населенный "
        "населенные пункт пункты нет не ничего никаких любой любые ограничений ограничения"
    )
)
# «исключ…» — служебное, когда это не знак исключения: «исключительно Казань», «Москву не исключаем».
_STOP_PREFIXES = ("показ", "геотарг", "таргет", "исключ")
# Сокращения и разговорные названия — словами справочника Директа; ключ — основа слова.
_ALIASES = {
    _stem(short): full
    for short, full in {
        "мск": "москва", "мо": "московская область", "мособласть": "московская область",
        "подмосковье": "московская область", "спб": "санкт петербург", "питер": "санкт петербург",
        "ло": "ленинградская область", "ленобласть": "ленинградская область", "екб": "екатеринбург",
        "ебург": "екатеринбург", "екат": "екатеринбург", "нск": "новосибирск", "новосиб": "новосибирск",
        "нн": "нижний новгород", "крд": "краснодар", "рнд": "ростов на дону", "челяба": "челябинск",
        "владик": "владивосток", "рф": "россия", "страна": "россия",
        "хмао": "ханты мансийский автономный округ", "югра": "ханты мансийский автономный округ",
        "янао": "ямало ненецкий автономный округ", "ямал": "ямало ненецкий автономный округ",
        "башкирия": "башкортостан", "татария": "татарстан", "кубань": "краснодарский край", "якутия": "саха",
        "чувашия": "чувашская", "удмуртия": "удмуртская", "чечня": "чеченская", "кузбасс": "кемеровская область",
        "ставрополье": "ставропольский край", "приморье": "приморский край", "забайкалье": "забайкальский край",
        "камчатка": "камчатский край", "прикамье": "пермский край", "чукотка": "чукотский автономный округ",
        "цфо": "центральный федеральный округ", "сзфо": "северо западный федеральный округ",
        "юфо": "южный федеральный округ", "скфо": "северо кавказский федеральный округ",
        "пфо": "приволжский федеральный округ", "урфо": "уральский федеральный округ",
        "сфо": "сибирский федеральный округ", "дфо": "дальневосточный федеральный округ",
    }.items()
}
# Знак исключения; «не исключаем» — не он. «не включая» — до «включая», который делит фразы.
_EXCLUDE_RE = re.compile(
    r"(?<!\bне )\b(?:кроме|без|минус|не\s+включая|не\s+считая|за\s+вычетом|исключ(?!ительн)\w*)\b"
)
# «исключить» в конце фразы относит к исключению то, что перед ним: «Москва и МО — исключить».
_POSTFIX_RE = re.compile(r"(?<!\bне )\bисключ(?!ительн)\w*")
_SPLIT_RE = re.compile(r"[,;/+·\n|&]|\b(?:и|с|со|плюс|включая|также|или|либо)\b")
_SEGMENT_RE = re.compile(r"([,;\n])")
_BRACKETS_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
_WORD_RE = re.compile(r"[a-zа-я]+")


def _clean(text) -> str:
    """Нижний регистр, ё → е, тире между словами — разделитель фраз, дефисы — пробелы: «Ростов-на-Дону» → «ростов
    на дону», «Москва — исключить» → «москва · исключить»."""
    low = re.sub(r"[ \t]+", " ", str(text or "").lower().replace("ё", "е"))
    return re.sub(r"[-‐‑‒–—]", " ", re.sub(r"\s[-‐‑‒–—]+(?=\s|$)", " ·", low))


def _phrase(text: str, aliases: bool = True) -> tuple[frozenset[str], bool, bool, bool]:
    """Фраза → (основы слов без служебных, назван тип «область/край», фраза — только «область/регион», фраза —
    только «регионы/другие города»)."""
    words: list[str] = []
    for word in _WORD_RE.findall(text):
        full = _ALIASES.get(_stem(word)) if aliases else None
        words += full.split() if full else [word]
    stems: set[str] = set()
    typed = own = rest = False
    for word in words:
        if len(word) < 2:
            continue
        stem = _stem(word)
        typed = typed or stem in _TYPE_WORDS
        own = own or stem in _OWN_AREA or word in _OWN_WORDS
        rest = rest or word in _REST_WORDS
        if stem not in _STOP and not word.startswith(_STOP_PREFIXES):
            stems.add(stem)
    return frozenset(stems), typed, own and not stems, rest and not stems


class _Index:
    """Справочник, разобранный для поиска по основам; строится раз на объект справочника."""

    def __init__(self, regions: dict[int, dict]):
        self.regions = {
            rid: {"id": rid, "name": name, "type": _KNOWN_TYPES.get(rid, "City"), "parent": _KNOWN_PARENTS.get(rid, 225)}
            for rid, name in KNOWN_REGIONS.items()
        }
        self.regions.update(regions)
        self.by_stem: dict[str, set[int]] = {}
        # Регионы с «область/край/республика» в названии: «Москва и Московская область», «Республика Татарстан».
        self.typed: set[int] = set()
        for rid, region in self.regions.items():
            stems, typed, _own, _rest = _phrase(_clean(region.get("name")), aliases=False)
            for stem in stems:
                self.by_stem.setdefault(stem, set()).add(rid)
            if typed:
                self.typed.add(rid)

    def chain(self, rid: int) -> list[int] | None:
        """Регион и его родители снизу вверх; None — региона нет ни в справочнике, ни среди основных."""
        if rid not in self.regions:
            return None
        out: list[int] = []
        while rid in self.regions and rid not in out:
            out.append(rid)
            rid = self.regions[rid].get("parent")
        return out

    def find(self, stems: frozenset[str], typed: bool, narrow: bool = True) -> set[int]:
        """Регионы, в названии которых есть все основы; названа «область/край» — сперва области, а не города. Не
        названа и narrow — сперва регионы без «область/край» в названии: «Москва» — город, а не «Москва и Московская
        область», «Омск» — не «Омская область». Исключению узкий выбор не нужен: «кроме Москвы» — и [-213], и [-1]."""
        found = set.intersection(*(self.by_stem.get(stem, set()) for stem in stems))
        if typed:
            found = {rid for rid in found if self.regions[rid].get("type") in _AREA_TYPES} or found
        elif narrow:
            found = (found - self.typed) or found
        return found

    def _first(self, rid: int, kind: str) -> int | None:
        for item in self.chain(rid) or []:
            if self.regions[item].get("type") == kind:
                return item
        return None

    def own_area(self, rid: int) -> int | None:
        """Область (край, республика) города — сам регион или ближайший родитель типа Administrative area."""
        return self._first(rid, "Administrative area")

    def country(self, rid: int) -> int | None:
        """Страна региона — он сам или его родитель типа Country."""
        return self._first(rid, "Country")

    def under(self, rid: int, ids) -> bool:
        """rid — один из ids или лежит внутри одного из них."""
        return any(item in ids for item in self.chain(rid) or ())


_index_lock = threading.Lock()
_index_cache: dict = {"regions": None, "size": 0, "index": None, "known": None}


def _geo_index(regions: dict[int, dict] | None) -> _Index:
    """Индекс справочника — один на объект (в справочнике Директа тысячи регионов); пустой — основные регионы."""
    with _index_lock:
        if not regions:
            if _index_cache["known"] is None:
                _index_cache["known"] = _Index({})
            return _index_cache["known"]
        if _index_cache["regions"] is not regions or _index_cache["size"] != len(regions):
            _index_cache.update(regions=regions, size=len(regions), index=_Index(regions))
        return _index_cache["index"]


def _pieces(clean: str) -> list[tuple[str, bool]]:
    """Текст → куски (текст, в скобках) по порядку. Скобки из одного слова исключения — не пояснение, а часть
    фразы: «Москва и МО (исключить)»."""
    pieces: list[tuple[str, bool]] = []
    pos = 0
    for m in _BRACKETS_RE.finditer(clean):
        inner = m.group(0)[1:-1]
        if _POSTFIX_RE.search(inner) and not _phrase(inner)[0]:
            continue
        pieces += [(clean[pos : m.start()], False), (inner, True)]
        pos = m.end()
    pieces.append((clean[pos:], False))
    return pieces


def _postfix(segment: str) -> int | None:
    """«Москва и МО — исключить», «(Москва исключена)»: слово исключения последнее во фразе до запятой — исключено
    то, что перед ним. → где начинается это слово; None — исключения в конце нет."""
    marks = list(_POSTFIX_RE.finditer(segment))
    if not marks:
        return None
    head, tail = segment[: marks[-1].start()], segment[marks[-1].end() :]
    if _phrase(tail)[0] or not _phrase(head)[0] or _EXCLUDE_RE.search(head):
        return None
    return marks[-1].start()


def _chunks(text, excluded: bool = False) -> list[tuple[str, bool, bool, str]]:
    """Текст → фразы (текст, исключение, необязательная, роль). До «кроме/без/минус» — где показывать, после — что
    исключить; «Москва и МО — исключить» — исключено то, что от запятой до слова.

    Роли: plus — место показа; lead — первое место исключения (не нашлось — текст не понять); soft — следующие места
    после «кроме»: место вне названного то ли показать, то ли исключить («Москва без Зеленограда, Питер»); unsure —
    то же после «;»; note — скобки после исключения, пояснение к нему; row — строка исключений целиком. Скобки —
    пояснение: регион показа из них можно взять, но не требовать; «кроме» в скобках действует только в них, зато
    исключение из скобок обязательно: «Россия (кроме Москвы)»."""
    out: list[tuple[str, bool, bool, str]] = []
    run: str | None = None  # роль следующего места исключения вне скобок; None — исключения нет
    for piece, bracket in _pieces(_clean(text)):
        if excluded or (bracket and run):
            role = "row" if excluded else "note"
            out += [(c, True, not excluded, role) for part in _EXCLUDE_RE.split(piece) for c in _SPLIT_RE.split(part)]
            continue
        state = None if bracket else run
        parts = _SEGMENT_RE.split(piece)
        for n in range(0, len(parts), 2):
            segment = parts[n]
            if state and n and parts[n - 1] == ";":
                state = "unsure"
            cut = _postfix(segment)
            if cut is not None:
                out += [(c, True, False, "lead") for c in _SPLIT_RE.split(segment[:cut])]
                continue
            for k, part in enumerate(_EXCLUDE_RE.split(segment)):
                if k:
                    state = "lead"
                for c in _SPLIT_RE.split(part):
                    out.append((c, state is not None, bracket and state is None, state or "plus"))
                    if state == "lead" and _phrase(c)[0]:
                        state = "soft"
        if not bracket:
            run = state
    return out


def names_place(text) -> bool:
    """Есть ли в тексте что-то, кроме служебных слов: «Исключить регионы» — нет, «кроме Москвы» — да."""
    return any(_phrase(chunk)[0] for chunk, *_rest in _chunks(text))


def parse_geo(text, regions: dict[int, dict] | None = None, excluded=(), allowed=()) -> dict | None:
    """Регион словами → {"plus": [(ids, обязательна)], "minus": [...], "open": bool}: у фразы — id подходящих
    регионов.

    excluded — тексты, исключённые целиком («Исключить регионы: Москва, СПб»); такое исключение, что целиком
    накрывает обязательное место показа («Регион: Москва» и старое «Исключить: Москва и МО»), спорит с ним — оно
    допустимо, но не обязательно. allowed — такие же тексты, но исключать их места можно, а не нужно (строки старше
    региона). Числа не учитываются. open — не всё в исключениях разобрано: лишнее исключение в группе не ошибка.
    None — уверенности нет: где показывать, не названо или не нашлось место показа или первое место после «кроме»."""
    index = _geo_index(regions)
    chunks = (
        _chunks(text)
        + [chunk for extra in excluded for chunk in _chunks(extra, excluded=True)]
        + [(c, minus, True, role) for extra in allowed for c, minus, _optional, role in _chunks(extra, excluded=True)]
    )
    out: dict = {"plus": [], "minus": [], "open": False}
    rows: list[tuple[int, set[int]]] = []  # исключения строками: (номер в minus, кандидаты без областей)
    prev: set[int] = set()
    shown: set[int] = set()
    for chunk, minus, optional, role in chunks:
        stems, typed, own, rest = _phrase(chunk)
        if own:
            found = {area for rid in prev if (area := index.own_area(rid))}
        elif stems:
            found = index.find(stems, typed, narrow=not minus)
        elif rest and not minus:
            # «Москва и регионы» — и остальная страна: её регионы допустимы, но не обязательны.
            found, optional = {country for rid in prev if (country := index.country(rid))}, True
        else:
            continue
        if not found:
            if role in ("plus", "lead") and not optional:
                return None
            out["open"] = out["open"] or minus
            continue
        ids = frozenset(found)
        if role == "unsure" or (role == "soft" and not any(index.under(c, shown) for c in found)):
            # После «кроме» место вне названного: показать его или исключить — не понять, допустимо и то, и то.
            out["plus"].append((ids, False))
            out["minus"].append((ids, False))
        elif minus:
            if role == "row":
                rows.append((len(out["minus"]), index.find(stems, typed) if stems else found))
            out["minus"].append((ids, not optional))
        else:
            out["plus"].append((ids, not optional))
            shown |= found
        prev = found
    required = [ids for ids, need in out["plus"] if need]
    for n, narrow in rows:
        if any(all(index.under(c, narrow) for c in ids) for ids in required):
            out["minus"][n] = (out["minus"][n][0], False)
    return out if required else None


def groups_fit(
    parsed: dict, groups: list[list[int]], regions: dict[int, dict] | None = None
) -> tuple[list[int], bool] | None:
    """Регионы групп черновика ([[213], [2]]) против разобранного parse_geo текста; None — id не знаем, сверить нечем.

    → (номера групп с нуля, что расходятся с текстом; все обязательные места показаны хоть одной группой). Группа
    может взять часть названного — по группе на город обычное дело. У группы каждый регион показа лежит внутри
    названного (он сам или его родитель); исключение, что попадает в её показ, исключено и в ней; её исключение есть
    в тексте или его регион показывает другая группа («Москва и МО»: [213] и [1, -213])."""
    index = _geo_index(regions)
    if any(index.chain(abs(rid)) is None for ids in groups for rid in ids):
        return None
    sets = [({rid for rid in ids if rid > 0}, {-rid for rid in ids if rid < 0}) for ids in groups]
    named = frozenset().union(*(ids for ids, _required in parsed["plus"]))
    dropped = frozenset().union(*(ids for ids, _required in parsed["minus"]))
    excluded = [ids for ids, required in parsed["minus"] if required]

    def elsewhere(rid: int, k: int) -> bool:
        """Регион показывает другая группа."""
        return any(
            index.under(rid, plus) and not index.under(rid, minus) for j, (plus, minus) in enumerate(sets) if j != k
        )

    bad: list[int] = []
    for k, (plus, minus) in enumerate(sets):
        fits = (
            all(index.under(rid, named) for rid in plus)
            and all(
                any(index.under(c, minus) for c in ids) for ids in excluded if any(index.under(c, plus) for c in ids)
            )
            and (parsed.get("open") or all(index.under(rid, dropped) or elsewhere(rid, k) for rid in minus))
        )
        if not fits:
            bad.append(k)
    shown = set().union(*(plus for plus, _minus in sets))
    covered = all(any(index.under(c, shown) for c in ids) for ids, required in parsed["plus"] if required)
    return bad, covered


def regions_fit(parsed: dict, region_ids: list[int], regions: dict[int, dict] | None = None) -> bool | None:
    """Совпадают ли регионы группы ([225, -1]) с разобранным parse_geo текстом; None — id не знаем, сверить нечем.

    Каждый регион показа лежит внутри названного; каждую обязательную фразу покрывает регион показа (она сама или
    её родитель); исключение, что попадает в показ, исключено и в группе; лишних исключений в группе нет."""
    fit = groups_fit(parsed, [region_ids], regions)
    return None if fit is None else not fit[0] and fit[1]


def geo_overlap(a, b, regions: dict[int, dict] | None = None) -> bool | None:
    """Есть ли у двух описаний общий регион — один совпадает с другим или лежит внутри него; None — не понять."""
    pa, pb = parse_geo(a, regions), parse_geo(b, regions)
    if pa is None or pb is None:
        return None
    index = _geo_index(regions)
    ids_a = frozenset().union(*(ids for ids, _required in pa["plus"]))
    ids_b = frozenset().union(*(ids for ids, _required in pb["plus"]))
    return any(not ids_b.isdisjoint(index.chain(x)) for x in ids_a) or any(
        not ids_a.isdisjoint(index.chain(y)) for y in ids_b
    )


def campaign_regions(user_id: int, campaign_row_id: int) -> list[int]:
    """Регионы групп кампании по последнему синку — объединение по всем группам."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT region_ids FROM direct_adgroups WHERE user_id = %s AND campaign_row_id = %s ORDER BY id",
            (user_id, campaign_row_id),
        ).fetchall()
    out: list[int] = []
    for row in rows:
        raw = row.get("region_ids") or "[]"
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        for rid in parse_region_ids(ids):
            if rid not in out:
                out.append(rid)
    return out


def _campaign(user_id: int, campaign_row_id: int) -> dict:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.account_id, c.campaign_id, c.name, a.connection_id, a.client_login, a.enabled
            FROM direct_campaigns c JOIN direct_accounts a ON a.id = c.account_id
            WHERE c.id = %s AND c.user_id = %s
            """,
            (campaign_row_id, user_id),
        ).fetchone()
    if row is None:
        raise YandexError("not-found")
    if not row.get("enabled"):
        raise YandexError("disabled")
    return row


def set_campaign_regions(
    user_id: int, campaign_row_id: int, region_ids: list[int], *, workspace_id: int | None = None
) -> dict:
    """Ставит регионы всем группам кампании в Директе и в локальной копии групп.

    Группы читаем из Директа, а не из базы: у свежей копии локальные группы могут быть ещё не
    загружены. Ошибки отдельных групп возвращаются списком, остальные группы обновляются.
    """
    if not any(rid > 0 for rid in region_ids):
        raise YandexError("args", "Нужен хотя бы один регион показа (id без минуса)")
    camp = _campaign(user_id, campaign_row_id)
    token = get_connection_access_token(camp["connection_id"], user_id)
    groups = direct_api.get_all(
        token,
        camp["client_login"],
        "adgroups",
        {"SelectionCriteria": {"CampaignIds": [int(camp["campaign_id"])]}, "FieldNames": ["Id", "RegionIds"]},
        "AdGroups",
    )
    groups = [g for g in groups if g.get("Id")]
    if not groups:
        raise YandexError("empty", "В кампании нет групп — регионы задаются в группах")
    before: list[int] = []
    for group in groups:
        for rid in parse_region_ids(group.get("RegionIds") or []):
            if rid not in before:
                before.append(rid)
    updated: list[int] = []
    errors: list[str] = []
    for chunk in direct_api.chunks(groups, 1000):
        result = direct_api.call(
            token,
            camp["client_login"],
            "adgroups",
            "update",
            {"AdGroups": [{"Id": int(g["Id"]), "RegionIds": region_ids} for g in chunk]},
        )
        failed = dict(direct_api.item_errors(result, "UpdateResults"))
        for index, group in enumerate(chunk):
            if index in failed:
                errors.append(f"группа {group['Id']}: {failed[index]}")
            else:
                updated.append(int(group["Id"]))
    if updated:
        with pool.connection() as conn:
            conn.execute(
                """
                UPDATE direct_adgroups SET region_ids = %s, synced_at = NOW()
                WHERE user_id = %s AND account_id = %s AND adgroup_id = ANY(%s)
                """,
                (json.dumps(region_ids), user_id, camp["account_id"], updated),
            )
    regions = load_regions(user_id)
    log_action(
        user_id,
        "adgroups",
        {"campaign_row_id": campaign_row_id, "region_ids": region_ids},
        {"groups": len(groups), "updated": len(updated), "errors": errors[:20]},
        "ok" if not errors else ("partial" if updated else "error"),
        workspace_id=workspace_id,
        title=f"Регионы «{camp['name']}»: {region_label(before, regions)} → {region_label(region_ids, regions)}",
        targets=[f"campaign:{campaign_row_id}"],
    )
    return {"groups": len(groups), "updated": len(updated), "errors": errors[:20]}


# --- Выбор регионов в кабинете ----------------------------------------------------------------------------
#
# Поле поиска в карточке кампании и группы: город или область по названию, как их пишут люди («питер», «екб»,
# «Казань»). Порядок: точное совпадение, начало названия, начало слова, вхождение; при равенстве — сперва
# города и области, потом страны и округа, потом сёла и районы. Номер региона тоже находится — для тех, кто
# знает id Директа.

_PICK_ORDER = {"City": 0, "Administrative area": 0, "Country": 1, "Region": 1}


def _plain(text) -> str:
    """Название для сравнения: строчные, «ё» → «е», дефисы — пробелы («Санкт-Петербург» = «санкт петербург»)."""
    return _norm(str(text or "").replace("-", " "))


def _pick_rank(name: str, query: str) -> int | None:
    if name == query:
        return 0
    if name.startswith(query):
        return 1
    if any(word.startswith(query) for word in name.split()):
        return 2
    if query in name:
        return 3
    return None


def _source(regions: dict[int, dict]) -> dict[int, dict]:
    """Справочник Директа, а без него — основные регионы с родителями и типами."""
    if regions:
        return regions
    return {
        rid: {
            "id": rid,
            "name": name,
            "type": _KNOWN_TYPES.get(rid, "City"),
            "parent": _KNOWN_PARENTS.get(rid, 225),
        }
        for rid, name in KNOWN_REGIONS.items()
    }


def _region_item(source: dict[int, dict], region: dict) -> dict:
    return {
        "id": region["id"],
        "name": region["name"],
        "type": _TYPE_LABELS.get(region["type"], ""),
        "path": _path(source, region),
    }


def suggest_regions(user_id: int, query: str, limit: int = SEARCH_LIMIT) -> dict:
    """Подсказки к полю «Регионы»: {"regions": [{id, name, type, path}], "note"?}; пустой запрос — пусто."""
    q = _plain(query)
    if not q:
        return {"regions": []}
    regions = load_regions(user_id)
    source = _source(regions)
    queries = [q]
    alias = _ALIASES.get(_stem(q)) if " " not in q else None
    if alias and alias not in queries:
        queries.append(alias)
    ranked = []
    for region in source.values():
        name = _plain(region["name"])
        ranks = [r for r in (_pick_rank(name, text) for text in queries) if r is not None]
        if q.isdigit() and int(q) == region["id"]:
            ranks.append(0)
        if not ranks:
            continue
        ranked.append((min(ranks), _PICK_ORDER.get(region["type"], 2), len(name), region["id"], region))
    ranked.sort(key=lambda item: item[:4])
    out: dict = {"regions": [_region_item(source, region) for *_, region in ranked[: max(1, min(limit, 50))]]}
    if not regions:
        out["note"] = "Справочник регионов Директа сейчас недоступен — показываю только крупные города"
    return out


def regions_named(user_id: int, region_ids: list[int]) -> list[dict]:
    """Выбранные регионы с названиями для поля выбора: [{id, name, type, path, minus}], минус — «кроме»."""
    source = _source(load_regions(user_id))
    out = []
    for rid in parse_region_ids(region_ids):
        region = source.get(abs(rid)) or {"id": abs(rid), "name": _name(source, abs(rid)), "type": "", "parent": None}
        out.append({**_region_item(source, region), "minus": rid < 0})
    return out
