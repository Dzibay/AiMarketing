"""I33: правила памяти «для всех проектов» и «догнать» без базы — всё, что ходит в Postgres, подменено."""

import pytest

from app import negative_lists as nl
from app import query_cleaning as qc
from app import shared_cleaning as sc
from app.yandex import YandexError

ROW = {"norm": "шкафы вакансии", "query": "шкафы вакансии", "campaign_row_ids": [1], "adgroup_row_ids": [10]}
NEGS = {"campaigns": {1: []}, "adgroups": {10: []}, "group_campaign": {10: 1}}


def _shared(phrase, query=""):
    return (phrase, qc.stems(phrase), "campaign", query or phrase, "shared")


def test_rule_label_shared_tuple_after_project_memory():
    project = [("вакансии", qc.stems("вакансии"), "adgroup", "кухни вакансии")]
    hit = qc.rule_label(ROW, project + [_shared("вакансии")], [], NEGS)
    assert hit["reason"] == "Как «кухни вакансии» — вы минусовали «вакансии»" and "_shared" not in hit
    hit = qc.rule_label(ROW, [_shared("вакансии")], [], NEGS)
    assert hit == {
        "verdict": "negative",
        "negative_phrase": "вакансии",
        "level": "campaign",
        "source": "memory",
        "reason": "Вы минусуете «вакансии» во всех проектах",
        "_shared": True,
    }
    assert qc.rule_label(ROW, [_shared("работа")], [], NEGS) is None


def test_rule_split_drops_shared_hit_that_hits_keywords(monkeypatch):
    monkeypatch.setattr(qc, "_negatives_index", lambda *a: NEGS)
    monkeypatch.setattr(qc, "_memory_rules", lambda w: [_shared("вакансии")])
    keyword = {"id": 5, "keyword": "вакансии шкафы", "stems": qc.stems("вакансии шкафы"), "adgroup_row_id": 10, "adgroup_name": "Г"}
    monkeypatch.setattr(qc, "_keyword_index", lambda u, ids: ({10: [keyword]}, {1: [keyword]}))
    labels, rest = qc._rule_split(1, 2, {}, [ROW])
    assert labels == [] and rest == [ROW]

    monkeypatch.setattr(qc, "_keyword_index", lambda u, ids: ({}, {}))
    labels, rest = qc._rule_split(1, 2, {}, [ROW])
    assert [lab["negative_phrase"] for lab in labels] == ["вакансии"] and "_shared" not in labels[0] and rest == []


def test_shared_rules_respect_targets_and_project_memory(monkeypatch):
    shared = [
        {"phrase": "вакансии", "phrase_key": "вакансии", "query": "кухни вакансии"},
        {"phrase": "работа", "phrase_key": "работа", "query": ""},
        {"phrase": "бесплатно", "phrase_key": "бесплатно", "query": ""},
    ]
    targets = [qc.stems("шкафы работа на дому")]
    monkeypatch.setattr(sc, "_project_view", lambda ws: (shared, targets, {"бесплатно"}))
    rules = sc.shared_rules(7)
    assert [r[0] for r in rules] == ["вакансии"] and rules[0][2:] == ("campaign", "кухни вакансии", "shared")
    assert sc.shared_examples(7, 5) == [{"query": "кухни вакансии", "negative_phrase": "вакансии", "level": "campaign"}]
    assert sc.shared_examples(7, 0) == []


def test_memory_examples_appends_shared_within_limit(monkeypatch):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            verdict = params[1]
            rows = [{"query": "кухни икеа", "negative_phrase": "икеа", "level": "adgroup"}] if verdict == "negative" else []
            return type("C", (), {"fetchall": lambda self: rows})()

    monkeypatch.setattr(qc.pool, "connection", lambda: Conn())
    import app.shared_cleaning as mod

    monkeypatch.setattr(
        mod,
        "shared_examples",
        lambda ws, limit: [
            {"query": "x", "negative_phrase": "икеа", "level": "campaign"},
            {"query": "y", "negative_phrase": "вакансии", "level": "campaign"},
            {"query": "z", "negative_phrase": "работа", "level": "campaign"},
        ],
    )
    out = qc.memory_examples(3, 2)
    assert [e["negative_phrase"] for e in out["negative"]] == ["икеа", "вакансии"]


def test_catch_up_selection_only_linked(monkeypatch):
    monkeypatch.setattr(nl, "_list_row", lambda u, l: {"id": l, "name": "Стоп", "phrases": ["x"]})
    monkeypatch.setattr(nl, "_linked_ids", lambda l: {1, 2})
    monkeypatch.setattr(nl, "_campaigns", lambda u, ids: [{"id": i} for i in ids])
    nlist, selected, rows = nl._selection(5, 9, [2, 3], catch_up=True)
    assert selected == {2} and [r["id"] for r in rows] == [2]
    with pytest.raises(YandexError) as exc:
        nl._selection(5, 9, [3], catch_up=True)
    assert exc.value.code == "empty"
    # Обычное применение берёт и привязанные — чтобы снять список с невыбранных.
    _n, selected, rows = nl._selection(5, 9, [2])
    assert selected == {2} and [r["id"] for r in rows] == [1, 2]


def test_to_list_items_validation():
    with pytest.raises(YandexError):
        sc._clean_to_list_items([])
    with pytest.raises(YandexError):
        sc._clean_to_list_items([{"query_norm": f"q{i}"} for i in range(sc.TO_LIST_MAX + 1)])
    got = sc._clean_to_list_items([{"query_norm": "a"}, {"query_norm": "a"}, "junk", {"query_norm": "b", "negative_phrase": " x "}])
    assert got == [{"query_norm": "a", "negative_phrase": ""}, {"query_norm": "b", "negative_phrase": "x"}]
