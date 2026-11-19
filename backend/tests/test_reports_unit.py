"""Отчёт клиенту: период, итоги с изменениями, кампании, «что сделано», план по умолчанию, логотип, промпт."""

import base64
from datetime import date

import pytest

from app import reports as r
from app.yandex import YandexError


def test_default_month_is_previous_calendar_month():
    assert r.default_month(date(2026, 11, 19)) == date(2026, 10, 1)
    assert r.default_month(date(2026, 1, 1)) == date(2025, 12, 1)
    assert r.parse_month("", date(2026, 11, 19)) == date(2026, 10, 1)
    assert r.parse_month("2026-03", date(2026, 11, 19)) == date(2026, 3, 1)
    assert r.parse_month("2026-11", date(2026, 11, 19)) == date(2026, 11, 1)
    for bad in ("2026-13", "26-10", "октябрь", "2026-12"):
        with pytest.raises(YandexError):
            r.parse_month(bad, date(2026, 11, 19))


def test_periods_compare_full_previous_month():
    p = r.periods(date(2026, 10, 1), date(2026, 11, 19))
    assert p["date_from"] == date(2026, 10, 1) and p["date_to"] == date(2026, 10, 31)
    assert p["prev_from"] == date(2026, 9, 1) and p["prev_to"] == date(2026, 9, 30)
    assert not p["partial"]
    feb = r.periods(date(2028, 3, 1), date(2028, 4, 2))
    assert feb["prev_to"] == date(2028, 2, 29)
    # Текущий месяц — по вчера.
    cur = r.periods(date(2026, 11, 1), date(2026, 11, 19))
    assert cur["date_to"] == date(2026, 11, 18) and cur["partial"]
    first_day = r.periods(date(2026, 11, 1), date(2026, 11, 1))
    assert first_day["date_to"] == date(2026, 11, 1)


def test_month_label():
    assert r.month_label(date(2026, 10, 1)) == "октябрь 2026"
    assert r.month_key(date(2026, 3, 1)) == "2026-03"


def test_totals_block_deltas():
    cur = {"cost_kopecks": 120_000, "clicks": 50, "impressions": 1000, "ctr": 5.0, "conversions": 6, "cpa_kopecks": 20_000}
    prev = {"cost_kopecks": 100_000, "clicks": 0, "impressions": 800, "ctr": 4.0, "conversions": 0, "cpa_kopecks": None}
    t = {i["key"]: i for i in r.totals_block(cur, prev)}
    assert t["cost_kopecks"]["delta"] == 20.0 and t["cost_kopecks"]["prev"] == 100_000
    assert t["clicks"]["delta"] is None  # к нулю — без «+∞ %»
    assert t["ctr"]["delta"] == 25.0
    assert t["cpa_kopecks"]["delta"] is None
    assert [i["key"] for i in r.totals_block(cur, prev)] == list(r.TOTAL_KEYS)


def test_campaigns_block_skips_silent_and_sorts_by_spend():
    cur = [
        {"campaign_row_id": 1, "name": "Поиск", "cost_kopecks": 5000, "impressions": 100, "clicks": 5, "ctr": 5.0,
         "conversions": 1, "cpa_kopecks": 5000},
        {"campaign_row_id": 2, "name": "РСЯ", "cost_kopecks": 9000, "impressions": 900, "clicks": 9, "ctr": 1.0,
         "conversions": 0, "cpa_kopecks": None},
        {"campaign_row_id": 3, "name": "Пустая", "cost_kopecks": 0, "impressions": 0, "clicks": 0},
        {"campaign_row_id": 4, "name": "Остановили", "cost_kopecks": 0, "impressions": 0, "clicks": 0},
    ]
    prev = [{"campaign_row_id": 1, "cost_kopecks": 10000, "conversions": 2}, {"campaign_row_id": 4, "cost_kopecks": 300}]
    out = r.campaigns_block(cur, prev)
    assert [c["name"] for c in out] == ["РСЯ", "Поиск", "Остановили"]
    search = out[1]
    assert search["prev_cost_kopecks"] == 10000 and search["cost_delta"] == -50.0 and search["prev_conversions"] == 2


def test_done_items_plain_language_without_undone():
    logs = [
        {"id": 1, "action": "negatives", "title": "Минус-фразы", "status": "ok", "created_at": "2026-10-03T10:00:00+00:00",
         "changes": ["Добавлено 12 минус-фраз", "a", "b", "c"], "source": "user"},
        {"id": 2, "action": "pause", "title": "Остановка", "status": "ok", "created_at": "2026-10-04T10:00:00+00:00",
         "undone_at": "2026-10-05T10:00:00+00:00", "changes": []},
        {"id": 3, "action": "undo", "title": "Откат", "status": "ok", "undo_of": 2, "created_at": "2026-10-05"},
        {"id": 4, "action": "set_budget", "title": "Бюджет", "status": "error", "created_at": "2026-10-06"},
        {"id": 5, "action": "set_budget", "title": "Бюджет", "status": "partial", "created_at": "2026-10-07T00:00:00",
         "changes": ["Бюджет: 1 000 ₽ → 1 500 ₽"], "source": "ai"},
    ]
    out = r.done_items(logs)
    assert [i["id"] for i in out] == [1, 5]
    assert out[0]["day"] == "2026-10-03" and out[0]["changes"] == ["Добавлено 12 минус-фраз", "a", "b"]
    assert out[1]["source"] == "ai"


def test_normalize_sections_defaults_on():
    assert r.normalize_sections(None) == {k: True for k in r.SECTION_KEYS}
    s = r.normalize_sections({"done": False, "junk": False, "plan": 0})
    assert s["done"] is False and s["plan"] is False and s["totals"] is True and "junk" not in s


def test_default_plan_from_project_goals():
    assert r.default_plan({}) == ""
    text = r.default_plan({"kpi_type": "cpa", "kpi_target": 1500, "monthly_budget_kopecks": 5_000_000}).replace("\xa0", " ")
    assert "цену заявки не выше 1 500 ₽" in text and "Бюджет на месяц — 50 000 ₽" in text
    assert "15 %" in r.default_plan({"kpi_type": "drr", "kpi_target": 15})
    # Цель клиента-папки, если у проекта своей нет.
    assert "800 ₽" in r.default_plan({"kpi_type": "", "client_kpi_type": "cpa", "client_kpi_target": 800}).replace("\xa0", " ")


def _url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def test_decode_logo_types_and_size():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    assert r.decode_logo(_url("image/png", png)) == (png, "image/png")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
    assert r.decode_logo(_url("image/svg+xml", svg))[1] == "image/svg+xml"
    bad = [
        _url("image/gif", b"GIF89a"),
        _url("image/png", b"not a png"),
        _url("image/jpeg", b"\x89PNG"),
        _url("image/svg+xml", b"<svg><script>alert(1)</script></svg>"),
        _url("image/svg+xml", b"hello"),
        _url("image/png", b"\x89PNG\r\n\x1a\n" + b"0" * r.LOGO_MAX_BYTES),
        "data:image/png;base64,@@@",
        "просто текст",
    ]
    for value in bad:
        with pytest.raises(YandexError):
            r.decode_logo(value)


def test_clean_text_limit():
    assert r._clean_text("  план \r\n дальше ") == "план \n дальше"
    with pytest.raises(YandexError):
        r._clean_text("x" * (r.TEXT_MAX + 1))


def _report(**extra):
    base = {
        "month_label": "октябрь 2026",
        "prev_label": "сентябрь 2026",
        "partial": False,
        "date_to": "2026-10-31",
        "totals": r.totals_block(
            {"cost_kopecks": 1_200_000, "clicks": 300, "impressions": 9000, "ctr": 3.33, "conversions": 12,
             "cpa_kopecks": 100_000},
            {"cost_kopecks": 1_000_000, "clicks": 250, "impressions": 8000, "ctr": 3.1, "conversions": 8,
             "cpa_kopecks": 125_000},
        ),
        "campaigns": [{"name": "Поиск", "cost_kopecks": 1_200_000, "conversions": 12, "cpa_kopecks": 100_000}],
        "done": [{"day": "2026-10-03", "title": "Минус-фразы", "changes": ["Добавлено 12 минус-фраз"]}],
        "plan": "Снизить цену заявки",
    }
    return {**base, **extra}


def test_comment_prompt_has_numbers_and_done():
    text = r.comment_prompt(_report(), "О бизнесе:\nКлиент: Окна").replace("\xa0", " ")
    assert "октябрь 2026" in text and "сентябрь 2026" in text
    assert "Расход: 12 000 ₽ (было 10 000 ₽, изменение +20 %)" in text
    assert "Заявки: 12 (было 8" in text
    assert "«Поиск»" in text and "Добавлено 12 минус-фраз" in text and "Снизить цену заявки" in text
    empty = r.comment_prompt(_report(done=[], campaigns=[], plan=""), "")
    assert "Изменений в рекламе за месяц не записано" in empty


def test_ai_comment_uses_report_and_returns_text(monkeypatch):
    ws = {"id": 7, "name": "Окна", "brief": {}}
    monkeypatch.setattr("app.workspaces.require_workspace", lambda user_id, wid: ws)
    monkeypatch.setattr(r, "build_report", lambda user_id, wid, month: _report())
    monkeypatch.setattr(r.ai, "load_config", lambda user_id, task, wid: {"task": task})
    monkeypatch.setattr("app.ai_memory.account_prefs", lambda user_id: {})
    seen = {}

    def fake_complete(cfg, messages):
        seen["cfg"], seen["messages"] = cfg, messages
        return {"content": "  За октябрь заявок стало больше.  "}

    monkeypatch.setattr(r.ai, "complete", fake_complete)
    out = r.ai_comment(1, {"workspace_id": 7, "month": "2026-10"})
    assert out["comment"] == "За октябрь заявок стало больше."
    assert seen["cfg"]["task"] == "report"
    assert "Расход: 12 000 ₽" in seen["messages"][1]["content"].replace("\xa0", " ")
    # Расход ИИ — без токенов и денег.
    assert out["usage"] is None or set(out["usage"]) <= {"calls", "percent", "estimated"}

    monkeypatch.setattr(r.ai, "complete", lambda cfg, messages: {"content": "  "})
    with pytest.raises(YandexError):
        r.ai_comment(1, {"workspace_id": 7})


def test_report_comment_job_registered():
    from app.job_handlers import HANDLERS

    assert "report_comment" in HANDLERS
    assert "report" in r.ai.TASKS


def test_report_comment_failure_reaches_bell():
    from app import operation_notify as op

    spec = op.OPERATIONS["report_comment"]
    assert spec["page"] == "/w/{ws}/report" and spec["retry"] is None
    assert op.is_operation({"kind": "report_comment", "user_id": 1, "priority": op.PRIORITY_USER})


def test_done_items_day_in_msk():
    from datetime import datetime, timezone

    logs = [
        # 1 ноября 01:30 по Москве = 31 октября 22:30 UTC — день по Москве.
        {"id": 1, "title": "a", "status": "ok", "created_at": "2026-10-31T22:30:00+00:00"},
        {"id": 2, "title": "b", "status": "ok", "created_at": datetime(2026, 10, 31, 22, 30, tzinfo=timezone.utc)},
        {"id": 3, "title": "c", "status": "ok", "created_at": "2026-11-02T00:10:00"},
        {"id": 4, "title": "d", "status": "ok", "created_at": None},
    ]
    assert [i["day"] for i in r.done_items(logs)] == ["2026-11-01", "2026-11-01", "2026-11-02", ""]
