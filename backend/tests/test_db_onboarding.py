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
        "ai_configured": False,
        "direct_connections": 0,
        "direct_accounts": 0,
        "direct_loaded": 0,
        "load_errors": 0,
        "workspaces": [],
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
        conn.execute("UPDATE users SET telegram_chat_id = '42' WHERE id = %s", (user,))
        # YandexGPT без каталога — ключ ещё не рабочий.
        conn.execute(
            "INSERT INTO ai_settings (user_id, provider, api_key_enc) VALUES (%s, 'yandexgpt', 'enc')", (user,)
        )

    facts = onboarding.load_facts(user)
    assert facts["direct_connections"] == 1
    assert facts["direct_accounts"] == 2 and facts["direct_loaded"] == 1 and facts["load_errors"] == 1
    assert facts["telegram_linked"] and not facts["ai_configured"]
    assert facts["workspaces"] == [
        {"id": ws, "name": "site.ru", "campaigns": 1, "has_template": True, "has_goal": True}
    ]

    with db.connection() as conn:
        conn.execute("UPDATE ai_settings SET folder_id = 'b1g' WHERE user_id = %s", (user,))
    assert onboarding.load_facts(user)["ai_configured"]


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
