"""Черновик с первого шага (docs/board-plan.md, пункт 2): после «Брифа» сервис создаёт черновик в Директе и
дописывает в него каждый согласованный шаг; доска показывает, что из этого уже в Директе."""

import itertools

import pytest

from app import actions, adgroups, ads, ai_agent, keywords
from app.yandex import YandexError
from tests.dbutil import make_campaign
from tests.test_db_ai import _call, _pending, _secret, _seed, _start, _text, _turn  # noqa: F401 — _secret: автофикстура
from tests.test_db_ai_build_memory import ADS, GOALS, KEYWORDS, _approve, _reply

CAMPAIGN = {
    "name": "Окна — поиск",
    "type": "TEXT_CAMPAIGN",
    "region_ids": [213],
    "strategy": {"search": "WB_MAXIMUM_CLICKS", "network": "SERVING_OFF", "weekly_budget_rub": 3500},
    "href": "https://shop.ru/okna",
}


def _wizard(stage, rows, **extra):
    if stage in ("goals", "analysis"):
        extra.setdefault("requirements", {})
    return _call("propose_items", {"stage": stage, "rows": rows, "by_stages": True, **extra}, f"t-{stage}")


class FakeDirect:
    """Директ на время теста: записывает вызовы и ведёт локальную копию, как настоящие функции."""

    def __init__(self, db, s, monkeypatch):
        self.db, self.s, self.calls = db, s, []
        self.ids = itertools.count(900001)
        self.campaign = None
        self.fail_ads = False
        monkeypatch.setattr(actions, "create_campaign", self.create_campaign)
        monkeypatch.setattr(actions, "update_campaign_settings", self.update_settings)
        monkeypatch.setattr(actions, "moderate_campaign", self.moderate)
        monkeypatch.setattr(adgroups, "create_adgroup", self.create_adgroup)
        monkeypatch.setattr(adgroups, "delete_adgroups", self.delete_adgroups)
        monkeypatch.setattr(adgroups, "set_regions", lambda *a, **kw: self.calls.append(("regions", a[1])) or {})
        monkeypatch.setattr(keywords, "create_keywords", self.create_keywords)
        monkeypatch.setattr(keywords, "bulk_keyword_action", self.keyword_action)
        monkeypatch.setattr(ads, "create_text_ad", self.create_ad)
        monkeypatch.setattr(ads, "update_text_ad", lambda u, ad, **kw: self.calls.append(("update_ad", ad, kw)) or {})
        monkeypatch.setattr(ads, "set_ads_extras", lambda u, ids, extras: self.calls.append(("extras", ids, extras)) or {})

    def tools(self):
        return [c[0] for c in self.calls]

    def create_campaign(self, user_id, *, account_id, spec, workspace_id=None):
        self.calls.append(("create_campaign", spec))
        self.campaign = make_campaign(self.db, user_id, account_id, next(self.ids), spec["name"], state="OFF", status="DRAFT")
        return {"campaign": {"id": self.campaign}, "groups": [], "warnings": [], "status": "ok"}

    def update_settings(self, user_id, row_id, changes):
        self.calls.append(("settings", changes))
        if "negative_keywords" in changes:
            with self.db.connection() as conn:
                conn.execute(
                    "UPDATE direct_campaigns SET negative_keywords = %s WHERE id = %s",
                    (__import__("json").dumps(changes["negative_keywords"], ensure_ascii=False), row_id),
                )
        return {"status": "ok"}

    def moderate(self, user_id, row_id):
        self.calls.append(("moderate", row_id))
        return {"drafts": 1, "sent": 1, "errors": [], "status": "ok"}

    def create_adgroup(self, user_id, *, campaign_row_id, name, region_ids=None):
        self.calls.append(("adgroup", name, region_ids))
        direct_id = next(self.ids)
        with self.db.connection() as conn:
            row = conn.execute(
                "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name, region_ids) "
                "VALUES (%s, %s, %s, %s, %s, '[213]') RETURNING id",
                (user_id, self.s["acc"], campaign_row_id, direct_id, name),
            ).fetchone()
        return {"id": row["id"], "adgroup_id": direct_id, "name": name}

    def delete_adgroups(self, user_id, ids):
        self.calls.append(("delete_adgroups", ids))
        with self.db.connection() as conn:
            conn.execute("DELETE FROM direct_adgroups WHERE id = ANY(%s)", (ids,))
        return {"deleted": len(ids)}

    def create_keywords(self, user_id, *, adgroup_row_id, phrases, bid_rub=None):
        self.calls.append(("keywords", adgroup_row_id, list(phrases)))
        items = []
        with self.db.connection() as conn:
            for phrase in phrases:
                conn.execute(
                    "INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword, state) "
                    "VALUES (%s, %s, %s, %s, %s, 'ON')",
                    (user_id, self.s["acc"], adgroup_row_id, next(self.ids), phrase),
                )
                items.append({"keyword": phrase})
        return {"items": items, "created": len(items), "errors": 0, "message": None, "status": "ok"}

    def keyword_action(self, user_id, *, keyword_row_ids, action, bid_rub=None):
        self.calls.append(("keywords_" + action, list(keyword_row_ids)))
        with self.db.connection() as conn:
            conn.execute("DELETE FROM direct_keywords WHERE id = ANY(%s)", (keyword_row_ids,))
        return {"applied": len(keyword_row_ids), "status": "ok"}

    def create_ad(self, user_id, *, adgroup_row_id, href, titles=None, texts=None, **kw):
        if self.fail_ads:
            raise YandexError("direct", "Директ не принял объявление")
        self.calls.append(("ad", adgroup_row_id, href, titles, texts))
        return {"id": next(self.ids)}


def test_brief_needs_campaign_for_the_draft(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    fake, out = _start(
        user, chat, "Сделай кампанию на окна", monkeypatch,
        _wizard("goals", GOALS),
        _wizard("goals", GOALS, campaign={**CAMPAIGN, "href": ""}),
    )
    assert "campaign" in _reply(fake, 1)["error"]
    # Бриф без ссылки ИИ присылает как есть (пункт 6 — бриф формой): ссылку допишет маркетолог,
    # а без неё и без шаблона ссылки у проекта «Дальше» не пройдёт — черновику некуда вести объявления.
    assert out["status"] == "waiting"
    table = _pending(user, chat, "propose_items")
    # Кабинет один — подставлен сам.
    assert table["args"]["campaign"]["account_id"] == s["acc"] and table["args"]["campaign"]["region_ids"] == [213]
    assert ai_agent.get_chat(user, chat)["build"]["brief"]["missing"] == ["посадочная ссылка"]
    with pytest.raises(YandexError) as err:
        ai_agent.decide_items(user, table["id"], rows=[r["n"] for r in table["args"]["rows"]])
    assert "посадочная ссылка" in err.value.message


def test_each_step_is_written_to_the_draft(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    direct = FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", GOALS, campaign=CAMPAIGN))
    _approve(user, chat)
    assert direct.calls == []  # до хода ИИ ничего не пишется — «Дальше» только согласует

    # Ход после «Дальше» по брифу: сначала черновик, потом модель готовит следующий шаг.
    fake, _ = _turn(user, chat, monkeypatch, _wizard("analysis", [{"cells": ["Окна ПВХ", "12 000"]}]))
    assert direct.tools() == ["create_campaign"]
    spec = direct.calls[0][1]
    assert spec["name"] == "Окна — поиск" and spec["groups"] == [] and spec["strategy"]["weekly_budget_rub"] == 3500
    assert "Черновик уже в Директе" in fake.notes[0] and "create_campaign не вызывай" in fake.notes[0]
    build = ai_agent.get_chat(user, chat)["build"]
    assert build["direct"]["campaign_row_id"] == direct.campaign and "direct_state" not in build
    _approve(user, chat)

    _turn(user, chat, monkeypatch, _wizard("keywords", KEYWORDS))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _wizard("ads", ADS))
    # Фразы, группа и минус-фразы — в черновике; объявлений ещё нет.
    assert direct.tools() == ["create_campaign", "adgroup", "keywords", "settings"]
    assert direct.calls[1] == ("adgroup", "Окна ПВХ", [213])
    assert direct.calls[2][2] == ["купить окна пвх", "окна в рассрочку"]
    assert direct.calls[3][1] == {"negative_keywords": ["бесплатно"]}

    # Доска глазами Директа: ✓ у того, что записано; фраза, добавленная в Директе руками, — «только в Директе».
    with db.connection() as conn:
        group = conn.execute("SELECT id FROM direct_adgroups WHERE campaign_row_id = %s", (direct.campaign,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword) VALUES (%s, %s, %s, 1, %s)",
            (user, s["acc"], group, "окна с установкой"),
        )
    build = ai_agent.get_chat(user, chat)["build"]
    [board_group] = build["groups"]
    marks = {k["text"]: (k.get("in_direct"), k.get("direct_only", False)) for k in board_group["keywords"]}
    assert marks == {
        "купить окна пвх": (True, False), "окна в рассрочку": (True, False), "окна с установкой": (True, True),
    }
    assert [n.get("in_direct") for n in build["negatives"]] == [True]
    # Заголовки ждут «Дальше» — их в Директе нет.
    assert all(not t.get("in_direct") for t in board_group["titles"])

    _approve(user, chat)
    _turn(user, chat, monkeypatch, _text("Объявления записаны. Дальше — картинки."))
    assert direct.calls[-1] == ("ad", group, "https://shop.ru/okna", ["Окна от завода"], ["Замер в день заказа"])

    # Повторный ход без новых согласований ничего не пишет.
    before = len(direct.calls)
    _turn(user, chat, monkeypatch, _text("Жду."))
    assert len(direct.calls) == before


def test_removed_phrase_and_group_leave_the_draft(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    direct = FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", GOALS, campaign=CAMPAIGN))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _wizard("keywords", KEYWORDS + [{"section": "Двери", "cells": ["купить двери", "300"]}]))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _text("Готово."))
    assert direct.tools().count("adgroup") == 2

    # На доске сняли фразу и убрали группу «Двери» — следующая запись убирает их из черновика.
    build = ai_agent.get_chat(user, chat)["build"]
    windows = next(g for g in build["groups"] if g["name"] == "Окна ПВХ")
    doors = next(g for g in build["groups"] if g["name"] == "Двери")
    drop = next(k for k in windows["keywords"] if k["text"] == "окна в рассрочку")
    ai_agent.edit_board(user, chat, [
        {"op": "remove", "kind": "keyword", "group": "Окна ПВХ", "text": drop["text"]},
        {"op": "remove", "kind": "keyword", "group": "Двери", "text": doors["keywords"][0]["text"]},
    ])
    # Правки в записанном шаге копятся: ход ИИ их не пишет, на доске — «Применить в Директ (N)».
    before = len(direct.calls)
    _turn(user, chat, monkeypatch, _text("Учёл."))
    assert len(direct.calls) == before
    queue = ai_agent.get_chat(user, chat)["build"]["queue"]
    assert {(c["kind"], c["op"], c["group"]) for c in queue} >= {("keyword", "remove", "Окна ПВХ")}

    out = ai_agent.retry_build_write(user, chat)
    assert out["build"]["queue"] == []
    assert "delete_adgroups" in direct.tools() and "keywords_delete" in direct.tools()
    with db.connection() as conn:
        left = [r["keyword"] for r in conn.execute("SELECT keyword FROM direct_keywords ORDER BY id").fetchall()]
    assert left == ["купить окна пвх"]
    # Применённое следующий ход не трогает.
    before = len(direct.calls)
    _turn(user, chat, monkeypatch, _text("Дальше."))
    assert len(direct.calls) == before


def test_write_error_is_on_the_board_and_retry_works(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    direct = FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", GOALS, campaign=CAMPAIGN))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _wizard("keywords", KEYWORDS))
    _approve(user, chat)
    _turn(user, chat, monkeypatch, _wizard("ads", ADS))
    _approve(user, chat)
    direct.fail_ads = True
    fake, _ = _turn(user, chat, monkeypatch, _text("Готово."))
    info = ai_agent.get_chat(user, chat)["build"]["direct"]
    assert any("объявление не записано" in w for w in info["warnings"])
    assert "Директ не принял при записи" in fake.notes[0]

    direct.fail_ads = False
    info = ai_agent.retry_build_write(user, chat)["build"]["direct"]
    assert info["warnings"] == [] and info["written"]["ads"] == 1


def test_moderation_only_from_the_board_when_ready(db, monkeypatch):
    s = _seed(db)
    user, chat = s["user"], s["chat"]
    direct = FakeDirect(db, s, monkeypatch)
    _start(user, chat, "Сделай кампанию на окна", monkeypatch, _wizard("goals", GOALS, campaign=CAMPAIGN))
    _approve(user, chat)
    # Модель черновик сама не создаёт: в сборке по шагам create_campaign отклоняется.
    fake, _ = _turn(
        user, chat, monkeypatch,
        _call("create_campaign", {"account_id": s["acc"], "spec": {"name": "Окна", "groups": []}}, "cc"),
        _text("Понял."),
    )
    assert "create_campaign не нужен" in _reply(fake, 1)["error"]
    with pytest.raises(YandexError) as err:
        ai_agent.moderate_build(user, chat)
    assert "Сначала пройдите шаги" in err.value.message
    assert "moderate" not in direct.tools()

    for stage, rows in (
        ("analysis", [{"cells": ["Окна ПВХ", "12 000"]}]), ("keywords", KEYWORDS), ("ads", ADS),
        ("settings", [{"kind": "setting", "cells": ["Бюджет", "600 ₽/день"]}]),
    ):
        extra = {"campaign": {"daily_budget_rub": 600}} if stage == "settings" else {}
        if stage == "analysis":
            _start(user, chat, "Дальше", monkeypatch, _wizard(stage, rows, **extra))
        else:
            _turn(user, chat, monkeypatch, _wizard(stage, rows, **extra))
        _approve(user, chat)
    out = ai_agent.moderate_build(user, chat)
    # «Настройки» уточнили бюджет — он записан перед модерацией.
    assert ("settings", {"daily_budget_rub": 600}) in direct.calls
    assert direct.calls[-1] == ("moderate", direct.campaign)
    assert out["build"]["direct"]["moderated"] is True
    with pytest.raises(YandexError):
        ai_agent.moderate_build(user, chat)


def test_campaign_page_board_reads_direct(db, monkeypatch):
    s = _seed(db)
    user = s["user"]
    with db.connection() as conn:
        group = conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name) "
            "VALUES (%s, %s, %s, 5, 'Окна') RETURNING id",
            (user, s["acc"], s["search"]),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword) VALUES (%s, %s, %s, 7, 'окна')",
            (user, s["acc"], group),
        )
    board = ai_agent.campaign_board(user, s["search"])
    assert board["campaign"]["id"] == s["search"]
    assert [(g["name"], [k["text"] for k in g["keywords"]]) for g in board["groups"]] == [("Окна", ["окна"])]
    assert board["groups"][0]["keywords"][0]["in_direct"] is True
    with pytest.raises(YandexError):
        ai_agent.campaign_board(user + 999, s["search"])
