"""Регион словами → регионы справочника Директа: фразы, кандидаты, сверка с регионами группы и пересечение."""

import pytest

from app import geo


def _r(rid, name, kind, parent):
    return {"id": rid, "name": name, "type": kind, "parent": parent}


# Кусок справочника Директа: страны, округа, области и города.
REGIONS = {
    r["id"]: r
    for r in [
        _r(10000, "Земля", "World", None),
        _r(10001, "Евразия", "Continent", 10000),
        _r(225, "Россия", "Country", 10001),
        _r(159, "Казахстан", "Country", 10001),
        _r(3, "Центральный федеральный округ", "Region", 225),
        _r(17, "Северо-Западный федеральный округ", "Region", 225),
        _r(26, "Южный федеральный округ", "Region", 225),
        _r(40, "Приволжский федеральный округ", "Region", 225),
        _r(52, "Уральский федеральный округ", "Region", 225),
        _r(1, "Москва и Московская область", "Administrative area", 3),
        _r(213, "Москва", "City", 1),
        _r(10174, "Санкт-Петербург и Ленинградская область", "Administrative area", 17),
        _r(2, "Санкт-Петербург", "City", 10174),
        _r(11162, "Свердловская область", "Administrative area", 52),
        _r(54, "Екатеринбург", "City", 11162),
        _r(11168, "Нижний Тагил", "City", 11162),
        _r(11079, "Нижегородская область", "Administrative area", 40),
        _r(47, "Нижний Новгород", "City", 11079),
        _r(10904, "Новгородская область", "Administrative area", 17),
        _r(24, "Великий Новгород", "City", 10904),
        _r(11119, "Республика Татарстан", "Administrative area", 40),
        _r(43, "Казань", "City", 11119),
        _r(11029, "Ростовская область", "Administrative area", 26),
        _r(39, "Ростов-на-Дону", "City", 11029),
        _r(10841, "Ярославская область", "Administrative area", 3),
        _r(10838, "Ростов", "City", 10841),
        _r(11111, "Республика Башкортостан", "Administrative area", 40),
        _r(172, "Уфа", "City", 11111),
        _r(10995, "Краснодарский край", "Administrative area", 26),
        _r(35, "Краснодар", "City", 10995),
        _r(10832, "Тульская область", "Administrative area", 3),
        _r(15, "Тула", "City", 10832),
    ]
}


def _plus(text, regions=REGIONS):
    parsed = geo.parse_geo(text, regions)
    return None if parsed is None else [set(ids) for ids, _required in parsed["plus"]]


def _fit(text, ids, regions):
    parsed = geo.parse_geo(text, regions)
    return None if parsed is None else geo.regions_fit(parsed, ids, regions)


def test_stems_compared_whole():
    assert geo._stem("казани") == geo._stem("казань")
    assert geo._stem("петербурге") == geo._stem("петербург")
    assert geo._stem("москве") == geo._stem("москва")
    assert geo._stem("казань") != geo._stem("казахстан")
    assert geo._stem("москва") != geo._stem("московская")
    assert geo._stem("уфе") == "уф"  # короче двух букв основа не бывает


def test_phrase_candidates():
    assert _plus("Казань") == [{43}]
    assert _plus("только по Казани") == [{43}]
    assert _plus("Ростов") == [{10838, 39}]  # и Ростов, и Ростов-на-Дону
    # Название без слова типа — город, а не одноимённая область: «Москва» не «Москва и Московская область».
    assert _plus("Москва") == [{213}]
    assert _plus("г. Санкт-Петербург") == [{2}]
    assert _plus("Екб и область") == [{54}, {11162}]
    assert _plus("Нижний Новгород + область") == [{47}, {11079}]
    assert _plus("Казань и республика") == [{43}, {11119}]
    assert _plus("Мособласть") == [{1}]
    assert _plus("Ленобласть") == [{10174}]
    assert _plus("Башкирия") == [{11111}]
    assert _plus("Кубань") == [{10995}]
    assert _plus("вся РФ") == [{225}]
    assert _plus("по всей стране") == [{225}]
    # Числа не учитываются, «радиус» и «км» — служебные слова.
    assert _plus("Казань, радиус 10 км") == [{43}]
    assert _plus("Нижний Новгород") == [{47}]
    assert _plus("Великий Новгород") == [{24}]


def test_unsure_text():
    # Неизвестное место, только исключение, только тип или числа — где показывать, не понять.
    for text in ("Тмутаракань", "Казань и окрестности", "—", "", "кроме Москвы", "область", "213"):
        assert geo.parse_geo(text, REGIONS) is None, text
    # Без справочника — только основные регионы.
    assert geo.parse_geo("Тула", {}) is None
    assert geo.parse_geo("Екб и область", {}) is None  # родителей Екатеринбурга без справочника не знаем
    assert _plus("Москва и область", {}) == [{213}, {1}]
    assert _plus("Питер и ЛО", {}) == [{2}, {10174}]


def test_excluded_part_and_brackets():
    parsed = geo.parse_geo("Россия, кроме Москвы и МО", REGIONS)
    # В исключении кандидаты шире: «кроме Москвы» — исключить можно и город, и Москву с областью.
    assert parsed == {"plus": [({225}, True)], "minus": [({213, 1}, True), ({1}, True)], "open": False}
    # Скобки — пояснение: регион из них допустим, но не обязателен; непонятное в них не мешает.
    assert geo.parse_geo("Екатеринбург (и область)", REGIONS)["plus"] == [({54}, True), ({11162}, False)]
    assert geo.parse_geo("Казань (основной рынок)", REGIONS)["plus"] == [({43}, True)]
    # Исключение в скобках обязательно.
    assert geo.parse_geo("Россия (без Москвы)", REGIONS)["minus"] == [({213, 1}, True)]
    # Исключения отдельной строкой настроек.
    parsed = geo.parse_geo("Россия", REGIONS, ["Исключить регионы: Москва, СПб"])
    assert parsed["minus"] == [({213, 1}, True), ({2, 10174}, True)]


CASES = [
    # (согласовано, регионы группы, со справочником, без справочника); None — не понять, не останавливаем.
    # Раньше останавливали зря.
    ("Екатеринбург и область", [11162], True, None),
    ("Екб и область", [11162], True, None),
    ("Нижний Новгород и область", [11079], True, None),
    ("НН и область", [11079], True, None),
    ("Тула и область", [10832], True, None),
    ("Мособласть", [1], True, True),
    ("Ленобласть", [10174], True, True),
    ("Башкирия", [11111], True, None),
    ("Кубань", [10995], True, None),
    ("Ростов", [39], True, None),
    ("Москва", [213], True, True),
    ("Москва и Московская область", [1], True, True),
    ("Россия, кроме Москвы и МО", [225, -1], True, True),
    ("Екатеринбург (и область)", [11162], True, None),
    # Раньше пропускали.
    ("Россия, кроме Москвы и МО", [1], False, False),
    ("Россия, кроме Москвы и МО", [225], False, False),
    ("Россия, кроме Москвы и МО", [225, -213], False, False),
    ("Россия (кроме Москвы и МО)", [225], False, False),
    ("Казань", [159], False, None),
    ("Нижний Новгород", [11168], False, None),
    ("Великий Новгород", [47], False, None),
    ("Москва", [213, 2], False, False),
    ("Казань, радиус 10 км", [1], False, False),
    # Уже, шире и лишние исключения.
    ("Россия", [213], False, False),
    ("Екатеринбург и область", [54], False, None),
    ("Москва и Московская область", [213], False, False),
    ("Россия", [225, -1], False, False),
    ("Казань", [11119], False, None),
    ("Москва", [1], False, False),
    ("Санкт-Петербург", [10174], False, False),
    # id, которых нет ни в справочнике, ни среди основных, — сверить нечем.
    ("Казань", [999999], None, None),
]


@pytest.mark.parametrize("text, ids, with_dict, known_only", CASES)
def test_regions_fit(text, ids, with_dict, known_only):
    assert _fit(text, ids, REGIONS) is with_dict
    assert _fit(text, ids, {}) is known_only


def test_extra_excluded_rows():
    parsed = geo.parse_geo("Россия", {}, ["Исключить регионы: Москва, СПб"])
    assert geo.regions_fit(parsed, [225, -1, -10174], {}) is True
    assert geo.regions_fit(parsed, [225, -213, -2], {}) is True
    assert geo.regions_fit(parsed, [225], {}) is False
    assert geo.regions_fit(parsed, [225, -1], {}) is False  # Петербург не исключён
    # Исключение вне региона показа исключать не нужно.
    assert geo.regions_fit(geo.parse_geo("Москва, кроме Казани", {}), [213], {}) is True


def _minus(text, regions=REGIONS):
    return [(set(ids), required) for ids, required in geo.parse_geo(text, regions)["minus"]]


# Справочник побольше: город и одноимённая область, район внутри города.
MORE = {
    **REGIONS,
    **{
        r["id"]: r
        for r in [
            _r(59, "Сибирский федеральный округ", "Region", 225),
            _r(11316, "Новосибирская область", "Administrative area", 59),
            _r(65, "Новосибирск", "City", 11316),
            _r(216, "Зеленоград", "City", 213),
        ]
    },
}


def test_untyped_name_is_the_city():
    assert _plus("Новосибирск", MORE) == [{65}]
    assert _plus("Новосибирская область", MORE) == [{11316}]
    assert _fit("Новосибирск", [11316], MORE) is False
    assert _fit("Новосибирск", [65], MORE) is True


def test_exclusion_words():
    for text in ("Россия, не включая Москву", "Россия, не считая Москвы", "Россия, за исключением Москвы"):
        assert _plus(text) == [{225}], text
        assert _minus(text) == [({213, 1}, True)], text
        assert _fit(text, [225, -213], REGIONS) is True, text
        assert _fit(text, [225], REGIONS) is False, text
    # «Не исключаем» и «исключительно» — не исключение.
    assert _plus("Россия, Москву не исключаем") == [{225}, {213}]
    assert _plus("исключительно Казань") == [{43}]
    assert _minus("Россия, без исключений") == []
    assert _minus("Россия (без исключений)") == []


def test_exclusion_after_the_place():
    # «X — исключить», «(X исключена)», «X и МО — исключены», «X и МО (исключить)»: исключено то, что перед словом.
    assert _minus("Россия, Москва — исключить") == [({213, 1}, True)]
    assert _minus("Россия (Москва исключена)") == [({213, 1}, True)]
    for text in ("Вся Россия, Москва и МО — исключены", "Россия, Москва и МО (исключить)"):
        assert _plus(text) == [{225}], text
        assert _minus(text) == [({213, 1}, True), ({1}, True)], text
        assert _fit(text, [225, -1], REGIONS) is True, text
        assert _fit(text, [225, -213], REGIONS) is False, text  # область не исключена
    assert _plus("Россия, Москва (исключена), Казань") == [{225}, {43}]
    assert _plus("Москва (не исключать)") == [{213}]


def test_rest_of_country():
    # «Москва и регионы» — не «Москва и область», а Москва и остальная страна.
    assert geo.parse_geo("Москва и регионы", REGIONS)["plus"] == [({213}, True), ({225}, False)]
    assert _fit("Москва и регионы", [225], REGIONS) is True
    assert _fit("Москва и регионы", [213], REGIONS) is True
    assert _fit("Москва и регионы", [1], REGIONS) is True


def test_exclusion_ends_at_comma():
    # После «без Зеленограда» Петербург — вне названного: то ли показать, то ли исключить; не обязателен ни так, ни так.
    text = "Москва без Зеленограда, Санкт-Петербург"
    assert _minus(text, MORE)[0] == ({216}, True)
    assert _fit(text, [213, -216, 2], MORE) is True
    assert _fit(text, [213, -216], MORE) is True
    assert _fit(text, [213, 2], MORE) is False  # Зеленоград не исключён
    # Место внутри названного после запятой — всё ещё исключение.
    assert _minus("Россия, кроме Москвы, Казани") == [({213, 1}, True), ({43}, True)]


def test_remarks_in_exclusion_do_not_stop_parsing():
    for text in ("Россия, кроме Москвы (там дорого)", "Россия, кроме Москвы, как в брифе"):
        parsed = geo.parse_geo(text, REGIONS)
        assert parsed is not None and parsed["open"] is True, text
        assert geo.regions_fit(parsed, [225, -1], REGIONS) is True, text
        assert geo.regions_fit(parsed, [225, -1, -43], REGIONS) is True, text  # пояснение не разобрать — не спорим
        assert geo.regions_fit(parsed, [225], REGIONS) is False, text
    # Первое место после «кроме» не нашлось — текст не понять.
    assert geo.parse_geo("Россия, кроме Тмутаракани", REGIONS) is None


def test_groups_split_regions():
    parsed = geo.parse_geo("Москва и Санкт-Петербург", REGIONS)
    assert geo.groups_fit(parsed, [[213], [2]], REGIONS) == ([], True)
    assert geo.groups_fit(parsed, [[213, 2]], REGIONS) == ([], True)
    assert geo.groups_fit(parsed, [[213]], REGIONS) == ([], False)  # Петербурга нет ни в одной группе
    assert geo.groups_fit(parsed, [[213], [43]], REGIONS) == ([1], False)
    assert geo.groups_fit(parsed, [[213], [2]], {}) == ([], True)
    # Исключение группы допустимо, если его регион показывает другая группа.
    parsed = geo.parse_geo("Москва и Московская область", REGIONS)
    assert geo.groups_fit(parsed, [[213], [1, -213]], REGIONS) == ([], True)
    assert geo.groups_fit(parsed, [[213], [1, -2]], REGIONS) == ([1], True)
    # Исключение текста обязательно в каждой группе, что его показывает.
    parsed = geo.parse_geo("Россия, кроме Москвы и МО", REGIONS)
    assert geo.groups_fit(parsed, [[225, -1], [11162]], REGIONS) == ([], True)
    assert geo.groups_fit(parsed, [[225, -1], [213]], REGIONS) == ([1], True)
    parsed = geo.parse_geo("Москва, Казань, Уфа", REGIONS)
    assert geo.groups_fit(parsed, [[213], [43], [172]], REGIONS) == ([], True)
    assert geo.groups_fit(parsed, [[213], [43]], REGIONS) == ([], False)
    assert geo.groups_fit(parsed, [[213], [999999]], REGIONS) is None


def test_exclusion_rows_against_region():
    # Строка исключений спорит с регионом («Москва» и «Исключить: Москва и МО») — исключать можно, но не нужно.
    parsed = geo.parse_geo("Москва", REGIONS, ["Исключить регионы: Москва и МО"])
    assert geo.regions_fit(parsed, [213], REGIONS) is True
    assert geo.regions_fit(parsed, [1], REGIONS) is False
    # Строка «Исключения: …» и строки старше региона (allowed) — тоже исключения.
    parsed = geo.parse_geo("Россия", REGIONS, ["Исключения: Москва и МО"])
    assert geo.regions_fit(parsed, [225, -1], REGIONS) is True
    assert geo.regions_fit(parsed, [225], REGIONS) is False
    parsed = geo.parse_geo("Россия", REGIONS, allowed=["Исключить: Москва"])
    assert parsed["minus"] == [({213, 1}, False)]
    assert geo.regions_fit(parsed, [225], REGIONS) is True
    assert geo.regions_fit(parsed, [225, -213], REGIONS) is True
    assert geo.regions_fit(parsed, [225, -2], REGIONS) is False


def test_geo_overlap():
    assert geo.geo_overlap("Екатеринбург и область", "Свердловская область", REGIONS) is True
    assert geo.geo_overlap("Казань", "Россия", REGIONS) is True  # одно внутри другого
    assert geo.geo_overlap("Казань", "Казахстан", REGIONS) is False
    assert geo.geo_overlap("Уфа", "Казань", REGIONS) is False
    assert geo.geo_overlap("Нижний Новгород", "Великий Новгород", REGIONS) is False
    assert geo.geo_overlap("Москва", "Казань", {}) is False
    assert geo.geo_overlap("Уфа", "Казань", {}) is None  # Уфы среди основных регионов нет


def test_names_place():
    assert not geo.names_place("Исключить регионы")
    assert not geo.names_place("Минус-регионы")
    assert not geo.names_place("без ограничений")
    assert geo.names_place("кроме Москвы")


def test_index_built_once_per_dictionary():
    assert geo._geo_index(REGIONS) is geo._geo_index(REGIONS)
    assert geo._geo_index({}) is geo._geo_index(None)
    assert geo._geo_index({**REGIONS}) is not geo._geo_index(REGIONS)
