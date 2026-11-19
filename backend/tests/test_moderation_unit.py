"""Причины отказа модерации без БД: что отклонено у объявления и как это сказать коротко."""

from app.ads import parse_ads, rejected_extensions
from app.overview import rejected_detail


def test_rejected_extensions_only_rejected():
    block = {
        "SitelinksModeration": {"Status": "REJECTED", "StatusClarification": " Ссылка ведёт на недоступную страницу "},
        "AdImageModeration": {"Status": "ACCEPTED", "StatusClarification": ""},
        "VCardModeration": {"Status": "REJECTED"},
    }
    assert rejected_extensions(block) == [
        {"what": "Быстрые ссылки", "reason": "Ссылка ведёт на недоступную страницу"},
        {"what": "Визитка", "reason": ""},
    ]
    assert rejected_extensions({}) == []


def test_parse_ads_keeps_reason_and_extras():
    [ad, plain] = parse_ads([
        {
            "Id": 7, "AdGroupId": 5, "Type": "TEXT_AD", "State": "OFF", "Status": "REJECTED",
            "StatusClarification": " Нет подтверждения скидки ",
            "TextAd": {
                "Title": "Окна от 990 ₽", "Text": "Скидка 50%", "Href": "https://okna.ru",
                "SitelinksModeration": {"Status": "REJECTED", "StatusClarification": "Нарушены требования"},
            },
        },
        {"Id": 8, "AdGroupId": 5, "Type": "TEXT_AD", "Status": "ACCEPTED", "TextAd": {"Title": "Окна"}},
    ])
    assert ad["status_clarification"] == "Нет подтверждения скидки"
    assert ad["moderation"] == [{"what": "Быстрые ссылки", "reason": "Нарушены требования"}]
    assert (plain["status_clarification"], plain["moderation"]) == ("", [])


def test_rejected_detail_short_and_counted():
    rows = [
        {"title": "Окна от 990 ₽", "status": "REJECTED", "reason": "Нет подтверждения скидки", "extras": []},
        {"title": "Двери", "status": "ACCEPTED", "reason": "", "extras": [{"what": "Быстрые ссылки", "reason": ""}]},
        {"title": "", "status": "REJECTED", "reason": "", "extras": [{"what": "Картинка", "reason": "Мелкий текст"}]},
    ]
    assert rejected_detail(rows) == (
        "«Окна от 990 ₽» — Нет подтверждения скидки; Быстрые ссылки в «Двери»; Объявление и ещё 1"
    )
    assert rejected_detail(rows, limit=10).endswith("Объявление; Картинка объявления — Мелкий текст")
    long_title = "Очень длинный заголовок объявления про пластиковые окна"
    assert rejected_detail([{"title": long_title, "status": "REJECTED", "reason": ""}]) == f"«{long_title[:40]}…»"
    assert rejected_detail([]) == ""
