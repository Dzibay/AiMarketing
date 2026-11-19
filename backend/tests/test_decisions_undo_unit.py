"""«Сделать иначе» и «Вернуть как было» без базы: текст причины, ответ модели, метка предложения ИИ
в журнале, откат нескольких записей и «Отменить» у задачи, оставившей несколько записей с откатом."""

import json
from contextlib import contextmanager

import pytest

from app import ai_agent, job_handlers, journal, undo
from app.journal import acting_as, collect_undoable, for_ai_action, log_action, note_undoable
from app.yandex import YandexError


def test_decline_text_joins_chip_and_comment():
    assert ai_agent.decline_text("Слишком резко", "  не больше   10 % ") == "Слишком резко: не больше 10 %"
    assert ai_agent.decline_text("Не то") == "Не то"
    assert ai_agent.decline_text("", "Только РСЯ") == "Только РСЯ"
    assert len(ai_agent.decline_text("", "х" * 5000)) == ai_agent.DECLINE_TEXT_LIMIT
    assert ai_agent.NO_TOUCH_REASON in ai_agent.DECLINE_REASONS
    for reason, comment in (("Надоело", ""), ("", "   ")):
        with pytest.raises(YandexError) as err:
            ai_agent.decline_text(reason, comment)
        assert err.value.code == "args"


def test_decline_reply_tells_model_why():
    assert json.loads(ai_agent._decline_reply("")) == {"ok": False, "error": "Пользователь отклонил действие"}
    raw = ai_agent._decline_reply("Слишком резко: мягче")
    reply = json.loads(raw)
    assert reply["error"] == "Пользователь отклонил действие: Слишком резко: мягче"
    assert reply["reason"] == "Слишком резко: мягче" and "переделай" in reply["note"]
    # Кириллица — как есть: модели короче и понятнее, чем \u-последовательности.
    assert "Слишком резко" in raw
    assert "запомнил" not in reply["note"]  # «запомнил» — только когда правда записано

    # «Не трогай эту кампанию»: записано — назвать кампании и сказать, что запомнил; не записано — спросить.
    saved = json.loads(ai_agent._decline_reply(ai_agent.NO_TOUCH_REASON, ["«РСЯ»"]))["note"]
    assert "«РСЯ»" in saved and "скажи, что запомнил" in saved
    lost = json.loads(ai_agent._decline_reply(ai_agent.NO_TOUCH_REASON, []))["note"]
    assert "Не говори, что запомнил" in lost and "скажи, что запомнил" not in lost


def test_touch_ids_walks_nested_args():
    found: dict = {}
    args = {
        "items": [{"ad_row_id": 5, "extras_from_ad_row_id": 9}, {"ad_row_id": "6"}],
        "variants": [{"groups": [{"adgroup_row_ids": [7, True, "x"]}]}],
        "experiment_id": 3,
        "source_campaign_row_id": 2,
        "_diff": [{"campaign_row_id": 99}],
    }
    ai_agent._touch_ids(args, found)
    assert found == {"ad": [5, 6], "adgroup": [7], "experiment": [3], "source": [2]}


@pytest.mark.parametrize(
    "payload, hit",
    [
        ({"campaign_row_ids": [5]}, True),
        ({"campaign_row_ids": [1, 5], "action": "delete"}, True),
        ({"keyword_row_ids": [7], "campaign_row_ids": [3, 5, 8]}, True),
        ({"campaign_row_id": 5}, True),
        ({"campaign_ids": [5]}, True),
        ({"campaign_row_ids": [15, 2]}, False),
        ({"campaign_row_ids": [51]}, False),
        ({"source_campaign_row_id": 5}, False),
        ({"keyword_row_ids": [5]}, False),
    ],
)
def test_campaign_in_payload_regex(payload, hit):
    """Тот же шаблон, что ищет Postgres (~) в тексте параметров — json.dumps с обычными разделителями."""
    import re

    assert bool(re.search(journal._payload_campaign_re(5), json.dumps(payload, ensure_ascii=False))) is hit


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    def execute(self, sql, params=None):
        self.calls.append(params)
        return self

    def fetchone(self):
        return {"id": 40 + len(self.calls)}


class _Pool:
    def __init__(self):
        self.calls: list = []

    @contextmanager
    def connection(self):
        yield _Conn(self.calls)


def test_log_action_marks_ai_action(monkeypatch):
    fake = _Pool()
    monkeypatch.setattr(journal, "pool", fake)
    steps = {"steps": [{"op": "campaigns_action", "args": {}}]}
    with acting_as("ai"), collect_undoable() as ids:
        with for_ai_action(7):
            marked = log_action(1, "pause", {"campaign_row_ids": [3, 2]}, {}, "ok", workspace_id=5, undo=steps)
        plain = log_action(1, "pause", {"campaign_row_ids": [2]}, {}, "ok", workspace_id=5)
        with for_ai_action(None):
            log_action(1, "pause", {}, {}, "ok", workspace_id=5, targets=["keyword:9"])
    # targets — 10-й параметр INSERT; метка предложения — обычная цель, без новой колонки.
    assert [c[9] for c in fake.calls] == [["ai_action:7", "campaign:2", "campaign:3"], ["campaign:2"], ["keyword:9"]]
    assert ids == [marked] and plain != marked


def _entry(i, *, steps=1, status="ok", undone=False):
    return {
        "id": i,
        "title": f"Запись {i}",
        "action": "pause",
        "status": status,
        "undo": {"steps": [{"op": "campaigns_action", "args": {}}] * steps} if steps else None,
        "undone_at": "2026-11-01" if undone else None,
    }


def test_group_undo_runs_newest_first_and_isolates_failures(monkeypatch):
    rows = [_entry(1), _entry(2, steps=0), _entry(3, steps=2), _entry(4, undone=True), _entry(5), _entry(6, status="error")]
    monkeypatch.setattr(undo, "_load_many", lambda user_id, ids: rows)
    seen, progress = [], []

    def fake_run(user_id, log_id, sub):
        seen.append(log_id)
        sub(1, 1, "шаг")
        if log_id == 3:
            raise YandexError("api", "Директ не ответил")
        return {"log_id": 100 + log_id, "steps": [{"op": "campaigns_action", "text": "шаг", "status": "ok"}]}

    monkeypatch.setattr(undo, "run_undo", fake_run)
    out = undo.run_undo_group(1, [1, 2, 3, 4, 5, 6], lambda n, total, text="": progress.append((n, total)))
    # Без отката, уже возвращённое и невыполненное пропускаются; сбой одной записи не останавливает другие.
    assert seen == [5, 3, 1]
    assert out["status"] == "partial" and out["undone"] == [5, 1] and out["log_ids"] == [105, 101]
    assert out["failed"] == [{"log_id": 3, "title": "Запись 3", "code": "api", "message": "Директ не ответил"}]
    assert progress == [(1, 4), (2, 4), (4, 4), (4, 4)]


def test_group_undo_fails_only_when_nothing_returned(monkeypatch):
    def boom(user_id, log_id, sub):
        if log_id == 2:
            raise RuntimeError("сеть")
        raise YandexError("api", "Не вышло")

    monkeypatch.setattr(undo, "run_undo", boom)
    monkeypatch.setattr(undo, "_load_many", lambda user_id, ids: [_entry(1), _entry(2)])
    with pytest.raises(YandexError) as err:
        undo.run_undo_group(1, [1, 2])
    assert (err.value.code, err.value.message) == ("internal", "Сбой при откате")  # первая — самая новая

    monkeypatch.setattr(undo, "_load_many", lambda user_id, ids: [_entry(1, undone=True), _entry(2, steps=0)])
    with pytest.raises(YandexError) as err:
        undo.run_undo_group(1, [1, 2])
    assert err.value.code == "undo"


def test_group_needs_ids():
    with pytest.raises(YandexError) as err:
        undo._load_many(1, [])
    assert err.value.code == "args"


def test_undo_job_routes_group(monkeypatch):
    calls = []
    monkeypatch.setattr(undo, "run_undo_group", lambda user_id, ids, progress: calls.append(("group", ids)) or {})
    monkeypatch.setattr(undo, "run_undo", lambda user_id, log_id, progress: calls.append(("one", log_id)) or {})
    job_handlers._undo_action(1, {"log_ids": [3, 4]}, lambda *a: None)
    job_handlers._undo_action(1, {"log_id": "5"}, lambda *a: None)
    assert calls == [("group", [3, 4]), ("one", 5)]


@pytest.mark.parametrize(
    "made, extra",
    [
        (1, {"undo_log_id": 10}),
        (3, {"undo_log_ids": [10, 11, 12]}),
        (0, {}),
        (undo.MAX_GROUP + 1, {}),  # больше, чем откатит одно окно, — «Отменить» не предлагаем
    ],
)
def test_job_offers_undo_for_every_undoable(monkeypatch, made, extra):
    out = {}

    def handler(user_id, payload, progress):
        for i in range(made):
            note_undoable(10 + i)
        return {"ok": True}

    monkeypatch.setitem(job_handlers.HANDLERS, "test_undoable", handler)
    monkeypatch.setattr(job_handlers, "_heartbeat_loop", lambda *a: None)
    monkeypatch.setattr(job_handlers, "complete", lambda job_id, worker_id, result: out.update(result=result))
    job = {"id": 1, "kind": "test_undoable", "user_id": 1, "payload": {}}
    assert job_handlers.run_job(job, "w1") == "done"
    assert out["result"] == {"ok": True, **extra}
