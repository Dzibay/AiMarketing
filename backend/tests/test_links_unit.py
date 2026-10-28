"""Шаблон ссылки сайта без БД и Директа: разбор, нормализация, проверка кампании."""

import pytest

from app import links
from app.yandex import YandexError

RAW = ("https://happ-connection.ru/?ref=Yandex&utm_source=yandex&utm_medium={source_type}"
       "&utm_campaign={campaign_id}&utm_content={ad_id}&utm_term={keyword}")
TRACKING = "utm_source=yandex&utm_medium={source_type}&utm_campaign={campaign_id}&utm_content={ad_id}&utm_term={keyword}"


@pytest.fixture
def t():
    return links.parse_template(RAW)


def test_parse_template_splits_ad_params_and_tracking(t):
    assert t["host"] == "happ-connection.ru" and t["path"] == "/"
    assert t["ad_params"] == [("ref", "Yandex")]
    assert links.template_href(t) == "https://happ-connection.ru/?ref=Yandex"
    assert links.template_tracking(t) == TRACKING
    assert links.parse_template("  ") is None
    assert links.clean_template("happ-connection.ru/?ref=Yandex") == "https://happ-connection.ru/?ref=Yandex"


@pytest.mark.parametrize(
    "raw, text",
    [
        ("ftp://site.ru/", "Шаблон ссылки — адрес сайта вида https://site.ru/?ref=Yandex&utm_source=yandex"),
        ("https://localhost/", "Шаблон ссылки — адрес сайта вида https://site.ru/?ref=Yandex&utm_source=yandex"),
        ("https://site.ru/?ref=a&ref=b", "Параметр ref в шаблоне повторяется"),
        ("https://site.ru/?q=" + "x" * 1024, "Шаблон ссылки длиннее 1024 символов"),
    ],
)
def test_parse_template_errors(raw, text):
    with pytest.raises(YandexError) as exc:
        links.parse_template(raw)
    assert exc.value.message == text


def test_normalize_href_keeps_path_and_own_params(t):
    href = "https://www.happ-connection.ru/tariffs?utm_source=google&ref=Google&x=1#plans"
    assert links.normalize_href(href, t) == "https://happ-connection.ru/tariffs?ref=Yandex&x=1#plans"
    assert links.normalize_href("https://other.ru/?a=1", t) == "https://happ-connection.ru/?ref=Yandex"
    assert links.normalize_href("", t) == "https://happ-connection.ru/?ref=Yandex"


def test_merge_tracking_keeps_foreign_params_only(t):
    assert links.merge_tracking("utm_source=google&from=direct&ref=x&utm_x=1", t) == TRACKING + "&from=direct"
    assert links.merge_tracking(None, t) == TRACKING


def test_problems(t):
    assert links.href_problems("https://happ-connection.ru/a?ref=Google&utm_source=yandex", t) == [
        "ref=Google вместо Yandex",
        "метки в ссылке объявления (utm_source) — они должны быть в параметрах кампании",
    ]
    assert links.href_problems("https://www.happ-connection.ru/any/path?ref=Yandex", t) == []  # путь может отличаться
    assert links.href_problems("https://other.ru/", t) == ["домен other.ru вместо happ-connection.ru"]
    assert links.href_problems("", t) == []
    assert links.tracking_problems(TRACKING, t) == []
    assert links.tracking_problems("utm_source=google&utm_medium={source_type}&ref=Yandex", t) == [
        "utm_source=google вместо yandex",
        "нет utm_campaign={campaign_id}",
        "нет utm_content={ad_id}",
        "нет utm_term={keyword}",
        "ref и в параметрах кампании, и в ссылке",
    ]


def test_check_campaign(t):
    ads = [
        {"id": 1, "ad_id": 11, "type": "TEXT_AD", "state": "ON", "title": "Окна", "href": "https://happ-connection.ru/"},
        {"id": 2, "ad_id": 12, "type": "IMAGE_AD", "state": "ON", "title": "", "href": "https://other.ru/"},
        {"id": 3, "ad_id": 13, "type": "TEXT_AD", "state": "ARCHIVED", "title": "Старое", "href": "https://other.ru/"},
        {"id": 4, "ad_id": 14, "type": "RESPONSIVE_AD", "state": "ON", "title": "Ок",
         "href": "https://happ-connection.ru/?ref=Yandex"},
    ]
    out = links.check_campaign(t, None, ads)
    assert (out["tracking_unknown"], out["campaign_problems"], out["ok"]) == (True, [], False)
    assert [(a["ad_row_id"], a["fixable"], a["fixed_href"]) for a in out["ads"]] == [
        (1, True, "https://happ-connection.ru/?ref=Yandex"),
        (2, False, "https://happ-connection.ru/?ref=Yandex"),
    ]
    assert out["ads"][0]["problems"] == ["нет ref=Yandex"]
    good = links.check_campaign(t, TRACKING, ads[3:])
    assert good["ok"] and good["fixed_tracking"] == TRACKING


def test_ai_tools_are_registered():
    from app import ai_agent

    names = {t["function"]["name"] for t in ai_agent.TOOLS}
    for name in ("get_headline_stats", "get_slices", "check_links"):
        assert name in ai_agent.READ_TOOLS and name in names and name in ai_agent.TOOL_LABELS
    assert "fix_links" in ai_agent.WRITE_TOOLS and "fix_links" in ai_agent._DIFFS and "fix_links" in names
