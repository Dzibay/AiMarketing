"""Отчёт клиенту за месяц: цифры к прошлому месяцу, «что сделано» из журнала, план, разделы, «готово», оформление."""

import base64
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import reports
from app.clock import MSK
from app.main import app
from app.web_auth import require_web_user
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

TODAY = date(2026, 11, 19)


@pytest.fixture
def cab(db, monkeypatch):
    monkeypatch.setattr(reports, "today_msk", lambda: TODAY)
    user = make_user(db)
    conn_id = make_connection(db, user)
    acc = make_account(db, user, conn_id, login="a")
    search = make_campaign(db, user, acc, 101, name="Поиск")
    rsya = make_campaign(db, user, acc, 102, name="РСЯ")
    idle = make_campaign(db, user, acc, 103, name="Пустая")
    ws = make_workspace(
        db, user, 1, name="Окна", accounts=[acc], campaigns=[search, rsya, idle], kpi_type="cpa", kpi_target=1500,
        monthly_budget_rub=50000,
    )
    return {"user": user, "acc": acc, "search": search, "rsya": rsya, "ws": ws}


def _stat(db, c, day: date, campaign_id: int, rub: int, clicks: int, conversions: int, impressions: int = 1000):
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO stats_daily (user_id, day, source, account_id, campaign_id, impressions, clicks, cost_kopecks,
                                     conversions)
            VALUES (%s, %s, 'direct', %s, %s, %s, %s, %s, %s)
            """,
            (c["user"], day, c["acc"], campaign_id, impressions, clicks, rub * 100, conversions),
        )


def _log(db, c, at: datetime, action: str = "set_budget", *, status: str = "ok", title: str = "", undone: bool = False):
    with db.connection() as conn:
        return conn.execute(
            """
            INSERT INTO action_logs (user_id, workspace_id, action, title, source, payload, result, status, created_at,
                                     undone_at)
            VALUES (%s, %s, %s, %s, 'user', %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                c["user"], c["ws"], action, title, '{"campaign_row_ids": [%d]}' % c["search"], "{}", status, at,
                at + timedelta(hours=1) if undone else None,
            ),
        ).fetchone()["id"]


def test_report_numbers_against_previous_month(db, cab):
    c = cab
    _stat(db, c, date(2026, 10, 5), 101, 6000, 100, 4)
    _stat(db, c, date(2026, 10, 20), 102, 4000, 50, 1)
    _stat(db, c, date(2026, 9, 10), 101, 5000, 80, 2)
    _stat(db, c, date(2026, 11, 2), 101, 9999, 99, 9)  # следующий месяц — не в отчёте
    rep = reports.build_report(c["user"], c["ws"])
    assert rep["month"] == "2026-10" and rep["month_label"] == "октябрь 2026" and rep["prev_label"] == "сентябрь 2026"
    assert rep["date_from"] == "2026-10-01" and rep["date_to"] == "2026-10-31" and rep["prev_to"] == "2026-09-30"
    t = {i["key"]: i for i in rep["totals"]}
    assert t["cost_kopecks"]["value"] == 1_000_000 and t["cost_kopecks"]["prev"] == 500_000
    assert t["cost_kopecks"]["delta"] == 100.0
    assert t["conversions"]["value"] == 5 and t["conversions"]["prev"] == 2
    assert t["cpa_kopecks"]["value"] == 200_000 and t["cpa_kopecks"]["prev"] == 250_000 and t["cpa_kopecks"]["delta"] == -20.0
    assert len(rep["by_day"]) == 31 and len(rep["prev_by_day"]) == 30
    assert [x["name"] for x in rep["campaigns"]] == ["Поиск", "РСЯ"]  # без показов — не в таблице
    assert rep["campaigns"][0]["prev_cost_kopecks"] == 500_000
    # План по умолчанию — из целей проекта, разделы все включены, не готов.
    assert "1 500" in rep["plan"].replace("\xa0", " ") and "50 000" in rep["plan"].replace("\xa0", " ")
    assert all(rep["sections"].values()) and rep["done_at"] is None and rep["comment"] == ""
    assert rep["brand"] == {"name": "", "contacts": "", "accent": "", "logo": ""}

    with pytest.raises(YandexError):
        reports.build_report(c["user"], c["ws"], "2026-12")
    other = make_user(db, "x@example.com")
    with pytest.raises(YandexError):
        reports.build_report(other, c["ws"])


def test_done_from_journal_in_month_only(db, cab):
    c = cab
    oct_ = datetime(2026, 10, 10, 12, tzinfo=MSK)
    kept = _log(db, c, oct_, "pause", title="Остановка кампаний")
    _log(db, c, oct_, "set_budget", undone=True)  # откатили — клиенту не показываем
    _log(db, c, oct_, "set_budget", status="error")
    _log(db, c, datetime(2026, 9, 30, 23, 30, tzinfo=MSK), "pause")  # сентябрь по Москве
    _log(db, c, datetime(2026, 10, 31, 21, 30, tzinfo=timezone.utc), "pause")  # 1 ноября по Москве
    last = _log(db, c, datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc), "resume", title="Запуск кампаний")
    rep = reports.build_report(c["user"], c["ws"], "2026-10")
    assert [d["id"] for d in rep["done"]] == [kept, last]
    assert rep["done"][0]["title"] == "Остановка кампаний"
    assert isinstance(rep["done"][0]["changes"], list)


def test_save_plan_comment_sections_and_done(db, cab):
    c = cab
    saved = reports.save_report(c["user"], c["ws"], "2026-10", comment="Хороший месяц", sections={"done": False})
    assert saved["comment"] == "Хороший месяц" and saved["plan"] is None and saved["sections"]["done"] is False
    # План не трогали — остаётся план по умолчанию; потом свой, в том числе пустой.
    assert "1 500" in reports.build_report(c["user"], c["ws"], "2026-10")["plan"].replace("\xa0", " ")
    reports.save_report(c["user"], c["ws"], "2026-10", plan="")
    rep = reports.build_report(c["user"], c["ws"], "2026-10")
    assert rep["plan"] == "" and rep["comment"] == "Хороший месяц"
    reports.save_report(c["user"], c["ws"], "2026-10", plan="Запустить РСЯ")
    assert reports.build_report(c["user"], c["ws"], "2026-10")["comment"] == "Хороший месяц"
    # Набор разделов — на проект: в следующем месяце тот же.
    assert reports.build_report(c["user"], c["ws"], "2026-09")["sections"]["done"] is False
    assert reports.build_report(c["user"], c["ws"], "2026-09")["plan"] != "Запустить РСЯ"
    with pytest.raises(YandexError):
        reports.save_report(c["user"], c["ws"], "2026-10", comment="x" * (reports.TEXT_MAX + 1))

    status = reports.reports_status(c["user"])
    assert status["month_name"] == "октябрь" and status["done"] == 0 and status["total"] == 1
    first = reports.mark_done(c["user"], c["ws"], "2026-10")
    again = reports.mark_done(c["user"], c["ws"], "2026-10")
    assert first["done_at"] and again["done_at"] == first["done_at"]
    assert reports.build_report(c["user"], c["ws"], "2026-10")["done_at"] == first["done_at"]
    status = reports.reports_status(c["user"])
    assert status["done"] == 1 and status["items"][0]["done"]
    assert reports.mark_done(c["user"], c["ws"], "2026-10", False)["done_at"] is None
    assert reports.reports_status(c["user"])["done"] == 0
    # Готов другой месяц — октябрь всё ещё не готов.
    reports.mark_done(c["user"], c["ws"], "2026-09")
    assert reports.reports_status(c["user"], "2026-10")["done"] == 0


def test_status_counts_only_active_projects(db, cab):
    c = cab
    ws2 = make_workspace(db, c["user"], 2, name="Двери")
    archived = make_workspace(db, c["user"], 3, name="Старый")
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET archived_at = now() WHERE id = %s", (archived,))
    reports.mark_done(c["user"], ws2, None)
    s = reports.reports_status(c["user"])
    assert s["total"] == 2 and s["done"] == 1
    assert {i["name"] for i in s["items"]} == {"Окна", "Двери"}


def test_brand_and_logo(db, cab):
    c = cab
    b = reports.save_brand(c["user"], name="Студия Мир", contacts="+7 900 000-00-00 · mir@example.com", accent="#A3E635")
    assert b["name"] == "Студия Мир" and b["accent"] == "#A3E635" and b["logo"] == ""
    with pytest.raises(YandexError):
        reports.save_brand(c["user"], accent="red")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    b = reports.set_logo(c["user"], url)
    assert b["logo"] == url and b["name"] == "Студия Мир"
    # Оформление не стирает логотип, и наоборот.
    b = reports.save_brand(c["user"], name="Мир", contacts="", accent="")
    assert b["logo"] == url and b["name"] == "Мир"
    assert reports.build_report(c["user"], c["ws"])["brand"]["logo"] == url
    assert reports.set_logo(c["user"], None)["logo"] == ""
    # Логотип раньше оформления — строка создаётся.
    other = make_user(db, "o@example.com")
    assert reports.set_logo(other, url)["logo"] == url


def test_api_flow(db, cab, monkeypatch):
    c = cab
    submitted = []
    import app.api.reports as api

    monkeypatch.setattr(
        api, "submit", lambda kind, user_id, payload, **kw: submitted.append((kind, payload)) or {"id": 1, "kind": kind}
    )
    monkeypatch.setattr(api, "public_job", lambda job: {"id": job["id"], "status": "queued"})
    app.dependency_overrides[require_web_user] = lambda: c["user"]
    try:
        client = TestClient(app)
        rep = client.get(f"/api/web/workspaces/{c['ws']}/report").json()
        assert rep["month"] == "2026-10" and rep["section_list"][0]["key"] == "totals"
        r = client.put(
            f"/api/web/workspaces/{c['ws']}/report",
            json={"month": "2026-10", "comment": "Текст", "sections": {"campaigns": False}},
        )
        assert r.status_code == 200 and r.json()["sections"]["campaigns"] is False
        assert client.post(f"/api/web/workspaces/{c['ws']}/report/done", json={"month": "2026-10"}).json()["done_at"]
        assert client.get("/api/web/reports/status").json()["done"] == 1
        assert client.get(f"/api/web/workspaces/{c['ws']}/report?month=2026-13").status_code == 400
        job = client.post(f"/api/web/workspaces/{c['ws']}/report/ai-comment", json={"month": "2026-10"}).json()
        assert job["job"]["id"] == 1
        assert submitted == [("report_comment", {"workspace_id": c["ws"], "month": "2026-10"})]
        assert client.put("/api/web/report-brand", json={"name": "Мир"}).json()["name"] == "Мир"
        bad = client.put("/api/web/report-brand/logo", json={"data": "data:image/gif;base64,R0lG"})
        assert bad.status_code == 400
        assert client.delete("/api/web/report-brand/logo").json()["logo"] == ""
        # Чужой проект — не найден.
        other = make_user(db, "z@example.com")
        app.dependency_overrides[require_web_user] = lambda: other
        assert client.get(f"/api/web/workspaces/{c['ws']}/report").status_code in (403, 404)
        assert client.post(f"/api/web/workspaces/{c['ws']}/report/ai-comment", json={}).status_code in (403, 404)
    finally:
        app.dependency_overrides.pop(require_web_user, None)


def test_project_delete_cascades(db, cab):
    c = cab
    reports.mark_done(c["user"], c["ws"], None)
    reports.save_report(c["user"], c["ws"], None, sections={"plan": False})
    with db.connection() as conn:
        conn.execute("DELETE FROM workspaces WHERE id = %s", (c["ws"],))
        assert conn.execute("SELECT COUNT(*) AS n FROM client_reports").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM report_settings").fetchone()["n"] == 0
