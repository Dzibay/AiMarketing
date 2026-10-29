"""Фразы: ответ keywordbids.set и перечитывание только что добавленных."""

from app import keywords
from app.keywords import BID_IGNORED_TEXT, bid_errors
from app.yandex import YandexError


def test_ignored_bid_is_an_error():
    """При автостратегии Директ отвечает без Errors, но с предупреждением 10160 — ставка не встала."""
    result = {
        "SetResults": [
            {"KeywordId": 1},
            {"KeywordId": 2, "Warnings": [{"Code": 10160, "Message": "Ставка не будет применена"}]},
            {"Errors": [{"Code": 8800, "Message": "Объект не найден", "Details": "Фраза не найдена"}]},
            {"KeywordId": 4, "Warnings": [{"Code": 10161, "Message": "Другое предупреждение"}]},
        ]
    }
    assert bid_errors(result) == {1: BID_IGNORED_TEXT, 2: "Объект не найден: Фраза не найдена"}


def test_added_keywords_are_read_back(monkeypatch):
    """После keywords.add берём из Директа ставку и статус, что он сам назначил новой фразе."""
    asked = []

    def get_all(token, login, service, params, key):
        asked.append(params["SelectionCriteria"]["Ids"])
        return [{"Id": 11, "Keyword": "слон", "AdGroupId": 5, "State": "ON", "Status": "DRAFT", "Bid": 300000}]

    monkeypatch.setattr(keywords.direct_api, "get_all", get_all)
    got = keywords._fetch_added("t", "login", {"AddResults": [{"Id": 11}, {"Errors": [{"Code": 1}]}]})
    assert asked == [[11]]
    assert got[11]["status"] == "DRAFT" and got[11]["bid_micros"] == 300000


def test_read_back_failure_keeps_add_result(monkeypatch):
    def get_all(*args):
        raise YandexError("direct")

    monkeypatch.setattr(keywords.direct_api, "get_all", get_all)
    assert keywords._fetch_added("t", "login", {"AddResults": [{"Id": 11}]}) == {}
    assert keywords._fetch_added("t", "login", {"AddResults": [{"Errors": []}]}) == {}
