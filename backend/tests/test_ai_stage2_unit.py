"""ИИ, второй этап, без БД и модели: вопросы-бриф, план, «было → станет», копия на новое гео."""

from types import SimpleNamespace

import pytest

from app import actions, ai_agent, geo
from app.workspaces import normalize_ai_access
from app.yandex import YandexError

CAMPAIGNS = {
    7: {"id": 7, "name": "Поиск Москва", "state": "ON", "status": "ACCEPTED", "account_id": 3,
        "daily_budget_kopecks": 150000},
    8: {"id": 8, "name": "РСЯ Москва", "state": "SUSPENDED", "status": "ACCEPTED", "account_id": 3,
        "daily_budget_kopecks": None},
}
ACCOUNT = {"id": 4, "client_login": "okna-spb"}

REGIONS = {
    10000: {"id": 10000, "name": "Земля", "type": "World", "parent": None},
    10001: {"id": 10001, "name": "Евразия", "type": "Continent", "parent": 10000},
    225: {"id": 225, "name": "Россия", "type": "Country", "parent": 10001},
    3: {"id": 3, "name": "Центральный федеральный округ", "type": "Region", "parent": 225},
    1: {"id": 1, "name": "Москва и Московская область", "type": "Administrative area", "parent": 3},
    213: {"id": 213, "name": "Москва", "type": "City", "parent": 1},
    20279: {"id": 20279, "name": "Новая Москва", "type": "City district", "parent": 213},
    20728: {"id": 20728, "name": "Королёв", "type": "City", "parent": 1},
    40: {"id": 40, "name": "Приволжский федеральный округ", "type": "Region", "parent": 225},
    11119: {"id": 11119, "name": "Республика Татарстан", "type": "Administrative area", "parent": 40},
    43: {"id": 43, "name": "Казань", "type": "City", "parent": 11119},
}

KNOWN = {"key": "success_criteria", "question": "Что считаем успехом?", "answer": "Заказы при 0 ₽"}

QUESTIONS = [
    {"n": 1, "key": "success_criteria", "question": "Что считаем успехом?",
     "options": ["Конверсии при 0 ₽", "Высокий CR"], "multiple": True, "remember": True},
    {"n": 2, "key": "test_budget", "question": "Лимит на тест?",
     "options": ["5 000 ₽", "10 000 ₽"], "multiple": False, "remember": False},
    {"n": 3, "key": "", "question": "Какой регион?", "options": [], "multiple": False, "remember": False},
    {"n": 4, "key": "deadline", "question": "Срок?", "options": ["Неделя"], "multiple": False, "remember": True},
]

PLAN = {
    "goal": "Вернуть заявки по 0 ₽",
    "steps": [
        {"n": 1, "title": "Копия кампании", "before": "CR 1%", "after": "CR 3%", "why": "схема затухла"},
        {"n": 2, "title": "Минус-фразы", "before": "", "after": "", "why": ""},
        {"n": 3, "title": "Регионы", "before": "Москва", "after": "Москва и Казань", "why": ""},
    ],
}


class FakeContext:
    """Context без БД: пространство из словаря, кампании — из CAMPAIGNS."""

    require_account = ai_agent.Context.require_account

    def __init__(self, ws: dict | None = None):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "accounts": [], **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))

    def campaigns(self, ids: list[int]) -> list[dict]:
        foreign = [i for i in ids if i not in CAMPAIGNS]
        if foreign:
            raise YandexError("scope", f"Кампании {foreign} не входят в это пространство")
        return [CAMPAIGNS[i] for i in ids]

    def campaign(self, campaign_row_id: int) -> dict:
        return self.campaigns([campaign_row_id])[0]


def _row(tool: str, args: dict, **extra) -> dict:
    return {"id": 11, "chat_id": 5, "message_id": 6, "tool_call_id": "call_1", "tool": tool, "workspace_id": 2,
            "args": args, "summary": "", "category": "", "status": "pending", "auto": False, **extra}


@pytest.fixture
def regions(monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)


@pytest.fixture
def no_memory(monkeypatch):
    monkeypatch.setattr(ai_agent, "memory_examples", lambda ws_id, limit: {})
    monkeypatch.setattr(ai_agent, "_image_gen_ready", lambda ctx: False)
    monkeypatch.setattr(ai_agent, "_running_experiments", lambda ctx: [])


@pytest.fixture
def direct(monkeypatch, regions):
    """Копия, регионы и модерация без Директа; calls — что ушло бы в Директ."""
    fake = SimpleNamespace(
        calls=[],
        copy_status="ok",
        regions={"groups": 2, "updated": 2, "errors": []},
        moderation={"drafts": 3, "sent": 3, "errors": [], "status": "ok"},
    )

    def duplicate(user_id, row_id, *, target_account_id=None, name=None, workspace_id=None, **kw):
        fake.calls.append(("copy", row_id, target_account_id, name, workspace_id))
        return {"campaign": {"id": 70, "name": name}, "groups": 2, "keywords": 5, "autotargeting": 0, "ads": 3,
                "warnings": [], "status": fake.copy_status}

    def set_regions(user_id, row_id, region_ids, *, workspace_id=None):
        fake.calls.append(("regions", row_id, region_ids, workspace_id))
        if isinstance(fake.regions, Exception):
            raise fake.regions
        return fake.regions

    def moderate(user_id, row_id):
        fake.calls.append(("moderate", row_id))
        if isinstance(fake.moderation, Exception):
            raise fake.moderation
        return fake.moderation

    monkeypatch.setattr(actions, "duplicate_campaign", duplicate)
    monkeypatch.setattr(actions, "moderate_campaign", moderate)
    monkeypatch.setattr(geo, "set_campaign_regions", set_regions)
    return fake


@pytest.fixture
def card(monkeypatch):
    """Карточка вопросов или плана без БД: closed — как её закрыли бы, submitted — запущенные ходы ИИ."""
    fake = SimpleNamespace(row=None, left=0, closed=[], remembered=[], submitted=[], remember_error=None)

    def pending(user_id, action_id, tool):
        assert (action_id, tool) == (fake.row["id"], fake.row["tool"])
        return fake.row

    def close(user_id, row, *, status, result, error, reply):
        fake.closed.append({"status": status, "result": result, "error": error, "reply": reply})
        return {**row, "status": status, "result": result, "error": error}, fake.left

    def remember(user_id, workspace_id, items):
        if fake.remember_error:
            raise fake.remember_error
        fake.remembered.append((workspace_id, items))

    def submit(user_id, chat_id, marker):
        fake.submitted.append(marker)

    monkeypatch.setattr(ai_agent, "_pending_interactive", pending)
    monkeypatch.setattr(ai_agent, "_close_interactive", close)
    monkeypatch.setattr(ai_agent, "remember_ai_facts", remember)
    monkeypatch.setattr(ai_agent, "_submit", submit)
    return fake


# --- Вопросы ----------------------------------------------------------------------------------


def test_questions_skip_known_keys_and_clean_options():
    ctx = FakeContext({"ai_facts": [KNOWN]})
    out = ai_agent._q_questions(ctx, {
        "understanding": "  Нужно  перезапустить кампанию.\n\n\n Схема 0 ₽ затухла. ",
        "questions": [
            {"key": "Success Criteria", "question": "Что для вас успех?", "options": ["Заказы", "CR"]},
            {"key": "test_budget", "question": "  Лимит   на тест? ", "multiple": True,
             "options": ["5 000 ₽", "5 000 ₽", {"label": "10 000 ₽"}, "", "x" * 300]},
            {"key": "test_budget", "question": "Ещё раз про лимит?", "options": "Да\nНет", "multiple": "да"},
            "Какой регион?",
            {"question": "  "},
            42,
        ],
    })
    assert out == {
        "understanding": "Нужно перезапустить кампанию.\n\nСхема 0 ₽ затухла.",
        "questions": [
            {"n": 1, "key": "test_budget", "question": "Лимит на тест?", "options": ["5 000 ₽", "10 000 ₽", "x" * 200],
             "multiple": True, "remember": True},
            # тот же key второй раз — вопрос задаём, но не запоминаем
            {"n": 2, "key": "", "question": "Ещё раз про лимит?", "options": ["Да", "Нет"],
             "multiple": True, "remember": False},
            {"n": 3, "key": "", "question": "Какой регион?", "options": [], "multiple": False, "remember": False},
        ],
        "answered": [KNOWN],
    }


def test_questions_flags():
    out = ai_agent._q_questions(FakeContext(), {"questions": [
        {"key": "region", "question": "Регион?", "options": ["Москва"], "multiple": True, "remember": False},
        {"question": "Срок?", "options": [f"вариант {i}" for i in range(12)]},
    ]})
    first, second = out["questions"]
    assert (first["multiple"], first["remember"], first["key"]) == (False, False, "region")  # один вариант — не «несколько»
    assert len(second["options"]) == ai_agent.MAX_OPTIONS


def test_questions_limits():
    many = [{"question": f"Вопрос {i}?"} for i in range(ai_agent.MAX_QUESTIONS + 1)]
    with pytest.raises(YandexError) as err:
        ai_agent._q_questions(FakeContext(), {"questions": many})
    assert err.value.code == "args"

    # уже отвеченные в лимит не входят
    known = FakeContext({"ai_facts": [KNOWN]})
    many[0] = {"key": "success_criteria", "question": "Успех?"}
    assert len(ai_agent._q_questions(known, {"questions": many})["questions"]) == ai_agent.MAX_QUESTIONS

    for empty in ([], [{"question": " "}], None, "  "):
        with pytest.raises(YandexError):
            ai_agent._q_questions(FakeContext(), {"questions": empty})

    assert ai_agent._q_questions(FakeContext(), {"questions": "Бюджет?"})["questions"][0]["question"] == "Бюджет?"


def test_questions_all_known_gives_no_card():
    out = ai_agent._q_questions(FakeContext({"ai_facts": [KNOWN]}), {"questions": [{"key": "success_criteria", "question": "?"}]})
    assert out["questions"] == []
    assert out["answered"] == [KNOWN]


def test_compose_answers():
    out, facts = ai_agent._compose_answers(QUESTIONS, [
        {"n": 1, "selected": ["Высокий CR", "Чужой вариант", "Конверсии при 0 ₽", "Высокий CR"], "text": " и  заявки "},
        {"n": 2, "selected": ["10 000 ₽", "5 000 ₽"]},
        {"n": 3, "text": "Казань"},
        {"n": 4, "selected": [], "text": "  "},
        {"n": 1, "selected": ["Конверсии при 0 ₽"]},  # повтор номера — берём первый ответ
        "мусор",
    ])
    assert out == [
        {"question": "Что считаем успехом?", "answer": "Высокий CR; Конверсии при 0 ₽; и заявки"},
        {"question": "Лимит на тест?", "answer": "10 000 ₽"},  # один вариант, если нельзя несколько
        {"question": "Какой регион?", "answer": "Казань"},
        {"question": "Срок?", "skipped": True},
    ]
    # Лимит без remember и регион без key не запоминаются.
    assert facts == [{"key": "success_criteria", "question": "Что считаем успехом?",
                      "answer": "Высокий CR; Конверсии при 0 ₽; и заявки"}]


def test_compose_answers_by_position_and_garbage():
    question = {**QUESTIONS[3], "n": 1, "options": ["Неделя", "Месяц"]}
    out, facts = ai_agent._compose_answers([question], [{"selected": "Месяц"}])
    assert out == [{"question": "Срок?", "answer": "Месяц"}]
    assert facts == [{"key": "deadline", "question": "Срок?", "answer": "Месяц"}]

    out, facts = ai_agent._compose_answers(QUESTIONS, None)
    assert all(a.get("skipped") for a in out) and facts == []


def test_answer_questions_remembers_keyed_answers(card):
    card.row = _row("ask_questions", {"understanding": "Понял так", "questions": QUESTIONS, "answered": [KNOWN]})
    out = ai_agent.answer_questions(
        1, 11, answers=[{"n": 1, "selected": ["Высокий CR"]}, {"n": 3, "text": "Казань"}], comment=" Срочно "
    )
    [closed] = card.closed
    assert (closed["status"], closed["error"]) == ("done", "")
    assert closed["result"] == {
        "answers": [
            {"question": "Что считаем успехом?", "answer": "Высокий CR"},
            {"question": "Лимит на тест?", "skipped": True},
            {"question": "Какой регион?", "answer": "Казань"},
            {"question": "Срок?", "skipped": True},
        ],
        "remembered": ["success_criteria"],
        "comment": "Срочно",
    }
    assert closed["reply"] == {"ok": True, **closed["result"], "already_answered": [KNOWN]}
    assert card.remembered == [(2, [{"key": "success_criteria", "question": "Что считаем успехом?", "answer": "Высокий CR"}])]
    assert card.submitted == ["q11"]
    assert out["action"]["status"] == "done"
    assert out["action"]["kind"] == "ask"
    assert out["pending"] == 0


def test_answer_questions_needs_an_answer_or_comment(card):
    card.row = _row("ask_questions", {"questions": QUESTIONS})
    with pytest.raises(YandexError) as err:
        ai_agent.answer_questions(1, 11, answers=[{"n": 1, "selected": ["Чужой вариант"]}, {"n": 3, "text": " "}])
    assert err.value.code == "args"
    assert card.closed == [] and card.submitted == []

    ai_agent.answer_questions(1, 11, answers=[], comment="Сначала посмотри статистику")
    assert card.closed[0]["status"] == "done"
    assert card.closed[0]["result"]["comment"] == "Сначала посмотри статистику"
    assert card.remembered == []


def test_answer_questions_survives_memory_failure(card):
    card.row = _row("ask_questions", {"questions": QUESTIONS})
    card.remember_error = RuntimeError("БД недоступна")
    ai_agent.answer_questions(1, 11, answers=[{"n": 1, "selected": ["Высокий CR"]}])
    assert card.closed[0]["status"] == "done"
    assert card.closed[0]["result"]["remembered"] == []  # не сохранилось — ИИ спросит ещё раз


def test_skip_questions(card):
    card.row = _row("ask_questions", {"questions": QUESTIONS})
    ai_agent.answer_questions(1, 11, skip=True)
    assert card.closed == [{
        "status": "rejected",
        "result": None,
        "error": "Пропущено",
        "reply": {"ok": False, "error": "Пользователь не стал отвечать — работай с тем, что есть, или спроси иначе"},
    }]
    ai_agent.answer_questions(1, 11, skip=True, comment=" Позже ")
    assert card.closed[-1]["result"] == {"comment": "Позже"}
    assert card.closed[-1]["reply"]["comment"] == "Позже"
    assert card.remembered == []
    assert card.submitted == ["q11", "q11"]


def test_answer_waits_for_other_open_cards(card):
    card.row = _row("ask_questions", {"questions": QUESTIONS})
    card.left = 1
    out = ai_agent.answer_questions(1, 11, answers=[{"n": 3, "text": "Казань"}])
    assert card.submitted == []  # ИИ продолжит, когда решат и остальные карточки
    assert out["pending"] == 1 and out["job"] is None


# --- План -------------------------------------------------------------------------------------


def test_plan_steps():
    out = ai_agent._q_plan(FakeContext(), {"goal": " Вернуть  заявки по 0 ₽ ", "steps": [
        {"title": "Копия кампании", "before": "CR 1%", "after": "CR 3%", "why": "схема затухла"},
        "Минус-фразы",
        {"step": "Регионы"},
        {"title": ""},
        None,
    ]})
    assert out == {"goal": "Вернуть заявки по 0 ₽", "steps": [
        {"n": 1, "title": "Копия кампании", "before": "CR 1%", "after": "CR 3%", "why": "схема затухла"},
        {"n": 2, "title": "Минус-фразы", "before": "", "after": "", "why": ""},
        {"n": 3, "title": "Регионы", "before": "", "after": "", "why": ""},
    ]}


def test_plan_limits():
    for empty in ([], [{"title": " "}], None):
        with pytest.raises(YandexError):
            ai_agent._q_plan(FakeContext(), {"steps": empty})
    with pytest.raises(YandexError):
        ai_agent._q_plan(FakeContext(), {"steps": [f"Шаг {i}" for i in range(ai_agent.MAX_PLAN_STEPS + 1)]})
    assert len(ai_agent._q_plan(FakeContext(), {"steps": "Один шаг"})["steps"]) == 1


def test_plan_approves_chosen_steps(card):
    card.row = _row("propose_plan", PLAN, id=12)
    out = ai_agent.decide_plan(1, 12, steps=[3, "1", 1, 99, "x"], comment="Без минус-фраз")
    [closed] = card.closed
    assert (closed["status"], closed["error"]) == ("done", "")
    assert closed["result"] == {"approved_steps": [1, 3], "declined_steps": [2], "comment": "Без минус-фраз"}
    assert closed["reply"]["ok"] is True
    assert closed["reply"]["approved"] == [{"n": 1, "title": "Копия кампании"}, {"n": 3, "title": "Регионы"}]
    assert "plan_step" in closed["reply"]["note"]
    assert card.submitted == ["p12"]
    assert out["action"]["status"] == "done"


def test_plan_needs_a_step_to_approve(card):
    card.row = _row("propose_plan", PLAN, id=12)
    with pytest.raises(YandexError) as err:
        ai_agent.decide_plan(1, 12, steps=[99])
    assert err.value.code == "args"
    assert card.closed == []


def test_plan_reject(card):
    card.row = _row("propose_plan", PLAN, id=12)
    ai_agent.decide_plan(1, 12, approve=False, steps=[1])
    [closed] = card.closed
    assert (closed["status"], closed["result"], closed["error"]) == ("rejected", None, "Пользователь отклонил план")
    assert closed["reply"]["ok"] is False
    assert "новый план" in closed["reply"]["note"]

    ai_agent.decide_plan(1, 12, approve=False, comment="Сначала регионы")
    assert card.closed[-1]["result"] == {"comment": "Сначала регионы"}
    assert card.closed[-1]["reply"]["comment"] == "Сначала регионы"


# --- Карточки в чате --------------------------------------------------------------------------


def test_kind():
    tools = ("ask_questions", "propose_plan", "copy_campaign_geo", "campaigns_action", "find_regions", "нет такого")
    assert [ai_agent._kind(t) for t in tools] == ["ask", "ask", "write", "write", "read", "read"]


def test_message_out_marks_call_kind():
    row = {"id": 1, "role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "name": "ask_questions", "arguments": '{"questions": []}'},
        {"id": "c2", "name": "copy_campaign_geo", "arguments": "{}"},
        {"id": "c3", "name": "find_regions", "arguments": '{"query": "Казань"}'},
    ]}
    calls = ai_agent._message_out(row)["tool_calls"]
    assert [(c["kind"], c["write"]) for c in calls] == [("ask", False), ("write", True), ("read", False)]


def test_action_out_moves_diff_to_changes():
    diff = [{"what": "«Поиск Москва»", "before": "работает", "after": "на паузе"}]
    row = _row("campaigns_action", {"action": "pause", "campaign_row_ids": [7], "reason": "Дорого", "plan_step": "2",
                                    "_diff": diff}, category="pause_resume")
    out = ai_agent._action_out(row)
    assert out["changes"] == diff
    assert "_diff" not in out["args"]
    assert "_diff" in row["args"]  # строку из БД не меняем
    assert (out["kind"], out["plan_step"], out["reason"]) == ("write", 2, "Дорого")

    assert ai_agent._action_out(_row("campaigns_action", {"plan_step": "второй"}))["plan_step"] is None
    ask = ai_agent._action_out(_row("ask_questions", {"questions": QUESTIONS}))
    assert (ask["kind"], ask["changes"], ask["plan_step"]) == ("ask", [], None)


def test_write_tools_accept_plan_step():
    for tool in ai_agent.TOOLS:
        fn = tool["function"]
        if fn["name"] in ai_agent.WRITE_TOOLS:
            assert {"reason", "plan_step"} <= set(fn["parameters"]["properties"]), fn["name"]


def test_every_write_tool_has_changes_builder():
    assert set(ai_agent._DIFFS) == set(ai_agent.WRITE_TOOLS)


def test_prompt_covers_brief_plan_and_new_geo(no_memory):
    prompt = ai_agent._system_prompt(FakeContext())
    for name in ("ask_questions", "propose_plan", "plan_step", "copy_campaign_geo", "find_regions"):
        assert name in prompt, name


# --- «Было → станет» --------------------------------------------------------------------------


def test_state_label():
    assert ai_agent._state_label("ON", "DRAFT") == "черновик"  # статус модерации важнее состояния
    assert ai_agent._state_label("SUSPENDED", "ACCEPTED") == "на паузе"
    assert ai_agent._state_label(None) == "нет данных"
    assert ai_agent._state_label("WEIRD") == "WEIRD"


def test_changes_pause_and_resume():
    ctx = FakeContext()
    assert ai_agent._changes(ctx, "campaigns_action", {"action": "pause", "campaign_row_ids": [7, 8]}) == [
        {"what": "«Поиск Москва»", "before": "работает", "after": "на паузе"},
        {"what": "«РСЯ Москва»", "before": "на паузе", "after": "на паузе"},
    ]
    assert ai_agent._changes(ctx, "campaigns_action", {"action": "resume", "campaign_row_ids": [8]}) == [
        {"what": "«РСЯ Москва»", "before": "на паузе", "after": "работает"},
    ]


def test_changes_budget():
    ctx = FakeContext()
    fixed = ai_agent._changes(ctx, "campaigns_action", {"action": "set_budget", "campaign_row_ids": [7, 8], "daily_budget_rub": "2000"})
    assert fixed == [
        {"what": "«Поиск Москва»: дневной бюджет", "before": "1 500 ₽", "after": "2 000 ₽"},
        {"what": "«РСЯ Москва»: дневной бюджет", "before": "не задан", "after": "2 000 ₽"},
    ]
    pct = ai_agent._changes(ctx, "campaigns_action", {"action": "set_budget", "campaign_row_ids": [7, 8], "budget_change_pct": -20})
    assert [r["after"] for r in pct] == ["1 200 ₽", "не задан — укажите сумму"]
    zero = ai_agent._changes(ctx, "campaigns_action", {"action": "set_budget", "campaign_row_ids": [7], "budget_change_pct": -100})
    assert zero[0]["after"] == "не выйдет: неверная сумма бюджета"


def test_changes_never_block_the_action():
    ctx = FakeContext()
    assert ai_agent._changes(ctx, "campaigns_action", {"action": "pause", "campaign_row_ids": [99]}) == []
    assert ai_agent._changes(ctx, "get_analytics", {}) == []


def test_changes_are_trimmed(monkeypatch):
    rows = [{"what": f"Строка\n{i}", "before": i, "after": None} for i in range(ai_agent.MAX_CHANGES + 5)]
    monkeypatch.setitem(ai_agent._DIFFS, "fake_tool", lambda ctx, args: rows)
    out = ai_agent._changes(FakeContext(), "fake_tool", {})
    assert len(out) == ai_agent.MAX_CHANGES
    assert out[0] == {"what": "Строка 0", "before": "0", "after": "—"}
    assert out[-1] == {"what": "и ещё 6", "before": "", "after": ""}


# --- Регионы ----------------------------------------------------------------------------------


def test_search_regions_ranking(regions):
    out = geo.search_regions(1, " МОСКВА ")
    assert out == {
        "regions": [
            {"id": 213, "name": "Москва", "type": "город",
             "path": "Россия › Центральный федеральный округ › Москва и Московская область"},
            {"id": 1, "name": "Москва и Московская область", "type": "область",
             "path": "Россия › Центральный федеральный округ"},
            {"id": 20279, "name": "Новая Москва", "type": "район города",
             "path": "Россия › Центральный федеральный округ › Москва и Московская область › Москва"},
        ],
        "hint": geo.HINT,
    }
    assert [r["id"] for r in geo.search_regions(1, "королев")["regions"]] == [20728]  # ё = е
    assert len(geo.search_regions(1, "а", limit=2)["regions"]) == 2
    nothing = geo.search_regions(1, "Тмутаракань")
    assert nothing["regions"] == [] and nothing["note"].startswith("Ничего не нашлось")
    with pytest.raises(YandexError):
        geo.search_regions(1, "  ")


def test_search_regions_without_directory(monkeypatch):
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    out = geo.search_regions(1, "казань")
    assert out["regions"] == [{"id": 43, "name": "Казань", "type": "", "path": ""}]
    assert out["note"].startswith("Справочник регионов Директа недоступен")


def test_parse_region_ids():
    assert geo.parse_region_ids([43, "43", -1, 0, "x", None, "213"]) == [43, -1, 213]
    assert geo.parse_region_ids("43") == []


def test_region_label():
    assert geo.region_label([213, 2], REGIONS) == "Москва, Санкт-Петербург"  # нет в справочнике — подпись из KNOWN_REGIONS
    assert geo.region_label([225, -1], REGIONS) == "Россия кроме Москва и Московская область"
    assert geo.region_label([-213]) == "кроме Москва"
    assert geo.region_label([1, 2, 213, 43], limit=2) == "Москва и Московская область, Санкт-Петербург и ещё 2"
    assert geo.region_label([999999]) == "999999"
    assert geo.region_label([]) == "—"


def test_unknown_regions(monkeypatch, regions):
    assert geo.unknown_regions(1, [43, -213, 999, -998]) == [999, -998]
    monkeypatch.setattr(geo, "load_regions", lambda user_id: {})
    assert geo.unknown_regions(1, [999]) == []  # справочника нет — проверить нечем


# --- Копия на новое гео -----------------------------------------------------------------------


def test_copy_geo_plan(regions):
    plan = ai_agent._p_copy_geo(FakeContext(), {"campaign_row_id": 7, "region_ids": [43]})
    assert plan.summary == (
        "Копия «Поиск Москва» на новое гео: копия «Поиск Москва — Казань» → Казань → на модерацию; оригинал работает"
    )
    assert plan.categories == ["campaigns"]

    other = ai_agent._p_copy_geo(
        FakeContext({"accounts": [ACCOUNT]}),
        {"campaign_row_id": 7, "region_ids": [11119, -43], "target_account_id": 4, "moderate": False,
         "name": "  Поиск  Татарстан "},
    )
    assert other.summary == (
        "Копия «Поиск Москва» на новое гео: копия «Поиск Татарстан» в кабинет okna-spb"
        " → Республика Татарстан кроме Казань; оригинал работает"
    )


def test_copy_geo_default_name_is_short(regions):
    plan = ai_agent._p_copy_geo(FakeContext(), {"campaign_row_id": 7, "region_ids": [43, 213, 20728]})
    assert "копия «Поиск Москва — Казань, Москва и ещё 1»" in plan.summary


@pytest.mark.parametrize(
    "args, code, text",
    [
        ({"campaign_row_id": 7, "region_ids": [-43]}, "args", "хотя бы один регион"),
        ({"campaign_row_id": 7, "region_ids": []}, "args", "хотя бы один регион"),
        ({"campaign_row_id": 7, "region_ids": [43, 999]}, "args", "[999]"),
        ({"campaign_row_id": 9, "region_ids": [43]}, "scope", "[9]"),
        ({"campaign_row_id": 7, "region_ids": [43], "target_account_id": 5}, "scope", "Кабинет"),
    ],
)
def test_copy_geo_refuses_bad_args(direct, args, code, text):
    with pytest.raises(YandexError) as err:
        ai_agent._x_copy_geo(FakeContext({"accounts": [ACCOUNT]}), args)
    assert err.value.code == code
    assert text in err.value.message
    assert direct.calls == []


def test_copy_geo_sets_regions_then_moderates(direct):
    out = ai_agent._x_copy_geo(FakeContext(), {"campaign_row_id": 7, "region_ids": ["43"]})
    assert direct.calls == [
        ("copy", 7, None, "Поиск Москва — Казань", 2),
        ("regions", 70, [43], 2),
        ("moderate", 70),
    ]
    assert out["copy"] == {"campaign_row_id": 70, "name": "Поиск Москва — Казань", "status": "ok",
                           "groups": 2, "keywords": 5, "ads": 3}
    assert out["regions"] == {"groups": 2, "updated": 2, "errors": []}
    assert out["moderation"]["sent"] == 3
    assert "note" not in out


def test_copy_geo_to_other_account(direct):
    ai_agent._x_copy_geo(
        FakeContext({"accounts": [ACCOUNT]}),
        {"campaign_row_id": 7, "region_ids": [43], "target_account_id": "4", "name": "Казань"},
    )
    assert direct.calls[0] == ("copy", 7, 4, "Казань", 2)


@pytest.mark.parametrize(
    "regions_result, note",
    [
        (YandexError("empty", "В кампании нет групп — регионы задаются в группах"), "Регионы не заменены"),
        ({"groups": 2, "updated": 0, "errors": []}, "Регионы не заменены"),
        ({"groups": 2, "updated": 1, "errors": ["группа 5: неверный регион"]}, "Регионы заменены не во всех группах"),
    ],
)
def test_copy_geo_without_new_regions_stays_draft(direct, regions_result, note):
    """С регионами оригинала копия конкурировала бы с ним за те же показы — на модерацию её не отправляем."""
    direct.regions = regions_result
    out = ai_agent._x_copy_geo(FakeContext(), {"campaign_row_id": 7, "region_ids": [43]})
    assert [call[0] for call in direct.calls] == ["copy", "regions"]
    assert out["note"].startswith(note)
    assert "moderation" not in out
    if isinstance(regions_result, YandexError):
        assert out["regions"] == {"status": "error", "error": "В кампании нет групп — регионы задаются в группах"}


def test_copy_geo_partial_copy_is_not_moderated(direct):
    direct.copy_status = "partial"
    out = ai_agent._x_copy_geo(FakeContext(), {"campaign_row_id": 7, "region_ids": [43]})
    assert [call[0] for call in direct.calls] == ["copy", "regions"]
    assert out["note"] == "Копия собрана не полностью — проверьте её и отправьте на модерацию сами"


def test_copy_geo_draft_on_request(direct):
    out = ai_agent._x_copy_geo(FakeContext(), {"campaign_row_id": 7, "region_ids": [43], "moderate": "false"})
    assert [call[0] for call in direct.calls] == ["copy", "regions"]
    assert out["note"] == "Копия — черновик: отправьте на модерацию, когда проверите"


def test_copy_geo_reports_moderation_error(direct):
    direct.moderation = YandexError("direct", "Объявления не отправлены")
    out = ai_agent._x_copy_geo(FakeContext(), {"campaign_row_id": 7, "region_ids": [43]})
    assert out["moderation"] == {"status": "error", "error": "Объявления не отправлены"}


def test_copy_geo_changes(monkeypatch, regions):
    monkeypatch.setattr(geo, "campaign_regions", lambda user_id, campaign_row_id: [213, 20728])
    assert ai_agent._changes(FakeContext(), "copy_campaign_geo", {"campaign_row_id": 7, "region_ids": [43]}) == [
        {"what": "Копия", "before": "нет", "after": "«Поиск Москва — Казань» — на модерации"},
        {"what": "Регионы копии", "before": "Москва, Королёв", "after": "Казань"},
        {"what": "Оригинал «Поиск Москва»", "before": "работает", "after": "без изменений"},
    ]
    draft = ai_agent._changes(
        FakeContext({"accounts": [ACCOUNT]}),
        "copy_campaign_geo",
        {"campaign_row_id": 7, "region_ids": [43], "target_account_id": 4, "moderate": False},
    )
    assert draft[0]["after"] == "«Поиск Москва — Казань» — черновик в кабинете okna-spb"
