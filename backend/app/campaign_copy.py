"""Копия кампании на новый город или сайт: упоминания старого города и сайта → замены построчно.

Без ИИ и бесплатно: формы города берутся из словаря крупных городов и простых правил склонения,
падеж замены — по форме найденного слова и предлогу перед ним («в Москве» → «в Казани», «по Москве» →
«по Казани», «московский» → «казанский»). Ключ строки (объявление, фраза, быстрая ссылка, уточнение и
поле) одинаков в предпросмотре и при копии: снятые галочки приходят списком skip, и копия считает замены
заново по тем же правилам.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

# Падежи: именительный, родительный, дательный, винительный, творительный, предложный.
NOM, GEN, DAT, ACC, INS, PREP = range(6)
MAX_ITEMS = 2000

# Формы, которых правилами не получить, и прилагательные (основа на -ск: + ий/ая/ое/ие/ого…).
_CITIES: dict[str, tuple[tuple[str, ...] | None, str | None]] = {
    "Москва": (None, "московск"),
    "Санкт-Петербург": (None, "петербургск"),
    "Нижний Новгород": (
        ("Нижний Новгород", "Нижнего Новгорода", "Нижнему Новгороду", "Нижний Новгород", "Нижним Новгородом", "Нижнем Новгороде"),
        "нижегородск",
    ),
    "Великий Новгород": (
        ("Великий Новгород", "Великого Новгорода", "Великому Новгороду", "Великий Новгород", "Великим Новгородом", "Великом Новгороде"),
        "новгородск",
    ),
    "Нижний Тагил": (
        ("Нижний Тагил", "Нижнего Тагила", "Нижнему Тагилу", "Нижний Тагил", "Нижним Тагилом", "Нижнем Тагиле"),
        "тагильск",
    ),
    "Набережные Челны": (
        ("Набережные Челны", "Набережных Челнов", "Набережным Челнам", "Набережные Челны", "Набережными Челнами", "Набережных Челнах"),
        "набережночелнинск",
    ),
    "Ростов-на-Дону": (
        ("Ростов-на-Дону", "Ростова-на-Дону", "Ростову-на-Дону", "Ростов-на-Дону", "Ростовом-на-Дону", "Ростове-на-Дону"),
        "ростовск",
    ),
    "Чебоксары": (("Чебоксары", "Чебоксар", "Чебоксарам", "Чебоксары", "Чебоксарами", "Чебоксарах"), "чебоксарск"),
    "Химки": (("Химки", "Химок", "Химкам", "Химки", "Химками", "Химках"), "химкинск"),
    "Мытищи": (("Мытищи", "Мытищ", "Мытищам", "Мытищи", "Мытищами", "Мытищах"), "мытищинск"),
    "Люберцы": (("Люберцы", "Люберец", "Люберцам", "Люберцы", "Люберцами", "Люберцах"), "люберецк"),
    "Уфа": (None, "уфимск"),
    "Тула": (None, "тульск"),
    "Пенза": (None, "пензенск"),
    "Калуга": (None, "калужск"),
    "Вологда": (None, "вологодск"),
    "Махачкала": (None, "махачкалинск"),
    "Балашиха": (None, "балашихинск"),
    "Ялта": (None, "ялтинск"),
    "Анапа": (None, "анапск"),
    "Самара": (None, "самарск"),
    "Кострома": (None, "костромск"),
    "Ярославль": (None, "ярославск"),
    "Владивосток": (None, "владивостокск"),
    "Барнаул": (None, "барнаульск"),
    "Ставрополь": (None, "ставропольск"),
    "Тольятти": (None, "тольяттинск"),
    "Сочи": (None, "сочинск"),
    "Кемерово": (None, "кемеровск"),
    "Иваново": (None, "ивановск"),
    "Королёв": (None, "королёвск"),
}
# Разговорные названия: ищем их в текстах оригинала, в копию пишем полное название.
_ALIASES: dict[str, tuple[list[str], list[str]]] = {
    "Санкт-Петербург": (["Петербург", "Питер", "СПб"], ["питерск"]),
    "Москва": (["Мск"], []),
    "Екатеринбург": (["Екб"], []),
}
_ADJ_ENDINGS = ("ими", "ий", "ая", "ое", "ие", "ого", "ой", "ому", "ую", "им", "ом", "их", "ым", "ых", "ыми", "ый")
_HUSHING = "гкхжшчщц"

# Предлог перед словом → падежи по убыванию вероятности.
_PREP_CASES: dict[str, tuple[int, ...]] = {
    **dict.fromkeys(("в", "во", "на"), (PREP, ACC)),
    **dict.fromkeys(("о", "об", "обо", "при"), (PREP,)),
    **dict.fromkeys(("к", "ко", "по"), (DAT,)),
    **dict.fromkeys(("из", "от", "до", "для", "у", "без", "около", "возле", "вокруг", "из-под", "с", "со", "кроме"), (GEN, INS)),
    **dict.fromkeys(("под", "над", "за", "перед", "между"), (INS, ACC)),
    **dict.fromkeys(("через", "про"), (ACC,)),
}
_DEFAULT_ORDER = (NOM, GEN, PREP, DAT, ACC, INS)

_LETTERS = "0-9a-zа-яё"
_TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
         "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
    )
)

# Поля для замены: ключ поля → (подпись, предел длины Директа, режим).
FIELDS = {
    "Title": ("Заголовок", 56, "text"),
    "Title2": ("Доп. заголовок", 30, "text"),
    "Text": ("Текст", 81, "text"),
    "Href": ("Ссылка", 1024, "href"),
    "DisplayUrlPath": ("Отображаемая ссылка", 20, "path"),
    "Titles": ("Заголовок", 56, "text"),
    "Texts": ("Текст", 81, "text"),
    "Keyword": ("Ключевая фраза", 4096, "keyword"),
    "SlTitle": ("Быстрая ссылка", 30, "text"),
    "SlDescription": ("Описание быстрой ссылки", 60, "text"),
    "SlHref": ("Адрес быстрой ссылки", 1024, "href"),
    "Callout": ("Уточнение", 25, "text"),
}
_NARROW = re.compile(r"[.,!:;\"]")
_AREA_AFTER = re.compile(r"\s+(?:обл|област|край|кра[ея]|регион)", re.IGNORECASE)
KEYWORD_MAX_WORDS = 7
KEYWORD_MAX_WORD = 35


def _norm(text: str) -> str:
    return str(text or "").lower().replace("ё", "е")


def translit(text: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in _norm(text).replace(" ", "-"))


def decline(name: str) -> tuple[str, ...]:
    """Шесть падежей названия по простым правилам; несклоняемые и составные — одна форма."""
    name = " ".join(str(name or "").split())
    known = _CITIES.get(name)
    if known and known[0]:
        return known[0]
    if not name:
        return ("",) * 6
    if " " in name:
        return (name,) * 6
    if "-" in name:
        # Санкт-Петербург, Петропавловск-Камчатский… склоняем последнюю часть, если она «простая».
        head, _, tail = name.rpartition("-")
        if tail[:1].isupper() and not tail.lower().endswith(("ий", "ый", "ой")):
            return tuple(f"{head}-{form}" for form in decline(tail))
        return (name,) * 6
    low = name.lower()
    stem = name[:-1]
    if low.endswith("ия"):
        base = name[:-2]
        return (name, base + "ии", base + "ии", base + "ию", base + "ией", base + "ии")
    if low.endswith("я"):
        return (name, stem + "и", stem + "е", stem + "ю", stem + "ей", stem + "е")
    if low.endswith("а"):
        gen = stem + ("и" if stem[-1:].lower() in _HUSHING[:7] else "ы")
        return (name, gen, stem + "е", stem + "у", stem + "ой", stem + "е")
    if low.endswith("ль") or low.endswith("ославль"):
        return (name, stem + "я", stem + "ю", name, stem + "ем", stem + "е")
    if low.endswith("ь"):
        return (name, stem + "и", stem + "и", name, name + "ю", stem + "и")
    if low.endswith("й"):
        return (name, stem + "я", stem + "ю", name, stem + "ем", stem + "е")
    if low[-1:] in "оеиуюэыё" or not low[-1:].isalpha():
        return (name,) * 6
    ins = "ем" if low[-1] in "жшчщц" else "ом"
    return (name, name + "а", name + "у", name, name + ins, name + "е")


def adjective(name: str) -> str | None:
    """Основа прилагательного («московск»); для «-а» без словаря не гадаем."""
    name = " ".join(str(name or "").split())
    known = _CITIES.get(name)
    if known:
        return known[1]
    low = name.lower()
    if not low or " " in low or "-" in low or not low.isalpha():
        return None
    if low.endswith("ск"):
        return low
    if low.endswith("ь") and not low.endswith("ль"):
        return low[:-1] + "ск"
    if low[-1] in "бвдзклмнпрстфх" and not low.endswith("ск"):
        return low + "ск"
    return None


def city_from_regions(region_ids, regions: dict[int, dict]) -> str:
    """Город для замен: первый регион-город показа; «Москва и Московская область» → «Москва»."""
    ids = [int(r) for r in region_ids or [] if int(r) > 0]
    named = [regions.get(r) or {} for r in ids]
    for region in named:
        if region.get("type") == "City" and region.get("name"):
            return str(region["name"])
    for region in named:
        name = str(region.get("name") or "")
        if " и " in name and "област" in name.lower():
            return name.split(" и ")[0].strip()
    return ""


def site_host(value) -> str:
    """Домен сайта без www: «https://www.Shop.ru/x» → «shop.ru»."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        host = (urlsplit(text).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    host = host[4:] if host.startswith("www.") else host
    return host if "." in host else ""


@dataclass
class _Form:
    case: int | None  # None — прилагательное
    alias: bool = False


@dataclass
class Rules:
    """Что на что меняем; stats — что сделано при копии."""

    from_city: str = ""
    to_city: str = ""
    from_site: str = ""
    to_site: str = ""
    skip: set[str] = field(default_factory=set)
    stats: dict = field(default_factory=lambda: {"applied": 0, "skipped": 0, "left": [], "too_long": [], "doubtful": []})

    def __post_init__(self) -> None:
        self.from_city = " ".join(self.from_city.split())
        self.to_city = " ".join(self.to_city.split())
        self.from_site = site_host(self.from_site)
        self.to_site = site_host(self.to_site)
        self._target = decline(self.to_city) if self.to_city else None
        self._target_adj = adjective(self.to_city) if self.to_city else None
        same_city = _norm(self.from_city) == _norm(self.to_city)
        self.city_on = bool(self.from_city) and not same_city
        self.site_on = bool(self.from_site and self.to_site and self.from_site != self.to_site)
        # Формы из словаря — точно город; формы по правилам («ковров», «шахты») бывают обычными словами.
        self._known = self.from_city in _CITIES or self.from_city in _ALIASES
        self._doubtful: list[str] = []
        self._forms: dict[str, list[_Form]] = {}
        self._adj_stems: list[str] = []
        if self.city_on:
            for case, form in enumerate(decline(self.from_city)):
                self._forms.setdefault(_norm(form), []).append(_Form(case))
            stem = adjective(self.from_city)
            if stem:
                self._adj_stems.append(_norm(stem))
            aliases, adj_aliases = _ALIASES.get(self.from_city, ([], []))
            for alias in aliases:
                # Сокращения («СПб», «Мск») не склоняются: «в СПб» — это «в Петербурге».
                abbr = sum(c.isupper() for c in alias) > 1 or len(alias) <= 3
                forms = (alias,) * 6 if abbr else decline(alias)
                for case, form in enumerate(forms):
                    self._forms.setdefault(_norm(form), []).append(_Form(case, alias=True))
            self._adj_stems += [_norm(a) for a in adj_aliases]
        words = sorted(self._forms, key=len, reverse=True)
        adj = [f"{re.escape(s)}(?:{'|'.join(_ADJ_ENDINGS)})" for s in sorted(self._adj_stems, key=len, reverse=True)]
        parts = [re.escape(w).replace(r"\ ", r"\s+").replace(r"\-", r"\s*-\s*") for w in words] + adj
        self._city_re = (
            re.compile(rf"(?<![{_LETTERS}])(?:{'|'.join(parts)})(?![{_LETTERS}])", re.IGNORECASE) if parts else None
        )
        self._translit_re = None
        if self.city_on and self.to_city:
            src = translit(self.from_city)
            if src:
                self._translit_re = re.compile(rf"(?<![a-z0-9]){re.escape(src)}(?![a-z0-9])", re.IGNORECASE)
        self._site_re = (
            re.compile(rf"(?<![{_LETTERS}.-])(?:www\.)?{re.escape(self.from_site)}(?![{_LETTERS}-]|\.[{_LETTERS}])", re.IGNORECASE)
            if self.site_on
            else None
        )

    @property
    def active(self) -> bool:
        return self.city_on or self.site_on

    # --- Замена в одной строке --------------------------------------------------------------------

    @staticmethod
    def _key(found: str) -> str:
        return " ".join(_norm(found).replace(" -", "-").replace("- ", "-").split())

    def _ambiguous(self, found: str, before: str) -> str:
        """Почему найденное слово, возможно, не город: "word" — со строчной буквы форма не из словаря
        («чистка ковров» при городе Ковров), "proper" — прилагательное с заглавной посреди фразы
        («Казанский собор»); пусто — город."""
        forms = self._forms.get(self._key(found))
        if forms:
            if found[:1].islower() and not self._known and not any(f.alias for f in forms):
                return "word"
            return ""
        tail = before.rstrip()
        if found[:1].isupper() and tail and tail[-1] not in ".!?…":
            return "proper"
        return ""

    def _city_word(self, found: str, before: str) -> str | None:
        """Форма нового города для найденного слова; None — формы нет (прилагательное без словаря)."""
        key = self._key(found)
        forms = self._forms.get(key)
        if forms:
            if not self._target:
                return None
            cases = {f.case for f in forms}
            prev = re.search(r"([a-zа-яё-]+)\s*$", _norm(before))
            order = _PREP_CASES.get(prev.group(1), ()) if prev else ()
            chosen = next((c for c in (*order, *_DEFAULT_ORDER) if c in cases), NOM)
            return _match_case(found, self._target[chosen])
        if not self._target_adj:
            return None
        for stem in self._adj_stems:
            if key.startswith(stem):
                return _match_case(found, self._target_adj + found[len(stem):].lower(), adjective=True)
        return None

    def replace(self, text: str, mode: str = "text") -> tuple[str, list[str]]:
        """Новый текст и упоминания, которые заменить не удалось."""
        text = str(text or "")
        left: list[str] = []
        self._doubtful = []
        if not text or not self.active:
            return text, left
        if mode == "href":
            return self._href(text), left
        out = text
        if self._site_re is not None:
            out = self._site_re.sub(self.to_site, out)
        if self._city_re is not None:

            def sub(m: re.Match) -> str:
                # «Московская область» — не «Казанская область»: область меняет только человек.
                region = _AREA_AFTER.match(m.string, m.end()) and _norm(m.group(0)) not in self._forms
                before = m.string[: m.start()]
                doubt = "" if region else self._ambiguous(m.group(0), before)
                if doubt == "word" and mode == "text":
                    return m.group(0)  # обычное слово в тексте объявления — не трогаем
                if doubt == "proper" and mode == "text":
                    left.append(m.group(0))  # имя собственное («Казанский собор») — решает человек
                    return m.group(0)
                new = None if region else self._city_word(m.group(0), before)
                if new is not None and doubt:
                    # Во фразах всё строчными: «ковров» может быть и городом — замену показываем, но с пометкой.
                    self._doubtful.append(m.group(0))
                if new is None:
                    left.append(m.group(0))
                    return m.group(0)
                return new

            out = self._city_re.sub(sub, out)
        if mode == "path" and self._translit_re is not None:
            out = self._translit_re.sub(lambda m: _match_case(m.group(0), translit(self.to_city)), out)
        return out, left

    def _href(self, href: str) -> str:
        if not self.site_on:
            return href
        raw = href if "://" in href else "https://" + href
        try:
            parts = urlsplit(raw)
            host = (parts.hostname or "").lower()
        except ValueError:
            return href
        if (host[4:] if host.startswith("www.") else host) != self.from_site:
            return href
        netloc = parts.netloc
        at = netloc.rfind("@") + 1
        port = f":{parts.port}" if parts.port else ""
        new = urlunsplit((parts.scheme, netloc[:at] + self.to_site + port, parts.path, parts.query, parts.fragment))
        return new if "://" in href else new.split("://", 1)[1]

    # --- При копии -------------------------------------------------------------------------------

    def change(self, key: str, text, field_name: str):
        """Текст поля для копии: с заменами, если строку не сняли. Счётчики — в stats."""
        if not self.active or not text:
            return text
        label, limit, mode = FIELDS[field_name]
        new, left = self.replace(str(text), mode)
        doubtful = self._doubtful
        if left:
            self.stats["left"].extend(left)
        if new == text:
            return text
        if key in self.skip:
            self.stats["skipped"] += 1
            return text
        self.stats["applied"] += 1
        self.stats["doubtful"].extend(doubtful)
        over = too_long(new, field_name)
        if over:
            self.stats["too_long"].append(f"{label} «{new}» — {over}")
        return new

    def summary(self) -> dict:
        left = list(dict.fromkeys(self.stats["left"]))
        return {
            "from_city": self.from_city,
            "to_city": self.to_city,
            "from_site": self.from_site if self.site_on else "",
            "to_site": self.to_site if self.site_on else "",
            "applied": self.stats["applied"],
            "skipped": self.stats["skipped"],
            "left": left[:20],
            "too_long": self.stats["too_long"][:20],
            "doubtful": list(dict.fromkeys(self.stats["doubtful"]))[:20],
        }


def _match_case(found: str, new: str, *, adjective: bool = False) -> str:
    letters = [c for c in found if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return new.upper()
    if letters and letters[0].islower():
        return new.lower()
    return new[:1].upper() + new[1:] if adjective else new


def too_long(text: str, field_name: str) -> str:
    """Почему Директ может не принять поле после замены; пусто — в пределах."""
    _label, limit, mode = FIELDS[field_name]
    if mode == "keyword":
        words = [w for w in str(text).split() if not w.startswith("-")]
        if len(words) > KEYWORD_MAX_WORDS:
            return f"больше {KEYWORD_MAX_WORDS} слов"
        if any(len(w.strip('!+"[]')) > KEYWORD_MAX_WORD for w in words):
            return f"слово длиннее {KEYWORD_MAX_WORD} знаков"
        return ""
    size = len(_NARROW.sub("", text)) if mode == "text" else len(text)
    return f"длиннее {limit} знаков" if size > limit else ""


# --- Что есть в кампании ------------------------------------------------------------------------


@dataclass
class Text:
    key: str
    field: str  # ключ из FIELDS
    where: str
    text: str


def ad_texts(ads: list[dict], group_names: dict[int, str]) -> list[Text]:
    """Поля объявлений по тем же правилам, что и копия (_ad_for_add): пустые заголовки не в счёт."""
    out: list[Text] = []
    for ad in ads:
        ad_id = int(ad.get("Id") or 0)
        where = f"Группа «{group_names.get(int(ad.get('AdGroupId') or 0), ad.get('AdGroupId'))}»"
        if ad.get("ResponsiveAd"):
            r = ad["ResponsiveAd"]
            titles = [str(t["Title"]) for t in r.get("Titles") or [] if t.get("Title")]
            texts = [str(t["Text"]) for t in r.get("Texts") or [] if t.get("Text")]
            out += [Text(f"ad:{ad_id}:Titles.{i}", "Titles", where, t) for i, t in enumerate(titles)]
            out += [Text(f"ad:{ad_id}:Texts.{i}", "Texts", where, t) for i, t in enumerate(texts)]
            body, names = r, ("Href", "DisplayUrlPath")
        elif ad.get("TextAd"):
            body, names = ad["TextAd"], ("Title", "Title2", "Text", "Href", "DisplayUrlPath")
        elif ad.get("TextImageAd"):
            body, names = ad["TextImageAd"], ("Href",)
        else:
            continue
        out += [Text(f"ad:{ad_id}:{n}", n, where, str(body[n])) for n in names if body.get(n)]
    return out


def keyword_texts(keywords: list[dict], group_names: dict[int, str]) -> list[Text]:
    return [
        Text(f"kw:{int(k['Id'])}", "Keyword", f"Группа «{group_names.get(int(k.get('AdGroupId') or 0), k.get('AdGroupId'))}»", str(k["Keyword"]))
        for k in keywords
        if k.get("Id") and k.get("Keyword") and not str(k["Keyword"]).startswith("---")
    ]


def sitelink_texts(sets: list[dict]) -> list[Text]:
    out: list[Text] = []
    for s in sets:
        for i, link in enumerate(s.get("Sitelinks") or []):
            for name, key in (("Title", "SlTitle"), ("Description", "SlDescription"), ("Href", "SlHref")):
                if link.get(name):
                    out.append(Text(f"sl:{int(s['Id'])}:{i}:{name}", key, "Быстрые ссылки", str(link[name])))
    return out


def callout_texts(exts: list[dict]) -> list[Text]:
    return [
        Text(f"co:{int(e['Id'])}", "Callout", "Уточнения", str(e["Callout"]["CalloutText"]))
        for e in exts
        if e.get("Id") and (e.get("Callout") or {}).get("CalloutText")
    ]


def proposals(texts: list[Text], rules: Rules) -> dict:
    """Строки «было → станет» для окна копии и упоминания, которые заменить нечем."""
    items: list[dict] = []
    left: Counter = Counter()
    for t in texts:
        label, _limit, mode = FIELDS[t.field]
        new, missed = rules.replace(t.text, mode)
        doubtful = rules._doubtful
        left.update(missed)
        if new == t.text:
            continue
        over = too_long(new, t.field)
        items.append(
            {
                "key": t.key,
                "field": label,
                "where": t.where,
                "before": t.text,
                "after": new,
                "too_long": over or None,
                "doubtful": bool(doubtful) or None,
            }
        )
    return {
        "items": items[:MAX_ITEMS],
        "more": max(0, len(items) - MAX_ITEMS),
        "left": [{"text": text, "count": n} for text, n in left.most_common(20)],
    }


def negatives_with_city(negatives: list[str], city: str) -> list[str]:
    """Минус-слова, в которых есть новый город: копия не покажется по запросам с ним."""
    if not city:
        return []
    probe = Rules(from_city=city, to_city="")
    if probe._city_re is None:
        return []
    return list(dict.fromkeys(n for n in negatives if probe._city_re.search(str(n))))[:10]


def apply_to_ad(entry: dict, ad_id: int, rules: Rules | None) -> dict:
    """Замены в элементе ads.add (результат _ad_for_add) по ключам строк объявления."""
    if not rules or not rules.active or not entry:
        return entry
    for kind, body in entry.items():
        if kind == "ResponsiveAd":
            for name in ("Titles", "Texts"):
                body[name] = [rules.change(f"ad:{ad_id}:{name}.{i}", v, name) for i, v in enumerate(body.get(name) or [])]
            names = ("Href", "DisplayUrlPath")
        elif kind == "TextAd":
            names = ("Title", "Title2", "Text", "Href", "DisplayUrlPath")
        elif kind == "TextImageAd":
            names = ("Href",)
        else:
            continue
        for name in names:
            if body.get(name):
                body[name] = rules.change(f"ad:{ad_id}:{name}", body[name], name)
    return entry


def rules_from(raw: dict | None, *, from_city: str = "", to_city: str = "", from_site: str = "", to_site: str = "") -> Rules | None:
    """Правила из запроса копии; пустые поля запроса — значения по умолчанию из регионов и проектов."""
    if raw is None:
        return None
    raw = raw if isinstance(raw, dict) else {}

    def pick(name: str, default: str) -> str:
        value = raw.get(name)
        return default if value is None else str(value)[:100]

    return Rules(
        from_city=pick("from_city", from_city),
        to_city=pick("to_city", to_city),
        from_site=pick("from_site", from_site),
        to_site=pick("to_site", to_site),
        skip={str(k) for k in raw.get("skip") or [] if k},
    )


def replace_name(name: str, rules: Rules | None) -> str:
    if not rules or not rules.city_on:
        return name
    new, _left = rules.replace(name)
    return new


# --- Значения по умолчанию и предпросмотр -------------------------------------------------------


def ws_site(ws: dict | None) -> str:
    """Домен сайта проекта: поле «Сайт», сайт счётчика или адрес из шаблона ссылки."""
    if not ws:
        return ""
    for value in (ws.get("site"), ws.get("counter_site"), ws.get("url_template")):
        host = site_host(value)
        if host:
            return host
    return ""


def defaults(user_id: int, groups: list[dict], source_ws: dict | None, target_ws: dict | None, region_ids) -> dict:
    """Старый и новый город — по регионам групп оригинала и регионам копии; сайты — по проектам."""
    from app import geo

    regions = geo._source(geo.load_regions(user_id))
    src_ids: list[int] = []
    for g in groups:
        for rid in geo.parse_region_ids(g.get("RegionIds") or []):
            if rid not in src_ids:
                src_ids.append(rid)
    from_city = city_from_regions(src_ids, regions)
    to_city = city_from_regions(region_ids, regions) if region_ids else from_city
    from_site = ws_site(source_ws)
    return {"from_city": from_city, "to_city": to_city, "from_site": from_site, "to_site": ws_site(target_ws) or from_site}


def preview(
    user_id: int,
    campaign_row_id: int,
    *,
    target_account_id: int | None,
    source_ws: dict | None,
    target_ws: dict | None,
    region_ids: list[int] | None = None,
    replace: dict | None = None,
) -> dict:
    """Что изменится в копии: строки замен «было → станет», корректировки ставок и наборы минус-фраз.

    Ничего не пишет; читает кампанию оригинала из Директа теми же полями, что и копия.
    """
    from app import actions as a
    from app.yandex import YandexError, get_connection_access_token

    src = a._campaign_full(user_id, campaign_row_id)
    ctype = a._supported(src)
    block = a._TYPE_BLOCK[ctype]
    unified = ctype == "UNIFIED_CAMPAIGN"
    target_id = int(target_account_id or src["account_id"])
    same = target_id == int(src["account_id"])
    token = get_connection_access_token(src["connection_id"], user_id)
    login = src["client_login"]
    cid = int(src["campaign_id"])
    item = a._fetch_campaign(token, login, cid, ctype)
    group_fields = {"FieldNames": ["Id", "Name", "RegionIds", "NegativeKeywords", "Type"]}
    groups = a._get_all(token, login, "adgroups", {"SelectionCriteria": {"CampaignIds": [cid]}, **group_fields}, "AdGroups")
    names = {int(g["Id"]): str(g.get("Name") or g["Id"]) for g in groups if g.get("Id")}
    warnings: list[str] = []

    keywords = a._get_all(
        token, login, "keywords", {"SelectionCriteria": {"CampaignIds": [cid]}, **a._KEYWORD_COPY_FIELDS}, "Keywords"
    )
    try:
        ads = a._get_all(
            token, login, "ads", {"SelectionCriteria": {"CampaignIds": [cid]}, **a._AD_COPY_FIELDS}, "Ads", version="v501"
        )
    except YandexError:
        plain = {k: v for k, v in a._AD_COPY_FIELDS.items() if k != "ShoppingAdFieldNames"}
        ads = a._get_all(token, login, "ads", {"SelectionCriteria": {"CampaignIds": [cid]}, **plain}, "Ads")
    ads = [ad for ad in ads if ad.get("State") != "ARCHIVED"]
    base = defaults(user_id, groups, source_ws, target_ws, region_ids or [])
    if not base["from_site"]:
        # У проекта не указан сайт — берём самый частый домен ссылок объявлений.
        hosts = Counter(site_host(a._ad_body(ad).get("Href")) for ad in ads)
        hosts.pop("", None)
        if hosts:
            base["from_site"] = hosts.most_common(1)[0][0]
            base["to_site"] = ws_site(target_ws) or base["from_site"]
    rules = rules_from(replace or {}, **base)
    bodies = [a._ad_body(ad) for ad in ads]
    set_ids = sorted({int(b["SitelinkSetId"]) for b in bodies if b.get("SitelinkSetId")})
    ext_ids = sorted(
        {
            int(e["AdExtensionId"])
            for b in bodies
            for e in b.get("AdExtensions") or []
            if e.get("AdExtensionId") and e.get("Type") in (None, "CALLOUT")
        }
    )
    sets: list[dict] = []
    exts: list[dict] = []
    if rules.active:
        try:
            if set_ids:
                sets = a._get_all(
                    token, login, "sitelinks", {"SelectionCriteria": {"Ids": set_ids}, "FieldNames": ["Id", "Sitelinks"]}, "SitelinksSets"
                )
            if ext_ids:
                exts = a._get_all(
                    token,
                    login,
                    "adextensions",
                    {"SelectionCriteria": {"Ids": ext_ids}, "FieldNames": ["Id", "Type"], "CalloutFieldNames": ["CalloutText"]},
                    "AdExtensions",
                )
        except YandexError as exc:
            warnings.append(f"Быстрые ссылки и уточнения не прочитаны: {exc.message or exc.code}")
    texts = (
        ad_texts(ads, names) + keyword_texts(keywords, names) + sitelink_texts(sets) + callout_texts(exts)
        if rules.active
        else []
    )
    out = proposals(texts, rules)

    negatives = list(((item.get("NegativeKeywords") or {}).get("Items")) or [])
    for g in groups:
        negatives += ((g.get("NegativeKeywords") or {}).get("Items")) or []
    if rules.city_on and rules.to_city:
        hits = negatives_with_city(negatives, rules.to_city)
        if hits:
            warnings.append(
                "В минус-словах оригинала есть новый город: "
                + ", ".join(f"«{h}»" for h in hits[:5])
                + " — копия не покажется по таким запросам. Уберите их в копии."
            )

    try:
        modifiers = a.read_bid_modifiers(token, login, cid, unified=unified)
        payload, skipped = a.bid_modifiers_for_add(modifiers, 0, {int(g): int(g) for g in names}, same=same)
        bid_modifiers = {
            "count": sum(len(v) if isinstance(v, list) else 1 for p in payload for k, v in p.items() if k not in ("CampaignId", "AdGroupId")),
            "skipped": skipped,
        }
    except YandexError:
        bid_modifiers = None

    shared = ((item.get(block) or {}).get("NegativeKeywordSharedSetIds") or {}).get("Items") or []
    shared_sets = None
    if shared and not same:
        try:
            found = a.read_shared_sets(token, login, shared)
            shared_sets = {"count": len(found), "names": [str(s.get("Name") or s["Id"]) for s in found][:10]}
        except YandexError:
            shared_sets = {"count": len(shared), "names": []}

    return {
        **out,
        "from_city": rules.from_city,
        "to_city": rules.to_city,
        "from_site": rules.from_site,
        "to_site": rules.to_site,
        "defaults": base,
        "suggested_name": replace_name(str(src["name"]), rules),
        "same_account": same,
        "bid_modifiers": bid_modifiers,
        "shared_sets": shared_sets,
        "warnings": warnings,
    }
