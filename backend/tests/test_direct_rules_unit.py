"""Правила Директа на доске сборки: технические пределы, риски модерации, пересечения фраз."""

import time

import pytest

from app import direct_rules as rules


# --- Фразы и минус-фразы ------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "купить окна",
    "(купить|заказать) окна [пвх москва] -дешево",
    '"окна пвх"',
    "!окна +в +москве",
    "окна 1.5 метра",
    "окна д'артаньян",
    "a b c d e f g -h -i",  # минус-слова в 7 слов не считаются
])
def test_keyword_ok(text):
    assert rules.keyword_problems(text) == []


@pytest.mark.parametrize("text, problem", [
    ("купить окна, дешево", "знак «,» во фразах нельзя"),
    ("окна @ москва", "знак «@» во фразах нельзя"),
    ("окна_пвх", "знак «_» во фразах нельзя"),
    ("a b c d e f g h", "больше 7 слов"),
    ("окна " + "о" * 36, "слово длиннее 35 знаков"),
    ('"окна пвх', "непарные кавычки"),
    ("[окна пвх", "непарные квадратные скобки"),
    ("[окна [пвх]]", "непарные квадратные скобки"),
    ("(купить|заказать окна", "непарные круглые скобки"),
    ("-дешево", "нет слов"),
])
def test_keyword_problems(text, problem):
    assert problem in rules.keyword_problems(text)


def test_keyword_word_length_ignores_operators():
    # Операторы ! + " [ ] к длине слова не прибавляются.
    assert rules.keyword_problems("!" + "о" * 35) == []
    assert rules.keyword_problems('"[' + "о" * 35 + ']"') == []


@pytest.mark.parametrize("text", ["бесплатно", "-бесплатно", "!своими руками", '"окна"', "[из москвы]"])
def test_negative_ok(text):
    assert rules.negative_problems(text) == []


@pytest.mark.parametrize("text, problem", [
    ("-бесплатно -даром", "минус внутри фразы — разбейте на отдельные строки"),
    ("своими (руками)", "знак «(» в минус-фразах нельзя"),
    ("a b c d e f g h", "больше 7 слов"),
    ("о" * 36, "слово длиннее 35 знаков"),
    ('"бесплатно', "непарные кавычки"),
    ("-", "нет слов"),
])
def test_negative_problems(text, problem):
    assert problem in rules.negative_problems(text)


# --- Заголовки, тексты, быстрые ссылки, уточнения ----------------------------------------------


def test_text_limits():
    title = "Окна от завода " * 4  # 60 знаков
    assert rules.text_problems(title[:56], "title") == []
    assert rules.text_problems(title[:57], "title") == ["длиннее 56 знаков (57)"]
    assert rules.text_problems(("Замер бесплатно " * 6)[:82], "text") == ["длиннее 81 знаков (82)"]
    assert rules.text_problems("С" * 31, "sitelink") == ["длиннее 30 знаков (31)"]
    assert rules.text_problems("О" * 61, "description") == ["длиннее 60 знаков (61)"]
    assert rules.text_problems("У" * 26, "callout") == ["длиннее 25 знаков (26)"]
    assert rules.text_problems("У" * 25, "callout") == []


def test_text_long_word():
    assert rules.text_problems("Окна " + "о" * 23, "title") == [f"слово «{'о' * 23}» длиннее 22 знаков"]
    assert rules.text_problems("Окна " + "о" * 23, "text") == []
    assert rules.text_problems("Окна " + "о" * 24, "text") == [f"слово «{'о' * 24}» длиннее 23 знаков"]
    # В быстрых ссылках длину слова Директ не ограничивает отдельно.
    assert rules.text_problems("о" * 25, "sitelink") == []


def test_text_forbidden_chars():
    assert rules.text_problems("Окна 🔥", "title") == ["значок «🔥» — эмодзи и спецзнаки Директ не принимает"]
    assert rules.text_problems("Окна ★ от завода", "text")[0].startswith("значок «★»")
    assert "перенос строки" in rules.text_problems("Окна\tот завода", "text")[0]
    # Обычная пунктуация, валюта и разрешённые значки — можно.
    assert rules.text_problems("Окна №1 — от 5 000 ₽, скидка 10%!", "text") == []
    assert rules.text_problems("Окна REHAU® за 1 день", "title") == []


# --- Риски модерации ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, word", [
    ("Лучшие окна в Москве", "Лучшие"),
    ("Самые тёплые окна", "Самые"),
    ("Крупнейший завод окон", "Крупнейший"),
    ("Лидер рынка окон", "Лидер"),
    ("Единственный завод в городе", "Единственный"),
    ("Окна №1 в Москве", "№1"),
    ("Наилучшее качество", "Наилучшее"),
])
def test_superlatives(text, word):
    assert f"«{word}» — модерация попросит доказательства" in rules.moderation_risks(text)


@pytest.mark.parametrize("text", [
    "Ближайший салон рядом",
    "Без малейших сколов",
    "Кратчайшие сроки монтажа",
    "Дальнейшее обслуживание",
    "Окна для дома",
])
def test_not_superlatives(text):
    assert rules.moderation_risks(text) == []


def test_caps():
    assert rules.moderation_risks("Окна от ЗАВОДА") == ["«ЗАВОДА» заглавными — так можно только сокращения и бренды, проверьте"]
    assert rules.caps_words("КУПИ ОКНА") == ["КУПИ", "ОКНА"]
    # Сокращения — не капс, даже несколько сразу.
    assert rules.caps_words("МРТ и УЗИ в ЖК, СПб, ИП и ООО") == []
    assert rules.caps_words("Окна ПВХ в МСК") == []
    # Одно короткое слово заглавными — скорее аббревиатура.
    assert rules.caps_words("Окна КБЕ") == []
    # Латиницей заглавными пишут бренды и экзамены — капс только от двух длинных слов.
    assert rules.caps_words("Подготовка к IELTS за 2 месяца") == []
    assert rules.caps_words("Ноутбуки ASUS и HP со скидкой") == []
    assert rules.caps_words("SUPER PRICE WINDOWS") == ["SUPER", "PRICE", "WINDOWS"]


def test_exclamations_and_phones():
    assert rules.moderation_risks("Звоните! Скидка!") == ["больше одного «!»"]
    assert rules.moderation_risks("Звоните сейчас!") == []
    phone = "телефон в тексте — его указывают в визитке"
    assert rules.moderation_risks("Звоните +7 (495) 123-45-67") == [phone]
    assert rules.moderation_risks("Звоните 8 800 555 35 35") == [phone]
    assert rules.moderation_risks("Звоните 123-45-67") == [phone]
    # Цены, размеры и годы — не телефоны.
    assert rules.moderation_risks("Окна 1200x1400 от 5 000 ₽ с 2005 года") == []
    # Размеры и артикулы через дефис похожи на городской номер — тоже не телефоны.
    assert rules.moderation_risks("Шкаф 120-45-60 см в наличии") == []
    assert rules.moderation_risks("Артикул 100-20-30, доставка") == []
    assert rules.moderation_risks("Размер: 200-90-45 мм") == []
    assert rules.moderation_risks("Тел. 123-45-67, звоните") == [phone]


# --- Доска ---------------------------------------------------------------------------------------


def _i(text, **extra):
    return {"text": text, "checked": True, **extra}


def _build(groups, negatives=(), sitelinks=(), callouts=()):
    return {
        "groups": [
            {"name": name, "keywords": [_i(k) if isinstance(k, str) else k for k in kws],
             "titles": [_i(t) for t in titles], "texts": [_i(t) for t in texts]}
            for name, kws, titles, texts in groups
        ],
        "negatives": [_i(n) if isinstance(n, str) else n for n in negatives],
        "sitelinks": list(sitelinks),
        "callouts": list(callouts),
    }


def _checks(build):
    return {c["key"]: c for c in rules.board_checks(build)}


def test_board_ok():
    build = _build([
        ("Окна", ["купить окна пвх"], ["Окна от завода"], ["Замер бесплатно"]),
        ("Балконы", ["остекление балкона"], ["Балконы под ключ"], ["Монтаж за день"]),
    ], negatives=["бесплатно"])
    checks = _checks(build)
    assert checks["rules"] == {"key": "rules", "label": "Правила Директа", "state": "ok", "note": "фразы и тексты в порядке"}
    assert checks["overlap"]["state"] == "ok" and "ask" not in checks["overlap"]
    assert not any("rule" in k for g in build["groups"] for k in g["keywords"])


def test_board_empty_has_no_checks():
    assert rules.board_checks(_build([])) == []


def test_board_technical_limits_block_and_mark_rows():
    build = _build(
        [("Окна", ["купить окна, дешево"], ["Окна 🔥 от завода"], ["Замер бесплатно"])],
        negatives=["-бесплатно -даром"],
        sitelinks=[_i("Доставка", href="https://okna.ru/d", description="д" * 61)],
        callouts=[_i("Гарантия на монтаж пять лет")],
    )
    checks = _checks(build)
    assert checks["rules"]["state"] == "bad"
    assert checks["rules"]["note"].startswith("Директ не примет: «купить окна, дешево» — знак «,» во фразах нельзя;")
    keyword = build["groups"][0]["keywords"][0]
    assert keyword["rule"] == {"state": "bad", "label": "Директ не примет", "note": "знак «,» во фразах нельзя"}
    assert build["groups"][0]["titles"][0]["rule"]["state"] == "bad"
    assert build["groups"][0]["texts"][0].get("rule") is None
    assert "минус внутри фразы" in build["negatives"][0]["rule"]["note"]
    assert build["sitelinks"][0]["rule"]["note"] == "описание: длиннее 60 знаков (61)"
    assert build["callouts"][0]["rule"]["note"] == "длиннее 25 знаков (27)"


def test_board_unchecked_rows_are_skipped_and_old_marks_cleared():
    build = _build([("Окна", [_i("купить, окна", checked=False), "окна пвх"], [], [])])
    build["groups"][0]["keywords"][1]["rule"] = {"state": "bad", "label": "старое", "note": "старое"}
    checks = _checks(build)
    assert checks["rules"]["state"] == "ok"
    assert all("rule" not in k for k in build["groups"][0]["keywords"])


def test_board_moderation_risks_warn():
    build = _build([("Окна", ["купить окна"], ["Лучшие окна!!"], ["Звоните +7 495 123-45-67"])])
    checks = _checks(build)
    assert checks["rules"]["state"] == "warn"
    assert checks["rules"]["note"].startswith("может не пройти модерацию: «Лучшие окна!!» — «Лучшие»")
    title = build["groups"][0]["titles"][0]
    assert title["rule"]["state"] == "warn" and title["rule"]["label"] == "модерация"
    assert title["rule"]["note"] == "«Лучшие» — модерация попросит доказательства; больше одного «!»"
    # Технический предел важнее риска: строка — «Директ не примет».
    build = _build([("Окна", ["купить окна"], ["Лучшие окна " + "о" * 50], [])])
    rule = build["groups"][0]["titles"][0]
    _checks(build)
    assert rule["rule"]["state"] == "bad" and rule["rule"]["label"] == "Директ не примет"
    assert rule["rule"]["note"].endswith("и ещё 1")


def test_board_same_phrase_in_two_groups():
    build = _build([
        ("Окна", ["купить окна", "окна пвх"], [], []),
        ("Пластиковые", ["Купить окно", "пластиковые окна"], [], []),
    ])
    checks = _checks(build)
    overlap = checks["overlap"]
    assert overlap["state"] == "warn"
    assert overlap["note"].startswith("фраза «купить окна» — в группах «Окна» и «Пластиковые»")
    assert "Разведи группы на доске" in overlap["ask"] and "сам ничего не меняй" in overlap["ask"]
    assert build["groups"][0]["keywords"][0]["rule"] == {
        "state": "warn", "label": "пересечение",
        "note": "такая же фраза есть в группе «Пластиковые» — группы спорят за запросы",
    }
    assert "«Окна»" in build["groups"][1]["keywords"][0]["rule"]["note"]
    # Пересечения — не правила Директа: проверка правил остаётся в порядке.
    assert checks["rules"]["state"] == "ok"


def test_board_duplicate_in_one_group_has_no_ask():
    build = _build([("Окна", ["купить окна", "окна купить"], [], [])])
    overlap = _checks(build)["overlap"]
    assert overlap["state"] == "warn" and "ask" not in overlap
    assert overlap["note"] == "«окна купить» — повтор в группе"
    assert build["groups"][0]["keywords"][1]["rule"]["note"] == "повторяет фразу «купить окна» в этой же группе"


def test_board_nested_phrase_across_groups():
    build = _build([
        ("Окна", ["окна"], [], []),
        ("Пластиковые", ["пластиковые окна"], [], []),
    ])
    overlap = _checks(build)["overlap"]
    assert overlap["note"] == "«окна» («Окна») перехватывает «пластиковые окна» («Пластиковые»)"
    assert "ask" in overlap
    wide, narrow = build["groups"][0]["keywords"][0], build["groups"][1]["keywords"][0]
    assert wide["rule"]["note"] == "перехватывает запросы фразы «пластиковые окна» из группы «Пластиковые»"
    assert "rule" not in narrow


def test_board_nested_resolved_by_minus_or_quotes():
    # Минус-слово широкой фразы разводит группы.
    build = _build([("Окна", ["окна -пластиковые"], [], []), ("Пластиковые", ["пластиковые окна"], [], [])])
    assert _checks(build)["overlap"]["state"] == "ok"
    # «Фраза в кавычках» шире своих слов не ловит — и её запросы Директ отдаст ей самой.
    build = _build([("Окна", ['"окна"'], [], []), ("Пластиковые", ["пластиковые окна"], [], [])])
    assert _checks(build)["overlap"]["state"] == "ok"
    build = _build([("Окна", ["окна"], [], []), ("Пластиковые", ['"пластиковые окна"'], [], [])])
    assert _checks(build)["overlap"]["state"] == "ok"
    # В одной группе вложенность не спор.
    build = _build([("Окна", ["окна", "пластиковые окна"], [], [])], negatives=["даром"])
    assert _checks(build)["overlap"]["state"] == "ok"


def test_board_negative_cuts_keyword():
    build = _build([("Окна", ["купить окна", "окна пвх дешево"], [], [])], negatives=["дешевые", "бесплатно"])
    overlap = _checks(build)["overlap"]
    assert overlap["state"] == "warn" and "ask" not in overlap
    assert overlap["note"] == "минус-фраза «дешевые» отключит «окна пвх дешево»"
    negative, other = build["negatives"]
    assert negative["rule"] == {"state": "warn", "label": "пересечение", "note": "отключит фразу «окна пвх дешево»"}
    assert "rule" not in other
    assert build["groups"][0]["keywords"][1]["rule"]["note"] == "минус-фраза «дешевые» отключит эту фразу"
    assert "rule" not in build["groups"][0]["keywords"][0]


def test_board_negative_counts_all_hits():
    build = _build([
        ("Окна", ["окна пвх"], [], []), ("Двери", ["двери пвх"], [], []),
    ], negatives=["пвх"])
    _checks(build)
    assert build["negatives"][0]["rule"]["note"] == "отключит фразу «окна пвх» и ещё 1"


def test_separate_request_lists_found():
    text = rules.separate_request(["фраза «a» — в группах «A» и «B»"], [])
    assert text.startswith("Разведи группы на доске — они спорят за одни запросы: фраза «a» — в группах «A» и «B».")
    many = rules.separate_request([f"f{i}" for i in range(8)], [])
    assert "и ещё 2" in many


def test_separate_request_twins_and_nested():
    # Одинаковые фразы минус-словами не развести — их оставляют в одной группе.
    twins = rules.separate_request(["фраза «a» — в группах «A» и «B»"], [])
    assert "оставь только в одной группе" in twins and "минус-слова" not in twins
    nested = rules.separate_request([], ["«a» («A») перехватывает «a b» («B»)"])
    assert "подбери минус-слова" in nested and "одной группе" not in nested
    both = rules.separate_request(["t"], ["n"])
    assert "одной группе" in both and "минус-слова" in both and both.endswith("сам ничего не меняй.")


def test_board_overlaps_fast_for_many_stems():
    # Слова через точку дают десятки основ: проверка пересечений не перебирает их подмножества.
    dotted = " ".join(".".join(f"{a}{b}" for b in "abcdefghijkl") for a in "abcdefg")
    build = _build([
        ("Окна", [dotted, "купить окна пвх"], [], []),
        ("Запчасти", ["купить запчасти a.b.c.d.e.f.g.h.i.j.k.l.m.n.o.p.q.r дешево", "aa.ab"], [], []),
    ])
    started = time.perf_counter()
    _checks(build)
    assert time.perf_counter() - started < 1
    # Вложенность находится по-прежнему.
    assert build["groups"][1]["keywords"][1]["rule"]["label"] == "пересечение"


def test_board_sitelink_without_href_is_bad():
    build = _build([("Окна", ["купить окна"], [], [])], sitelinks=[_i("Отзывы", href=""), _i("Цены", href="okna.ru/p")])
    checks = _checks(build)
    assert checks["rules"]["state"] == "bad" and "«Отзывы» — нет адреса страницы" in checks["rules"]["note"]
    assert build["sitelinks"][0]["rule"]["note"] == "нет адреса страницы" and "rule" not in build["sitelinks"][1]
    # Без галочки — не в счёт.
    build = _build([("Окна", ["купить окна"], [], [])], sitelinks=[_i("Отзывы", href="", checked=False)])
    assert _checks(build)["rules"]["state"] == "ok"
