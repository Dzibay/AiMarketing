"""Аудитории групп: ретаргетинг, условия кабинета (LAL), интересы, новый LAL из Яндекс Аудиторий — модуль и путь
через чат и доску, без БД и сети."""

from types import SimpleNamespace

import pytest

from app import ai_agent, audiences, geo, yandex
from app.workspaces import normalize_ai_access
from app.yandex import YandexError

ACCOUNT = {"id": 3, "client_login": "acc-a", "name": "Кабинет"}
GOALS = [{"goal_id": 11, "name": "Заявка"}, {"goal_id": 12, "name": "Звонок"}]
LAL = 1_600_000_001
LISTS = [
    {"id": 501, "name": "Похожие на покупателей", "type": "RETARGETING", "scope": "FOR_TARGETS_AND_ADJUSTMENTS",
     "available": True, "lal": True, "rules": [{"operator": "ANY", "ids": [LAL], "days": None}]},
    {"id": 502, "name": "Только для ставок", "type": "RETARGETING", "scope": "FOR_ADJUSTMENTS_ONLY",
     "available": True, "lal": False, "rules": [{"operator": "ANY", "ids": [11], "days": 30}]},
    {"id": 503, "name": "Заявки за месяц", "type": "RETARGETING", "scope": "FOR_TARGETS_AND_ADJUSTMENTS",
     "available": True, "lal": False, "rules": [{"operator": "ANY", "ids": [11], "days": 30}]},
]
CATALOG = {
    7: {"id": 7, "name": "Путешествия", "path": "", "description": "Туризм", "parent": None},
    8: {"id": 8, "name": "Пляжный отдых", "path": "Путешествия", "description": "Море", "parent": 7},
    9: {"id": 9, "name": "Ремонт", "path": "", "description": "", "parent": None},
}


class FakeContext:
    require_account = ai_agent.Context.require_account

    def __init__(self, ws: dict | None = None, chat_id: int | None = 5):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "goals": GOALS, "accounts": [ACCOUNT], **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))
        self.chat_id = chat_id


# Права логина на Аудитории: приложение Аудиторий задано, токен у подключения 7 есть.
RIGHTS = {"login": "acc-a", "connection_id": 7, "enabled": True, "rights": True}


@pytest.fixture
def account(monkeypatch):
    """Кабинет без сети: условия и справочник интересов из констант, Аудитории — в памяти
    (segments — сегменты логина, audience_calls — запросы к API Аудиторий, audience_tokens — их токены;
    enabled — задано приложение Аудиторий, rights — логину Аудитории подключены)."""
    state = SimpleNamespace(
        lists=LISTS, catalog=CATALOG, lists_error=None, enabled=True, token_error=None,
        rights=True, segments=[], segments_error=None, lal_error=None, owner="acc-a", audience_calls=[], audience_tokens=[],
    )

    def lists(token, login):
        if state.lists_error:
            raise YandexError("api", state.lists_error)
        return state.lists

    def audience_call(token, method, path, *, body=None, params=None):
        state.audience_calls.append((method, path, body))
        state.audience_tokens.append(token)
        if path == "segments":
            if state.segments_error:
                raise state.segments_error
            return {"segments": [dict(s) for s in state.segments]}
        seg_id = 300 + len(state.audience_calls)
        if path == "segments/create_metrika":
            return {"segment": {"id": seg_id, "type": "metrika", "status": "uploaded", "owner": state.owner, **body["segment"]}}
        if path == "segments/create_lookalike":
            if state.lal_error:
                raise YandexError("audience", state.lal_error)
            return {"segment": {"id": seg_id, "type": "lookalike", "status": "is_processed", "owner": state.owner, **body["segment"]}}
        if path.endswith("/grant"):
            return {"grant": body["grant"]}
        raise AssertionError((method, path))

    monkeypatch.setattr(audiences, "account_access", lambda user_id, account_id: ("token", "acc-a"))
    monkeypatch.setattr(audiences, "account_lists", lists)
    monkeypatch.setattr(audiences, "interest_catalog", lambda token: state.catalog)
    def rights(user_id, account_id):
        return {"login": "acc-a", "connection_id": 7, "enabled": state.enabled, "rights": state.enabled and state.rights}

    def audience_token(connection_id):
        assert connection_id == 7
        if state.token_error:
            raise state.token_error
        return "aud-token"

    monkeypatch.setattr(audiences, "enabled", lambda: state.enabled)
    monkeypatch.setattr(audiences, "account_rights", rights)
    monkeypatch.setattr(yandex, "get_audience_access_token", audience_token)
    monkeypatch.setattr(audiences, "audience_call", audience_call)
    return state


# --- Аудитория спецификации -----------------------------------------------------------------------


def test_clean_retargeting_short_form():
    aud = audiences.clean_audience(
        {"name": "  Были   на сайте ", "goal_ids": [11, "12", 11], "exclude_goal_ids": [13], "days": "60"}
    )
    assert aud == {"type": "retargeting", "name": "Были на сайте", "rules": [
        {"operator": "ANY", "goal_ids": [11, 12], "days": 60}, {"operator": "NONE", "goal_ids": [13], "days": 60},
    ]}
    assert audiences.clean_audience({"type": "retargeting", "name": "А", "goal_ids": 11})["rules"][0]["days"] == 30
    assert audiences.goal_ids(aud) == [11, 12, 13]


def test_clean_list_and_interests():
    assert audiences.clean_audience({"name": "LAL", "list_id": "501"}) == {"type": "list", "name": "LAL", "list_id": 501}
    # Полный id справочника (10 + ключ) — тот же интерес, повторы убираются.
    aud = audiences.clean_audience({"name": "Х", "interest_ids": ["100000000007", 7, 8]}, name="Путешественники")
    assert aud == {"type": "interests", "name": "Путешественники", "interest_ids": [7, 8]}


@pytest.mark.parametrize(
    "raw, text",
    [
        ("ретаргетинг", "объект"),
        ({"type": "list", "list_id": 1}, "нет названия"),
        ({"type": "lal", "name": "А"}, "retargeting, list, interests или lookalike"),
        ({"type": "lookalike", "name": "А"}, "источник LAL — одно из goal_id"),
        ({"name": "А", "source": {"goal_id": 1, "counter_id": 2}}, "источник LAL — одно из goal_id"),
        ({"name": "А", "source": {"kind": "pixel", "id": 1}}, "нужен goal, segment или counter"),
        ({"name": "А", "source": {"goal_id": "заявка"}}, "не id источника LAL"),
        ({"name": "А", "source": {"goal_id": 11}, "similarity": 6}, "похожесть LAL — от 1 до 5"),
        ({"type": "list", "name": "А"}, "нужен list_id"),
        ({"type": "interests", "name": "А", "interest_ids": []}, "хотя бы один интерес"),
        ({"type": "interests", "name": "А", "interest_ids": list(range(1, 12))}, "не больше 10"),
        ({"type": "interests", "name": "А", "interest_ids": ["путешествия"]}, "не id интереса"),
        ({"name": "А", "goal_ids": []}, "нет целей"),
        ({"name": "А", "goal_ids": ["заявка"]}, "не id"),
        ({"name": "А", "goal_ids": [1], "days": 541}, "от 1 до 540"),
        ({"name": "А", "rules": [{"operator": "NOT", "goal_ids": [1]}]}, "ANY, ALL или NONE"),
        ({"name": "А", "rules": [{"operator": "NONE", "goal_ids": [1]}]}, "одних «не выполнили» мало"),
        ({"name": "А", "rules": [{"goal_ids": [i]} for i in range(1, 7)]}, "не больше 5 правил"),
    ],
)
def test_clean_audience_errors(raw, text):
    with pytest.raises(YandexError) as err:
        audiences.clean_audience(raw)
    assert text in err.value.message


def test_signature_ignores_name_and_order():
    one = audiences.clean_audience({"name": "А", "rules": [
        {"operator": "NONE", "goal_ids": [13]}, {"operator": "ANY", "goal_ids": [12, 11]},
    ]})
    two = audiences.clean_audience({"name": "Б", "goal_ids": [11, 12], "exclude_goal_ids": [13]})
    assert audiences.audience_signature(one) == audiences.audience_signature(two)
    assert audiences.audience_signature({"type": "interests", "interest_ids": [8, 7]}) == ("interests", (7, 8))
    assert audiences.audience_signature({"type": "list", "list_id": 5}) == ("list", 5)


def test_describe_and_summary():
    goals = {11: "Заявка", 12: "Звонок"}
    one = audiences.clean_audience({"name": "А", "goal_ids": [11]})
    assert audiences.describe(one, goals=goals) == "выполнили цель «Заявка» за 30 дн."
    two = audiences.clean_audience({"name": "А", "goal_ids": [11, 12], "exclude_goal_ids": [99], "days": 7})
    assert audiences.summary(two, goals=goals) == (
        "ретаргетинг: выполнили любую из «Заявка», «Звонок» за 7 дн.; не выполнили «99» за 7 дн."
    )
    lists = {item["id"]: item for item in LISTS}
    lal = {"type": "list", "name": "LAL", "list_id": 501}
    assert audiences.summary(lal, lists=lists) == f"LAL — условие кабинета «Похожие на покупателей». {audiences.LAL_NOTE}"
    assert audiences.summary({"type": "list", "name": "Х", "list_id": 9}) == "условие кабинета №9"
    interests = {"type": "interests", "name": "И", "interest_ids": [7, 404]}
    assert audiences.summary(interests, interests={7: "Путешествия"}) == "интересы: Путешествия, 404"


def test_list_text():
    item = {"type": "RETARGETING", "rules": [
        {"operator": "ANY", "ids": [LAL, 11, 77, 1, 2, 3, 4, 5], "days": 30},
        {"operator": "NONE", "ids": [12], "days": None},
    ]}
    assert audiences.list_text(item, goals={11: "Заявка", 12: "Звонок"}, limit=3) == (
        f"любое из LAL-сегмент {LAL}, цель «Заявка», №77 и ещё 5 за 30 дн.; кроме цель «Звонок»"
    )
    interests = {"type": "AUDIENCE", "rules": [{"operator": "ALL", "ids": [100000000007], "days": None}]}
    assert audiences.list_text(interests, interests={7: "Путешествия"}) == "все из Путешествия"


# --- Интересы -------------------------------------------------------------------------------------


def test_parse_and_find_interests():
    raw = {"AudienceInterests": [
        {"Id": 1, "InterestKey": 7, "Name": "Путешествия", "InterestType": "SHORT_TERM", "Description": "Туризм"},
        {"Id": 2, "ParentId": 1, "InterestKey": 8, "Name": "Пляжный отдых", "InterestType": "SHORT_TERM"},
        {"Id": 3, "ParentId": 2, "InterestKey": 10, "Name": "Отдых на море", "InterestType": "SHORT_TERM"},
        {"Id": 4, "InterestKey": 9, "Name": "Путешествия навсегда", "InterestType": "LONG_TERM"},
    ]}
    catalog = audiences._parse_interests(raw)
    assert set(catalog) == {7, 8, 10}
    assert catalog[10]["path"] == "Путешествия › Пляжный отдых" and catalog[10]["parent"] == 8
    assert catalog[7]["parent"] is None and catalog[7]["description"] == "Туризм"
    # Сначала совпадения в названии, потом в разделе — по алфавиту.
    assert [i["id"] for i in audiences.find_interests(catalog, "путешествия")] == [7, 10, 8]
    assert [i["id"] for i in audiences.find_interests(catalog, "отдых море")] == [10]
    assert audiences.find_interests(catalog, "  ") == []
    assert audiences.interest_roots(catalog) == [{"id": 7, "name": "Путешествия", "children": 1}]


# --- Проверка и нацеливание групп -----------------------------------------------------------------


@pytest.fixture
def direct(monkeypatch, account):
    """Директ без сети: calls — запросы, add — ответы на audiencetargets.add."""
    state = SimpleNamespace(calls=[], tokens=[], targets=None, list_error=None)

    def call(token, login, service, method, params):
        state.calls.append((service, method, params))
        state.tokens.append(token)
        if service == "retargetinglists" and method == "add":
            if state.list_error:
                return {"AddResults": [{"Errors": [{"Message": state.list_error}]}]}
            return {"AddResults": [{"Id": 900 + len(state.calls)}]}
        if service == "audiencetargets" and method == "add":
            items = params["AudienceTargets"]
            return state.targets or {"AddResults": [{"Id": i} for i, _ in enumerate(items)]}
        raise AssertionError((service, method))

    monkeypatch.setattr(audiences.direct_api, "call", call)
    return state


def test_prepare_checks_network_goals_and_account(account):
    def error(groups, **kw):
        with pytest.raises(YandexError) as err:
            audiences.prepare("t", "acc-a", groups, **{"network": True, **kw})
        return err.value.message

    interests = {"type": "interests", "name": "Отдых", "interest_ids": [8]}
    assert "только в сетях" in error([{"name": "А", "keywords": ["окна"], "audiences": [interests]}], network=False)
    retarget = {"name": "Заявки", "goal_ids": [11]}
    assert "без фраз" in error([{"name": "А", "audiences": [retarget]}], network=False)
    assert "целей 11 нет" in error([{"name": "А", "keywords": ["окна"], "audiences": [retarget]}], allowed_goals={12})
    assert "повторяется" in error([{"name": "А", "audiences": [retarget, {**retarget, "name": "Б"}]}])
    assert "№404" in error([{"name": "А", "audiences": [{"name": "Х", "list_id": 404}]}])
    assert "только для корректировок" in error([{"name": "А", "audiences": [{"name": "Х", "list_id": 502}]}])
    unknown = {"type": "interests", "name": "Х", "interest_ids": [404]}
    assert "интересов 404 нет" in error([{"name": "А", "audiences": [unknown]}])
    # Без аудиторий кабинет не спрашивается.
    assert audiences.prepare("t", "acc-a", [{"name": "А"}], network=False) == {"groups": [[]], "lists": [], "made": {}}


def test_attach_reuses_same_condition_and_warns(direct):
    groups = [{"name": "А", "keywords": ["окна"], "audiences": [
        {"name": "Заявки", "goal_ids": [11]},  # такое условие в кабинете уже есть — 503
        {"name": "Отдых", "type": "interests", "interest_ids": [8]},
        {"name": "LAL", "list_id": 501},
    ]}]
    ctx = audiences.prepare("t", "acc-a", groups, network=True)
    out = audiences.attach("t", "acc-a", 77, ctx["groups"][0], ctx, bid_rub=12.5)
    assert out == {"attached": 3, "warnings": []}
    [(_, _, made), (_, _, targets)] = [c for c in direct.calls]
    assert made["RetargetingLists"][0]["Type"] == "AUDIENCE"
    assert made["RetargetingLists"][0]["Rules"] == [{"Operator": "ANY", "Arguments": [{"ExternalId": 100000000008}]}]
    assert [t["RetargetingListId"] for t in targets["AudienceTargets"]] == [503, ctx["created"][0], 501]
    assert {t["ContextBid"] for t in targets["AudienceTargets"]} == {12_500_000}

    # Вторая группа с тем же интересом — то же новое условие, не ещё одно.
    direct.calls.clear()
    direct.targets = {"AddResults": [{"Errors": [{"Message": "Объект уже существует", "Details": "LAL"}]}]}
    out = audiences.attach("t", "acc-a", 78, ctx["groups"][0][1:2], ctx)
    assert [c[:2] for c in direct.calls] == [("audiencetargets", "add")]
    assert out == {"attached": 0, "warnings": ["Аудитория «Отдых» не добавлена: Объект уже существует: LAL"]}

    direct.calls.clear()
    direct.list_error = "Недоступно"
    fresh = audiences.prepare("t", "acc-a", [{"name": "Б", "audiences": [{"name": "Звонки", "goal_ids": [12]}]}], network=True)
    out = audiences.attach("t", "acc-a", 79, fresh["groups"][0], fresh)
    assert out == {"attached": 0, "warnings": ["Аудитория «Звонки» не добавлена: Недоступно"]}
    assert [c[:2] for c in direct.calls] == [("retargetinglists", "add")]


# --- Чат: справочник и таблица этапа «Аудитории» -------------------------------------------------


def test_get_audiences_tool(account):
    out = ai_agent._t_audiences(FakeContext(), {})
    assert out["account_id"] == 3 and out["goals"] == [{"goal_id": 11, "name": "Заявка"}, {"goal_id": 12, "name": "Звонок"}]
    # Только годные для показа, новые сверху.
    assert [(i["list_id"], i["kind"]) for i in out["lists"]] == [(503, "ретаргетинг и сегменты"), (501, "LAL")]
    assert out["lists"][0]["rules"] == "любое из цель «Заявка» за 30 дн."
    assert [s["name"] for s in out["interest_sections"]] == ["Путешествия", "Ремонт"]
    assert any("LAL" in n for n in out["notes"])

    found = ai_agent._t_audiences(FakeContext(), {"query": "путешествия"})
    assert [i["interest_id"] for i in found["interests"]] == [7, 8] and found["lists"] == []

    account.lists_error, account.catalog = "Нет доступа", {}
    broken = ai_agent._t_audiences(FakeContext(ws={"goals": []}), {})
    assert broken["lists_error"] == "Нет доступа" and "interests_error" in broken
    assert broken["notes"][0].startswith("У проекта нет целей Метрики")

    with pytest.raises(YandexError) as err:
        ai_agent._t_audiences(FakeContext(ws={"accounts": [ACCOUNT, {**ACCOUNT, "id": 4}]}), {})
    assert "account_id" in err.value.message


def _audience_table(*rows, **args):
    return ai_agent._q_items(FakeContext(**args.pop("ctx", {})), {"stage": "audiences", "rows": list(rows), **args})


def test_items_audiences_rows(account):
    out = _audience_table(
        {"section": "Окна ПВХ", "cells": ["Оставляли заявку", "дожать тех, кто уже был"],
         "audience": {"type": "retargeting", "goal_ids": [11], "exclude_goal_ids": [12]}},
        {"section": "Похожие", "cells": ["Похожие на покупателей", "новые люди"], "audience": {"type": "list", "list_id": 501}},
        {"section": "Окна ПВХ", "cells": ["Едут на море"], "audience": {"type": "interests", "interest_ids": [8]}},
    )
    assert out["columns"] == ["Аудитория", "Что внутри", "Почему"]
    first, lal, interests = out["rows"]
    assert first["kind"] == "audience" and first["audience"]["name"] == "Оставляли заявку"
    # «Что внутри» пишет код по сути аудитории.
    assert first["cells"] == [
        "Оставляли заявку",
        "ретаргетинг: выполнили цель «Заявка» за 30 дн.; не выполнили «Звонок» за 30 дн.",
        "дожать тех, кто уже был",
    ]
    assert lal["lal"] is True and lal["cells"][1].startswith("LAL — условие кабинета «Похожие на покупателей»")
    assert interests["cells"] == ["Едут на море", "интересы: Пляжный отдых", ""] and "lal" not in interests
    # В других этапах kind audience не бывает.
    keywords = ai_agent._q_items(FakeContext(), {"stage": "keywords", "rows": [{"kind": "audience", "cells": ["окна"]}]})
    assert keywords["rows"][0]["kind"] == "keyword"


@pytest.mark.parametrize(
    "row, text",
    [
        ({"cells": ["Заявки"], "audience": {"goal_ids": [11]}}, "section — группа аудитории"),
        ({"section": "А", "cells": ["Заявки"], "audience": {"goal_ids": [99]}}, "целей 99 нет в проекте"),
        ({"section": "А", "cells": ["Х"], "audience": {"list_id": 404}}, "условия №404 нет в кабинете"),
        ({"section": "А", "cells": ["Х"], "audience": {"list_id": 502}}, "не годится для показа"),
        ({"section": "А", "cells": ["Х"], "audience": {"interest_ids": [404]}}, "нет в справочнике"),
        ({"section": "А", "cells": ["Х"], "audience": None}, "объект"),
    ],
)
def test_items_audiences_errors(account, row, text):
    with pytest.raises(YandexError) as err:
        _audience_table(row)
    assert text in err.value.message


def test_items_audiences_duplicates_and_no_goals(account):
    same = {"goal_ids": [11]}
    with pytest.raises(YandexError) as err:
        _audience_table({"section": "А", "cells": ["Заявки"], "audience": same},
                        {"section": "А", "cells": ["Тоже заявки"], "audience": same})
    assert "уже есть" in err.value.message
    # В другой группе та же аудитория — можно.
    _audience_table({"section": "А", "cells": ["Заявки"], "audience": same},
                    {"section": "Б", "cells": ["Заявки"], "audience": same})
    with pytest.raises(YandexError) as err:
        _audience_table({"section": "А", "cells": ["Заявки"], "audience": same}, ctx={"ws": {"goals": []}})
    assert "нет целей Метрики" in err.value.message
    # Кабинет не ответил — условия проверит создание кампании.
    account.lists_error = "Таймаут"
    out = _audience_table({"section": "А", "cells": ["Х"], "audience": {"list_id": 404}})
    assert out["rows"][0]["cells"][1] == "условие кабинета №404"


# --- Согласование и создание кампании -------------------------------------------------------------


RETARGET = {"type": "retargeting", "name": "Оставляли заявку", "goal_ids": [11]}
INTERESTS = {"type": "interests", "name": "Едут на море", "interest_ids": [8]}


def _card(stage, rows, approved):
    rows = [{"n": i + 1, "checked": True, **r} for i, r in enumerate(rows)]
    return {"tool": "propose_items", "args": {"stage": stage, "rows": rows}, "result": {"approved_rows": approved}}


def _aud_row(group, aud):
    clean = audiences.clean_audience(aud)
    return {"section": group, "kind": "audience", "cells": [clean["name"], ""], "audience": clean}


def test_approved_audiences_follow_tables_and_board():
    cards = [
        _card("audiences", [_aud_row("Окна", RETARGET), _aud_row("Окна", INTERESTS), _aud_row("Море", INTERESTS)], [1, 2]),
        _card("keywords", [{"section": "Окна", "cells": ["окна"]}], [1]),
        {"tool": "board_edit", "args": {"ops": [{"op": "rename_group", "group": "Окна", "to": "Окна ПВХ"}]}, "result": None},
    ]
    approved = ai_agent._approved_audiences(cards, [])
    assert [(a["name"], a["group"]) for a in approved] == [("оставляли заявку", "Окна ПВХ"), ("едут на море", "Окна ПВХ")]
    # Убрали на доске (в том числе правкой, которая применяется сейчас).
    removed = {"op": "remove", "kind": "audience", "group": "Окна ПВХ", "text": "Едут на море!"}
    assert [a["name"] for a in ai_agent._approved_audiences(cards, [removed])] == ["оставляли заявку"]
    # Новая таблица предложила аудиторию заново, а маркетолог снял галочку — не согласована.
    again = _card("audiences", [_aud_row("Окна ПВХ", RETARGET)], [])
    assert [a["name"] for a in ai_agent._approved_audiences(cards + [again], [])] == ["едут на море"]


def test_require_audiences(monkeypatch):
    approved = ai_agent._approved_audiences([_card("audiences", [_aud_row("Окна", RETARGET)], [1])], [])
    monkeypatch.setattr(ai_agent, "_audience_approvals", lambda ctx: approved)
    retarget = audiences.clean_audience({**RETARGET, "name": "Другое название"})  # суть та же
    ai_agent._require_audiences(FakeContext(), [("Окна", retarget)])
    with pytest.raises(YandexError) as err:
        ai_agent._require_audiences(FakeContext(), [("Двери", retarget), ("Окна", audiences.clean_audience(INTERESTS))])
    assert "согласована для группы «Окна», а не «Двери»" in err.value.message
    assert "«Едут на море» (группа «Окна»)" in err.value.message
    monkeypatch.setattr(ai_agent, "_audience_approvals", lambda ctx: pytest.fail("не из чата — не проверяем"))
    ai_agent._require_audiences(FakeContext(chat_id=None), [("Двери", retarget)])


@pytest.fixture
def approvals(monkeypatch):
    state = SimpleNamespace(stages={"goals", "analysis", "keywords", "audiences", "ads", "settings"}, audiences=[])
    rows = ["купить окна пвх", "Окна ПВХ от завода", "Замер бесплатно", "Отдых у моря", "Окна на дачу"]

    def stage_approvals(ctx):
        keys = {ai_agent._item_key(t, "keyword") for t in rows} | {ai_agent._item_key(t) for t in rows}
        return state.stages, keys, ""

    monkeypatch.setattr(ai_agent, "_stage_approvals", stage_approvals)
    monkeypatch.setattr(ai_agent, "_audience_approvals", lambda ctx: state.audiences)
    return state


def _spec(strategy, groups):
    return {"name": "Окна", "strategy": strategy, "groups": groups}


GROUP = {"name": "Окна ПВХ", "keywords": ["купить окна пвх"], "audiences": [RETARGET],
         "ad": {"titles": ["Окна ПВХ от завода"], "texts": ["Замер бесплатно"], "href": "https://okna.ru"}}
SEA = {"name": "Море", "audiences": [INTERESTS],
       "ad": {"titles": ["Отдых у моря"], "texts": ["Окна на дачу"], "href": "https://okna.ru/sea"}}
NETWORK = {"search": "WB_MAXIMUM_CLICKS", "weekly_budget_rub": 3000}


def test_create_campaign_with_audiences(approvals):
    approvals.audiences = ai_agent._approved_audiences(
        [_card("audiences", [_aud_row("Окна ПВХ", RETARGET), _aud_row("Море", INTERESTS)], [1, 2])], []
    )
    plan = ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(NETWORK, [GROUP, SEA])})
    assert plan.summary.endswith("2 группы, 1 фраза, 2 объявления, 2 аудитории)")

    approvals.stages.discard("audiences")
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(NETWORK, [GROUP])})
    assert "Аудитории" in err.value.message

    approvals.stages.add("audiences")
    approvals.audiences = []
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(NETWORK, [GROUP])})
    assert "Этих аудиторий маркетолог не согласовывал" in err.value.message


def test_create_campaign_audiences_need_network(approvals):
    manual = {"search": "HIGHEST_POSITION"}  # ручные ставки — сети выключены
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(manual, [GROUP, SEA])})
    assert "Интересы работают только в сетях" in err.value.message
    bare = {**SEA, "audiences": [RETARGET]}
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(manual, [bare])})
    assert "Группы без фраз показываются только в сетях" in err.value.message and "«Море»" in err.value.message
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(NETWORK, [{**GROUP, "audiences": [RETARGET, RETARGET]}])})
    assert "повторяется" in err.value.message


def test_create_campaign_card_and_picks(monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    monkeypatch.setattr(geo, "region_label", lambda ids, regions: "Россия")
    args = {"account_id": 3, "spec": _spec(NETWORK, [GROUP, SEA])}
    rows = {c["what"]: c["after"] for c in ai_agent._d_create_campaign(FakeContext(), args)}
    assert rows["Аудитории «Окна ПВХ»"] == (
        "Оставляли заявку (ретаргетинг) · на поиске фразы группы покажутся только этой аудитории "
        "· в сетях — и по фразам, и по аудитории"
    )
    assert rows["Группа «Море»"].startswith("без фраз — показ в сетях по аудитории · Россия")
    assert rows["Аудитории «Море»"] == "Едут на море (интересы и привычки)"
    picks = [p for p in ai_agent._picks(FakeContext(), "create_campaign", args) if p["kind"] == "audience"]
    assert [(p["id"], p["text"], p["section"], p["note"]) for p in picks] == [
        ("spec.groups.0.audiences.0", "Оставляли заявку", "Окна ПВХ", "ретаргетинг"),
        ("spec.groups.1.audiences.0", "Едут на море", "Море", "интересы и привычки"),
    ]


# --- Доска -------------------------------------------------------------------------------------------


def _table(action_id, stage, rows, status="done", approved=None):
    rows = [{"n": i + 1, "section": "", "kind": "", "checked": True, **r} for i, r in enumerate(rows)]
    result = {"approved_rows": approved if approved is not None else [r["n"] for r in rows]} if status == "done" else None
    return {"id": action_id, "tool": "propose_items", "status": status, "args": {"stage": stage, "rows": rows}, "result": result}


def _board_rows(lal=False):
    lal_row = _aud_row("Похожие", {"type": "list", "name": "Похожие на покупателей", "list_id": 501})
    lal_row["cells"] = ["Похожие на покупателей", "LAL — условие кабинета", "новые люди"]
    if lal:
        lal_row["lal"] = True
    return [
        _table(0, "goals", [{"cells": ["Ссылка", "https://okna.ru"]}]),
        _table(1, "keywords", [{"section": "Окна", "cells": ["купить окна", "9 000"]}]),
        _table(2, "audiences", [
            {**_aud_row("Окна", RETARGET), "cells": ["Оставляли заявку", "ретаргетинг: …", "дожать"]},
            lal_row,
        ]),
        _table(3, "ads", [
            {"section": "Окна", "kind": "title", "cells": ["Окна от завода"]},
            {"section": "Окна", "kind": "text", "cells": ["Замер бесплатно"]},
            {"section": "Похожие", "kind": "title", "cells": ["Окна для дома"]},
            {"section": "Похожие", "kind": "text", "cells": ["Монтаж за день"]},
        ]),
    ]


def test_campaign_build_shows_audiences():
    build = ai_agent.campaign_build(_board_rows(lal=True))
    okna, similar = build["groups"]
    assert [(a["text"], a["value"], a["note"], a["type"], a["lal"]) for a in okna["audiences"]] == [
        ("Оставляли заявку", "ретаргетинг: …", "дожать", "retargeting", False),
    ]
    # Группа только с аудиторией — на доске и не «без фраз».
    assert similar["keywords"] == [] and similar["audiences"][0]["lal"] is True
    checks = {c["key"]: (c["state"], c["note"]) for c in build["checks"]}
    assert checks["keywords"] == ("ok", "2 группы, 1 фраза; по аудитории без фраз: «Похожие»")
    assert checks["audiences"][0] == "warn" and "2 аудитории в 2 группах; LAL «Похожие на покупателей»" in checks["audiences"][1]
    stage = next(s for s in build["stages"] if s["key"] == "audiences")
    assert (stage["status"], stage["count"], stage["action_id"]) == ("done", 2, 2)

    # Ждущая таблица — аудитории с галочками; без аудиторий этап необязателен.
    pending = _board_rows()[:2] + [{**_board_rows()[2], "status": "pending", "result": None}]
    build = ai_agent.campaign_build(pending)
    assert [(a["pending"], a["action_id"], a["n"]) for g in build["groups"] for a in g["audiences"]] == [(True, 2, 1), (True, 2, 2)]
    assert "audiences" not in {c["key"] for c in ai_agent.campaign_build(_board_rows()[:2])["checks"]}


def test_board_removes_audience():
    edits = {"id": 4, "tool": "board_edit", "status": "done", "args": {"ops": [
        {"op": "remove", "kind": "audience", "group": "Окна", "text": "оставляли заявку"},
        {"op": "rename_group", "group": "Похожие", "to": "LAL"},
    ]}}
    build = ai_agent.campaign_build(_board_rows() + [edits])
    assert [(g["name"], [a["text"] for a in g["audiences"]]) for g in build["groups"]] == [
        ("Окна", []), ("LAL", ["Похожие на покупателей"]),
    ]

    with pytest.raises(YandexError) as err:
        ai_agent._board_op({"op": "add", "kind": "audience", "group": "Окна", "text": "Новая"})
    assert "можно только убрать" in err.value.message
    build = ai_agent.campaign_build(_board_rows())
    op = ai_agent._board_op({"op": "remove", "kind": "audience", "group": "Окна", "text": "Оставляли заявку"})
    assert ai_agent._board_validate(build, op)["type"] == "retargeting"
    assert ai_agent._board_note(op) == "убрал аудиторию «Оставляли заявку» из группы «Окна»"
    with pytest.raises(YandexError):
        ai_agent._board_validate(build, ai_agent._board_op({**op, "text": "Нет такой"}))

    spec = {"groups": [{"name": "Окна", "keywords": ["окна"], "audiences": [RETARGET, INTERESTS]}]}
    ai_agent._spec_patch(spec, {**op, "text": "оставляли  заявку!"})
    assert spec["groups"][0]["audiences"] == [INTERESTS]
    with pytest.raises(YandexError):
        ai_agent._spec_patch(spec, op)


def test_create_card_keeps_audience_details():
    spec = {"name": "Окна", "groups": [
        {"name": "Окна", "keywords": ["купить окна"], "audiences": [{**RETARGET, "name": "ОСТАВЛЯЛИ заявку"}],
         "ad": {"titles": ["Окна от завода"], "texts": ["Замер бесплатно"], "href": "https://okna.ru"}},
        {"name": "Похожие", "audiences": [{"type": "list", "name": "Похожие на покупателей", "list_id": 501}, {"bad": 1}],
         "ad": {"titles": ["Окна для дома"], "texts": ["Монтаж за день"], "href": "https://okna.ru"}},
    ]}
    create = {"id": 5, "tool": "create_campaign", "status": "pending", "args": {"spec": spec}, "summary": "Создать"}
    build = ai_agent.campaign_build(_board_rows(lal=True) + [create])
    okna, similar = build["groups"]
    # Суть и причина — из таблицы этапа, по названию.
    assert [(a["text"], a["value"], a["note"]) for a in okna["audiences"]] == [("ОСТАВЛЯЛИ заявку", "ретаргетинг: …", "дожать")]
    assert [(a["text"], a["type"], a["lal"]) for a in similar["audiences"]] == [("Похожие на покупателей", "list", True)]


# --- Новый LAL из Яндекс Аудиторий ---------------------------------------------------------------


LOOKALIKE = {"type": "lookalike", "name": "Похожие на тех, кто оставил заявку", "source": {"goal_id": 11}}
LAL_GROUP = {"name": "Похожие", "audiences": [LOOKALIKE]}


def test_clean_lookalike_forms():
    aud = audiences.clean_audience({**LOOKALIKE, "name": "  Похожие   на заявки ", "source": {"goal_id": "11"}})
    assert aud == {"type": "lookalike", "name": "Похожие на заявки", "source": {"kind": "goal", "id": 11}, "similarity": 3}
    # Тип по источнику; источник — {kind, id}, {segment_id} или поле аудитории.
    assert audiences.clean_audience({"name": "А", "source": {"kind": "segment", "id": "5"}, "similarity": "5"}) == {
        "type": "lookalike", "name": "А", "source": {"kind": "segment", "id": 5}, "similarity": 5,
    }
    assert audiences.clean_audience({"name": "А", "type": "lookalike", "counter_id": 555})["source"] == {"kind": "counter", "id": 555}
    assert audiences.goal_ids(aud) == [11]
    assert audiences.goal_ids(audiences.clean_audience({"name": "А", "source": {"counter_id": 555}})) == []
    # Суть — источник и похожесть, не название.
    same = audiences.clean_audience({"name": "Другое", "source": {"kind": "goal", "id": 11}})
    closer = audiences.clean_audience({**LOOKALIKE, "similarity": 5})
    assert audiences.audience_signature(aud) == audiences.audience_signature(same) == ("lookalike", "goal", 11, 3)
    assert audiences.audience_signature(closer) != audiences.audience_signature(aud)


def test_describe_lookalike():
    goal = audiences.clean_audience(LOOKALIKE)
    assert audiences.describe(goal, goals={11: "Заявка"}) == "похожие на тех, кто выполнил цель «Заявка», похожесть 3 из 5"
    assert audiences.summary(goal, goals={11: "Заявка"}) == (
        f"новый LAL — похожие на тех, кто выполнил цель «Заявка», похожесть 3 из 5. {audiences.NEW_LAL_NOTE}. "
        f"{audiences.LAL_NOTE}"
    )
    segment = audiences.clean_audience({"name": "А", "source": {"segment_id": 5}, "similarity": 1})
    assert audiences.describe(segment, segments={5: "Клиенты из CRM"}) == "похожие на сегмент Аудиторий «Клиенты из CRM», похожесть 1 из 5"
    assert audiences.describe(segment) == "похожие на сегмент Аудиторий №5, похожесть 1 из 5"
    counter = audiences.clean_audience({"name": "А", "source": {"counter_id": 555}})
    assert audiences.source_text(counter["source"]) == "посетителей сайта (счётчик 555)"
    item = {"type": "RETARGETING", "rules": [{"operator": "ANY", "ids": [audiences.AUDIENCE_OFFSET + 5], "days": None}]}
    assert audiences.list_text(item) == "любое из сегмент Аудиторий №5"


def test_audience_rights_and_texts():
    assert audiences.has_rights("login:info direct:api audience:read audience:write")
    assert not audiences.has_rights("login:info direct:api audience:read")
    assert not audiences.has_rights(None)
    text = audiences.rights_text("acc-a")
    assert "@acc-a" in text and "«Подключить Аудитории»" in text
    assert audiences.segment_text({"type": "uploading", "status": "processed"}) == "загруженный список (CRM); готов"
    assert audiences.segment_text({"type": "lookalike", "lookalike_link": 5, "lookalike_value": 2, "status": "is_processed"}) == (
        "LAL от сегмента №5, похожесть 2 из 5; обрабатывается"
    )
    assert audiences.segment_text({"type": "metrika", "metrika_segment_type": "goal_id", "metrika_segment_id": 11}) == "Метрика: цель 11"


def test_audience_call_errors(monkeypatch):
    replies = []

    class Response:
        def __init__(self, status, payload):
            self.status_code, self._payload = status, payload
            self.content = b"{}"

        def json(self):
            return self._payload

    class Client:
        def request(self, method, url, **kw):
            assert url.startswith(audiences.AUDIENCE_API) and kw["headers"]["Authorization"] == "OAuth t"
            return replies.pop(0)

    monkeypatch.setattr(audiences.direct_api, "_client", lambda: Client())

    def error(status, payload, path="segments"):
        replies.append(Response(status, payload))
        with pytest.raises(YandexError) as err:
            audiences.audience_call("t", "GET", path)
        return err.value

    replies.append(Response(200, {"segments": [{"id": 5}, {"id": None}]}))
    assert audiences.list_segments("t") == [{"id": 5}]
    denied = {"errors": [{"error_type": "access_denied", "message": "Access denied"}], "code": 403}
    assert error(403, denied).code == "audience-scope" and "«Подключить Аудитории»" in error(401, {}).message
    # 403 на чужой сегмент — не про права подключения.
    other = error(403, denied, "segment/7/grant")
    assert (other.code, other.message) == ("audience", "Access denied")
    assert "Лимит запросов" in error(429, {"errors": [{"error_type": "quota", "message": "Quota"}]}).message
    assert error(400, {"message": "Bad"}).message == "Bad"
    with pytest.raises(YandexError) as err:
        audiences.audience_call(SimpleNamespace(sandbox=True), "GET", "segments")
    assert err.value.code == "sandbox"


def test_prepare_lookalike_needs_rights_and_source(account):
    def error(groups):
        with pytest.raises(YandexError) as err:
            audiences.prepare("t", "acc-a", groups, network=True, rights=audiences.account_rights(1, 3))
        return err.value

    account.rights = False
    no_rights = error([LAL_GROUP])
    assert no_rights.code == "audience-scope" and "@acc-a" in no_rights.message
    assert account.audience_calls == []
    # Права в подключении есть, а Аудитории ответили «нет доступа» — тот же понятный текст.
    account.rights = True
    account.segments_error = YandexError("audience-scope", audiences.rights_text())
    assert "@acc-a" in error([LAL_GROUP]).message
    account.segments_error = None
    segment = {"name": "Похожие на CRM", "source": {"segment_id": 77}}
    assert "№77" in error([{"name": "А", "audiences": [segment]}]).message
    account.segments = [{"id": 77, "name": "CRM", "type": "uploading", "status": "processing_failed"}]
    assert "«CRM» не обработан" in error([{"name": "А", "audiences": [segment]}]).message
    account.segments[0]["status"] = "processed"
    ctx = audiences.prepare("t", "acc-a", [{"name": "А", "audiences": [segment]}], network=True, rights=RIGHTS)
    assert [s["id"] for s in ctx["segments"]] == [77] and ctx["groups"][0][0]["source"] == {"kind": "segment", "id": 77}


def test_attach_creates_lookalike_segment(account, direct):
    # Сегменты в Аудиториях агентского логина — клиенту кабинета нужен доступ к ним.
    account.owner = "agency"
    groups = [LAL_GROUP, {"name": "Ещё", "audiences": [{**LOOKALIKE, "name": "Те же похожие", "similarity": 3}]}]
    ctx = audiences.prepare("t", "acc-a", groups, network=True, rights={**RIGHTS, "login": "agency"})
    out = audiences.attach("t", "acc-a", 77, ctx["groups"][0], ctx)
    assert out == {"attached": 1, "warnings": []}
    # Аудитории — токеном второго приложения, Директ — токеном кабинета.
    assert set(account.audience_tokens) == {"aud-token"} and set(direct.tokens) == {"t"}
    [(_, _, _), (_, _, metrika), (_, _, lookalike), (method, grant_path, grant)] = account.audience_calls
    source_id, lal_id = ctx["segments_created"]
    assert metrika == {"segment": {"name": "Источник LAL «Похожие на тех, кто оставил заявку»", "metrika_segment_type": "goal_id",
                                   "metrika_segment_id": 11}}
    assert lookalike == {"segment": {"name": "Похожие на тех, кто оставил заявку", "lookalike_link": source_id, "lookalike_value": 3,
                                     "maintain_device_distribution": True, "maintain_geo_distribution": True}}
    assert (method, grant_path, grant["grant"]["user_login"], grant["grant"]["permission"]) == ("PUT", f"segment/{lal_id}/grant", "acc-a", "view")
    [(_, _, made), (_, _, targets)] = direct.calls
    assert made["RetargetingLists"] == [{"Type": "RETARGETING", "Name": "Похожие на тех, кто оставил заявку",
                                         "Rules": [{"Operator": "ANY", "Arguments": [{"ExternalId": 2_000_000_000 + lal_id}]}]}]
    assert targets["AudienceTargets"] == [{"AdGroupId": 77, "RetargetingListId": ctx["created"][0]}]

    # Та же суть в другой группе — тот же сегмент и условие, без новых запросов.
    account.audience_calls.clear()
    direct.calls.clear()
    assert audiences.attach("t", "acc-a", 78, ctx["groups"][1], ctx) == {"attached": 1, "warnings": []}
    assert account.audience_calls == [] and [c[:2] for c in direct.calls] == [("audiencetargets", "add")]


def test_attach_reuses_existing_lookalike(account, direct):
    lal_id = 41
    account.segments = [
        {"id": 40, "type": "metrika", "metrika_segment_type": "goal_id", "metrika_segment_id": 11, "status": "processed", "owner": "Acc.A"},
        {"id": 39, "type": "lookalike", "lookalike_link": 40, "lookalike_value": 3, "status": "processing_failed", "owner": "acc-a"},
        {"id": lal_id, "type": "lookalike", "lookalike_link": 40, "lookalike_value": 3, "status": "processed", "owner": "Acc.A"},
    ]
    # Условие на этот сегмент в кабинете уже есть (срок у сегментов Аудиторий не важен).
    account.lists = LISTS + [{"id": 504, "name": "LAL заявок", "type": "RETARGETING", "scope": "FOR_TARGETS_AND_ADJUSTMENTS",
                              "available": True, "lal": False,
                              "rules": [{"operator": "ANY", "ids": [audiences.AUDIENCE_OFFSET + lal_id], "days": 30}]}]
    ctx = audiences.prepare("t", "acc-a", [LAL_GROUP], network=True)
    assert audiences.attach("t", "acc-a", 77, ctx["groups"][0], ctx) == {"attached": 1, "warnings": []}
    # Только список сегментов: свой логин (точка и дефис равны) — доступ выдавать не нужно.
    assert [c[:2] for c in account.audience_calls] == [("GET", "segments")]
    assert direct.calls == [("audiencetargets", "add", {"AudienceTargets": [{"AdGroupId": 77, "RetargetingListId": 504}]})]
    assert "segments_created" not in ctx


def test_attach_lookalike_warnings(account, direct):
    # Директ видит сегмент только «Готов» — группа создаётся без LAL, маркетолог знает, что делать.
    direct.list_error = "Сегмент недоступен"
    ctx = audiences.prepare("t", "acc-a", [LAL_GROUP], network=True)
    out = audiences.attach("t", "acc-a", 77, ctx["groups"][0], ctx)
    lal_id = ctx["segments_created"][1]
    assert out["attached"] == 0 and out["warnings"] == [
        f"Аудитория «Похожие на тех, кто оставил заявку» не добавлена: LAL-сегмент №{lal_id} есть в Яндекс Аудиториях, "
        "но Директ его пока не принял (Сегмент недоступен). Сегмент обрабатывается от двух часов; когда в Аудиториях "
        "он станет «Готов», добавьте его группе условием подбора аудитории"
    ]
    assert [c[:2] for c in direct.calls] == [("retargetinglists", "add")]

    # Аудитории не построили LAL из свежего источника.
    account.audience_calls.clear()
    account.lal_error = "Сегмент слишком мал"
    ctx = audiences.prepare("t", "acc-a", [LAL_GROUP], network=True)
    [warning] = audiences.attach("t", "acc-a", 78, ctx["groups"][0], ctx)["warnings"]
    assert "Яндекс Аудитории не создали LAL: Сегмент слишком мал. Источник ещё не готов (загружен, ждёт обработки)" in warning


def test_get_audiences_lookalike(account):
    account.segments = [
        {"id": 5, "name": "Клиенты из CRM", "type": "uploading", "status": "processed"},
        {"id": 9, "name": "LAL CRM", "type": "lookalike", "lookalike_link": 5, "lookalike_value": 3, "status": "is_processed"},
    ]
    out = ai_agent._t_audiences(FakeContext(), {})
    assert out["lookalike"] == {"available": True, "segments": [
        {"segment_id": 9, "name": "LAL CRM", "what": "LAL от сегмента №5, похожесть 3 из 5; обрабатывается"},
        {"segment_id": 5, "name": "Клиенты из CRM", "what": "загруженный список (CRM); готов"},
    ]}
    assert any("type: lookalike" in n for n in out["notes"]) and not any("сейчас не создать" in n for n in out["notes"])

    account.segments_error = YandexError("audience", "Аудитории не ответили")
    assert ai_agent._t_audiences(FakeContext(), {})["lookalike"] == {"available": True, "segments_error": "Аудитории не ответили"}
    account.segments_error = YandexError("audience-scope", "нет прав")
    assert ai_agent._t_audiences(FakeContext(), {})["lookalike"]["available"] is False

    account.rights = False
    account.audience_calls.clear()
    out = ai_agent._t_audiences(FakeContext(), {})
    assert out["lookalike"] == {"available": False, "why": audiences.rights_text("acc-a")} and account.audience_calls == []
    assert any(n.startswith("Новый LAL сейчас не создать: Яндекс Аудитории не подключены логину @acc-a") for n in out["notes"])


def test_items_lookalike_rows(account):
    out = _audience_table(
        {"section": "Похожие", "cells": ["Похожие на тех, кто оставил заявку", "новые люди"],
         "audience": {"type": "lookalike", "source": {"goal_id": 11}}},
    )
    [row] = out["rows"]
    assert row["audience"] == {"type": "lookalike", "name": "Похожие на тех, кто оставил заявку",
                               "source": {"kind": "goal", "id": 11}, "similarity": 3}
    assert row["lal"] is True and row["lookalike"] == {"source": "тех, кто выполнил цель «Заявка»", "similarity": 3}
    assert row["cells"][1].startswith("новый LAL — похожие на тех, кто выполнил цель «Заявка», похожесть 3 из 5.")
    # Сегмент Аудиторий — по названию.
    account.segments = [{"id": 5, "name": "Клиенты из CRM", "type": "uploading", "status": "processed"}]
    out = _audience_table({"section": "А", "cells": ["Как клиенты"], "audience": {"source": {"segment_id": 5}, "similarity": 4}})
    assert out["rows"][0]["lookalike"] == {"source": "сегмент Аудиторий «Клиенты из CRM»", "similarity": 4}


@pytest.mark.parametrize(
    "audience, text",
    [
        ({"source": {"goal_id": 99}}, "целей 99 нет в проекте"),
        ({"source": {"segment_id": 77}}, "сегмента Аудиторий №77 нет"),
        ({"source": {"goal_id": 11}, "similarity": 0}, "от 1 до 5"),
    ],
)
def test_items_lookalike_errors(account, audience, text):
    with pytest.raises(YandexError) as err:
        _audience_table({"section": "А", "cells": ["Похожие"], "audience": {"type": "lookalike", **audience}})
    assert text in err.value.message


def test_items_lookalike_without_rights(account):
    account.rights = False
    with pytest.raises(YandexError) as err:
        _audience_table({"section": "А", "cells": ["Похожие"], "audience": LOOKALIKE})
    assert err.value.code == "audience-scope" and "«Подключить Аудитории»" in err.value.message
    assert account.audience_calls == []


def test_get_audiences_uses_audience_token(account):
    account.segments = [{"id": 5, "name": "Клиенты из CRM", "type": "uploading", "status": "processed"}]
    assert ai_agent._t_audiences(FakeContext(), {})["lookalike"]["available"] is True
    assert account.audience_tokens == ["aud-token"]
    # Токен Аудиторий отозван — новый LAL недоступен с понятной причиной, остальное на месте.
    account.token_error = YandexError("audience-scope")
    out = ai_agent._t_audiences(FakeContext(), {})
    assert out["lookalike"] == {"available": False, "why": audiences.rights_text("acc-a")} and out["lists"]


def test_lookalike_off_without_audience_app(account, direct):
    """Приложение Аудиторий не задано — новых LAL нет нигде, остальное работает как раньше."""
    account.enabled = False
    out = ai_agent._t_audiences(FakeContext(), {})
    assert "lookalike" not in out and [i["list_id"] for i in out["lists"]][-1] == 501
    assert not any("type: lookalike" in n or "сейчас не создать" in n for n in out["notes"])
    assert any("только готовое условие кабинета с kind LAL" in n for n in out["notes"])
    assert "type: lookalike, source" not in ai_agent._lal_prompt() and "kind LAL" in ai_agent._lal_prompt()
    # Таблица с новым LAL — отказ с подсказкой; готовое LAL-условие кабинета — как обычно.
    with pytest.raises(YandexError) as err:
        _audience_table({"section": "А", "cells": ["Похожие"], "audience": LOOKALIKE})
    assert (err.value.code, err.value.message) == ("args", audiences.DISABLED_TEXT)
    ready = _audience_table({"section": "А", "cells": ["Похожие на покупателей"], "audience": {"type": "list", "list_id": 501}})
    assert ready["rows"][0]["lal"] is True
    # Создание кампании с новым LAL не доходит до Аудиторий.
    with pytest.raises(YandexError) as err:
        audiences.prepare("t", "acc-a", [LAL_GROUP], network=True, rights=audiences.account_rights(1, 3))
    assert err.value.code == "audience-config" and err.value.message == audiences.DISABLED_TEXT
    ready_lal = {"type": "list", "list_id": 501, "name": "Похожие на покупателей"}
    ctx = audiences.prepare("t", "acc-a", [{"name": "А", "audiences": [ready_lal]}], network=True,
                            rights=audiences.account_rights(1, 3))
    assert audiences.attach("t", "acc-a", 77, ctx["groups"][0], ctx) == {"attached": 1, "warnings": []}
    assert account.audience_calls == []
    account.enabled = True
    assert "type: lookalike, source" in ai_agent._lal_prompt()


def test_audience_token_errors(monkeypatch):
    with pytest.raises(YandexError) as err:
        audiences.audience_token({**RIGHTS, "enabled": False})
    assert err.value.code == "audience-config"
    with pytest.raises(YandexError) as err:
        audiences.audience_token({**RIGHTS, "rights": False})
    assert err.value.code == "audience-scope" and "@acc-a" in err.value.message

    def revoked(connection_id):
        raise YandexError("audience-scope")

    monkeypatch.setattr(yandex, "get_audience_access_token", revoked)
    with pytest.raises(YandexError) as err:
        audiences.audience_token(RIGHTS)
    assert err.value.message == audiences.rights_text("acc-a")


def test_lookalike_approval_and_card(approvals, monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    monkeypatch.setattr(geo, "region_label", lambda ids, regions: "Россия")
    group = {**SEA, "name": "Похожие", "audiences": [LOOKALIKE]}
    approvals.audiences = ai_agent._approved_audiences([_card("audiences", [_aud_row("Похожие", LOOKALIKE)], [1])], [])
    args = {"account_id": 3, "spec": _spec(NETWORK, [group])}
    assert ai_agent._p_create_campaign(FakeContext(), args).summary.endswith("1 аудитория)")
    rows = {c["what"]: c["after"] for c in ai_agent._d_create_campaign(FakeContext(), args)}
    assert rows["Аудитории «Похожие»"].startswith("Похожие на тех, кто оставил заявку (новый LAL)")
    assert "новый LAL создастся в Яндекс Аудиториях" in rows["Аудитории «Похожие»"]
    # Согласовали похожесть 3 — ближе к источнику без согласования не создаём.
    closer = {**group, "audiences": [{**LOOKALIKE, "similarity": 5}]}
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(NETWORK, [closer])})
    assert "Этих аудиторий маркетолог не согласовывал" in err.value.message


def test_board_shows_lookalike():
    row = _aud_row("Похожие", LOOKALIKE)
    row.update(cells=["Похожие на тех, кто оставил заявку", "новый LAL — …", "новые люди"], lal=True,
               lookalike={"source": "тех, кто выполнил цель «Заявка»", "similarity": 3})
    tables = _board_rows()[:2] + [_table(2, "audiences", [row])]
    [lal] = [a for g in ai_agent.campaign_build(tables)["groups"] for a in g["audiences"]]
    assert (lal["type"], lal["lal"], lal["lookalike"]) == ("lookalike", True, {"source": "тех, кто выполнил цель «Заявка»", "similarity": 3})

    # Карточка создания: суть из таблицы, а без таблицы — из источника.
    other = {**LOOKALIKE, "name": "Другие", "source": {"counter_id": 555}}
    spec = {"name": "Окна", "groups": [{"name": "Похожие", "audiences": [LOOKALIKE, other],
                                        "ad": {"titles": ["Окна для дома"], "texts": ["Монтаж за день"], "href": "https://okna.ru"}}]}
    create = {"id": 5, "tool": "create_campaign", "status": "pending", "args": {"spec": spec}, "summary": "Создать"}
    [group] = [g for g in ai_agent.campaign_build(tables + [create])["groups"] if g["audiences"]]
    assert [(a["text"], a["lookalike"]["source"], a["lal"]) for a in group["audiences"]] == [
        ("Похожие на тех, кто оставил заявку", "тех, кто выполнил цель «Заявка»", True),
        ("Другие", "посетителей сайта (счётчик 555)", True),
    ]
