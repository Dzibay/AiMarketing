"""«Защита бюджета»: переключатели проекта, порог в ценах заявки при проверке и «Включить у всех клиентов»."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import rules, rules_templates as rt
from app.clock import yesterday_msk
from app.main import app
from app.web_auth import require_web_user
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace


@pytest.fixture
def cab(db, monkeypatch):
    user = make_user(db)
    conn_id = make_connection(db, user)
    acc = make_account(db, user, conn_id, login="a")
    camp = make_campaign(db, user, acc, 101, name="Поиск")
    ws = make_workspace(db, user, 1, name="Окна", accounts=[acc], campaigns=[camp], kpi_type="cpa", kpi_target=1000)
    sent: list[dict] = []
    monkeypatch.setattr(rules, "notify", lambda user_id, kind, title, **kw: sent.append({"title": title, **kw}))
    return {"user": user, "acc": acc, "camp": camp, "ws": ws, "sent": sent}


def _spend(db, c, rub: int, conversions: int = 0, campaign_id: int = 101, days_ago: int = 1):
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO stats_daily (user_id, day, source, account_id, campaign_id, clicks, cost_kopecks, conversions)
            VALUES (%s, %s, 'direct', %s, %s, 10, %s, %s)
            """,
            (c["user"], yesterday_msk() - timedelta(days=days_ago), c["acc"], campaign_id, rub * 100, conversions),
        )


def _items(state):
    return {i["key"]: i for i in state["items"]}


def test_state_defaults_and_toggle(cab):
    c = cab
    state = rt.protection_state(c["user"], c["ws"])
    assert state["target_cpa_rub"] == 1000.0
    items = _items(state)
    assert not any(i["enabled"] for i in items.values())
    assert items["spend_notify"]["threshold_kind"] == "cpa_x" and items["spend_notify"]["value"] == 2.0
    assert "2 000" in items["spend_notify"]["title"].replace("\xa0", " ")

    # Выключить то, чего нет, — ничего не создаёт.
    rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=False)
    assert _items(rt.protection_state(c["user"], c["ws"]))["spend_pause"]["rule_id"] is None

    on = _items(rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=True))["spend_pause"]
    assert on["enabled"] and on["rule_id"] and on["threshold_kind"] == "cpa_x" and on["value"] == 3.0
    rule = next(r for r in rules.list_rules(c["user"]) if r["id"] == on["rule_id"])
    assert rule["template"] == "spend_pause" and rule["action"] == "pause" and not rule["dry_run"]
    assert rule["scope_workspace_id"] == c["ws"] and rule["threshold_kind"] == "cpa_x"

    # Новый порог и выключение — то же правило, без дублей.
    thr = _items(rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=True, threshold_kind="rub", value=7000))
    assert thr["spend_pause"]["rule_id"] == on["rule_id"] and thr["spend_pause"]["value"] == 7000.0
    off = _items(rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=False))["spend_pause"]
    assert not off["enabled"] and off["threshold_kind"] == "rub" and off["value"] == 7000.0
    assert sum(1 for r in rules.list_rules(c["user"]) if r.get("template") == "spend_pause") == 1

    with pytest.raises(YandexError):
        rt.set_protection(c["user"], c["ws"], "nope", enabled=True)


def test_threshold_saved_before_switch_on(cab):
    c = cab
    # Порог сохранили, пока проверка выключена и правила ещё нет: правило создаётся выключенным с этим порогом.
    saved = _items(rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=False, threshold_kind="cpa_x", value=5))
    item = saved["spend_pause"]
    assert not item["enabled"] and item["rule_id"] and item["threshold_kind"] == "cpa_x" and item["value"] == 5.0
    rule = next(r for r in rules.list_rules(c["user"]) if r["id"] == item["rule_id"])
    assert not rule["enabled"] and rule["template"] == "spend_pause"
    # Включают без нового порога — остаётся сохранённый 5×, а не 3× по умолчанию.
    on = _items(rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=True))["spend_pause"]
    assert on["enabled"] and on["rule_id"] == item["rule_id"] and on["value"] == 5.0
    assert sum(1 for r in rules.list_rules(c["user"]) if r.get("template") == "spend_pause") == 1


def test_cpa_x_threshold_resolved_at_check(db, cab):
    c = cab
    rule_id = _items(rt.set_protection(c["user"], c["ws"], "spend_notify", enabled=True))["spend_notify"]["rule_id"]
    _spend(db, c, 1500)
    # 2 × 1000 ₽ = 2000 ₽ — 1500 ₽ без заявок ещё не повод.
    assert rules.run_rule(c["user"], rule_id)["matched"] == 0

    _spend(db, c, 600, days_ago=2)
    result = rules.run_rule(c["user"], rule_id)
    assert result["matched"] == 1 and result["error"] == ""
    assert "от цены заявки" in result["items"][0]["reason"]
    assert c["sent"] and c["sent"][-1]["link"] == f"/w/{c['ws']}/campaigns"
    assert c["sent"][-1]["workspace_id"] == c["ws"]

    # Цена заявки выросла — порог поднялся сам.
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET kpi_target = 5000 WHERE id = %s", (c["ws"],))
    assert rules.run_rule(c["user"], rule_id)["matched"] == 0


def test_no_target_cpa_rule_stays_silent(db, cab):
    c = cab
    rule_id = _items(rt.set_protection(c["user"], c["ws"], "spend_notify", enabled=True))["spend_notify"]["rule_id"]
    _spend(db, c, 50000)
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET kpi_type = '', kpi_target = NULL WHERE id = %s", (c["ws"],))
    result = rules.run_rule(c["user"], rule_id)
    assert result["matched"] == 0 and result["error"] == rules.NO_TARGET_CPA
    item = _items(rt.protection_state(c["user"], c["ws"]))["spend_notify"]
    assert item["needs_target"] and item["enabled"]

    # Порог в ценах заявки без цены не задать; включение нового — в рублях.
    with pytest.raises(YandexError) as exc:
        rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=True, threshold_kind="cpa_x", value=2)
    assert exc.value.code == "no-target-cpa"
    pause = _items(rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=True))["spend_pause"]
    assert pause["threshold_kind"] == "rub" and pause["value"] == 5000.0


def test_client_target_cpa_fallback(db, cab):
    c = cab
    with db.connection() as conn:
        client = conn.execute(
            "INSERT INTO clients (user_id, name, kpi_type, kpi_target) VALUES (%s, 'Клиент', 'cpa', 400) RETURNING id",
            (c["user"],),
        ).fetchone()["id"]
        conn.execute(
            "UPDATE workspaces SET kpi_type = '', kpi_target = NULL, client_row_id = %s WHERE id = %s",
            (client, c["ws"]),
        )
    assert rules.project_target_cpa_rub(c["user"], c["ws"]) == Decimal("400")
    assert rt.protection_state(c["user"], c["ws"])["target_cpa_rub"] == 400.0


def test_pause_goes_to_history_link(db, cab, monkeypatch):
    c = cab
    calls = []

    def fake_bulk(user_id, *, campaign_ids, action, daily_budget_rub=None):
        calls.append((campaign_ids, action))
        return {"applied": len(campaign_ids), "errors": []}

    monkeypatch.setattr(rules, "bulk_campaign_action", fake_bulk)
    rule_id = _items(
        rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=True, threshold_kind="rub", value=1000)
    )["spend_pause"]["rule_id"]
    _spend(db, c, 1200)
    result = rules.run_rule(c["user"], rule_id)
    assert result["applied"] == 1 and calls and calls[0][1] == "pause"
    assert c["sent"][-1]["link"] == f"/w/{c['ws']}/history"


def test_default_plan_and_apply(db, cab):
    c = cab
    other = make_workspace(db, c["user"], 2, name="Двери")
    plan = rt.default_plan(c["user"])
    assert plan["total"] == 2 and plan["protected"] == 0
    by_ws = {i["workspace_id"]: i for i in plan["items"]}
    assert len(by_ws[c["ws"]]["checks"]) == 2  # без остановки кампаний
    assert by_ws[other]["target_cpa_rub"] is None
    assert any("3 000" in t.replace("\xa0", " ") for t in by_ws[other]["checks"])

    # Уже защищённый проект кнопка не трогает.
    rt.set_protection(c["user"], c["ws"], "spend_pause", enabled=True)
    plan = rt.default_plan(c["user"])
    assert [i["workspace_id"] for i in plan["items"]] == [other] and plan["protected"] == 1

    done = rt.apply_default(c["user"], [c["ws"], other, 999999])
    assert done == {"projects": 1, "rules": 2}
    templates = {(r["scope_workspace_id"], r["template"]) for r in rules.list_rules(c["user"]) if r.get("template")}
    assert templates == {(c["ws"], "spend_pause"), (other, "spend_notify"), (other, "daily_budget")}
    other_items = _items(rt.protection_state(c["user"], other))
    assert other_items["spend_notify"]["threshold_kind"] == "rub" and other_items["daily_budget"]["enabled"]
    assert rt.default_plan(c["user"])["items"] == []
    # Повторно — ничего.
    assert rt.apply_default(c["user"], [other]) == {"projects": 0, "rules": 0}


def test_api_gated_by_autopilot(db, cab, monkeypatch):
    c = cab
    app.dependency_overrides[require_web_user] = lambda: c["user"]
    try:
        client = TestClient(app)
        monkeypatch.setattr("app.plans.has_feature", lambda user_id, key: key != "autopilot")
        assert client.get(f"/api/web/workspaces/{c['ws']}/budget-protection").json()["allowed"] is False
        assert client.put(f"/api/web/workspaces/{c['ws']}/budget-protection/spend_notify", json={"enabled": True}).status_code >= 400
        assert client.post("/api/web/budget-protection/all", json={"workspace_ids": [c["ws"]]}).status_code >= 400
        # Сохранить порог без автопилота тоже нельзя — иначе появилось бы правило в обход тарифа.
        res = client.put(
            f"/api/web/workspaces/{c['ws']}/budget-protection/spend_pause",
            json={"enabled": False, "threshold_kind": "rub", "value": 7000},
        )
        assert res.status_code >= 400
        assert not any(r.get("template") for r in rules.list_rules(c["user"]))

        monkeypatch.setattr("app.plans.has_feature", lambda user_id, key: True)
        plan = client.get("/api/web/budget-protection/all").json()
        assert plan["allowed"] and [i["workspace_id"] for i in plan["items"]] == [c["ws"]]
        res = client.put(
            f"/api/web/workspaces/{c['ws']}/budget-protection/spend_notify",
            json={"enabled": True, "threshold_kind": "cpa_x", "value": 1.5},
        )
        assert res.status_code == 200
        assert _items(res.json())["spend_notify"]["value"] == 1.5
        # NaN в теле — отказ «Укажите порог числом», а не 500.
        res = client.put(
            f"/api/web/workspaces/{c['ws']}/budget-protection/spend_notify",
            content='{"enabled": true, "threshold_kind": "rub", "value": NaN}',
            headers={"Content-Type": "application/json"},
        )
        assert 400 <= res.status_code < 500
        assert client.post("/api/web/budget-protection/all", json={"workspace_ids": [c["ws"]]}).json()["projects"] == 0
    finally:
        app.dependency_overrides.pop(require_web_user, None)
