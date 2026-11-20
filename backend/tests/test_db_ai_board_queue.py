"""Правки и сверка с Директом (docs/board-plan.md, пункт 4): правки записанных шагов копятся до «Применить в
Директ», «Сбросить» их отменяет; изменения в самом Директе — плашкой, «Подтянуть» или «Оставить мои»."""

import pytest

from app import ai_agent
from app.yandex import YandexError
from tests.test_db_ai import _seed, _start, _text, _turn
from tests.test_db_ai import _secret  # noqa: F401 — автофикстура
from tests.test_db_ai_build_memory import GOALS, KEYWORDS, _approve
from tests.test_db_ai_build_sync import CAMPAIGN, FakeDirect, _wizard


def _written(db, monkeypatch):
    """Сборка, в которой группа «Окна ПВХ» с двумя фразами и минус-фраза уже в черновике."""
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    direct = FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", GOALS, campaign=CAMPAIGN))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _wizard("keywords", KEYWORDS))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _text("Готово."))
    with db.connection() as conn:
        group = conn.execute(
            "SELECT id FROM direct_adgroups WHERE campaign_row_id = %s", (direct.campaign,)
        ).fetchone()["id"]
    assert ai_agent.get_chat(user, chat)["build"]["queue"] == []
    return s, direct, group


def _keywords(db):
    with db.connection() as conn:
        return sorted(r["keyword"] for r in conn.execute("SELECT keyword FROM direct_keywords").fetchall())


def test_edits_wait_for_apply_and_reset_drops_them(db, monkeypatch):
    s, direct, _group = _written(db, monkeypatch)
    user, chat = s["user"], s["chat"]
    ai_agent.edit_board(user, chat, [{"op": "add", "kind": "keyword", "group": "Окна ПВХ", "text": "окна недорого"}])
    ai_agent.edit_board(user, chat, [{"op": "add", "kind": "negative", "text": "своими руками"}])
    build = ai_agent.get_chat(user, chat)["build"]
    assert [(c["kind"], c["op"], c["text"]) for c in build["queue"]] == [
        ("keyword", "add", "окна недорого"), ("negative", "add", "своими руками"),
    ]

    build = ai_agent.reset_build_edits(user, chat)["build"]
    assert build["queue"] == []
    texts = [k["text"] for k in build["groups"][0]["keywords"]]
    assert "окна недорого" not in texts
    assert [n["text"] for n in build["negatives"]] == ["бесплатно"]
    before = len(direct.calls)
    ai_agent.retry_build_write(user, chat)
    assert len(direct.calls) == before


def test_apply_writes_the_queue(db, monkeypatch):
    s, direct, _group = _written(db, monkeypatch)
    user, chat = s["user"], s["chat"]
    ai_agent.edit_board(user, chat, [{"op": "add", "kind": "keyword", "group": "Окна ПВХ", "text": "окна недорого"}])
    before = len(direct.calls)
    _turn(user, chat, monkeypatch, _text("Ок."))
    assert len(direct.calls) == before
    out = ai_agent.retry_build_write(user, chat)
    assert out["build"]["queue"] == []
    assert _keywords(db) == ["купить окна пвх", "окна в рассрочку", "окна недорого"]


def test_moderation_waits_for_queued_edits(db, monkeypatch):
    s, direct, _group = _written(db, monkeypatch)
    user, chat = s["user"], s["chat"]
    ai_agent.edit_board(user, chat, [{"op": "add", "kind": "keyword", "group": "Окна ПВХ", "text": "окна недорого"}])
    with pytest.raises(YandexError) as err:
        ai_agent.moderate_build(user, chat)
    assert "правки, которых нет в Директе (1)" in err.value.message
    assert "moderate" not in direct.tools()


def _direct_edit(db, s, group):
    """Маркетолог поправил черновик в самом Директе (локальная копия уже перечитана сверкой)."""
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword) "
            "VALUES (%s, %s, %s, 77, 'окна с установкой')",
            (s["user"], s["acc"], group),
        )
        conn.execute("DELETE FROM direct_keywords WHERE keyword = 'окна в рассрочку'")


def test_changes_in_direct_are_pulled_to_the_board(db, monkeypatch):
    s, direct, group = _written(db, monkeypatch)
    user, chat = s["user"], s["chat"]
    _direct_edit(db, s, group)
    build = ai_agent.get_chat(user, chat)["build"]
    assert {(c["kind"], c["op"], c["text"], c["conflict"]) for c in build["drift"]} == {
        ("keyword", "add", "окна с установкой", False), ("keyword", "remove", "окна в рассрочку", False),
    }
    assert build["queue"] == []

    build = ai_agent.pull_build(user, chat)["build"]
    assert build["drift"] == [] and build["queue"] == []
    texts = sorted(k["text"] for k in build["groups"][0]["keywords"])
    assert texts == ["купить окна пвх", "окна с установкой"]
    # Подтянутое ничего не пишет ни следующим ходом, ни по «Применить».
    before = len(direct.calls)
    _turn(user, chat, monkeypatch, _text("Вижу."))
    ai_agent.retry_build_write(user, chat)
    assert len(direct.calls) == before
    assert _keywords(db) == ["купить окна пвх", "окна с установкой"]


def test_keep_mine_queues_the_board_over_direct(db, monkeypatch):
    s, direct, group = _written(db, monkeypatch)
    user, chat = s["user"], s["chat"]
    # Ту же фразу удалили и на доске, и в Директе, а на доске ещё добавили свою — конфликт по группе.
    ai_agent.edit_board(user, chat, [{"op": "add", "kind": "keyword", "group": "Окна ПВХ", "text": "окна недорого"}])
    _direct_edit(db, s, group)
    build = ai_agent.get_chat(user, chat)["build"]
    assert {c["text"] for c in build["drift"]} == {"окна с установкой", "окна в рассрочку"}

    build = ai_agent.pull_build(user, chat, keep=True)["build"]
    assert build["drift"] == []
    # Доска не изменилась: по «Применить» Директ вернётся к ней.
    assert {(c["op"], c["text"]) for c in build["queue"]} == {
        ("add", "окна в рассрочку"), ("add", "окна недорого"), ("remove", "окна с установкой"),
    }
    ai_agent.retry_build_write(user, chat)
    assert _keywords(db) == ["купить окна пвх", "окна в рассрочку", "окна недорого"]


def test_check_rereads_the_draft_only_when_direct_changed(db, monkeypatch):
    from app import build_sync, direct_api

    s, direct, _group = _written(db, monkeypatch)
    user, chat = s["user"], s["chat"]
    with db.connection() as conn:
        campaign_id = conn.execute(
            "SELECT campaign_id FROM direct_campaigns WHERE id = %s", (direct.campaign,)
        ).fetchone()["campaign_id"]
    answers = iter([
        {"Timestamp": "2026-11-20T10:00:00Z"},
        {"Timestamp": "2026-11-20T10:03:00Z", "Campaigns": []},
        {"Timestamp": "2026-11-20T10:06:00Z", "Campaigns": [{"CampaignId": campaign_id, "ChangesIn": ["CHILDREN"]}]},
    ])
    calls, refreshed = [], []

    def call(token, login, service, method, params, **kw):
        calls.append((method, params.get("Timestamp")))
        return next(answers)

    monkeypatch.setattr(direct_api, "call", call)
    monkeypatch.setattr(build_sync, "get_connection_access_token", lambda *a: "token")
    monkeypatch.setattr(build_sync, "refresh_local", lambda u, row: refreshed.append(row))

    # Метки нет — перечитать и запомнить; дальше — только если Директ говорит, что кампанию меняли.
    assert ai_agent.check_build_direct(user, chat)["changed"] is True
    assert ai_agent.check_build_direct(user, chat) == {"changed": False}
    out = ai_agent.check_build_direct(user, chat)
    assert out["changed"] is True and "build" in out["chat"]
    assert calls == [
        ("checkDictionaries", None),
        ("checkCampaigns", "2026-11-20T10:00:00Z"),
        ("checkCampaigns", "2026-11-20T10:03:00Z"),
    ]
    assert refreshed == [direct.campaign, direct.campaign]


def test_reset_after_pull_returns_the_board_to_direct(db, monkeypatch):
    s, direct, group = _written(db, monkeypatch)
    user, chat = s["user"], s["chat"]
    ai_agent.edit_board(user, chat, [{"op": "remove", "kind": "keyword", "group": "Окна ПВХ", "text": "окна в рассрочку"}])
    ai_agent.edit_board(user, chat, [{"op": "add", "kind": "keyword", "group": "Окна ПВХ", "text": "окна недорого"}])
    _direct_edit(db, s, group)
    # Ту же фразу удалили и на доске, и в Директе — это не конфликт.
    assert not any(c["conflict"] for c in ai_agent.get_chat(user, chat)["build"]["drift"])
    ai_agent.pull_build(user, chat)
    build = ai_agent.reset_build_edits(user, chat)["build"]
    assert build["queue"] == [] and build["drift"] == []
    assert sorted(k["text"] for k in build["groups"][0]["keywords"]) == ["купить окна пвх", "окна с установкой"]
    before = len(direct.calls)
    ai_agent.retry_build_write(user, chat)
    assert len(direct.calls) == before
