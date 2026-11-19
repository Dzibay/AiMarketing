"""Скрытие сигналов («⋯»): ключи, область «такого типа», правила «стало хуже», деление и строки «Скрыто»."""

from datetime import datetime, timedelta, timezone

from app import snoozes

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc)
WS_CLIENT = {1: 10, 2: 10, 3: None}


def _p(kind="balance", severity="warning", **extra):
    base = {
        "kind": kind,
        "severity": severity,
        "title": "Баланса хватит на 3 дн.",
        "workspace_id": 1,
        "workspace_ids": [1],
        "workspace_name": "Окна",
        "client_name": "Иванов",
        "object_name": "",
        "account_id": None,
        "campaign_row_id": None,
    }
    base.update(extra)
    return base


def _s(id_, mode, problem_key="", kind="balance", scope="", snapshot=None, until=None, created=NOW):
    return {
        "id": id_,
        "mode": mode,
        "problem_key": problem_key,
        "kind": kind,
        "scope": scope,
        "title": "",
        "snapshot": snapshot or {},
        "until": until,
        "created_at": created,
    }


def test_problem_key_prefers_narrowest_object():
    assert snoozes.problem_key(_p(campaign_row_id=5, account_id=7)) == "balance:c5"
    assert snoozes.problem_key(_p(account_id=7)) == "balance:a7"
    assert snoozes.problem_key(_p(kind="budget", level="client", client_id=10, workspace_ids=[1, 2])) == "budget:k10"
    assert snoozes.problem_key(_p(kind="stats_error", object_name="Кабинет")) == "stats_error:oКабинет"
    assert snoozes.problem_key(_p(kind="kpi", workspace_ids=[1, None])) == "kpi:w1,-"


def test_problem_key_ignores_title_and_numbers():
    a = _p(account_id=7, title="Баланса хватит на 3 дн.", metric={"days": 3})
    b = _p(account_id=7, title="Баланса хватит на 1,5 дн.", metric={"days": 1.5})
    assert snoozes.problem_key(a) == snoozes.problem_key(b)


def test_type_scope_client_folder_or_project():
    assert snoozes.type_scope(_p(workspace_ids=[1, 2]), WS_CLIENT) == "client:10"
    assert snoozes.type_scope(_p(workspace_ids=[3], workspace_id=3), WS_CLIENT) == "ws:3"
    assert snoozes.type_scope(_p(workspace_ids=[1, 3]), WS_CLIENT) is None  # разные клиенты
    assert snoozes.type_scope(_p(workspace_ids=[None], workspace_id=None), WS_CLIENT) is None  # «Без проекта»
    assert snoozes.type_scope(_p(level="client", client_id=10, workspace_ids=[1, 2]), WS_CLIENT) == "client:10"


def test_is_worse_rules():
    snap = {"severity": "warning", "metric": {"balance": 300000, "days": 4, "reasons": ["Мелкий шрифт"], "count": 2}}
    same = _p(metric={"balance": 250000, "days": 3, "reasons": ["Мелкий шрифт"], "count": 2})
    assert not snoozes.is_worse(snap, same)
    assert snoozes.is_worse(snap, _p(severity="critical", metric={"balance": 250000, "days": 3}))
    assert snoozes.is_worse(snap, _p(metric={"balance": 0, "days": 0}))  # деньги кончились
    assert snoozes.is_worse(snap, _p(metric={"balance": 150000, "days": 2}))  # дней вдвое меньше
    assert snoozes.is_worse(snap, _p(metric={"reasons": ["Мелкий шрифт", "Нет лицензии"]}))  # новая причина
    assert snoozes.is_worse(snap, _p(metric={"count": 3}))  # больше отклонённых
    assert not snoozes.is_worse(snap, _p(metric={"reasons": ["Мелкий шрифт", ""]}))
    assert not snoozes.is_worse({}, _p(severity="info"))


def test_split_days_intended_and_type():
    money = _p(account_id=7, metric={"balance": 300000, "days": 4})
    stopped = _p(kind="stopped", campaign_row_id=5, workspace_ids=[2], workspace_id=2)
    kpi = _p(kind="kpi")
    snaps = [
        _s(1, "days", "balance:a7", snapshot=snoozes.snapshot_of(money), until=NOW + timedelta(days=7)),
        _s(2, "type", kind="stopped", scope="client:10"),
    ]
    out = snoozes.split([money, stopped, kpi], snaps, WS_CLIENT, NOW)
    assert out["visible"] == [kpi]
    assert out["hidden"] == {1: [money], 2: [stopped]}
    assert out["broken"] == set() and out["seen"] == {1, 2}


def test_split_unhides_expired_and_worse():
    money = _p(account_id=7, metric={"balance": 0, "days": 0}, severity="critical")
    snaps = [
        _s(1, "intended", "balance:a7", snapshot={"severity": "warning", "metric": {"balance": 300000, "days": 4}}),
        _s(2, "days", "kpi:w1", until=NOW - timedelta(minutes=1)),
    ]
    out = snoozes.split([money, _p(kind="kpi")], snaps, WS_CLIENT, NOW)
    assert len(out["visible"]) == 2
    assert out["broken"] == {1, 2}


def test_type_snooze_shows_more_serious_but_stays():
    """Скрытие типа прячет только то, что не серьёзнее скрытого; само скрытие при этом не снимается."""
    critical = _p(kind="stopped", campaign_row_id=5, severity="critical")
    legacy = _s(1, "type", kind="stopped", scope="client:10")  # старая запись без снимка — как «внимание»
    out = snoozes.split([critical], [legacy], WS_CLIENT, NOW)
    assert out["visible"] == [critical] and out["broken"] == set() and out["seen"] == set()

    pending = _p(kind="moderation", campaign_row_id=5, severity="info")
    rejected = _p(kind="moderation", campaign_row_id=6, severity="critical")
    info_type = _s(2, "type", kind="moderation", scope="client:10", snapshot={"severity": "info"})
    out = snoozes.split([pending, rejected], [info_type], WS_CLIENT, NOW)
    assert out["visible"] == [rejected] and out["hidden"] == {2: [pending]} and out["broken"] == set()


def test_type_snooze_returns_when_money_runs_out():
    low = _p(account_id=7, metric={"balance": 400000, "days": 4})
    out_ = _p(account_id=7, metric={"balance": 0, "days": 0}, severity="critical")
    snap = snoozes.type_snapshot(low)
    assert snap == {"severity": "warning", "money_out": False}
    hide = [_s(1, "type", kind="balance", scope="client:10", snapshot=snap)]
    assert snoozes.split([low], hide, WS_CLIENT, NOW)["visible"] == []
    assert snoozes.split([out_], hide, WS_CLIENT, NOW)["visible"] == [out_]
    # Деньги кончились, а серьёзность та же (так бывает у снимка «критично» на 1 дне) — всё равно видно.
    one_day = {"severity": "critical", "money_out": False}
    assert snoozes.type_worse(one_day, out_)
    assert not snoozes.type_worse(one_day, _p(account_id=7, metric={"balance": 100000, "days": 1}, severity="critical"))


def test_type_snapshot_only_widens():
    assert snoozes.type_snapshot(_p(kind="stopped")) == {"severity": "warning"}
    assert snoozes.type_snapshot(_p(kind="moderation", severity="info")) == {"severity": "info"}
    out_ = _p(metric={"balance": 0}, severity="critical")
    assert snoozes.type_snapshot(out_, {"severity": "warning", "money_out": False}) == {"severity": "critical", "money_out": True}
    # Повторное скрытие более мягкой проблемы не сужает уже скрытое.
    assert snoozes.type_snapshot(_p(metric={"balance": 400000}), {"severity": "critical", "money_out": True}) == {
        "severity": "critical",
        "money_out": True,
    }


def test_is_hidden_checks_severity_like_overview():
    info_type = [_s(1, "type", kind="moderation", scope="ws:3", snapshot={"severity": "info"})]
    rejected = _p(kind="moderation", campaign_row_id=9, workspace_ids=[3], workspace_id=3, severity="critical")
    assert not snoozes.is_hidden(rejected, info_type, WS_CLIENT, NOW)
    assert snoozes.is_hidden({**rejected, "severity": "info"}, info_type, WS_CLIENT, NOW)
    # «Так задумано» на «внимании» не прячет то же самое, ставшее критичным.
    intended = [_s(2, "intended", "moderation:c9", kind="moderation", snapshot={"severity": "warning", "metric": {}})]
    assert not snoozes.is_hidden(rejected, intended, WS_CLIENT, NOW)
    assert snoozes.is_hidden({**rejected, "severity": "warning"}, intended, WS_CLIENT, NOW)


def test_is_hidden_for_urgent_events():
    snap = snoozes.snapshot_of(_p(kind="no_conversions", campaign_row_id=5))
    snaps = [
        _s(1, "days", "no_conversions:c5", snapshot=snap, until=NOW + timedelta(days=1)),
        _s(2, "type", kind="cpa_spike", scope="ws:3"),
    ]
    assert snoozes.is_hidden(_p(kind="no_conversions", campaign_row_id=5), snaps, WS_CLIENT, NOW)
    assert not snoozes.is_hidden(_p(kind="no_conversions", campaign_row_id=6), snaps, WS_CLIENT, NOW)
    assert snoozes.is_hidden(_p(kind="cpa_spike", campaign_row_id=9, workspace_ids=[3]), snaps, WS_CLIENT, NOW)
    assert not snoozes.is_hidden(
        _p(kind="no_conversions", campaign_row_id=5), [_s(1, "days", "no_conversions:c5", until=NOW)], WS_CLIENT, NOW
    )


def test_hidden_rows_show_matched_problems_and_types():
    money = _p(account_id=7, object_name="Кабинет")
    snaps = [
        _s(1, "days", "balance:a7", until=datetime(2026, 11, 25, 22, 30, tzinfo=timezone.utc)),
        _s(2, "intended", "kpi:w1"),  # проблемы сейчас нет — строки нет
        _s(3, "type", kind="stopped", scope="client:10"),
    ]
    rows = snoozes.hidden_rows(snaps, {1: [money]}, WS_CLIENT, {1: "Окна", 2: "Двери"}, {10: "Иванов"}, NOW)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {1, 3}
    assert by_id[1]["title"] == money["title"]
    assert by_id[1]["place"] == "Окна · Кабинет"
    assert by_id[1]["note"] == "Скрыто до 26.11"  # по Москве уже следующий день
    assert by_id[1]["severity"] == "warning" and by_id[1]["workspace_ids"] == [1]
    assert by_id[3]["title"].startswith("Все сигналы «")
    assert by_id[3]["place"] == "Клиент «Иванов»"
    assert sorted(by_id[3]["workspace_ids"]) == [1, 2]


def test_scope_info_labels():
    assert snoozes.scope_info("client:10", {}, {10: "Иванов"}) == {"key": "client:10", "level": "client", "name": "Иванов"}
    assert snoozes.scope_info("ws:3", {3: "Сайт"}, {}) == {"key": "ws:3", "level": "project", "name": "Сайт"}
    assert snoozes.scope_info(None, {}, {}) is None
