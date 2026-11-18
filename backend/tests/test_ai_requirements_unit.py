"""Требования ТЗ: разбор списка из вызова модели, подсчёт строк и поиск запрещённых слов."""

import pytest

from app import ai_agent
from app.yandex import YandexError


def test_requirements_are_cleaned():
    req = ai_agent.brief_requirements(
        {"titles": "7", "texts": 3, "sitelinks": 8, "callouts": 0, "banned_words": "Бесплатно, «лучший»; бесплатно",
         "other": ["Без восклицательных знаков", ""]}
    )
    assert req == {
        "titles": 7, "texts": 3, "sitelinks": 8, "banned_words": ["бесплатно", "лучший"],
        "other": ["Без восклицательных знаков"],
    }
    assert ai_agent.brief_requirements({}) == {} and ai_agent.brief_requirements(None) == {}
    text = ai_agent.requirements_text(req)
    assert "в каждой группе не меньше 7 заголовков" in text and "без слов «бесплатно», «лучший»" in text


@pytest.mark.parametrize(
    "raw, needle",
    [({"titles": 8}, "от 1 до 7"), ({"texts": "три"}, "число текстов"), ("7 заголовков", "requirements — объект")],
)
def test_bad_requirements(raw, needle):
    with pytest.raises(YandexError) as err:
        ai_agent.brief_requirements(raw)
    assert needle in err.value.message


def test_banned_words_match_word_starts():
    texts = ["Бесплатный замер", "Окна с гарантией", "Самый лучший выбор", "Нелучший вариант", "Ёлки"]
    hits = ai_agent._banned_hits(["бесплатн", "лучш", "елк"], texts)
    assert hits == ["«бесплатн» в «Бесплатный замер»", "«лучш» в «Самый лучший выбор»", "«елк» в «Ёлки»"]


def _group(name, **kw):
    return {"name": name, "keywords": ["окна"], "titles": ["A"], "texts": ["B"], "sitelinks": [], "callouts": [], **kw}


def test_requirement_checks_per_group_and_shared():
    req = {"titles": 2, "keywords": 1, "sitelinks": 2, "banned_words": ["скидк"]}
    groups = [
        _group("Окна", titles=["A", "B"], sitelinks=None),
        _group("Двери", texts=["Скидка 10%"], sitelinks=None),
        _group("Ретаргетинг", keywords=None, titles=["A", "C"], sitelinks=None),  # по аудитории — фраз не ждём
    ]
    checks = {c["key"]: c for c in ai_agent._requirement_checks(req, groups, {"sitelinks": ["Доставка"], "callouts": []})}
    assert checks["req_keywords"]["state"] == "ok"
    assert checks["req_titles"]["state"] == "bad" and checks["req_titles"]["note"] == "не хватает: «Двери» — 1"
    # Быстрые ссылки пока общие на доске — считаются один раз.
    assert checks["req_sitelinks"]["state"] == "bad" and checks["req_sitelinks"]["note"] == "на доске 1 из 2"
    assert checks["req_banned"]["state"] == "bad" and "«скидк» в «Скидка 10%»" in checks["req_banned"]["note"]
    assert ai_agent._requirement_checks({}, groups) == []


def test_spec_problems_text():
    view = ai_agent._spec_view(
        "Окна", ["окна"], False,
        {"titles": ["A"], "texts": ["B"], "extras": {"sitelinks": [{"title": "Цены", "description": "Лучшие цены"}]}},
        (["A"], ["B"]),
    )
    assert view["sitelinks"] == ["Цены Лучшие цены"] and view["callouts"] == []
    problems = ai_agent._spec_requirement_problems({"titles": 3, "callouts": 1, "banned_words": ["лучш"]}, [view])
    assert "в каждой группе не меньше 3 заголовков — не хватает: «Окна» — 1" in problems
    assert "у каждого объявления не меньше 1 уточнения" in problems and "«лучш» в «Цены Лучшие цены»" in problems
    # Расширения взяты у готового объявления — их не посчитать, и это не нарушение.
    copied = ai_agent._spec_view("Окна", ["окна"], False, {"extras_from_ad_row_id": 5}, (["A"], ["B"]))
    assert ai_agent._spec_requirement_problems({"callouts": 1}, [copied]) == ""
