"""Ссылка маркетолога со своим ref: сервис не подменяет её молча, а спрашивает и запоминает выбор за кампанией."""

import json

import pytest

from app import actions, ads, ai_agent, links, undo
from app.adgroups import upsert_adgroups
from app.ads import upsert_ads
from app.journal import list_action_logs
from tests.test_db_ai import _call, _pending, _secret, _seed, _start, _text, _turn  # noqa: F401 — _secret: автофикстура
from tests.test_db_ai_build_memory import _build, _reply

TEMPLATE = "https://shop.ru/?ref=Yandex&utm_source=yandex&utm_campaign={campaign_id}"
USE = "ref=Telegram — как в ссылке"
KEEP = "ref=Yandex — как в шаблоне проекта"


def _links_seed(db):
    """Проект с шаблоном ссылки; в «Поиске» и «РСЯ» — по группе с объявлениями."""
    s = _seed(db)
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET url_template = %s WHERE id = %s", (TEMPLATE, s["ws"]))
    for key, direct_id, group_id in (("search", 11, 70), ("network", 12, 80)):
        upsert_adgroups(s["user"], s["acc"], s[key], [{"adgroup_id": group_id, "campaign_id": direct_id, "name": "Окна"}])
        with db.connection() as conn:
            group = conn.execute("SELECT id FROM direct_adgroups WHERE adgroup_id = %s", (group_id,)).fetchone()["id"]
        items = [
            {"ad_id": group_id * 10 + n, "adgroup_id": group_id, "type": "TEXT_AD", "state": "ON",
             "title": f"Объявление {n}", "text": "Текст", "href": "https://shop.ru/?ref=Yandex"}
            for n in (1, 2)
        ]
        upsert_ads(s["user"], s["acc"], {group_id: group}, items)
        with db.connection() as conn:
            rows = conn.execute("SELECT id FROM direct_ads WHERE adgroup_row_id = %s ORDER BY ad_id", (group,)).fetchall()
        s[f"{key}_group"], s[f"{key}_ads"] = group, [r["id"] for r in rows]
    return s


def _choice(db, campaign_row_id):
    with db.connection() as conn:
        return conn.execute("SELECT link_params FROM direct_campaigns WHERE id = %s", (campaign_row_id,)).fetchone()["link_params"]


def _new_ad(s, href, title="Окна от завода"):
    return {"adgroup_row_id": s["search_group"], "title": title, "text": "Замер в день заказа", "href": href}


def _open(user, chat, tool):
    return [a for a in ai_agent.get_chat(user, chat)["actions"] if a["status"] == "pending" and a["tool"] == tool]


def test_own_ref_is_asked_once_and_kept_for_the_campaign(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    href = "https://shop.ru/tarify?ref=Telegram&utm_source=tg"
    _fake, out = _start(user, chat, f"Сделай объявление со ссылкой {href}", monkeypatch, _call("create_text_ad", _new_ad(s, href)))

    # Вместо действия — вопрос: какой ref ставить. Само объявление ещё не предложено.
    assert out["status"] == "waiting" and not _open(user, chat, "create_text_ad")
    card = _pending(user, chat, "ask_questions")
    assert card["label"] == "Выбор ref в ссылке" and card["kind"] == _pending(user, chat, "ask_questions")["kind"]
    [question] = card["args"]["questions"]
    assert question["question"] == "В ссылке ref=Telegram, а в шаблоне проекта — ref=Yandex. Какой ставить в ссылках кампании «Поиск»?"
    assert question["options"] == [USE, KEEP] and question["multiple"] is False
    assert _choice(db, s["search"]) == {}

    answered = ai_agent.answer_questions(user, card["id"], answers=[{"n": 1, "selected": [USE]}])
    assert answered["action"]["status"] == "done" and answered["pending"] == 0
    assert _choice(db, s["search"]) == {"own": {"ref": "Telegram"}, "asked": {"ref": ["Telegram", "Yandex"]}}

    # Модель получает ответ результатом того же вызова и повторяет его — теперь это карточка объявления.
    fake, out = _turn(user, chat, monkeypatch, _call("create_text_ad", _new_ad(s, href), "c2"))
    reply = fake.calls[0][-1]
    assert reply["role"] == "tool" and reply["tool_call_id"] == "c1"
    told = json.loads(reply["content"])
    assert told["ok"] is True and told["executed"] is False and "повтори тот же вызов" in told["note"]
    assert told["decided"] == ["ref=Telegram — закреплён за кампанией, ставь его во все её ссылки"]
    proposal = _pending(user, chat, "create_text_ad")
    # В карточке — адрес, который уйдёт в Директ: ref маркетолога, метки — в параметрах кампании.
    assert out["status"] == "waiting" and proposal["args"]["href"] == "https://shop.ru/tarify?ref=Telegram"
    assert {"what": "Ссылка", "before": "—", "after": "https://shop.ru/tarify?ref=Telegram"} in [
        {k: c[k] for k in ("what", "before", "after")} for c in proposal["changes"]
    ]

    sent = {}
    monkeypatch.setattr(ads, "create_text_ad", lambda user_id, **kw: sent.update(kw) or {"ad": {"id": 1}})
    ai_agent.decide_action(user, proposal["id"], approve=True)
    _turn(user, chat, monkeypatch, _text("Создал."))
    assert sent["href"] == "https://shop.ru/tarify?ref=Telegram"

    # Тот же ref и шаблонный — больше не вопрос: кампания живёт со своим. Новый ref — спрашиваем снова.
    again = _call("update_text_ad", {"ad_row_id": s["search_ads"][0], "href": "https://shop.ru/?ref=Yandex"}, "c3")
    _start(user, chat, "Поправь ссылку первого объявления", monkeypatch, again)
    assert _pending(user, chat, "update_text_ad")["args"]["href"] == "https://shop.ru/?ref=Telegram"
    assert not _open(user, chat, "ask_questions")
    ai_agent.decide_action(user, _pending(user, chat, "update_text_ad")["id"], approve=False)
    other = _call("update_text_ad", {"ad_row_id": s["search_ads"][0], "href": "https://shop.ru/?ref=Vk"}, "c4")
    _turn(user, chat, monkeypatch, other)
    [question] = _pending(user, chat, "ask_questions")["args"]["questions"]
    assert question["question"] == "В ссылке ref=Vk, а у кампании сейчас — ref=Telegram. Какой ставить в ссылках кампании «Поиск»?"
    assert question["options"] == ["ref=Vk — как в ссылке", "ref=Telegram — как сейчас у кампании"]

    # Проверка ссылок считает правильным ref кампании: шаблонный Yandex в её объявлениях — теперь расхождение.
    [checked] = [c for c in links.workspace_link_issues(user, s["ws"])["campaigns"] if c["campaign_row_id"] == s["search"]]
    assert checked["own_href"] == "https://shop.ru/?ref=Telegram"
    assert {a["fixed_href"] for a in checked["ads"]} == {"https://shop.ru/?ref=Telegram"}
    assert all("ref=Yandex вместо Telegram" in a["problems"] for a in checked["ads"])


def test_keeping_template_ref_and_skipping_do_not_loop(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    edit = {"ad_row_id": s["search_ads"][0], "href": "https://shop.ru/?ref=Telegram"}
    _start(user, chat, "Поправь ссылку", monkeypatch, _call("update_text_ad", edit))
    card = _pending(user, chat, "ask_questions")
    ai_agent.answer_questions(user, card["id"], answers=[{"n": 1, "selected": [KEEP]}])
    # Шаблонный ref остаётся, а о Telegram больше не спрашиваем: повтор сразу даёт карточку правки.
    assert _choice(db, s["search"]) == {"own": {}, "asked": {"ref": ["Telegram"]}}
    fake, _ = _turn(user, chat, monkeypatch, _call("update_text_ad", edit, "c2"))
    assert json.loads(fake.calls[0][-1]["content"])["decided"] == ["ref=Yandex остаётся; ref=Telegram из ссылки в эту кампанию не ставим"]
    proposal = _pending(user, chat, "update_text_ad")
    assert proposal["args"]["href"] == "https://shop.ru/?ref=Yandex" and not _open(user, chat, "ask_questions")
    ai_agent.decide_action(user, proposal["id"], approve=False)

    # Несколько объявлений разных кампаний одной правкой: вопрос только о кампании, где ref ещё не обсуждали.
    bulk = {"items": [
        {"ad_row_id": s["search_ads"][1], "href": "https://shop.ru/a?ref=Telegram"},
        {"ad_row_id": s["network_ads"][0], "href": "https://shop.ru/b?ref=Telegram"},
        {"ad_row_id": s["network_ads"][1], "href": "https://shop.ru/c?ref=Telegram"},
    ]}
    _turn(user, chat, monkeypatch, _call("update_text_ads", bulk, "c3"))
    card = _pending(user, chat, "ask_questions")
    assert [q["question"] for q in card["args"]["questions"]] == [
        "В ссылке ref=Telegram, а в шаблоне проекта — ref=Yandex. Какой ставить в ссылках кампании «РСЯ»?"
    ]
    # «Пропустить» — ref остаётся прежним и вопрос не повторяется.
    skipped = ai_agent.answer_questions(user, card["id"], skip=True)
    assert skipped["action"]["status"] == "rejected" and skipped["action"]["error"] == "Пропущено"
    assert _choice(db, s["network"]) == {"own": {}, "asked": {"ref": ["Telegram"]}}
    fake, _ = _turn(user, chat, monkeypatch, _call("update_text_ads", bulk, "c4"))
    told = json.loads(fake.calls[0][-1]["content"])
    assert told["skipped"] is True and told["executed"] is False
    assert [i["href"] for i in _pending(user, chat, "update_text_ads")["args"]["items"]] == [
        "https://shop.ru/a?ref=Yandex", "https://shop.ru/b?ref=Yandex", "https://shop.ru/c?ref=Yandex",
    ]


def test_ref_answered_by_text_or_message(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    href = "https://shop.ru/?ref=Telegram"
    _start(user, chat, "Сделай объявление", monkeypatch, _call("create_text_ad", _new_ad(s, href)))
    first = _pending(user, chat, "ask_questions")

    # Сообщение вместо выбора: действие не выполнено и выбор не записан — карточка появится снова.
    fake, _ = _start(user, chat, "А что такое ref?", monkeypatch, _call("create_text_ad", _new_ad(s, href), "c2"))
    told = _reply(fake, 0) if fake.calls[0][-1]["role"] == "tool" else json.loads(fake.calls[0][-2]["content"])
    assert told["ok"] is False and "не выбрал ref" in told["error"]
    assert _choice(db, s["search"]) == {}
    second = _pending(user, chat, "ask_questions")
    assert second["id"] != first["id"]

    # Ответ своими словами в поле карточки понимаем, если назван один из двух ref.
    ai_agent.answer_questions(user, second["id"], answers=[{"n": 1, "text": "ставь телеграмовский, Telegram"}])
    assert _choice(db, s["search"])["own"] == {"ref": "Telegram"}

    # Непонятный ответ ничего не закрепляет — модель читает его сама.
    other = _call("create_text_ad", _new_ad(s, "https://shop.ru/?ref=Vk"), "c3")
    _turn(user, chat, monkeypatch, other)
    third = _pending(user, chat, "ask_questions")
    ai_agent.answer_questions(user, third["id"], answers=[{"n": 1, "text": "а какая разница?"}])
    assert _choice(db, s["search"]) == {"own": {"ref": "Telegram"}, "asked": {"ref": ["Telegram", "Yandex"]}}
    fake, _ = _turn(user, chat, monkeypatch, _text("Разница в том, откуда придёт клиент."))
    assert "маркетолог не выбрал вариант" in json.loads(fake.calls[0][-1]["content"])["decided"][0]


def test_foreign_site_is_refused_not_replaced(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    sitelinks = [{"title": "Мы в Telegram", "href": "https://t.me/shop"}, {"title": "Цены", "href": "https://shop.ru/ceny?ref=Yandex"}]
    fake, out = _start(
        user, chat, "Сделай объявление на t.me", monkeypatch,
        _call("create_text_ad", _new_ad(s, "https://t.me/shop?ref=Telegram")),
        _call("update_text_ad", {"ad_row_id": s["search_ads"][0], "href": "https://other.ru/"}, "c2"),
        # Быстрая ссылка на другой сайт — можно: меняем только ссылки на сайт проекта.
        _call("create_text_ad", {**_new_ad(s, "https://shop.ru/"), "extras": {"sitelinks": sitelinks}}, "c3"),
    )
    assert "ведёт не на сайт проекта (shop.ru)" in _reply(fake, 1)["error"]
    assert "ведёт не на сайт проекта (shop.ru)" in _reply(fake, 2)["error"]
    proposal = _pending(user, chat, "create_text_ad")
    assert out["status"] == "waiting" and proposal["args"]["href"] == "https://shop.ru/?ref=Yandex"
    assert [link["href"] for link in proposal["args"]["extras"]["sitelinks"]] == ["https://t.me/shop", "https://shop.ru/ceny?ref=Yandex"]


def test_copy_keeps_source_links(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    args = {"campaign_row_id": s["search"], "variants": [{"name": "Б", "href": "https://shop.ru/?ref=Telegram"}]}
    fake, _ = _start(user, chat, "Сделай копию с ref=Telegram", monkeypatch, _call("create_campaign_variants", args), _text("Ок."))
    error = _reply(fake, 1)["error"]
    assert "В ссылке ref=Telegram, а у исходной кампании ref=Yandex" in error and "set_campaign_ref" in error
    assert not _open(user, chat, "ask_questions") and not _open(user, chat, "create_campaign_variants")


def test_new_campaign_gets_the_chosen_ref(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    _build(db, s, monkeypatch, upto="settings")
    spec = {"account_id": s["acc"], "spec": {"name": "Окна", "negative_keywords": ["бесплатно"], "link_params": {"own": {"ref": "Hack"}}, "groups": [
        {"name": "Окна ПВХ", "keywords": ["купить окна пвх", "окна в рассрочку"],
         "ad": {"titles": ["Окна от завода"], "texts": ["Замер в день заказа"], "href": "https://shop.ru/okna?ref=Telegram"}},
    ]}}
    _turn(user, chat, monkeypatch, _call("create_campaign", spec, "create"))
    card = _pending(user, chat, "ask_questions")
    [question] = card["args"]["questions"]
    assert question["question"] == "В ссылке ref=Telegram, а в шаблоне проекта — ref=Yandex. Какой ставить в ссылках новой кампании?"
    assert not _open(user, chat, "create_campaign")
    answered = ai_agent.answer_questions(user, card["id"], answers=[{"n": 1, "selected": [USE]}])
    assert answered["action"]["result"]["link_choice"] == {"own": {"ref": "Telegram"}, "asked": {"ref": ["Telegram", "Yandex"]}}

    _turn(user, chat, monkeypatch, _call("create_campaign", spec, "create2"))
    proposal = _pending(user, chat, "create_campaign")
    stored = proposal["args"]["spec"]
    # Выбор ставит сервис: то, что модель передала в link_params сама, отброшено.
    assert stored["link_params"] == {"own": {"ref": "Telegram"}, "asked": {"ref": ["Telegram", "Yandex"]}}
    assert stored["groups"][0]["ad"]["href"] == "https://shop.ru/okna?ref=Telegram"
    rows = {c["what"]: c["after"] for c in proposal["changes"]}
    assert rows["Группа «Окна ПВХ»"].endswith("https://shop.ru/okna?ref=Telegram")

    # Объявление без ссылки получит ссылку кампании — в карточке она показана целиком, с выбранным ref.
    ai_agent.decide_action(user, proposal["id"], approve=False)
    del spec["spec"]["groups"][0]["ad"]["href"]
    _turn(user, chat, monkeypatch, _call("create_campaign", spec, "create3"))
    proposal = _pending(user, chat, "create_campaign")
    assert proposal["args"]["spec"]["link_params"]["own"] == {"ref": "Telegram"}
    rows = {c["what"]: c["after"] for c in proposal["changes"]}
    assert rows["Группа «Окна ПВХ»"].endswith("https://shop.ru/?ref=Telegram")

    seen = {}
    row = _pending(user, chat, "create_campaign")
    with db.connection() as conn:
        draft = conn.execute("SELECT id FROM direct_campaigns WHERE campaign_id = 715000001").fetchone()["id"]
    monkeypatch.setattr(actions, "create_campaign", lambda user_id, **kw: seen.update(kw) or {"campaign": {"id": draft}, "status": "ok"})
    ai_agent.decide_action(user, row["id"], approve=True)
    _turn(user, chat, monkeypatch, _text("Черновик создан."))
    assert seen["spec"]["link_params"]["own"] == {"ref": "Telegram"}


def test_set_campaign_ref_replaces_ref_in_one_action(db, monkeypatch):
    s = _links_seed(db)
    user, chat = s["user"], s["chat"]
    fake, out = _start(
        user, chat, "Замени ref на Telegram в Поиске", monkeypatch,
        _call("set_campaign_ref", {"campaign_row_ids": [s["search"]], "value": "Yandex"}),
        _call("set_campaign_ref", {"campaign_row_ids": [s["search"]], "value": "a b"}, "c2"),
        _call("set_campaign_ref", {"campaign_row_ids": [s["search"]], "value": "Telegram", "reason": "просьба"}, "c3"),
    )
    assert "уже стоит во всех ссылках" in _reply(fake, 1)["error"]
    assert "без пробелов" in _reply(fake, 2)["error"]
    proposal = _pending(user, chat, "set_campaign_ref")
    assert out["status"] == "waiting" and proposal["summary"] == "ref=Telegram в ссылках: «Поиск»; 2 объявления уйдут на модерацию"
    rows = [(c["what"], c["before"], c["after"]) for c in proposal["changes"]]
    assert rows[0] == ("«Поиск»: ref в ссылках", "Yandex", "Telegram")
    assert "адрес у 2 объявлений" in rows[1][2] and "тексты не меняются" in rows[1][2]

    fixed = []

    def fix(user_id, campaign_row_id, *, journal=True):
        # К этому моменту ref уже закреплён за кампанией: исправление идёт по её шаблону.
        fixed.append((campaign_row_id, links.template_href(links.campaign_template(user_id, campaign_row_id)), journal))
        steps = [{"op": "ad_update", "args": {"ad_row_id": s["search_ads"][0], "href": "https://shop.ru/?ref=Yandex"}}]
        return {"campaign_row_id": campaign_row_id, "name": "Поиск", "tracking_fixed": False, "groups_fixed": 0,
                "ads_fixed": 2, "sitelinks_fixed": 0, "errors": [], "status": "ok", "undo_steps": steps}

    monkeypatch.setattr(links, "fix_campaign_links", fix)
    ai_agent.decide_action(user, proposal["id"], approve=True)
    fake, _ = _turn(user, chat, monkeypatch, _text("Заменил."))
    done = json.loads(fake.calls[0][-1]["content"])
    assert done["ok"] is True and done["result"]["ads_fixed"] == 2 and done["result"]["value"] == "Telegram"
    assert fixed == [(s["search"], "https://shop.ru/?ref=Telegram", False)]
    assert _choice(db, s["search"]) == {"own": {"ref": "Telegram"}, "asked": {"ref": ["Telegram", "Yandex"]}}

    # В журнале — одна запись с откатом: прежний ref кампании и прежние адреса.
    [entry] = [e for e in list_action_logs(user) if e["action"] == "links"]
    assert entry["title"] == "Ссылки «Поиск»: ref=Telegram" and entry["undo"]["label"] == "Вернуть прежние ссылки"
    preview = undo.undo_preview(user, entry["id"])
    assert preview["available"] and any("ref" in str(step) for step in preview["steps"])
    assert undo._campaign_link_params(user, {"campaign_row_id": s["search"], "own": {"ref": None}}) == {"own": {}}
    assert _choice(db, s["search"])["own"] == {}

    # Директ не принял ни одной правки — ref за кампанией не закрепляется.
    def broken(user_id, campaign_row_id, *, journal=True):
        return {"errors": ["Объявление 701: отклонено"], "status": "error", "ads_fixed": 0, "sitelinks_fixed": 0, "undo_steps": []}

    monkeypatch.setattr(links, "fix_campaign_links", broken)
    with pytest.raises(Exception) as err:
        links.set_campaign_params(user, s["search"], {"ref": "Vk"})
    assert "отклонено" in str(getattr(err.value, "message", err.value))
    assert _choice(db, s["search"])["own"] == {}
