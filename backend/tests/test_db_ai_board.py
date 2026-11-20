"""Сборка одной доской: таблицы этапов не останавливают ход, кампания согласуется одним решением."""

import copy

import pytest

from app import actions, ai_agent
from app.yandex import YandexError
from tests.dbutil import make_campaign
from tests.test_db_ai import _call, _pending, _secret, _seed, _start, _text, _turn  # noqa: F401 — _secret: автофикстура
from tests.test_db_ai_build_memory import ADS, GOALS, KEYWORDS, SETTINGS, _cards, _reply, _spec, _table

ANALYSIS = [{"cells": ["Окна ПВХ", "12 000"]}]
BRIEF = "Собери кампанию на окна: Москва, поиск, 500 ₽ в день"



@pytest.fixture(autouse=True)
def _old_brief(monkeypatch):
    """Сборки здесь — без campaign в брифе (как до черновика с первого шага): черновик создаёт create_campaign.
    Запись в Директ по шагам — tests/test_db_ai_build_sync.py."""
    monkeypatch.setattr(ai_agent, "_need_campaign", lambda args, payload: None)


def _stages(*more):
    return (
        _table("goals", GOALS), _table("analysis", ANALYSIS), _table("keywords", KEYWORDS), _table("ads", ADS),
        _table("settings", SETTINGS, columns=["Настройка", "Значение"]),
        *more,
        _text("Кампания на доске — проверьте и согласуйте."),
    )


def _held(user, chat):
    return [a for a in _cards(user, chat) if a["status"] == "pending" and a["args"].get("held")]


def _fake_direct(db, s, monkeypatch):
    row = make_campaign(db, s["user"], s["acc"], 715000002, "Окна", state="OFF", status="DRAFT")
    created = []
    monkeypatch.setattr(
        actions, "create_campaign", lambda user_id, **kw: created.append(kw) or {"campaign": {"id": row}, "status": "ok"}
    )
    return row, created


def test_detailed_brief_is_built_on_one_board(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    row, created = _fake_direct(db, s, monkeypatch)

    # Пять этапов подряд в одном ходе: таблицы ложатся на доску, ход не останавливается.
    fake, out = _start(user, chat, BRIEF, monkeypatch, *_stages())
    assert out["status"] == "idle" and len(fake.calls) == 6
    first = _reply(fake, 1)
    assert first["on_board"] is True and first["stage"] == "Цели" and first["rows"] == 2
    assert "следующий этап — «Анализ» — готовь его сейчас" in first["note"]
    assert "вся кампания на доске и ждёт одного согласования" in _reply(fake, 5)["note"]
    assert "- Цели: на доске, ждёт общего согласования, строк: 2" in fake.notes[1]
    assert "На доске в группах: «Окна ПВХ» (фраз 2, заголовков 1, текстов 1)." in fake.notes[5]
    assert "Этап, который уже лежит на доске, заново не показывай" in fake.notes[5]

    tables = _held(user, chat)
    assert len(tables) == 5 and all(t["detached"] and t["kind"] == "ask" for t in tables)
    build = ai_agent.get_chat(user, chat)["build"]
    assert build["board"] is True and build["held"] == [t["id"] for t in tables] and build["board_approved"] is False
    assert [(st["key"], st["status"], st["held"]) for st in build["stages"]] == [
        ("goals", "pending", True), ("analysis", "pending", True), ("keywords", "pending", True),
        ("negatives", "pending", True), ("audiences", "skipped", False), ("ads", "pending", True),
        ("images", "skipped", False), ("extras", "skipped", False), ("settings", "pending", True),
    ]
    # В счётчиках и в очереди решений доска — одно решение, а не пять.
    assert [c["pending_actions"] for c in ai_agent.list_chats(user, s["ws"]) if c["id"] == chat] == [1]
    assert ai_agent.pending_count(user) == {"count": 1}
    [item] = ai_agent.pending_actions(user)["items"]
    assert item["id"] == tables[-1]["id"] and item["chat"]["id"] == chat
    assert item["label"] == "Кампания на доске" and "согласуйте одной кнопкой" in item["summary"]
    assert build["missing"] == []  # все обязательные этапы на доске — согласование создаст черновик

    # Маркетолог пишет в чат — доска остаётся; до согласования черновик не создать.
    fake, out = _start(
        user, chat, "Создавай уже", monkeypatch,
        _call("create_campaign", _spec(s), "early"), _call("get_build", {}, "look"), _text("Жду согласования на доске."),
    )
    assert "ещё ждёт согласования маркетолога" in _reply(fake, 1)["error"]
    snapshot = _reply(fake, 2)["build"]
    assert snapshot["groups"][0]["keywords"] == ["купить окна пвх", "окна в рассрочку"]
    assert out["status"] == "idle" and not created and len(_held(user, chat)) == 5

    # Одно согласование на всю доску; «окна в рассрочку» сняты галочкой.
    keywords = next(t for t in tables if t["args"]["stage"] == "keywords")
    decided = ai_agent.decide_build(user, chat, rows={str(keywords["id"]): [1, 3]})
    assert [a["status"] for a in decided["actions"]] == ["done"] * 5 and decided["job"]["kind"] == "ai_chat"
    assert all(a["result"]["board"] is True for a in decided["actions"])
    with pytest.raises(YandexError):  # решать больше нечего
        ai_agent.decide_build(user, chat)

    spec = copy.deepcopy(_spec(s))
    spec["spec"]["groups"][0]["keywords"] = ["купить окна пвх"]
    fake, out = _turn(
        user, chat, monkeypatch,
        _call("create_campaign", _spec(s), "bad"),  # со снятой фразой — нельзя
        _call("create_campaign", spec, "go"),
        _text("Черновик создан."),
    )
    note = fake.calls[0][-1]
    assert note["role"] == "user" and note["content"].startswith("[Служебная заметка: маркетолог принял решение по кампании")
    assert "Маркетолог согласовал кампанию на доске" in note["content"] and "согласовано 2 из 3" in note["content"]
    assert "окна в рассрочку" in note["content"] and "создастся сразу, без второй карточки" in note["content"]
    assert "доска согласована — создавай черновик create_campaign" in fake.notes[0]
    assert "снял галочку" in _reply(fake, 1)["error"]
    # Черновик создан сразу, без второй карточки, — но это решение маркетолога, а не право ИИ.
    assert _reply(fake, 2)["ok"] is True and out["status"] == "idle"
    [kw] = created
    assert kw["spec"]["groups"][0]["keywords"] == ["купить окна пвх"]
    [card] = _cards(user, chat, "create_campaign")
    assert card["status"] == "done" and card["auto"] is False
    build = ai_agent.get_chat(user, chat)["build"]
    assert build["draft"]["status"] == "done" and build["draft"]["campaign_row_id"] == row and build["held"] == []


def test_new_table_replaces_only_its_section(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _start(user, chat, BRIEF, monkeypatch, *_stages())
    ads = next(t for t in _held(user, chat) if t["args"]["stage"] == "ads")

    titles = [
        {"section": "Окна ПВХ", "kind": "title", "cells": ["Окна ПВХ от завода"]},
        {"section": "Окна ПВХ", "kind": "title", "cells": ["Окна за 1 день"]},
    ]
    fake, out = _start(
        user, chat, "Перепиши заголовки и дай два", monkeypatch,
        _table("ads", titles),
        _table("ads", titles),  # то же второй раз — на доске ничего не меняется
        _table("goals", GOALS),
        _text("Заголовки обновил."),
    )
    replaced = _reply(fake, 1)
    assert replaced["on_board"] is True and replaced["replaced_rows"] == 1 and "заменены новыми" in replaced["note"]
    assert _reply(fake, 2)["already_on_board"] is True and _reply(fake, 3)["already_on_board"] is True
    assert out["status"] == "idle"

    # Заменены только заголовки группы: текст остался в прежней таблице, остальные этапы не тронуты.
    held = _held(user, chat)
    assert len(held) == 6
    old = next(t for t in held if t["id"] == ads["id"])
    assert [(r["kind"], r["cells"][0]) for r in old["args"]["rows"]] == [("text", "Замер в день заказа")]
    [group] = ai_agent.get_chat(user, chat)["build"]["groups"]
    assert [t["text"] for t in group["titles"]] == ["Окна ПВХ от завода", "Окна за 1 день"]
    assert [t["text"] for t in group["texts"]] == ["Замер в день заказа"]

    # Таблица, из которой заменили все строки, с доски уходит.
    settings = next(t for t in held if t["args"]["stage"] == "settings")
    _start(
        user, chat, "Бюджет 700", monkeypatch,
        _table("settings", [{"kind": "setting", "cells": ["Бюджет", "700 ₽/день"]}], columns=["Настройка", "Значение"]),
        _text("Бюджет поправил."),
    )
    gone = next(a for a in _cards(user, chat) if a["id"] == settings["id"])
    assert gone["status"] == "rejected" and gone["error"] == "Заменена новой таблицей этапа"
    build = ai_agent.get_chat(user, chat)["build"]
    assert [(i["text"], i["value"]) for i in build["settings"]] == [("Бюджет", "700 ₽/день")]
    assert len(build["held"]) == 6

    # Правка маркетолога на доске — в той же таблице; заметка о решении её перечисляет.
    ai_agent.edit_board(user, chat, [
        {"op": "edit", "kind": "keyword", "group": "Окна ПВХ", "text": "окна в рассрочку", "to": "окна в кредит"},
    ])
    ai_agent.decide_build(user, chat, comment="Запусти только на Москву")
    fake, _ = _turn(user, chat, monkeypatch, _text("Учёл, создаю."))
    note = fake.calls[0][-1]["content"]
    assert "«окна в рассрочку» → «окна в кредит»" in note and "Запусти только на Москву" in note
    assert "покажи изменённый раздел новой таблицей" in note


def test_board_can_be_cancelled(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _row, created = _fake_direct(db, s, monkeypatch)
    _start(user, chat, BRIEF, monkeypatch, *_stages())

    # Отмена без слов: ход не начинается, заметку модель увидит со следующим сообщением.
    decided = ai_agent.decide_build(user, chat, approve=False)
    assert decided["job"] is None and [a["status"] for a in decided["actions"]] == ["rejected"] * 5
    assert {a["error"] for a in decided["actions"]} == {"Маркетолог отменил сборку на доске"}
    assert ai_agent.get_chat(user, chat)["chat"]["status"] == "idle" and ai_agent.pending_count(user) == {"count": 0}
    fake, _ = _start(
        user, chat, "Передумал, пока не надо", monkeypatch, _call("create_campaign", _spec(s), "no"), _text("Хорошо.")
    )
    assert any("Маркетолог отменил сборку кампании на доске" in str(m.get("content")) for m in fake.calls[0])
    assert "Ещё не согласовано" in _reply(fake, 1)["error"] and not created

    # Отмена с комментарием — ИИ сразу берётся переделывать, и снова одной доской.
    _start(user, chat, BRIEF, monkeypatch, *_stages())
    decided = ai_agent.decide_build(user, chat, approve=False, comment="Сделай только на Подмосковье")
    assert decided["job"]["kind"] == "ai_chat"
    fake, out = _turn(user, chat, monkeypatch, _table("goals", [{"cells": ["Гео", "Подмосковье"]}]), _text("Переделал."))
    note = fake.calls[0][-1]
    assert note["role"] == "user" and "Сделай только на Подмосковье" in note["content"]
    assert "покажи исправленное новыми таблицами" in note["content"]
    assert _reply(fake, 1)["on_board"] is True and out["status"] == "idle" and len(_held(user, chat)) == 1


def test_table_on_board_is_decided_alone(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _row, created = _fake_direct(db, s, monkeypatch)
    _start(user, chat, BRIEF, monkeypatch, *_stages())
    tables = {t["args"]["stage"]: t for t in _held(user, chat)}

    # Одну таблицу отклонили своей кнопкой: остальные лежат, ИИ показывает этап заново.
    out = ai_agent.decide_items(user, tables["ads"]["id"], approve=False, comment="Заголовки слабые")
    assert out["action"]["status"] == "rejected" and out["action"]["error"] == "Пользователь не согласовал таблицу"
    assert out["job"]["kind"] == "ai_chat" and len(_held(user, chat)) == 4
    better = [
        {"section": "Окна ПВХ", "kind": "title", "cells": ["Окна с монтажом за день"]},
        {"section": "Окна ПВХ", "kind": "text", "cells": ["Замер в день заказа"]},
    ]
    fake, _ = _turn(user, chat, monkeypatch, _table("ads", better), _text("Переписал заголовки."))
    note = fake.calls[0][-1]["content"]
    assert "Маркетолог отклонил таблицу на доске" in note and "Заголовки слабые" in note
    assert _reply(fake, 1)["on_board"] is True  # режим сборки сам не меняется

    # Таблицы согласовали по отдельности — общего решения не было, черновик идёт обычной карточкой.
    with pytest.raises(YandexError) as err:
        ai_agent.decide_items(user, tables["goals"]["id"])
    assert "Отметьте хотя бы одну строку" in err.value.message
    done = ai_agent.decide_items(user, tables["goals"]["id"], rows=[1, 2])
    assert done["action"]["status"] == "done" and "board" not in done["action"]["result"]
    with pytest.raises(YandexError) as err:  # ИИ уже отвечает на это решение
        ai_agent.decide_build(user, chat)
    assert err.value.code == "busy"
    _turn(user, chat, monkeypatch, _text("Жду остальные таблицы."))
    rest = ai_agent.decide_build(user, chat, only=[t["id"] for t in _held(user, chat)])
    assert len(rest["actions"]) == 4 and all("board" not in a["result"] for a in rest["actions"])
    spec = copy.deepcopy(_spec(s))
    spec["spec"]["groups"][0]["ad"]["titles"] = ["Окна с монтажом за день"]
    fake, out = _turn(user, chat, monkeypatch, _call("create_campaign", spec, "go"))
    assert "Маркетолог согласовал таблицу на доске" in fake.calls[0][-1]["content"]
    assert out["status"] == "waiting" and not created
    assert _pending(user, chat, "create_campaign")["args"]["spec"]["name"] == "Окна"


def test_required_stage_without_rows_goes_back(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    extras = [{"kind": "callout", "cells": ["Гарантия 5 лет"]}]
    _start(user, chat, BRIEF, monkeypatch, *_stages(_table("extras", extras)))
    tables = {t["args"]["stage"]: t for t in _held(user, chat)}
    assert len(tables) == 6

    with pytest.raises(YandexError) as err:  # сняли всё — согласовывать нечего
        ai_agent.decide_build(user, chat, rows={str(t["id"]): [] for t in tables.values()})
    assert "Отметьте хотя бы одну строку" in err.value.message

    # Сняли все строки: необязательный этап пропущен, обязательный — возвращается на доработку.
    decided = ai_agent.decide_build(user, chat, rows={str(tables["extras"]["id"]): [], str(tables["ads"]["id"]): []})
    states = {a["args"]["stage"]: a["status"] for a in decided["actions"]}
    assert states == {
        "goals": "done", "analysis": "done", "keywords": "done", "ads": "rejected", "extras": "done", "settings": "done",
    }
    fake, out = _turn(user, chat, monkeypatch, _call("create_campaign", _spec(s), "early"), _text("Покажу объявления заново."))
    assert "следующий этап — «Объявления»" in fake.calls[0][-1]["content"]
    assert "Ещё не согласовано: Заголовки и тексты" in _reply(fake, 1)["error"] and out["status"] == "idle"


def test_board_waits_for_the_answer_in_chat(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    ask = _call("ask_questions", {"understanding": "x", "questions": [{"question": "Бюджет?", "options": ["1", "2"]}]}, "q1")
    _fake, out = _start(user, chat, BRIEF, monkeypatch, _table("goals", GOALS), _table("analysis", ANALYSIS), ask)
    assert out["status"] == "waiting" and len(_held(user, chat)) == 2

    # Пока ИИ ждёт ответа на вопрос, доску не согласовать: ответ на вопрос должен встать сразу за вопросом.
    with pytest.raises(YandexError) as err:
        ai_agent.decide_build(user, chat)
    assert "сначала ответьте ему" in err.value.message and len(_held(user, chat)) == 2

    ai_agent.answer_questions(user, _pending(user, chat, "ask_questions")["id"], answers=[{"n": 1, "selected": ["1"]}])
    _turn(user, chat, monkeypatch, _text("Понял, готовлю дальше."))
    decided = ai_agent.decide_build(user, chat)
    assert [a["status"] for a in decided["actions"]] == ["done", "done"]


def test_stage_by_stage_and_ready_campaign_tables_wait(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]

    # Заголовки для готовой кампании (сборки нет) — обычная карточка, ход ждёт решения.
    titles = [{"section": "Поиск", "kind": "title", "cells": ["Окна от завода"]}]
    _fake, out = _start(user, chat, "Предложи заголовки для «Поиск»", monkeypatch, _table("ads", titles))
    card = _pending(user, chat, "propose_items")
    assert out["status"] == "waiting" and card["detached"] is False and not card["args"].get("held")
    ai_agent.decide_items(user, card["id"], approve=False)
    _turn(user, chat, monkeypatch, _text("Хорошо."))

    # По шагам — только с by_stages: таблица ждёт своего решения, следующие идут так же.
    _fake, out = _start(user, chat, "Собери кампанию, но по шагам", monkeypatch, _table("goals", GOALS, by_stages=True))
    assert out["status"] == "waiting" and _held(user, chat) == []
    table = _pending(user, chat, "propose_items")
    ai_agent.decide_items(user, table["id"], rows=[1, 2])
    _fake, out = _turn(user, chat, monkeypatch, _table("analysis", ANALYSIS, by_stages=True))
    assert out["status"] == "waiting" and _held(user, chat) == []
    with pytest.raises(YandexError):  # общего согласования у пошаговой сборки нет
        ai_agent.decide_build(user, chat)

    # Маркетолог попросил дальше не останавливаться — by_stages=false, и остаток ложится на доску.
    ai_agent.decide_items(user, _pending(user, chat, "propose_items")["id"], rows=[1])
    fake, out = _turn(
        user, chat, monkeypatch,
        _table("keywords", KEYWORDS, by_stages=False), _table("ads", ADS), _text("Остальное — на доске."),
    )
    assert _reply(fake, 1)["on_board"] is True and _reply(fake, 2)["on_board"] is True
    assert out["status"] == "idle" and len(_held(user, chat)) == 2


def test_board_decision_over_api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.web_auth import make_session

    s = _seed(db)
    user, chat = s["user"], s["chat"]
    _start(user, chat, BRIEF, monkeypatch, *_stages())
    tables = {t["args"]["stage"]: t for t in _held(user, chat)}
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}

    out = client.post(
        f"/api/web/ai/chats/{chat}/board/decide", headers=auth,
        json={"rows": {str(tables["keywords"]["id"]): [1, 3]}, "comment": ""},
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert len(body["actions"]) == 5 and body["job"]["kind"] == "ai_chat"
    done = next(a for a in body["actions"] if a["id"] == tables["keywords"]["id"])
    assert done["result"]["approved_rows"] == [1, 3] and done["result"]["removed_rows"] == [2]
    again = client.post(f"/api/web/ai/chats/{chat}/board/decide", headers=auth, json={})
    assert again.status_code >= 400 and len(_held(user, chat)) == 0  # ход уже идёт, решать больше нечего


def test_wizard_is_default_and_optional_step_skips(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]

    # Мастер по шагам (пункт 3 docs/board-plan.md) — режим по умолчанию: таблица без by_stages ждёт «Дальше».
    goals = _call("propose_items", {"stage": "goals", "rows": GOALS, "requirements": {}}, "t-goals")
    _fake, out = _start(user, chat, BRIEF, monkeypatch, goals)
    assert out["status"] == "waiting" and _held(user, chat) == []
    ai_agent.decide_items(user, _pending(user, chat, "propose_items")["id"], rows=[1, 2])
    for stage, rows in (("analysis", ANALYSIS), ("keywords", KEYWORDS), ("ads", ADS)):
        _fake, out = _turn(user, chat, monkeypatch, _table(stage, rows, by_stages=True))
        assert out["status"] == "waiting" and _held(user, chat) == []
        table = _pending(user, chat, "propose_items")
        if stage == "ads":  # обязательный этап не пропустить
            with pytest.raises(YandexError) as err:
                ai_agent.decide_items(user, table["id"], skip=True)
            assert "обязательный" in err.value.message
        ai_agent.decide_items(user, table["id"], rows=[r["n"] for r in table["args"]["rows"]])

    extras = [{"kind": "callout", "cells": ["Гарантия 5 лет"]}]
    _turn(user, chat, monkeypatch, _table("extras", extras, by_stages=True))
    table = _pending(user, chat, "propose_items")
    ai_agent.decide_items(user, table["id"], skip=True)
    fake, _out = _turn(user, chat, monkeypatch, _text("Дальше — настройки."))
    assert "пропустил этот необязательный этап" in fake.calls[0][-1]["content"]
    build = ai_agent.campaign_build(_cards(user, chat))
    assert {st["key"]: st["status"] for st in build["stages"]}["extras"] == "skipped"
    assert build["callouts"] == [] or all(not c.get("checked", True) for c in build["callouts"])


def test_held_optional_table_skips_alone(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    extras = [{"kind": "callout", "cells": ["Гарантия 5 лет"]}]
    _start(user, chat, BRIEF, monkeypatch, *_stages(_table("extras", extras)))
    tables = {t["args"]["stage"]: t for t in _held(user, chat)}
    # «Пропустить» шага старой доски: одна необязательная таблица без строк — пропуск, а не ошибка.
    out = ai_agent.decide_build(user, chat, rows={str(tables["extras"]["id"]): []}, only=[tables["extras"]["id"]])
    assert [a["status"] for a in out["actions"]] == ["done"]
    assert len(_held(user, chat)) == 5
