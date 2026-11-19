"""Правила Директа для доски сборки: что Директ не примет и что может не пройти модерацию.

Технические пределы (длина, число слов, недопустимые знаки) — блокирующие: такой черновик Директ
не создаст. Риски модерации (превосходная степень, слова заглавными, несколько «!», телефон в
тексте) и пересечения фраз — предупреждения: решает маркетолог. Чистые функции, без базы и Директа.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from app import query_cleaning
from app.ad_extras import CALLOUT_LEN, SITELINK_DESC_LEN, SITELINK_TITLE_LEN
from app.ads import TEXT_LEN, TITLE_LEN
from app.negative_lists import MAX_WORD_CHARS, MAX_WORDS

KEYWORD_LEN = 4096
# Длина слова в объявлении: в заголовке до 22 знаков, в тексте до 23.
WORD_LEN = {"title": 22, "text": 23}
TEXT_LIMITS = {
    "title": TITLE_LEN,
    "text": TEXT_LEN,
    "sitelink": SITELINK_TITLE_LEN,
    "description": SITELINK_DESC_LEN,
    "callout": CALLOUT_LEN,
}
# Во фразах: буквы, цифры, пробел и операторы ! + - " [ ] ( ) | . '; в минус-фразах — без ( ) |.
_KEYWORD_BAD = re.compile(r"[^\w\s!+\-\"\[\]().'|]|_")
_NEGATIVE_BAD = re.compile(r"[^\w\s!+\-\"\[\].']|_")
_NOT_COUNTED = re.compile(r'[\s\-!+"\[\]()|]')
_GROUPING = re.compile(r"\([^()]*\)")
# Значки, которые Директ в объявлениях принимает, хотя это «символы».
_SYMBOLS_OK = set("№®™©°")
_BAD_CATEGORIES = {"So", "Cs", "Co", "Cn", "Cc", "Cf"}

# --- Риски модерации --------------------------------------------------------------------------------

_SUPERLATIVE = re.compile(
    r"(?<![\w-])(?:"
    r"(?:наи)?лучш\w*|сам(?:ый|ая|ое|ые|ых|ого|ой|ым|ыми|ому|ую)(?!\w)|лидер\w*|единственн\w*|"
    r"непревзойденн\w*|№\s*1(?!\d)|номер\s+(?:один|1)(?!\d)|топ[-\s]?1(?!\d)|\w{2,}(?:ейш|айш)\w*"
    r")"
)
# Слова на -ейш-/-айш-, которые превосходной степенью в рекламе не считаются.
_NOT_SUPERLATIVE = ("ближайш", "малейш", "дальнейш", "кратчайш", "позднейш", "мельчайш")
# Сокращения пишут заглавными — это не «капс».
CAPS_OK = frozenset(
    """
    РФ СПБ МСК МО ЛО НСК ЕКБ ЮАО САО ЗАО ВАО ЦАО СЗАО ЮЗАО ЮВАО СВАО ЗелАО
    МРТ КТ УЗИ ЭКГ ЛФК ОРВИ ИП ООО ОАО ПАО АО НКО ТСЖ ЖК ЖКХ СНТ ИЖС ДНП ПВХ МДФ ДСП ЛДСП ОСБ ГКЛ
    ОСАГО КАСКО ГИБДД ДТП ПДД СТО ТО КПП АКПП МКПП ДВС ГБО ГСМ АЗС СНИЛС ИНН ОГРН ЕГРЮЛ ЕГРН НДС НДФЛ УСН
    ЕГЭ ОГЭ ВУЗ ВУЗЫ ВУЗОВ СПО ДПО МГУ МФЦ ГОСТ СНИП ТУ СМИ ПК ТВ ИИ ЧПУ ЛЭП ЦОД АТС СКУД
    IT SEO SMM CRM ERP API AI VIP LED USB GPS SSD HDD CPU GPU TV PC SUV DIY HR PR CEO B2B B2C
    BMW VW KIA ВАЗ ГАЗ УАЗ КАМАЗ МАЗ ЗИЛ ЛАДА LADA SPA
    """.upper().split()
)
_CAPS_LONG = 5
_PHONE = re.compile(r"(?<![\d+])(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
_PHONE_LOCAL = re.compile(r"(?<![\d.,\-])\d{2,3}-\d{2}-\d{2}(?![\d\-])")
# Городской номер похож на размер и артикул: «Шкаф 120-45-60 см», «Артикул 100-20-30» — не телефон.
_SIZE_AFTER = re.compile(r"\s*(?:см|мм|дм|кг|мл|л|шт|руб|₽|%|°)(?![^\W\d_])", re.I)
_SIZE_BEFORE = re.compile(
    r"(?:(?<![^\W\d_])(?:арт|размер|габарит|модел|код)[^\W\d_]*|№|\d\s*[xх×*])[.:]?\s*$", re.I
)

NO_HREF = "нет адреса страницы"
BAD_LABEL = "Директ не примет"
RISK_LABEL = "модерация"
OVERLAP_LABEL = "пересечение"
MAX_REASONS = 2
OVERLAP_NOTES = 4


def _q(text: str) -> str:
    return f"«{text}»"


def _brackets(text: str, left: str, right: str) -> bool:
    """Скобки парные и не вложенные."""
    depth = 0
    for char in text:
        if char == left:
            depth += 1
            if depth > 1:
                return False
        elif char == right:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def keyword_problems(text: str) -> list[str]:
    """Что во фразе Директ не примет: число и длина слов, знаки, непарные кавычки и скобки."""
    text = str(text or "").strip()
    problems: list[str] = []
    if len(text) > KEYWORD_LEN:
        problems.append(f"длиннее {KEYWORD_LEN} знаков")
    words = text.split()
    # Группа (купить|заказать) — одно слово на месте каждого варианта.
    plus = [w for w in _GROUPING.sub(" (x) ", text).split() if not w.startswith("-")]
    if not plus:
        problems.append("нет слов")
    elif len(plus) > MAX_WORDS:
        problems.append(f"больше {MAX_WORDS} слов")
    long = next((w for w in words if len(_NOT_COUNTED.sub("", w)) > MAX_WORD_CHARS), None)
    if long:
        problems.append(f"слово длиннее {MAX_WORD_CHARS} знаков")
    bad = _KEYWORD_BAD.search(text)
    if bad:
        problems.append(f"знак {_q(bad.group(0))} во фразах нельзя")
    if text.count('"') % 2:
        problems.append("непарные кавычки")
    if not _brackets(text, "[", "]"):
        problems.append("непарные квадратные скобки")
    if not _brackets(text, "(", ")"):
        problems.append("непарные круглые скобки")
    return problems


def negative_problems(text: str) -> list[str]:
    """Что в минус-фразе Директ не примет."""
    words = str(text or "").strip().split()
    if words:
        words[0] = words[0].lstrip("-")
    words = [w for w in words if w]
    if not words:
        return ["нет слов"]
    problems: list[str] = []
    phrase = " ".join(words)
    if len(words) > MAX_WORDS:
        problems.append(f"больше {MAX_WORDS} слов")
    if any(len(_NOT_COUNTED.sub("", w)) > MAX_WORD_CHARS for w in words):
        problems.append(f"слово длиннее {MAX_WORD_CHARS} знаков")
    if any(w.startswith("-") for w in words[1:]):
        problems.append("минус внутри фразы — разбейте на отдельные строки")
    bad = _NEGATIVE_BAD.search(phrase)
    if bad:
        problems.append(f"знак {_q(bad.group(0))} в минус-фразах нельзя")
    if phrase.count('"') % 2:
        problems.append("непарные кавычки")
    if not _brackets(phrase, "[", "]"):
        problems.append("непарные квадратные скобки")
    return problems


def _bad_char(text: str) -> str:
    for char in text:
        if char in _SYMBOLS_OK:
            continue
        if unicodedata.category(char) in _BAD_CATEGORIES:
            return char
    return ""


def text_problems(text: str, kind: str) -> list[str]:
    """Что в заголовке, тексте, быстрой ссылке («sitelink», «description») или уточнении Директ не примет."""
    text = str(text or "").strip()
    problems: list[str] = []
    limit = TEXT_LIMITS.get(kind)
    if limit and len(text) > limit:
        problems.append(f"длиннее {limit} знаков ({len(text)})")
    word_limit = WORD_LEN.get(kind)
    if word_limit:
        long = next((w for w in re.findall(r"\w+", text) if len(w) > word_limit), None)
        if long:
            problems.append(f"слово {_q(long)} длиннее {word_limit} знаков")
    bad = _bad_char(text)
    if bad:
        shown = "перенос строки" if bad in "\n\r\t" else _q(bad)
        problems.append(f"значок {shown} — эмодзи и спецзнаки Директ не принимает")
    return problems


def _superlative(text: str) -> str:
    low = text.lower().replace("ё", "е")
    for match in _SUPERLATIVE.finditer(low):
        word = match.group(0)
        if not word.startswith(_NOT_SUPERLATIVE):
            return text[match.start(): match.end()]
    return ""


def caps_words(text: str) -> list[str]:
    """Слова заглавными, которые модерация сочтёт «капсом»; сокращения вроде СПб, МРТ, ИП — не в счёт."""
    caps = [w for w in re.findall(r"[^\W\d_]+", str(text or "")) if len(w) >= 2 and w.isupper()]
    caps = [w for w in caps if w.upper().replace("Ё", "Е") not in CAPS_OK and w.upper() not in CAPS_OK]
    # Латиницей заглавными пишут бренды и экзамены (ASUS, HP, IELTS): капс — от двух длинных слов.
    latin = [w for w in caps if w.isascii()]
    if len([w for w in latin if len(w) >= _CAPS_LONG]) < 2:
        caps = [w for w in caps if not w.isascii()]
    if any(len(w) >= _CAPS_LONG for w in caps) or len(caps) >= 2:
        return caps
    return []


def moderation_risks(text: str) -> list[str]:
    """Что может не пройти модерацию: превосходная степень, капс, несколько «!», телефон."""
    text = str(text or "")
    risks: list[str] = []
    word = _superlative(text)
    if word:
        risks.append(f"{_q(word.strip())} — модерация попросит доказательства")
    caps = caps_words(text)
    if caps:
        risks.append(f"{_q(caps[0])} заглавными — так можно только сокращения и бренды, проверьте")
    if text.count("!") > 1:
        risks.append("больше одного «!»")
    if _has_phone(text):
        risks.append("телефон в тексте — его указывают в визитке")
    return risks


def _has_phone(text: str) -> bool:
    if _PHONE.search(text):
        return True
    return any(
        not _SIZE_AFTER.match(text, m.end()) and not _SIZE_BEFORE.search(text[: m.start()])
        for m in _PHONE_LOCAL.finditer(text)
    )


# --- Доска ----------------------------------------------------------------------------------------


def _on(item: dict) -> bool:
    return item.get("checked", True) is not False


class _Marks:
    """Причины по строкам доски: bad — Директ не примет, risk — модерация, overlap — пересечения."""

    def __init__(self):
        self.items: list[dict] = []
        self.reasons: dict[int, dict[str, list[str]]] = {}

    def add(self, item: dict, category: str, reason: str) -> None:
        key = id(item)
        if key not in self.reasons:
            self.items.append(item)
            self.reasons[key] = {"bad": [], "risk": [], "overlap": []}
        if reason not in self.reasons[key][category]:
            self.reasons[key][category].append(reason)

    def has(self, category: str) -> list[dict]:
        return [i for i in self.items if self.reasons[id(i)][category]]

    def first(self, item: dict, category: str) -> str:
        return self.reasons[id(item)][category][0]

    def apply(self) -> None:
        for item in self.items:
            found = self.reasons[id(item)]
            top = "bad" if found["bad"] else "risk" if found["risk"] else "overlap"
            reasons = found["bad"] + found["risk"] + found["overlap"]
            note = "; ".join(reasons[:MAX_REASONS])
            if len(reasons) > MAX_REASONS:
                note += f" и ещё {len(reasons) - MAX_REASONS}"
            item["rule"] = {
                "state": "bad" if top == "bad" else "warn",
                "label": {"bad": BAD_LABEL, "risk": RISK_LABEL, "overlap": OVERLAP_LABEL}[top],
                "note": note,
            }


def _ad_lines(build: dict):
    """(строка, вид) — всё, что уйдёт в объявления: заголовки, тексты, быстрые ссылки, уточнения."""
    for g in build.get("groups") or []:
        for kind, key in (("title", "titles"), ("text", "texts"), ("sitelink", "sitelinks"), ("callout", "callouts")):
            for item in g.get(key) or []:
                yield item, kind
    for kind, key in (("sitelink", "sitelinks"), ("callout", "callouts")):
        for item in build.get(key) or []:
            yield item, kind


def _check_lines(build: dict, marks: _Marks) -> None:
    for g in build.get("groups") or []:
        for item in g.get("keywords") or []:
            if _on(item):
                for problem in keyword_problems(item.get("text")):
                    marks.add(item, "bad", problem)
    for item in build.get("negatives") or []:
        if _on(item):
            for problem in negative_problems(item.get("text")):
                marks.add(item, "bad", problem)
    for item, kind in _ad_lines(build):
        if not _on(item):
            continue
        for problem in text_problems(item.get("text"), kind):
            marks.add(item, "bad", problem)
        if kind == "sitelink" and not str(item.get("href") or "").strip():
            marks.add(item, "bad", NO_HREF)
        description = str(item.get("description") or "") if kind == "sitelink" else ""
        for problem in text_problems(description, "description") if description else []:
            marks.add(item, "bad", f"описание: {problem}")
        for risk in moderation_risks(f"{item.get('text') or ''} {description}".strip()):
            marks.add(item, "risk", risk)


def _keyword_entries(build: dict) -> list[dict]:
    out = []
    for g in build.get("groups") or []:
        for item in g.get("keywords") or []:
            raw = str(item.get("text") or "").strip()
            if not _on(item) or not raw:
                continue
            base = query_cleaning.keyword_text(raw)
            stems = frozenset(query_cleaning.stems(base))
            if not stems:
                continue
            minus = [s for w in re.findall(r"(?:^|\s)-(\S+)", raw) for s in query_cleaning.stems(w)]
            out.append({
                "id": len(out),
                "item": item,
                "keyword": raw,
                "adgroup_name": g.get("name") or "",
                "group": g.get("name") or "",
                "set": stems,
                "stems": list(stems),
                "minus": minus,
                # «Фраза в кавычках» ловит запросы ровно из своих слов — шире не перехватывает.
                "quoted": raw.startswith('"'),
            })
    return out


def _check_overlaps(build: dict, marks: _Marks) -> dict:
    """Одна фраза в двух группах, фраза-«родитель» без минус-слов, минус-фраза против фразы доски."""
    entries = _keyword_entries(build)
    by_set: dict[frozenset, list[dict]] = defaultdict(list)
    for e in entries:
        by_set[e["set"]].append(e)
    twins: list[str] = []
    nested: list[str] = []
    for same in by_set.values():
        if len(same) < 2:
            continue
        groups = []
        for e in same:
            if e["group"] not in groups:
                groups.append(e["group"])
        for e in same:
            others = [g for g in groups if g != e["group"]]
            if others:
                marks.add(e["item"], "overlap", f"такая же фраза есть в группе {_q(others[0])} — группы спорят за запросы")
            elif e is not same[0] and same[0]["group"] == e["group"]:
                marks.add(e["item"], "overlap", f"повторяет фразу {_q(same[0]['keyword'])} в этой же группе")
        if len(groups) > 1:
            twins.append(f"фраза {_q(same[0]['keyword'])} — в группах " + " и ".join(_q(g) for g in groups[:3]))
    # Фраза-«родитель» (narrow) — та, чьи основы целиком входят в другую фразу (wide). Фразы в кавычках
    # не в счёт: запрос ровно из своих слов Директ отдаст им самим. Более широкие ищем по самой редкой
    # основе narrow, а не перебором подмножеств основ wide: у фразы со словами через точку основ бывают
    # десятки, и перебор (2^n) вешал доску.
    by_stem: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        if not e["quoted"]:
            for stem in e["set"]:
                by_stem[stem].append(e)
    pairs: list[tuple[dict, dict]] = []
    for narrow in entries:
        if narrow["quoted"]:
            continue
        rare = min((by_stem[s] for s in narrow["set"]), key=len)
        pairs += [(w, narrow) for w in rare if narrow["set"] < w["set"] and narrow["group"] != w["group"]]
    pairs.sort(key=lambda p: (p[0]["id"], len(p[1]["set"]), sorted(p[1]["set"]), p[1]["id"]))
    for wide_e, narrow in pairs:
        extra = list(wide_e["set"] - narrow["set"])
        if any(query_cleaning.blocks_stems([m], extra) for m in narrow["minus"]):
            continue
        marks.add(
            narrow["item"], "overlap",
            f"перехватывает запросы фразы {_q(wide_e['keyword'])} из группы {_q(wide_e['group'])}",
        )
        nested.append(
            f"{_q(narrow['keyword'])} ({_q(narrow['group'])}) перехватывает "
            f"{_q(wide_e['keyword'])} ({_q(wide_e['group'])})"
        )
    cut: list[str] = []
    buckets: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        for prefix in {s[:4] for s in e["set"]}:
            buckets[prefix].append(e)
    for neg in build.get("negatives") or []:
        if not _on(neg):
            continue
        phrase = query_cleaning.clean_negative(neg.get("text"))
        neg_stems = query_cleaning.stems(phrase)
        if not neg_stems:
            continue
        hits = query_cleaning.find_conflicts(
            phrase, "campaign", {"campaign_row_ids": [0], "adgroup_row_ids": []},
            ({}, {0: buckets.get(neg_stems[0][:4], [])}),
        )
        if not hits:
            continue
        more = f" и ещё {len(hits) - 1}" if len(hits) > 1 else ""
        marks.add(neg, "overlap", f"отключит фразу {_q(hits[0]['keyword'])}{more}")
        for hit in hits:
            marks.add(hit["item"], "overlap", f"минус-фраза {_q(neg.get('text') or phrase)} отключит эту фразу")
        cut.append(f"минус-фраза {_q(neg.get('text') or phrase)} отключит {_q(hits[0]['keyword'])}{more}")
    return {"twins": twins, "nested": nested, "cut": cut, "keywords": bool(entries)}


def _preview(items: list[str], limit: int) -> str:
    text = "; ".join(items[:limit])
    return text + (f" и ещё {len(items) - limit}" if len(items) > limit else "")


def _line_note(marks: _Marks, items: list[dict], category: str) -> str:
    return _preview([f"{_q(i.get('text') or '')} — {marks.first(i, category)}" for i in items], 2)


def separate_request(twins: list[str], nested: list[str]) -> str:
    """Просьба ИИ развести группы и показать правки на доске.

    Одинаковые фразы минус-словами не развести — такую фразу оставляют в одной группе; вложенные разводят минус-словами.
    """
    found = _preview(twins + nested, 6)
    steps = []
    if twins:
        steps.append("Одинаковую фразу оставь только в одной группе — в какой, реши по смыслу.")
    if nested:
        steps.append("Между группами с вложенными фразами подбери минус-слова, чтобы каждый запрос вёл в свою группу.")
    return (
        f"Разведи группы на доске — они спорят за одни запросы: {found}. {' '.join(steps)} "
        "Покажи изменения на доске на согласование — сам ничего не меняй."
    )


def board_checks(build: dict) -> list[dict]:
    """Проверки доски по правилам Директа; у строки с ошибкой или риском — пометка rule {state, label, note}."""
    for g in build.get("groups") or []:
        for key in ("keywords", "titles", "texts", "sitelinks", "callouts"):
            for item in g.get(key) or []:
                item.pop("rule", None)
    for key in ("negatives", "sitelinks", "callouts"):
        for item in build.get(key) or []:
            item.pop("rule", None)
    marks = _Marks()
    _check_lines(build, marks)
    overlaps = _check_overlaps(build, marks)
    marks.apply()

    checks: list[dict] = []
    bad, risky = marks.has("bad"), marks.has("risk")
    if bad:
        checks.append({"key": "rules", "label": "Правила Директа", "state": "bad",
                       "note": "Директ не примет: " + _line_note(marks, bad, "bad")})
    elif risky:
        checks.append({"key": "rules", "label": "Правила Директа", "state": "warn",
                       "note": "может не пройти модерацию: " + _line_note(marks, risky, "risk")})
    elif marks.items or overlaps["keywords"] or any(_on(i) for i, _ in _ad_lines(build)):
        checks.append({"key": "rules", "label": "Правила Директа", "state": "ok", "note": "фразы и тексты в порядке"})

    found = overlaps["twins"] + overlaps["nested"] + overlaps["cut"]
    dupes = [i for i in marks.has("overlap") if "в этой же группе" in marks.first(i, "overlap")]
    if found or dupes:
        notes = found + [f"{_q(i.get('text') or '')} — повтор в группе" for i in dupes]
        check = {"key": "overlap", "label": "Пересечения фраз", "state": "warn", "note": _preview(notes, OVERLAP_NOTES)}
        if overlaps["twins"] or overlaps["nested"]:
            check["ask"] = separate_request(overlaps["twins"], overlaps["nested"])
        checks.append(check)
    elif overlaps["keywords"] and (
        len(build.get("groups") or []) > 1 or any(_on(n) for n in build.get("negatives") or [])
    ):
        checks.append({"key": "overlap", "label": "Пересечения фраз", "state": "ok", "note": "группы не спорят за запросы"})
    return checks
