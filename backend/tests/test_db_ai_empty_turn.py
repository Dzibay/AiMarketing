"""Пустых ответов нет (docs/board-plan.md, пункт 7): ход кончился без текста — сервис пишет итог сам.
В сборке — какой шаг согласован и какой следующий, с кнопкой-подсказкой; вне сборки — что ИИ смотрел."""

from app import ai_agent
from tests.test_db_ai import _call, _seed, _start, _text, _turn
from tests.test_db_ai import _secret  # noqa: F401 — автофикстура
from tests.test_db_ai_build_memory import GOALS, _approve
from tests.test_db_ai_build_sync import CAMPAIGN, FakeDirect, _wizard

EMPTY = {"content": "", "tool_calls": []}


def _last(user, chat):
    return [m for m in ai_agent.get_chat(user, chat)["messages"] if m["role"] == "assistant"][-1]


def test_empty_answer_after_reads_gets_a_summary(db, monkeypatch):
    s = _seed(db)
    _fake, out = _start(s["user"], s["chat"], "Как дела у кампаний?", monkeypatch, _call("list_campaigns", {}), EMPTY)
    assert out["status"] == "idle"
    last = _last(s["user"], s["chat"])
    assert last["content"].startswith("Посмотрел данные (1 источник), но ответ не написал.")
    assert last["followups"] == ["Ответь по тому, что посмотрел"]


def test_text_in_the_turn_means_no_summary(db, monkeypatch):
    s = _seed(db)
    _start(s["user"], s["chat"], "Как дела?", monkeypatch, _text("Всё хорошо."), EMPTY)
    messages = [m for m in ai_agent.get_chat(s["user"], s["chat"])["messages"] if m["role"] == "assistant"]
    assert [m["content"] for m in messages] == ["Всё хорошо."]


def test_build_turn_without_text_names_the_next_step(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", GOALS, campaign=CAMPAIGN))
    _approve(user, chat)
    # После «Дальше» сервис пишет черновик, а модель молча заканчивает ход — без таблицы шага «Анализ».
    _turn(user, chat, monkeypatch, EMPTY)
    last = _last(user, chat)
    assert last["content"] == (
        "Готово: шаг «Цели» согласован и записан в черновик. "
        "Следующий шаг — «Анализ»: ИИ подготовит его по кнопке ниже."
    )
    assert last["followups"] == ["Подготовь шаг «Анализ»"]
