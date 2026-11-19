"""Ручное «в минус-фразы» из структуры кампании: целевой запрос, который ещё и заденет ключи (без БД)."""

from collections import defaultdict

import pytest

from app import search_queries as sq
from app.query_cleaning import stems

CAMP, GROUP, OTHER = 3, 10, 11
KEYWORDS = (
    (1, "кухни с бесплатной доставкой", GROUP),
    (2, "бесплатные кухни икеа", OTHER),
    (3, "шкафы купе", GROUP),
)


def _row(**over) -> dict:
    row = {
        "id": 5,
        "query": "кухни  бесплатно",
        "query_norm": "кухни бесплатно",
        "campaign_row_id": CAMP,
        "adgroup_row_id": GROUP,
        "workspace_id": 9,
        "marked_target": True,
        "enabled": True,
    }
    return {**row, **over}


@pytest.fixture
def env(monkeypatch):
    """Строки запросов, ключи и Директ — подставные; что дошло до Директа и что легло в «нужно решить» — в state."""
    state = {"row": _row(), "applied": [], "index_calls": [], "parked": [], "apply_outcome": None}

    def fake_index(user_id, campaign_ids):
        state["index_calls"].append(list(campaign_ids))
        by_group, by_campaign = defaultdict(list), defaultdict(list)
        for kid, text, group in KEYWORDS:
            item = {
                "id": kid,
                "keyword": text,
                "adgroup_row_id": group,
                "adgroup_name": f"Группа {group}",
                "campaign_row_id": CAMP,
                "stems": stems(text),
            }
            by_group[group].append(item)
            if CAMP in campaign_ids:
                by_campaign[CAMP].append(item)
        return by_group, by_campaign

    def fake_apply(user_id, entries, *, force=False):
        state["applied"].append(([e["phrase"] for e in entries], force))
        made = {
            e["key"]: state["apply_outcome"] or {"status": "ok", "message": "", "phrase": e["phrase"], "level": e["level"]}
            for e in entries
        }
        return {"campaigns": {}, "adgroups": {}, "skipped": [], "errors": []}, made

    monkeypatch.setattr(sq, "_query_rows", lambda user_id, ids: [dict(state["row"]) for _ in ids])
    monkeypatch.setattr(sq, "_keyword_index", fake_index)
    monkeypatch.setattr(sq, "apply_negative_entries", fake_apply)
    monkeypatch.setattr(sq, "_remember", lambda *a, **k: {})
    monkeypatch.setattr(sq, "_log", lambda *a, **k: 77)
    monkeypatch.setattr(sq, "suggest_labels", lambda *a, **k: state["parked"].append(a))
    return state


def test_target_query_that_hits_keywords_names_both_reasons(env):
    res = sq.add_query_as_negative(1, 5, scope="campaign")
    assert res == {
        "status": "needs_confirm",
        "message": "Вы отметили запрос целевым, и минус-фраза заденет ключи "
        "«кухни с бесплатной доставкой», «бесплатные кухни икеа»",
        "phrase": "кухни бесплатно",
        "conflicts": ["кухни с бесплатной доставкой", "бесплатные кухни икеа"],
    }
    assert env["applied"] == []  # до «да» в Директ ничего не уходит


def test_adgroup_scope_checks_only_keywords_of_its_group(env):
    res = sq.add_query_as_negative(1, 5, scope="adgroup")
    assert res["message"] == "Вы отметили запрос целевым, и минус-фраза заденет ключ «кухни с бесплатной доставкой»"
    assert res["conflicts"] == ["кухни с бесплатной доставкой"]


def test_query_without_group_is_checked_across_campaign(env):
    env["row"] = _row(adgroup_row_id=None)
    res = sq.add_query_as_negative(1, 5, scope="campaign")
    assert res["conflicts"] == ["кухни с бесплатной доставкой", "бесплатные кухни икеа"]


def test_target_query_without_conflicts_keeps_short_reason(env):
    env["row"] = _row(query="кухни на заказ", query_norm="кухни на заказ")
    res = sq.add_query_as_negative(1, 5, scope="campaign")
    assert res == {
        "status": "needs_confirm",
        "message": "Вы отметили запрос целевым",
        "phrase": "кухни на заказ",
        "conflicts": [],
    }


def test_conflict_without_target_mark_is_reported_as_before(env):
    env["row"] = _row(marked_target=False)
    env["apply_outcome"] = {
        "status": "skipped",
        "message": "Заденет ключ «кухни с бесплатной доставкой»",
        "conflicts": [{"keyword": "кухни с бесплатной доставкой"}],
        "phrase": "кухни бесплатно",
        "level": "adgroup",
    }
    res = sq.add_query_as_negative(1, 5, scope="adgroup")
    assert res["message"] == "Заденет ключ «кухни с бесплатной доставкой»"
    assert res["conflicts"] == ["кухни с бесплатной доставкой"]
    assert env["applied"] == [(["кухни бесплатно"], False)]
    assert env["index_calls"] == []  # ключи проверил общий путь, второй раз не читаем


def test_confirmed_retry_adds_phrase(env):
    res = sq.add_query_as_negative(1, 5, scope="campaign", force=True)
    assert res == {"status": "ok", "scope": "campaign", "phrase": "кухни бесплатно", "log_id": 77}
    assert env["applied"] == [(["кухни бесплатно"], True)]


def test_rules_and_ai_path_still_skip_target_query_quietly(env):
    res = sq.bulk_search_query_action(1, query_ids=[5], action="negative_campaign", park=True)
    assert (res["applied"], res["skipped"]) == (0, 1)
    assert res["skipped_items"] == [
        {"query_row_id": 5, "query": "кухни  бесплатно", "message": "Вы отметили запрос целевым", "conflicts": []}
    ]
    # Целевой запрос не уходит в «нужно решить» и ключи ради него не читаем.
    assert env["parked"] == [] and env["index_calls"] == [] and env["applied"] == []


def test_blockers_card_names_target_mark_or_keywords(env):
    assert sq.negative_blockers(1, [5], "campaign") == [("кухни  бесплатно", "вы отметили запрос целевым")]
    env["row"] = _row(marked_target=False)
    assert sq.negative_blockers(1, [5], "adgroup") == [
        ("кухни  бесплатно", "заденет ключ «кухни с бесплатной доставкой»")
    ]
