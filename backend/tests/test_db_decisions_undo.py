"""«Сделать иначе» (причина отказа уходит ИИ, и он переделывает) и «Вернуть как было» (все записи журнала
решения ИИ разом) на настоящем Postgres; модель и Директ подменяются."""

import json

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import actions, ai, ai_agent, direct_api, keywords, undo, workspaces
from app.journal import ai_action_logs, for_ai_action, list_action_logs, log_action
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user
from tests.test_db_ai import FakeModel, _call, _secret, _seed, _start, _text  # noqa: F401 — _secret: автофикстура


def _calls(*items):
    """Одно сообщение ИИ с несколькими вызовами — одна пачка предложений."""
    return {"content": "", "tool_calls": [{"id": cid, "name": name, "arguments": json.dumps(args)} for cid, name, args in items]}


def _cards(s):
    return ai_agent.get_chat(s["user"], s["chat"])["actions"]


def _status(s):
    return ai_agent.get_chat(s["user"], s["chat"])["chat"]["status"]


def _propose(s, monkeypatch, args, text="Останови"):
    _start(s["user"], s["chat"], text, monkeypatch, _call("campaigns_action", args))
    return _cards(s)[-1]


# --- «Сделать иначе» ------------------------------------------------------------------------------


def test_decline_with_reason_goes_to_model(db, monkeypatch):
    s = _seed(db)
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["search"]], "action": "pause", "reason": "дорого"})
    with pytest.raises(YandexError) as err:
        ai_agent.decide_action(s["user"], card["id"], approve=False, reason="Надоело")
    assert err.value.code == "args" and _cards(s)[0]["status"] == "pending"

    out = ai_agent.decide_action(s["user"], card["id"], approve=False, reason="Слишком резко", comment="сначала снизь ставки")
    assert out["action"]["status"] == "rejected" and out["action"]["decline_reason"] == "Слишком резко: сначала снизь ставки"
    assert out["job"]["kind"] == "ai_chat" and "remembered" not in out and _status(s) == "running"

    fake = FakeModel(_text("Понял — предлагаю снизить ставки на 10 %."))
    monkeypatch.setattr(ai, "complete", fake)
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"
    reply = json.loads(fake.calls[0][-1]["content"])
    assert reply["error"] == "Пользователь отклонил действие: Слишком резко: сначала снизь ставки"
    assert _cards(s)[0]["decline_reason"] == "Слишком резко: сначала снизь ставки"


def test_plain_reject_waits_unless_batch_goes_on(db, monkeypatch):
    s = _seed(db)
    done = []
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: done.append(kw) or {"applied": 1})
    pause = {"campaign_row_ids": [s["search"]], "action": "pause"}
    stop = {"campaign_row_ids": [s["network"]], "action": "pause"}
    _start(s["user"], s["chat"], "Останови обе", monkeypatch, _calls(("c1", "campaigns_action", pause), ("c2", "campaigns_action", stop)))
    first, second = _cards(s)
    assert ai_agent.decide_action(s["user"], first["id"], approve=True)["job"] is None  # второе ещё ждёт
    # Последнее отклонено просто, но первое из той же пачки одобрено — ход нужен: его выполнит ИИ.
    out = ai_agent.decide_action(s["user"], second["id"], approve=False)
    assert out["job"]["kind"] == "ai_chat" and out["action"]["decline_reason"] == ""
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Остановил «Поиск».")))
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"
    assert [c["status"] for c in _cards(s)] == ["done", "rejected"] and len(done) == 1


def test_redo_under_rejected_card(db, monkeypatch):
    s = _seed(db)
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["network"]], "action": "delete"}, "Удали РСЯ")
    plain = ai_agent.decide_action(s["user"], card["id"], approve=False)
    assert plain["job"] is None and _status(s) == "idle"

    # «Сделать иначе» под уже отклонённой карточкой: причина заменяет ответ на её вызов, ход начинается.
    out = ai_agent.decide_action(s["user"], card["id"], approve=False, comment="Лучше просто останови")
    assert out["job"]["kind"] == "ai_chat" and out["action"]["decline_reason"] == "Лучше просто останови"
    with pytest.raises(YandexError) as err:
        ai_agent.decide_action(s["user"], card["id"], approve=False, reason="Не то")
    assert err.value.code == "decided"
    fake = FakeModel(_text("Тогда останавливаю, а не удаляю."))
    monkeypatch.setattr(ai, "complete", fake)
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"
    answers = [m for m in fake.calls[0] if m["role"] == "tool"]
    assert len(answers) == 1 and json.loads(answers[0]["content"])["reason"] == "Лучше просто останови"


def test_redo_too_late_after_conversation_moved_on(db, monkeypatch):
    s = _seed(db)
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["network"]], "action": "delete"}, "Удали РСЯ")
    ai_agent.decide_action(s["user"], card["id"], approve=False)
    _start(s["user"], s["chat"], "Ладно, что с расходом?", monkeypatch, _text("Расход ровный."))
    with pytest.raises(YandexError) as err:
        ai_agent.decide_action(s["user"], card["id"], approve=False, reason="Не то")
    assert err.value.code == "args" and "ушёл дальше" in err.value.message
    assert _cards(s)[0]["decline_reason"] == ""


def test_no_touch_remembers_campaign(db, monkeypatch):
    s = _seed(db)
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["network"]], "action": "pause"})
    out = ai_agent.decide_action(s["user"], card["id"], approve=False, reason=ai_agent.NO_TOUCH_REASON)
    key = f"no_touch_campaign_{s['network']}"
    assert out["remembered"] == [key] and out["job"]["kind"] == "ai_chat"
    fact = next(f for f in workspaces.get_workspace(s["user"], s["ws"])["ai_facts"] if f["key"] == key)
    assert "«РСЯ»" in fact["question"] and fact["answer"].startswith("Нет")


def _as_card(s, monkeypatch, tool, args):
    """Карточка с нужным инструментом и аргументами (подготовка инструмента тут не важна — важны id в args)."""
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["search"]], "action": "pause"})
    with s["db"].connection() as conn:
        conn.execute("UPDATE ai_actions SET tool = %s, args = %s WHERE id = %s", (tool, Jsonb(args), card["id"]))
    return card


def _decline_note(s, call_id="c1"):
    with s["db"].connection() as conn:
        content = conn.execute(
            "SELECT content FROM ai_messages WHERE chat_id = %s AND role = 'tool' AND tool_call_id = %s ORDER BY id DESC LIMIT 1",
            (s["chat"], call_id),
        ).fetchone()["content"]
    return json.loads(content)["note"]


def _facts(s):
    return [f["key"] for f in workspaces.get_workspace(s["user"], s["ws"])["ai_facts"] if f["key"].startswith("no_touch_")]


def test_no_touch_finds_campaign_in_nested_args(db, monkeypatch):
    """update_text_ads: объявления — в items[]; служебный _diff карточки не считается."""
    s = {**_seed(db), "db": db}
    with db.connection() as conn:
        group = conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name) VALUES (%s, %s, %s, 9001, 'Г') RETURNING id",
            (s["user"], s["acc"], s["network"]),
        ).fetchone()["id"]
        ad = conn.execute(
            "INSERT INTO direct_ads (user_id, account_id, adgroup_row_id, ad_id) VALUES (%s, %s, %s, 7001) RETURNING id",
            (s["user"], s["acc"], group),
        ).fetchone()["id"]
    args = {"items": [{"ad_row_id": ad, "title": "Кухни на заказ"}], "_diff": [{"campaign_row_id": s["search"]}]}
    card = _as_card(s, monkeypatch, "update_text_ads", args)
    out = ai_agent.decide_action(s["user"], card["id"], approve=False, reason=ai_agent.NO_TOUCH_REASON)
    key = f"no_touch_campaign_{s['network']}"
    assert out["remembered"] == [key] and _facts(s) == [key] and out["job"]["kind"] == "ai_chat"
    note = _decline_note(s)
    assert "«РСЯ»" in note and "скажи, что запомнил" in note


def test_no_touch_experiment_and_nothing_found(db, monkeypatch):
    s = {**_seed(db), "db": db}
    with db.connection() as conn:
        exp = conn.execute(
            """
            INSERT INTO ai_experiments (user_id, workspace_id, started_on, days, criterion, campaign_row_ids, source_campaign_row_id)
            VALUES (%s, %s, CURRENT_DATE, 14, 'cr', %s, %s) RETURNING id
            """,
            (s["user"], s["ws"], Jsonb([s["network"]]), s["search"]),
        ).fetchone()["id"]
    # finish_experiment: только experiment_id — кампании берутся из эксперимента (тестовые, не образец).
    card = _as_card(s, monkeypatch, "finish_experiment", {"experiment_id": exp, "verdict": "fail"})
    out = ai_agent.decide_action(s["user"], card["id"], approve=False, reason=ai_agent.NO_TOUCH_REASON)
    assert out["remembered"] == [f"no_touch_campaign_{s['network']}"]
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Понял, РСЯ не трогаю.")))
    ai_agent.run_chat(s["user"], s["chat"])

    # Кампанию не понять — ничего не записано, и модели не велят говорить «запомнил».
    card = _as_card(s, monkeypatch, "update_text_ads", {"items": [{"ad_row_id": 999999}]})
    out = ai_agent.decide_action(s["user"], card["id"], approve=False, reason=ai_agent.NO_TOUCH_REASON)
    assert out["remembered"] == [] and out["job"]["kind"] == "ai_chat"
    assert _facts(s) == [f"no_touch_campaign_{s['network']}"]
    note = _decline_note(s)
    assert "Не говори, что запомнил" in note and "скажи, что запомнил" not in note


def test_no_touch_save_failure_is_told_to_model(db, monkeypatch):
    s = {**_seed(db), "db": db}
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["network"]], "action": "pause"})

    def full(*_a, **_k):
        raise YandexError("facts-full", "Память полна")

    monkeypatch.setattr(ai_agent, "remember_ai_facts", full)
    out = ai_agent.decide_action(s["user"], card["id"], approve=False, reason=ai_agent.NO_TOUCH_REASON)
    assert out["remembered"] == [] and out["job"]["kind"] == "ai_chat"
    assert "Не говори, что запомнил" in _decline_note(s)


def test_redo_of_detached_card_starts_turn(db, monkeypatch):
    """«Сделать иначе» у отложенной карточки: ИИ не ждёт следующего сообщения, а сразу переделывает."""
    s = _seed(db)
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["network"]], "action": "pause"}, "Останови РСЯ")
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Расход ровный.")))
    ai_agent.post_message(s["user"], s["chat"], "Сначала покажи расход", pending="keep")
    ai_agent.run_chat(s["user"], s["chat"])
    assert _cards(s)[0]["detached"] and _status(s) == "idle"

    out = ai_agent.decide_action(s["user"], card["id"], approve=False, reason="Не то", comment="лучше снизь ставки")
    assert out["job"]["kind"] == "ai_chat" and _status(s) == "running"
    fake = FakeModel(_text("Тогда снижу ставки на 10 %."))
    monkeypatch.setattr(ai, "complete", fake)
    assert ai_agent.run_chat(s["user"], s["chat"])["status"] == "idle"
    last = fake.calls[0][-1]
    assert last["role"] == "user" and "Служебная заметка" in last["content"] and "лучше снизь ставки" in last["content"]


# --- «Вернуть как было» в карточке ИИ --------------------------------------------------------------


def _approved_with_two_logs(s, monkeypatch):
    """Выполненное предложение, оставившее две записи с откатом (по записи на кампанию)."""
    made = []

    def bulk(user_id, **kw):
        for rid in kw["campaign_ids"]:
            step = {"op": "campaigns_action", "args": {"campaign_ids": [rid], "action": "resume"}}
            made.append((log_action(user_id, "pause", {"campaign_ids": [rid]}, {"applied": 1}, "ok", undo={"label": "Включить", "steps": [step]}), rid))
        return {"applied": len(kw["campaign_ids"])}

    monkeypatch.setattr(actions, "bulk_campaign_action", bulk)
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["search"], s["network"]], "action": "pause"}, "Останови обе")
    ai_agent.decide_action(s["user"], card["id"], approve=True)
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Остановил обе.")))
    ai_agent.run_chat(s["user"], s["chat"])
    return card["id"], made


def test_ai_card_returns_everything(db, monkeypatch):
    s = _seed(db)
    action_id, made = _approved_with_two_logs(s, monkeypatch)
    assert len(made) == 2
    card = _cards(s)[0]
    assert card["status"] == "done" and card["undo"] == {"entries": 2, "undoable": 2, "undone": 0}

    preview = undo.ai_action_undo_preview(s["user"], action_id)
    assert preview["available"] and preview["label"] == "Вернуть как было" and preview["title"] == "Действие ИИ"
    assert preview["log_ids"] == [i for i, _rid in made] and len(preview["entries"]) == 2
    assert all(e["available"] and e["steps"] for e in preview["entries"])
    # Записи одного решения друг другу не «позже»: предупреждения о более поздних изменениях нет.
    assert preview["later"] == [] and preview["warnings"] == []

    seen = []
    monkeypatch.setitem(undo.OPS, "campaigns_action", lambda user_id, args: seen.append(args["campaign_ids"]) or {"applied": 1})
    out = undo.run_undo_group(s["user"], preview["log_ids"])
    # От новой к старой — значения возвращаются на момент до первой записи.
    assert out["status"] == "ok" and out["undone"] == [i for i, _rid in reversed(made)]
    assert seen == [[rid] for _i, rid in reversed(made)] and len(out["log_ids"]) == 2
    # Откаты копируют цели (и метку предложения), но записями решения не считаются.
    assert _cards(s)[0]["undo"] == {"entries": 2, "undoable": 0, "undone": 2}
    assert len(ai_action_logs(s["user"], [action_id])[action_id]) == 2
    again = undo.ai_action_undo_preview(s["user"], action_id)
    assert not again["available"] and again["reason"] == "Это уже вернули"

    with pytest.raises(YandexError) as err:
        undo.ai_action_undo_preview(make_user(db, "other@example.com"), action_id)
    assert err.value.code == "not-found"


def test_ai_card_without_journal_entries(db, monkeypatch):
    s = _seed(db)
    monkeypatch.setattr(actions, "bulk_campaign_action", lambda user_id, **kw: {"applied": 1})
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["search"]], "action": "pause"})
    ai_agent.decide_action(s["user"], card["id"], approve=True)
    monkeypatch.setattr(ai, "complete", FakeModel(_text("Готово.")))
    ai_agent.run_chat(s["user"], s["chat"])
    assert "undo" not in _cards(s)[0]
    preview = undo.ai_action_undo_preview(s["user"], card["id"])
    assert not preview["available"] and preview["reason"].startswith("Это вернуть нельзя")


# --- Несколько записей разом -------------------------------------------------------------------------


@pytest.fixture
def env(db):
    user = make_user(db, "u@example.com")
    acc = make_account(db, user, make_connection(db, user))
    a = make_campaign(db, user, acc, 101, "Кухни")
    b = make_campaign(db, user, acc, 102, "Шкафы")
    return {"db": db, "user": user, "acc": acc, "a": a, "b": b}


def _log(env, rid, *, op="campaigns_action", status="ok", steps=True, title="Остановка"):
    undo_ = {"label": "Вернуть", "steps": [{"op": op, "args": {"campaign_ids": [rid], "action": "resume"}}]} if steps else None
    return log_action(env["user"], "pause", {"campaign_ids": [rid]}, {"applied": 1}, status, title=title, undo=undo_)


def test_group_names_what_cannot_be_undone(env, monkeypatch):
    first = _log(env, env["a"], title="Остановка «Кухни»")
    no_undo = _log(env, env["a"], steps=False, title="Пауза без отката")
    budget = _log(env, env["b"], op="campaign_budgets", title="Бюджет «Шкафы»")
    failed = _log(env, env["b"], status="error", title="Не вышло")
    ids = [failed, budget, no_undo, first]

    preview = undo.undo_group_preview(env["user"], ids)
    assert preview["available"] and preview["log_ids"] == [first, budget] and preview["title"] == "Изменений: 4"
    assert preview["blocked"] == [
        "«Пауза без отката» — у этого действия нет отката",
        "«Не вышло» — действие не выполнилось — откатывать нечего",
    ]
    assert [e["available"] for e in preview["entries"]] == [True, False, True, False]

    # Откат одной записи не вышел — остальные возвращены, а она снова доступна.
    monkeypatch.setitem(undo.OPS, "campaigns_action", lambda user_id, args: {"applied": 1})

    def broken(user_id, args):
        raise YandexError("api", "Директ не ответил")

    monkeypatch.setitem(undo.OPS, "campaign_budgets", broken)
    out = undo.run_undo_group(env["user"], ids)
    assert out["status"] == "partial" and out["undone"] == [first]
    assert [(f["log_id"], f["message"]) for f in out["failed"]] == [(budget, "Директ не ответил")]
    after = undo.undo_group_preview(env["user"], ids)
    assert after["log_ids"] == [budget] and after["undone"] == ["Остановка «Кухни»"]

    blocked = undo.undo_group_preview(env["user"], [no_undo, first])
    assert not blocked["available"] and blocked["reason"].startswith("Это вернуть нельзя: «Пауза без отката»")
    with pytest.raises(YandexError) as err:
        undo.undo_group_preview(make_user(env["db"], "other@example.com"), ids)
    assert err.value.code == "not-found"


def test_campaign_history_filter(env):
    with env["db"].connection() as conn:
        group = conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name) VALUES (%s, %s, %s, 9001, 'Г') RETURNING id",
            (env["user"], env["acc"], env["a"]),
        ).fetchone()["id"]
    own = _log(env, env["a"])
    in_group = log_action(env["user"], "adgroup_update", {"adgroup_row_id": group}, {}, "ok", targets=[f"adgroup:{group}"])
    sites = log_action(
        env["user"], "exclude_sites", {}, {"items": [{"campaign_row_id": env["a"], "sites": ["x.ru"]}]}, "ok", targets=[]
    )
    _log(env, env["b"])
    log_action(env["user"], "exclude_sites", {}, {"items": [{"campaign_row_id": env["b"], "sites": ["y.ru"]}]}, "ok", targets=[])

    got = [r["id"] for r in list_action_logs(env["user"], campaign_row_id=env["a"])]
    assert got == [sites, in_group, own]
    assert len(list_action_logs(env["user"])) == 5


def test_deleted_keyword_stays_in_campaign_history(env, monkeypatch):
    """Удалённой фразы в таблицах уже нет — запись и её откат находятся по кампании из параметров."""
    with env["db"].connection() as conn:
        group = conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name) VALUES (%s, %s, %s, 9001, 'Г') RETURNING id",
            (env["user"], env["acc"], env["a"]),
        ).fetchone()["id"]
        kw = conn.execute(
            """
            INSERT INTO direct_keywords (user_id, account_id, adgroup_row_id, keyword_id, keyword, state)
            VALUES (%s, %s, %s, 5001, 'кухни на заказ', 'ON') RETURNING id
            """,
            (env["user"], env["acc"], group),
        ).fetchone()["id"]
    monkeypatch.setattr(keywords, "get_connection_access_token", lambda *a: "token")
    monkeypatch.setattr(keywords, "_call", lambda *a: {})
    monkeypatch.setattr(direct_api, "item_errors", lambda *a: [])
    assert keywords.bulk_keyword_action(env["user"], keyword_row_ids=[kw], action="delete")["applied"] == 1
    _log(env, env["b"])
    # Кампания 1 и 10 не путаются с 101-й и т. п.: id в параметрах сверяется целиком.
    log_action(env["user"], "keywords", {"campaign_row_ids": [env["a"] * 10 + 1], "action": "delete"}, {}, "ok", targets=[])

    [entry] = list_action_logs(env["user"], campaign_row_id=env["a"])
    assert entry["title"].startswith("Удаление фраз") and entry["undo"]["label"] == "Добавить фразы заново"
    assert all(r["id"] != entry["id"] for r in list_action_logs(env["user"], campaign_row_id=env["b"]))

    recreated = []
    monkeypatch.setitem(undo.OPS, "keywords_recreate", lambda user_id, args: recreated.append(args) or {"added": 1})
    back = undo.run_undo(env["user"], entry["id"])
    assert back["status"] == "ok" and recreated[0]["items"][0]["keyword"] == "кухни на заказ"
    history = [r["id"] for r in list_action_logs(env["user"], campaign_row_id=env["a"])]
    assert history == [back["log_id"], entry["id"]]


def test_ai_card_returns_more_than_window(db, monkeypatch):
    """Решение ИИ с записями больше окна по списку: «Вернуть как было» возвращает все, а не 50 старых."""
    s = _seed(db)
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["search"]], "action": "pause"})
    total = undo.MAX_GROUP + 5
    with for_ai_action(card["id"]):
        made = [
            log_action(
                s["user"],
                "pause",
                {"campaign_ids": [s["search"]]},
                {"applied": 1},
                "ok",
                title=f"Шаг {n}",
                undo={"label": "Вернуть", "steps": [{"op": "campaigns_action", "args": {"campaign_ids": [s["search"]], "action": "resume"}}]},
            )
            for n in range(total)
        ]
    preview = undo.ai_action_undo_preview(s["user"], card["id"])
    assert preview["available"] and preview["log_ids"] == made and len(preview["entries"]) == total
    seen = []
    monkeypatch.setitem(undo.OPS, "campaigns_action", lambda user_id, args: seen.append(1) or {"applied": 1})
    out = undo.run_undo_group(s["user"], preview["log_ids"])
    assert out["status"] == "ok" and out["undone"] == list(reversed(made)) and len(seen) == total
    assert undo.ai_action_undo_preview(s["user"], card["id"])["reason"] == "Это уже вернули"

    # Окно по произвольному списку записей не режет его молча: больше 50 — понятная ошибка.
    with pytest.raises(YandexError) as err:
        undo.undo_group_preview(s["user"], made)
    assert err.value.code == "args" and "Слишком много" in err.value.message


def test_undo_api(env, monkeypatch):
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(env['user'])}"}
    other = {"Authorization": f"Bearer {make_session(make_user(env['db'], 'x@example.com'))}"}
    first = _log(env, env["a"])
    second = _log(env, env["b"])
    no_undo = _log(env, env["a"], steps=False)

    preview = client.get(f"/api/web/actions/undo-group?ids={first},{second},{no_undo}", headers=auth).json()
    assert preview["log_ids"] == [first, second] and len(preview["blocked"]) == 1
    assert client.get(f"/api/web/actions/undo-group?ids={first}", headers=other).status_code == 404
    assert client.get("/api/web/actions/undo-group?ids=x", headers=auth).status_code == 400
    bad = client.post("/api/web/actions/undo-group", headers=auth, json={"ids": [no_undo]})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "undo"
    job = client.post("/api/web/actions/undo-group", headers=auth, json={"ids": [first, second]}).json()["job"]
    assert job["kind"] == "undo_action"
    with env["db"].connection() as conn:
        payload = conn.execute("SELECT payload FROM jobs WHERE id = %s", (job["id"],)).fetchone()["payload"]
    assert payload == {"log_ids": [first, second]}

    items = client.get(f"/api/web/actions?campaign_row_id={env['b']}", headers=auth).json()["items"]
    assert [i["id"] for i in items] == [second]


def test_ai_undo_api(db, monkeypatch):
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    action_id, made = _approved_with_two_logs(s, monkeypatch)
    preview = client.get(f"/api/web/ai/actions/{action_id}/undo", headers=auth).json()
    assert preview["available"] and preview["log_ids"] == [i for i, _rid in made]
    job = client.post(f"/api/web/ai/actions/{action_id}/undo", headers=auth, json={}).json()["job"]
    assert job["kind"] == "undo_action"
    other = {"Authorization": f"Bearer {make_session(make_user(db, 'x@example.com'))}"}
    assert client.post(f"/api/web/ai/actions/{action_id}/undo", headers=other, json={}).status_code == 404

    # «Сделать иначе» через API: причина и «как надо» в теле решения.
    card = _propose(s, monkeypatch, {"campaign_row_ids": [s["network"]], "action": "pause"}, "А РСЯ?")
    decided = client.post(
        f"/api/web/ai/actions/{card['id']}/decide", headers=auth, json={"approve": False, "reason": "Сейчас не время"}
    ).json()
    assert decided["action"]["decline_reason"] == "Сейчас не время" and decided["job"]["kind"] == "ai_chat"
    empty = client.post(f"/api/web/ai/actions/{card['id']}/undo", headers=auth, json={})
    assert empty.status_code == 400 and empty.json()["detail"]["code"] == "undo"
