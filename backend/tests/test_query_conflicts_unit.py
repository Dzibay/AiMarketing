"""I42: выходы из пересечения минус-фразы с ключом — чистые функции без базы."""

from app import query_cleaning as qc


def test_quoted_keyword_keeps_minus_words():
    assert qc.quoted_keyword("кухни на заказ") == '"кухни на заказ"'
    assert qc.quoted_keyword("кухни недорого -бесплатно -даром") == '"кухни недорого" -бесплатно -даром'
    assert qc.quoted_keyword("+в !москве окна") == '"+в !москве окна"'


def test_quoted_keyword_skips_already_fixed():
    assert qc.quoted_keyword('"кухни на заказ"') is None
    assert qc.quoted_keyword("[москва питер] билеты") is None
    assert qc.quoted_keyword("---autotargeting") is None
    assert qc.quoted_keyword("") is None


def test_has_extra_word():
    kw = qc.stems("кухни на заказ")
    assert qc.has_extra_word(kw, qc.stems("кухни на заказ москва"))
    assert not qc.has_extra_word(kw, qc.stems("кухню на заказ"))
    assert not qc.has_extra_word(kw, qc.stems("заказ кухни"))


def test_narrow_candidates_take_new_words_from_query():
    assert qc.narrow_candidates("бесплатно", "Кухни бесплатно в Москве") == ["бесплатно кухни", "бесплатно москве"]
    # Слова, которые уже есть во фразе (в любой форме), и стоп-слова не предлагаем.
    assert qc.narrow_candidates("кухня бесплатная", "кухни бесплатно") == []
    assert qc.narrow_candidates("ремонт", "ремонт ремонта ёлки") == ["ремонт елки"]


def test_narrowed_phrase_still_blocks_query_but_not_keyword():
    query = "кухни бесплатно своими руками"
    keyword = qc.stems("кухни с бесплатной доставкой")
    assert qc.blocks_stems(qc.stems("бесплатно"), keyword)
    for candidate in qc.narrow_candidates("бесплатно", query):
        assert qc.blocks(candidate, query)
    ok = [c for c in qc.narrow_candidates("бесплатно", query) if not qc.blocks_stems(qc.stems(c), keyword)]
    assert ok == ["бесплатно своими", "бесплатно руками"]
