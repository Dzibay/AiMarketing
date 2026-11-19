"""Копия кампании в другой город или клиенту: замена города и сайта, корректировки ставок, наборы минус-фраз."""

import pytest

from app import actions, campaign_copy, direct_api
from app.campaign_copy import Rules, decline, adjective, too_long
from app.yandex import YandexError


# --- Склонение и прилагательные -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name, forms",
    [
        ("Москва", ("Москва", "Москвы", "Москве", "Москву", "Москвой", "Москве")),
        ("Казань", ("Казань", "Казани", "Казани", "Казань", "Казанью", "Казани")),
        ("Томск", ("Томск", "Томска", "Томску", "Томск", "Томском", "Томске")),
        ("Сочи", ("Сочи",) * 6),
    ],
)
def test_decline(name, forms):
    assert decline(name) == forms


def test_adjective_from_dictionary_and_rule():
    assert adjective("Москва") == "московск"
    assert adjective("Казань") == "казанск"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Окна в Москве недорого", "Окна в Казани недорого"),
        ("Доставка по Москве", "Доставка по Казани"),
        ("Едем в Москву", "Едем в Казань"),
        ("Из Москвы за час", "Из Казани за час"),
        ("Московский завод окон", "Казанский завод окон"),
        ("МОСКВА — ОКНА", "КАЗАНЬ — ОКНА"),
        ("окна москва", "окна казань"),
        ("Окна Мск", "Окна Казань"),
    ],
)
def test_city_replaced_in_right_case(text, expected):
    rules = Rules(from_city="Москва", to_city="Казань")
    assert rules.replace(text) == (expected, [])


def test_spb_aliases_go_to_target_forms():
    rules = Rules(from_city="Санкт-Петербург", to_city="Нижний Новгород")
    new, left = rules.replace("Окна в СПб и Питере")
    assert new == "Окна в Нижнем Новгороде и Нижнем Новгороде"
    assert left == []


def test_region_adjective_is_left_for_the_person():
    """«Московская область» не превращаем в «Казанскую область» — это решает человек."""
    rules = Rules(from_city="Москва", to_city="Казань")
    new, left = rules.replace("Москва и Московская область")
    assert new == "Казань и Московская область"
    assert left == ["Московская"]


def test_no_target_adjective_is_reported_as_left():
    rules = Rules(from_city="Москва", to_city="Нижний Новгород Тест")
    new, left = rules.replace("Московский завод")
    assert new == "Московский завод"
    assert left == ["Московский"]


def test_same_city_does_nothing():
    rules = Rules(from_city="Москва", to_city="москва")
    assert not rules.active
    assert rules.replace("в Москве") == ("в Москве", [])


# --- Сайт, ссылки, отображаемая ссылка ----------------------------------------------------------


def test_site_replaced_in_text_and_href_host_only():
    rules = Rules(from_site="https://www.old.ru/", to_site="new.ru")
    assert rules.replace("Заходите на old.ru и www.old.ru")[0] == "Заходите на new.ru и new.ru"
    assert rules.replace("https://old.ru/okna?utm=1", "href")[0] == "https://new.ru/okna?utm=1"
    assert rules.replace("https://other.ru/old.ru", "href")[0] == "https://other.ru/old.ru"
    # Поддомены и похожие домены не трогаем.
    assert rules.replace("gold.ru и old.ru.com")[0] == "gold.ru и old.ru.com"


def test_display_path_gets_translit():
    rules = Rules(from_city="Москва", to_city="Казань")
    assert rules.replace("okna-moskva", "path")[0] == "okna-kazan"


# --- Длины Директа ------------------------------------------------------------------------------


def test_too_long_limits():
    assert too_long("а" * 56, "Title") == ""
    assert too_long("а" * 57, "Title") == "длиннее 56 знаков"
    # Узкие знаки в лимит не входят.
    assert too_long("а" * 55 + "!.,", "Title") == ""
    assert too_long("а" * 26, "Callout") == "длиннее 25 знаков"
    assert too_long("раз два три четыре пять шесть семь восемь", "Keyword") == "больше 7 слов"
    assert too_long("окна -москва -дешево", "Keyword") == ""


def test_change_counts_and_skip():
    rules = Rules(from_city="Москва", to_city="Санкт-Петербург", skip={"ad:1:Title2"})
    title = rules.change("ad:1:Title", "Окна в Москве", "Title")
    title2 = rules.change("ad:1:Title2", "Москва", "Title2")
    long = rules.change("co:5", "Доставка по Москве в день", "Callout")
    assert title == "Окна в Санкт-Петербурге"
    assert title2 == "Москва"
    assert long == "Доставка по Санкт-Петербургу в день"
    summary = rules.summary()
    assert summary["applied"] == 2
    assert summary["skipped"] == 1
    assert summary["too_long"] and summary["too_long"][0].startswith("Уточнение")


# --- Предпросмотр и замены в объявлении ---------------------------------------------------------


ADS = [
    {
        "Id": 11,
        "AdGroupId": 1,
        "TextAd": {
            "Title": "Окна в Москве",
            "Title2": "Скидки",
            "Text": "Московский завод. Замер бесплатно",
            "Href": "https://old.ru/okna",
            "DisplayUrlPath": "moskva",
        },
    },
    {
        "Id": 12,
        "AdGroupId": 1,
        "ResponsiveAd": {"Titles": [{"Title": "Окна"}, {"Title": "Окна Москва"}], "Texts": [{"Text": "Москва и область"}]},
    },
]


def test_proposals_lines_and_apply_with_skip():
    rules = Rules(from_city="Москва", to_city="Казань", from_site="old.ru", to_site="new.ru")
    texts = campaign_copy.ad_texts(ADS, {1: "Окна"})
    out = campaign_copy.proposals(texts, rules)
    by_key = {i["key"]: i for i in out["items"]}
    assert set(by_key) == {
        "ad:11:Title",
        "ad:11:Text",
        "ad:11:Href",
        "ad:11:DisplayUrlPath",
        "ad:12:Titles.1",
        "ad:12:Texts.0",
    }
    assert by_key["ad:11:Title"]["after"] == "Окна в Казани"
    assert by_key["ad:11:Href"]["after"] == "https://new.ru/okna"
    assert by_key["ad:11:Title"]["where"] == "Группа «Окна»"
    assert out["left"] == []

    # Копия с теми же ключами: снятая строка остаётся как в оригинале.
    copy_rules = Rules(from_city="Москва", to_city="Казань", from_site="old.ru", to_site="new.ru", skip={"ad:11:Text"})
    entry = {"TextAd": {k: v for k, v in ADS[0]["TextAd"].items()}}
    campaign_copy.apply_to_ad(entry, 11, copy_rules)
    assert entry["TextAd"]["Title"] == "Окна в Казани"
    assert entry["TextAd"]["Text"] == "Московский завод. Замер бесплатно"
    assert entry["TextAd"]["DisplayUrlPath"] == "kazan"
    responsive = {"ResponsiveAd": {"Titles": ["Окна", "Окна Москва"], "Texts": ["Москва и область"]}}
    campaign_copy.apply_to_ad(responsive, 12, copy_rules)
    assert responsive["ResponsiveAd"]["Titles"] == ["Окна", "Окна Казань"]
    assert copy_rules.summary()["skipped"] == 1


def test_keyword_sitelink_callout_texts():
    rules = Rules(from_city="Москва", to_city="Казань", from_site="old.ru", to_site="new.ru")
    texts = (
        campaign_copy.keyword_texts([{"Id": 5, "AdGroupId": 1, "Keyword": "окна москва -дешево"}, {"Id": 6, "Keyword": "---autotargeting"}], {1: "Окна"})
        + campaign_copy.sitelink_texts([{"Id": 7, "Sitelinks": [{"Title": "Цены в Москве", "Href": "https://old.ru/price"}]}])
        + campaign_copy.callout_texts([{"Id": 8, "Callout": {"CalloutText": "Замер по Москве"}}])
    )
    items = {i["key"]: i["after"] for i in campaign_copy.proposals(texts, rules)["items"]}
    assert items == {
        "kw:5": "окна казань -дешево",
        "sl:7:0:Title": "Цены в Казани",
        "sl:7:0:Href": "https://new.ru/price",
        "co:8": "Замер по Казани",
    }


def test_rules_from_uses_defaults_for_missing_fields():
    assert campaign_copy.rules_from(None, from_city="Москва") is None
    rules = campaign_copy.rules_from({"to_city": "Тула", "skip": ["kw:1", ""]}, from_city="Москва", to_city="Казань")
    assert (rules.from_city, rules.to_city, rules.skip) == ("Москва", "Тула", {"kw:1"})
    # Пустая строка — «не менять»: человек стёр город.
    assert not campaign_copy.rules_from({"from_city": ""}, from_city="Москва", to_city="Казань").city_on


def test_city_from_regions():
    regions = {
        213: {"name": "Москва", "type": "City"},
        1: {"name": "Москва и Московская область", "type": "Administrative area"},
        11119: {"name": "Республика Татарстан", "type": "Administrative area"},
    }
    assert campaign_copy.city_from_regions([11119, 213], regions) == "Москва"
    assert campaign_copy.city_from_regions([1], regions) == "Москва"
    assert campaign_copy.city_from_regions([11119], regions) == ""


def test_negatives_with_target_city():
    assert campaign_copy.negatives_with_city(["казань", "дешево", "в казани"], "Казань") == ["казань", "в казани"]


# --- Быстрые ссылки и уточнения -----------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    """direct_api.call без сети: get — из данных, add — новые id; calls — что ушло бы в Директ."""
    state = {"calls": [], "data": {}, "next": 900, "fail": set()}

    def call(token, login, service, method, params, version="v5"):
        state["calls"].append((login, service, method, params, version))
        if (service, method) in state["fail"]:
            raise YandexError("direct", f"{service}.{method} упал")
        if method == "get":
            return state["data"].get(service, {})
        key = next(iter(params))
        results = []
        for _item in params[key]:
            state["next"] += 1
            results.append({"Id": state["next"]})
        return {"AddResults": results}

    monkeypatch.setattr(direct_api, "call", call)
    return state


def test_sitelinks_same_account_only_changed_sets(api):
    api["data"]["sitelinks"] = {
        "SitelinksSets": [
            {"Id": 1, "Sitelinks": [{"Title": "Цены в Москве", "Href": "https://old.ru/a"}]},
            {"Id": 2, "Sitelinks": [{"Title": "Отзывы", "Href": "https://old.ru/b"}]},
        ]
    }
    rules = Rules(from_city="Москва", to_city="Казань")

    def transform(set_id, index, link):
        link["Title"] = rules.change(f"sl:{set_id}:{index}:Title", link["Title"], "SlTitle")
        return link

    warnings: list[str] = []
    mapping = actions._copy_sitelinks("t", "src", "t", "src", {1, 2}, warnings, transform=transform, only_changed=True)
    assert mapping == {1: 901}
    adds = [c for c in api["calls"] if c[2] == "add"]
    assert adds[0][3] == {"SitelinksSets": [{"Sitelinks": [{"Title": "Цены в Казани", "Href": "https://old.ru/a"}]}]}
    assert warnings == []


def test_ad_for_add_same_account_uses_new_sets_when_recreated():
    ad = {
        "Id": 1,
        "TextAd": {
            "Title": "Окна",
            "Text": "Текст",
            "Href": "https://a.ru",
            "SitelinkSetId": 1,
            "AdExtensions": [{"AdExtensionId": 5, "Type": "CALLOUT"}, {"AdExtensionId": 6, "Type": "CALLOUT"}],
        },
    }
    entry = actions._ad_for_add(ad, unified=False, same=True, sitelinks={1: 901}, callouts={5: 905}, images={})
    assert entry["TextAd"]["SitelinkSetId"] == 901
    assert entry["TextAd"]["AdExtensionIds"] == [905, 6]
    kept = actions._ad_for_add(ad, unified=False, same=True, sitelinks={}, callouts={}, images={})
    assert kept["TextAd"]["SitelinkSetId"] == 1
    assert kept["TextAd"]["AdExtensionIds"] == [5, 6]


def test_callouts_transform_in_other_account(api):
    api["data"]["adextensions"] = {
        "AdExtensions": [
            {"Id": 5, "Type": "CALLOUT", "Callout": {"CalloutText": "Замер по Москве"}},
            {"Id": 6, "Type": "CALLOUT", "Callout": {"CalloutText": "Гарантия"}},
        ]
    }
    rules = Rules(from_city="Москва", to_city="Казань")
    mapping = actions._copy_callouts(
        "t", "src", "t2", "dst", {5, 6}, [], transform=lambda i, t: rules.change(f"co:{i}", t, "Callout")
    )
    assert mapping == {5: 901, 6: 902}
    add = next(c for c in api["calls"] if c[2] == "add")
    assert add[0] == "dst"
    assert [x["Callout"]["CalloutText"] for x in add[3]["AdExtensions"]] == ["Замер по Казани", "Гарантия"]


# --- Корректировки ставок -----------------------------------------------------------------------


BID_MODIFIERS = [
    {"Id": 1, "CampaignId": 100, "Level": "CAMPAIGN", "Type": "MOBILE_ADJUSTMENT",
     "MobileAdjustment": {"BidModifier": 120, "OperatingSystemType": None}},
    {"Id": 2, "CampaignId": 100, "Level": "CAMPAIGN", "Type": "DEMOGRAPHICS_ADJUSTMENT",
     "DemographicsAdjustment": {"Gender": "GENDER_MALE", "Age": "AGE_18_24", "BidModifier": 50, "Enabled": "YES"}},
    {"Id": 3, "CampaignId": 100, "Level": "CAMPAIGN", "Type": "DEMOGRAPHICS_ADJUSTMENT",
     "DemographicsAdjustment": {"Gender": "GENDER_FEMALE", "Age": "AGE_55", "BidModifier": 0, "Enabled": "YES"}},
    {"Id": 4, "CampaignId": 100, "AdGroupId": 10, "Level": "AD_GROUP", "Type": "RETARGETING_ADJUSTMENT",
     "RetargetingAdjustment": {"RetargetingConditionId": 77, "BidModifier": 150, "Accessible": "YES", "Enabled": "YES"}},
    {"Id": 5, "CampaignId": 100, "Level": "CAMPAIGN", "Type": "REGIONAL_ADJUSTMENT",
     "RegionalAdjustment": {"RegionId": 213, "BidModifier": 80, "Enabled": "NO"}},
    {"Id": 6, "CampaignId": 100, "AdGroupId": 99, "Level": "AD_GROUP", "Type": "MOBILE_ADJUSTMENT",
     "MobileAdjustment": {"BidModifier": 90}},
]


def test_bid_modifiers_for_add_same_account():
    payload, skipped = actions.bid_modifiers_for_add(BID_MODIFIERS, 500, {10: 510}, same=True)
    assert payload == [
        {"CampaignId": 500, "MobileAdjustment": {"BidModifier": 120}},
        {"CampaignId": 500, "DemographicsAdjustments": [
            {"Gender": "GENDER_MALE", "Age": "AGE_18_24", "BidModifier": 50},
            {"Gender": "GENDER_FEMALE", "Age": "AGE_55", "BidModifier": 0},
        ]},
        {"AdGroupId": 510, "RetargetingAdjustments": [{"RetargetingConditionId": 77, "BidModifier": 150}]},
    ]
    assert skipped == 1  # выключенная региональная; группа 99 не копировалась


def test_bid_modifiers_for_add_other_account_skips_retargeting():
    payload, skipped = actions.bid_modifiers_for_add(BID_MODIFIERS, 500, {10: 510}, same=False)
    assert not any("RetargetingAdjustments" in p for p in payload)
    assert skipped == 2


def test_copy_bid_modifiers_reads_and_adds(api):
    api["data"]["bidmodifiers"] = {"BidModifiers": BID_MODIFIERS}
    warnings: list[str] = []
    done = actions._copy_bid_modifiers(
        "t", "src", 100, "t", "src", 500, {10: 510}, same=True, unified=True, warnings=warnings
    )
    assert done == 4
    get = api["calls"][0]
    assert get[1:3] == ("bidmodifiers", "get") and get[4] == "v501"
    assert get[3]["SelectionCriteria"] == {"CampaignIds": [100], "Levels": ["CAMPAIGN", "AD_GROUP"]}
    add = api["calls"][1]
    assert add[1:3] == ("bidmodifiers", "add") and len(add[3]["BidModifiers"]) == 3
    assert warnings == ["Не перенесено корректировок ставок: 1 (выключенные или ретаргетинг другого кабинета)"]


def test_copy_bid_modifiers_read_error_is_a_warning(api):
    api["fail"].add(("bidmodifiers", "get"))
    warnings: list[str] = []
    assert actions._copy_bid_modifiers("t", "s", 1, "t", "s", 2, {}, same=True, unified=False, warnings=warnings) == 0
    assert warnings[0].startswith("Корректировки ставок не прочитаны")


# --- Наборы минус-фраз --------------------------------------------------------------------------


def test_copy_shared_sets_reuses_identical_and_adds_new(api):
    def call(token, login, service, method, params, version="v5"):
        api["calls"].append((login, service, method, params, version))
        if method == "get" and login == "src":
            return {"NegativeKeywordSharedSets": [
                {"Id": 1, "Name": "Общие", "NegativeKeywords": {"Items": ["бесплатно", "своими руками"]}},
                {"Id": 2, "Name": "Работа", "NegativeKeywords": {"Items": ["вакансии"]}},
            ]}
        if method == "get":
            return {"NegativeKeywordSharedSets": [
                {"Id": 50, "Name": "Общие", "NegativeKeywords": {"Items": ["своими руками", "бесплатно"]}},
            ]}
        return {"AddResults": [{"Id": 60}]}

    direct_api.call = call  # monkeypatch из фикстуры api вернёт исходный
    warnings: list[str] = []
    ids = actions._copy_shared_sets("t", "src", "t2", "dst", [1, 2], warnings)
    assert ids == [50, 60]
    add = next(c for c in api["calls"] if c[2] == "add")
    assert add[0] == "dst"
    assert add[3] == {"NegativeKeywordSharedSets": [{"Name": "Работа", "NegativeKeywords": ["вакансии"]}]}
    assert warnings == []


def test_copy_shared_sets_read_error_keeps_old_warning(api):
    api["fail"].add(("negativekeywordsharedsets", "get"))
    warnings: list[str] = []
    assert actions._copy_shared_sets("t", "src", "t2", "dst", [1], warnings) == []
    assert "подключите их к копии вручную" in warnings[0]


def test_replace_title_for_journal():
    summary = {"applied": 3, "from_city": "Москва", "to_city": "Казань", "from_site": "a.ru", "to_site": "b.ru"}
    assert actions._replace_title(summary) == "; замены: Москва → Казань, a.ru → b.ru"
    assert actions._replace_title({**summary, "applied": 0}) == ""
    assert actions._replace_title(None) == ""


# --- Копия целиком: регионы, замены, корректировки, наборы минус-фраз ---------------------------


def test_duplicate_campaign_to_new_city_and_cabinet(monkeypatch):
    source = {
        "id": 10, "account_id": 1, "campaign_id": 111, "name": "Окна Москва", "campaign_type": "TEXT_CAMPAIGN",
        "connection_id": 5, "client_login": "client-src", "enabled": True,
    }
    manual = {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}, "Network": {"BiddingStrategyType": "SERVING_OFF"}}
    found = {
        "adgroups": [{"Id": 1, "Name": "Окна", "Type": "TEXT_AD_GROUP", "RegionIds": [213]}],
        "keywords": [{"Id": 31, "AdGroupId": 1, "Keyword": "окна москва", "Bid": 5_000_000}],
        "ads": [{"Id": 51, "AdGroupId": 1, "TextAd": {
            "Title": "Окна в Москве", "Text": "Замер по Москве", "Href": "https://old.ru/okna", "SitelinkSetId": 7,
            "AdExtensions": [{"AdExtensionId": 8, "Type": "CALLOUT"}]}}],
        "sitelinks": [{"Id": 7, "Sitelinks": [{"Title": "Цены в Москве", "Href": "https://old.ru/price"}]}],
        "adextensions": [{"Id": 8, "Type": "CALLOUT", "Callout": {"CalloutText": "Москва"}}],
        "bidmodifiers": [{"CampaignId": 111, "MobileAdjustment": {"BidModifier": 120}}],
    }
    shared = {
        "client-src": [{"Id": 3, "Name": "Общие", "NegativeKeywords": {"Items": ["бесплатно"]}}],
        "client-dst": [],
    }
    added: dict[str, list] = {}
    journal: list[dict] = []

    def get_all(token, login, service, params, key, *, version="v5"):
        if service == "negativekeywordsharedsets":
            return shared[login]
        if service == "keywords" and params["SelectionCriteria"].get("CampaignIds") == [222]:
            return []
        return found[service]

    def add_batches(token, login, service, key, objects, *, version="v5"):
        added.setdefault(service, []).extend(objects)
        return [900 + i for i in range(len(objects))], []

    calls: list[tuple] = []

    def call(token, login, service, method, params, version="v5"):
        calls.append((login, service, method, params))
        if service == "bidmodifiers" and method == "get":
            return {"BidModifiers": found["bidmodifiers"]}
        return {"AddResults": [{"Id": 1} for _ in params.get("BidModifiers") or []]}

    created: list[dict] = []
    monkeypatch.setattr(actions, "_campaign_full", lambda *a: dict(source))
    monkeypatch.setattr(actions, "_account_creds", lambda *a: {"connection_id": 6, "client_login": "client-dst"})
    monkeypatch.setattr(actions, "get_connection_access_token", lambda *a: "token")
    monkeypatch.setattr(actions, "_fetch_campaign", lambda *a: {"TextCampaign": {
        "BiddingStrategy": manual, "NegativeKeywordSharedSetIds": {"Items": [3]}}})
    monkeypatch.setattr(actions, "_add_copy", lambda token, login, body, *a: created.append(body) or 222)
    monkeypatch.setattr(actions, "insert_campaign_local", lambda *a, **kw: {"id": 20})
    monkeypatch.setattr(actions, "_update_local", lambda *a, **kw: None)
    monkeypatch.setattr(actions, "_get_all", get_all)
    monkeypatch.setattr(actions, "_add_batches", add_batches)
    monkeypatch.setattr(actions.direct_api, "call", call)
    monkeypatch.setattr(actions, "log_action", lambda *a, **kw: journal.append(kw))
    monkeypatch.setattr("app.workspaces.campaign_workspace_id", lambda *a: None)
    monkeypatch.setattr(
        campaign_copy, "defaults",
        lambda user_id, groups, src_ws, ws, region_ids: {
            "from_city": "Москва", "to_city": "Казань" if region_ids == [43] else "Москва",
            "from_site": "", "to_site": "",
        },
    )
    for sync in ("adgroups.sync_campaign_adgroups", "keywords.sync_campaign_keywords", "ads.sync_campaign_ads"):
        monkeypatch.setattr(f"app.{sync}", lambda *a: None)

    out = actions.duplicate_campaign(
        7, 10, target_account_id=2, name="Окна Казань", region_ids=[43],
        replace={"from_site": "old.ru", "to_site": "new.ru", "skip": ["co:8"]}, copy_shared_sets=True,
    )
    assert out["status"] == "ok"
    assert created[0]["TextCampaign"]["NegativeKeywordSharedSetIds"] == {"Items": [900]}
    assert added["negativekeywordsharedsets"] == [{"Name": "Общие", "NegativeKeywords": ["бесплатно"]}]
    assert out["shared_sets"] == 1
    assert added["adgroups"][0]["RegionIds"] == [43]
    assert added["keywords"][0]["Keyword"] == "окна казань"
    ad = added["ads"][0]["TextAd"]
    assert (ad["Title"], ad["Text"], ad["Href"].split("?")[0]) == ("Окна в Казани", "Замер по Казани", "https://new.ru/okna")
    assert added["sitelinks"] == [{"Sitelinks": [{"Title": "Цены в Казани", "Href": "https://new.ru/price"}]}]
    # Снятая строка — как в оригинале.
    assert added["adextensions"] == [{"Callout": {"CalloutText": "Москва"}}]
    assert out["bid_modifiers"] == 1
    assert out["replace"]["applied"] == 6 and out["replace"]["skipped"] == 1
    assert not any("Наборы минус-фраз" in w for w in out["warnings"])
    assert journal[0]["title"].endswith("; замены: Москва → Казань, old.ru → new.ru")


# --- Проверка ревью: обычные слова, неудачные наборы, чужие адреса быстрых ссылок --------------------


@pytest.mark.parametrize(
    "city, text",
    [("Ковров", "Чистка ковров недорого"), ("Шахты", "Ремонт шахты лифта")],
)
def test_city_form_from_rules_is_not_replaced_in_lowercase_text(city, text):
    """«ковров» в тексте — обычное слово, не город Ковров: в объявлении не меняем и не считаем остатком."""
    rules = Rules(from_city=city, to_city="Казань")
    assert rules.replace(text) == (text, [])
    assert rules.change("ad:1:Text", text, "Text") == text
    assert rules.summary()["applied"] == 0 and rules.summary()["doubtful"] == []


def test_city_form_from_rules_in_keyword_is_marked_doubtful():
    rules = Rules(from_city="Ковров", to_city="Казань")
    assert rules.replace("Доставка в Ковров")[0] == "Доставка в Казань"  # с заглавной — город
    assert rules.change("kw:1", "чистка ковров", "Keyword") != "чистка ковров"
    assert rules.summary()["doubtful"] == ["ковров"]
    found = campaign_copy.proposals([campaign_copy.Text("kw:1", "Keyword", "Группа", "чистка ковров")], rules)
    assert found["items"][0]["doubtful"] is True


def test_dictionary_city_in_lowercase_keyword_is_not_doubtful():
    rules = Rules(from_city="Москва", to_city="Казань")
    assert rules.change("kw:1", "доставка москва", "Keyword") == "доставка казань"
    assert rules.summary()["doubtful"] == []


def test_proper_adjective_mid_phrase_is_left_for_the_person():
    """«Казанский собор» — имя собственное: не меняем, показываем как оставшийся город."""
    rules = Rules(from_city="Казань", to_city="Самара")
    assert rules.replace("Экскурсия в Казанский собор") == ("Экскурсия в Казанский собор", ["Казанский"])
    assert rules.replace("Казанская доставка цветов")[0] == "Самарская доставка цветов"


def test_ad_for_add_same_account_drops_sets_that_failed_to_recreate():
    """Набор или уточнение с заменами не создались — не подставляем старые (в них прежний город)."""
    ad = {
        "Id": 1,
        "TextAd": {
            "Title": "Окна", "Text": "Текст", "Href": "https://a.ru", "SitelinkSetId": 1,
            "AdExtensions": [{"AdExtensionId": 5, "Type": "CALLOUT"}, {"AdExtensionId": 6, "Type": "CALLOUT"}],
        },
    }
    entry = actions._ad_for_add(ad, unified=False, same=True, sitelinks={1: 0}, callouts={5: 0}, images={})
    assert entry["TextAd"].get("SitelinkSetId") is None
    assert entry["TextAd"]["AdExtensionIds"] == [6]
    other = actions._ad_for_add(ad, unified=False, same=False, sitelinks={1: 0}, callouts={5: 0, 6: 906}, images={})
    assert other["TextAd"].get("SitelinkSetId") is None
    assert other["TextAd"]["AdExtensionIds"] == [906]


def test_copy_sitelinks_and_callouts_report_failed(api, monkeypatch):
    api["data"]["sitelinks"] = {"SitelinksSets": [{"Id": 1, "Sitelinks": [{"Title": "Цены в Москве", "Href": "https://a.ru"}]}]}
    api["data"]["adextensions"] = {"AdExtensions": [{"Id": 5, "Type": "CALLOUT", "Callout": {"CalloutText": "По Москве"}}]}
    monkeypatch.setattr(actions, "_add_batches", lambda *a, **kw: ([None], ["длиннее 25 знаков"]))
    rules = Rules(from_city="Москва", to_city="Санкт-Петербург")

    def transform(set_id, index, link):
        link["Title"] = rules.change(f"sl:{set_id}:{index}:Title", link["Title"], "SlTitle")
        return link

    failed_sl: set[int] = set()
    failed_co: set[int] = set()
    warnings: list[str] = []
    assert actions._copy_sitelinks("t", "s", "t", "s", {1}, warnings, transform=transform, only_changed=True, failed=failed_sl) == {}
    assert actions._copy_callouts(
        "t", "s", "t", "s", {5}, warnings, transform=lambda i, t: rules.change(f"co:{i}", t, "Callout"),
        only_changed=True, failed=failed_co,
    ) == {}
    assert failed_sl == {1} and failed_co == {5}
    # Чтение упало — все наборы считаем неудачными: без проверки старые не подставляем.
    api["fail"].add(("sitelinks", "get"))
    failed_read: set[int] = set()
    actions._copy_sitelinks("t", "s", "t", "s", {1, 2}, [], transform=transform, only_changed=True, failed=failed_read)
    assert failed_read == {1, 2}


def test_sitelink_href_follows_template_only_on_its_site():
    from app import links

    template = links.parse_template("https://site.ru/?utm_source=yandex&ref=ya")
    assert actions._sitelink_href("https://www.site.ru/price", template).startswith("https://site.ru/price")
    for href in ("https://vk.com/shop", "https://ozon.ru/seller/1", "https://msk.site.ru/okna"):
        assert actions._sitelink_href(href, template) == href
