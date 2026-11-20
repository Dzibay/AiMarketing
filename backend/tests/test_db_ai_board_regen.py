"""Точечная перегенерация на доске (docs/board-plan.md, пункт 5): один короткий вызов модели без инструментов и
истории, варианты проверяются правилами доски и запретами ТЗ, выбранные ложатся обычной правкой в очередь."""

import json

import pytest

from app import ai, ai_agent, billing
from app.yandex import YandexError
from tests.test_db_ai import FakeModel, _seed, _start, _text, _turn
from tests.test_db_ai import _secret  # noqa: F401 — автофикстура
from tests.test_db_ai_build_memory import ADS, GOALS, KEYWORDS, _approve
from tests.test_db_ai_build_sync import CAMPAIGN, FakeDirect, _wizard

REQ = {"banned_words": ["дешев"]}


def _board(db, monkeypatch, *, ads=True):
    """Сборка: бриф с запретом «дешев…», группа «Окна ПВХ» с фразами записана в черновик; объявление — ждёт."""
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", GOALS, campaign=CAMPAIGN, requirements=REQ))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _wizard("keywords", KEYWORDS))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _wizard("ads", ADS) if ads else _text("Готово."))
    return user, chat


def _model(monkeypatch, *variants):
    fake = FakeModel(*({"content": json.dumps({"variants": list(v)}), "tool_calls": []} for v in variants))
    monkeypatch.setattr(ai, "complete", fake)
    return fake


def test_titles_one_light_call_and_server_filters(db, monkeypatch):
    user, chat = _board(db, monkeypatch)
    fake = _model(
        monkeypatch,
        [
            "Окна ПВХ с установкой",
            "Окна от завода",  # уже есть
            "Дешевые окна ПВХ",  # запрет ТЗ
            "Окна ПВХ с гарантией 10 лет и бесплатным замером по всей Москве и области",  # длиннее 56
            "Окна ПВХ с установкой",  # повтор
            "Тёплые окна в рассрочку.",
        ],
    )
    out = ai_agent.regenerate_board(user, chat, "title", group="Окна ПВХ", comment="короче")
    assert out["variants"] == ["Окна ПВХ с установкой", "Тёплые окна в рассрочку"]
    assert out["dropped"] == 4
    # Один вызов, без инструментов и истории чата: системная инструкция и короткий контекст.
    assert len(fake.calls) == 1
    messages = fake.calls[0]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert fake.cfgs[0]["task"] == "board_regen"
    context = messages[1]["content"]
    assert "купить окна пвх" in context and "Окна от завода" in context
    assert "«дешев»" in context and "Что не так: короче" in context
    assert "Сделай кампанию" not in context
    # Доска не изменилась — выбирает маркетолог.
    titles = [t["text"] for t in ai_agent.get_chat(user, chat)["build"]["groups"][0]["titles"]]
    assert titles == ["Окна от завода"]


def test_one_line_variants_replace_through_board_edit(db, monkeypatch):
    user, chat = _board(db, monkeypatch)
    _model(monkeypatch, ["Замер в день заказа", "Бесплатный замер в день заявки", "Рассрочка 0% на 12 месяцев"])
    out = ai_agent.regenerate_board(user, chat, "text", group="Окна ПВХ", text="Замер в день заказа")
    # Та же строка — не вариант.
    assert out["variants"] == ["Бесплатный замер в день заявки", "Рассрочка 0% на 12 месяцев"]
    assert "Заменить одну строку: «Замер в день заказа»" in ai.complete.calls[0][1]["content"]
    build = ai_agent.edit_board(
        user, chat, [{"op": "edit", "kind": "text", "group": "Окна ПВХ", "text": "Замер в день заказа", "to": out["variants"][0]}]
    )["build"]
    assert [t["text"] for t in build["groups"][0]["texts"]] == ["Бесплатный замер в день заявки"]


def test_keywords_of_a_written_step_go_to_the_queue(db, monkeypatch):
    user, chat = _board(db, monkeypatch, ads=False)
    _model(monkeypatch, ["купить окна пвх", "пластиковые окна москва", "окна пвх цена", "-дешево"])
    out = ai_agent.regenerate_board(user, chat, "keyword", group="Окна ПВХ")
    assert "купить окна пвх" not in out["variants"]
    assert out["variants"][:2] == ["пластиковые окна москва", "окна пвх цена"]
    build = ai_agent.edit_board(
        user, chat, [{"op": "add", "kind": "keyword", "group": "Окна ПВХ", "text": t} for t in out["variants"][:2]]
    )["build"]
    assert [(c["kind"], c["op"], c["text"]) for c in build["queue"]] == [
        ("keyword", "add", "пластиковые окна москва"), ("keyword", "add", "окна пвх цена"),
    ]


def test_negatives_without_group(db, monkeypatch):
    user, chat = _board(db, monkeypatch, ads=False)
    _model(monkeypatch, ["бесплатно", "своими руками", "вакансии"])
    out = ai_agent.regenerate_board(user, chat, "negative")
    assert out["variants"] == ["своими руками", "вакансии"]


def test_bad_requests_and_limit_cost_nothing(db, monkeypatch):
    user, chat = _board(db, monkeypatch)
    fake = _model(monkeypatch)
    for kwargs, message in (
        ({"kind": "image", "group": "Окна ПВХ"}, "kind"),
        ({"kind": "keyword", "group": "Окна ПВХ", "text": "купить окна пвх"}, "только список"),
        ({"kind": "title"}, "Укажите группу"),
        ({"kind": "title", "group": "Двери"}, "Группы «Двери»"),
        ({"kind": "title", "group": "Окна ПВХ", "text": "Нет такого"}, "уже нет на доске"),
    ):
        kind = kwargs.pop("kind")
        with pytest.raises(YandexError) as err:
            ai_agent.regenerate_board(user, chat, kind, **kwargs)
        assert message in err.value.message
    # Лимит исчерпан — до модели дело не доходит.
    def exhausted(user_id, need=0.0):
        raise YandexError("units-exhausted", "Лимит исчерпан")

    monkeypatch.setattr(billing, "check", exhausted)
    with pytest.raises(YandexError) as err:
        ai_agent.regenerate_board(user, chat, "title", group="Окна ПВХ")
    assert err.value.code == "units-exhausted"
    assert fake.calls == []
