"""История изменений: группы действий, запись без битого JSON и строки «было → стало» словами."""

import json

from app.journal import (
    _LOST_REASON,
    _NO_REASON,
    ACTION_KINDS,
    ACTION_TITLES,
    _dump,
    _log_out,
    _parse,
    _size,
)

CARDS = {
    1: {"id": 1, "name": "Окна", "workspace_id": 5, "direct_id": 111},
    2: {"id": 2, "name": "Двери", "workspace_id": 5, "direct_id": 222},
    3: {"id": 3, "name": "Балконы", "workspace_id": None, "direct_id": 333},
}


def _row(action, payload, result, status="ok", undo=None, *, raw=False):
    """Строка action_logs, как её отдаёт база: payload и result — текст."""
    return {
        "id": 1,
        "action": action,
        "title": None,
        "source": "user",
        "workspace_id": None,
        "payload": json.dumps(payload, ensure_ascii=False),
        "result": result if raw else _dump(result),
        "status": status,
        "created_at": None,
        "undo": undo,
        "undone_at": None,
        "undone_by": None,
        "undo_of": None,
        "targets": [],
    }


def _out(*args, **kw) -> dict:
    out = _log_out(_row(*args, **kw), CARDS)
    # Числа и «₽» склеены неразрывным пробелом — в проверках сравниваем с обычным.
    for key in ("changes", "problems"):
        out[key] = [line.replace("\u00a0", " ") for line in out[key]]
    return out


def _steps(*steps):
    return {"label": "Вернуть", "steps": [{"op": op, "args": args} for op, args in steps]}


def test_every_action_has_exactly_one_kind():
    grouped = [a for actions in ACTION_KINDS.values() for a in actions]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(ACTION_TITLES)


# --- запись: всегда целый JSON ---------------------------------------------------------------


def test_dump_small_value_unchanged():
    value = {"applied": 2, "errors": []}
    assert _dump(value) == json.dumps(value, ensure_ascii=False)


def test_dump_big_result_is_valid_json_and_keeps_errors_whole():
    errors = [
        {"campaign_row_id": i, "name": f"Кампания {i}", "error": "direct", "message": "Фраза слишком длинная " + "я" * 700}
        for i in range(6)
    ]
    big = {"applied": 494, "details": [{"campaign_row_id": i, "data": "x" * 200} for i in range(500)], "errors": errors}
    text = _dump(big)
    data = json.loads(text)  # не падает: JSON целый
    assert len(text) < len(json.dumps(big, ensure_ascii=False)) // 5
    assert data["errors"] == errors  # ошибки — полностью
    assert data["applied"] == 494
    assert data["details"][-1].startswith("… и ещё ")
    assert _size(data["details"]) == 500  # по хвосту «… и ещё N» видно, сколько было


def test_dump_thousands_of_errors_still_bounded_and_counted():
    big = {"errors": [{"campaign_row_id": i, "message": f"Ошибка {i}"} for i in range(3000)], "applied": 0}
    text = _dump(big)
    data = json.loads(text)
    assert len(text) < 40_000
    assert data["errors"][0] == {"campaign_row_id": 0, "message": "Ошибка 0"}
    assert _size(data["errors"]) == 3000


# --- старые записи, обрезанные по 4000 символов ----------------------------------------------


def _old_cut(result) -> str:
    return json.dumps(result, ensure_ascii=False)[:4000]


def test_old_cut_row_is_repaired_and_keeps_reason():
    result = {
        "applied": 8,
        "errors": [
            {"campaign_row_id": 1, "name": "Окна", "error": "direct", "message": "Фраза слишком длинная"},
            {"campaign_row_id": 2, "name": "Двери", "error": "direct", "message": "Фраза слишком длинная"},
        ],
        "details": [{"campaign_row_id": i, "data": "x" * 100} for i in range(60)],
    }
    value, cut = _parse(_old_cut(result))
    assert cut and isinstance(value, dict) and value["errors"] == result["errors"]
    out = _out("set_budget", {"campaign_ids": list(range(1, 11))}, _old_cut(result), "partial", raw=True)
    assert out["cut"] is True
    # «Сколько из скольких» в обрезанной записи честно не посчитать — только причина.
    assert out["problems"][0] == "Фраза слишком длинная («Окна», «Двери»)"
    assert not any(_NO_REASON in p or "не прислал причину" in p for p in out["problems"])


def test_old_cut_row_without_errors_says_reason_is_lost():
    result = {"applied": 8, "details": [{"data": "x" * 100} for i in range(60)], "errors": [{"message": "Нет денег"}]}
    out = _out("set_budget", {"campaign_ids": [1, 2]}, _old_cut(result), "partial", raw=True)
    assert out["problems"] == [_LOST_REASON]


def test_old_ascii_escaped_row_and_unparsable_text():
    result = {"errors": [{"campaign_row_id": 1, "message": "Кампания не найдена " + "я" * 400} for _ in range(20)]}
    old = json.dumps(result)[:4000]  # \uXXXX может оборваться посередине
    out = _out("pause", {"campaign_ids": [1]}, old, "error", raw=True)
    assert out["problems"][0].startswith("«Окна»: кампания не найдена")
    garbage = 'Traceback {"message": "Кампания не найдена", "x": [1, 2\\'
    out = _out("pause", {"campaign_ids": [1]}, garbage, "error", raw=True)
    assert out["problems"][0] == "Кампания не найдена"


# --- что не применилось ----------------------------------------------------------------------


def test_partial_errors_counted_with_reason_in_words():
    errors = [
        {"campaign_row_id": 1, "name": "Окна", "error": "direct", "message": "Фраза слишком длинная"},
        {"campaign_row_id": 2, "name": "Двери", "error": "direct", "message": "Фраза слишком длинная"},
    ]
    out = _out("set_budget", {"campaign_ids": list(range(1, 11)), "daily_budget_rub": 1500}, {"applied": 8, "errors": errors}, "partial")
    assert out["problems"] == ["2 из 10 не применились: фраза слишком длинная («Окна», «Двери»)"]


def test_different_reasons_and_codes_without_text():
    errors = [
        {"campaign_row_id": 1, "error": "budget", "message": "Сумма меньше минимальной"},
        {"campaign_row_id": 2, "error": "token-expired"},
        {"client_login": "shop", "error": "units"},
    ]
    out = _out("pause", {"campaign_ids": [1, 2, 3, 4]}, {"applied": 1, "errors": errors}, "partial")
    assert out["problems"] == [
        "3 из 4 не применились:",
        "Сумма меньше минимальной — «Окна»",
        "Доступ к Директу истёк — подключите аккаунт заново — «Двери»",
        "Закончились баллы API Директа — повторите позже — аккаунт shop",
    ]
    one = _out("pause", {"campaign_ids": [1]}, {"errors": [{"campaign_row_id": 1, "error": "archived"}]}, "error")
    assert one["problems"] == ["«Окна»: кампания в архиве"]
    assert one["changes"] == []


def test_no_reason_at_all_gives_full_advice():
    assert _out("pause", {"campaign_ids": [1]}, {}, "error")["problems"] == [_NO_REASON]
    assert _out("pause", {"campaign_ids": [1]}, {"error": "direct"}, "error")["problems"] == [_NO_REASON]


def test_undo_failed_step_in_words():
    result = {
        "steps": [
            {"op": "campaign_budgets", "text": "Вернуть дневной бюджет «Окна»: 1 000 ₽", "status": "ok"},
            {"op": "negatives", "text": "Минус-фразы «Окна»: убрать 5", "status": "error", "message": "Кампания не найдена"},
        ]
    }
    out = _out("undo", {"log_id": 7, "campaign_row_ids": [1]}, result, "partial")
    assert out["changes"] == ["Вернули дневной бюджет «Окна»: 1 000 ₽"]
    assert out["problems"] == ["Не выполнено шагов: 1 из 2", "Минус-фразы «Окна»: убрать 5 — кампания не найдена"]


# --- что поменялось --------------------------------------------------------------------------


def test_daily_budget_same_for_all_campaigns():
    undo = _steps(("campaign_budgets", {"budgets": {"1": 100000, "2": 100000, "3": 100000, "4": 100000}}))
    out = _out("set_budget", {"campaign_ids": [1, 2, 3, 4], "daily_budget_rub": 1500}, {"applied": 4, "errors": []}, undo=undo)
    assert out["changes"] == ["Дневной бюджет: 1 000 ₽ → 1 500 ₽, 4 кампании"]
    assert out["problems"] == []


def test_daily_budget_by_percent_names_each_campaign():
    undo = _steps(("campaign_budgets", {"budgets": {"1": 100000, "2": 50000}}))
    out = _out("set_budget", {"campaign_ids": [1, 2], "budget_change_pct": 20}, {"applied": 2}, undo=undo)
    assert out["changes"] == ["Дневной бюджет «Окна»: 1 000 ₽ → 1 200 ₽", "Дневной бюджет «Двери»: 500 ₽ → 600 ₽"]


def test_keyword_bids_and_single_keyword_edit():
    undo = _steps(("keyword_bids", {"bids": {str(k): 12_000_000 for k in (5, 6, 7, 8)}}))
    payload = {"keyword_row_ids": [5, 6, 7, 8], "campaign_row_ids": [1], "action": "set_bid", "bid_rub": 15}
    assert _out("keywords", payload, {"applied": 4}, undo=undo)["changes"] == ["Ставка: 12 ₽ → 15 ₽, 4 фразы"]
    undo = _steps(("keyword_update", {"keyword_row_id": 5, "keyword": "окна", "bid_rub": 12}))
    out = _out("keywords", {"keyword_row_ids": [5]}, {"keyword": "окна пвх", "bid_micros": 15_500_000}, undo=undo)
    assert out["changes"] == ["Текст: «окна» → «окна пвх»", "Ставка: 12 ₽ → 15,50 ₽"]


def test_negatives_in_one_and_several_campaigns():
    added = ["бесплатно", "своими руками", "скачать", "фото", "отзывы"]
    undo = _steps(("negatives", {"campaigns": {"1": {"add": [], "remove": added}}}))
    out = _out("negatives", {"campaign_row_ids": [1]}, {"added": 5, "removed": 0, "errors": []}, undo=undo)
    assert out["changes"] == ["Минус-фразы: +5 в кампании «Окна»", "Добавлены: «бесплатно», «своими руками», «скачать» и ещё 2"]
    undo = _steps(("negatives", {"campaigns": {"1": {"add": ["x"], "remove": ["a"]}, "2": {"add": [], "remove": ["a", "b"]}}}))
    out = _out("negatives", {"campaign_row_ids": [1, 2]}, {"added": 2, "removed": 1}, undo=undo)
    assert out["changes"][0] == "Минус-фразы: +2, −1 в 2 кампаниях"


def test_state_and_weekly_budget_and_adjustments():
    assert _out("pause", {"campaign_ids": [1, 2, 3]}, {"applied": 3})["changes"] == ["Показы: идут → на паузе, 3 кампании"]
    assert _out("resume", {"campaign_ids": [1]}, {"applied": 1})["changes"] == ["Показы: на паузе → идут"]
    items = [
        {"campaign_row_id": i, "name": n, "changed": True, "before": "8 000 ₽/нед.", "after": "10 000 ₽/нед."}
        for i, n in ((1, "Окна"), (2, "Двери"))
    ]
    out = _out("budget", {"campaign_ids": [1, 2, 3]}, {"applied": 2, "unchanged": 1, "items": items})
    assert out["changes"] == ["8 000 ₽/нед. → 10 000 ₽/нед., 2 кампании", "Без изменений: 1 кампания"]
    undo = _steps(("bid_modifiers", {"campaign_row_id": 1, "dimension": "device", "values": {"MOBILE": 100, "TABLET": 100}}))
    payload = {"dimension": "device", "values": {"MOBILE": 120, "TABLET": 0, "DESKTOP": 100}, "campaign_ids": [1]}
    out = _out("bid_adjust", payload, {"applied": 1}, undo=undo)
    assert out["changes"] == ["Смартфоны: без корректировки → +20 %", "Планшеты: без корректировки → не показывать"]


def test_settings_ads_and_sites():
    undo = _steps(("campaign_settings", {"campaign_row_id": 1, "changes": {"name": "Окна", "daily_budget_rub": 0}}))
    out = _out("settings", {"campaign_row_id": 1, "changes": {"name": "Окна СПб", "daily_budget_rub": 2000}}, {}, undo=undo)
    assert out["changes"] == ["Название: «Окна» → «Окна СПб»", "Дневной бюджет: без ограничения → 2 000 ₽"]
    undo = _steps(("ad_update", {"ad_row_id": 7, "titles": ["A", "B", "C"], "href": "https://site.ru/old"}))
    changes = {"titles": ["A", "B2", "C"], "texts": ["Текст"], "href": "https://site.ru/new"}
    out = _out("ads", {"ad_row_ids": [7], "campaign_row_ids": [1], "changes": changes}, {}, undo=undo)
    assert out["changes"] == ["Заголовок 2: «B» → «B2»", "Ссылка: site.ru/old → site.ru/new"]
    undo = _steps(("campaign_settings", {"campaign_row_id": 1, "changes": {"excluded_sites": ["a.ru"] * 12}}))
    result = {"applied": 1, "sites": 4, "items": [{"campaign_row_id": 1, "name": "Окна", "added": 4}], "errors": []}
    out = _out("exclude_sites", {"items": []}, result, undo=undo)
    assert out["changes"] == ["Запрещённых площадок: 12 → 16"]
    assert [c["id"] for c in out["campaigns"]] == [1]  # кампания — из ответа: в параметрах её нет


def test_unknown_action_falls_back_to_applied_count():
    assert _out("something_new", {}, {"applied": 3})["changes"] == ["Применено изменений: 3"]
    assert _out("something_new", {}, {})["changes"] == []


def test_campaign_cards_for_links():
    out = _out("pause", {"campaign_ids": [1, 2, 3, 99]}, {"applied": 4})
    assert [c["id"] for c in out["campaigns"]] == [1, 2, 3]
    assert out["campaigns"][0] == {"id": 1, "name": "Окна", "workspace_id": 5, "direct_id": 111}
    assert out["campaigns_total"] == 3  # кампании 99 у пользователя уже нет


def test_queries_links_and_account_error():
    undo = _steps(("negatives", {"campaigns": {"1": {"add": [], "remove": ["a", "b", "c", "d"]}}}))
    result = {"negative": 4, "target": 2, "keywords": 1, "skipped": 0, "errors": 0}
    out = _out("queries", {"query_row_ids": [1, 2], "campaign_row_ids": [1]}, result, undo=undo)
    assert out["changes"] == ["Минус-фразы: +4 в кампании «Окна»", "Новые ключевые фразы: 1", "Отмечено целевыми: 2 запроса"]
    undo = _steps(("campaign_link_params", {"campaign_row_id": 1, "own": {"ref": None}}))
    assert _out("links", {"campaign_row_id": 1, "params": {"ref": "Telegram"}}, {}, undo=undo)["changes"] == [
        "Метка ref: по шаблону сайта → Telegram"
    ]
    result = {"tracking_fixed": True, "groups_fixed": 2, "ads_fixed": 5, "sitelinks_fixed": 0, "errors": []}
    assert _out("links", {"campaign_row_id": 1}, result)["changes"] == [
        "Исправлены ссылки: параметры кампании, 2 группы, 5 объявлений"
    ]
    result = {"updated": 1, "added": 40, "removed": 0, "errors": [{"client_login": "shop", "error": "token-expired"}]}
    out = _out("negative_list_apply", {"list_id": 1, "campaign_row_ids": [1, 2]}, result, "partial")
    assert out["changes"] == ["Минус-фразы: +40 в 1 кампании"]
    assert out["problems"] == ["Аккаунт shop: доступ к Директу истёк — подключите аккаунт заново"]
    result = {"groups": 3, "keywords": 40, "ads": 6, "warnings": ["Не скопированы картинки"]}
    out = _out("duplicate", {"campaign_row_id": 1, "new_campaign_row_id": 2}, result, "partial")
    assert out["changes"] == ["Скопировано: 3 группы, 40 фраз, 6 объявлений"]
    assert out["problems"] == ["Не скопированы картинки"]
