"""Чек-лист первых шагов на настоящем Postgres: факты из таблиц и скрытие у пользователя."""

from app import onboarding
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace


def _steps(result: dict) -> dict:
    return {s["id"]: s for s in result["steps"]}


def test_facts_come_from_existing_tables(db):
    user = make_user(db)
    assert onboarding.load_facts(user) == {
        "dismissed": False,
        "telegram_linked": False,
        "email_verified": False,
        "asked_ai": False,
        "decided_ai": False,
        "metrika_connections": 0,
        "direct_connections": 0,
        "direct_accounts": 0,
        "direct_loaded": 0,
        "load_errors": 0,
        "workspaces": [],
        "role": "",
    }

    conn_id = make_connection(db, user)
    loaded = make_account(db, user, conn_id, login="one")
    failed = make_account(db, user, conn_id, login="two")
    off = make_account(db, user, conn_id, login="off")
    camp = make_campaign(db, user, loaded, 101)
    ws = make_workspace(db, user, 1, "site.ru", campaigns=[camp], goal_id=77)
    archived = make_workspace(db, user, 2, "old.ru")
    with db.connection() as conn:
        conn.execute("UPDATE direct_accounts SET loaded_at = NOW() WHERE id = %s", (loaded,))
        conn.execute(
            """UPDATE direct_accounts SET load_state = '{"campaigns": {"status": "error"}}'::jsonb WHERE id = %s""",
            (failed,),
        )
        conn.execute("UPDATE direct_accounts SET enabled = FALSE WHERE id = %s", (off,))
        conn.execute("UPDATE workspaces SET archived_at = NOW() WHERE id = %s", (archived,))
        conn.execute("UPDATE workspaces SET url_template = 'https://site.ru/?ref=Yandex' WHERE id = %s", (ws,))
        conn.execute("UPDATE users SET telegram_chat_id = '42', email_verified_at = NOW() WHERE id = %s", (user,))
        chat = conn.execute(
            "INSERT INTO ai_chats (user_id, workspace_id) VALUES (%s, NULL) RETURNING id", (user,)
        ).fetchone()["id"]
        msg = conn.execute(
            "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, 'user', 'С чего начать?') RETURNING id", (chat,)
        ).fetchone()["id"]
        # Сам ИИ выполнил правку (полный доступ) — это не решение пользователя.
        conn.execute(
            "INSERT INTO ai_actions (chat_id, message_id, user_id, status, auto) VALUES (%s, %s, %s, 'done', TRUE)",
            (chat, msg, user),
        )
    assert not onboarding.load_facts(user)["decided_ai"]
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO ai_actions (chat_id, message_id, user_id, status) VALUES (%s, %s, %s, 'rejected')",
            (chat, msg, user),
        )

    facts = onboarding.load_facts(user)
    assert facts["direct_connections"] == 1
    assert facts["direct_accounts"] == 2 and facts["direct_loaded"] == 1 and facts["load_errors"] == 1
    assert facts["telegram_linked"] and facts["email_verified"] and facts["asked_ai"] and facts["decided_ai"]
    assert facts["metrika_connections"] == 1
    assert facts["workspaces"] == [
        {"id": ws, "name": "site.ru", "campaigns": 1, "has_template": True, "has_goal": True, "locked": False}
    ]


def test_projects_over_the_plan_limit_are_locked(db):
    """Проект сверх лимита тарифа (новее) закрыт: шаблон и цель по нему чек-лист не ждёт."""
    from app import plans

    user = make_user(db)
    first = make_workspace(db, user, 1, "first.ru", goal_id=7)
    second = make_workspace(db, user, 2, "second.ru")
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET url_template = 'https://first.ru/?utm_source=yandex' WHERE id = %s", (first,))
    plans.set_subscription(user, overrides={"projects_limit": 1})
    facts = onboarding.load_facts(user)
    assert {w["id"]: w["locked"] for w in facts["workspaces"]} == {first: False, second: True}
    steps = _steps(onboarding.build_onboarding(facts, telegram_available=False))
    assert steps["link_template"]["done"] and steps["goals"]["done"]

    plans.set_subscription(user, overrides={"projects_limit": None})
    steps = _steps(onboarding.get_onboarding(user))
    assert not steps["link_template"]["done"]
    assert steps["link_template"]["detail"] == "Готово в 1 из 2 проектов, осталось: «second.ru»"


def test_other_users_data_does_not_count(db):
    user = make_user(db, "a@example.com")
    other = make_user(db, "b@example.com")
    acc = make_account(db, other, make_connection(db, other))
    make_workspace(db, other, 1, campaigns=[make_campaign(db, other, acc, 1)])
    facts = onboarding.load_facts(user)
    assert facts["direct_connections"] == 0 and facts["workspaces"] == []


def test_dismissal_is_stored_per_user(db):
    user = make_user(db, "a@example.com")
    other = make_user(db, "b@example.com")
    assert onboarding.get_onboarding(user)["visible"]

    hidden = onboarding.set_onboarding_dismissed(user)
    assert hidden["dismissed"] and not hidden["visible"]
    assert onboarding.get_onboarding(user)["dismissed"]
    assert onboarding.get_onboarding(other)["visible"]

    restored = onboarding.set_onboarding_dismissed(user, False)
    assert not restored["dismissed"] and restored["visible"]
    assert _steps(restored)["direct"]["status"] == "todo"


def test_first_login_leads_to_wizard_until_projects_exist(db):
    user = make_user(db)
    state = onboarding.get_onboarding(user)
    assert state["start_wizard"] and state["wizard"]["current"] == "role"

    with db.connection() as conn:
        conn.execute("""UPDATE users SET ui = jsonb_build_object('role', 'pro') WHERE id = %s""", (user,))
    assert onboarding.get_onboarding(user)["wizard"]["current"] == "email"

    with db.connection() as conn:
        conn.execute("UPDATE users SET email_verified_at = NOW() WHERE id = %s", (user,))
    assert onboarding.get_onboarding(user)["wizard"]["current"] == "yandex"

    conn_id = make_connection(db, user)
    make_account(db, user, conn_id)
    state = onboarding.get_onboarding(user)
    assert state["wizard"]["current"] == "projects" and state["start_wizard"]

    make_workspace(db, user, 1, "site.ru")
    state = onboarding.get_onboarding(user)
    # Проекты есть — дальше мастер не навязываем, он ждёт на своей странице.
    assert state["wizard"]["current"] == "load" and not state["start_wizard"]

    onboarding.set_onboarding_dismissed(user)
    assert not onboarding.get_onboarding(user)["start_wizard"]


def test_chosen_role_reaches_the_agent_prompt(db):
    from app import ai_agent

    user = make_user(db)
    assert ai_agent.Context(user, None).role == ""
    with db.connection() as conn:
        conn.execute("UPDATE users SET ui = jsonb_build_object('role', 'pro') WHERE id = %s", (user,))
    ctx = ai_agent.Context(user, None)
    assert ctx.role == "pro"
    assert "специалист по контекстной рекламе" in ai_agent._role_rule(ctx)
