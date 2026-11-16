"""Блоки в ответе ИИ (show): числа считает сервис и они совпадают с «Отчётами» за тот же период."""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import ai, ai_agent, stats
from app.config import settings
from app.main import app
from app.web_auth import make_session
from tests.dbutil import make_account, make_campaign, make_connection, make_counter, make_user, make_workspace, setup_ai

D1 = date(2026, 10, 19)  # период 19–25 октября, прошлый — 12–18
D7 = date(2026, 10, 25)
RANGE = {"date_from": D1.isoformat(), "date_to": D7.isoformat()}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-ai-secret")


def _seed(db):
    user = make_user(db)
    conn_id = make_connection(db, user, "agency")
    acc = make_account(db, user, conn_id, name="Кабинет", login="acc-a")
    other_acc = make_account(db, user, conn_id, name="Блог", login="acc-b")
    counter = make_counter(db, user, conn_id, 555, site="shop.ru")
    search = make_campaign(db, user, acc, 11, "Поиск")
    network = make_campaign(db, user, acc, 12, "РСЯ", state="SUSPENDED")
    foreign = make_campaign(db, user, other_acc, 21, "Блог")
    ws = make_workspace(db, user, 555, "Магазин", counter_row_id=counter, accounts=[acc], campaigns=[search, network])
    blog = make_workspace(db, user, 777, "Блог", accounts=[other_acc], campaigns=[foreign])
    # Две недели: прошлая (12–18) и текущая (19–25), у кампаний разные расходы и заявки.
    for i in range(14):
        day = D1 - timedelta(days=7) + timedelta(days=i)
        for acc_id, cid, cost, clicks, conv in ((acc, 11, 1000 + i * 50, 20 + i, i % 3), (acc, 12, 400, 30, 1 if i > 9 else 0),
                                                (other_acc, 21, 300, 5, 1)):
            stats.upsert_direct_stat(
                user_id=user, day=day, account_id=acc_id, campaign_id=cid, impressions=clicks * 12, clicks=clicks,
                cost_kopecks=cost * 100, conversions=conv, revenue_kopecks=0,
            )
    setup_ai()
    return {"user": user, "ws": ws, "blog": blog, "search": search, "network": network, "foreign": foreign}


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, cfg, messages, tools=None):
        self.calls.append(messages)
        return self.replies.pop(0)


def _show(*blocks):
    return {
        "content": "",
        "tool_calls": [{"id": f"b{i}", "name": "show", "arguments": json.dumps(b)} for i, b in enumerate(blocks)],
    }


def _ask(user, chat_id, monkeypatch, *replies):
    fake = FakeModel(*replies)
    monkeypatch.setattr(ai, "complete", fake)
    ai_agent.post_message(user, chat_id, "Покажи, как дела")
    ai_agent.run_chat(user, chat_id)
    return fake


def _blocks(user, chat_id):
    messages = ai_agent.get_chat(user, chat_id)["messages"]
    return [b for m in messages for b in m.get("blocks") or []]


def test_blocks_match_reports(db, monkeypatch):
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    report = client.get(f"/api/web/workspaces/{s['ws']}/analytics", params=RANGE, headers=auth).json()
    chat = ai_agent.create_chat(s["user"], s["ws"])["id"]
    fake = _ask(
        s["user"],
        chat,
        monkeypatch,
        _show(
            {"type": "chart", "metric": "cpa", **RANGE},
            {"type": "compare", "metrics": ["cost", "clicks", "conversions", "cpa", "ctr"], **RANGE},
            {"type": "campaigns", **RANGE},
            {"type": "table", "by": "days", "metrics": ["cost", "ctr", "cpc"], **RANGE},
        ),
        {"content": "Заявки подорожали из-за РСЯ.", "tool_calls": []},
    )
    chart, compare, cards, table = _blocks(s["user"], chat)

    # График: каждый день и пунктир прошлого периода — как ряды отчёта.
    assert [p["day"] for p in chart["points"]] == [d["day"] for d in report["by_day"]]
    assert [p["value"] for p in chart["points"]] == [d["cpa_kopecks"] for d in report["by_day"]]
    assert [p["value"] for p in chart["prev_points"]] == [d["cpa_kopecks"] for d in report["prev_by_day"]]
    assert chart["total"] == report["totals"]["cpa_kopecks"] and chart["delta"] == report["compare"]["cpa_kopecks"]
    assert chart["unit"] == "kop" and chart["good"] == "down"

    # Сравнение периодов: обе колонки и изменение — как итоги и compare отчёта.
    fields = {"cost": "cost_kopecks", "clicks": "clicks", "conversions": "conversions", "cpa": "cpa_kopecks", "ctr": "ctr"}
    for row in compare["rows"]:
        f = fields[row["metric"]]
        assert (row["cur"], row["prev"], row["delta"]) == (report["totals"][f], report["previous"][f], report["compare"][f])

    # Карточки: строки отчёта по кампаниям, три числа по умолчанию.
    by_id = {c["campaign_row_id"]: c for c in report["by_campaign"]}
    assert [i["campaign_row_id"] for i in cards["items"]] == [c["campaign_row_id"] for c in report["by_campaign"]]
    for item in cards["items"]:
        c = by_id[item["campaign_row_id"]]
        assert [v["value"] for v in item["values"]] == [c["cost_kopecks"], c["conversions"], c["cpa_kopecks"]]
        assert item["workspace_id"] == s["ws"]
    assert next(i for i in cards["items"] if i["name"] == "РСЯ")["state"] == "SUSPENDED"

    # Таблица по дням: производные метрики дня считаются из его сумм так же, как в итогах отчёта.
    for row, d in zip(table["rows"], report["by_day"]):
        ctr = round(d["clicks"] / d["impressions"] * 100, 2)
        cpc = round(d["cost_kopecks"] / d["clicks"])
        assert row["values"] == [d["cost_kopecks"], ctr, cpc]

    # Модели — те же числа коротко; в ленте вызов show не шаг, а блоки сообщения.
    brief = json.loads(fake.calls[1][-4]["content"])
    assert brief["shown"] == "chart" and brief["delta_pct"] == report["compare"]["cpa_kopecks"]
    assert "show" in [t["function"]["name"] for t in ai_agent.TOOLS]
    assert "блоком show" in fake.calls[0][0]["content"]


def test_campaign_chart_and_table_sorting(db, monkeypatch):
    s = _seed(db)
    from app.analytics import workspace_analytics

    report = workspace_analytics(s["user"], s["ws"], date_from=D1, date_to=D7, with_tops=False, campaign_row_id=s["network"])
    chat = ai_agent.create_chat(s["user"], s["ws"])["id"]
    _ask(
        s["user"],
        chat,
        monkeypatch,
        _show(
            {"type": "chart", "metric": "conversions", "campaign_row_id": s["network"], "compare_previous": False, **RANGE},
            {"type": "table", "by": "campaigns", "metrics": ["cost", "conversions"], "sort": "cost", **RANGE},
        ),
        {"content": "Готово.", "tool_calls": []},
    )
    chart, table = _blocks(s["user"], chat)
    assert [p["value"] for p in chart["points"]] == [d["conversions"] for d in report["by_day"]]
    assert chart["prev_points"] == [] and "РСЯ" in chart["title"]
    assert [r["label"] for r in table["rows"]] == ["Поиск", "РСЯ"]  # по расходу, больший сверху
    assert table["columns"][0]["label"] == "Расход" and table["first"] == "Кампания"


def test_show_errors_and_scope(db, monkeypatch):
    s = _seed(db)
    chat = ai_agent.create_chat(s["user"], s["ws"])["id"]
    fake = _ask(
        s["user"],
        chat,
        monkeypatch,
        _show(
            {"type": "pie"},
            {"type": "chart", "metric": "likes"},
            {"type": "campaigns", "campaign_row_ids": [s["foreign"]]},
            {"type": "table", "by": "projects"},
        ),
        {"content": "Не вышло.", "tool_calls": []},
    )
    results = [json.loads(m["content"]) for m in fake.calls[1] if m["role"] == "tool"]
    assert all(r["ok"] is False for r in results)
    assert "Вид блока" in results[0]["error"] and "likes" in results[1]["error"]
    assert "нет в этом проекте" in results[2]["error"] and "общем чате" in results[3]["error"]
    assert _blocks(s["user"], chat) == []

    # Не больше четырёх блоков в одном сообщении.
    chat2 = ai_agent.create_chat(s["user"], s["ws"])["id"]
    fake = _ask(s["user"], chat2, monkeypatch, _show(*[{"type": "compare"}] * 5), {"content": "Ок.", "tool_calls": []})
    assert len(_blocks(s["user"], chat2)) == 4
    last = [json.loads(m["content"]) for m in fake.calls[1] if m["role"] == "tool"][-1]
    assert last["ok"] is False


def test_general_chat_blocks_over_projects(db, monkeypatch):
    s = _seed(db)
    from app.analytics import portfolio_analytics

    portfolio = portfolio_analytics(s["user"], date_from=D1, date_to=D7)
    chat = ai_agent.create_chat(s["user"], None)["id"]
    _ask(
        s["user"],
        chat,
        monkeypatch,
        _show(
            {"type": "table", "by": "projects", "metrics": ["cost", "conversions"], **RANGE},
            {"type": "compare", "project_id": s["blog"], **RANGE},
            {"type": "campaigns", "metrics": ["cost"], **RANGE},
        ),
        {"content": "Сравнил.", "tool_calls": []},
    )
    table, compare, cards = _blocks(s["user"], chat)
    assert {r["label"]: r["values"] for r in table["rows"]} == {
        p["name"]: [p["totals"]["cost_kopecks"], p["totals"]["conversions"]] for p in portfolio["projects"]
    }
    assert compare["rows"][0]["cur"] == 7 * 300 * 100  # только «Блог»
    blog_card = next(i for i in cards["items"] if i["name"] == "Блог")
    assert blog_card["workspace_id"] == s["blog"] and blog_card["project"] == "Блог"
