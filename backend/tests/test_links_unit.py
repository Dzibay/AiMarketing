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



def test_check_campaign_groups_and_sitelinks(t):
    groups = [
        {"id": 7, "adgroup_id": 70, "name": "Своя метка", "tracking_params": "utm_source=vk&from=g"},
        {"id": 8, "adgroup_id": 80, "name": "Без меток", "tracking_params": ""},
        {"id": 9, "adgroup_id": 90, "name": "Не загружена", "tracking_params": None},
    ]
    ads = [
        {"id": 1, "ad_id": 11, "type": "TEXT_AD", "state": "ON", "title": "Окна",
         "href": "https://happ-connection.ru/?ref=Yandex",
         "sitelink_hrefs": ["https://happ-connection.ru/tarify?ref=Yandex", "https://happ-connection.ru/faq"]},
        {"id": 2, "ad_id": 12, "type": "TEXT_AD", "state": "ON", "title": "Ок",
         "href": "https://happ-connection.ru/?ref=Yandex", "sitelink_hrefs": None},
    ]
    out = links.check_campaign(t, TRACKING, ads, groups)
    assert not out["ok"] and out["campaign_problems"] == []
    assert [g["adgroup_row_id"] for g in out["groups"]] == [7]
    assert out["groups"][0]["fixed_tracking"] == TRACKING + "&from=g"
    assert "utm_source=vk вместо yandex" in out["groups"][0]["problems"]
    [ad] = out["ads"]
    assert (ad["ad_row_id"], ad["problems"], ad["sitelink_problems"]) == (1, [], ["нет ref=Yandex"])
    assert ad["fixed_href"] == ad["href"]
    assert links.check_campaign(t, TRACKING, ads[1:], groups[1:])["ok"]


def test_template_without_utm_keeps_own_tags():
    """Шаблон только с ref: свои utm кампании не требуем переносить и не стираем при исправлении."""
    t = links.parse_template("https://happ-connection.ru/?ref=Yandex")
    href = "https://happ-connection.ru/?ref=Yandex_CPC&utm_source=yandex&utm_term={keyword}"
    assert links.href_problems(href, t) == ["ref=Yandex_CPC вместо Yandex"]
    assert links.normalize_href(href, t) == "https://happ-connection.ru/?ref=Yandex&utm_source=yandex&utm_term={keyword}"
    assert links.tracking_problems("ref=Yandex_CPC&utm_medium=cpc", t) == ["ref и в параметрах кампании, и в ссылке"]
    assert links.merge_tracking("ref=Yandex_CPC&utm_medium=cpc", t) == "utm_medium=cpc"


def test_check_campaign_groups_same_links_and_marks_bad_params(t):
    bad = "https://happ-connection.ru/?ref=Yandex_CPC&utm_source=yandex"
    ads = [
        {"id": i, "ad_id": 10 + i, "type": "TEXT_AD", "state": "ON", "title": title, "href": href}
        for i, (title, href) in enumerate(
            [("Окна", bad), ("Двери", bad), ("Окна", bad), ("Сайт", "https://other.ru/")], start=1
        )
    ]
    out = links.check_campaign(t, TRACKING.replace("yandex", "google") + "&ref=Yandex", ads)
    assert out["campaign_bad_params"] == ["utm_source", "ref"]
    first, other = out["ad_links"]
    assert (first["href"], first["count"], first["titles"]) == (bad, 3, ["Окна", "Двери"])
    assert first["bad_params"] == ["ref", "utm_source"]
    assert first["fixed_href"] == "https://happ-connection.ru/?ref=Yandex"
    assert (other["count"], other["bad_params"]) == (1, [])
    reasons = links._reasons([out])
    assert {"where": "Ссылки объявлений", "text": "ref=Yandex_CPC вместо Yandex", "campaigns": 1} in reasons


def test_parse_ads_and_groups_read_link_fields():
    from app.adgroups import parse_adgroups
    from app.ads import parse_ads

    [g] = parse_adgroups([{"Id": 5, "CampaignId": 1, "TrackingParams": "utm_source=yandex"}])
    assert g["tracking_params"] == "utm_source=yandex"
    ads = parse_ads([
        {"Id": 1, "AdGroupId": 5, "TextAd": {"Title": "a", "Text": "b", "Href": "h", "SitelinkSetId": 77}},
        {"Id": 2, "AdGroupId": 5, "ResponsiveAd": {"Titles": [{"Title": "a"}], "Texts": [{"Text": "b"}], "Href": "h"}},
    ])
    assert [a["sitelink_set_id"] for a in ads] == [77, None]


def test_attach_sitelink_hrefs(monkeypatch):
    from app import ad_extras

    monkeypatch.setattr(
        ad_extras, "sitelink_sets", lambda token, login, ids: {77: [{"title": "Цены", "href": "https://s.ru/p", "description": ""}]}
    )
    ads = ad_extras.attach_sitelink_hrefs("t", "l", [{"sitelink_set_id": 77}, {"sitelink_set_id": None}, {"sitelink_set_id": 99}])
    assert [a.get("sitelink_hrefs") for a in ads] == [["https://s.ru/p"], [], None]

    def fail(*a):
        raise YandexError("direct")

    monkeypatch.setattr(ad_extras, "sitelink_sets", fail)
    assert "sitelink_hrefs" not in ad_extras.attach_sitelink_hrefs("t", "l", [{"sitelink_set_id": 77}])[0]

def test_ai_tools_are_registered():
    from app import ai_agent

    names = {t["function"]["name"] for t in ai_agent.TOOLS}
    for name in ("get_headline_stats", "get_slices", "check_links"):
        assert name in ai_agent.READ_TOOLS and name in names and name in ai_agent.TOOL_LABELS
    assert "fix_links" in ai_agent.WRITE_TOOLS and "fix_links" in ai_agent._DIFFS and "fix_links" in names
