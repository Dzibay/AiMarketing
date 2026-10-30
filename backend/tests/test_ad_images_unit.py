"""Картинки объявлений новой кампании: генерация по API пользователя, вопрос об источнике, этап «Картинки»
в чате, доска и создание кампании — без БД и сети."""

import base64
from types import SimpleNamespace

import httpx
import pytest

from app import actions, ad_extras, ai, ai_agent, audiences, image_gen
from app.workspaces import normalize_ai_access
from app.yandex import YandexError

ACCOUNT = {"id": 3, "client_login": "acc-a", "name": "Кабинет"}
OTHER = {"id": 4, "client_login": "acc-b", "name": "Второй"}
JPEG = b"\xff\xd8\xff" + b"0" * 64


class FakeContext:
    require_account = ai_agent.Context.require_account
    account_ids = ai_agent.Context.account_ids

    def __init__(self, ws: dict | None = None, chat_id: int | None = 5):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "accounts": [ACCOUNT, OTHER], **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))
        self.chat_id = chat_id
        self.board_ops = []


@pytest.fixture
def library(monkeypatch):
    """Библиотеки картинок кабинетов в памяти: {account_id: {hash: preview_url}}; calls — запросы превью."""
    state = SimpleNamespace(
        images={3: {"h1": "https://img/h1", "h2": "https://img/h2"}, 4: {"x1": "https://img/x1"}},
        logins={"acc-a": 3, "acc-b": 4},
        calls=[],
        error=None,
    )

    def previews(token, login, hashes):
        state.calls.append((login, list(hashes)))
        if state.error:
            raise YandexError("api", state.error)
        have = state.images[state.logins[login]]
        return {h: have[h] for h in hashes if h in have}

    monkeypatch.setattr(audiences, "account_access", lambda user_id, account_id: (f"t{account_id}", {3: "acc-a", 4: "acc-b"}[account_id]))
    monkeypatch.setattr(ad_extras, "image_previews", previews)
    return state


# --- Генерация по API пользователя ------------------------------------------------------------------


def _cfg(provider="openai", model="gpt-image-1", **extra):
    base = image_gen.YANDEX_BASE if provider == "yandexart" else image_gen.OPENAI_BASE
    return {"provider": provider, "base_url": base, "model": model, "model_name": model, "headers": {},
            "check_host": False, "user_id": 1, "workspace_id": 2, "task": "images", "price": None, "limit_rub": None, **extra}


def _reply(status=200, data=None):
    body = {"data": [data if data is not None else {"b64_json": base64.b64encode(JPEG).decode()}]}
    return httpx.Response(status, json=body if status < 400 else {"error": {"message": "нет"}})


def test_request_bodies_and_answers(monkeypatch):
    sent = []

    def post(cfg, body):
        sent.append(body)
        return _reply()

    monkeypatch.setattr(image_gen, "_post", post)
    assert image_gen._request(_cfg(), "окно") == JPEG
    # gpt-image — JPEG: PNG 1024×1024 бывает больше 5 МБ.
    assert sent[-1] == {"model": "gpt-image-1", "prompt": "окно", "n": 1, "size": "1024x1024", "output_format": "jpeg"}
    image_gen._request(_cfg("yandexart", "art://b1g/aliceai-image-art-3.0"), "окно")
    assert "output_format" not in sent[-1] and sent[-1]["model"] == "art://b1g/aliceai-image-art-3.0"


@pytest.mark.parametrize(
    "reply, code",
    [
        (_reply(401), "image-auth"),
        (_reply(429), "image-limit"),
        (_reply(500), "image-error"),
        (_reply(data={"b64_json": base64.b64encode(b"<html>").decode()}), "image-error"),
        (_reply(data={"b64_json": base64.b64encode(b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)).decode()}), "image-error"),
        (_reply(data={}), "image-error"),
    ],
)
def test_request_errors(monkeypatch, reply, code):
    monkeypatch.setattr(image_gen, "_post", lambda cfg, body: reply)
    with pytest.raises(YandexError) as err:
        image_gen._request(_cfg(), "окно")
    assert err.value.code == code


def test_generate_counts_budget_and_partial_errors(monkeypatch):
    cfg = _cfg(price=40.0)
    monkeypatch.setattr(image_gen, "load_config", lambda user_id, workspace_id=None: cfg)
    monkeypatch.setattr(ai, "check_budget", lambda c: None)
    monkeypatch.setattr(ai, "budget_left", lambda c: 100.0)
    recorded = []
    monkeypatch.setattr(image_gen, "_record", lambda c, prompt: recorded.append(prompt))
    calls = []

    def request(c, prompt):
        calls.append(prompt)
        if len(calls) == 2:
            raise YandexError("image-error", "Генератор вернул не JPG")
        return JPEG

    monkeypatch.setattr(image_gen, "_request", request)
    # Просили 4, а лимита хватает на 2 (100 ₽ / 40 ₽); одна не получилась — учтена только удачная.
    out = image_gen.generate(1, "  окно   в доме ", count=4)
    assert (len(calls), out["images"], out["errors"], out["model"]) == (2, [JPEG], ["Генератор вернул не JPG"], "gpt-image-1")
    assert recorded == ["окно в доме"]

    monkeypatch.setattr(image_gen, "_request", lambda c, prompt: (_ for _ in ()).throw(YandexError("image-auth", "ключ")))
    with pytest.raises(YandexError) as err:
        image_gen.generate(1, "окно", count=1)
    assert err.value.code == "image-auth"
    with pytest.raises(YandexError):
        image_gen.generate(1, "   ")
    assert len(image_gen.clean_prompt("я" * 900, "yandexart")) == 500


def test_upload_keeps_paid_images(monkeypatch):
    names = []

    def upload_image(token, login, *, name, data_b64):
        names.append(name)
        if len(names) == 2:
            raise YandexError("api", "Слишком маленькая")
        return {"hash": f"h{len(names)}", "preview_url": f"https://img/{len(names)}"}

    monkeypatch.setattr(ad_extras, "upload_image", upload_image)
    out, errors = image_gen.upload("t", "acc-a", [JPEG, JPEG, JPEG], name="Окна")
    assert [i["hash"] for i in out] == ["h1", "h3"] and errors == ["Слишком маленькая"]
    assert names == ["Окна 1", "Окна 2", "Окна 3"]
    assert image_gen.to_data_url(JPEG).startswith("data:image/jpeg;base64,")


def test_generate_tool_guards(monkeypatch, library):
    monkeypatch.setattr(image_gen, "is_configured", lambda user_id: False)
    with pytest.raises(YandexError) as err:
        ai_agent._t_generate_images(FakeContext(), {"prompt": "окно"})
    assert err.value.code == "image-not-configured" and "из других кампаний" in err.value.message

    monkeypatch.setattr(image_gen, "is_configured", lambda user_id: True)
    monkeypatch.setattr(ai_agent, "_wants_generation", lambda ctx: False)
    with pytest.raises(YandexError) as err:
        ai_agent._t_generate_images(FakeContext(), {"prompt": "окно"})
    assert "сначала спроси" in err.value.message and ai_agent.IMAGE_SOURCE_KEY in err.value.message

    monkeypatch.setattr(ai_agent, "_wants_generation", lambda ctx: True)
    with pytest.raises(YandexError) as err:  # два кабинета — какой, должна сказать модель
        ai_agent._t_generate_images(FakeContext(), {"prompt": "окно"})
    assert "account_id" in err.value.message

    asked = []
    monkeypatch.setattr(image_gen, "generate", lambda user_id, prompt, count, workspace_id: asked.append((prompt, count)) or {
        "images": [JPEG, JPEG], "errors": [], "model": "m"})
    monkeypatch.setattr(image_gen, "upload", lambda token, login, images, name: (
        [{"hash": "g1", "preview_url": "u1"}], [f"{login}: отказ"]))
    out = ai_agent._t_generate_images(FakeContext(), {"prompt": "окно", "account_id": 4, "group": "Окна"})
    assert asked == [("окно", 2)]  # по умолчанию две на выбор
    assert out["images"] == [{"hash": "g1", "account_id": 4}] and out["errors"] == ["acc-b: отказ"]

    monkeypatch.setattr(image_gen, "upload", lambda token, login, images, name: ([], ["не принял"]))
    with pytest.raises(YandexError) as err:
        ai_agent._t_generate_images(FakeContext(), {"prompt": "окно", "account_id": 3})
    assert "не принял" in err.value.message
    # Без чата (вызов не из ассистента) спрашивать некого.
    assert ai_agent._wants_generation(SimpleNamespace(chat_id=None)) is True


def test_board_generate_and_library_check_workspace(monkeypatch):
    monkeypatch.setattr(ai_agent, "require_workspace", lambda user_id, ws_id: {"id": ws_id})
    monkeypatch.setattr(ai_agent, "list_workspace_accounts", lambda user_id, ws_id: [ACCOUNT])
    with pytest.raises(YandexError) as err:
        ai_agent.generate_images(1, 2, account_id=4, prompt="окно")
    assert err.value.code == "scope"
    with pytest.raises(YandexError):
        ai_agent.generate_images(1, 2, account_id=None, prompt="окно")
    seen = []
    monkeypatch.setattr(ai_agent, "_generate_into", lambda *a, **k: seen.append((a, k)) or {"images": []})
    ai_agent.generate_images(1, 2, account_id="3", prompt="окно", count=3)
    assert seen == [((1, 2, 3, "окно", 3), {"name": "ИИ"})]


# --- Вопрос: откуда брать картинки -----------------------------------------------------------------


def test_image_source_question_has_our_options():
    out = ai_agent._q_questions(FakeContext(), {"questions": [
        {"question": "Как поступим с картинками?", "key": "image_source", "options": ["Нарисуй сам"], "remember": True},
        {"question": "Ещё раз про картинки", "key": "image_source"},
    ]})
    question, again = out["questions"]
    assert again["key"] == "" and again["options"] == []  # второй с тем же key — обычный вопрос без памяти
    assert question["options"] == ["Взять из других кампаний", "Загружу свои", "Сгенерировать по описанию", "Без картинок"]
    assert question["multiple"] is True and question["remember"] is False
    assert question["needs"] == {"Сгенерировать по описанию": "image_gen"}
    # Ответ — на эту кампанию: прошлый ответ из памяти пространства не подставляем.
    remembered = FakeContext(ws={"ai_facts": [{"key": "image_source", "question": "Картинки?", "answer": "Загружу свои"}]})
    assert ai_agent._q_questions(remembered, {"questions": [{"question": "Картинки?", "key": "image_source"}]})["questions"]


def test_answer_needs_connected_generation():
    question = ai_agent._q_questions(FakeContext(), {"questions": [{"question": "Картинки?", "key": "image_source"}]})["questions"]
    generate = {"n": 1, "selected": ["Сгенерировать по описанию", "Загружу свои"]}
    with pytest.raises(YandexError) as err:
        ai_agent._compose_answers(question, [generate])
    assert "недоступно" in err.value.message and "Настройки → Генерация картинок" in err.value.message
    out, facts = ai_agent._compose_answers(question, [generate], {"image_gen"})
    assert out == [{"question": "Картинки?", "answer": "Сгенерировать по описанию; Загружу свои"}] and facts == []
    out, _ = ai_agent._compose_answers(question, [{"n": 1, "selected": ["Взять из других кампаний"], "text": "из летней"}])
    assert out[0]["answer"] == "Взять из других кампаний; из летней"


# --- Этап «Картинки» в чате ------------------------------------------------------------------------


def _images_table(*rows, **args):
    return ai_agent._q_items(FakeContext(**args.pop("ctx", {})), {"stage": "images", "rows": list(rows), **args})


def test_items_images_rows(library):
    out = _images_table(
        {"section": "Окна", "kind": "image", "cells": ["Окно в интерьере", "из «Окна — поиск»", "лучший CTR"],
         "image": {"hash": "h1"}},
        {"section": "Окна", "kind": "image", "cells": ["Чужой кабинет", "из «Двери»"], "image": {"hash": "x1", "account_id": 4}},
        {"section": "Окна", "kind": "image", "cells": ["Повтор"], "image": "h1"},
        account_id=3,
    )
    assert out["columns"] == ["Картинка", "Откуда", "Почему"] and out["account_id"] == 3
    first, other = out["rows"]
    assert first["image"] == {"hash": "h1", "account_id": 3, "preview_url": "https://img/h1"}
    assert other["image"] == {"hash": "x1", "account_id": 4, "preview_url": "https://img/x1"}
    assert other["cells"] == ["Чужой кабинет", "из «Двери»", ""]
    # В других этапах kind image не бывает, а в этапе картинок — только он.
    ads = ai_agent._q_items(FakeContext(), {"stage": "keywords", "rows": [{"kind": "image", "cells": ["окна"]}]})
    assert ads["rows"][0]["kind"] == "keyword"
    # Директ не ответил — превью нет, картинки проверит создание кампании.
    library.error = "Таймаут"
    assert "preview_url" not in _images_table({"section": "А", "cells": ["Х"], "image": {"hash": "zz"}}, account_id=3)["rows"][0]["image"]


@pytest.mark.parametrize(
    "row, args, text",
    [
        ({"cells": ["Х"], "image": {"hash": "h1"}}, {"account_id": 3}, "section — группа"),
        ({"section": "А", "cells": ["Х"], "image": {"hash": "не хэш"}}, {"account_id": 3}, "image.hash"),
        ({"section": "А", "cells": ["Х"], "image": None}, {"account_id": 3}, "image — объект"),
        ({"section": "А", "cells": ["Х"], "image": {"hash": "h1", "account_id": "три"}}, {}, "image.account_id — число"),
        ({"section": "А", "cells": ["Х"], "image": {"hash": "h9"}}, {"account_id": 3}, "нет в библиотеке кабинета 3"),
        ({"section": "А", "cells": ["Х"], "image": {"hash": "h1", "account_id": 9}}, {}, "не подключён"),
        ({"section": "А", "cells": ["Х"], "image": {"hash": "h1"}}, {}, "account_id"),
    ],
)
def test_items_images_errors(library, row, args, text):
    with pytest.raises(YandexError) as err:
        _images_table(row, **args)
    assert text in err.value.message


def test_items_images_group_limit(library):
    library.images[3].update({f"g{i}": f"https://img/g{i}" for i in range(6)})
    rows = [{"section": "Окна", "cells": [f"№{i}"], "image": {"hash": f"g{i}"}} for i in range(6)]
    with pytest.raises(YandexError) as err:
        _images_table(*rows, account_id=3)
    assert "до 5 картинок" in err.value.message and "«Окна»" in err.value.message
    rows[5]["checked"] = False  # лишняя без галочки — можно
    assert len(_images_table(*rows, account_id=3)["rows"]) == 6


def _card(rows, approved, stage="images"):
    rows = [{"n": i + 1, "checked": True, **r} for i, r in enumerate(rows)]
    return {"tool": "propose_items", "args": {"stage": stage, "rows": rows}, "result": {"approved_rows": approved}}


def _img_row(group, image_hash, account_id=3):
    return {"section": group, "kind": "image", "cells": [image_hash], "image": {"hash": image_hash, "account_id": account_id}}


def test_approved_images_follow_tables_and_board():
    cards = [
        _card([_img_row("Окна", "h1"), _img_row("Окна", "h2"), _img_row("Двери", "x1", 4)], [1, 3]),
        {"tool": "board_edit", "args": {"ops": [
            {"op": "rename_group", "group": "Окна", "to": "Окна ПВХ"},
            {"op": "add", "kind": "image", "group": "Окна ПВХ", "text": "u1", "image": {"account_id": 3}},
        ]}, "result": None},
    ]
    approved = ai_agent._approved_images(cards, [])
    assert [(i["hash"], i["group"], i["account_id"]) for i in approved] == [
        ("h1", "Окна ПВХ", 3), ("x1", "Двери", 4), ("u1", "Окна ПВХ", 3),
    ]
    removed = {"op": "remove", "kind": "image", "group": "Окна ПВХ", "text": "h1"}
    assert [i["hash"] for i in ai_agent._approved_images(cards, [removed])] == ["x1", "u1"]
    # Новая таблица предложила картинку заново, а галочку сняли — не согласована.
    again = _card([_img_row("Двери", "x1", 4)], [])
    assert [i["hash"] for i in ai_agent._approved_images(cards + [again], [])] == ["h1", "u1"]


def test_require_images(monkeypatch):
    approved = ai_agent._approved_images([_card([_img_row("Окна", "h1")], [1])], [])
    monkeypatch.setattr(ai_agent, "_image_approvals", lambda ctx: approved)
    ai_agent._require_images(FakeContext(), [("Окна", "h1")])
    with pytest.raises(YandexError) as err:
        ai_agent._require_images(FakeContext(), [("Двери", "h1"), ("Окна", "h9")])
    assert "h1 согласована для группы «Окна», а не «Двери»" in err.value.message and "h9 (группа «Окна»)" in err.value.message
    monkeypatch.setattr(ai_agent, "_image_approvals", lambda ctx: pytest.fail("не из чата — не проверяем"))
    ai_agent._require_images(FakeContext(chat_id=None), [("Двери", "h1")])


def test_create_campaign_counts_and_checks_images(monkeypatch):
    monkeypatch.setattr(ai_agent, "_stage_approvals", lambda ctx: (
        {"goals", "analysis", "keywords", "ads", "settings"},
        {ai_agent._item_key(t, k) for t in ("купить окна",) for k in ("keyword",)}
        | {ai_agent._item_key(t) for t in ("Окна от завода", "Замер бесплатно")},
        "",
    ))
    monkeypatch.setattr(ai_agent, "_image_approvals", lambda ctx: [{"hash": "h1", "account_id": 3, "group": "Окна"}])
    group = {"name": "Окна", "keywords": ["купить окна"], "ad": {
        "titles": ["Окна от завода"], "texts": ["Замер бесплатно"], "href": "https://okna.ru", "extras": {"image_hashes": ["h1"]}}}
    spec = {"name": "Окна", "strategy": {"search": "HIGHEST_POSITION"}, "groups": [group]}
    plan = ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": spec})
    assert plan.summary.endswith("1 группа, 1 фраза, 1 объявление, 1 картинка)")
    group["ad"]["extras"]["image_hashes"] = ["h1", "h2"]
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": spec})
    assert "Этих картинок маркетолог не согласовывал" in err.value.message and "h2" in err.value.message


def test_bring_images_copies_from_other_account(monkeypatch, library):
    monkeypatch.setattr(ai_agent, "_image_approvals", lambda ctx: [
        {"hash": "x1", "account_id": 4, "group": "Окна"}, {"hash": "h1", "account_id": 3, "group": "Окна"},
    ])
    copies = []

    def copy(src_token, src_login, token, login, hashes, warnings):
        copies.append((src_login, login, sorted(hashes)))
        return {"x1": "new1"}

    monkeypatch.setattr(actions, "_copy_images", copy)
    groups = [
        {"name": "Окна", "ad": {"extras": {"image_hashes": ["h1", "x1", "gone"]}}},
        {"name": "Двери", "ad": {"extras": {"image_hashes": ["gone"]}}},
        {"name": "Без картинок", "ad": {"titles": ["Х"]}},
    ]
    warnings = ai_agent._bring_images(FakeContext(), groups, 3)
    assert copies == [("acc-b", "acc-a", ["x1"])]
    assert groups[0]["ad"]["extras"]["image_hashes"] == ["h1", "new1"]
    assert "image_hashes" not in groups[1]["ad"]["extras"]
    assert warnings == ["Нет в библиотеке кабинета, объявления выйдут без них: 1 картинка"]
    # Всё уже в кабинете — копировать нечего; библиотека не ответила — проверит создание объявлений.
    assert ai_agent._bring_images(FakeContext(), [{"name": "А", "ad": {"extras": {"image_hashes": ["h1"]}}}], 3) == []
    library.error = "Таймаут"
    assert ai_agent._bring_images(FakeContext(), [{"name": "А", "ad": {"extras": {"image_hashes": ["zz"]}}}], 3) == []


# --- Доска ---------------------------------------------------------------------------------------------


def _table(action_id, stage, rows, status="done", approved=None, **args):
    rows = [{"n": i + 1, "section": "", "kind": "", "checked": True, **r} for i, r in enumerate(rows)]
    result = {"approved_rows": approved if approved is not None else [r["n"] for r in rows]} if status == "done" else None
    return {"id": action_id, "tool": "propose_items", "status": status, "args": {"stage": stage, "rows": rows, **args},
            "result": result}


def _image(group, image_hash, name, account_id=3):
    return {"section": group, "kind": "image", "cells": [name, "из «Окна — поиск»", "лучший CTR"],
            "image": {"hash": image_hash, "account_id": account_id, "preview_url": f"https://img/{image_hash}"}}


def _board_rows(images_status="done"):
    return [
        _table(0, "goals", [{"cells": ["Ссылка", "https://okna.ru"]}]),
        _table(1, "keywords", [{"section": "Окна", "cells": ["купить окна", "9 000"]},
                               {"section": "Двери", "cells": ["купить двери", "5 000"]}]),
        _table(2, "ads", [
            {"section": "Окна", "kind": "title", "cells": ["Окна от завода"]},
            {"section": "Окна", "kind": "text", "cells": ["Замер бесплатно"]},
            {"section": "Двери", "kind": "title", "cells": ["Двери от завода"]},
            {"section": "Двери", "kind": "text", "cells": ["Монтаж за день"]},
        ]),
        _table(3, "images", [_image("Окна", "h1", "Окно в интерьере"), _image("Окна", "h2", "Замерщик")],
               status=images_status, approved=[1], account_id=3),
    ]


def test_campaign_build_shows_images():
    build = ai_agent.campaign_build(_board_rows())
    okna, doors = build["groups"]
    assert [(i["text"], i["hash"], i["account_id"], i["preview_url"], i["note"]) for i in okna["images"]] == [
        ("Окно в интерьере", "h1", 3, "https://img/h1", "лучший CTR"),
    ]
    assert doors["images"] == [] and build["account_id"] == 3
    stage = next(s for s in build["stages"] if s["key"] == "images")
    assert (stage["status"], stage["count"], stage["action_id"]) == ("done", 1, 3)
    checks = {c["key"]: (c["state"], c["note"]) for c in build["checks"]}
    assert checks["images"] == ("warn", "1 картинка; без картинок: «Двери»")

    pending = ai_agent.campaign_build(_board_rows("pending"))
    assert [(i["pending"], i["action_id"], i["n"]) for i in pending["groups"][0]["images"]] == [(True, 3, 1), (True, 3, 2)]
    # Без картинок — предупреждение, не ошибка: этап необязательный.
    bare = {c["key"]: c["state"] for c in ai_agent.campaign_build(_board_rows()[:3])["checks"]}
    assert bare["images"] == "warn"


def test_build_account_skips_rejected_cards():
    rows = _board_rows()
    rows[3]["args"]["account_id"] = 4
    rows.append({"id": 5, "tool": "create_campaign", "status": "rejected", "args": {"account_id": 3, "spec": {}}})
    assert ai_agent.campaign_build(rows)["account_id"] == 4
    # Кабинета в карточках нет — по картинкам.
    del rows[3]["args"]["account_id"]
    assert ai_agent.campaign_build(rows[:4])["account_id"] == 3


def test_board_image_ops():
    with pytest.raises(YandexError) as err:
        ai_agent._board_op({"op": "add", "kind": "image", "group": "Окна", "text": "h3", "name": "Моя"})
    assert "кабинет" in err.value.message
    with pytest.raises(YandexError) as err:
        ai_agent._board_op({"op": "edit", "kind": "image", "group": "Окна", "text": "h1", "to": "h3"})
    assert "добавить или убрать" in err.value.message
    with pytest.raises(YandexError) as err:
        ai_agent._board_op({"op": "remove", "kind": "image", "group": "Окна", "text": "не хэш"})
    assert "хэша" in err.value.message

    add = ai_agent._board_op({"op": "add", "kind": "image", "group": "Окна", "text": "h3", "name": "Моя", "account_id": "3"})
    assert add == {"op": "add", "group": "Окна", "kind": "image", "text": "h3", "name": "Моя", "image": {"account_id": 3}}
    assert ai_agent._board_note(add) == "добавил картинку «Моя» в группу «Окна»: image_hash h3"
    build = ai_agent.campaign_build(_board_rows())
    assert ai_agent._board_validate(build, add) is None
    with pytest.raises(YandexError) as err:
        ai_agent._board_validate(build, {**add, "text": "h1"})
    assert "уже есть" in err.value.message
    full = ai_agent.campaign_build(_board_rows()[:3] + [_table(
        3, "images", [_image("Окна", f"g{i}", f"№{i}") for i in range(5)], account_id=3)])
    with pytest.raises(YandexError) as err:
        ai_agent._board_validate(full, add)
    assert "уже 5 картинок" in err.value.message

    remove = ai_agent._board_op({"op": "remove", "kind": "image", "group": "Окна", "text": "h1", "name": "Окно"})
    assert ai_agent._board_validate(build, remove)["hash"] == "h1"
    assert ai_agent._board_note(remove) == "убрал картинку «Окно» из группы «Окна»: image_hash h1"

    # Записанные правки — на доске: своя картинка с превью, убранная пропала.
    edits = {"id": 4, "tool": "board_edit", "status": "done", "args": {"ops": [
        {**add, "image": {"account_id": 3, "preview_url": "https://img/h3"}}, remove,
    ]}}
    okna = ai_agent.campaign_build(_board_rows() + [edits])["groups"][0]
    assert [(i["text"], i["hash"], i["own"], i["preview_url"]) for i in okna["images"]] == [
        ("Моя", "h3", True, "https://img/h3"),
    ]


def test_board_patches_pending_images_table_and_spec():
    rows = _board_rows("pending")
    table = rows[3]
    build = ai_agent.campaign_build(rows)
    add = {**ai_agent._board_op({"op": "add", "kind": "image", "group": "Окна", "text": "h3", "name": "Моя", "account_id": 3}),
           "image": {"account_id": 3, "preview_url": "https://img/h3"}}
    assert ai_agent._table_row_patch(table, add, None, build) is True
    new = table["args"]["rows"][-1]
    assert (new["n"], new["kind"], new["cells"], new["added"]) == (3, "image", ["Моя", "добавил маркетолог", ""], True)
    assert new["image"] == {"hash": "h3", "account_id": 3, "preview_url": "https://img/h3"}

    # Предложенную ИИ не удаляют, а снимают галочку; свою — удаляют.
    remove = ai_agent._board_op({"op": "remove", "kind": "image", "group": "Окна", "text": "h1", "name": "Окно"})
    with pytest.raises(YandexError) as err:
        ai_agent._table_row_patch(table, remove, ai_agent._board_validate(build, remove), build)
    assert "Картинку «Окно» предложил ИИ — снимите с неё галочку" in err.value.message
    mine = ai_agent._board_op({"op": "remove", "kind": "image", "group": "Окна", "text": "h3", "name": "Моя"})
    build = ai_agent.campaign_build(rows)
    assert ai_agent._table_row_patch(table, mine, ai_agent._board_validate(build, mine), build) is True
    assert [r["image"]["hash"] for r in table["args"]["rows"]] == ["h1", "h2"]

    spec = {"groups": [{"name": "Окна", "keywords": ["окна"], "ad": {"titles": ["Окна"]}}]}
    ai_agent._spec_patch(spec, add)
    ai_agent._spec_patch(spec, {**add, "text": "h4"})
    assert spec["groups"][0]["ad"]["extras"]["image_hashes"] == ["h3", "h4"]
    ai_agent._spec_patch(spec, {**remove, "text": "h3"})
    ai_agent._spec_patch(spec, {**remove, "text": "h4"})
    assert "image_hashes" not in spec["groups"][0]["ad"]["extras"]
    with pytest.raises(YandexError):
        ai_agent._spec_patch(spec, remove)


def test_create_card_shows_images_with_previews():
    spec = {"name": "Окна", "groups": [
        {"name": "Окна", "keywords": ["купить окна"],
         "ad": {"titles": ["Окна от завода"], "texts": ["Замер бесплатно"], "href": "https://okna.ru",
                "extras": {"image_hashes": ["h1", "h1", "u9"]}}},
    ]}
    create = {"id": 5, "tool": "create_campaign", "status": "pending", "args": {"account_id": 3, "spec": spec}, "summary": "Создать"}
    build = ai_agent.campaign_build(_board_rows() + [create])
    # Превью и подпись — из таблицы этапа по хэшу; незнакомая картинка — без превью.
    assert [(i["text"], i["hash"], i["preview_url"]) for i in build["groups"][0]["images"]] == [
        ("Окно в интерьере", "h1", "https://img/h1"), ("Картинка", "u9", ""),
    ]


def test_board_images_check_account_and_library(monkeypatch, library):
    monkeypatch.setattr(ai_agent, "list_workspace_accounts", lambda user_id, ws_id: [ACCOUNT])
    op = ai_agent._board_op({"op": "add", "kind": "image", "group": "Окна", "text": "h2", "name": "Моя", "account_id": 3})
    ai_agent._board_images(1, 2, [op, {"op": "remove", "kind": "image", "group": "Окна", "text": "h1"}])
    assert op["image"] == {"account_id": 3, "preview_url": "https://img/h2"}
    with pytest.raises(YandexError) as err:
        ai_agent._board_images(1, 2, [{**op, "text": "h9", "image": {"account_id": 3}}])
    assert "загрузите её заново" in err.value.message
    with pytest.raises(YandexError) as err:
        ai_agent._board_images(1, 2, [{**op, "image": {"account_id": 4}}])
    assert err.value.code == "scope"
