"""Группы действий для фильтра «Тип» в истории."""

from app.journal import ACTION_KINDS, ACTION_TITLES


def test_every_action_has_exactly_one_kind():
    grouped = [a for actions in ACTION_KINDS.values() for a in actions]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(ACTION_TITLES)
