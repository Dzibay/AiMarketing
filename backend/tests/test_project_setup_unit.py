"""«Проект не настроен» в сводке: чего не хватает и для каких проектов это показываем (без БД)."""

from datetime import datetime, timedelta, timezone

from app.overview import SETUP_FRESH_DAYS, _setup_problem, setup_missing

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
WHERE = {"workspace_id": 5, "workspace_name": "Ромашка", "client_id": None, "client_name": ""}


def _ws(goal_id=None, url_template="", age_days=1, naive=False):
    created = NOW - timedelta(days=age_days)
    if naive:
        created = created.replace(tzinfo=None)
    return {"id": 5, "goal_id": goal_id, "url_template": url_template, "created_at": created}


def test_missing_lists_goal_and_template():
    assert setup_missing(_ws()) == ["цели", "шаблона ссылки"]
    assert setup_missing(_ws(goal_id=12)) == ["шаблона ссылки"]
    assert setup_missing(_ws(url_template="https://site.ru/?utm_source=yandex")) == ["цели"]
    assert setup_missing(_ws(goal_id=12, url_template="https://site.ru/")) == []
    assert setup_missing(_ws(url_template="   ")) == ["цели", "шаблона ссылки"]


def test_fresh_project_gets_quiet_problem_with_link_target():
    p = _setup_problem(_ws(), NOW, WHERE)
    assert p["severity"] == "info"
    assert p["kind"] == "setup"
    assert p["title"] == "Проект не настроен: нет цели и шаблона ссылки"
    assert p["detail"].startswith("Без цели не посчитать заявки")
    assert p["workspace_id"] == 5

    only_goal = _setup_problem(_ws(url_template="https://site.ru/"), NOW, WHERE)
    assert only_goal["title"] == "Проект не настроен: нет цели"
    assert "шаблона" not in only_goal["detail"]


def test_old_or_ready_projects_are_not_nagged():
    assert _setup_problem(_ws(age_days=SETUP_FRESH_DAYS + 1), NOW, WHERE) is None
    assert _setup_problem(_ws(goal_id=1, url_template="https://site.ru/"), NOW, WHERE) is None
    assert _setup_problem({**_ws(), "created_at": None}, NOW, WHERE) is None


def test_naive_created_at_counts_as_utc():
    assert _setup_problem(_ws(age_days=3, naive=True), NOW, WHERE)["kind"] == "setup"
    assert _setup_problem(_ws(age_days=SETUP_FRESH_DAYS + 2, naive=True), NOW, WHERE) is None
