"""Колокольчик на настоящем Postgres: запись, склейка повторов, прочтение, настройки «куда», чистка."""

import pytest

from app import notifications as n
from app import telegram_notify
from tests.dbutil import make_user


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(telegram_notify, "send_message", lambda chat, text: out.append((chat, text)) or True)
    return out


def _link_telegram(db, user):
    with db.connection() as conn:
        conn.execute("UPDATE users SET telegram_chat_id = '42' WHERE id = %s", (user,))


def test_notify_writes_feed_and_badges_count_unread(db, sent):
    user = make_user(db)
    first = n.notify(user, "ai_answer", "ИИ ответил: План", body="Готово", link="/cabinet/chat/1")
    assert first is not None
    feed = n.list_notifications(user)
    assert feed["unread"] == 1 and feed["items"][0]["title"] == "ИИ ответил: План"
    assert feed["items"][0]["link"] == "/cabinet/chat/1" and not feed["items"][0]["read"]
    assert n.badges(user) == {"pending": 0, "notifications": 1, "support": 0}
    # Telegram не привязан — в него ничего не уходит.
    assert sent == []


def test_unread_duplicate_is_refreshed_not_added(db, sent):
    user = make_user(db)
    a = n.notify(user, "signal", "Затухание: 2 кампании", dedup_key="signal:1:decay:")
    b = n.notify(user, "signal", "Затухание: 3 кампании", dedup_key="signal:1:decay:")
    assert a == b
    feed = n.list_notifications(user)
    assert len(feed["items"]) == 1 and feed["items"][0]["title"] == "Затухание: 3 кампании"


def test_read_signal_does_not_repeat_within_week_but_ai_answer_does(db, sent):
    user = make_user(db)
    n.notify(user, "signal", "Сигнал", dedup_key="signal:1:idle:")
    n.mark_read(user, everything=True)
    assert n.notify(user, "signal", "Сигнал", dedup_key="signal:1:idle:") is None
    with db.connection() as conn:
        conn.execute("UPDATE notifications SET created_at = NOW() - INTERVAL '8 days' WHERE user_id = %s", (user,))
    assert n.notify(user, "signal", "Сигнал", dedup_key="signal:1:idle:") is not None

    n.notify(user, "ai_answer", "Ответ 1", dedup_key="ai_answer:5")
    n.mark_read(user, everything=True)
    assert n.notify(user, "ai_answer", "Ответ 2", dedup_key="ai_answer:5") is not None
    assert n.unread_count(user) == 1


def test_mark_read_by_ids_and_by_link(db, sent):
    user = make_user(db)
    other = make_user(db, email="other@example.com")
    chat = n.notify(user, "ai_answer", "Ответ", link="/w/3/chat/9")
    plan = n.notify(user, "plan", "Тариф закончится завтра", link="/cabinet/plan")
    foreign = n.notify(other, "plan", "Чужое", link="/cabinet/plan")
    assert n.mark_read(user, link="/w/3/chat/9") == {"marked": 1, "unread": 1}
    # Чужие записи по id не отмечаются.
    assert n.mark_read(user, ids=[foreign])["marked"] == 0
    assert n.mark_read(user, ids=[plan, chat]) == {"marked": 1, "unread": 0}
    assert n.unread_count(other) == 1
    with pytest.raises(n.NotificationsError):
        n.mark_read(user)


def test_prefs_route_events_between_cabinet_and_telegram(db, sent):
    user = make_user(db)
    _link_telegram(db, user)
    prefs = {e["kind"]: e for e in n.get_prefs(user)["events"]}
    assert prefs["rule"]["telegram"] and not prefs["ai_answer"]["telegram"] and prefs["signal"]["cabinet"]

    n.notify(user, "rule", "Правило «Стоп» применило: 1", link="/cabinet/autopilot", telegram_text="Толк · правило")
    assert sent == [("42", "Толк · правило")]

    n.set_prefs(user, {"signal": {"cabinet": False, "telegram": True}})
    assert n.notify(user, "signal", "Затухание", body="Показы упали", link="/w/1/chat", dedup_key="s:1") is None
    assert sent[-1][0] == "42" and sent[-1][1].startswith("Затухание\nПоказы упали\n") and sent[-1][1].endswith("/w/1/chat")
    assert [i["kind"] for i in n.list_notifications(user)["items"]] == ["rule"]
    # Повтор того же сигнала в Telegram склеивается, хотя в ленте записи нет.
    n.notify(user, "signal", "Затухание", dedup_key="s:1")
    assert len(sent) == 2

    with pytest.raises(n.NotificationsError):
        n.set_prefs(user, {"nope": {"cabinet": True}})
    with pytest.raises(n.NotificationsError):
        n.set_prefs(user, {"rule": {"cabinet": "да"}})


def test_unknown_kind_is_swallowed_and_purge_drops_old(db, sent):
    user = make_user(db)
    assert n.notify(user, "nope", "x") is None
    n.notify(user, "plan", "Старое")
    n.notify(user, "plan", "Новое")
    with db.connection() as conn:
        conn.execute("UPDATE notifications SET created_at = NOW() - INTERVAL '91 days' WHERE title = 'Старое'")
    assert n.purge() == 1
    assert [i["title"] for i in n.list_notifications(user)["items"]] == ["Новое"]


def test_ai_answer_entry_links_to_its_chat(db, sent):
    from app.job_handlers import _notify_answer

    user = make_user(db)
    with db.connection() as conn:
        chat = conn.execute(
            "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, NULL, 'Почему мало заявок') RETURNING id",
            (user,),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, 'assistant', 'Заявок мало из-за   ставок.')",
            (chat,),
        )
    _notify_answer(user, chat, {"status": "idle"})
    item = n.list_notifications(user)["items"][0]
    assert item["kind"] == "ai_answer" and item["title"] == "ИИ ответил: Почему мало заявок"
    assert item["body"] == "Заявок мало из-за ставок." and item["link"] == f"/cabinet/chat/{chat}"
    # Открыли чат — запись прочитана; следующий ответ — снова в колокольчике.
    n.mark_read(user, link=f"/cabinet/chat/{chat}")
    _notify_answer(user, chat, {"status": "waiting"})
    item = n.list_notifications(user)["items"][0]
    assert item["title"].startswith("ИИ ждёт вашего решения") and n.unread_count(user) == 1


def test_limit_crossing_80_and_100_percent_once_per_period(db, sent):
    from app import billing
    from tests.dbutil import set_units_limit

    user = make_user(db)
    set_units_limit(user, 100)
    billing.record(user, kind="model", task="chat", charged=50)
    assert n.unread_count(user) == 0
    billing.record(user, kind="model", task="chat", charged=35)
    billing.record(user, kind="model", task="chat", charged=1)
    assert [i["title"] for i in n.list_notifications(user)["items"]] == ["Израсходовано 80 % лимита ИИ"]
    billing.record(user, kind="model", task="chat", charged=20)
    titles = [i["title"] for i in n.list_notifications(user)["items"]]
    assert titles == ["Лимит ИИ израсходован", "Израсходовано 80 % лимита ИИ"]
