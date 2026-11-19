"""Бесплатные правила после загрузки запросов и «Разметить все проекты» — без базы и без ИИ."""

import math

import pytest

from app import job_handlers
from app import query_cleaning as qc
from app.yandex import YandexError


def _row(norm, cost=0, campaigns=(1,)):
    return {
        "norm": norm, "query": norm, "cost_kopecks": cost, "clicks": 1, "conversions": 0,
        "campaign_row_ids": list(campaigns), "adgroup_row_ids": [], "matched": [],
    }


class _Cur:
    def __init__(self, rows=None, rowcount=0):
        self._rows, self.rowcount = rows or [], rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Pool:
    """Подмена pool: выборка очереди отдаёт строки, вставка меток — записывается."""

    def __init__(self, rows):
        self.rows, self.selects, self.inserts = rows, [], []

    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("INSERT INTO query_labels"):
            self.inserts.append((sql, params))
            return _Cur(rowcount=len(params[2]))
        self.selects.append(sql)
        return _Cur(self.rows)


@pytest.fixture
def no_ai(monkeypatch):
    """Любой вызов ИИ или проверка лимита — провал теста."""

    def boom(*a, **kw):
        raise AssertionError("ИИ вызывать нельзя")

    for name in ("load_config", "complete_json", "check_budget", "budget_left"):
        monkeypatch.setattr(qc.ai, name, boom)


# --- Правила после загрузки ---------------------------------------------------------------------


def test_rules_after_load_no_ai_and_only_empty_places(monkeypatch, no_ai):
    fake = _Pool([_row("кухни икеа", 1000), _row("кухни бесплатно", 500), _row("шкаф вакансии", 300), _row("кухни угловые", 100)])
    monkeypatch.setattr(qc, "pool", fake)
    monkeypatch.setattr(qc, "_loaded_workspaces", lambda *a: [7])
    monkeypatch.setattr(qc, "require_workspace", lambda u, w: {"id": w, "brief": {"not_ours": ["вакансии"]}})
    monkeypatch.setattr(
        qc, "_negatives_index",
        lambda u, c, g: {"campaigns": {1: [("икеа", qc.stems("икеа"))]}, "adgroups": {}, "group_campaign": {}},
    )
    monkeypatch.setattr(qc, "_memory_rules", lambda w: [("бесплатно", qc.stems("бесплатно"), "adgroup", "двери бесплатно")])

    assert qc.label_after_load(1, campaign_row_ids=[5]) == 3
    # Берём только запросы без метки; переразметки неподтверждённых нет.
    assert "l.id IS NULL" in fake.selects[0] and "confirmed = FALSE" not in fake.selects[0]
    ((sql, params),) = fake.inserts
    # Только в пустые места: метку ИИ и решение маркетолога не перетираем, сами — неподтверждённые подсказки.
    assert "DO NOTHING" in sql and "DO UPDATE" not in sql and "FALSE" in sql
    assert params[:2] == (1, 7)
    assert dict(zip(params[2], params[8])) == {"кухни икеа": "direct", "кухни бесплатно": "memory", "шкаф вакансии": "brief"}
    assert set(params[4]) == {"negative"} and "кухни угловые" not in params[2]


def test_rules_after_load_nothing_to_store(monkeypatch, no_ai):
    fake = _Pool([_row("кухни угловые")])
    monkeypatch.setattr(qc, "pool", fake)
    monkeypatch.setattr(qc, "_loaded_workspaces", lambda *a: [7])
    monkeypatch.setattr(qc, "require_workspace", lambda u, w: {"id": w, "brief": {}})
    monkeypatch.setattr(qc, "_negatives_index", lambda u, c, g: {"campaigns": {}, "adgroups": {}, "group_campaign": {}})
    monkeypatch.setattr(qc, "_memory_rules", lambda w: [])
    assert qc.label_after_load(1, workspace_ids=[7]) == 0 and fake.inserts == []


def test_rules_after_load_never_breaks_the_load(monkeypatch, no_ai):
    calls = []

    def flaky(user_id, workspace_id):
        calls.append(workspace_id)
        if workspace_id == 1:
            raise RuntimeError("база недоступна")
        return 4

    monkeypatch.setattr(qc, "_loaded_workspaces", lambda *a: [1, 2])
    monkeypatch.setattr(qc, "label_by_rules", flaky)
    assert qc.label_after_load(9, workspace_ids=[1, 2]) == 4 and calls == [1, 2]

    def broken(*a):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(qc, "_loaded_workspaces", broken)
    assert qc.label_after_load(9, account_id=3) == 0


def test_load_jobs_run_free_rules(monkeypatch):
    seen = []
    monkeypatch.setattr(job_handlers, "label_after_load", lambda user_id, **kw: seen.append(kw) or 2)
    monkeypatch.setattr(job_handlers, "sync_campaign_search_queries", lambda u, c, days: {"queries": 5})
    monkeypatch.setattr(job_handlers, "sync_campaign_entity_stats", lambda u, c, days: {})
    res = job_handlers._sync_search_queries(1, {"campaign_row_id": 11}, lambda *a: None)
    assert res["rules"] == 2 and res["status"] == "ok" and seen[-1] == {"campaign_row_ids": [11]}

    monkeypatch.setattr(job_handlers, "sync_workspace_queries", lambda u, p, progress: {"queries": 3})
    res = job_handlers._sync_workspace_queries(1, {"workspace_id": 7}, lambda *a: None)
    assert res == {"queries": 3, "rules": 2} and seen[-1] == {"workspace_ids": [7]}

    # Загрузка кабинета: правила — только если этап запросов прошёл.
    monkeypatch.setattr(job_handlers, "run_account", lambda u, a, p: {"status": "ok", "mode": "refresh", "stages": ["campaigns", "queries"]})
    assert job_handlers._load_account(1, {"account_id": 4}, lambda *a: None)["rules"] == 2
    assert seen[-1] == {"account_id": 4}
    n = len(seen)
    monkeypatch.setattr(job_handlers, "run_account", lambda u, a, p: {"status": "ok", "mode": "refresh", "stages": ["campaigns"]})
    assert "rules" not in job_handlers._load_account(1, {"account_id": 4}, lambda *a: None)
    monkeypatch.setattr(job_handlers, "run_account", lambda u, a, p: {"status": "units", "stage": "queries"})
    assert "rules" not in job_handlers._load_account(1, {"account_id": 4}, lambda *a: None)
    assert len(seen) == n
    assert job_handlers.HANDLERS["classify_all_queries"] is job_handlers._classify_all_queries


# --- Разметить все проекты ----------------------------------------------------------------------


_PROJECTS = [
    {"id": 1, "name": "Кухни", "new": 10, "cost": 900},
    {"id": 2, "name": "Двери", "new": 3, "cost": 200},
    {"id": 3, "name": "Шкафы", "new": 2, "cost": 50},
]


def _done(labeled, negative, target, left, ai_error=None):
    return {"labeled": labeled, "negative": negative, "target": target, "rules": 1, "ai": True, "ai_error": ai_error, "left": left, "usage": None}


def test_classify_all_in_order_and_rules_only_after_ai_error(monkeypatch):
    monkeypatch.setattr(qc, "_pending_projects", lambda u: list(_PROJECTS))
    calls = []

    def fake(user_id, payload, progress):
        calls.append((payload["workspace_id"], payload["limit"], payload["rules_only"]))
        progress(0, 1, "ИИ размечает запросы")
        if payload["workspace_id"] == 1:
            return _done(8, 5, 3, 2)
        if payload["workspace_id"] == 2:
            raise YandexError("units-exhausted", "Лимит ИИ на этот месяц исчерпан")
        return _done(1, 1, 0, 1)

    monkeypatch.setattr(qc, "classify_queries", fake)
    progress = []
    res = qc.classify_all_queries(5, {}, lambda *a: progress.append(a))
    # Проекты — по очереди; после сбоя ИИ остальные — только правилами.
    assert calls == [(1, qc.ALL_LIMIT, False), (2, qc.ALL_LIMIT, False), (3, qc.ALL_LIMIT, True)]
    assert res["projects"] == 2 and res["labeled"] == 9 and res["negative"] == 6 and res["target"] == 3
    assert res["skipped"] == 1 and res["left"] == 2 + 3 + 1
    assert res["ai_error"] == "Лимит ИИ на этот месяц исчерпан" and res["usage"] is None
    assert [i["id"] for i in res["items"]] == [1, 2, 3]
    assert (0, 3, "Кухни — ИИ размечает запросы") in progress and (1, 3, "Двери — ИИ размечает запросы") in progress
    assert progress[-1] == (3, 3, "Готово")


def test_classify_all_stops_ai_when_limit_runs_out_midway(monkeypatch):
    monkeypatch.setattr(qc, "_pending_projects", lambda u: list(_PROJECTS))
    calls = []

    def fake(user_id, payload, progress):
        calls.append(payload["rules_only"])
        return _done(4, 2, 2, 6, ai_error="Не хватит лимита") if payload["workspace_id"] == 1 else _done(0, 0, 0, 3)

    monkeypatch.setattr(qc, "classify_queries", fake)
    res = qc.classify_all_queries(5)
    assert calls == [False, True, True]
    assert res["projects"] == 1 and res["labeled"] == 4 and res["ai_error"] == "Не хватит лимита" and res["skipped"] == 0


def test_classify_all_nothing_pending(monkeypatch):
    monkeypatch.setattr(qc, "_pending_projects", lambda u: [])
    monkeypatch.setattr(qc, "classify_queries", lambda *a, **kw: pytest.fail("размечать нечего"))
    res = qc.classify_all_queries(5, {})
    assert res["projects"] == 0 and res["labeled"] == 0 and res["items"] == [] and res["usage"] is None


def _plan(rules, rest):
    labels = [{"verdict": "negative"}] * rules
    rows = [{"norm": f"q{i}"} for i in range(rest)]
    return {"rows": labels + rows, "labels": labels, "rest": rows}


@pytest.fixture
def estimate_env(monkeypatch):
    """Три проекта: 1 правило + 80 в ИИ (две пачки), 40 в ИИ (одна пачка), 1 правило. Пачка — 1 единица."""
    plans = {1: _plan(1, 80), 2: _plan(0, 40), 3: _plan(1, 0)}
    monkeypatch.setattr(qc, "_pending_projects", lambda u: list(_PROJECTS))
    monkeypatch.setattr(qc, "_classify_plan", lambda u, payload: plans[payload["workspace_id"]])

    def needs(user_id, plan, cfg):
        batches = [plan["rest"][i : i + qc.AI_BATCH] for i in range(0, len(plan["rest"]), qc.AI_BATCH)]
        return batches, [1.0] * math.ceil(len(plan["rest"]) / qc.AI_BATCH), False

    monkeypatch.setattr(qc, "_plan_needs", needs)
    monkeypatch.setattr(qc.ai, "load_config", lambda user_id, task: {"user_id": user_id})
    monkeypatch.setattr(qc.ai, "check_budget", lambda cfg, need=0.0: None)
    monkeypatch.setattr(qc.billing, "share", lambda user_id, units: round(units * 10, 2))
    monkeypatch.setattr(qc.ai, "complete_json", lambda *a, **kw: pytest.fail("оценка без вызова ИИ"))


def test_estimate_all_counts_projects_that_fit(monkeypatch, estimate_env):
    monkeypatch.setattr(qc.ai, "budget_left", lambda cfg: 2.5)
    est = qc.estimate_classify_all(5)
    assert [(p["id"], p["queries"], p["rules"], p["ai"], p["fits"]) for p in est["projects"]] == [
        (1, 81, 1, 80, True), (2, 40, 0, 40, False), (3, 1, 1, 0, True),
    ]
    assert est["projects"][0]["percent"] == 20.0 and est["projects"][2]["percent"] is None
    assert est["queries"] == 122 and est["rules"] == 2 and est["ai"] == 120
    assert est["percent"] == 30.0 and est["left_percent"] == 25.0
    # Первый проект влез, второй — нет; третьему хватит правил.
    assert est["fits_projects"] == 2 and est["fits_queries"] == 81 + 1
    assert est["ai_error"] is None


def test_estimate_all_without_limit_fits_everything(monkeypatch, estimate_env):
    monkeypatch.setattr(qc.ai, "budget_left", lambda cfg: None)
    est = qc.estimate_classify_all(5)
    assert est["fits_projects"] == 3 and est["fits_queries"] == 122 and est["left_percent"] is None


def test_estimate_all_when_ai_unavailable(monkeypatch, estimate_env):
    def off(user_id, task):
        raise YandexError("ai-not-configured", "Подключите ИИ в настройках")

    monkeypatch.setattr(qc.ai, "load_config", off)
    est = qc.estimate_classify_all(5)
    assert est["ai_error"] == "Подключите ИИ в настройках" and est["percent"] is None
    assert est["fits_projects"] == 1 and est["fits_queries"] == 2

    # Лимит исчерпан: объём считаем, но в ИИ ничего не уйдёт.
    monkeypatch.setattr(qc.ai, "load_config", lambda user_id, task: {"user_id": user_id})
    monkeypatch.setattr(qc.ai, "budget_left", lambda cfg: 0.0)

    def exhausted(cfg, need=0.0):
        raise YandexError("units-exhausted", "Лимит ИИ на этот месяц исчерпан")

    monkeypatch.setattr(qc.ai, "check_budget", exhausted)
    est = qc.estimate_classify_all(5)
    assert est["ai_error"] == "Лимит ИИ на этот месяц исчерпан" and est["percent"] == 30.0
    assert est["left_percent"] == 0.0 and est["fits_projects"] == 1 and est["fits_queries"] == 2


def test_estimate_all_nothing_pending(monkeypatch, no_ai):
    monkeypatch.setattr(qc, "_pending_projects", lambda u: [])
    est = qc.estimate_classify_all(5)
    assert est["projects"] == [] and est["queries"] == 0 and est["percent"] is None


# --- Правки после ревью ---------------------------------------------------------------------------


def test_chat_refresh_runs_free_rules(monkeypatch):
    """Загрузка запросов из чата ИИ — те же бесплатные правила, что после кнопки."""
    from app import ai_agent
    from app import search_queries

    seen = []

    class Ctx:
        user_id = 3

        def campaign(self, cid):
            seen.append(("campaign", cid))

    monkeypatch.setattr(search_queries, "sync_campaign_search_queries", lambda u, c, days: {"queries": 4, "days": days})
    monkeypatch.setattr(qc, "label_after_load", lambda user_id, **kw: seen.append((user_id, kw)) or 2)
    res = ai_agent._t_refresh_queries(Ctx(), {"campaign_row_id": 11, "days": 7})
    assert res == {"queries": 4, "days": 7, "rules": 2}
    assert seen == [("campaign", 11), (3, {"campaign_row_ids": [11]})]


def test_classify_all_keeps_ai_after_project_error(monkeypatch):
    """Проект удалили или закрыли посреди задачи — его пропускаем, ИИ для остальных не выключаем."""
    monkeypatch.setattr(qc, "_pending_projects", lambda u: list(_PROJECTS))
    calls = []

    def fake(user_id, payload, progress):
        calls.append((payload["workspace_id"], payload["rules_only"]))
        if payload["workspace_id"] == 1:
            raise YandexError("not-found")
        if payload["workspace_id"] == 2:
            raise YandexError("busy", "Запросы этого проекта уже размечаются")
        return _done(2, 1, 1, 0)

    monkeypatch.setattr(qc, "classify_queries", fake)
    res = qc.classify_all_queries(5, {})
    assert calls == [(1, False), (2, False), (3, False)]
    assert res["ai_error"] is None and res["skipped"] == 2 and res["projects"] == 1 and res["labeled"] == 2


@pytest.mark.parametrize(
    "code", ["units-exhausted", "subscription-expired", "ai-not-configured", "ai-balance", "ai-timeout", "bridge-network"]
)
def test_ai_and_limit_errors_stop_ai(code):
    assert qc.ai_stopped(YandexError(code, "x"))


@pytest.mark.parametrize("code", ["not-found", "project-locked", "busy", "direct"])
def test_project_errors_keep_ai(code):
    assert not qc.ai_stopped(YandexError(code, "x"))


def test_rules_after_load_scan_is_capped(monkeypatch, no_ai):
    """После каждой загрузки правила проходят только верх очереди: загрузку не тормозим."""
    params = []

    class Pool(_Pool):
        def execute(self, sql, p=()):
            params.append(p)
            return super().execute(sql, p)

    monkeypatch.setattr(qc, "pool", Pool([_row("кухни угловые")]))
    monkeypatch.setattr(qc, "require_workspace", lambda u, w: {"id": w, "brief": {}})
    monkeypatch.setattr(qc, "_negatives_index", lambda u, c, g: {"campaigns": {}, "adgroups": {}, "group_campaign": {}})
    monkeypatch.setattr(qc, "_memory_rules", lambda w: [])
    assert qc.label_by_rules(1, 7) == 0
    assert params[0][-1] == qc.RULES_MAX and qc.RULES_MAX <= 1000


def test_estimate_all_tells_the_per_project_cap(monkeypatch, estimate_env):
    """За раз — до ALL_LIMIT запросов на проект: оценка отдаёт и сколько всего ждёт, и этот предел."""
    monkeypatch.setattr(qc.ai, "budget_left", lambda cfg: None)
    est = qc.estimate_classify_all(5)
    assert est["per_project"] == qc.ALL_LIMIT
    # Двери: в очереди 3, а план отдал 40 — new не меньше того, что возьмём.
    assert [(p["queries"], p["new"]) for p in est["projects"]] == [(81, 81), (40, 40), (1, 2)]
