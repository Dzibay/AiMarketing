"""Загрузка кабинета без сети и БД: расписание этапов, Changes API, отчёты, разбор строк."""

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import account_load, direct_api, direct_reports, metrika_sync
from app.account_load import (
    HISTORY_DAYS,
    REFRESH_STATS_DAYS,
    STRUCTURE_EVERY,
    _AccountLoad,
    account_load_out,
    is_due,
    parse_changed_campaigns,
    stats_days,
)
from app.metrika_sync import parse_daily_rows, parse_goal_rows
from app.search_queries import aggregate_query_rows
from app.stats import parse_campaign_day_rows
from app.yandex import YandexError

_RealClient = httpx.Client


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


@pytest.fixture
def respond(monkeypatch):
    """respond(handler): все httpx.Client в тесте отвечают через handler(request) -> Response."""
    requests: list[httpx.Request] = []

    def install(handler):
        def recording(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(recording), **kw)
        )
        return requests

    return install


# --- расписание этапов ---


def test_is_due_unfinished_stage_always_runs():
    assert is_due(None, STRUCTURE_EVERY, initial=True)
    assert is_due({"status": "error", "at": _ago(minutes=1)}, STRUCTURE_EVERY, initial=False)
    assert is_due({"status": "running"}, STRUCTURE_EVERY, initial=True)


def test_is_due_finished_stage_waits_for_its_interval():
    assert not is_due({"status": "ok", "at": _ago(days=5)}, STRUCTURE_EVERY, initial=True)
    assert not is_due({"status": "ok", "at": _ago(hours=2)}, STRUCTURE_EVERY, initial=False)
    assert is_due({"status": "ok", "at": _ago(hours=25)}, STRUCTURE_EVERY, initial=False)
    assert is_due({"status": "ok"}, STRUCTURE_EVERY, initial=False)
    assert is_due({"status": "ok", "at": "мусор"}, STRUCTURE_EVERY, initial=False)


def test_stats_days_full_history_on_first_load_and_after_gap():
    assert stats_days(None, initial=True) == HISTORY_DAYS
    assert stats_days({"status": "ok", "at": _ago(hours=6)}, initial=False) == REFRESH_STATS_DAYS
    assert stats_days({"status": "ok", "at": _ago(days=10)}, initial=False) == HISTORY_DAYS
    assert stats_days({"status": "error", "at": _ago(hours=1)}, initial=False) == HISTORY_DAYS


def test_parse_changed_campaigns_ignores_stat_only_changes():
    result = {
        "Campaigns": [
            {"CampaignId": 1, "ChangesIn": ["SELF"]},
            {"CampaignId": 2, "ChangesIn": ["CHILDREN", "STAT"]},
            {"CampaignId": 3, "ChangesIn": ["STAT"]},
            {"CampaignId": "x", "ChangesIn": ["SELF"]},
            {"ChangesIn": ["SELF"]},
        ]
    }
    assert parse_changed_campaigns(result) == {1, 2}
    assert parse_changed_campaigns({}) == set()


# --- актуализация структуры ---


def _loader(monkeypatch, *, state=None, changed=None, units=None):
    load = _AccountLoad(
        1,
        {"id": 7, "connection_id": 3, "client_login": "client", "loaded_at": datetime.now(timezone.utc),
         "changes_ts": "2026-10-26T00:00:00Z", "load_state": state or {}},
        lambda *a: None,
    )
    calls: dict = {"structure": [], "saved": []}
    monkeypatch.setattr(load, "save", lambda **kw: calls["saved"].append(kw))
    monkeypatch.setattr(
        load, "structure", lambda rows, resume: calls["structure"].append(sorted(r["campaign_id"] for r in rows))
    )
    monkeypatch.setattr(load, "_changed_campaigns", lambda: changed)
    monkeypatch.setattr(load, "_changes_timestamp", lambda: "2026-10-27T00:00:00Z")
    monkeypatch.setattr(direct_api, "last_units", lambda login: units)
    return load, calls


ROWS = [{"id": 100 + cid, "campaign_id": cid, "state": "ON"} for cid in (1, 2, 3, 4)]


def test_refresh_structure_reads_changed_fresh_and_failed(monkeypatch):
    state = {"structure": {"status": "ok", "at": _ago(hours=1), "failed": [4]}}
    load, calls = _loader(monkeypatch, state=state, changed=({2}, "2026-10-27T01:00:00Z"))
    load._refresh_structure(ROWS, fresh=[101])
    assert calls["structure"] == [[1, 2, 4]]
    assert calls["saved"][-1] == {"changes_ts": "2026-10-27T01:00:00Z"}


def test_refresh_structure_nothing_changed_marks_stage_fresh(monkeypatch):
    state = {"structure": {"status": "ok", "at": _ago(hours=5)}}
    load, calls = _loader(monkeypatch, state=state, changed=(set(), "ts2"))
    load._refresh_structure(ROWS, fresh=[])
    assert calls["structure"] == []
    assert load.stage("structure")["status"] == "ok"
    assert datetime.fromisoformat(load.stage("structure")["at"]) > datetime.now(timezone.utc) - timedelta(minutes=1)


def test_refresh_structure_without_changes_api_rereads_only_new_until_due(monkeypatch):
    state = {"structure": {"status": "ok", "at": _ago(hours=2), "failed": [3]}}
    load, calls = _loader(monkeypatch, state=state, changed=None)
    load._refresh_structure(ROWS, fresh=[101])
    assert calls["structure"] == [[1, 3]]

    state = {"structure": {"status": "ok", "at": _ago(hours=30)}}
    load, calls = _loader(monkeypatch, state=state, changed=None)
    load._refresh_structure(ROWS, fresh=[])
    assert calls["structure"] == [[1, 2, 3, 4]]
    assert calls["saved"][-1] == {"changes_ts": "2026-10-27T00:00:00Z"}


def test_refresh_structure_skips_when_units_low(monkeypatch):
    state = {"structure": {"status": "ok", "at": _ago(hours=30)}}
    load, calls = _loader(monkeypatch, state=state, changed=({1}, "ts"), units={"spent": 0, "left": 10, "limit": 1000})
    load._refresh_structure(ROWS, fresh=[101])
    assert calls["structure"] == []
    assert load.stage("structure")["skipped"] == "units"
    assert load.stage("structure")["status"] == "ok"


# --- клиент API и отчёты ---


def test_get_all_follows_limited_by(monkeypatch):
    pages = []

    def fake_call(token, login, service, method, params, **kw):
        pages.append(params["Page"]["Offset"])
        if params["Page"]["Offset"] == 0:
            return {"Ads": [{"Id": 1}, {"Id": 2}], "LimitedBy": 2}
        return {"Ads": [{"Id": 3}]}

    monkeypatch.setattr(direct_api, "call", fake_call)
    items = direct_api.get_all("t", "client", "ads", {"SelectionCriteria": {"CampaignIds": [5]}}, "Ads")
    assert [i["Id"] for i in items] == [1, 2, 3]
    assert pages == [0, 2]


def test_call_remembers_units(respond):
    respond(lambda request: httpx.Response(200, json={"result": {"Campaigns": []}}, headers={"Units": "15/985/1000"}))
    direct_api.call("t", "Units-Login", "campaigns", "get", {})
    assert direct_api.last_units("units-login") == {"spent": 15, "left": 985, "limit": 1000}


def test_fetch_report_waits_for_offline_build(respond, monkeypatch):
    slept = []
    monkeypatch.setattr(direct_reports.time, "sleep", lambda s: slept.append(s))
    tsv = "Date\tCampaignId\tImpressions\n2026-10-25\t11\t100\n"
    answers = [httpx.Response(201, headers={"retryIn": "7"}), httpx.Response(202), httpx.Response(200, text=tsv)]
    requests = respond(lambda request: answers.pop(0))

    rows = direct_reports.fetch_report(
        "t",
        "client",
        report_type="CUSTOM_REPORT",
        field_names=["Date", "CampaignId", "Impressions"],
        date_from=date(2026, 9, 27),
        date_to=date(2026, 10, 26),
        filters=[{"Field": "CampaignId", "Operator": "IN", "Values": ["11"]}],
    )
    assert rows == [{"Date": "2026-10-25", "CampaignId": "11", "Impressions": "100"}]
    assert slept == [7, 5]
    body = json.loads(requests[0].content)["params"]
    assert body["SelectionCriteria"]["DateFrom"] == "2026-09-27"
    assert body["SelectionCriteria"]["Filter"][0]["Field"] == "CampaignId"
    assert requests[0].headers["Client-Login"] == "client"
    # Пока Директ строит отчёт, опрашиваем его под тем же именем — новое имя начало бы построение заново.
    names = {json.loads(r.content)["params"]["ReportName"] for r in requests}
    assert len(names) == 1


def test_fetch_report_gives_up_after_deadline(respond, monkeypatch):
    monkeypatch.setattr(direct_reports.time, "sleep", lambda s: None)
    respond(lambda request: httpx.Response(201, headers={"retryIn": "30"}))
    with pytest.raises(YandexError):
        direct_reports.fetch_report(
            "t", "client", report_type="CUSTOM_REPORT", field_names=["Date"],
            date_from=date(2026, 10, 1), date_to=date(2026, 10, 2), deadline_seconds=5,
        )


def test_fetch_report_raises_direct_error(respond):
    error = {"error": {"error_code": "8000", "error_string": "Неверный запрос", "error_detail": "плохое поле"}}
    respond(lambda request: httpx.Response(400, json=error))
    with pytest.raises(YandexError):
        direct_reports.fetch_report(
            "t", "client", report_type="CUSTOM_REPORT", field_names=["Date"],
            date_from=date(2026, 10, 1), date_to=date(2026, 10, 2),
        )


# --- разбор строк отчётов ---


def test_campaign_day_rows_merge_and_tolerate_missing_goal_data():
    rows = parse_campaign_day_rows(
        [
            {"Date": "2026-10-25", "CampaignId": "11", "Impressions": "100", "Clicks": "5", "Cost": "10.5",
             "Conversions": "--", "Revenue": "--"},
            {"Date": "2026-10-25", "CampaignId": "11", "Impressions": "50", "Clicks": "1", "Cost": "1",
             "Conversions": "2", "Revenue": "300"},
            {"Date": "плохая дата", "CampaignId": "11"},
            {"Date": "2026-10-25", "CampaignId": "0"},
        ]
    )
    assert rows == [
        {"day": date(2026, 10, 25), "campaign_id": 11, "impressions": 150, "clicks": 6, "cost_kopecks": 1150,
         "conversions": 2, "revenue_kopecks": 30000}
    ]


def test_aggregate_query_rows_merges_case_and_tolerates_missing_goal_data():
    rows = aggregate_query_rows(
        [
            {"Query": "Купить слона", "AdGroupId": "5", "Criterion": "слон", "Impressions": "10", "Clicks": "2",
             "Cost": "3.5", "Conversions": "--"},
            {"Query": "купить слона", "AdGroupId": "5", "Criterion": "", "Impressions": "1", "Clicks": "--",
             "Cost": "--", "Conversions": "1"},
            {"Query": "  ", "AdGroupId": "5"},
        ]
    )
    assert rows == [
        {"query": "Купить слона", "adgroup_id": 5, "matched_keyword": "слон", "impressions": 11, "clicks": 2,
         "cost_kopecks": 350, "conversions": 1}
    ]


def test_metrika_daily_and_goal_rows():
    daily = parse_daily_rows(
        [
            {"dimensions": [{"name": "2026-10-25"}], "metrics": [120, 100, 33.5, 7]},
            {"dimensions": [{"name": None}], "metrics": [1, 1, 1, 1]},
        ]
    )
    assert list(daily) == [date(2026, 10, 25)]
    assert daily[date(2026, 10, 25)]["visits"] == 120
    assert daily[date(2026, 10, 25)]["goal_reaches"] == 7

    by_day = parse_goal_rows([{"dimensions": [{"name": "2026-10-25"}], "metrics": [3, 0]}], [10, 20])
    assert by_day == {(date(2026, 10, 25), 10): 3, (date(2026, 10, 25), 20): 0}

    # Кампания Директа в группировке: номер в id и direct_id, в name — название.
    by_campaign = parse_goal_rows(
        [
            {"dimensions": [{"name": "2026-10-25"}, {"id": "555", "name": "Бренд 2026", "direct_id": "N-555"}],
             "metrics": [2, 0]},
            {"dimensions": [{"name": "2026-10-25"}, {"id": None, "name": "Поиск", "direct_id": "N-777"}],
             "metrics": [1, 4]},
            {"dimensions": [{"name": "2026-10-25"}, {"id": None, "name": None, "direct_id": None}], "metrics": [9, 9]},
        ],
        [10, 20],
    )
    assert by_campaign == {
        (date(2026, 10, 25), 10, 555): 2,
        (date(2026, 10, 25), 10, 777): 1,
        (date(2026, 10, 25), 20, 777): 4,
    }


def test_daily_metrics_are_known_to_metrika():
    # «ym:s:goalReachesAny» Метрика отклоняла (400, код 4002) — статистика счётчиков не загружалась вовсе.
    assert metrika_sync.DAILY_METRICS.split(",") == [
        "ym:s:visits", "ym:s:users", "ym:s:bounceRate", "ym:s:anyGoalReaches",
    ]


def test_metrika_error_keeps_api_message(respond):
    message = "Incorrectly specified metric, value: ym:s:goal5reaches, error code: 4002"
    respond(lambda request: httpx.Response(
        400, json={"errors": [{"error_type": "invalid_parameter", "message": message}], "code": 400, "message": message}
    ))
    with pytest.raises(YandexError) as err:
        metrika_sync._get(metrika_sync.STAT_URL, "token", {"ids": 1})
    assert (err.value.code, err.value.message) == ("metrika", message)

    respond(lambda request: httpx.Response(403, json={"errors": [{"message": "Access is denied"}]}))
    with pytest.raises(YandexError) as err:
        metrika_sync._get(metrika_sync.STAT_URL, "token")
    assert err.value.message == "Access is denied"

    respond(lambda request: httpx.Response(502, text="<html>Bad Gateway</html>"))
    with pytest.raises(YandexError) as err:
        metrika_sync._get(metrika_sync.STAT_URL, "token")
    assert err.value.message == "HTTP 502"


def _goal_report_reply(deleted: set[int]):
    """Отчёт Метрики по целям: цель из deleted — ошибка 4002 на весь отчёт, иначе достижений столько, каков номер цели."""

    def handler(request):
        goals = [int(m.removeprefix("ym:s:goal").removesuffix("reaches")) for m in request.url.params["metrics"].split(",")]
        bad = [goal_id for goal_id in goals if goal_id in deleted]
        if bad:
            message = f"Incorrectly specified metric, value: ym:s:goal{bad[0]}reaches, error code: 4002"
            return httpx.Response(400, json={"code": 400, "message": message})
        return httpx.Response(200, json={"data": [{"dimensions": [{"name": "2026-10-25"}], "metrics": goals}], "total_rows": 1})

    return handler


def test_goal_report_skips_goal_deleted_in_metrika(respond):
    requests = respond(_goal_report_reply({20}))
    day = date(2026, 10, 25)
    skipped: set[int] = set()
    out = metrika_sync.goal_report("token", 1, [10, 20, 30], skipped, dimensions="ym:s:date", date1=day, date2=day)
    assert skipped == {20}
    assert out == {(day, 10): 10, (day, 30): 30}
    assert [r.url.params["metrics"] for r in requests] == [
        "ym:s:goal10reaches,ym:s:goal20reaches,ym:s:goal30reaches",
        "ym:s:goal10reaches,ym:s:goal30reaches",
    ]

    # Следующий отчёт (по кампаниям) уже не спрашивает отклонённую цель.
    requests.clear()
    metrika_sync.goal_report("token", 1, [10, 20, 30], skipped, dimensions="ym:s:date", date1=day, date2=day)
    assert [r.url.params["metrics"] for r in requests] == ["ym:s:goal10reaches,ym:s:goal30reaches"]


def test_goal_report_raises_other_metrika_errors(respond):
    day = date(2026, 10, 25)
    respond(lambda request: httpx.Response(403, json={"code": 403, "message": "Access is denied"}))
    with pytest.raises(YandexError) as err:
        metrika_sync.goal_report("token", 1, [10], set(), dimensions="ym:s:date", date1=day, date2=day)
    assert err.value.message == "Access is denied"

    # Ошибка про цель не из этого запроса — не повод выкидывать цели запроса.
    respond(lambda request: httpx.Response(
        400, json={"message": "Incorrectly specified metric, value: ym:s:goal99reaches, error code: 4002"}
    ))
    skipped: set[int] = set()
    with pytest.raises(YandexError):
        metrika_sync.goal_report("token", 1, [10], skipped, dimensions="ym:s:date", date1=day, date2=day)
    assert skipped == set()


# --- обзор для интерфейса ---


def test_account_load_out_lists_every_stage():
    at = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    out = account_load_out(
        {
            "loaded_at": None,
            "load_state": {
                "campaigns": {"status": "ok", "at": "2026-10-27T08:00:00+00:00", "count": 12},
                "structure": {"status": "error", "error": "units", "done": 4, "total": 12, "failed": [5, 6]},
            },
            "units_left": 120,
            "units_limit": 20000,
            "units_at": at,
        }
    )
    assert out["loaded_at"] is None
    assert [s["key"] for s in out["stages"]] == list(account_load.STAGES)
    campaigns, structure, stats, queries = out["stages"]
    assert campaigns["status"] == "ok" and campaigns["count"] == 12
    assert structure == {
        "key": "structure", "title": account_load.STAGE_TITLES["structure"], "status": "error", "at": None,
        "done": 4, "total": 12, "count": 0, "error": "units", "message": "", "goals_skipped": [], "failed": 2,
        "skipped": "",
    }
    assert stats["status"] == "pending" and queries["status"] == "pending"
    assert out["units"] == {"left": 120, "limit": 20000, "at": at.isoformat()}

    assert account_load_out({"loaded_at": at, "load_state": None})["units"] is None


def test_stage_error_keeps_api_message_until_restart(monkeypatch):
    load, _ = _loader(monkeypatch)
    load.begin("structure", 3)
    load.fail("direct-denied", "Нет прав на кампании клиента")
    structure = account_load_out({"loaded_at": None, "load_state": load.state})["stages"][1]
    assert (structure["status"], structure["error"], structure["message"]) == (
        "error", "direct-denied", "Нет прав на кампании клиента",
    )

    load.begin("structure", 3)
    assert (load.state["structure"]["error"], load.state["structure"]["message"]) == ("", "")
