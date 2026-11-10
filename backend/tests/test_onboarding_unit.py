"""Чек-лист первых шагов без БД: статусы, ссылки, обязательные шаги и скрытие."""

from app.onboarding import build_onboarding, build_wizard


def _facts(**over) -> dict:
    facts = {
        "dismissed": False,
        "telegram_linked": False,
        "direct_connections": 0,
        "direct_accounts": 0,
        "direct_loaded": 0,
        "load_errors": 0,
        "workspaces": [],
    }
    facts.update(over)
    return facts


def _ws(ws_id: int, campaigns: int = 0, template: bool = False, goal: bool = False) -> dict:
    return {"id": ws_id, "name": f"site{ws_id}.ru", "campaigns": campaigns, "has_template": template, "has_goal": goal}


def _steps(result: dict) -> dict:
    return {s["id"]: s for s in result["steps"]}


def _done_facts() -> dict:
    return _facts(
        telegram_linked=True,
        direct_connections=1,
        direct_accounts=2,
        direct_loaded=2,
        workspaces=[_ws(5, campaigns=3, template=True, goal=True)],
    )


def test_new_user_sees_every_step_in_order_and_nothing_done():
    result = build_onboarding(_facts(), telegram_available=True)
    assert [s["id"] for s in result["steps"]] == [
        "direct", "first_load", "workspace", "campaigns", "link_template", "goals", "telegram",
    ]
    assert result["done"] == 0 and result["total"] == 7
    assert result["visible"] and not result["complete"]
    steps = _steps(result)
    assert steps["direct"]["status"] == "todo" and steps["direct"]["to"] == "/cabinet/connections"
    # Шаги, которым нужно проект, ждут его и никуда не ведут.
    for key in ("campaigns", "link_template", "goals"):
        assert steps[key]["status"] == "blocked" and steps[key]["to"] is None
    assert steps["first_load"]["status"] == "blocked"
    # ИИ — общий для всех клиентов: ключ добавлять не нужно.
    assert "ai" not in steps and not steps["direct"]["optional"]


def test_first_load_progress_and_error():
    found = _steps(build_onboarding(_facts(direct_connections=1), telegram_available=True))
    assert found["direct"]["done"]
    assert found["first_load"]["status"] == "progress" and "Ищем кабинеты" in found["first_load"]["detail"]

    loading = _steps(build_onboarding(_facts(direct_connections=1, direct_accounts=3), telegram_available=True))
    assert loading["first_load"]["status"] == "progress"
    assert loading["first_load"]["detail"] == "Загружено кабинетов: 0 из 3"

    failed = _steps(
        build_onboarding(_facts(direct_connections=1, direct_accounts=3, load_errors=1), telegram_available=True)
    )
    assert failed["first_load"]["status"] == "error"

    # Хотя бы один кабинет загружен — шаг сделан, ошибки остальных покажет «Где горит».
    loaded = _steps(
        build_onboarding(
            _facts(direct_connections=1, direct_accounts=3, direct_loaded=1, load_errors=1), telegram_available=True
        )
    )
    assert loaded["first_load"]["done"] and loaded["first_load"]["status"] == "done"


def test_links_lead_to_the_workspace_that_still_needs_the_step():
    workspaces = [
        _ws(1),  # счётчик без кампаний (например, личный сайт)
        _ws(2, campaigns=4, template=True),
        _ws(3, campaigns=2),
    ]
    steps = _steps(build_onboarding(_facts(workspaces=workspaces), telegram_available=True))
    assert steps["workspace"]["done"]
    assert steps["campaigns"]["done"] and steps["campaigns"]["to"] == "/w/1/settings"
    assert steps["link_template"]["done"]
    # Цели нет нигде — ведём в проект, где уже есть кампании.
    assert not steps["goals"]["done"] and steps["goals"]["to"] == "/w/2/settings"


def test_link_template_points_to_the_links_panel():
    steps = _steps(build_onboarding(_facts(workspaces=[_ws(1), _ws(7, campaigns=1)]), telegram_available=True))
    assert steps["link_template"]["to"] == "/w/7/settings#links"
    assert steps["link_template"]["status"] == "todo"


def test_complete_hides_the_card():
    result = build_onboarding(_done_facts(), telegram_available=True)
    assert result["complete"] and not result["visible"]
    assert result["done"] == result["total"]


def test_telegram_step_is_hidden_when_bot_is_not_configured():
    facts = _done_facts()
    facts["telegram_linked"] = False
    with_bot = build_onboarding(facts, telegram_available=True)
    assert not with_bot["complete"] and _steps(with_bot)["telegram"]["to"] == "/cabinet/settings/notifications#telegram"

    without_bot = build_onboarding(facts, telegram_available=False)
    assert "telegram" not in _steps(without_bot)
    assert without_bot["complete"] and not without_bot["visible"]


def test_dismissed_card_stays_hidden_but_reports_progress():
    result = build_onboarding(_facts(dismissed=True, direct_connections=1), telegram_available=True)
    assert result["dismissed"] and not result["visible"]
    assert not result["complete"] and result["done"] == 1


def test_done_steps_carry_no_detail_status():
    steps = build_onboarding(_done_facts(), telegram_available=True)["steps"]
    for step in steps:
        assert step["status"] == ("done" if step["done"] else "todo")


def _wizard(**over) -> dict:
    return {s["id"]: s for s in build_wizard(_facts(**over))["steps"]}


def test_wizard_walks_email_yandex_projects_load_ai():
    result = build_wizard(_facts(email_verified=False))
    assert [s["id"] for s in result["steps"]] == ["email", "yandex", "projects", "load", "ai"]
    assert result["current"] == "email" and not result["complete"] and result["done"] == 0

    steps = _wizard(email_verified=True)
    assert steps["yandex"]["status"] == "todo"
    assert steps["projects"]["status"] == "blocked" and steps["projects"]["detail"] == "Сначала подключите Метрику"
    assert steps["load"]["status"] == "blocked" and steps["ai"]["status"] == "blocked"


def test_wizard_names_the_missing_yandex_role():
    steps = _wizard(email_verified=True, metrika_connections=1)
    assert steps["yandex"]["status"] == "progress" and steps["yandex"]["detail"] == "Осталось подключить Директ"
    assert steps["projects"]["status"] == "todo"
    steps = _wizard(email_verified=True, direct_connections=1, direct_accounts=2, direct_loaded=1)
    assert steps["yandex"]["detail"] == "Осталось подключить Метрику" and steps["load"]["done"]


def test_wizard_complete_after_first_question():
    facts = dict(_done_facts(), email_verified=True, metrika_connections=1)
    assert build_wizard(facts)["current"] == "ai"
    result = build_wizard(dict(facts, asked_ai=True))
    assert result["complete"] and result["current"] is None and result["done"] == 5
    onboarding = build_onboarding(dict(facts, asked_ai=True), telegram_available=True)
    assert onboarding["wizard"]["complete"] and not onboarding["start_wizard"]


def test_start_wizard_only_before_projects_and_not_dismissed():
    assert build_onboarding(_facts(email_verified=True), telegram_available=True)["start_wizard"]
    assert not build_onboarding(_facts(email_verified=True, dismissed=True), telegram_available=True)["start_wizard"]
    with_ws = _facts(email_verified=True, metrika_connections=1, workspaces=[_ws(1)])
    assert not build_onboarding(with_ws, telegram_available=True)["start_wizard"]
