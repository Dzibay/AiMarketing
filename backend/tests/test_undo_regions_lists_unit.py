"""Откат регионов кампании и общих списков минус-фраз: шаги отката и их описание без базы."""

from app import geo, undo
from app import negative_lists as nl


def test_ops_registered():
    assert {"adgroup_regions", "campaign_group_regions", "negative_list_links"} <= set(undo.OPS)


def test_regions_undo_keeps_each_group_and_skips_empty():
    step = geo._regions_undo(5, {11: [213], 12: [2, -10174], 13: []})
    assert step == {
        "label": "Вернуть прежние регионы",
        "steps": [{"op": "campaign_group_regions", "args": {"campaign_row_id": 5, "groups": {"11": [213], "12": [2, -10174]}}}],
    }
    assert geo._regions_undo(5, {13: []}) is None
    assert geo._version({"campaign_type": "UNIFIED_CAMPAIGN"}) == "v501"
    assert geo._version({"campaign_type": "TEXT_CAMPAIGN"}) == "v5"


def _entry(current, desired, owned=(), protected=(), action="attach"):
    plan = nl.plan_campaign(list(current), desired, list(owned), set(protected))
    return {"_plan": plan, "add": plan["add"], "remove": plan["remove"], "action": action}


def test_undo_entry_attach_and_detach():
    attach = _entry(["бесплатно"], ["бесплатно", "скачать"])
    assert nl._undo_entry(7, attach, []) == {"add": [], "remove": ["скачать"], "links": {"7": None}}

    links = [{"list_id": 7, "added_phrases": ["скачать"], "applied_at": None}]
    detach = _entry(["бесплатно", "скачать"], None, owned=["скачать"], action="detach")
    assert nl._undo_entry(7, detach, links) == {
        "add": ["скачать"],
        "remove": [],
        "links": {"7": {"added": ["скачать"], "applied_at": None}},
    }


def test_undo_entry_keeps_other_lists_only_on_handover():
    links = [
        {"list_id": 7, "added_phrases": ["работа"], "applied_at": None},
        {"list_id": 8, "added_phrases": ["резюме"], "applied_at": None, "phrases": ["работа", "резюме"]},
    ]
    handover = _entry(["работа", "резюме"], None, owned=["работа"], protected={"работа", "резюме"}, action="detach")
    assert handover["_plan"]["handover"] == ["работа"]
    back = nl._undo_entry(7, handover, links)
    assert back["links"] == {"7": {"added": ["работа"], "applied_at": None}, "8": {"added": ["резюме"]}}
    assert back["add"] == [] and back["remove"] == []

    plain = _entry(["работа"], ["работа", "скачать"], owned=["работа"], action="update")
    assert set(nl._undo_entry(7, plain, links)["links"]) == {"7"}


def test_describe_steps(monkeypatch):
    monkeypatch.setattr(undo, "_names", lambda user_id, table, column, ids: ", ".join(f"«{i}»" for i in ids))
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    same = {"op": "campaign_group_regions", "args": {"campaign_row_id": 5, "groups": {"11": [213], "12": [213]}}}
    assert undo.describe_step(1, same) == "Вернуть регионы Москва: «5»"
    mixed = {"op": "campaign_group_regions", "args": {"campaign_row_id": 5, "groups": {"11": [213], "12": [2]}}}
    assert undo.describe_step(1, mixed) == "Вернуть каждой группе прежние регионы: «5»"

    links = {
        "op": "negative_list_links",
        "args": {
            "list_id": 7,
            "name": "Стоп",
            "campaigns": {"1": {"links": {"7": None}}, "2": {"links": {"7": {"added": ["x"]}}}},
        },
    }
    assert undo.describe_step(1, links) == "Список минус-фраз «Стоп»: снять с кампаний «1»; вернуть как было в кампаниях «2»"
