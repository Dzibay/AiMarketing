"""Память ИИ без БД и модели: правила аккаунта → клиента → проекта, «для сайта / для клиента», выжимка решений."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import ai_agent, ai_digest, workspaces
from app.clock import MSK
from app.workspaces import AI_TONES, normalize_ai_access

CLIENT_NOTES = "Платит за заявку. Без скидок в объявлениях."
CLIENT_FACTS = [
    {"key": "success_criteria", "question": "Что считаем успехом?", "answer": "Заявки дешевле 900 ₽"},
    {"key": "region", "question": "Регион?", "answer": "Вся Россия"},
]
SITE_FACT = {"key": "region", "question": "Регион?", "answer": "Только Казань"}


class FakeContext:
    """Context без БД: проект из словаря; account_ai — «Мои правила для ИИ» аккаунта."""

    general = False

    def __init__(self, ws: dict | None = None, *, account: dict | None = None, digest: str = ""):
        self.user_id = 1
        self.ws_id = 2
        self.ws = {"name": "Окна", "counter_id": 555, "accounts": [], "campaigns_count": 1, **(ws or {})}
        self.access = normalize_ai_access(self.ws.get("ai_access"))
        self.chat_id = None
        if account is not None:
            self.account_ai = account
        if digest:
            self.digest = digest


def client_ws(**extra) -> dict:
    return {
        "client_id": 9,
        "client_name": "Ромашка",
        "client_ai_notes": CLIENT_NOTES,
        "client_ai_facts": CLIENT_FACTS,
        **extra,
    }


@pytest.fixture
def no_memory(monkeypatch):
    monkeypatch.setattr(ai_agent, "memory_examples", lambda ws_id, limit: {})
    monkeypatch.setattr(ai_agent, "_running_experiments", lambda ctx: [])
    monkeypatch.setattr(ai_agent, "_image_gen_ready", lambda ctx: False)
    monkeypatch.setattr(ai_agent, "_context_files_block", lambda ctx: "")


# --- Порядок памяти в промпте проекта ---------------------------------------------------------------


def test_project_prompt_goes_account_then_client_then_project(no_memory):
    ctx = FakeContext(
        client_ws(ai_facts=[SITE_FACT], ai_notes="Кампанию «Бренд» не трогать"),
        account={"rules": "Отвечай коротко.", "tone": "sell"},
    )
    prompt = ai_agent._system_prompt(ctx)
    order = [
        ai_agent.MEMORY_ORDER_RULE,
        "Общие правила маркетолога (для всех его проектов и чатов):\nОтвечай коротко.",
        f"Что ИИ знает о клиенте «Ромашка» (общее для всех его проектов):\n{CLIENT_NOTES}",
        "- Что считаем успехом?: Заявки дешевле 900 ₽",
        "Что маркетолог уже ответил (учитывай, не переспрашивай):\n- Регион?: Только Казань",
        "Заметки маркетолога о проекте:\nКампанию «Бренд» не трогать",
    ]
    positions = [prompt.index(part) for part in order]
    assert positions == sorted(positions)
    assert "проекта важнее правил клиента" in prompt and "клиента — важнее общих правил маркетолога" in prompt
    # Ответ клиента с тем же key, что у сайта, не дублируется: действует ответ сайта.
    assert "Вся Россия" not in prompt
    # Унаследованный тон аккаунта стоит на уровне аккаунта — до клиента, чтобы тон клиента его перекрывал.
    default = prompt.index(f"Тон текстов для клиентов по умолчанию — {AI_TONES['sell']}")
    assert positions[1] < default < positions[2]


def test_inherited_tone_does_not_override_client_tone(no_memory):
    # Аккаунт — спокойный, клиент просит продающий, проект тон наследует: проект не повторяет «спокойный» ниже клиента.
    ws = client_ws(client_ai_notes="Тон — продающий, с призывом звонить.", ai_style={"tone": "", "address": "vy"})
    prompt = ai_agent._system_prompt(FakeContext(ws, account={"tone": "calm"}))
    client_at = prompt.index("Тон — продающий, с призывом звонить.")
    style = prompt[prompt.index(ai_agent.ai_memory.STYLE_TITLE):]
    assert AI_TONES["calm"] not in style and "Тон текстов для клиентов —" not in style
    assert prompt.index(f"по умолчанию — {AI_TONES['calm']}") < client_at
    # Свой тон проекта — в блоке проекта, после клиента, и строки «по умолчанию» нет.
    own = client_ws(ai_style={"tone": "expert", "address": "vy"})
    prompt = ai_agent._system_prompt(FakeContext(own, account={"tone": "calm"}))
    assert prompt.index(f"Тон текстов для клиентов — {AI_TONES['expert']}") > prompt.index(CLIENT_NOTES)
    assert "Тон текстов для клиентов по умолчанию" not in prompt


def test_rewrite_ads_gets_same_memory_as_chat(monkeypatch):
    # Переписывание объявлений: правила и тон аккаунта, память клиента и заметки проекта — как в чате проекта.
    from app import ad_stats, ai

    raw = {"client_row_id": 9, "client_name": "Ромашка", "client_ai_notes": CLIENT_NOTES,
           "client_ai_facts": CLIENT_FACTS, "ai_facts": [SITE_FACT], "ai_style": {"tone": "", "address": "ty"},
           "ai_notes": "Кампанию «Бренд» не трогать"}
    sent = []
    monkeypatch.setattr(ad_stats, "require_workspace", lambda user_id, ws_id: raw)
    monkeypatch.setattr(ad_stats, "_workspace_ads", lambda user_id, ws_id, ids: {1: {"type": "TEXT_AD", "adgroup_row_id": 7}})
    monkeypatch.setattr(ad_stats, "_group_keywords", lambda user_id, ids: {})
    monkeypatch.setattr(ad_stats, "brief_prompt", lambda ws: "БРИФ")
    monkeypatch.setattr(ad_stats, "_ad_block", lambda *a: "объявление")
    monkeypatch.setattr(ad_stats.ai_memory, "account_prefs", lambda user_id: {"rules": "Без капса.", "tone": "expert"})
    monkeypatch.setattr(ai, "load_config", lambda *a, **k: {})
    monkeypatch.setattr(ai, "usage_out", lambda acc, user_id: None)
    monkeypatch.setattr(ai, "complete_json", lambda cfg, messages: sent.append(messages[0]["content"]) or {"ads": []})
    ad_stats.ai_rewrite_ads(1, {"workspace_id": 2, "items": [{"ad_row_id": 1}]})
    system = sent[0]
    chat = "\n".join(ai_agent.ai_memory.memory_lines(raw, {"rules": "Без капса.", "tone": "expert"})).strip()
    assert chat in system and system.index("БРИФ") < system.index(chat)
    for part in ("Без капса.", AI_TONES["expert"], CLIENT_NOTES, "Только Казань", "на «ты»", "«Бренд» не трогать"):
        assert part in system, part


def test_project_prompt_without_account_rules_and_client_has_no_order_rule(no_memory):
    prompt = ai_agent._system_prompt(FakeContext(account={"rules": "  ", "tone": ""}))
    assert ai_agent.MEMORY_ORDER_RULE not in prompt
    assert "Общие правила маркетолога" not in prompt and "Что ИИ знает о клиенте" not in prompt
    # Контекст без account_ai (старые тесты, другой проект) — тоже без блока и без падения.
    assert ai_agent.MEMORY_ORDER_RULE not in ai_agent._system_prompt(FakeContext())


def test_client_block_only_for_project_with_client(no_memory):
    # Поля клиента без client_id (проект убрали из папки) в промпт не идут.
    ctx = FakeContext({"client_ai_notes": CLIENT_NOTES, "client_ai_facts": CLIENT_FACTS})
    prompt = ai_agent._system_prompt(ctx)
    assert CLIENT_NOTES not in prompt and "Заявки дешевле 900 ₽" not in prompt
    # Клиент без заметок и фактов — блока нет, но правила аккаунта с порядком есть.
    prompt = ai_agent._system_prompt(
        FakeContext({"client_id": 9, "client_name": "Ромашка"}, account={"rules": "Без воды."})
    )
    assert "Что ИИ знает о клиенте" not in prompt
    assert ai_agent.MEMORY_ORDER_RULE in prompt and "Без воды." in prompt


def test_client_scheme_answer_counts_as_known(no_memory):
    scheme = {"key": workspaces.SCHEME_FACT, "question": "Схема 0 ₽?", "answer": "Платим за заказ"}
    prompt = ai_agent._system_prompt(FakeContext(client_ws(client_ai_facts=[scheme])))
    assert f"key {workspaces.SCHEME_FACT}" not in prompt  # ответ клиента — тоже ответ, не переспрашиваем


def test_project_tone_falls_back_to_account_tone(no_memory):
    inherit = {"ai_style": {"tone": "", "address": "vy"}, "ai_tone_own": False}
    prompt = ai_agent._system_prompt(FakeContext(inherit, account={"tone": "expert"}))
    assert AI_TONES["expert"] in prompt
    own = {"ai_style": {"tone": "sell", "address": "vy"}, "ai_tone_own": True}
    prompt = ai_agent._system_prompt(FakeContext(own, account={"tone": "expert"}))
    assert AI_TONES["sell"] in prompt and AI_TONES["expert"] not in prompt
    # Ни своего тона, ни тона аккаунта — спокойный.
    assert AI_TONES["calm"] in ai_agent._system_prompt(FakeContext(inherit, account={"tone": "громкий"}))


def test_ai_style_prompt_reads_own_tone_from_raw_style():
    assert AI_TONES["sell"] in workspaces.ai_style_prompt({"ai_style": {"tone": ""}}, "sell")
    assert AI_TONES["calm"] in workspaces.ai_style_prompt({"ai_style": {"tone": "calm"}}, "sell")
    assert AI_TONES["calm"] in workspaces.ai_style_prompt({"ai_style": None})


def test_clean_ai_style_keeps_inherited_tone_empty(monkeypatch):
    clean = workspaces._clean_ai_style
    assert clean({"tone": "sell", "address": "vy"}, {"tone": ""})["tone"] == ""
    assert clean({"tone": "", "address": "vy"}, {"address": "ty"}) == {"tone": "", "address": "ty", "model_class": ""}
    assert clean({"tone": ""}, {"tone": "expert"})["tone"] == "expert"
    assert clean({"tone": "calm"}, {"address": "ty"})["tone"] == "calm"
    assert workspaces.tone_own({"tone": "sell"}) and not workspaces.tone_own({"tone": ""})


# --- Общий чат: правила аккаунта и тон по умолчанию --------------------------------------------------


def test_general_prompt_has_account_rules_and_default_tone(monkeypatch):
    monkeypatch.setattr(ai_agent, "list_workspaces", lambda user_id: [])
    ctx = FakeContext(account={"rules": "Сначала спрашивай бюджет.", "tone": "calm"})
    ctx.general = True
    prompt = ai_agent._general_prompt(ctx)
    assert prompt.endswith(
        "Общие правила маркетолога (для всех его проектов и чатов):\nСначала спрашивай бюджет.\n\n"
        f"Тон текстов для клиентов по умолчанию — {AI_TONES['calm']}. Если в проекте свой тон, важнее он."
    )
    ctx = FakeContext()
    ctx.general = True
    assert "Общие правила маркетолога" not in ai_agent._general_prompt(ctx)


# --- Выжимка прошлых решений в промпте ---------------------------------------------------------------


def test_digest_block_only_when_chat_has_digest(no_memory):
    digest = "- 03.11 отклонено: Остановить «РСЯ» — причина: «там идут заказы»"
    prompt = ai_agent._system_prompt(FakeContext(digest=digest))
    assert f"{ai_agent.DIGEST_TITLE}\n{digest}\n{ai_agent.DIGEST_RULE}" in prompt
    assert "Не предлагай снова то, что маркетолог отклонил" in ai_agent.DIGEST_RULE
    assert "прямо скажи, что раньше это отклонили" in ai_agent.DIGEST_RULE
    plain = ai_agent._system_prompt(FakeContext())
    assert ai_agent.DIGEST_TITLE not in plain and ai_agent.DIGEST_RULE not in plain


def test_chat_digest_skips_general_chat():
    ctx = FakeContext()
    ctx.general = True
    assert ai_agent._chat_digest(ctx, 5) == ""  # без обращения к БД


# --- Где запомнить: для этого сайта или для всего клиента ---------------------------------------------


def test_fact_scope_defaults():
    with_client = FakeContext({"client_id": 9})
    assert ai_agent._fact_scope(with_client, "success_criteria") == "client"
    assert ai_agent._fact_scope(with_client, "region") == "site"
    assert ai_agent._fact_scope(with_client, "success_criteria", "site") == "site"
    assert ai_agent._fact_scope(with_client, "region", "client") == "client"
    assert ai_agent._fact_scope(with_client, "region", "везде") == "site"
    # Без клиента — всегда этот сайт, что бы ни просили.
    assert ai_agent._fact_scope(FakeContext(), "success_criteria", "client") == "site"
    assert ai_agent._fact_scope(SimpleNamespace(), "region") == "site"


def test_questions_offer_scope_only_for_project_with_client():
    ask = {"questions": [
        {"key": "success_criteria", "question": "Что считаем успехом?"},
        {"key": "deadline", "question": "Срок?"},
        {"key": "budget", "question": "Бюджет?", "scope": "client"},
        {"key": "region", "question": "Регион?"},
        {"key": "tmp", "question": "Разово?", "remember": False},
    ]}
    out = ai_agent._q_questions(FakeContext(client_ws(client_ai_facts=[CLIENT_FACTS[1]])), ask)
    by_key = {q["key"]: q for q in out["questions"]}
    assert by_key["success_criteria"]["scope"] == "client"
    assert by_key["deadline"]["scope"] == "site"
    assert by_key["budget"]["scope"] == "client"
    assert "scope" not in by_key["tmp"]  # не запоминаем — и выбирать нечего
    # Ответ клиента — уже известен: вопрос не задаём, показываем ответ.
    assert "region" not in by_key
    assert out["answered"] == [{"key": "region", "question": "Регион?", "answer": "Вся Россия"}]
    # Ответ сайта важнее ответа клиента с тем же key.
    out = ai_agent._q_questions(FakeContext(client_ws(ai_facts=[SITE_FACT])), {"questions": [ask["questions"][3]]})
    assert out["answered"] == [{"key": "region", "question": "Регион?", "answer": "Только Казань"}]
    # Без клиента — как раньше, без выбора.
    plain = ai_agent._q_questions(FakeContext(), ask)
    assert all("scope" not in q for q in plain["questions"])


def test_compose_answers_takes_marketer_scope():
    questions = [
        {"n": 1, "key": "success_criteria", "question": "Успех?", "options": [], "remember": True, "scope": "client"},
        {"n": 2, "key": "deadline", "question": "Срок?", "options": [], "remember": True, "scope": "site"},
        {"n": 3, "key": "budget", "question": "Бюджет?", "options": [], "remember": True},
    ]
    answers = [
        {"n": 1, "text": "Заявки", "scope": "site"},
        {"n": 2, "text": "Неделя", "scope": "куда-то"},
        {"n": 3, "text": "50 000 ₽", "scope": "client"},
    ]
    _, facts = ai_agent._compose_answers(questions, answers)
    assert [(f["key"], f.get("scope")) for f in facts] == [
        ("success_criteria", "site"),
        ("deadline", "site"),
        ("budget", None),  # вопрос без выбора (проект без клиента) — scope из ответа не берём
    ]


@pytest.fixture
def card(monkeypatch):
    fake = SimpleNamespace(row=None, closed=[], remembered=[], where="")

    def pending(user_id, action_id, tool):
        return fake.row

    def close(user_id, row, *, status, result, error, reply):
        fake.closed.append({"status": status, "result": result})
        return {**row, "status": status, "result": result, "error": error}, 0

    def remember(user_id, workspace_id, items, scope="site"):
        fake.remembered.append((scope, [f["key"] for f in items]))

    def save(user_id, workspace_id, items, *, scope="site"):
        remember(user_id, workspace_id, items, scope)
        return [], fake.where or scope  # where — куда запомнилось на самом деле

    monkeypatch.setattr(ai_agent, "_pending_interactive", pending)
    monkeypatch.setattr(ai_agent, "_close_interactive", close)
    monkeypatch.setattr(ai_agent, "remember_ai_facts", remember)
    monkeypatch.setattr(ai_agent, "save_ai_facts", save)
    monkeypatch.setattr(ai_agent, "_submit", lambda user_id, chat_id, marker: None)
    return fake


def test_answer_questions_remembers_site_and_client_apart(card):
    questions = [
        {"n": 1, "key": "success_criteria", "question": "Успех?", "options": [], "remember": True, "scope": "client"},
        {"n": 2, "key": "deadline", "question": "Срок?", "options": [], "remember": True, "scope": "site"},
    ]
    card.row = {"id": 11, "chat_id": 5, "message_id": 6, "tool_call_id": "c1", "tool": "ask_questions",
                "workspace_id": 2, "args": {"questions": questions}, "summary": "", "category": "",
                "status": "pending", "auto": False}
    ai_agent.answer_questions(1, 11, answers=[{"n": 1, "text": "Заявки"}, {"n": 2, "text": "Неделя"}])
    assert card.remembered == [("site", ["deadline"]), ("client", ["success_criteria"])]
    result = card.closed[0]["result"]
    assert result["remembered"] == ["success_criteria", "deadline"]
    assert result["remembered_client"] == ["success_criteria"]


def test_answer_questions_reports_where_fact_was_really_stored(card):
    # Проект убрали из папки клиента, пока висела карточка: ответ запомнен у сайта — «для клиента» не пишем.
    questions = [{"n": 1, "key": "success_criteria", "question": "Успех?", "options": [], "remember": True,
                  "scope": "client"}]
    card.row = {"id": 12, "chat_id": 5, "message_id": 6, "tool_call_id": "c1", "tool": "ask_questions",
                "workspace_id": 2, "args": {"questions": questions}, "summary": "", "category": "",
                "status": "pending", "auto": False}
    card.where = "site"
    ai_agent.answer_questions(1, 12, answers=[{"n": 1, "text": "Заявки"}])
    result = card.closed[0]["result"]
    assert result["remembered"] == ["success_criteria"] and "remembered_client" not in result


def test_remember_tool_passes_scope(monkeypatch):
    calls = []

    def remember(user_id, workspace_id, items, *, scope="site"):
        [item] = items
        calls.append((item["key"], scope))
        if scope == "client":
            return [], "client"
        return [{**item, "at": None}], "site"

    monkeypatch.setattr(workspaces, "save_ai_facts", remember)
    ctx = FakeContext(client_ws(client_ai_facts=[]))
    out = ai_agent._t_remember(ctx, {"key": "success_criteria", "question": "Успех?", "answer": "Заявки"})
    assert out == {"remembered": True, "facts_total": 0, "scope": "client"}
    assert [f["key"] for f in ctx.ws["client_ai_facts"]] == ["success_criteria"]
    out = ai_agent._t_remember(ctx, {"key": "region", "answer": "Казань", "scope": "site"})
    assert out["scope"] == "site"
    plain = FakeContext()
    out = ai_agent._t_remember(plain, {"key": "success_criteria", "answer": "Заявки", "scope": "client"})
    assert "scope" not in out
    assert calls == [("success_criteria", "client"), ("region", "site"), ("success_criteria", "site")]
    # Клиента убрали посреди хода: запомнилось у сайта — так и отвечаем, память клиента в ctx не трогаем.
    monkeypatch.setattr(workspaces, "save_ai_facts", lambda user_id, ws_id, items, *, scope="site": ([], "site"))
    gone = FakeContext(client_ws(client_ai_facts=[]))
    out = ai_agent._t_remember(gone, {"key": "success_criteria", "answer": "Заявки"})
    assert out["scope"] == "site" and gone.ws["client_ai_facts"] == []


# --- Выжимка решений: строки и порядок ------------------------------------------------------------------


def _at(days_ago: float) -> datetime:
    return datetime.now(MSK) - timedelta(days=days_ago)


def test_decisions_digest_lines_newest_first(monkeypatch):
    rows = [
        {"summary": "Остановить «РСЯ»", "status": "rejected", "auto": False, "decline_reason": "там идут заказы",
         "snoozed_at": None, "at": _at(1)},
        {"summary": "Минус-фраза «бесплатно»", "status": "done", "auto": False, "decline_reason": "",
         "snoozed_at": None, "at": _at(3)},
        {"summary": "Ставка +10%", "status": "done", "auto": True, "decline_reason": "", "snoozed_at": None,
         "at": _at(4)},
        {"summary": "Бюджет 3000 ₽", "status": "rejected", "auto": False, "decline_reason": None,
         "snoozed_at": None, "at": _at(5)},
        {"summary": "Новая группа", "status": "pending", "auto": False, "decline_reason": "", "snoozed_at": None,
         "at": _at(0.5)},
        {"summary": "Регион Казань", "status": "pending", "auto": False, "decline_reason": "",
         "snoozed_at": _at(2), "at": _at(6)},
    ]
    logs = [
        {"title": "Бюджет кампании", "changes": ["1000 ₽ → 2000 ₽"], "campaigns": [{"name": "Поиск"}],
         "source": "user", "_at": _at(2), "_undone": False},
        {"title": "Минус-фразы", "changes": [], "campaigns": [], "source": "ai", "_at": _at(7), "_undone": True},
    ]
    seen = {}

    def action_rows(user_id, ws_id, tools, exclude_chat, days):
        seen.update(user=user_id, ws=ws_id, tools=tools, chat=exclude_chat, days=days)
        return rows

    monkeypatch.setattr(ai_digest, "_action_rows", action_rows)
    monkeypatch.setattr(ai_digest, "_log_rows", lambda user_id, ws_id, days: logs)
    lines = ai_digest.decisions_digest(1, 2, tools=["pause_campaigns"], exclude_chat=5)
    assert seen == {"user": 1, "ws": 2, "tools": ["pause_campaigns"], "chat": 5, "days": 30}
    texts = [line.split(" ", 2)[2] for line in lines]
    assert texts == [
        "ждёт решения в другом чате: Новая группа",
        "отклонено: Остановить «РСЯ» — причина: «там идут заказы»",
        "маркетолог сам изменил: Бюджет кампании — 1000 ₽ → 2000 ₽ («Поиск»)",
        "одобрено и сделано: Минус-фраза «бесплатно»",
        "сделано сразу (по доступу ИИ): Ставка +10%",
        "отклонено: Бюджет 3000 ₽ — причину не назвал",
        "отложено, ждёт решения: Регион Казань",
        "откатили изменение ИИ: Минус-фразы",
    ]
    assert all(line.startswith("- ") and line[4] == "." for line in lines)  # «- дд.мм …»
    assert lines[0][2:7] == f"{_at(0.5):%d.%m}"
    assert len(ai_digest.decisions_digest(1, 2, tools=[], limit=3)) == 3


def test_decisions_digest_survives_journal_failure(monkeypatch):
    row = {"summary": "x" * 400, "status": "done", "auto": False, "decline_reason": "", "snoozed_at": None,
           "at": _at(1)}
    monkeypatch.setattr(ai_digest, "_action_rows", lambda *a: [row])

    def broken(*a):
        raise RuntimeError("журнал недоступен")

    monkeypatch.setattr(ai_digest, "_log_rows", broken)
    [line] = ai_digest.decisions_digest(1, 2, tools=[])
    assert line.endswith("…") and len(line) < 200  # длинное описание обрезано


def test_log_rows_keep_manual_and_undone_only(monkeypatch):
    now = datetime.now(MSK)
    items = [
        {"id": 1, "title": "Ручная правка", "source": "user", "status": "ok", "created_at": now.isoformat(),
         "undone_at": None, "undo_of": None},
        {"id": 2, "title": "Правка ИИ", "source": "ai", "status": "ok", "created_at": now.isoformat(),
         "undone_at": None, "undo_of": None},
        {"id": 3, "title": "Правка ИИ, откачена", "source": "ai", "status": "ok",
         "created_at": (now - timedelta(days=40)).isoformat(), "undone_at": now.isoformat(), "undo_of": None},
        {"id": 4, "title": "Откат", "source": "user", "status": "ok", "created_at": now.isoformat(),
         "undone_at": None, "undo_of": 3},
        {"id": 5, "title": "Ручная с ошибкой", "source": "user", "status": "error", "created_at": now.isoformat(),
         "undone_at": None, "undo_of": None},
        {"id": 6, "title": "Старая ручная", "source": "user", "status": "ok",
         "created_at": (now - timedelta(days=31)).isoformat(), "undone_at": None, "undo_of": None},
    ]
    import app.journal as journal

    monkeypatch.setattr(journal, "list_action_logs", lambda user_id, limit, workspace_id=None: items)
    out = ai_digest._log_rows(1, 2, 30)
    assert [(i["id"], i["_undone"]) for i in out] == [(1, False), (3, True)]
