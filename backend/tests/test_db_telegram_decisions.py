"""«Решать из Telegram» (I44) на настоящем Postgres: карточки, повторный вопрос, проверки кнопок, откат.

Telegram, Директ и ИИ не вызываются: send_message / edit_message / answer_callback и постановка задач подменены.
"""

from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from app import ai_agent, notifications
from app import telegram_decisions as td
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace

TG = "777000"


def _sql(db, query, params=()):
    with db.connection() as conn:
        return conn.execute(query, params)


def _action(db, user, chat, tool, args, summary, status="pending"):
    msg = _sql(
        db, "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, 'assistant', '') RETURNING id", (chat,)
    ).fetchone()["id"]
    return _sql(
        db,
        """
        INSERT INTO ai_actions (chat_id, message_id, user_id, tool, args, summary, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (chat, msg, user, tool, Jsonb(args), summary, status),
    ).fetchone()["id"]


@pytest.fixture
def tg(db, monkeypatch):
    user = make_user(db)
    conn_id = make_connection(db, user)
    acc = make_account(db, user, conn_id, login="a")
    camp = make_campaign(db, user, acc, 101, "Окна — поиск")
    ws = make_workspace(db, user, 1, name="Окна", accounts=[acc], campaigns=[camp])
    _sql(db, "UPDATE users SET telegram_chat_id = %s WHERE id = %s", (TG, user))
    chat = _sql(
        db, "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, %s, 'Чат') RETURNING id", (user, ws)
    ).fetchone()["id"]
    sent, edits, answers, jobs = [], [], [], []
    monkeypatch.setattr(
        "app.telegram_notify.send_message", lambda chat_id, text, markup=None: sent.append((chat_id, text, markup)) or True
    )
    monkeypatch.setattr(
        "app.telegram_notify.edit_message",
        lambda chat_id, mid, text, markup=None: edits.append((chat_id, mid, text, markup)) or True,
    )
    monkeypatch.setattr("app.telegram_notify.answer_callback", lambda cid, text="": answers.append(text))
    monkeypatch.setattr("app.settings_store.public_base_url", lambda: "https://tolk.test")
    monkeypatch.setattr(ai_agent, "_submit", lambda user_id, chat_id, marker: None)  # ход ИИ не запускаем
    monkeypatch.setattr(
        "app.job_handlers.submit", lambda kind, user_id, payload, **kw: jobs.append((kind, payload)) or {"id": 1}
    )
    return {
        "user": user, "camp": camp, "ws": ws, "chat": chat,
        "sent": sent, "edits": edits, "answers": answers, "jobs": jobs,
    }


def _on(db, user):
    notifications.set_prefs(user, {"telegram_decide": True})


def _buttons(markup):
    return {b["text"]: b.get("callback_data") or b.get("url") for row in (markup or {}).get("inline_keyboard", []) for b in row}


def _press(data, *, chat=TG, from_id=TG, mid=55):
    td.handle_callback({"id": "cb", "data": data, "from": {"id": int(from_id)}, "message": {"chat": {"id": int(chat)}, "message_id": mid}})


def _negatives(db, t, n=12):
    args = {"campaign_row_id": t["camp"], "phrases": [f"фраза {i}" for i in range(n)]}
    return _action(db, t["user"], t["chat"], "add_campaign_negatives", args, "Минус-фразы в «Окна — поиск»")


def test_setting_off_by_default(db, tg):
    assert notifications.get_prefs(tg["user"])["telegram_decide"] is False
    _negatives(db, tg)
    assert td.offer_chat(tg["user"], tg["chat"]) == 0
    assert tg["sent"] == []
    assert notifications.set_prefs(tg["user"], {"telegram_decide": True})["telegram_decide"] is True
    with pytest.raises(notifications.NotificationsError):
        notifications.set_prefs(tg["user"], {"telegram_decide": "да"})


def test_offer_and_run_yes_flow(db, tg):
    _on(db, tg["user"])
    simple = _negatives(db, tg)
    _action(db, tg["user"], tg["chat"], "propose_items", {"items": [{"x": 1}]}, "Таблица ключевых фраз")
    assert td.offer_chat(tg["user"], tg["chat"]) == 2
    # Повторно то же не присылаем.
    assert td.offer_chat(tg["user"], tg["chat"]) == 0
    (c1, text1, m1), (c2, text2, m2) = tg["sent"]
    assert c1 == TG and "ИИ предлагает · Окна" in text1
    b1 = _buttons(m1)
    assert set(b1) == {"Выполнить", "Отклонить", "Открыть в кабинете"}
    assert b1["Открыть в кабинете"] == f"https://tolk.test/w/{tg['ws']}/chat/{tg['chat']}"
    assert all(len(v.encode()) <= 64 for k, v in b1.items() if k != "Открыть в кабинете")
    assert set(_buttons(m2)) == {"Открыть в кабинете"} and "решите в кабинете" in text2

    _press(b1["Выполнить"])
    chat_id, mid, question, qm = tg["edits"][-1]
    assert (chat_id, mid) == (TG, 55)
    assert question.endswith("Добавить 12 минус-фраз в «Окна — поиск»?")
    qb = _buttons(qm)
    assert set(qb) == {"Да, выполнить", "Отмена"}
    # Старые кнопки погасли.
    _press(b1["Отклонить"])
    assert tg["answers"][-1] == td.USED

    _press(qb["Да, выполнить"])
    assert tg["answers"][-1] == "Принято"
    status = _sql(db, "SELECT status FROM ai_actions WHERE id = %s", (simple,)).fetchone()["status"]
    assert status == "approved"
    # В чате ждёт ещё таблица — ИИ выполнит, когда решат и её.
    assert "когда вы решите остальные предложения" in tg["edits"][-1][2]
    ev = _sql(db, "SELECT meta FROM user_events WHERE user_id = %s AND kind = 'action_applied'", (tg["user"],)).fetchone()
    assert ev and ev["meta"]["source"] == "telegram"


def test_cancel_returns_card(db, tg):
    _on(db, tg["user"])
    _negatives(db, tg, 3)
    td.offer_chat(tg["user"], tg["chat"])
    _press(_buttons(tg["sent"][0][2])["Выполнить"])
    _press(_buttons(tg["edits"][-1][3])["Отмена"])
    assert tg["answers"][-1] == "Отменено"
    assert set(_buttons(tg["edits"][-1][3])) == {"Выполнить", "Отклонить", "Открыть в кабинете"}


def test_reject(db, tg):
    _on(db, tg["user"])
    aid = _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    _press(_buttons(tg["sent"][0][2])["Отклонить"])
    assert tg["answers"][-1] == "Отклонено"
    assert _sql(db, "SELECT status FROM ai_actions WHERE id = %s", (aid,)).fetchone()["status"] == "rejected"


def test_already_decided_in_cabinet(db, tg):
    _on(db, tg["user"])
    aid = _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    _sql(db, "UPDATE ai_actions SET status = 'rejected' WHERE id = %s", (aid,))
    _press(_buttons(tg["sent"][0][2])["Выполнить"])
    assert tg["answers"][-1] == td.ALREADY
    assert "Уже решено в кабинете" in tg["edits"][-1][2]


def test_foreign_chat_and_user(db, tg):
    _on(db, tg["user"])
    _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    run = _buttons(tg["sent"][0][2])["Выполнить"]
    _press(run, chat="999")
    assert tg["answers"][-1] == td.FOREIGN
    _press(run, from_id="999")
    assert tg["answers"][-1] == td.FOREIGN
    # Перепривязали Telegram на другой чат — старые кнопки не работают.
    _sql(db, "UPDATE users SET telegram_chat_id = '888' WHERE id = %s", (tg["user"],))
    _press(run)
    assert tg["answers"][-1] == td.FOREIGN
    assert tg["edits"] == []
    _press("d:nonexistent")
    assert tg["answers"][-1] == td.EXPIRED


def test_single_use_expiry_and_turned_off(db, tg):
    _on(db, tg["user"])
    _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    b = _buttons(tg["sent"][0][2])
    _sql(db, "UPDATE telegram_decision_tokens SET expires_at = %s", (datetime.now(timezone.utc) - timedelta(minutes=1),))
    _press(b["Выполнить"])
    assert tg["answers"][-1] == td.EXPIRED
    _sql(db, "UPDATE telegram_decision_tokens SET expires_at = %s", (datetime.now(timezone.utc) + timedelta(hours=1),))
    notifications.set_prefs(tg["user"], {"telegram_decide": False})
    _press(b["Выполнить"])
    assert tg["answers"][-1] == td.TURNED_OFF
    notifications.set_prefs(tg["user"], {"telegram_decide": True})
    _press(b["Выполнить"])
    assert tg["answers"][-1] == ""
    _press(b["Выполнить"])
    assert tg["answers"][-1] == td.USED


def test_finished_and_undo(db, tg, monkeypatch):
    _on(db, tg["user"])
    aid = _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    _press(_buttons(tg["sent"][0][2])["Выполнить"])
    _press(_buttons(tg["edits"][-1][3])["Да, выполнить"])
    monkeypatch.setattr("app.undo.ai_action_log_ids", lambda user_id, action_id: [11, 12])
    monkeypatch.setattr(
        "app.undo.ai_action_undo_preview",
        lambda user_id, action_id: {"available": True, "log_ids": [11, 12], "steps": ["Убрать 12 минус-фраз"], "warnings": [], "reason": ""},
    )
    _sql(db, "UPDATE ai_actions SET status = 'done' WHERE id = %s", (aid,))
    td.action_finished(aid)
    text, markup = tg["edits"][-1][2], tg["edits"][-1][3]
    assert text.endswith("Готово.")
    ub = _buttons(markup)
    assert "Вернуть как было" in ub

    _press(ub["Вернуть как было"])
    assert "Вернуть как было?" in tg["edits"][-1][2] and "Убрать 12 минус-фраз" in tg["edits"][-1][2]
    _press(_buttons(tg["edits"][-1][3])["Да, вернуть"])
    assert tg["answers"][-1] == "Возвращаю"
    kind, payload = tg["jobs"][-1]
    assert kind == "undo_action" and payload["log_ids"] == [11, 12]
    assert payload["telegram"]["chat_id"] == TG and payload["telegram"]["message_id"] == 55

    td.undo_finished(payload["telegram"], {"status": "done"})
    assert tg["edits"][-1][2].endswith("Вернули как было.")


def test_action_error_reported(db, tg):
    _on(db, tg["user"])
    aid = _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    _press(_buttons(tg["sent"][0][2])["Выполнить"])
    _press(_buttons(tg["edits"][-1][3])["Да, выполнить"])
    _sql(db, "UPDATE ai_actions SET status = 'error', error = 'Кампания в архиве' WHERE id = %s", (aid,))
    td.action_finished(aid)
    assert tg["edits"][-1][2].endswith("Не получилось: Кампания в архиве")


def test_overflow_line(db, tg):
    _on(db, tg["user"])
    for _ in range(td.MAX_CARDS + 2):
        _negatives(db, tg, 2)
    assert td.offer_chat(tg["user"], tg["chat"]) == td.MAX_CARDS + 1
    assert tg["sent"][-1][1].startswith("И ещё 2 предложения ИИ — в кабинете.")
    assert td.offer_chat(tg["user"], tg["chat"]) == 0


def test_tokens_committed_before_send(db, tg, monkeypatch):
    """Коды кнопок видны другому соединению уже в момент отправки карточки (нажатие сразу после доставки)."""
    _on(db, tg["user"])
    _negatives(db, tg, 2)
    _negatives(db, tg, 3)
    seen = []

    def send(chat_id, text, markup=None):
        token = _buttons(markup)["Выполнить"][len(td.PREFIX):]
        with db.connection() as conn:  # отдельное соединение — видит только закоммиченное
            seen.append(conn.execute("SELECT 1 FROM telegram_decision_tokens WHERE token = %s", (token,)).fetchone())
        return True

    monkeypatch.setattr("app.telegram_notify.send_message", send)
    assert td.offer_chat(tg["user"], tg["chat"]) == 2
    assert len(seen) == 2 and all(seen)


def test_undo_with_blocked_part(db, tg, monkeypatch):
    """Часть решения вернуть нельзя — вопрос говорит об этом, итог — «Вернули не всё»."""
    _on(db, tg["user"])
    aid = _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    _press(_buttons(tg["sent"][0][2])["Выполнить"])
    _press(_buttons(tg["edits"][-1][3])["Да, выполнить"])
    monkeypatch.setattr("app.undo.ai_action_log_ids", lambda user_id, action_id: [11, 12])
    monkeypatch.setattr(
        "app.undo.ai_action_undo_preview",
        lambda user_id, action_id: {
            "available": True, "log_ids": [11], "steps": ["Убрать 12 минус-фраз"], "warnings": [], "reason": "",
            "blocked": ["«Ставка» — изменение уже перезаписано"], "undone": [],
        },
    )
    _sql(db, "UPDATE ai_actions SET status = 'done' WHERE id = %s", (aid,))
    td.action_finished(aid)
    _press(_buttons(tg["edits"][-1][3])["Вернуть как было"])
    assert "Не вернётся: «Ставка» — изменение уже перезаписано" in tg["edits"][-1][2]
    _press(_buttons(tg["edits"][-1][3])["Да, вернуть"])
    _kind, payload = tg["jobs"][-1]
    assert payload["log_ids"] == [11] and payload["telegram"]["partial"] is True
    td.undo_finished(payload["telegram"], {"status": "ok"})
    assert "Вернули не всё" in tg["edits"][-1][2]


def test_stale_action_reported(db, tg):
    """Одобрено из Telegram, ход ИИ умер — сборщик «зависших» пишет итог в сообщение бота."""
    _on(db, tg["user"])
    aid = _negatives(db, tg)
    td.offer_chat(tg["user"], tg["chat"])
    _press(_buttons(tg["sent"][0][2])["Выполнить"])
    _press(_buttons(tg["edits"][-1][3])["Да, выполнить"])
    _sql(
        db,
        "UPDATE ai_actions SET status = 'approved', started_at = NOW() - INTERVAL '1 hour' WHERE id = %s",
        (aid,),
    )
    with db.connection() as conn:
        ids = ai_agent._recover_stale_actions(conn, tg["chat"])
    assert ids == [aid]
    ai_agent._stale_to_telegram(ids)
    assert "Не получилось: Выполнение прервалось" in tg["edits"][-1][2]
