"""Общие списки минус-фраз без БД: разбор ввода, лимиты Директа, план изменений кампании."""

from app.negative_lists import negatives_length, parse_phrases, phrase_key, phrase_problems, plan_campaign


def _keys(phrases):
    return {phrase_key(p) for p in phrases}


def test_parse_phrases_from_direct_style_text():
    text = "-бесплатно -скачать\nработа, Работа;  купить   слона\n-купить квартиру\n\n"
    assert parse_phrases(text) == ["бесплатно", "скачать", "работа", "купить слона", "купить квартиру"]
    assert parse_phrases(["Ёлка", "елка", " -торрент "]) == ["Ёлка", "торрент"]
    assert parse_phrases(None) == []


def test_phrase_problems_follow_direct_limits():
    assert phrase_problems(["купить слона недорого"]) == []
    assert "больше 7 слов" in phrase_problems(["раз два три четыре пять шесть семь восемь"])[0]
    assert "длиннее 35" in phrase_problems(["а" * 36])[0]
    assert "минус внутри" in phrase_problems(["купить -слона"])[0]


def test_negatives_length_skips_spaces_hyphens_and_operators():
    assert negatives_length(['"!купить +слона"', "интернет-магазин", "[из москвы]"]) == 11 + 15 + 8


def test_attach_adds_only_missing_phrases():
    plan = plan_campaign(["Бесплатно", "отзывы"], ["бесплатно", "скачать", "ёлка"], [], set())
    assert plan["add"] == ["скачать", "ёлка"]
    assert plan["remove"] == []
    assert plan["final"] == ["Бесплатно", "отзывы", "скачать", "ёлка"]
    # «Бесплатно» была в кампании до списка — список ей не хозяин и при отвязке не тронет.
    assert plan["owned"] == ["скачать", "ёлка"]


def test_update_removes_only_own_phrases():
    current = ["отзывы", "скачать", "работа"]
    plan = plan_campaign(current, ["скачать", "торрент"], ["скачать", "работа"], set())
    assert plan["add"] == ["торрент"]
    assert plan["remove"] == ["работа"]
    assert plan["final"] == ["отзывы", "скачать", "торрент"]
    assert plan["owned"] == ["скачать", "торрент"]


def test_phrase_of_other_list_is_handed_over_not_removed():
    plan = plan_campaign(["скачать", "работа"], None, ["скачать", "работа"], {"работа"})
    assert plan["remove"] == ["скачать"]
    assert plan["final"] == ["работа"]
    assert plan["handover"] == ["работа"]
    assert plan["owned"] == []


def test_phrase_deleted_by_hand_is_added_again():
    plan = plan_campaign(["отзывы"], ["скачать"], ["скачать"], set())
    assert plan["add"] == ["скачать"]
    assert plan["owned"] == ["скачать"]
    assert _keys(plan["final"]) == {"отзывы", "скачать"}


def test_nothing_to_do():
    plan = plan_campaign(["скачать"], ["Скачать"], ["скачать"], set())
    assert not plan["changed"]
    assert plan["final"] == ["скачать"]
