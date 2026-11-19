"""Регионы по названию: подсказки поля «Регионы», выбранные с названиями и инструмент ИИ set_regions."""

from types import SimpleNamespace

import pytest

from app import adgroups, ai_agent, geo
from app.yandex import YandexError
from tests.test_geo_resolve_unit import REGIONS


@pytest.fixture
def regions(monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    return REGIONS


def _names(found):
    return [r["name"] for r in found["regions"]]


def test_suggest_by_name_city_first(regions):
    found = geo.suggest_regions(1, "каз")
    assert _names(found)[:2] == ["Казань", "Казахстан"]
    kazan = found["regions"][0]
    assert kazan == {"id": 43, "name": "Казань", "type": "город", "path": kazan["path"]}
    assert "Татарстан" in kazan["path"]
    assert "note" not in found


def test_suggest_exact_beats_prefix_and_words(regions):
    # «Ростов» — точное совпадение раньше «Ростов-на-Дону» и «Ростовской области».
    assert _names(geo.suggest_regions(1, "ростов"))[:3] == ["Ростов", "Ростов-на-Дону", "Ростовская область"]
    # Слово внутри названия: «новгород» находит и Нижний, и Великий.
    assert {"Нижний Новгород", "Великий Новгород"} <= set(_names(geo.suggest_regions(1, "новгород")))
    # Дефис и «ё» не мешают.
    assert "Ростов-на-Дону" in _names(geo.suggest_regions(1, "ростов на дону"))


def test_suggest_aliases_and_ids(regions):
    assert _names(geo.suggest_regions(1, "питер"))[0] == "Санкт-Петербург"
    assert _names(geo.suggest_regions(1, "213")) == ["Москва"]
    assert geo.suggest_regions(1, "  ") == {"regions": []}
    assert len(geo.suggest_regions(1, "а", limit=3)["regions"]) == 3


def test_region_types_in_russian(monkeypatch):
    # Типы, которых раньше не было в подписях, — по-русски; незнакомый тип — без подписи, а не английским словом.
    own = {
        **REGIONS,
        90001: {"id": 90001, "name": "Пушкинский район", "type": "District", "parent": 225},
        90002: {"id": 90002, "name": "Остров Буяный", "type": "Island", "parent": 225},
    }
    monkeypatch.setattr(geo, "load_regions", lambda user_id: own)
    types = {
        r["name"]: r["type"]
        for query in ("евразия", "земля", "пушкинский", "буяный")
        for r in geo.suggest_regions(1, query)["regions"]
    }
    assert types["Евразия"] == "континент" and types["Земля"] == "весь мир"
    assert types["Пушкинский район"] == "район" and types["Остров Буяный"] == ""
    assert [r["type"] for r in geo.regions_named(1, [10001, 90002])] == ["континент", ""]
    assert geo.search_regions(1, "буяный")["regions"][0]["type"] == ""


def test_suggest_without_dictionary(monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    found = geo.suggest_regions(1, "моск")
    assert "Москва" in _names(found) and "недоступен" in found["note"]


def test_regions_named_keeps_minus(regions):
    named = geo.regions_named(1, [213, -2, "15"])
    assert [(r["id"], r["name"], r["minus"]) for r in named] == [
        (213, "Москва", False),
        (2, "Санкт-Петербург", True),
        (15, "Тула", False),
    ]


def test_check_region_ids(regions, monkeypatch):
    monkeypatch.setattr(geo, "unknown_regions", lambda user_id, ids: [i for i in ids if abs(i) not in REGIONS])
    assert adgroups._check_region_ids(1, [213, -1]) == [213, -1]
    for bad, text in (([-213], "хотя бы один"), ([], "хотя бы один"), ([213, 999999], "999999")):
        with pytest.raises(YandexError) as err:
            adgroups._check_region_ids(1, bad)
        assert err.value.code == "args" and text in err.value.message


# --- Инструмент ИИ ----------------------------------------------------------------------------------


def _ctx():
    return SimpleNamespace(
        user_id=1,
        ws_id=7,
        campaign=lambda cid: {"id": cid, "name": "Кухни"},
        adgroups=lambda ids: [{"id": i, "name": f"Г{i}", "campaign_row_id": 5} for i in ids],
    )


@pytest.fixture
def plan(monkeypatch, regions):
    monkeypatch.setattr(geo, "unknown_regions", lambda user_id, ids: [])
    state = {
        "what": "кампании «Кухни»",
        "groups": 3,
        "changed": 2,
        "mixed": True,
        "before": "Москва, Санкт-Петербург",
        "after": "Казань",
        "items": [{"id": 1, "name": "Г1", "before": "Москва"}, {"id": 2, "name": "Г2", "before": "Санкт-Петербург"}],
    }
    seen = []

    def fake_plan(user_id, region_ids, **target):
        seen.append((region_ids, target))
        return dict(state)

    monkeypatch.setattr(adgroups, "regions_plan", fake_plan)
    return SimpleNamespace(state=state, seen=seen)


def test_set_regions_args(plan):
    prepare = ai_agent.WRITE_TOOLS["set_regions"][0]
    for args in ({"region_ids": [43]}, {"region_ids": [43], "campaign_row_id": 5, "adgroup_row_ids": [1]}):
        with pytest.raises(YandexError) as err:
            prepare(_ctx(), args)
        assert "что-то одно" in err.value.message
    with pytest.raises(YandexError) as err:
        prepare(_ctx(), {"campaign_row_id": 5, "region_ids": [-43]})
    assert "find_regions" in err.value.message
    prepare(_ctx(), {"adgroup_row_id": 2, "region_ids": ["43", -2]})
    assert plan.seen[-1] == ([43, -2], {"adgroup_row_ids": [2]})


def test_set_regions_plan_and_diff(plan):
    prepare = ai_agent.WRITE_TOOLS["set_regions"][0]
    got = prepare(_ctx(), {"campaign_row_id": 5, "region_ids": [43]})
    assert got.summary == "Регионы кампании «Кухни»: Москва, Санкт-Петербург → Казань; изменится в 2 из 3 групп"
    assert got.categories == ["campaigns"]
    assert plan.seen[-1] == ([43], {"campaign_row_id": 5})
    # У групп были разные регионы — в карточке «было» каждой группы.
    diff = ai_agent._DIFFS["set_regions"](_ctx(), {"campaign_row_id": 5, "region_ids": [43]})
    assert [(d["what"], d["before"], d["after"]) for d in diff] == [
        ("Регионы группы «Г1»", "Москва", "Казань"),
        ("Регионы группы «Г2»", "Санкт-Петербург", "Казань"),
        ("Остальные группы", "ещё 1", "Казань"),
    ]
    plan.state.update(mixed=False, groups=1, changed=1, before="Москва", what="группы «Г1»")
    diff = ai_agent._DIFFS["set_regions"](_ctx(), {"adgroup_row_ids": [1], "region_ids": [43]})
    assert [(d["what"], d["before"], d["after"]) for d in diff] == [("Регионы группы «Г1»", "Москва", "Казань")]
    plan.state.update(changed=0)
    with pytest.raises(YandexError) as err:
        prepare(_ctx(), {"adgroup_row_ids": [1], "region_ids": [43]})
    assert "уже стоят" in err.value.message


def test_set_regions_execute(plan, monkeypatch):
    calls = []
    monkeypatch.setattr(adgroups, "set_regions", lambda user_id, ids, **kw: calls.append((ids, kw)) or {"updated": 3})
    execute = ai_agent.WRITE_TOOLS["set_regions"][1]
    assert execute(_ctx(), {"campaign_row_id": 5, "region_ids": [43, -2]}) == {"updated": 3}
    assert calls == [([43, -2], {"workspace_id": 7, "campaign_row_id": 5})]


def test_set_regions_registered():
    names = {t["function"]["name"] for t in ai_agent.TOOLS}
    assert "set_regions" in names and ai_agent.TOOL_LABELS["set_regions"] == "Регионы показа"
    assert "set_regions" in ai_agent._DIFFS
