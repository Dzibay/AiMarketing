"""«Для новых проектов» (настройки по умолчанию) и «Взять из проекта…»."""

import pytest
from fastapi.testclient import TestClient

from app import project_defaults as pd, rules_templates as rt, workspaces
from app.main import app
from app.web_auth import require_web_user
from app.yandex import YandexError
from tests.dbutil import make_connection, make_counter, make_user, make_workspace, setup_ai


def _sql(db, query, params=()):
    with db.connection() as conn:
        conn.execute(query, params)


def _prot(user, ws):
    return {i["key"]: i for i in rt.protection_state(user, ws)["items"]}


def test_defaults_roundtrip_and_validation(db):
    user = make_user(db)
    assert pd.get_defaults(user) == {
        "url_template": "",
        "model_class": "",
        "ai_access": None,
        "kpi": None,
        "budget_protection": None,
    }
    setup_ai(fast="lite")
    saved = pd.set_defaults(
        user,
        {
            "url_template": "https://old.ru/?utm_source=yandex&utm_campaign={campaign_id}",
            "model_class": "fast",
            "kpi": {"kpi_type": "cpa", "kpi_target": 1200},
        },
    )
    assert saved["url_template"] == "/?utm_source=yandex&utm_campaign={campaign_id}"
    assert saved["model_class"] == "fast" and saved["kpi"] == {"kpi_type": "cpa", "kpi_target": 1200.0}
    # Меняется только присланное; null — «не задавать».
    again = pd.set_defaults(user, {"kpi": None})
    assert again["kpi"] is None and again["model_class"] == "fast"
    with pytest.raises(YandexError):
        pd.set_defaults(user, {"model_class": "thinking"})  # выключена админом
    with pytest.raises(YandexError):
        pd.set_defaults(user, {"url_template": "utm_source=a&utm_source=b"})


def test_new_project_gets_defaults(db):
    user = make_user(db)
    conn_id = make_connection(db, user)
    setup_ai(fast="lite")
    access = {"full_access": False, "categories": {"negatives": True}, "max_change_pct": 15}
    pd.set_defaults(
        user,
        {
            "url_template": "ref=Yandex&utm_source=yandex",
            "model_class": "fast",
            "ai_access": access,
            "kpi": {"kpi_type": "cpa", "kpi_target": 800},
            "budget_protection": {
                "spend_notify": {"enabled": True, "threshold_kind": "cpa_x", "value": 2.5},
                "daily_budget": {"enabled": True},
            },
        },
    )
    make_counter(db, user, conn_id, 42, site="okna.ru")
    created = workspaces.ensure_workspaces(user)
    assert len(created) == 1
    ws = workspaces.get_workspace(user, created[0])
    assert ws["url_template"] == "https://okna.ru/?ref=Yandex&utm_source=yandex"
    assert ws["ai_style"]["model_class"] == "fast" and not ws["ai_tone_own"]
    assert ws["ai_access"]["categories"]["negatives"] and ws["ai_access"]["max_change_pct"] == 15
    assert ws["kpi_type"] == "cpa" and ws["kpi_target"] == 800.0
    prot = _prot(user, created[0])
    assert prot["spend_notify"]["enabled"] and prot["spend_notify"]["threshold_kind"] == "cpa_x"
    assert prot["spend_notify"]["value"] == 2.5
    assert prot["daily_budget"]["enabled"] and not prot["spend_pause"]["enabled"]

    # Выбранный вручную счётчик — тоже новый проект; без сайта шаблон ссылки не задаётся.
    make_counter(db, user, conn_id, 43, site="")
    _sql(db, "UPDATE users SET project_defaults = project_defaults || '{\"kpi\": null}'::jsonb WHERE id = %s", (user,))
    choices = {c["counter_id"]: c for c in workspaces.counter_choices(user)["items"]}
    assert choices[43]["state"] == "available"
    second = workspaces.create_project_for_counter(user, 43)
    assert second["url_template"] == "" and second["kpi_type"] == ""
    # Без цены заявки порог «в ценах заявки» не задать — включается рублёвый по умолчанию.
    prot2 = _prot(user, second["id"])
    assert prot2["spend_notify"]["enabled"] and prot2["spend_notify"]["threshold_kind"] == "rub"


def test_existing_projects_untouched_and_broken_default_skipped(db):
    user = make_user(db)
    conn_id = make_connection(db, user)
    old = make_workspace(db, user, 41)
    setup_ai(fast="lite")
    pd.set_defaults(user, {"model_class": "fast", "kpi": {"kpi_type": "drr", "kpi_target": 15}})
    # Админ выключил класс после сохранения — модель пропускается, KPI применяется.
    setup_ai(fast="")
    make_counter(db, user, conn_id, 42, site="a.ru")
    created = workspaces.ensure_workspaces(user)
    ws = workspaces.get_workspace(user, created[0])
    assert ws["ai_style"]["model_class"] == "" and ws["kpi_type"] == "drr" and ws["kpi_target"] == 15.0
    assert workspaces.get_workspace(user, old)["kpi_type"] == ""


def test_save_as_defaults(db):
    user = make_user(db)
    ws = make_workspace(db, user, 1, kpi_type="cpa", kpi_target=500)
    workspaces.update_workspace(user, ws, url_template="https://okna.ru/sale?utm_source=yandex")
    rt.set_protection(user, ws, "spend_pause", enabled=True, threshold_kind="rub", value=7000)
    out = pd.save_as_defaults(user, ws, ["url_template", "kpi", "budget_protection", "unknown"])
    assert out["url_template"] == "/sale?utm_source=yandex"
    assert out["kpi"] == {"kpi_type": "cpa", "kpi_target": 500.0}
    assert out["budget_protection"] == {"spend_pause": {"enabled": True, "threshold_kind": "rub", "value": 7000.0}}
    with pytest.raises(YandexError):
        pd.save_as_defaults(user, ws, ["nope"])
    other = make_user(db, email="o@example.com")
    with pytest.raises(YandexError) as exc:
        pd.save_as_defaults(other, ws, ["kpi"])
    assert exc.value.code == "not-found"


def _seed_pair(db):
    user = make_user(db)
    conn_id = make_connection(db, user)
    c1 = make_counter(db, user, conn_id, 1, site="source.ru")
    c2 = make_counter(db, user, conn_id, 2, site="target.ru")
    src = make_workspace(db, user, 1, "Источник", counter_row_id=c1, kpi_type="cpa", kpi_target=1000)
    dst = make_workspace(db, user, 2, "Новый", counter_row_id=c2)
    _sql(db, "UPDATE workspaces SET site = 'target.ru' WHERE id = %s", (dst,))
    workspaces.update_workspace(
        user,
        src,
        url_template="https://source.ru/?ref=Yandex&utm_source=yandex",
        ai_style={"tone": "sell", "address": "ty"},
        ai_access={"categories": {"bids": True}, "max_change_pct": 10},
        ai_notes="Без скидок",
    )
    workspaces.remember_ai_fact(user, src, key="zero_cost_scheme", question="Как считаются заявки?", answer="Звонки")
    rt.set_protection(user, src, "spend_notify", enabled=True, threshold_kind="cpa_x", value=3)
    return user, src, dst


def test_copy_preview_and_apply(db):
    user, src, dst = _seed_pair(db)
    preview = pd.copy_preview(user, dst, src)
    assert preview["source"] == {"id": src, "name": "Источник"}
    items = {i["kind"]: i for i in preview["items"]}
    assert items["url_template"]["before"] == "Не задан"
    assert items["url_template"]["after"] == "https://target.ru/?ref=Yandex&utm_source=yandex"
    assert items["ai_style"]["after"] == "Продающий, на «ты»" and not items["ai_style"]["same"]
    assert items["model_class"]["same"]
    assert items["kpi"]["before"] == "Не задан" and items["kpi"]["after"].startswith("Цена заявки")
    assert items["budget_protection"]["before"] == "Выключена" and "Сообщить" in items["budget_protection"]["after"]
    assert items["ai_facts"]["after"] == "1 ответ (добавится 1)"
    # Без цены заявки у проекта порог показан в рублях; если перенести и KPI — в ценах заявки источника.
    assert "3 000" in items["budget_protection"]["after"].replace("\xa0", " ")
    assert items["budget_protection"]["note"].startswith("Если перенести и KPI: ") and "3 цены заявки" in items["budget_protection"]["note"]

    res = pd.copy_apply(user, dst, src, ["url_template", "ai_style", "ai_access", "kpi", "budget_protection", "ai_notes", "ai_facts"])
    assert {a["kind"] for a in res["applied"]} == {
        "url_template",
        "ai_style",
        "ai_access",
        "kpi",
        "budget_protection",
        "ai_notes",
        "ai_facts",
    }
    ws = res["workspace"]
    assert ws["url_template"] == "https://target.ru/?ref=Yandex&utm_source=yandex"
    assert ws["ai_style"]["tone"] == "sell" and ws["ai_style"]["address"] == "ty"
    assert ws["ai_access"]["categories"]["bids"] and ws["ai_access"]["max_change_pct"] == 10
    assert ws["kpi_type"] == "cpa" and ws["kpi_target"] == 1000.0
    assert ws["ai_notes"] == "Без скидок" and [f["key"] for f in ws["ai_facts"]] == ["zero_cost_scheme"]
    prot = _prot(user, dst)
    assert prot["spend_notify"]["enabled"] and prot["spend_notify"]["value"] == 3.0
    # Повтор ничего не дублирует.
    pd.copy_apply(user, dst, src, ["budget_protection", "ai_facts"])
    assert len(workspaces.get_workspace(user, dst)["ai_facts"]) == 1
    assert sum(1 for i in rt.protection_state(user, dst)["items"] if i["rule_id"]) == 1

    # В источнике защиту выключили — перенос выключает её и здесь.
    rt.set_protection(user, src, "spend_notify", enabled=False)
    pd.copy_apply(user, dst, src, ["budget_protection"])
    assert not _prot(user, dst)["spend_notify"]["enabled"]


def test_copy_facts_keep_own_answers(db):
    user, src, dst = _seed_pair(db)
    for i in range(workspaces.MAX_AI_FACTS):
        workspaces.remember_ai_fact(user, dst, key=f"own_{i}", question=f"Вопрос {i}?", answer="Ответ")
    item = {i["kind"]: i for i in pd.copy_preview(user, dst, src)["items"]}["ai_facts"]
    # Память полна — переносить нечего, и об этом сказано, а не «уже так же».
    assert not item["available"] and "Не поместятся: 1 ответ" in item["note"]
    res = pd.copy_apply(user, dst, src, ["ai_facts"])
    assert res["skipped"][0]["kind"] == "ai_facts"
    keys = [f["key"] for f in workspaces.get_workspace(user, dst)["ai_facts"]]
    assert keys == [f"own_{i}" for i in range(workspaces.MAX_AI_FACTS)]


def test_copy_protection_without_cpa_uses_default_rub(db):
    user, src, dst = _seed_pair(db)
    rt.set_protection(user, dst, "spend_notify", enabled=True, threshold_kind="rub", value=10000)
    item = {i["kind"]: i for i in pd.copy_preview(user, dst, src)["items"]}["budget_protection"]
    assert "3 000" in item["after"].replace(" ", " ")
    pd.copy_apply(user, dst, src, ["budget_protection"])
    prot = _prot(user, dst)["spend_notify"]
    # Как в предпросмотре: рублёвый порог по умолчанию, а не прежние 10 000 ₽.
    assert prot["enabled"] and prot["threshold_kind"] == "rub" and prot["value"] == 3000.0


def test_copy_checks(db):
    user, src, dst = _seed_pair(db)
    with pytest.raises(YandexError):
        pd.copy_apply(user, dst, src, [])
    with pytest.raises(YandexError) as exc:
        pd.copy_preview(user, dst, dst)
    assert exc.value.code == "same-project"
    other = make_user(db, email="o@example.com")
    foreign = make_workspace(db, other, 9)
    for target, source in ((dst, foreign), (foreign, src)):
        with pytest.raises(YandexError) as exc:
            pd.copy_preview(user, target, source)
        assert exc.value.code == "not-found"
    # Без сайта у проекта шаблон ссылки не переносится — остальное переносится.
    _sql(db, "UPDATE workspaces SET site = '' WHERE id = %s", (dst,))
    _sql(db, "UPDATE metrika_counters SET site = '' WHERE counter_id = 2")
    res = pd.copy_apply(user, dst, src, ["url_template", "ai_notes"])
    assert [s["kind"] for s in res["skipped"]] == ["url_template"]
    assert [a["kind"] for a in res["applied"]] == ["ai_notes"]
    assert res["workspace"]["url_template"] == ""


def test_api(db):
    user, src, dst = _seed_pair(db)
    other = make_user(db, email="o@example.com")
    app.dependency_overrides[require_web_user] = lambda: user
    try:
        client = TestClient(app)
        got = client.get("/api/web/me/project-defaults").json()
        assert got["defaults"]["kpi"] is None and got["protection"] and got["ai_categories"]
        put = client.put("/api/web/me/project-defaults", json={"kpi": {"kpi_type": "drr", "kpi_target": 20}})
        assert put.status_code == 200 and put.json()["defaults"]["kpi"]["kpi_type"] == "drr"
        bad = client.put("/api/web/me/project-defaults", json={"kpi": {"kpi_type": "drr", "kpi_target": -5}})
        assert bad.status_code == 400
        as_def = client.post(f"/api/web/workspaces/{src}/as-defaults", json={"kinds": ["url_template"]})
        assert as_def.json()["defaults"]["url_template"] == "/?ref=Yandex&utm_source=yandex"
        prev = client.get(f"/api/web/workspaces/{dst}/copy-from/{src}")
        assert prev.status_code == 200 and len(prev.json()["items"]) == len(pd.COPY_KINDS)
        done = client.post(f"/api/web/workspaces/{dst}/copy-from", json={"source_id": src, "kinds": ["ai_notes"]})
        assert done.status_code == 200 and done.json()["workspace"]["ai_notes"] == "Без скидок"
        app.dependency_overrides[require_web_user] = lambda: other
        assert client.get(f"/api/web/workspaces/{dst}/copy-from/{src}").status_code == 404
        assert client.post(f"/api/web/workspaces/{src}/as-defaults", json={"kinds": ["kpi"]}).status_code == 404
    finally:
        app.dependency_overrides.pop(require_web_user, None)
