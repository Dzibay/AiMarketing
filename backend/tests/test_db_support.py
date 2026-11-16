"""Поддержка на настоящем Postgres: переписка, тема в группе, ответ из темы и из админки, письмо, счётчики."""

import base64

import pytest

from app import mailer, notifications, support, telegram_notify
from app.config import settings
from tests.dbutil import make_user, make_workspace

PNG = base64.b64encode(b"\x89PNG fake").decode()


@pytest.fixture
def tg(monkeypatch):
    """Группа поддержки настроена; вызовы Telegram пишем в список."""
    calls = {"topics": [], "sent": [], "direct": []}
    monkeypatch.setattr(settings, "telegram_bot_token", "x")
    monkeypatch.setattr(settings, "telegram_support_chat_id", "-1001")

    def topic(name):
        calls["topics"].append(name)
        return 77

    def send(topic_id, text, *, image=None, image_type=""):
        calls["sent"].append((topic_id, text, bool(image)))
        return 1000 + len(calls["sent"])

    monkeypatch.setattr(telegram_notify, "create_support_topic", topic)
    monkeypatch.setattr(telegram_notify, "send_to_support_topic", send)
    monkeypatch.setattr(telegram_notify, "send_message", lambda chat, text: calls["direct"].append((chat, text)) or True)
    monkeypatch.setattr(telegram_notify, "download_file", lambda file_id, max_bytes=0: b"jpeg-bytes")
    return calls


def _workspace(db, user, name="Мебель"):
    return make_workspace(db, user, 1000 + user, name)


def _group(topic, text="", *, message_id=500, photo=False, bot=False):
    message = {
        "message_id": message_id,
        "chat": {"id": -1001},
        "from": {"id": 9, "is_bot": bot},
        "message_thread_id": topic,
        "is_topic_message": True,
    }
    if text:
        message["caption" if photo else "text"] = text
    if photo:
        message["photo"] = [{"file_id": "s", "file_size": 10}, {"file_id": "b", "file_size": 99}]
    return {"update_id": 1, "message": message}


def test_first_message_opens_topic_with_context(db, tg):
    user = make_user(db, email="anna@example.com")
    ws = _workspace(db, user)
    other = _workspace(db, make_user(db, email="b@example.com"), "Чужой")
    thread = support.post_message(
        user,
        "  Не грузится статистика  ",
        context={"page": "/w/1/campaigns", "workspace_id": ws, "browser": "Chrome 131 · Windows"},
    )
    assert [m["body"] for m in thread["messages"]] == ["Не грузится статистика"]
    assert thread["unread"] == 0 and thread["telegram"] is False
    assert tg["topics"] == [f"{user} · anna@example.com · без тарифа"]
    topic, text, has_image = tg["sent"][0]
    assert topic == 77 and not has_image
    assert "страница /w/1/campaigns" in text and "проект «Мебель»" in text and "Chrome 131" in text

    # Вторая — в ту же тему; чужой проект в контекст не попадает, скриншот уходит фото.
    support.post_message(user, "", image_data=f"data:image/png;base64,{PNG}", context={"workspace_id": other})
    assert tg["topics"] == [tg["topics"][0]] and tg["sent"][1][2] is True and "Чужой" not in tg["sent"][1][1]
    with db.connection() as conn:
        rows = conn.execute("SELECT tg_message_id, image_type, context FROM support_messages ORDER BY id").fetchall()
    assert [r["tg_message_id"] for r in rows] == [1001, 1002]
    assert rows[1]["image_type"] == "image/png" and "workspace" not in rows[1]["context"]
    msg_id = support.get_thread(user)["messages"][1]["id"]
    assert support.image(msg_id, user_id=user) == (b"\x89PNG fake", "image/png")
    assert support.image(msg_id, user_id=user + 999) is None


def test_validation_and_rate_limit(db, tg, monkeypatch):
    user = make_user(db)
    with pytest.raises(support.SupportError):
        support.post_message(user, "   ")
    with pytest.raises(support.SupportError):
        support.post_message(user, "x" * (support.MAX_TEXT + 1))
    with pytest.raises(support.SupportError):
        support.post_message(user, "", image_data="data:text/html;base64,PGI+")
    monkeypatch.setattr(support, "RATE_PER_HOUR", 2)
    support.post_message(user, "1")
    support.post_message(user, "2")
    with pytest.raises(support.SupportError) as exc:
        support.post_message(user, "3")
    assert exc.value.code == "rate"


def test_reply_in_topic_reaches_widget_bell_and_badge(db, tg):
    user = make_user(db)
    support.post_message(user, "Помогите")
    telegram_notify.handle_update(_group(77, "Смотрим, минуту"))
    # Повтор того же сообщения (опрос и webhook) и сообщения бота не дублируются.
    telegram_notify.handle_update(_group(77, "Смотрим, минуту"))
    telegram_notify.handle_update(_group(77, "копия", message_id=501, bot=True))
    telegram_notify.handle_update(_group(78, "чужая тема", message_id=502))
    thread = support.get_thread(user)
    assert [(m["author"], m["body"]) for m in thread["messages"]] == [("user", "Помогите"), ("admin", "Смотрим, минуту")]
    assert thread["unread"] == 1
    assert notifications.badges(user)["support"] == 1
    feed = notifications.list_notifications(user)["items"]
    assert feed[0]["kind"] == "support" and feed[0]["link"] == support.SUPPORT_LINK

    # Фото с подписью — картинкой.
    telegram_notify.handle_update(_group(77, "Вот так", message_id=503, photo=True))
    last = support.get_thread(user)["messages"][-1]
    assert last["has_image"] and last["body"] == "Вот так"
    assert support.image(last["id"]) == (b"jpeg-bytes", "image/jpeg")

    support.mark_read(user)
    assert notifications.badges(user)["support"] == 0
    assert notifications.unread_count(user) == 0


def test_reply_goes_to_linked_telegram(db, tg):
    user = make_user(db)
    with db.connection() as conn:
        conn.execute("UPDATE users SET telegram_chat_id = '42' WHERE id = %s", (user,))
    support.post_message(user, "Вопрос")
    telegram_notify.handle_update(_group(77, "Ответ"))
    assert tg["direct"] and tg["direct"][0][0] == "42" and "Ответ" in tg["direct"][0][1]
    assert support.get_thread(user)["telegram"] is True


def test_unread_reply_emailed_once_without_telegram(db, tg, monkeypatch):
    mails = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, *, link="": mails.append((to, body)))
    user = make_user(db, email="u1@example.com")
    linked = make_user(db, email="u2@example.com")
    with db.connection() as conn:
        conn.execute("UPDATE users SET telegram_chat_id = '42' WHERE id = %s", (linked,))
    support.post_message(user, "Вопрос 1")
    support.admin_reply(user, "Ответ первый")
    support.admin_reply(user, "Ответ второй")
    # Копия ответа из админки — в тему группы.
    assert tg["sent"][-1][1].endswith("Ответ второй")
    support.post_message(linked, "Вопрос 2")
    support.admin_reply(linked, "Ответ")
    assert support.email_unread_replies() == 0  # ещё не прошло 10 минут
    with db.connection() as conn:
        conn.execute("UPDATE support_messages SET created_at = NOW() - INTERVAL '11 minutes' WHERE author = 'admin'")
        conn.execute("UPDATE support_threads SET user_read_at = NOW() - INTERVAL '12 minutes'")
    assert support.email_unread_replies() == 1
    assert mails[0][0] == "u1@example.com" and "Ответ первый" in mails[0][1] and "Ответ второй" in mails[0][1]
    assert support.email_unread_replies() == 0


def test_read_reply_is_not_emailed_and_admin_counts(db, tg, monkeypatch):
    mails = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: mails.append(a))
    user = make_user(db)
    support.post_message(user, "Вопрос")
    support.post_message(user, "Ещё вопрос")
    assert support.admin_counts()[user] == {"total": 2, "unread": 2}
    view = support.admin_thread(user)
    assert view["topic"] == 77 and view["messages"][0]["context"].get("plan") == "без тарифа"
    assert support.admin_counts()[user]["unread"] == 0
    support.admin_reply(user, "Ответ")
    support.mark_read(user)
    with db.connection() as conn:
        conn.execute("UPDATE support_messages SET created_at = NOW() - INTERVAL '11 minutes' WHERE author = 'admin'")
    assert support.email_unread_replies() == 0 and mails == []
    with pytest.raises(support.SupportError):
        support.admin_reply(make_user(db, email="new@example.com"), "Нет переписки")


def test_without_support_group_message_is_kept(db, monkeypatch):
    monkeypatch.setattr(settings, "telegram_support_chat_id", "")
    user = make_user(db)
    thread = support.post_message(user, "Работает и без Telegram")
    assert len(thread["messages"]) == 1
