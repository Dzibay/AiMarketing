"""«Решать из Telegram» (I44) без БД: какие предложения простые, повторный вопрос, кнопки, разбор нажатий.

Telegram не вызывается: httpx.Client подменён.
"""

import json

from app import telegram_decisions as td
from app import telegram_notify


def test_is_simple():
    assert td.is_simple("add_campaign_negatives", {"campaign_row_id": 5, "phrases": ["бесплатно", "своими руками"]})
    assert not td.is_simple("add_campaign_negatives", {"campaign_row_id": 5, "phrases": []})
    assert not td.is_simple("add_campaign_negatives", {"phrases": ["x"]})
    assert td.is_simple("campaigns_action", {"action": "pause", "campaign_row_ids": [1]})
    assert td.is_simple("campaigns_action", {"action": "set_budget", "campaign_row_ids": [1]})
    assert not td.is_simple("campaigns_action", {"action": "archive", "campaign_row_ids": [1]})
    assert td.is_simple("update_campaign_settings", {"changes": {"daily_budget_rub": 900}})
    assert not td.is_simple("update_campaign_settings", {"changes": {"daily_budget_rub": 900, "name": "x"}})
    assert td.is_simple("keywords_action", {"action": "set_bid", "keyword_row_ids": [3], "bid_rub": 12})
    assert not td.is_simple("keywords_action", {"action": "delete", "keyword_row_ids": [3]})
    # Где надо выбирать строки — только «Открыть в кабинете».
    assert not td.is_simple("propose_items", {"items": [1, 2]})
    assert not td.is_simple("ask_questions", {})
    assert not td.is_simple("add_campaign_negatives", None)


def test_confirm_question():
    args = {"campaign_row_id": 5, "phrases": [f"p{i}" for i in range(12)]}
    assert td.confirm_question("add_campaign_negatives", args, "", "Окна — поиск") == "Добавить 12 минус-фраз в «Окна — поиск»?"
    one = {"campaign_row_id": 5, "phrases": ["x"]}
    assert td.confirm_question("add_campaign_negatives", one, "") == "Добавить 1 минус-фразу?"
    assert (
        td.confirm_question("update_campaign_settings", {"changes": {"daily_budget_rub": 900}}, "Настройки «Окна»: бюджет 900 ₽.")
        == "Изменить настройки «Окна»: бюджет 900 ₽?"
    )
    bid = td.confirm_question("keywords_action", {"action": "set_bid", "bid_rub": 12.5}, "Ставка: «окна пвх» → 12,5 ₽")
    assert bid == "Поставить ставку 12.5 ₽ ключевым фразам «окна пвх»?"
    assert td.confirm_question("campaigns_action", {"action": "pause"}, "Остановить «Окна»") == "Остановить «Окна»?"
    assert td.confirm_question("campaigns_action", {}, "") == "Выполнить предложение ИИ?"


def test_plural():
    assert [td.plural(n, "а", "б", "в") for n in (1, 2, 5, 11, 12, 21, 22, 25, 111)] == list("абвввабвв")


def test_usable_url_and_markup():
    assert td.usable_url("https://tolk-ads.ru/w/1/chat/2")
    assert not td.usable_url("http://localhost:5173/w/1")
    assert not td.usable_url("http://127.0.0.1/w/1")
    assert not td.usable_url("/w/1/chat/2")
    assert not td.usable_url("http://intranet/w/1")
    assert td.open_markup("http://localhost/w/1") is None
    assert td.open_markup("https://tolk-ads.ru/x") == {
        "inline_keyboard": [[{"text": "Открыть в кабинете", "url": "https://tolk-ads.ru/x"}]]
    }
    # Негодный адрес — строкой в тексте, годный — только кнопкой.
    assert td._with_link("Текст", "http://localhost/w/1") == "Текст\nhttp://localhost/w/1"
    assert td._with_link("Текст", "https://tolk-ads.ru/x") == "Текст"
    assert td.chat_link(7, 3) == "/w/3/chat/7"
    assert td.chat_link(7, None) == "/cabinet/chat/7"


def test_card_text():
    item = {"project": {"name": "Окна"}, "label": "Минус-фразы", "summary": "Добавить  12 фраз", "reason": "Нет заявок"}
    text = td.card_text(item, True)
    assert text.splitlines() == ["Толк · ИИ предлагает · Окна", "Минус-фразы: Добавить 12 фраз", "Почему: Нет заявок"]
    hard = td.card_text({"chat": {"title": "Чат"}, "summary": "Таблица"}, False)
    assert "решите в кабинете" in hard and "Чат" in hard


def test_callback_data_fits():
    # «d:» + код токена — с запасом меньше 64 байт.
    import secrets

    assert len((td.PREFIX + secrets.token_urlsafe(12)).encode()) <= 64


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.text = ""
        self._body = body or {"ok": True, "result": True}

    def json(self):
        return self._body


class _Client:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, data=None, files=None):
        _Client.calls.append((url.rsplit("/", 1)[-1], json))
        return _Resp()


def _fake_http(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(telegram_notify.httpx, "Client", _Client)
    monkeypatch.setattr(telegram_notify, "telegram_configured", lambda: True)
    return _Client.calls


def test_send_message_with_buttons(monkeypatch):
    calls = _fake_http(monkeypatch)
    keyboard = {"inline_keyboard": [[{"text": "Выполнить", "callback_data": "d:abc"}]]}
    assert telegram_notify.send_message(42, "Привет", keyboard)
    assert telegram_notify.send_message(42, "Без кнопок")
    assert calls[0][0] == "sendMessage" and calls[0][1]["reply_markup"] == keyboard
    assert "reply_markup" not in calls[1][1]
    json.dumps(calls[0][1])  # уходит JSON-ом


def test_answer_and_edit(monkeypatch):
    calls = _fake_http(monkeypatch)
    telegram_notify.answer_callback("cb1", "Принято")
    assert telegram_notify.edit_message("42", 7, "Готово.", {"inline_keyboard": []})
    assert calls[0] == ("answerCallbackQuery", {"callback_query_id": "cb1", "text": "Принято"})
    assert calls[1][0] == "editMessageText"
    assert calls[1][1]["chat_id"] == "42" and calls[1][1]["message_id"] == 7 and calls[1][1]["text"] == "Готово."


def test_handle_update_routes_callback(monkeypatch):
    seen = []
    monkeypatch.setattr(td, "handle_callback", lambda cq: seen.append(cq))
    telegram_notify.handle_update({"callback_query": {"id": "1", "data": "d:x"}})
    assert seen == [{"id": "1", "data": "d:x"}]


def test_handle_callback_ignores_foreign_data(monkeypatch):
    answered = []
    monkeypatch.setattr(telegram_notify, "answer_callback", lambda cid, text="": answered.append((cid, text)))
    # Не наша кнопка — без похода в БД, но часики гасим.
    td.handle_callback({"id": "cb", "data": "other", "message": {"chat": {"id": 1}, "message_id": 2}})
    assert answered == [("cb", "")]


def test_rule_outcome():
    assert telegram_notify.rule_outcome("pause", False, 3, 2) == "остановило 2 из 3"
    assert "режиме проверки" in telegram_notify.rule_outcome("pause", True, 3, 0)


def test_scheduled_review_chat_still_gets_cards(monkeypatch):
    """Чат разбора по расписанию: без «ИИ ответил», но карточки «Решать из Telegram» — да."""
    from app import ai_agent, ai_review, job_handlers

    offered, answered = [], []
    monkeypatch.setattr(ai_agent, "run_chat", lambda user_id, chat_id, progress: {"status": "waiting"})
    monkeypatch.setattr(ai_review, "pending_summary_chat", lambda chat_id: True)
    monkeypatch.setattr(td, "offer_chat", lambda user_id, chat_id: offered.append((user_id, chat_id)) or 1)
    monkeypatch.setattr(job_handlers, "_notify_answer", lambda *a: answered.append(a))
    job_handlers._ai_chat(3, {"chat_id": 9}, None)
    assert offered == [(3, 9)] and answered == []

    # Сбой Telegram не роняет ход ИИ.
    def boom(user_id, chat_id):
        raise RuntimeError("telegram")

    monkeypatch.setattr(td, "offer_chat", boom)
    assert job_handlers._ai_chat(3, {"chat_id": 9}, None) == {"status": "waiting"}


def test_undo_unexpected_error_reported(monkeypatch):
    """Не YandexError при откате из Telegram — итог в сообщении, а не вечное «Возвращаю как было…»."""
    import pytest

    from app import job_handlers, undo

    finished = []

    def boom(user_id, log_ids, progress):
        raise RuntimeError("db")

    monkeypatch.setattr(undo, "run_undo_group", boom)
    monkeypatch.setattr(td, "undo_finished", lambda tg, result=None, error="": finished.append((tg, result, error)))
    with pytest.raises(RuntimeError):
        job_handlers._undo_action(1, {"log_ids": [5], "telegram": {"chat_id": "1", "message_id": 2}}, None)
    assert finished and finished[0][2]


def test_undo_finished_partial_flag(monkeypatch):
    edits = []
    monkeypatch.setattr(telegram_notify, "edit_message", lambda c, m, text, markup=None: edits.append(text) or True)
    tg = {"chat_id": "1", "message_id": 2, "text": "Карточка", "url": ""}
    td.undo_finished(tg, {"status": "ok"})
    assert edits[-1].endswith("Вернули как было.")
    td.undo_finished({**tg, "partial": True}, {"status": "ok"})
    assert "Вернули не всё" in edits[-1]


def test_stale_to_telegram(monkeypatch):
    from app import ai_agent

    seen = []
    monkeypatch.setattr(td, "action_finished", lambda action_id: seen.append(action_id))
    ai_agent._stale_to_telegram([])
    ai_agent._stale_to_telegram(0)
    assert seen == []
    ai_agent._stale_to_telegram([4, 7])
    assert seen == [4, 7]
