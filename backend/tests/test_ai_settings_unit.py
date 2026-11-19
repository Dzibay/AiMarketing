"""Настройки черновика: пункты «Настроек» словами, сверка черновика с согласованным и пункт «Готовности»."""

import pytest

from app import ai_agent, geo
from app.yandex import YandexError
from tests.test_geo_resolve_unit import REGIONS


def _rows(*pairs):
    return [{"text": name, "value": value} for name, value in pairs]


def test_setting_rows_by_name_newest_first():
    rows = ai_agent._setting_rows(_rows(
        ("Регион показа", "Самара"), ("Регион", "Казань"), ("Недельный бюджет", "3 000 ₽"),
        ("Стратегия", "Максимум конверсий"), ("Тип кампании", "ЕПК"), ("Старт", "1 декабря"), ("Цель", "Заявка"),
    ))
    assert rows["region"] == {"name": "Регион показа", "value": "Самара"}
    assert {k: v["value"] for k, v in rows.items()} == {
        "region": "Самара", "budget": "3 000 ₽", "strategy": "Максимум конверсий", "type": "ЕПК",
        "dates": "1 декабря", "goal": "Заявка",
    }
    # Без «Значения» пункт — сам текст строки.
    assert ai_agent._setting_rows([{"text": "Гео: Москва"}])["region"]["value"] == "Гео: Москва"


def test_setting_rows_region_exclusions():
    # Корректировки ставок по регионам — не регион показа; исключения — отдельным пунктом, а не вместо региона.
    rows = ai_agent._setting_rows(_rows(
        ("Корректировки ставок по регионам", "Москва +20%"), ("Исключить регионы", "Москва, СПб"), ("Регион", "Россия"),
    ))
    assert rows["region"] == {"name": "Регион", "value": "Россия"}
    assert rows["region_minus"] == {"name": "исключить", "value": "Исключить регионы: Москва, СПб"}

    rows = ai_agent._setting_rows([{"text": "Минус-регионы: Москва"}, {"text": "Гео", "value": "Россия"}])
    assert rows["region"]["value"] == "Россия"
    assert rows["region_minus"]["value"] == "Минус-регионы: Москва"

    # Исключение в названии строки региона.
    rows = ai_agent._setting_rows(_rows(("Регион (кроме Москвы)", "Россия"), ("Регионы-исключения", "Тула")))
    assert rows["region"] == {"name": "Регион (кроме Москвы)", "value": "Россия"}
    assert rows["region_minus"]["value"] == "кроме Москвы; Регионы-исключения: Тула"

    # «Без ограничений» — не исключение.
    rows = ai_agent._setting_rows(_rows(("Регион без ограничений", "вся Россия")))
    assert rows["region"]["value"] == "вся Россия" and "region_minus" not in rows

    # Строка «Исключения» — исключение из региона, если в значении место; минус-фразы — нет.
    rows = ai_agent._setting_rows(_rows(("Исключения", "Москва и МО"), ("Минус-фразы", "бесплатно"), ("Регион", "Россия")))
    assert rows["region"]["value"] == "Россия"
    assert rows["region_minus"]["value"] == "Исключения: Москва и МО"
    assert ai_agent._setting_rows([{"text": "Исключения: Москва"}])["region_minus"]["value"] == "Исключения: Москва"
    assert "region_minus" not in ai_agent._setting_rows(_rows(("Исключения", "—"), ("Регион", "Россия")))


def _acted(*rows):
    """Строки доски с номером таблицы: (название, значение, action_id)."""
    return [{"text": name, "value": value, "action_id": aid} for name, value, aid in rows]


def test_setting_rows_follow_table_order():
    # Правка доски меняет строку на месте: «Регион» из таблицы 9 стоит раньше «Регион» из таблицы 3 — побеждает 9.
    rows = ai_agent._setting_rows(_acted(("Регион показа", "Самара", 3), ("Регион", "Казань", 9)))
    assert rows["region"]["value"] == "Казань"
    # Без номеров у всех строк — порядок как пришёл.
    rows = ai_agent._setting_rows(_rows(("Регион показа", "Самара"), ("Регион", "Казань")))
    assert rows["region"]["value"] == "Самара"


def test_setting_rows_old_exclusions():
    # Регион поменяли позже исключений — прежние исключения уже не обязательны.
    rows = ai_agent._setting_rows(_acted(("Исключить регионы", "Москва", 5), ("Регион", "Казань", 9)))
    assert rows["region"]["value"] == "Казань" and "region_minus" not in rows
    assert rows["region_minus_old"]["value"] == "Исключить регионы: Москва"
    # Исключения той же таблицы или новее — действуют.
    rows = ai_agent._setting_rows(_acted(("Исключить регионы", "Москва", 9), ("Минус-регионы", "Тула", 12), ("Регион", "Россия", 9)))
    assert rows["region_minus"]["value"] == "Минус-регионы: Тула; Исключить регионы: Москва"
    assert "region_minus_old" not in rows
    # Регион «без исключений» ослабляет и исключения той же таблицы.
    rows = ai_agent._setting_rows(_acted(("Исключить регионы", "Москва", 9), ("Регион", "Россия, без исключений", 9)))
    assert "region_minus" not in rows and rows["region_minus_old"]["value"] == "Исключить регионы: Москва"
    rows = ai_agent._setting_rows(_rows(("Исключить регионы", "Москва"), ("Регион", "Россия без исключений")))
    assert "region_minus" not in rows and "region_minus_old" in rows


@pytest.mark.parametrize("text, rub", [
    ("3 000 ₽", 3000.0), ("15 тыс.", 15000.0), ("2,5 тыс ₽", 2500.0), ("от 500 руб в день", 500.0),
    ("3 500 ₽/неделя", 3500.0), ("не знаем", None),
])
def test_rub_amount(text, rub):
    assert ai_agent._rub_amount(text) == rub


def test_budget_period():
    assert ai_agent._budget_of({"name": "Бюджет", "value": "500 ₽/день"}) == (500.0, "day")
    assert ai_agent._budget_of({"name": "Недельный бюджет", "value": "3 000 ₽"}) == (3000.0, "week")
    assert ai_agent._budget_of({"name": "Дневной бюджет", "value": "700"}) == (700.0, "day")
    # Месяца Директ не знает, а без суммы сверять нечего.
    assert ai_agent._budget_of({"name": "Бюджет", "value": "30 000 ₽ в месяц"}) is None
    assert ai_agent._budget_of({"name": "Бюджет", "value": "как в прошлый раз"}) is None


def test_geo_overlap():
    assert ai_agent._geo_overlap("Казань", "только по Казани")
    assert ai_agent._geo_overlap("Москва и Московская область", "Москва")
    # Было «не пересекаются»: теперь регион внутри другого — общий регион, бриф и настройки не спорят.
    assert ai_agent._geo_overlap("Казань", "Россия")
    assert not ai_agent._geo_overlap("Ленинградская область", "Московская область")
    # Сокращения и разговорные названия — как полные.
    assert ai_agent._geo_overlap("Мск и МО", "Москва и Московская область")
    assert ai_agent._geo_overlap("Подмосковье", "Московская область")
    assert ai_agent._geo_overlap("СПб", "Санкт-Петербург")
    assert ai_agent._geo_overlap("Питер и ЛО", "Санкт-Петербург и Ленинградская область")
    assert ai_agent._geo_overlap("ЕКБ", "Екатеринбург")
    assert ai_agent._geo_overlap("вся РФ", "Россия")
    assert ai_agent._geo_overlap("Уфа", "Уфа")
    assert not ai_agent._geo_overlap("Мск", "Санкт-Петербург")
    # Было «не пересекаются» и без справочника: Уфы среди основных регионов нет — не понять, не предупреждаем;
    # со справочником Уфа и Казань — разные города.
    assert ai_agent._geo_overlap("Уфа", "Казань")
    assert not ai_agent._geo_overlap("Уфа", "Казань", REGIONS)
    assert not ai_agent._geo_overlap("Казань", "Казахстан", REGIONS)
    assert ai_agent._geo_overlap("Екб и область", "Свердловская область", REGIONS)
    # Описание, из которого регион не понять, — не останавливаем зря.
    assert ai_agent._geo_overlap("—", "Казань")


def test_strategy_kind():
    assert ai_agent._strategy_kind("Ручные ставки") == "manual"
    assert ai_agent._strategy_kind("Максимум кликов") == "clicks"
    assert ai_agent._strategy_kind("Максимум конверсий") == "conv"
    assert ai_agent._strategy_kind("Оплата за конверсии") == "pay_conv"
    assert ai_agent._strategy_kind("на усмотрение Директа") is None
    assert ai_agent._spec_strategy_kind(None) == "manual"
    assert ai_agent._spec_strategy_kind("WB_MAXIMUM_CLICKS") == "clicks"
    assert ai_agent._spec_strategy_kind("PAY_FOR_CONVERSION") == "pay_conv"
    assert ai_agent._spec_strategy_kind("WB_MAXIMUM_CONVERSION_RATE") == "conv"


def _items(*pairs):
    return [{"text": name, "value": value, "checked": True} for name, value in pairs]


def test_settings_check_on_board():
    full = _items(("Регион", "Казань"), ("Тип", "Текстово-графическая"), ("Стратегия", "Ручные ставки"),
                  ("Бюджет", "500 ₽/день"), ("Даты", "с 1 декабря"))
    assert ai_agent._settings_check({"goals": [], "settings": []}) is None
    check = ai_agent._settings_check({"goals": [], "settings": full})
    assert (check["state"], check["note"]) == ("ok", "Казань · Ручные ставки · 500 ₽/день")

    no_region = [i for i in full if i["text"] != "Регион"]
    check = ai_agent._settings_check({"goals": [], "settings": no_region})
    assert check["state"] == "bad" and "не указан регион" in check["note"]

    # Снятая галочка — пункта нет.
    unchecked = [{**i, "checked": i["text"] != "Регион"} for i in full]
    assert ai_agent._settings_check({"goals": [], "settings": unchecked})["state"] == "bad"

    brief = _items(("Гео", "Москва"), ("Бюджет", "700 ₽ в день"))
    check = ai_agent._settings_check({"goals": brief, "settings": full})
    assert check["state"] == "warn"
    assert check["note"] == "в брифе «Москва», в настройках «Казань»; бюджет в брифе 700 ₽ в день, в настройках 500 ₽ в день"

    # Стратегии на конверсии нужна цель; ручным ставкам — нет.
    conv = _items(("Регион", "Казань"), ("Стратегия", "Максимум конверсий"), ("Бюджет", "3 000 ₽ в неделю"))
    check = ai_agent._settings_check({"goals": [], "settings": conv})
    assert (check["state"], check["note"]) == ("warn", "не указаны: тип кампании, даты, цель")


def test_settings_check_region_against_brief(monkeypatch):
    monkeypatch.setattr(geo, "cached_regions", lambda: REGIONS)
    settings = _items(("Регион", "Свердловская область"), ("Тип", "ЕПК"), ("Стратегия", "Ручные ставки"),
                      ("Бюджет", "500 ₽/день"), ("Даты", "с 1 декабря"))

    def check(brief_geo, region):
        rows = [{**i, "value": region} if i["text"] == "Регион" else i for i in settings]
        return ai_agent._settings_check({"goals": _items(("Гео", brief_geo)), "settings": rows})

    assert check("Екб и область", "Свердловская область")["state"] == "ok"
    # Бриф шире настроек — не спор: Казань в России.
    assert check("вся Россия", "Казань")["state"] == "ok"
    assert (check("Казань", "Казахстан")["state"], check("Казань", "Казахстан")["note"]) == (
        "warn", "в брифе «Казань», в настройках «Казахстан»"
    )
    # Справочник ещё не загружен — Тагила среди основных регионов нет, не предупреждаем.
    monkeypatch.setattr(geo, "cached_regions", lambda: {})
    assert check("Нижний Тагил", "Екатеринбург")["state"] == "ok"


SPEC_GROUP = {"name": "Окна", "keywords": ["купить окна"], "region_ids": [225]}


@pytest.fixture
def agreed(monkeypatch):
    """Согласованные «Настройки» чата — без БД."""
    rows: list[dict] = []
    monkeypatch.setattr(ai_agent, "_chat_settings", lambda ctx: list(reversed(rows)))
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    return rows


class Ctx:
    user_id = 1
    ws_id = 2
    chat_id = 3


def _check(spec):
    ai_agent._require_settings(Ctx(), spec, spec["groups"])


def test_draft_must_match_agreed_settings(agreed):
    spec = {"name": "Окна", "groups": [SPEC_GROUP]}
    _check(spec)  # настроек не согласовывали — сверять не с чем

    agreed += _rows(("Регион", "Казань"), ("Стратегия", "Максимум кликов"), ("Бюджет", "3 000 ₽ в неделю"))
    with pytest.raises(YandexError) as err:
        _check({**spec, "strategy": {"search": "HIGHEST_POSITION"}})
    assert err.value.message.startswith(
        "Черновик расходится с согласованными настройками — регион: в настройках «Казань», в черновике «Россия»; "
        "стратегия: в настройках «Максимум кликов», в черновике HIGHEST_POSITION; "
        "бюджет: в настройках 3 000 ₽ в неделю, в черновике бюджета нет."
    )

    kazan = {**SPEC_GROUP, "region_ids": [43]}
    good = {"name": "Окна", "strategy": {"search": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 3000}, "groups": [kazan]}
    _check(good)
    with pytest.raises(YandexError) as err:
        _check({**good, "strategy": {"search": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 5000}})
    assert "бюджет: в настройках 3 000 ₽ в неделю, в черновике 5 000 ₽ в неделю" in err.value.message

    # Одна группа из двух не в том регионе — названа она.
    with pytest.raises(YandexError) as err:
        _check({**good, "groups": [kazan, {**SPEC_GROUP, "name": "Двери", "region_ids": [213]}]})
    assert "регион: в настройках «Казань», в черновике «Москва» (группы «Двери»)" in err.value.message

    # Без справочника Директа названия региона не узнать — не останавливаем.
    _check({**good, "groups": [{**kazan, "region_ids": [11119]}]})


def _region_problem(ids: list[int]) -> str | None:
    """Текст отказа по черновику с одной группой в регионах ids; None — черновик прошёл сверку."""
    try:
        _check({"name": "Окна", "groups": [{**SPEC_GROUP, "region_ids": ids}]})
    except YandexError as err:
        return err.message
    return None


REGION_CASES = [
    # (согласованный регион, регионы группы, остановит со справочником, остановит без справочника).
    # Раньше останавливали зря: область целиком, сокращения, разговорные названия, исключение.
    ("Екатеринбург и область", [11162], False, False),
    ("Екб и область", [11162], False, False),
    ("НН и область", [11079], False, False),
    ("Тула и область", [10832], False, False),
    ("Мособласть", [1], False, False),
    ("Ленобласть", [10174], False, False),
    ("Башкирия", [11111], False, False),
    ("Кубань", [10995], False, False),
    ("Ростов", [39], False, False),
    ("Россия, кроме Москвы и МО", [225, -1], False, False),
    ("Казань (основной рынок)", [43], False, False),
    # Раньше пропускали: «Казань» ~ «Казахстан», «Новгород» в двух городах, исключение не перенесено в группу.
    ("Казань", [159], True, False),
    ("Нижний Новгород", [11168], True, False),
    ("Великий Новгород", [47], True, False),
    ("Россия, кроме Москвы и МО", [225], True, True),
    ("Россия, кроме Москвы и МО", [1], True, True),
    ("Россия, кроме Москвы и МО", [225, -213], True, True),
    ("Москва", [213, 2], True, True),
    ("Казань, радиус 10 км", [1], True, True),
    # Исключения, которых не согласовывали, и регион уже согласованного.
    ("Россия", [225, -1], True, True),
    ("Москва и Московская область", [213], True, True),
    # id, которых нет ни в справочнике, ни среди основных, — сверить нечем.
    ("Россия", [999999], False, False),
]


@pytest.mark.parametrize("text, ids, with_dict, known_only", REGION_CASES)
def test_draft_region_vs_agreed(agreed, monkeypatch, text, ids, with_dict, known_only):
    agreed += _rows(("Регион", text))
    assert (_region_problem(ids) is not None) is known_only
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    assert (_region_problem(ids) is not None) is with_dict


def test_draft_region_exclusion_rows(agreed, monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    agreed += _rows(("Регион", "Россия"), ("Исключить регионы", "Москва, СПб"),
                    ("Корректировки ставок по регионам", "Москва +20%"))
    assert _region_problem([225, -1, -10174]) is None
    assert _region_problem([225, -213, -2]) is None
    assert "регион: в настройках «Россия; Исключить регионы: Москва, СПб», в черновике «Россия, кроме: Москва»" in (
        _region_problem([225, -213]) or ""
    )
    assert _region_problem([225]) is not None

    # Исключение в названии строки региона.
    agreed[:] = _rows(("Регион (кроме Москвы)", "Россия"))
    assert _region_problem([225, -1]) is None
    assert "в настройках «Россия; кроме Москвы», в черновике «Россия»" in (_region_problem([225]) or "")


def _groups_problem(*regions: list[int]) -> str | None:
    """Текст отказа по черновику с группами в регионах regions; None — черновик прошёл сверку."""
    groups = [{**SPEC_GROUP, "name": f"Г{n}", "region_ids": ids} for n, ids in enumerate(regions, 1)]
    try:
        _check({"name": "Окна", "groups": groups})
    except YandexError as err:
        return err.message
    return None


def test_draft_regions_split_across_groups(agreed, monkeypatch):
    # По группе на город — обычное дело: группа берёт часть названного, а все места — хоть в одной группе.
    agreed += _rows(("Регион", "Москва и Санкт-Петербург"))
    assert _groups_problem([213], [2]) is None  # без справочника — основные регионы
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    assert _groups_problem([213], [2]) is None
    assert _groups_problem([213, 2]) is None
    assert "регион: в настройках «Москва и Санкт-Петербург», в черновике «Москва»." in _groups_problem([213], [213])
    problem = _groups_problem([213], [43])
    assert "в черновике «Казань» (группы «Г2»)" in problem
    assert "в черновике «Москва, Казань»" in problem
    # Группа без региона — «регион не выбран» ловит своя проверка, покрытие не сверяем.
    assert _groups_problem([213], []) is None

    # Исключение одной группы показывает другая.
    agreed[:] = _rows(("Регион", "Москва и Московская область"))
    assert _groups_problem([213], [1, -213]) is None
    assert "в черновике «Москва»." in _groups_problem([213], [213])

    # Исключение текста — в каждой группе, что его показывает.
    agreed[:] = _rows(("Регион", "Россия, кроме Москвы и МО"))
    assert _groups_problem([225, -1], [11162]) is None
    assert "в черновике «Москва» (группы «Г2»)" in _groups_problem([225, -1], [213])

    # Регион тот же, что раньше, — сообщение одно.
    agreed[:] = _rows(("Регион", "Казань"))
    assert _groups_problem([225]).count("регион:") == 1


def test_draft_region_vs_old_exclusions(agreed, monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    # Регион из таблицы 9, исключение — из таблицы 5: исключать Москву можно, но не нужно.
    agreed += _acted(("Регион", "Россия", 9), ("Исключить регионы", "Москва", 5))
    assert _region_problem([225]) is None
    assert _region_problem([225, -213]) is None
    assert _region_problem([225, -2]) is not None
    # Та же таблица — исключение обязательно.
    agreed[:] = _acted(("Регион", "Россия", 9), ("Исключить регионы", "Москва", 9))
    assert "в настройках «Россия; Исключить регионы: Москва», в черновике «Россия»" in (_region_problem([225]) or "")
    assert _region_problem([225, -213]) is None
    # «Без исключений» — исключение той же таблицы не обязательно.
    agreed[:] = _acted(("Регион", "Россия, без исключений", 9), ("Исключить регионы", "Москва", 9))
    assert _region_problem([225]) is None
    assert _region_problem([225, -213]) is None
    # Строка «Исключения» со значением-местом.
    agreed[:] = _rows(("Регион", "Россия"), ("Исключения", "Москва и МО"))
    assert _region_problem([225, -1]) is None
    assert "в настройках «Россия; Исключения: Москва и МО», в черновике «Россия»" in (_region_problem([225]) or "")


def test_draft_type_and_daily_budget(agreed):
    agreed += _rows(("Тип кампании", "ЕПК"), ("Бюджет", "500 ₽/день"))
    spec = {"name": "Окна", "daily_budget_rub": 500, "groups": [SPEC_GROUP]}
    with pytest.raises(YandexError) as err:
        _check(spec)
    assert "тип: в настройках «ЕПК», в черновике «Текстово-графическая»" in err.value.message
    _check({**spec, "type": "UNIFIED_CAMPAIGN"})
    # Дневной бюджет согласован, а в черновике недельный — сверяем по неделе.
    _check({"name": "Окна", "type": "UNIFIED_CAMPAIGN", "strategy": {"weekly_budget_rub": 3500}, "groups": [SPEC_GROUP]})
