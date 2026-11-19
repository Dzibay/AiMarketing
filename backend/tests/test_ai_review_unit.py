"""Разбор недели по расписанию без БД: время запуска, неделя, заголовок чата и текст сводки."""

from datetime import date, datetime, timedelta, timezone

from app import ai_review
from app.clock import MSK


def test_next_run_is_strictly_after():
    wed = datetime(2026, 11, 18, 12, 30, tzinfo=MSK)  # среда
    assert ai_review.next_run(0, 9, wed) == datetime(2026, 11, 23, 9, tzinfo=MSK)
    assert ai_review.next_run(2, 13, wed) == datetime(2026, 11, 18, 13, tzinfo=MSK)
    # Тот же час уже наступил — через неделю.
    assert ai_review.next_run(2, 12, wed) == datetime(2026, 11, 25, 12, tzinfo=MSK)
    at = datetime(2026, 11, 23, 9, tzinfo=MSK)
    assert ai_review.next_run(0, 9, at) == at + timedelta(days=7)
    # Время в UTC считается по Москве: 22:00 UTC воскресенья — уже понедельник 01:00 МСК.
    utc = datetime(2026, 11, 22, 22, 0, tzinfo=timezone.utc)
    assert ai_review.next_run(0, 9, utc) == datetime(2026, 11, 23, 9, tzinfo=MSK)
    assert ai_review.next_run(0, 0, utc) == datetime(2026, 11, 30, 0, tzinfo=MSK)


def test_week_and_title():
    assert ai_review.week_of(datetime(2026, 11, 22, 23, tzinfo=MSK)) == date(2026, 11, 16)
    assert ai_review.week_of(datetime(2026, 11, 23, 0, tzinfo=MSK)) == date(2026, 11, 23)
    assert ai_review.chat_title(date(2026, 11, 17)) == "Разбор недели · 17 ноября"
    assert ai_review.chat_title(date(2026, 3, 1)) == "Разбор недели · 1 марта"


def _row(name, ws, chat=None, pending=0, reason=""):
    return {"name": name, "workspace_id": ws, "chat_id": chat, "pending": pending, "reason": reason}


def test_summary_lists_found_clean_and_skipped(monkeypatch):
    monkeypatch.setattr("app.settings_store.public_base_url", lambda: "https://tolk.test/")
    text = ai_review._summary(
        [
            _row("Окна", 1, 10, 2),
            _row("Двери", 2, 11, 1),
            _row("Пол", 3, 12, 0),
            _row("Крыша", 4, reason=ai_review.LIMIT_REASON),
            _row("Сад", 5, reason=ai_review.LIMIT_REASON),
        ]
    )
    assert text["title"] == "Разбор недели: у 2 клиентов нашлось что поправить"
    assert text["body"].startswith("Окна (2 предложения), Двери (1 предложение)")
    assert "Без замечаний: Пол" in text["body"]
    assert "Не хватило лимита ИИ: Крыша, Сад" in text["body"]
    assert text["link"] == "/cabinet/pending" and text["found"] == 2
    assert "Окна — 2 предложения: https://tolk.test/w/1/chat/10" in text["telegram"]
    assert "https://tolk.test/w/2/chat/11" in text["telegram"]


def test_summary_single_found_links_to_chat_and_empty_cases(monkeypatch):
    monkeypatch.setattr("app.settings_store.public_base_url", lambda: "https://tolk.test")
    one = ai_review._summary([_row("Окна", 1, 10, 5)])
    assert one["title"] == "Разбор недели: у 1 клиента нашлось что поправить"
    assert one["body"] == "Окна (5 предложений)"
    assert one["link"] == "/w/1/chat/10"

    clean = ai_review._summary([_row("Окна", 1, 10, 0)])
    assert clean["title"] == "Разбор недели: замечаний нет" and clean["severity"] == "ok"

    none = ai_review._summary([_row("Окна", 1, reason=ai_review.LIMIT_REASON)])
    assert none["title"] == "Разбор недели не прошёл" and none["severity"] == "warn"
    assert none["body"] == "Не хватило лимита ИИ: Окна"
    # Ни токенов, ни денег, ни единиц.
    for t in (one, clean, none):
        for word in ("токен", "₽", "руб", "единиц"):
            assert word not in t["body"] and word not in t["telegram"]


def test_summary_failed_and_late_chats_are_not_clean(monkeypatch):
    monkeypatch.setattr("app.settings_store.public_base_url", lambda: "https://tolk.test")
    rows = [
        {**_row("Окна", 1, 10, 2), "status": "idle"},
        {**_row("Двери", 2, 11, 0), "status": "error"},
        {**_row("Пол", 3, 12, 0), "status": "running"},
        {**_row("Сад", 4, 13, 0), "status": "idle"},
    ]
    text = ai_review._summary(rows)
    assert "Без замечаний: Сад" in text["body"]
    assert "Разбор не прошёл: Двери" in text["body"] and "Не успел ответить: Пол" in text["body"]
    assert "Двери" not in text["body"].split("Без замечаний")[1].split(".")[0]

    only_failed = ai_review._summary([{**_row("Двери", 2, 11, 0), "status": "error"}])
    assert only_failed["title"] == "Разбор недели не прошёл" and only_failed["severity"] == "warn"
    assert only_failed["link"] == "/w/2/chat/11"

    mixed = ai_review._summary(
        [{**_row("Сад", 4, 13, 0), "status": "idle"}, {**_row("Двери", 2, 11, 0), "status": "error"}]
    )
    assert mixed["title"] == "Разбор недели: замечаний нет, но не все проекты проверены"
    assert mixed["severity"] == "warn" and "Проверили: Сад" in mixed["body"]
