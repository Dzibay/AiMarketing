"""Сбой и частичный успех долгих операций: разбор результата, заголовки, «Повторить», коды ошибок словами."""

from app import operation_notify as op
from app.jobs import PRIORITY_BACKGROUND, PRIORITY_USER, public_job
from app.plain_errors import GENERIC, plain_error


def _job(kind="bulk_budget", payload=None, priority=PRIORITY_USER, **extra):
    return {"id": 7, "user_id": 3, "kind": kind, "payload": payload or {}, "priority": priority, **extra}


# ---------------------------------------------------------------- коды словами


def test_plain_error_codes_and_messages():
    assert plain_error("token-expired") == "Доступ к Директу истёк — подключите аккаунт заново"
    assert plain_error("units") == "Закончились баллы API Директа — повторите позже"
    # Текст Директа — как есть, без номера кода и точки в конце.
    assert plain_error("direct", "Кампания в архиве.") == "Кампания в архиве"
    assert plain_error("direct-denied|Незавершенная регистрация (ошибка 58). Создайте заявку") == (
        "Незавершенная регистрация. Создайте заявку"
    )
    # str(исключения) и служебный текст воркера — не для кабинета.
    assert "сбой на нашей стороне" in plain_error("internal", "KeyError: 'x'").lower()
    assert plain_error("stale", "Воркер перестал отвечать").startswith("Задача прервалась")
    # Английский технический текст не показываем — расшифровка кода.
    assert plain_error("direct", "HTTP 502") == "Директ не прислал причину"
    assert plain_error("weird-code") == GENERIC[:1].upper() + GENERIC[1:]
    assert plain_error("", "") == GENERIC[:1].upper() + GENERIC[1:]


def test_plain_error_ai_provider_uses_ai_texts():
    from app import ai

    assert plain_error("ai-json", "") == ai.ERROR_FORMAT.rstrip(".")


def test_public_job_translates_bare_codes():
    row = {
        "id": 1, "kind": "bulk_budget", "status": "error", "attempts": 1, "max_attempts": 1,
        "progress_done": 0, "progress_total": 0, "progress_text": "", "error_code": "stale",
        "error": "Воркер перестал отвечать",
    }
    assert public_job(row)["error"].startswith("Задача прервалась")
    assert public_job({**row, "error_code": "units", "error": ""})["error"].startswith("Закончились баллы")
    # Русский текст сервиса остаётся как был.
    assert public_job({**row, "error_code": "limit", "error": "За раз — до 100 кампаний"})["error"] == "За раз — до 100 кампаний"


# ---------------------------------------------------------------- чьи задачи


def test_only_user_started_operations():
    assert op.is_operation(_job())
    assert not op.is_operation(_job(priority=PRIORITY_BACKGROUND))  # расписание
    assert not op.is_operation(_job(kind="load_account"))  # у загрузок своя запись
    assert not op.is_operation(_job(kind="ai_chat"))
    assert not op.is_operation(_job(kind="urgent_check"))
    assert not op.is_operation({**_job(), "user_id": None})


def test_dedup_key_by_object():
    assert op.dedup_key("apply_negative_list", {"list_id": 5, "campaign_row_ids": [1]}) == "op:apply_negative_list:list_id=5"
    assert op.dedup_key("classify_all_queries", {}) == "op:classify_all_queries"
    assert op.dedup_key("duplicate_campaign", {"campaign_row_id": 5, "workspace_id": 2}) == (
        "op:duplicate_campaign:campaign_row_id=5"
    )


def test_dedup_key_by_target_set_inside_project():
    # Бюджеты A и B и бюджет C одного проекта — разные записи: удача с C не гасит сбой B и не затирает его кнопку.
    ab = op.dedup_key("bulk_budget", {"workspace_id": 2, "campaign_ids": [1, 2], "weekly_budget_rub": 900})
    c = op.dedup_key("bulk_budget", {"workspace_id": 2, "campaign_ids": [3], "weekly_budget_rub": 900})
    assert ab.startswith("op:bulk_budget:workspace_id=2:") and ab != c
    # Та же задача ещё раз (кнопкой или ИИ) — та же запись.
    assert ab == op.dedup_key(
        "bulk_budget", {"weekly_budget_rub": 900, "campaign_ids": [1, 2], "workspace_id": 2, "source": "ai"}
    )
    assert op.dedup_key("classify_queries", {"workspace_id": 2}) == "op:classify_queries:workspace_id=2"
    assert op.dedup_key("fix_links", {"campaign_ids": [1]}) != op.dedup_key("fix_links", {"campaign_ids": [2]})


def test_negative_list_account_error_not_double_counted():
    # 3 из 10 уже были привязаны до сбоя кабинета: не дошли 7, всего 10, а не 13.
    nl = {"linked": 3, "detached": 0, "skipped": [], "status": "partial",
          "errors": [{"client_login": "a", "error": "units", "campaigns": 7}]}
    assert op.counts("apply_negative_list", nl)[:2] == (3, 10)
    # Поштучные ошибки (без «campaigns») — по одной кампании; явный 0 — ноль.
    nl = {"linked": 1, "detached": 0, "skipped": [{"campaign_row_id": 9}], "status": "partial",
          "errors": [{"campaign_row_id": 2, "error": "direct"}, {"client_login": "a", "error": "units", "campaigns": 0}]}
    assert op.counts("apply_negative_list", nl)[:2] == (1, 3)


def test_stale_rerun_only_when_safe():
    assert op._safe_to_rerun("bulk_budget", {"campaign_ids": [1], "weekly_budget_rub": 900})
    assert not op._safe_to_rerun("bulk_budget", {"campaign_ids": [1], "budget_change_pct": 10})
    assert not op._safe_to_rerun("bulk_budget", {"campaign_ids": [1], "target_change_pct": -5})
    assert not op._safe_to_rerun("duplicate_campaign", {"campaign_row_id": 1})
    assert op._safe_to_rerun("apply_negative_list", {"list_id": 1})


# ---------------------------------------------------------------- разбор результата


def test_outcome_and_counts():
    nl = {"linked": 7, "detached": 1, "skipped": [], "status": "partial",
          "errors": [{"client_login": "b", "error": "units", "campaigns": 2}]}
    assert op.outcome("apply_negative_list", nl) == "partial"
    assert op.counts("apply_negative_list", nl)[:2] == (8, 10)

    bb = {"items": [{"campaign_row_id": 1}], "errors": [{"campaign_row_id": 2, "error": "direct", "message": ""}],
          "status": "partial"}
    assert op.counts("bulk_budget", bb)[:2] == (1, 2)

    links = {"items": [{"campaign_row_id": 1, "tracking_fixed": True},
                       {"campaign_row_id": 2, "status": "error", "errors": ["Кампания в архиве"]}]}
    assert op.outcome("fix_links", links) == "partial"
    assert op.counts("fix_links", links)[:2] == (1, 2)
    assert op.first_reason(links) == "Кампания в архиве"

    decisions = {"negative": 3, "target": 1, "errors": 2, "skipped": 0, "status": "partial"}
    assert op.counts("apply_query_decisions", decisions)[:2] == (4, 6)
    assert op.outcome("slice_report", {"rows": []}) == "ok"
    assert op.outcome("bulk_budget", None) == "ok"


def test_first_reason_translates_codes():
    assert op.first_reason({"errors": [{"error": "token-expired", "message": ""}]}).startswith("Доступ к Директу истёк")
    assert op.first_reason({"warnings": ["Структура скопирована не полностью: Директ не ответил"]}).startswith("Структура")
    assert op.first_reason({"errors": []}) == ""


def test_retry_payload_rules():
    payload = {"campaign_ids": [1, 2, 3], "budget_change_pct": 10, "workspace_id": 4}
    result = {"items": [{"campaign_row_id": 1}], "errors": [
        {"campaign_row_id": 2, "error": "direct"}, {"campaign_row_id": 3, "error": "units"}], "status": "partial"}
    again, label = op.retry_payload("bulk_budget", payload, result, "partial")
    # Только упавшие: «+10 %» прошедшим второй раз не прибавляем.
    assert again == {**payload, "campaign_ids": [2, 3]} and label == op.RETRY_REST
    assert op.retry_payload("bulk_budget", payload, None, "error") == (payload, op.RETRY_AGAIN)

    nl = {"list_id": 5, "campaign_row_ids": [1, 2]}
    assert op.retry_payload("apply_negative_list", nl, {"status": "partial"}, "partial") == (nl, op.RETRY_REST)
    # Повтор безопасен только целиком — частичный успех без кнопки.
    assert op.retry_payload("slice_adjust", {"a": 1}, {"status": "partial"}, "partial") is None
    assert op.retry_payload("slice_adjust", {"a": 1}, None, "error") == ({"a": 1}, op.RETRY_AGAIN)
    # Повтор задвоил бы изменения.
    for kind in ("ads_ab", "duplicate_campaign", "apply_query_decisions", "undo_action"):
        assert op.retry_payload(kind, {}, None, "error") is None


# ---------------------------------------------------------------- текст записи


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(op, "_send", lambda job, title, body, severity, log_ids, retry: sent.append((title, body, severity, retry)))
    monkeypatch.setattr(op, "_subject", lambda kind, user_id, payload: op.OPERATIONS[kind]["title"])
    resolved = []
    monkeypatch.setattr(op, "resolve", lambda user_id, kind, payload: resolved.append(kind))
    return sent, resolved


def test_partial_notice_text(monkeypatch):
    sent, resolved = _capture(monkeypatch)
    result = {"linked": 8, "detached": 0, "skipped": [], "status": "partial",
              "errors": [{"campaign_row_id": 9, "error": "direct", "message": "Кампания в архиве"},
                         {"campaign_row_id": 10, "error": "direct", "message": ""}]}
    op.notify_result(_job("apply_negative_list", {"list_id": 1, "campaign_row_ids": [1]}), result, [])
    title, body, severity, retry = sent[0]
    assert title == "Список минус-фраз: применилось к 8 из 10 кампаний"
    assert body == "Причина: Кампания в архиве."
    assert severity == "warn" and retry[1] == op.RETRY_REST
    assert resolved == []


def test_ok_result_resolves_and_failure_text(monkeypatch):
    sent, resolved = _capture(monkeypatch)
    op.notify_result(_job(), {"items": [], "errors": [], "status": "ok"}, [])
    assert sent == [] and resolved == ["bulk_budget"]

    op.notify_failed(_job("duplicate_campaign"), "internal", "KeyError: 'x'", [])
    title, body, severity, retry = sent[0]
    assert title == "Копирование кампании: не получилось"
    assert "KeyError" not in body and "сбой на нашей стороне" in body.lower()
    assert severity == "error" and retry is None


def test_partial_without_counts(monkeypatch):
    sent, _ = _capture(monkeypatch)
    op.notify_result(_job("duplicate_campaign"), {"status": "partial", "warnings": ["Структура скопирована не полностью"]}, [])
    assert sent[0][0] == "Копирование кампании: выполнено частично"
    assert sent[0][1] == "Причина: Структура скопирована не полностью."


def test_plural():
    assert op._plural(1, op._CAMPAIGNS) == "кампании"
    assert op._plural(21, op._CAMPAIGNS) == "кампании"
    assert op._plural(10, op._CAMPAIGNS) == "кампаний"
    assert op._plural(3, op._CAMPAIGNS) == "кампаний"


def test_plain_error_direct_numeric_codes():
    from app.plain_errors import plain_error

    assert plain_error(8800, "Object not found") == "Не найдено в Директе — возможно, удалено"
    assert plain_error("152", "") == "Закончились баллы API Директа — повторите позже"
    assert plain_error(8800, "Объект не найден (ошибка 8800)") == "Объект не найден"
