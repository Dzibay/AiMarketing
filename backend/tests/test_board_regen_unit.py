"""↻ на доске: варианты быстрых ссылок и уточнений проверяются теми же правилами, что правка доски."""

from app import ai_agent
from tests.test_board_extras_unit import _build


def _problem(build, kind, variant, line=None):
    bucket, item, _group = ai_agent._regen_target(build, kind, "", line or "")
    return ai_agent._regen_problem(build, kind, "", item, bucket, variant)


def test_sitelink_line_variants():
    build = _build()
    assert _problem(build, "sitelink", "Прайс-лист", line="Цены") == ""
    assert "уже есть" in _problem(build, "sitelink", "Доставка", line="Цены")
    assert _problem(build, "sitelink", "Очень длинное название раздела сайта", line="Цены") == "длинный"


def test_callout_list_variants_and_banned_words():
    build = _build()
    assert _problem(build, "callout", "Рассрочка 0%") == ""
    assert _problem(build, "callout", "Гарантия 5 лет") == "уже есть"
    assert _problem(build, "callout", "Бесплатный замер на дому сегодня") == "длинный"
    build["requirements"] = {"banned_words": ["рассроч"]}
    assert _problem(build, "callout", "Рассрочка 0%") == "запрещённое слово"
