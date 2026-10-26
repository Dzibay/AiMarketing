"""Откат из журнала на настоящем Postgres; шаги отката (вызовы Директа) подменяются."""

import pytest

from app import undo
from app.journal import acting_as, list_action_logs, log_action
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user

@pytest.fixture
def env(db):
    user = make_user(db, "u@example.com")
    acc = make_account(db, user, make_connection(db, user))
    camp = make_campaign(db, user, acc, 101, "Кухни")
    return {"db": db, "user": user, "camp": camp, "step": {"op": "campaign_budgets", "args": {"budgets": {str(camp): 50000}}}}


def _log(env, *, status="ok", steps=None, action="set_budget", **kw):
    steps = [env["step"]] if steps is None else steps
    return log_action(
        env["user"],
        action,
        {"campaign_row_ids": [env["camp"]]},
        {},
        status,
        undo={"label": "Вернуть бюджет", "steps": steps},
        **kw,
    )


def _row(env, log_id):
    with env["db"].connection() as c:
        return c.execute("SELECT * FROM action_logs WHERE id = %s", (log_id,)).fetchone()


def test_log_action_targets_and_empty_undo(env):
    log_id = _log(env)
    row = _row(env, log_id)
    assert row["targets"] == [f"campaign:{env['camp']}"] and row["undo"]["steps"] == [env["step"]]
    assert _row(env, _log(env, steps=()))["undo"] is None
    item = list_action_logs(env["user"])[-1]
    assert item["undo"] == {"label": "Вернуть бюджет", "danger": False}


def test_preview_reasons_and_later_changes(env):
    log_id = _log(env)
    failed = _log(env, status="error")
    no_undo = _log(env, steps=())
    assert undo.undo_preview(env["user"], failed)["reason"] == "Действие не выполнилось — откатывать нечего"
    assert undo.undo_preview(env["user"], no_undo)["reason"] == "У этого действия нет отката"
    with pytest.raises(YandexError) as err:
        undo.undo_preview(make_user(env["db"], "other@example.com"), log_id)
    assert err.value.code == "not-found"

    # Та же кампания позже поменялась (не откаты и не ошибки) — предупреждаем.
    with acting_as("rule"):
        later = log_action(env["user"], "pause", {"campaign_row_ids": [env["camp"]]}, {}, "ok")
    preview = undo.undo_preview(env["user"], log_id)
    assert preview["available"] and [l["id"] for l in preview["later"]] == [no_undo, later]
    assert preview["later"][1]["source"] == "rule"
    assert any("меняли ещё раз" in w for w in preview["warnings"])
    assert preview["steps"] == ["Вернуть дневной бюджет «Кухни»: 500 ₽"]


def test_preview_warns_about_recreate(env):
    log_id = _log(env, steps=[{"op": "keywords_recreate", "args": {"items": [{"keyword": "кухни", "adgroup_row_id": 1}]}}])
    preview = undo.undo_preview(env["user"], log_id)
    assert preview["steps"] == ["Добавить фразы заново: «кухни»"]
    assert any("без прежней статистики" in w for w in preview["warnings"])


def test_run_once_and_journal_quiet(env, monkeypatch):
    log_id = _log(env)
    seen = []

    def fake(user_id, args):
        seen.append(args)
        # Вложенная запись в журнал во время отката не появляется.
        assert log_action(user_id, "set_budget", {"campaign_row_ids": [env["camp"]]}, {}, "ok") is None
        return {"applied": 1}

    monkeypatch.setitem(undo.OPS, "campaign_budgets", fake)
    out = undo.run_undo(env["user"], log_id)
    assert out["status"] == "ok" and seen == [env["step"]["args"]]
    row = _row(env, log_id)
    assert row["undone_at"] and row["undone_by"] == out["log_id"]
    new = _row(env, out["log_id"])
    assert new["action"] == "undo" and new["undo_of"] == log_id and new["title"] == "Откат: Изменение дневного бюджета"
    assert new["undo"] is None
    assert len(list_action_logs(env["user"])) == 2

    with pytest.raises(YandexError) as err:
        undo.run_undo(env["user"], log_id)
    assert err.value.code == "undo"
    assert len(seen) == 1


def test_failed_undo_can_be_retried(env, monkeypatch):
    log_id = _log(env)

    def broken(user_id, args):
        raise YandexError("direct", "Кампания на модерации")

    monkeypatch.setitem(undo.OPS, "campaign_budgets", broken)
    with pytest.raises(YandexError) as err:
        undo.run_undo(env["user"], log_id)
    assert err.value.message == "Кампания на модерации"
    assert _row(env, log_id)["undone_at"] is None
    failed = [r for r in list_action_logs(env["user"]) if r["undo_of"] == log_id]
    assert [r["status"] for r in failed] == ["error"]

    monkeypatch.setitem(undo.OPS, "campaign_budgets", lambda user_id, args: {"applied": 1})
    assert undo.run_undo(env["user"], log_id)["status"] == "ok"


def test_crash_releases_claim(env, monkeypatch):
    log_id = _log(env)

    def crash(user_id, args):
        raise RuntimeError("сеть упала")

    monkeypatch.setitem(undo.OPS, "campaign_budgets", crash)
    with pytest.raises(RuntimeError):
        undo.run_undo(env["user"], log_id)
    assert _row(env, log_id)["undone_at"] is None
    assert undo.undo_preview(env["user"], log_id)["available"]


def test_partial_undo_and_unknown_step(env, monkeypatch):
    log_id = _log(env, steps=[env["step"], {"op": "nope", "args": {}}])
    monkeypatch.setitem(undo.OPS, "campaign_budgets", lambda user_id, args: {"applied": 1})
    out = undo.run_undo(env["user"], log_id)
    assert out["status"] == "partial"
    assert [s["status"] for s in out["steps"]] == ["ok", "error"]
    assert out["steps"][1]["message"] == "Неизвестный шаг"
    assert _row(env, log_id)["undone_by"] == out["log_id"]


def test_existing_skips_deleted_objects(env):
    assert undo._existing(env["user"], "direct_campaigns", [env["camp"], 999999]) == [env["camp"]]
    assert undo._existing(env["user"], "direct_campaigns", []) == []
