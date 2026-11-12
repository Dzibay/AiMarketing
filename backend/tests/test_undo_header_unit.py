"""«Отменить» в сообщении о результате: номер записи с откатом — в заголовке ответа или в результате задачи."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import job_handlers
from app.api.undo_header import UndoLogHeader
from app.journal import collect_undoable, note_undoable, single_undoable


def _client(ids: list[int]) -> TestClient:
    app = FastAPI()
    app.add_middleware(UndoLogHeader)

    @app.post("/sync")
    def sync_action():  # синхронные ручки FastAPI выполняет в пуле потоков
        for log_id in ids:
            note_undoable(log_id)
        return {"ok": True}

    @app.post("/async")
    async def async_action():
        for log_id in ids:
            note_undoable(log_id)
        return {"ok": True}

    @app.get("/read")
    def read():
        for log_id in ids:
            note_undoable(log_id)
        return {"ok": True}

    return TestClient(app)


def test_single_undoable_entry_goes_to_header():
    client = _client([42])
    for path in ("/sync", "/async"):
        res = client.post(path)
        assert res.status_code == 200
        assert res.headers["x-undo-log"] == "42"
        assert res.json() == {"ok": True}


def test_no_header_without_exactly_one_entry():
    assert "x-undo-log" not in _client([]).post("/sync").headers
    # Два действия с откатом за запрос — одной кнопкой их не отменить, остаётся «История».
    assert "x-undo-log" not in _client([1, 2]).post("/sync").headers


def test_reads_never_get_header():
    assert "x-undo-log" not in _client([7]).get("/read").headers


def test_note_outside_collection_is_ignored():
    note_undoable(5)  # вне запроса и задачи — некуда собирать, без ошибки
    with collect_undoable() as ids:
        note_undoable(9)
    assert ids == [9]
    assert single_undoable([]) is None
    assert single_undoable([3, 4]) is None


def _run(monkeypatch, handler) -> dict:
    saved = {}
    monkeypatch.setitem(job_handlers.HANDLERS, "unit_undo", handler)
    monkeypatch.setattr(job_handlers, "_heartbeat_loop", lambda *a: None)
    monkeypatch.setattr(job_handlers, "complete", lambda job_id, worker, result: saved.update(result=result))
    status = job_handlers.run_job({"id": 1, "kind": "unit_undo", "user_id": 1, "payload": {}}, "w")
    assert status == "done"
    return saved["result"]


def test_job_result_carries_single_undo_entry(monkeypatch):
    def one(user_id, payload, progress):
        note_undoable(77)
        return {"applied": 3}

    assert _run(monkeypatch, one) == {"applied": 3, "undo_log_id": 77}


def test_job_result_untouched_without_single_entry(monkeypatch):
    def two(user_id, payload, progress):
        note_undoable(1)
        note_undoable(2)
        return {"applied": 3}

    def none(user_id, payload, progress):
        return {"applied": 0}

    assert _run(monkeypatch, two) == {"applied": 3}
    assert _run(monkeypatch, none) == {"applied": 0}
