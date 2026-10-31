"""Новая кампания по этапам, таблицы propose_items и строки с галочками в карточках — без БД."""

from types import SimpleNamespace

import pytest

from app import ai_agent, geo
from app.workspaces import normalize_ai_access
from app.yandex import YandexError

ACCOUNT = {"id": 3, "client_login": "acc-a", "name": "Кабинет"}


class FakeContext:
    """Context без БД: пространство с одним кабинетом; chat_id — чат, где идёт работа."""

    require_account = ai_agent.Context.require_account

    def __init__(self, ws: dict | None = None, chat_id: int | None = 5):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "accounts": [ACCOUNT], **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))
        self.chat_id = chat_id


def _row(tool: str, args: dict, **extra) -> dict:
    return {"id": 11, "chat_id": 5, "message_id": 6, "tool_call_id": "call_1", "tool": tool, "workspace_id": 2,
            "args": args, "summary": "", "category": "", "status": "pending", "auto": False, **extra}


@pytest.fixture
def card(monkeypatch):
    """Карточка таблицы без БД: closed — как её закрыли бы, submitted — запущенные ходы ИИ."""
    fake = SimpleNamespace(row=None, left=0, closed=[], submitted=[])

    def pending(user_id, action_id, tool):
        assert (action_id, tool) == (fake.row["id"], fake.row["tool"])
        return fake.row

    def close(user_id, row, *, status, result, error, reply):
        fake.closed.append({"status": status, "result": result, "error": error, "reply": reply})
        return {**row, "status": status, "result": result, "error": error}, fake.left

    monkeypatch.setattr(ai_agent, "_pending_interactive", pending)
    monkeypatch.setattr(ai_agent, "_close_interactive", close)
    monkeypatch.setattr(ai_agent, "_submit", lambda user_id, chat_id, marker: fake.submitted.append(marker))
    return fake


@pytest.fixture
def approvals(monkeypatch):
    """Что «согласовано в чате»: этапы, строки таблиц и слова маркетолога."""
    state = SimpleNamespace(stages=set(), rows=[], said="")

    def fake(ctx):
        keys = set()
        for text in state.rows:
            keys |= {ai_agent._item_key(text, "keyword"), ai_agent._item_key(text)}
        return state.stages, keys, f" {ai_agent._item_key(state.said)} "

    monkeypatch.setattr(ai_agent, "_stage_approvals", fake)
    return state


# --- Таблица propose_items ---------------------------------------------------------------------


def test_items_table_defaults_and_dedup():
    out = ai_agent._q_items(FakeContext(), {"stage": "keywords", "intro": " Частоты из Вордстата ", "rows": [
        {"section": "Окна ПВХ", "cells": ["купить окна пвх", "12 000"]},
        {"section": "Окна ПВХ", "cells": ["Купить  окна ПВХ!", "12 000"]},  # та же фраза
        {"section": "Окна ПВХ", "kind": "negative", "cells": ["-бесплатно"], "checked": False},
        "окна на дачу",
        {"cells": [""]},
        None,
    ]})
    assert out["stage"] == "keywords" and out["title"] == "Ключи и группы" and out["intro"] == "Частоты из Вордстата"
    assert out["columns"] == ["Фраза", "Показов в месяц"]
    assert out["rows"] == [
        {"n": 1, "section": "Окна ПВХ", "kind": "keyword", "cells": ["купить окна пвх", "12 000"], "checked": True},
        {"n": 2, "section": "Окна ПВХ", "kind": "negative", "cells": ["-бесплатно", ""], "checked": False},
        {"n": 3, "section": "", "kind": "keyword", "cells": ["окна на дачу", ""], "checked": True},
    ]


def test_items_table_own_columns_are_padded():
    out = ai_agent._q_items(FakeContext(), {"stage": "analysis", "columns": ["Направление"], "rows": [
        {"cells": ["Окна ПВХ", "12 000", "запросы"]},
    ]})
    assert out["columns"] == ["Направление", "", ""]
    assert out["rows"][0]["kind"] == "direction"


def test_items_table_ads_need_kind_and_limits():
    with pytest.raises(YandexError) as err:
        ai_agent._q_items(FakeContext(), {"stage": "ads", "rows": [{"cells": ["Окна за 1 день"]}]})
    assert "title или text" in err.value.message
    with pytest.raises(YandexError) as err:
        ai_agent._q_items(FakeContext(), {"stage": "ads", "rows": [{"kind": "title", "cells": ["О" * 57]}]})
    assert "57 > 56" in err.value.message
    ok = ai_agent._q_items(FakeContext(), {"stage": "ads", "rows": [
        {"section": "Окна ПВХ", "kind": "title", "cells": ["О" * 56]},
        {"section": "Окна ПВХ", "kind": "text", "cells": ["Т" * 81]},
    ]})
    assert [r["kind"] for r in ok["rows"]] == ["title", "text"]


def test_items_table_refuses_bad_input():
    with pytest.raises(YandexError):
        ai_agent._q_items(FakeContext(), {"stage": "launch", "rows": ["x"]})
    with pytest.raises(YandexError):
        ai_agent._q_items(FakeContext(), {"stage": "keywords", "rows": []})
    with pytest.raises(YandexError):
        ai_agent._q_items(FakeContext(), {"stage": "keywords", "rows": [f"фраза {i}" for i in range(ai_agent.MAX_ITEM_ROWS + 1)]})


TABLE = {"stage": "keywords", "title": "Ключи", "intro": "", "columns": ["Фраза", "Показов"], "rows": [
    {"n": 1, "section": "Окна ПВХ", "kind": "keyword", "cells": ["купить окна пвх", "12 000"], "checked": True},
    {"n": 2, "section": "Окна ПВХ", "kind": "keyword", "cells": ["окна дешево", "900"], "checked": True},
    {"n": 3, "section": "Минус-фразы", "kind": "negative", "cells": ["бесплатно", ""], "checked": True},
    {"n": 4, "section": "Двери", "kind": "keyword", "cells": ["двери межкомнатные", "5 000"], "checked": True},
]}


def test_items_approve_chosen_rows(card):
    card.row = _row("propose_items", TABLE, id=12)
    out = ai_agent.decide_items(1, 12, rows=[4, "1", 1, 3, 99, "x"], comment="Добавь «окна в рассрочку»")
    [closed] = card.closed
    assert (closed["status"], closed["error"]) == ("done", "")
    assert closed["result"] == {"approved_rows": [1, 3, 4], "removed_rows": [2], "comment": "Добавь «окна в рассрочку»"}
    reply = closed["reply"]
    assert reply["ok"] is True and reply["stage"] == "Ключи и группы"
    assert reply["approved"] == {
        "Окна ПВХ": ["купить окна пвх"],
        "Минус-фразы": ["минус-фраза: бесплатно"],
        "Двери": ["двери межкомнатные"],
    }
    assert reply["removed"] == {"Окна ПВХ": ["окна дешево"]}
    assert "propose_items" in reply["note"]
    assert card.submitted == ["i12"] and out["action"]["status"] == "done"


def test_items_need_a_row_or_reject(card):
    card.row = _row("propose_items", TABLE, id=12)
    with pytest.raises(YandexError):
        ai_agent.decide_items(1, 12, rows=[])
    assert card.closed == []
    ai_agent.decide_items(1, 12, approve=False, comment="Мало фраз")
    [closed] = card.closed
    assert (closed["status"], closed["result"]) == ("rejected", {"comment": "Мало фраз"})
    assert "не переходи" in closed["reply"]["note"] and closed["reply"]["comment"] == "Мало фраз"


def test_numbers():
    assert ai_agent._numbers([3, "1", 1, 99, "x", None], [1, 2, 3]) == [1, 3]
    assert ai_agent._numbers("1", [1]) == []


def test_items_tool_is_an_ask_card():
    assert ai_agent._kind("propose_items") == "ask"
    assert "propose_items" in ai_agent._ASK_SKIPPED
    assert any(t["function"]["name"] == "propose_items" for t in ai_agent.TOOLS)


# --- Согласованное ------------------------------------------------------------------------------


def test_item_key():
    assert ai_agent._item_key("Купить «Окна» ПВХ!") == "купить окна пвх"
    assert ai_agent._item_key("купить окна -бесплатно -своими", "keyword") == "купить окна"
    assert ai_agent._item_key("-бесплатно", "negative") == "бесплатно"
    assert ai_agent._item_key("Ёлка") == "елка"


def test_require_approved_lists_missing_stages(approvals):
    approvals.stages = {"analysis"}
    with pytest.raises(YandexError) as err:
        ai_agent._require_approved(FakeContext(), {}, stages=["analysis", "keywords", "ads", "settings"])
    assert "Ключи и группы, Заголовки и тексты, Настройки" in err.value.message


def test_require_approved_checks_rows_and_own_words(approvals):
    approvals.stages = {"analysis", "keywords"}
    approvals.rows = ["купить окна пвх", "Окна за 1 день"]
    approvals.said = "Ещё добавь фразу окна в рассрочку"
    ai_agent._require_approved(
        FakeContext(),
        {"keyword": ["купить окна ПВХ -бесплатно", "окна в рассрочку"], "title": ["Окна за 1 день!"]},
        stages=["analysis", "keywords"],
    )
    with pytest.raises(YandexError) as err:
        ai_agent._require_approved(FakeContext(), {"keyword": ["окна дешево"], "text": ["Скидка 50%"]}, stages=[])
    assert "фразы «окна дешево»" in err.value.message and "тексты «Скидка 50%»" in err.value.message


def test_require_approved_outside_chat(monkeypatch):
    monkeypatch.setattr(ai_agent, "_stage_approvals", lambda ctx: pytest.fail("не из чата — не проверяем"))
    ai_agent._require_approved(FakeContext(chat_id=None), {"keyword": ["что угодно"]}, stages=["analysis"])


# --- Новая кампания с группами --------------------------------------------------------------------


def _spec(**over):
    spec = {
        "name": "Окна — поиск",
        "strategy": {"search": "HIGHEST_POSITION"},
        "daily_budget_rub": 500,
        "negative_keywords": ["бесплатно"],
        "groups": [
            {"name": "Окна ПВХ", "keywords": ["купить окна пвх"],
             "ad": {"titles": ["Окна ПВХ от завода", "Окна за 1 день"], "texts": ["Замер бесплатно"], "href": "https://okna.ru"}},
            {"name": "Двери", "keywords": ["двери межкомнатные"],
             "ad": {"titles": ["Двери от завода"], "texts": ["Установка за день"], "href": "https://okna.ru/doors"}},
        ],
    }
    spec.update(over)
    return spec


APPROVED = ["купить окна пвх", "двери межкомнатные", "бесплатно", "Окна ПВХ от завода", "Окна за 1 день",
            "Замер бесплатно", "Двери от завода", "Установка за день"]


def test_create_campaign_needs_stages(approvals):
    approvals.rows = APPROVED
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec()})
    assert "Цели кампании, Анализ" in err.value.message and "propose_items" in err.value.message

    approvals.stages = {"goals", "analysis", "keywords", "ads", "settings"}
    plan = ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec()})
    assert plan.summary == (
        "Создать кампанию «Окна — поиск» в кабинете acc-a (стратегия HIGHEST_POSITION, бюджет 500 ₽/день, "
        "2 группы, 2 фразы, 2 объявления)"
    )


def test_create_campaign_only_approved_rows(approvals):
    approvals.stages = {"goals", "analysis", "keywords", "ads", "settings"}
    approvals.rows = [r for r in APPROVED if r != "Окна за 1 день"]
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec()})
    assert "снял галочку" in err.value.message and "«Окна за 1 день»" in err.value.message


def test_create_campaign_without_groups_needs_no_stages(approvals):
    plan = ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": {"name": "Пустая"}})
    assert plan.summary == "Создать кампанию «Пустая» в кабинете acc-a"


@pytest.mark.parametrize(
    "groups, text",
    [
        ([{"name": "А"}, {"name": "А"}], "называются «А»"),
        ([{"name": "А", "ad": {"titles": ["З"], "texts": ["Т"]}}], "укажи href"),
        ([{"name": "А", "ad": {"titles": ["З"], "texts": ["Т"], "title2": "Ещё", "href": "https://a.ru"}}], "второго заголовка"),
        ([{"name": "А", "ad": {"titles": ["З"], "href": "https://a.ru"}}], "titles и texts"),
        ([{"name": "А", "ad": {"titles": ["З" * 57], "texts": ["Т"], "href": "https://a.ru"}}], "56"),
        ([{"name": "А", "ad": {"titles": ["З"], "texts": ["Т"], "href": "https://a.ru"}},
          {"name": "Б", "ad": {"titles": ["З"], "texts": ["Т"], "href": "https://a.ru"}}], "одинаковые объявления"),
        ([{"name": "А", "keywords": [f"фраза {i}" for i in range(201)]}], "больше 200 фраз"),
    ],
)
def test_create_campaign_refuses_bad_groups(approvals, groups, text):
    approvals.stages = {"goals", "analysis", "keywords", "ads", "settings"}
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec(groups=groups)})
    assert text in err.value.message


def test_create_campaign_href_from_template(approvals):
    approvals.stages = {"goals", "analysis", "ads", "settings"}
    approvals.rows = ["З", "Т"]
    ws = {"url_template": "https://okna.ru/?ref=direct"}
    spec = _spec(negative_keywords=[], groups=[{"name": "А", "ad": {"titles": ["З"], "texts": ["Т"]}}])
    ai_agent._p_create_campaign(FakeContext(ws), {"account_id": 3, "spec": spec})


def test_create_campaign_card_has_row_per_group(monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    monkeypatch.setattr(geo, "region_label", lambda ids, regions: "Россия")
    changes = ai_agent._d_create_campaign(FakeContext(), {"account_id": 3, "spec": _spec()})
    rows = {c["what"]: c["after"] for c in changes}
    assert rows["Группа «Окна ПВХ»"] == "1 фраза · Россия · объявление: 2 заголовка, 1 текст, https://okna.ru"
    assert rows["Группа «Двери»"].startswith("1 фраза · Россия · объявление: 1 заголовок")
    assert rows["Минус-фразы кампании"] == "бесплатно"


def test_execute_create_campaign_passes_groups(monkeypatch):
    from app import actions

    seen = {}
    monkeypatch.setattr(actions, "create_campaign", lambda user_id, **kw: seen.update(kw) or {"status": "ok"})
    args = {"account_id": 3, "spec": _spec()}
    ai_agent._x_create_campaign(FakeContext(), args)
    assert [g["name"] for g in seen["spec"]["groups"]] == ["Окна ПВХ", "Двери"]
    assert "group" not in seen["spec"] and seen["account_id"] == 3 and seen["workspace_id"] == 2
    assert seen["spec"] is not args["spec"]  # аргументы карточки не меняются


def test_normalize_call():
    args = ai_agent._normalize_call("create_campaign", {
        "_diff": [1], "_items": [2], "_removed": [3],
        "spec": {"name": "X", "group": {"name": "А", "keywords": "окна\nдвери\n"}, "negative_keywords": "а\nб"},
    })
    assert args == {"spec": {"name": "X", "groups": [{"name": "А", "keywords": ["окна", "двери"]}], "negative_keywords": ["а", "б"]}}
    assert ai_agent._normalize_call("add_keywords", {"phrases": "а\nб"}) == {"phrases": ["а", "б"]}


# --- Строки с галочками в карточке действия --------------------------------------------------------


def test_picks_for_new_campaign():
    picks = ai_agent._picks(FakeContext(), "create_campaign", {"account_id": 3, "spec": _spec()})
    assert [(p["id"], p["kind"], p["section"]) for p in picks[:5]] == [
        ("spec.groups.0", "group", "Окна ПВХ"),
        ("spec.groups.0.keywords.0", "keyword", "Окна ПВХ"),
        ("spec.groups.0.ad.titles.0", "title", "Окна ПВХ"),
        ("spec.groups.0.ad.titles.1", "title", "Окна ПВХ"),
        ("spec.groups.0.ad.texts.0", "text", "Окна ПВХ"),
    ]
    assert picks[-1] == {"id": "spec.negative_keywords.0", "section": "Минус-фразы кампании", "kind": "negative",
                         "text": "бесплатно", "note": ""}


def test_picks_need_two_rows():
    assert ai_agent._picks(FakeContext(), "add_keywords", {"phrases": ["окна"]}) == []
    assert len(ai_agent._picks(FakeContext(), "add_keywords", {"phrases": ["окна", "двери"]})) == 2
    assert ai_agent._picks(FakeContext(), "moderate_campaign", {"campaign_row_id": 1}) == []
    # Не собрались (например, нет кампаний) — карточка просто без галочек.
    assert ai_agent._picks(FakeContext(), "campaigns_action", {"campaign_row_ids": [1, 2]}) == []


def test_drop_paths():
    args = {"spec": _spec()}
    out = ai_agent._drop_paths(args, [
        "spec.groups.0.ad.titles.1",
        "spec.groups.1",
        "spec.groups.1.keywords.0",  # внутри снятой группы — уходит вместе с ней
        "spec.negative_keywords.0",
    ])
    assert [g["name"] for g in out["spec"]["groups"]] == ["Окна ПВХ"]
    assert out["spec"]["groups"][0]["ad"]["titles"] == ["Окна ПВХ от завода"]
    assert out["spec"]["negative_keywords"] == []
    assert len(args["spec"]["groups"]) == 2  # исходные аргументы не меняются
    assert ai_agent._drop_paths({"phrases": ["а", "б", "в"]}, ["phrases.2", "phrases.0", "phrases.9"]) == {"phrases": ["б"]}
    assert ai_agent._top_paths(["a.1.b", "a.1", "a.10"]) == ["a.1", "a.10"]


def test_action_out_shows_picks_and_removed():
    row = _row("add_keywords", {"phrases": ["а"], "_items": [{"id": "phrases.0"}], "_removed": [{"text": "б"}]})
    out = ai_agent._action_out(row)
    assert out["items"] == [{"id": "phrases.0"}] and out["removed"] == [{"text": "б"}]
    assert out["args"] == {"phrases": ["а"]}


# --- Согласование комплектов объявлений ---------------------------------------------------------


class FakePool:
    """pool.connection() без БД: карточки (ai_actions) и слова маркетолога (ai_messages)."""

    def __init__(self, cards: list[dict], said: list[str] | None = None):
        self.cards, self.said = cards, said or []

    def connection(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        rows = self.cards if "ai_actions" in sql else [{"content": s} for s in self.said]
        return SimpleNamespace(fetchall=lambda: rows)


def _ads_card(rows: list[dict], approved: list[int], stage: str = "ads") -> dict:
    return {"tool": "propose_items", "args": {"stage": stage, "rows": rows}, "result": {"approved_rows": approved}}


COMBO = {
    "n": 1, "kind": "other", "checked": True, "section": "Телефон",
    "cells": [
        "Android/iPhone · A2",
        "HAPP на телефоне от 167 ₽;Скачайте HAPP на Android; Ключ HAPP за минуту",
        "Подписка HAPP от 167 ₽ в месяц.;Android и iPhone: скачайте приложение. Попробуйте 2 дня.",
    ],
}


def test_ads_combo_row_approves_every_title_and_text(monkeypatch):
    """Комплект «название / заголовки через ; / описания» согласован целиком, а не только по названию."""
    monkeypatch.setattr(ai_agent, "pool", FakePool([_ads_card([COMBO], [1])]))
    ctx = SimpleNamespace(chat_id=5, board_ops=[])
    stages, approved, _ = ai_agent._stage_approvals(ctx)
    assert stages == {"ads"}
    ai_agent._require_approved(ctx, {
        "title": ["Скачайте HAPP на Android", "Ключ HAPP за минуту", "HAPP на телефоне от 167 ₽"],
        "text": ["Подписка HAPP от 167 ₽ в месяц.", "Android и iPhone: скачайте приложение. Попробуйте 2 дня."],
    }, stages=[])
    with pytest.raises(YandexError) as err:
        ai_agent._require_approved(ctx, {"title": ["Скачайте HAPP на iPhone"], "text": ["Скидка 50%"]}, stages=[])
    assert "Скачайте HAPP на iPhone" in err.value.message and "Скидка 50%" in err.value.message
    assert ai_agent._item_key("Android/iPhone · A2") in approved


def test_ads_row_not_checked_is_not_approved(monkeypatch):
    monkeypatch.setattr(ai_agent, "pool", FakePool([_ads_card([COMBO], [])]))
    ctx = SimpleNamespace(chat_id=5, board_ops=[])
    with pytest.raises(YandexError):
        ai_agent._require_approved(ctx, {"title": ["Ключ HAPP за минуту"]}, stages=[])


def test_keywords_table_still_approves_first_cell_only(monkeypatch):
    """Другие этапы по-прежнему согласуют только первую ячейку: частота и группа не делают фразу согласованной."""
    row = {"n": 1, "kind": "keyword", "checked": True, "section": "Окна",
           "cells": ["купить окна", "12 000", "окна пвх дёшево"]}
    monkeypatch.setattr(ai_agent, "pool", FakePool([_ads_card([row], [1], stage="keywords")]))
    ctx = SimpleNamespace(chat_id=5, board_ops=[])
    ai_agent._require_approved(ctx, {"keyword": ["Купить окна"]}, stages=[])
    with pytest.raises(YandexError):
        ai_agent._require_approved(ctx, {"keyword": ["окна пвх дёшево"]}, stages=[])


def test_ads_glued_texts_get_a_hint():
    with pytest.raises(YandexError) as err:
        ai_agent._q_items(FakeContext(), {"stage": "ads", "rows": [
            {"kind": "text", "section": "Телефон", "cells": ["Первый текст объявления про HAPP | " + "Т" * 60 + " | третий"]},
        ]})
    assert "> 81" in err.value.message and "одна строка — один заголовок или текст" in err.value.message


# --- Стратегия и ценность целей проверяются до карточки ----------------------------------------


@pytest.mark.parametrize("strategy,text", [
    ({"search": True, "network": True}, "строкой"),
    ({"search": "ЛУЧШАЯ"}, "PAY_FOR_CONVERSION_MULTIPLE_GOALS"),
    ({"search": "WB_MAXIMUM_CLICKS"}, "недельный бюджет"),
])
def test_create_campaign_plan_checks_strategy(approvals, strategy, text):
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": {"name": "Новая", "strategy": strategy}})
    assert text.lower() in err.value.message.lower()


def test_create_campaign_plan_checks_goal_value(approvals):
    spec = {"name": "Новая", "strategy": {"search": "HIGHEST_POSITION"}, "priority_goals": [{"goal_id": 599}]}
    with pytest.raises(YandexError) as err:
        ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": spec})
    assert "value_rub" in err.value.message
    spec["priority_goals"] = [{"goal_id": 599, "value_rub": 0.3}]
    assert ai_agent._p_create_campaign(FakeContext(), {"account_id": 3, "spec": spec}).summary


# --- История запроса и след ошибки -----------------------------------------------------------------


class HistoryPool(FakePool):
    """pool.connection() для _history: сообщения чата (новые первыми, как в запросе), правок доски нет."""

    def __init__(self, rows: list[dict]):
        super().__init__([])
        self.rows = rows

    def execute(self, sql, params=None):
        rows = self.rows if "ai_messages" in sql else []
        return SimpleNamespace(fetchall=lambda: list(rows))


def _msg(id, role, content="", calls=None, call_id="", name=""):
    return {"id": id, "role": role, "content": content, "tool_calls": calls, "tool_call_id": call_id, "tool_name": name}


def test_history_shortens_read_results_of_earlier_turns(monkeypatch):
    big = "x" * 5000
    call = lambda call_id, name: {"id": call_id, "name": name, "arguments": "{}"}
    rows = [
        _msg(1, "user", "Проверь кампанию"),
        _msg(2, "assistant", "", [call("a", "get_campaign"), call("b", "propose_items")]),
        _msg(3, "tool", big, call_id="a", name="get_campaign"),
        _msg(4, "tool", big, call_id="b", name="propose_items"),
        _msg(5, "error", "ИИ не ответил за отведённое время (запрос ≈ 90000 токенов, сообщений: 4)"),
        _msg(6, "user", "Продолжай"),
        _msg(7, "assistant", "", [call("c", "get_campaign")]),
        _msg(8, "tool", big, call_id="c", name="get_campaign"),
    ]
    monkeypatch.setattr(ai_agent, "pool", HistoryPool(rows[::-1]))
    history = ai_agent._history(1)
    tools = {m["tool_call_id"]: m["content"] for m in history if m["role"] == "tool"}
    assert tools["a"].startswith("x" * ai_agent.OLD_RESULT_CHARS + "…") and "было 5000 символов" in tools["a"]
    assert tools["b"] == big  # согласования и ответы на вопросы не сокращаются
    assert tools["c"] == big  # ход в работе — целиком
    # Строка ошибки — след для людей, модели её не показываем.
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "tool", "user", "assistant", "tool"]


def test_end_with_error_keeps_it_in_the_feed(monkeypatch):
    seen = []
    monkeypatch.setattr(ai_agent, "_set_status", lambda chat_id, status, error="": seen.append(("status", status, error)))
    monkeypatch.setattr(ai_agent, "pool", FakePool([]))
    monkeypatch.setattr(ai_agent, "_insert_message", lambda conn, chat_id, role, content="", **kw: seen.append((role, content)))
    assert ai_agent._end_with_error(5, "ИИ не ответил за отведённое время", (95000, 40)) == {"status": "error"}
    assert ai_agent._end_with_error(5, "ИИ не подключён") == {"status": "error"}
    assert seen == [
        ("status", "error", "ИИ не ответил за отведённое время"),
        ("error", "ИИ не ответил за отведённое время (запрос ≈ 95000 токенов, сообщений: 40)"),
        ("status", "error", "ИИ не подключён"),
        ("error", "ИИ не подключён"),
    ]


def test_model_call_timeout_comes_from_config(monkeypatch):
    import httpx

    from app import ai

    seen = []

    def post(url, **kw):
        seen.append(kw["timeout"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "готов"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(ai.httpx, "post", post)
    cfg = {"base_url": "https://ai.example", "model": "m", "headers": {}, "check_host": False, "limit_rub": None, "user_id": None}
    ai.complete(cfg, [{"role": "user", "content": "привет"}])
    ai.complete({**cfg, "timeout": ai_agent.CHAT_TIMEOUT_SECONDS}, [{"role": "user", "content": "привет"}])
    assert seen == [ai.TIMEOUT_SECONDS, ai_agent.CHAT_TIMEOUT_SECONDS] and seen[1] > seen[0]
    assert ai.estimate_tokens([{"role": "user", "content": "я" * 250}]) > 100
