"""Память ИИ в БД: правила аккаунта, память клиента, «для сайта / для клиента», выжимка решений для нового чата."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import ai_agent, workspaces
from app.ai_digest import decisions_digest
from app.clients import create_client, forget_client_fact, get_client, remember_client_facts, update_client
from app.main import app
from app.users import UsersError, get_ai_prefs, set_ai_prefs
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_user, make_workspace

TOOLS = sorted(ai_agent.WRITE_TOOLS)


def _fact(key: str, answer: str, question: str = "") -> dict:
    return {"key": key, "question": question or f"Вопрос {key}?", "answer": answer}


def _answers(facts: list[dict]) -> dict:
    return {f["key"]: f["answer"] for f in facts}


# --- Правила аккаунта ---------------------------------------------------------------------------------


def test_account_ai_prefs(db):
    user = make_user(db)
    assert get_ai_prefs(user) == {"rules": "", "tone": ""}
    assert set_ai_prefs(user, rules="  Отвечай коротко.  ", tone="sell") == {"rules": "Отвечай коротко.", "tone": "sell"}
    # Присланное меняется, остальное остаётся; пустой тон — «не задан».
    assert set_ai_prefs(user, tone="") == {"rules": "Отвечай коротко.", "tone": ""}
    assert set_ai_prefs(user) == {"rules": "Отвечай коротко.", "tone": ""}
    with pytest.raises(UsersError) as err:
        set_ai_prefs(user, rules="а" * 1001)
    assert err.value.code == "ai-rules-too-long"
    with pytest.raises(UsersError) as err:
        set_ai_prefs(user, tone="громкий")
    assert err.value.code == "ai-tone"
    assert get_ai_prefs(user)["rules"] == "Отвечай коротко."
    with pytest.raises(UsersError):
        get_ai_prefs(user + 100)


# --- Память клиента ------------------------------------------------------------------------------------


def test_client_ai_notes_and_facts(db):
    user = make_user(db)
    client = create_client(user, name="Ромашка", notes="Личное: должен за май", ai_notes="  Платит за заявку  ")
    assert (client["ai_notes"], client["ai_facts"]) == ("Платит за заявку", [])
    assert update_client(user, client["id"], ai_notes="Без скидок")["ai_notes"] == "Без скидок"
    assert update_client(user, client["id"], name="Ромашка 2")["ai_notes"] == "Без скидок"  # не трогаем

    remember_client_facts(user, client["id"], [_fact("region", "Вся Россия"), _fact("deadline", "Неделя")])
    remember_client_facts(user, client["id"], [_fact("region", "Казань")])  # тот же key заменяет ответ
    facts = get_client(user, client["id"])["ai_facts"]
    assert [f["key"] for f in facts] == ["deadline", "region"]
    assert _answers(facts) == {"deadline": "Неделя", "region": "Казань"}
    assert all(f["at"] for f in facts)
    with pytest.raises(YandexError):
        remember_client_facts(user, client["id"], [{"key": "region", "answer": " "}])

    assert [f["key"] for f in forget_client_fact(user, client["id"], "deadline")["ai_facts"]] == ["region"]
    other = make_user(db, "other@example.com")
    with pytest.raises(YandexError):
        forget_client_fact(other, client["id"], "region")
    with pytest.raises(YandexError):
        remember_client_facts(other, client["id"], [_fact("region", "Москва")])


# --- Для этого сайта / для всего клиента ----------------------------------------------------------------


def _client_project(db):
    user = make_user(db)
    client = create_client(user, name="Ромашка", notes="Личное: должен за май", ai_notes="Платит за заявку")
    ws = make_workspace(db, user, 555, "Окна", client_id=client["id"])
    sibling = make_workspace(db, user, 556, "Двери", client_id=client["id"])
    return user, client["id"], ws, sibling


def test_workspace_sees_client_memory_but_not_private_notes(db):
    user, client, ws, sibling = _client_project(db)
    out = workspaces.get_workspace(user, ws)
    assert (out["client_ai_notes"], out["client_ai_facts"]) == ("Платит за заявку", [])
    assert "Личное" not in str(out)  # личные заметки о клиенте в проект (и в ИИ) не идут
    alone = make_workspace(db, user, 557, "Без клиента")
    out = workspaces.get_workspace(user, alone)
    assert (out["client_id"], out["client_ai_notes"], out["client_ai_facts"]) == (None, "", [])


def test_remember_for_site_and_for_client(db):
    user, client, ws, sibling = _client_project(db)
    workspaces.remember_ai_fact(user, ws, key="region", question="Регион?", answer="Казань")
    assert _answers(workspaces.get_workspace(user, ws)["ai_facts"]) == {"region": "Казань"}

    # Для всего клиента: ответ виден во всех проектах клиента, прежний ответ сайта с тем же key убран.
    facts = workspaces.remember_ai_facts(
        user, ws, [_fact("region", "Вся Россия"), _fact("success_criteria", "Заявки до 900 ₽")], scope="client"
    )
    assert facts == []
    for project in (ws, sibling):
        out = workspaces.get_workspace(user, project)
        assert _answers(out["client_ai_facts"]) == {"region": "Вся Россия", "success_criteria": "Заявки до 900 ₽"}
    assert workspaces.get_workspace(user, ws)["ai_facts"] == []

    # Забыть у клиента — пропадает во всех проектах; у сайта — только у него.
    workspaces.remember_ai_fact(user, sibling, key="deadline", question="Срок?", answer="Неделя")
    out = workspaces.forget_ai_fact(user, ws, "region", scope="client")
    assert _answers(out["client_ai_facts"]) == {"success_criteria": "Заявки до 900 ₽"}
    out = workspaces.forget_ai_fact(user, sibling, "deadline")
    assert out["ai_facts"] == [] and len(out["client_ai_facts"]) == 1

    # Проект без клиента помнит у себя, что бы ни просили.
    alone = make_workspace(db, user, 557, "Без клиента")
    facts = workspaces.remember_ai_fact(user, alone, key="region", question="", answer="Москва", scope="client")
    assert _answers(facts) == {"region": "Москва"}
    assert _answers(get_client(user, client)["ai_facts"]) == {"success_criteria": "Заявки до 900 ₽"}


def test_move_fact_between_site_and_client(db):
    user, client, ws, sibling = _client_project(db)
    workspaces.remember_ai_fact(user, ws, key="region", question="Регион?", answer="Казань")

    out = workspaces.move_ai_fact(user, ws, "region", "client")
    assert out["ai_facts"] == []
    assert _answers(out["client_ai_facts"]) == {"region": "Казань"}
    assert _answers(workspaces.get_workspace(user, sibling)["client_ai_facts"]) == {"region": "Казань"}

    out = workspaces.move_ai_fact(user, sibling, "region", "site")
    assert _answers(out["ai_facts"]) == {"region": "Казань"} and out["client_ai_facts"] == []
    assert workspaces.get_workspace(user, ws)["client_ai_facts"] == []

    for key, scope, code in (("region", "везде", "args"), ("nope", "client", "not-found"), ("nope", "site", "not-found")):
        with pytest.raises(YandexError) as err:
            workspaces.move_ai_fact(user, sibling, key, scope)
        assert err.value.code == code
    alone = make_workspace(db, user, 557, "Без клиента")
    workspaces.remember_ai_fact(user, alone, key="region", question="", answer="Москва")
    with pytest.raises(YandexError) as err:
        workspaces.move_ai_fact(user, alone, "region", "client")
    assert err.value.code == "no-client"


def test_forget_client_fact_without_client_keeps_site_fact(db):
    user = make_user(db)
    alone = make_workspace(db, user, 557, "Без клиента")
    workspaces.remember_ai_fact(user, alone, key="success_criteria", question="", answer="Заявки")
    for scope, code in (("client", "no-client"), ("везде", "args")):
        with pytest.raises(YandexError) as err:
            workspaces.forget_ai_fact(user, alone, "success_criteria", scope=scope)
        assert err.value.code == code
    assert _answers(workspaces.get_workspace(user, alone)["ai_facts"]) == {"success_criteria": "Заявки"}


def test_move_fact_into_full_memory_fails_instead_of_evicting(db):
    user, client, ws, sibling = _client_project(db)
    full = [_fact(f"k{i}", f"ответ {i}") for i in range(workspaces.MAX_AI_FACTS)]
    workspaces.remember_ai_facts(user, ws, full)
    remember_client_facts(user, client, [_fact("region", "Казань")])
    with pytest.raises(YandexError) as err:
        workspaces.move_ai_fact(user, ws, "region", "site")
    assert err.value.code == "facts-full" and "У сайта уже 30" in err.value.message
    out = workspaces.get_workspace(user, ws)  # ничего не потеряно: ни ответ клиента, ни старый ответ сайта
    assert len(out["ai_facts"]) == workspaces.MAX_AI_FACTS and out["ai_facts"][0]["key"] == "k0"
    assert _answers(out["client_ai_facts"]) == {"region": "Казань"}
    # Тот же key у сайта уже есть — это замена, место не нужно.
    workspaces.remember_ai_fact(user, ws, key="k0", question="", answer="старый")
    remember_client_facts(user, client, [_fact("k0", "новый")])
    assert _answers(workspaces.move_ai_fact(user, ws, "k0", "site")["ai_facts"])["k0"] == "новый"
    # В клиента: у клиента 30 ответов — тоже понятная ошибка, ответ сайта остаётся.
    remember_client_facts(user, client, [_fact(f"c{i}", "x") for i in range(workspaces.MAX_AI_FACTS)])
    with pytest.raises(YandexError) as err:
        workspaces.move_ai_fact(user, ws, "k1", "client")
    assert err.value.code == "facts-full" and "У клиента" in err.value.message
    assert "k1" in _answers(workspaces.get_workspace(user, ws)["ai_facts"])
    api = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    res = api.post(f"/api/web/workspaces/{ws}/ai-facts/move", headers=auth, json={"key": "k1", "scope": "client"})
    assert res.status_code == 400 and res.json()["detail"]["code"] == "facts-full"


def test_project_tone_inherits_account_tone(db):
    user = make_user(db)
    ws = make_workspace(db, user, 555, "Окна")
    out = workspaces.update_workspace(user, ws, ai_style={"tone": "", "address": "ty"})
    assert out["ai_tone_own"] is False and out["ai_style"]["address"] == "ty"
    assert workspaces.AI_TONES["sell"] in workspaces.ai_style_prompt(out, "sell")
    # Смена обращения не превращает «как в аккаунте» в свой тон.
    out = workspaces.update_workspace(user, ws, ai_style={"address": "vy"})
    assert out["ai_tone_own"] is False
    out = workspaces.update_workspace(user, ws, ai_style={"tone": "expert"})
    assert out["ai_tone_own"] is True
    prompt = workspaces.ai_style_prompt(out, "sell")
    assert workspaces.AI_TONES["expert"] in prompt and workspaces.AI_TONES["sell"] not in prompt


# --- Выжимка прошлых решений -----------------------------------------------------------------------------


def _chat(db, user: int, ws: int | None) -> int:
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, %s, 'Чат') RETURNING id", (user, ws)
        ).fetchone()["id"]


def _message(db, chat: int, role: str = "assistant") -> int:
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, %s, 'ок') RETURNING id", (chat, role)
        ).fetchone()["id"]


def _action(db, user, chat, summary, status, *, hours=1, tool="campaigns_action", args=None, auto=False,
            error="", reason="", snoozed=False) -> int:
    message = _message(db, chat)
    with db.connection() as conn:
        return conn.execute(
            """
            INSERT INTO ai_actions (
                chat_id, message_id, user_id, tool, args, summary, status, auto, error, decline_reason,
                created_at, decided_at, snoozed_at
            )
            VALUES (
                %(chat)s, %(message)s, %(user)s, %(tool)s, %(args)s, %(summary)s, %(status)s, %(auto)s, %(error)s,
                %(reason)s, NOW() - make_interval(hours => %(hours)s),
                CASE WHEN %(status)s <> 'pending' THEN NOW() - make_interval(hours => %(hours)s) END,
                CASE WHEN %(snoozed)s THEN NOW() END
            )
            RETURNING id
            """,
            {"chat": chat, "message": message, "user": user, "tool": tool, "args": Jsonb(args or {}),
             "summary": summary, "status": status, "auto": auto, "error": error, "reason": reason, "hours": hours,
             "snoozed": snoozed},
        ).fetchone()["id"]


def _manual_log(db, user: int, ws: int, title: str, *, minutes: int, source: str = "user", status: str = "ok") -> None:
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO action_logs (user_id, action, payload, result, status, workspace_id, source, title, created_at)
            VALUES (%s, 'manual_test', '{}', '{}', %s, %s, %s, %s, NOW() - make_interval(mins => %s))
            """,
            (user, status, ws, source, title, minutes),
        )


def _seed_decisions(db):
    user = make_user(db)
    ws = make_workspace(db, user, 555, "Окна")
    other_ws = make_workspace(db, user, 556, "Двери")
    old, new = _chat(db, user, ws), _chat(db, user, ws)
    general, foreign = _chat(db, user, None), _chat(db, user, other_ws)
    _action(db, user, old, "Новая группа «Пластиковые окна»", "pending", hours=1)
    _action(db, user, old, "Минус-фраза «бесплатно»", "done", hours=2)
    _action(db, user, old, "Ставка +10%", "done", hours=3, auto=True)
    _action(db, user, old, "Остановить «РСЯ»", "rejected", hours=4, reason="там идут заказы")
    _action(db, user, old, "Регион Казань", "pending", hours=5, snoozed=True)
    _action(db, user, general, "Бюджет 3000 ₽ из общего чата", "done", hours=6, args={"project_id": ws})
    # Не попадают: своя карточка нового чата, пропуск сервисом, чужой проект, старьё, не-действие.
    _action(db, user, new, "Своё предложение", "pending", hours=1)
    _action(db, user, old, "Закрыто новым сообщением", "rejected", hours=2, error="Пропущено: новое сообщение")
    _action(db, user, general, "Для другого проекта", "done", hours=2, args={"project_id": other_ws})
    _action(db, user, foreign, "Чужой проект", "done", hours=2)
    _action(db, user, old, "Месяц назад", "done", hours=31 * 24)
    _action(db, user, old, "Вопросы", "done", hours=2, tool="ask_questions")
    _manual_log(db, user, ws, "Бюджет кампании", minutes=30)
    _manual_log(db, user, ws, "Ручная правка с ошибкой", minutes=40, status="error")
    _manual_log(db, user, ws, "Правка ИИ", minutes=50, source="ai")
    return SimpleNamespace(user=user, ws=ws, old=old, new=new)


def test_decisions_digest_query(db):
    s = _seed_decisions(db)
    lines = decisions_digest(s.user, s.ws, tools=TOOLS, exclude_chat=s.new)
    texts = [line.split(" ", 2)[2] for line in lines]
    assert texts[0].startswith("маркетолог сам изменил: Бюджет кампании")
    assert texts[1:] == [
        "ждёт решения в другом чате: Новая группа «Пластиковые окна»",
        "одобрено и сделано: Минус-фраза «бесплатно»",
        "сделано сразу (по доступу ИИ): Ставка +10%",
        "отклонено: Остановить «РСЯ» — причина: «там идут заказы»",
        "отложено, ждёт решения: Регион Казань",
        "одобрено и сделано: Бюджет 3000 ₽ из общего чата",
    ]
    assert len(decisions_digest(s.user, s.ws, tools=TOOLS, exclude_chat=s.new, limit=2)) == 2
    # Из старого чата его собственная ждущая карточка не видна, а карточка нового чата — видна.
    texts = [line.split(" ", 2)[2] for line in decisions_digest(s.user, s.ws, tools=TOOLS, exclude_chat=s.old)]
    assert "ждёт решения в другом чате: Своё предложение" in texts
    assert not any("Пластиковые окна" in t for t in texts)
    other = make_user(db, "other@example.com")
    assert decisions_digest(other, s.ws, tools=TOOLS) == []


def test_chat_digest_is_taken_once_for_new_chat(db):
    s = _seed_decisions(db)
    ctx = SimpleNamespace(general=False, user_id=s.user, ws_id=s.ws)
    fresh = _chat(db, s.user, s.ws)
    _message(db, fresh, role="user")  # первый ход: ИИ ещё не отвечал
    digest = ai_agent._chat_digest(ctx, fresh)
    assert "отклонено: Остановить «РСЯ» — причина: «там идут заказы»" in digest
    assert "ждёт решения в другом чате: Своё предложение" in digest
    assert digest.count("\n") == 7  # восемь строк, новые сверху
    with db.connection() as conn:
        assert conn.execute("SELECT digest FROM ai_chats WHERE id = %s", (fresh,)).fetchone()["digest"] == digest
    # Снимается один раз: промпт чата от хода к ходу не меняется.
    _action(db, s.user, s.old, "Новое после начала чата", "done", hours=0)
    assert ai_agent._chat_digest(ctx, fresh) == digest
    # Чат, где ИИ уже отвечал, выжимку не получает; чужой чат — тоже.
    assert ai_agent._chat_digest(ctx, s.old) == ""
    with db.connection() as conn:
        assert conn.execute("SELECT digest FROM ai_chats WHERE id = %s", (s.old,)).fetchone()["digest"] == ""
    stranger = SimpleNamespace(general=False, user_id=make_user(db, "x@example.com"), ws_id=s.ws)
    assert ai_agent._chat_digest(stranger, fresh) == ""


def test_review_button_chat_gets_digest(db, monkeypatch):
    # «Разбор» по кнопке: в новом чате до первого хода уже лежит вызов get_signals от ИИ — без текста.
    from app import ai, insights, jobs

    s = _seed_decisions(db)
    monkeypatch.setattr(ai, "require_ready", lambda user_id: None)
    monkeypatch.setattr(insights, "get_signals", lambda user_id, ws_id: {})
    monkeypatch.setattr(insights, "signals_digest", lambda signals: "сигналов нет")
    monkeypatch.setattr(ai_agent, "_submit", lambda user_id, chat_id, marker: {"id": 1})
    monkeypatch.setattr(jobs, "public_job", lambda job: job)
    chat = ai_agent.start_review(s.user, s.ws)["chat"]["id"]
    ctx = SimpleNamespace(general=False, user_id=s.user, ws_id=s.ws)
    digest = ai_agent._chat_digest(ctx, chat)
    assert "отклонено: Остановить «РСЯ» — причина: «там идут заказы»" in digest
    with db.connection() as conn:
        assert conn.execute("SELECT digest FROM ai_chats WHERE id = %s", (chat,)).fetchone()["digest"] == digest


# --- Кабинет: ручки памяти ИИ -----------------------------------------------------------------------------


def test_web_api_for_ai_memory(db):
    user, client, ws, sibling = _client_project(db)
    api = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}
    other = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}

    assert api.get("/api/web/me/ai", headers=auth).json() == {"rules": "", "tone": "", "tones": ["calm", "sell", "expert"]}
    saved = api.patch("/api/web/me/ai", headers=auth, json={"rules": " Коротко ", "tone": "expert"}).json()
    assert (saved["rules"], saved["tone"]) == ("Коротко", "expert")
    bad = api.patch("/api/web/me/ai", headers=auth, json={"rules": "я" * 1001})
    assert (bad.status_code, bad.json()["detail"]) == (400, "ai-rules-too-long")
    assert api.patch("/api/web/me/ai", headers=auth, json={"tone": "громкий"}).json()["detail"] == "ai-tone"

    patched = api.patch(f"/api/web/clients/{client}", headers=auth, json={"ai_notes": "Только заявки"}).json()
    assert patched["ai_notes"] == "Только заявки"

    workspaces.remember_ai_fact(user, ws, key="region", question="Регион?", answer="Казань")
    moved = api.post(f"/api/web/workspaces/{ws}/ai-facts/move", headers=auth, json={"key": "region", "scope": "client"})
    assert moved.status_code == 200 and _answers(moved.json()["client_ai_facts"]) == {"region": "Казань"}
    assert api.post(f"/api/web/workspaces/{ws}/ai-facts/move", headers=other,
                    json={"key": "region", "scope": "site"}).status_code == 404
    forgot = api.post(f"/api/web/clients/{client}/ai-facts/forget", headers=other, json={"key": "region"})
    assert forgot.status_code == 404
    forgot = api.post(f"/api/web/clients/{client}/ai-facts/forget", headers=auth, json={"key": "region"}).json()
    assert forgot["ai_facts"] == []
    workspaces.remember_ai_fact(user, ws, key="deadline", question="Срок?", answer="Неделя", scope="client")
    out = api.post(f"/api/web/workspaces/{sibling}/ai-facts/forget", headers=auth,
                   json={"key": "deadline", "scope": "client"}).json()
    assert out["client_ai_facts"] == []
