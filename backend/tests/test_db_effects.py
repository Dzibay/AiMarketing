"""Помогла ли правка: расчёт окон 7 и 14 дней на настоящем Postgres, колокольчик, история и общая главная."""

from datetime import datetime, time, timedelta

import pytest

from app import effects, telegram_notify
from app import notifications as n
from app.clock import MSK, today_msk
from app.job_handlers import run_job, submit
from app.jobs import claim
from app.journal import list_action_logs, log_action
from app.stats import upsert_direct_stat
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

WORKER = "test-host:1:0"


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(telegram_notify, "send_message", lambda chat, text: out.append((chat, text)) or True)
    return out


@pytest.fixture
def setup(db):
    user = make_user(db)
    conn_id = make_connection(db, user)
    acc = make_account(db, user, conn_id)
    camp = make_campaign(db, user, acc, 555, "Окна ПВХ")
    ws = make_workspace(db, user, 1, "Окна", accounts=[acc], campaigns=[camp])
    with db.connection() as conn:
        conn.execute("UPDATE direct_accounts SET stats_synced_at = NOW() WHERE id = %s", (acc,))
    return {"user": user, "acc": acc, "camp": camp, "ws": ws}


def _change(db, s, days_ago: int, action="set_budget", undo=True) -> int:
    log_id = log_action(
        s["user"],
        action,
        {"campaign_row_ids": [s["camp"]]},
        {"ok": True},
        "ok",
        undo={"label": "Вернуть бюджет", "steps": [{"op": "campaign_budgets", "args": {}}]} if undo else None,
    )
    at = datetime.combine(today_msk() - timedelta(days=days_ago), time(12), MSK)
    with db.connection() as conn:
        conn.execute("UPDATE action_logs SET created_at = %s WHERE id = %s", (at, log_id))
    return log_id


def _stats(s, day, cost_rub, conv, clicks=30):
    upsert_direct_stat(
        user_id=s["user"],
        day=day,
        account_id=s["acc"],
        campaign_id=555,
        impressions=1000,
        clicks=clicks,
        cost_kopecks=int(cost_rub * 100),
        conversions=conv,
    )


def _fill(s, applied_ago: int, before=(1000, 2), after=(1000, 2)):
    """По дню: до правки — before (расход ₽, заявки), после — after; день правки — огромный расход (не считается)."""
    applied = today_msk() - timedelta(days=applied_ago)
    for d in range(1, 15):
        _stats(s, applied - timedelta(days=d), *before)
        if applied + timedelta(days=d) < today_msk():
            _stats(s, applied + timedelta(days=d), *after)
    _stats(s, applied, 999_999, 0)


def test_worse_change_gets_both_windows_one_notification_and_history_line(db, setup, sent):
    s = setup
    log_id = _change(db, s, 20)
    _fill(s, 20, before=(1000, 2), after=(1500, 2))  # цена заявки 500 → 750 ₽: +50 %

    res = effects.compute_for_user(s["user"])
    assert res == {"computed": 2, "notified": 1}
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM action_effects WHERE log_id = %s ORDER BY window_days", (log_id,)
        ).fetchall()
    assert [r["window_days"] for r in rows] == [7, 14]
    week = rows[0]
    assert week["before_cost_kopecks"] == 700_000 and week["after_cost_kopecks"] == 1_050_000
    assert week["before_conversions"] == 14 and week["after_conversions"] == 14
    assert week["verdict"] == "worse" and week["metric"] == "cpa" and round(week["change_pct"]) == 50
    assert week["workspace_id"] == s["ws"] and week["campaign_row_ids"] == [s["camp"]]

    [note] = n.list_notifications(s["user"])["items"]
    assert note["kind"] == "effect" and note["title"] == "После правки в «Окна» стало хуже"
    assert note["link"] == f"/w/{s['ws']}/history?log={log_id}"
    assert note["undo"] == {"log_id": log_id, "label": "Вернуть как было"}
    assert note["body"].replace(" ", " ").startswith("Цена заявки +50 % за 7 дней")

    [item] = list_action_logs(s["user"], workspace_id=s["ws"])
    assert item["effect"]["window_days"] == 14 and item["effect"]["tone"] == "warn"
    assert item["effect"]["text"].replace(" ", " ") == "Цена заявки +50 % за 14 дней"

    # Повторный расчёт ничего не добавляет и не шлёт второй раз.
    assert effects.compute_for_user(s["user"]) == {"computed": 0, "notified": 0}
    n.mark_read(s["user"], everything=True)
    with db.connection() as conn:
        conn.execute("DELETE FROM action_effects WHERE window_days = 14")
    assert effects.compute_for_user(s["user"]) == {"computed": 1, "notified": 0}
    assert len(n.list_notifications(s["user"])["items"]) == 1

    summary = effects.week_summary(s["user"])
    assert summary == {"text": "За неделю после правок стало хуже у 1 проекта", "better": 0, "worse": 1}


def test_undone_change_drops_button_and_week_summary(db, setup, sent):
    s = setup
    log_id = _change(db, s, 20)
    _fill(s, 20, before=(1000, 2), after=(1500, 2))
    effects.compute_for_user(s["user"])
    assert effects.week_summary(s["user"])["worse"] == 1
    with db.connection() as conn:
        conn.execute("UPDATE action_logs SET undone_at = NOW() WHERE id = %s", (log_id,))
    [note] = n.list_notifications(s["user"])["items"]
    assert note["undo"] is None
    assert effects.week_summary(s["user"]) is None


def test_one_failing_row_does_not_block_others(db, setup, sent, monkeypatch):
    s = setup
    bad = _change(db, s, 20)
    good = _change(db, s, 19)
    _fill(s, 20, before=(1000, 2), after=(1500, 2))
    real = effects.judge

    def judge(before, after, **kw):
        if judge.calls == 0:
            judge.calls += 1
            raise TypeError("boom")
        return real(before, after, **kw)

    judge.calls = 0
    monkeypatch.setattr(effects, "judge", judge)
    effects.compute_for_user(s["user"])
    with db.connection() as conn:
        ids = {r["log_id"] for r in conn.execute("SELECT log_id FROM action_effects").fetchall()}
    assert bad not in ids and good in ids


def test_only_finished_and_loaded_windows(db, setup, sent):
    s = setup
    _change(db, s, 10)  # окно 7 дней кончилось, 14 — ещё нет
    _fill(s, 10, before=(1000, 2), after=(700, 2))
    assert effects.compute_for_user(s["user"]) == {"computed": 1, "notified": 0}
    with db.connection() as conn:
        [row] = conn.execute("SELECT window_days, verdict FROM action_effects").fetchall()
        assert row == {"window_days": 7, "verdict": "better"}
        # Статистика кабинета загружена давно — свежую правку не считаем.
        conn.execute(
            "UPDATE direct_accounts SET stats_synced_at = NOW() - INTERVAL '30 days' WHERE id = %s", (s["acc"],)
        )
    fresh = _change(db, s, 9)
    effects.compute_for_user(s["user"])
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM action_effects WHERE log_id = %s", (fresh,)).fetchone()["n"] == 0
    assert effects.week_summary(s["user"])["text"] == "За неделю правки помогли у 1 проекта"
    assert n.list_notifications(s["user"])["items"] == []


def test_reads_undone_and_other_changes(db, setup, sent):
    s = setup
    _fill(s, 20, before=(1000, 0, ), after=(1000, 0))
    created = _change(db, s, 20, action="duplicate")  # копия — не правка
    undone = _change(db, s, 20)
    with db.connection() as conn:
        conn.execute(
            "UPDATE action_logs SET undone_at = %s WHERE id = %s",
            (datetime.combine(today_msk() - timedelta(days=17), time(12), MSK), undone),
        )
    main = _change(db, s, 20, action="pause", undo=False)
    other = _change(db, s, 18, action="bid_adjust", undo=False)
    effects.compute_for_user(s["user"])
    with db.connection() as conn:
        rows = conn.execute("SELECT log_id, window_days, verdict, others FROM action_effects ORDER BY log_id, window_days").fetchall()
    ids = {r["log_id"] for r in rows}
    assert created not in ids and undone not in ids
    mine = [r for r in rows if r["log_id"] == main]
    # Заявок нет ни до, ни после — мало данных; в те же дни кампанию правили ещё дважды (и откаченная правка тоже была).
    assert [(r["window_days"], r["verdict"], r["others"]) for r in mine] == [(7, "low_data", 2), (14, "low_data", 2)]
    assert other in ids
    assert effects.week_summary(s["user"]) is None


def test_scheduled_job_runs_compute(db, setup, sent):
    s = setup
    _change(db, s, 20)
    _fill(s, 20, before=(1000, 2), after=(1500, 2))
    assert effects.users_to_check() == [s["user"]]
    submit("compute_effects", s["user"])
    assert run_job(claim(WORKER), WORKER) == "done"
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM action_effects").fetchone()["n"] == 2
    assert effects.users_to_check() == []
