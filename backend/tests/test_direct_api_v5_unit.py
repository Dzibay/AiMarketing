"""Работа с API Директа по актуальной документации: комбинаторные объявления, копия, стратегии ЕПК."""

import pytest

from app import actions, direct_api, keywords
from app.actions import (
    _ad_for_add,
    _copy_body,
    _download_image,
    _group_for_add,
    _keyword_for_add,
    attribution_for_api,
    build_bidding_strategy,
    campaign_options,
    strategy_from_api,
)
from app.ads import clean_variants, parse_ads
from app.yandex import YandexError, create_oauth_state

RESPONSIVE = {
    "Id": 11,
    "AdGroupId": 5,
    "Type": "RESPONSIVE_AD",
    "State": "ON",
    "Status": "ACCEPTED",
    "ResponsiveAd": {
        "Titles": [{"Title": "Диваны"}, {"Title": "Диваны со скидкой"}, {"Title": "Диван за день"}],
        "Texts": [{"Text": "Доставка завтра"}, {"Text": "Рассрочка 0%"}],
        "Href": "https://shop.ru",
        "SitelinkSetId": 70,
        "AdImages": {"Items": [{"ImageHash": "img1"}, {"ImageHash": "img2"}]},
        "VideoExtensions": {"Items": [{"CreativeId": 900}]},
        "AdExtensions": [{"AdExtensionId": 31, "Type": "CALLOUT"}],
        "PriceExtension": {"Price": 1_000_000_000, "PriceQualifier": "FROM", "PriceCurrency": "RUB"},
        "BusinessId": 55,
    },
}


def test_parse_responsive_keeps_every_variant():
    """Без ResponsiveAd Директ отдавал один заголовок, текст и картинку — отсюда потери при копии."""
    row = parse_ads([RESPONSIVE])[0]
    assert row["type"] == "RESPONSIVE_AD"
    assert row["titles"] == ["Диваны", "Диваны со скидкой", "Диван за день"]
    assert row["texts"] == ["Доставка завтра", "Рассрочка 0%"]
    assert row["image_hashes"] == ["img1", "img2"]
    assert row["title"] == "Диваны" and row["text"] == "Доставка завтра"


def test_parse_text_ad_keeps_title2():
    row = parse_ads(
        [{"Id": 1, "AdGroupId": 2, "Type": "TEXT_AD", "TextAd": {"Title": "А", "Title2": "Б", "Text": "В", "AdImageHash": "h"}}]
    )[0]
    assert (row["titles"], row["title2"], row["texts"], row["image_hashes"]) == (["А"], "Б", ["В"], ["h"])


def test_clean_variants_trims_dedupes_and_limits():
    assert clean_variants([" a ", "a", "", "b" * 70, "c"], 2, 56) == ["a", "b" * 56]


def test_attribution_maps_retired_models():
    assert attribution_for_api("lsc") == "LSCCD"
    assert attribution_for_api("FC") == "FCCD"
    assert attribution_for_api("LYDC") == "AUTO"
    assert attribution_for_api("LC") == "LC"
    assert attribution_for_api("WHATEVER") is None
    assert attribution_for_api("") is None


def test_unified_manual_has_weekly_limit_and_no_network_bids():
    bidding, manual = build_bidding_strategy(
        {"search": "HIGHEST_POSITION", "network": "SERVING_OFF", "weekly_budget_rub": 5000}, "UNIFIED_CAMPAIGN"
    )
    assert manual is True
    assert bidding["Search"] == {"BiddingStrategyType": "HIGHEST_POSITION", "HighestPosition": {"WeeklySpendLimit": 5_000_000_000}}
    assert strategy_from_api(bidding)["weekly_budget_rub"] == 5000
    with pytest.raises(YandexError):
        build_bidding_strategy({"search": "HIGHEST_POSITION", "network": "MAXIMUM_COVERAGE"}, "UNIFIED_CAMPAIGN")
    with pytest.raises(YandexError):
        build_bidding_strategy({"search": "SERVING_OFF"}, "UNIFIED_CAMPAIGN")


def test_text_campaign_manual_ignores_weekly_limit():
    bidding, manual = build_bidding_strategy({"search": "HIGHEST_POSITION", "weekly_budget_rub": 5000})
    assert manual is True and "HighestPosition" not in bidding["Search"]
    bidding, manual = build_bidding_strategy({"search": "SERVING_OFF"})
    assert bidding["Network"]["BiddingStrategyType"] == "MAXIMUM_COVERAGE" and manual is True


def test_options_by_type_hide_network_manual_bids_in_unified():
    opts = campaign_options()
    unified = opts["by_type"]["UNIFIED_CAMPAIGN"]
    networks = [n["id"] for side in unified["network_by_search"].values() for n in side]
    assert "MAXIMUM_COVERAGE" not in networks
    assert [n["id"] for n in unified["network_by_search"]["HIGHEST_POSITION"]] == ["SERVING_OFF"]
    manual = next(s for s in unified["search_strategies"] if s["id"] == "HIGHEST_POSITION")
    assert [p["key"] for p in manual["params"]] == ["weekly_budget_rub"]
    assert {m["id"] for m in opts["attribution_models"]} == {"AUTO", "LC", "LSCCD", "FCCD"}


def test_responsive_copy_same_account_keeps_everything():
    out = _ad_for_add(RESPONSIVE, unified=True, same=True, sitelinks={}, callouts={}, images={})["ResponsiveAd"]
    assert out["Titles"] == ["Диваны", "Диваны со скидкой", "Диван за день"]
    assert out["Texts"] == ["Доставка завтра", "Рассрочка 0%"]
    assert out["AdImageHashes"] == ["img1", "img2"]
    assert out["VideoExtensionIds"] == [900]
    assert out["BusinessId"] == 55
    assert out["SitelinkSetId"] == 70 and out["AdExtensionIds"] == [31]
    assert out["PriceExtension"] == {"Price": 1_000_000_000, "PriceQualifier": "FROM", "PriceCurrency": "RUB"}


def test_responsive_copy_other_account_maps_images_and_drops_account_objects():
    out = _ad_for_add(
        RESPONSIVE, unified=True, same=False, sitelinks={70: 71}, callouts={31: 32}, images={"img2": "new2"}
    )["ResponsiveAd"]
    assert out["AdImageHashes"] == ["new2"]
    assert out["SitelinkSetId"] == 71 and out["AdExtensionIds"] == [32]
    assert "VideoExtensionIds" not in out and "BusinessId" not in out
    assert len(out["Titles"]) == 3 and len(out["Texts"]) == 2


def test_text_ad_into_unified_group_drops_vcard_turbo_and_mobile():
    ad = {
        "TextAd": {
            "Title": "А",
            "Text": "Б",
            "Href": "https://x.ru",
            "Mobile": "YES",
            "VCardId": 1,
            "TurboPageId": 2,
            "AdImageHash": "h",
        }
    }
    unified = _ad_for_add(ad, unified=True, same=True, sitelinks={}, callouts={}, images={})["TextAd"]
    assert unified["Mobile"] == "NO" and "VCardId" not in unified and "TurboPageId" not in unified
    assert unified["AdImageHash"] == "h"
    text = _ad_for_add(ad, unified=False, same=True, sitelinks={}, callouts={}, images={})["TextAd"]
    assert (text["Mobile"], text["VCardId"], text["TurboPageId"]) == ("YES", 1, 2)


def test_image_ad_without_copied_image_is_skipped():
    ad = {"TextImageAd": {"AdImageHash": "h", "Href": "https://x.ru"}}
    assert _ad_for_add(ad, unified=False, same=False, sitelinks={}, callouts={}, images={}) is None
    out = _ad_for_add(ad, unified=False, same=False, sitelinks={}, callouts={}, images={"h": "n"})
    assert out == {"TextImageAd": {"AdImageHash": "n", "Href": "https://x.ru"}}


def test_keyword_copy_autotargeting_and_bids():
    auto = {
        "Keyword": "---autotargeting",
        "Bid": 3_000_000,
        "ContextBid": 3_000_000,
        "AutotargetingSearchBidIsAuto": "YES",
        "AutotargetingSettings": {"Categories": {"Exact": "YES", "Broader": "NO"}},
    }
    out = _keyword_for_add(auto, 7, manual=True, unified=True)
    assert out["Keyword"] == "---autotargeting" and out["Bid"] == 3_000_000
    assert "ContextBid" not in out
    assert out["AutotargetingSettings"]["Categories"]["Broader"] == "NO"
    # Группа уже создана со ставкой 0,30 ₽. Видимые 3 ₽ фиксируем отдельно, автоставку выключаем.
    bid = actions._copied_bid(auto, 99, search_manual=False, network_manual=False)
    assert bid == {"KeywordId": 99, "SearchBid": 3_000_000, "AutotargetingSearchBidIsAuto": "NO"}
    phrase = _keyword_for_add({"Keyword": "диван", "Bid": 1, "StrategyPriority": "HIGH"}, 7, manual=False, unified=False)
    assert phrase == {"AdGroupId": 7, "Keyword": "диван", "StrategyPriority": "HIGH"}
    assert actions._copied_bid({"Keyword": "диван", "Bid": 1}, 8, search_manual=False, network_manual=False) is None
    assert actions._copied_bid({"Keyword": "диван", "Bid": 3_000_000}, 8, search_manual=True, network_manual=False) == {
        "KeywordId": 8,
        "SearchBid": 3_000_000,
    }


def test_shopping_ad_copies_feed_on_v501_only_inside_account():
    ad = {
        "Type": "SHOPPING_AD",
        "ShoppingAd": {
            "FeedId": 55,
            "GenerationScopes": ["OFFER"],
            "FeedFilterConditions": [{"Operand": "price", "Operator": "GREATER_THAN", "Arguments": ["100"]}],
            "TitleSources": ["name"],
            "SitelinkSetId": 7,
            "AdExtensions": [{"AdExtensionId": 3, "Type": "CALLOUT"}],
            "BusinessId": 9,
        },
    }
    out = _ad_for_add(ad, unified=True, same=True, sitelinks={}, callouts={}, images={})
    assert out["ShoppingAd"]["FeedId"] == 55
    assert out["ShoppingAd"]["FeedFilterConditions"][0]["Operand"] == "price"
    assert out["ShoppingAd"]["SitelinkSetId"] == 7
    assert out["ShoppingAd"]["AdExtensionIds"] == [3]
    assert _ad_for_add(ad, unified=True, same=False, sitelinks={}, callouts={}, images={}) is None
    assert direct_api.v5_url("token", "ads", "v501") == "https://api.direct.yandex.com/json/v501/ads"
    assert direct_api.v5_url("token", "ads") == "https://api.direct.yandex.com/json/v5/ads"


def test_unified_group_gets_offer_retargeting():
    group = {"Name": "Г", "RegionIds": [213], "UnifiedAdGroup": {"OfferRetargeting": "YES"}}
    assert _group_for_add(group, 9, unified=True)["UnifiedAdGroup"] == {"OfferRetargeting": "YES"}
    assert "UnifiedAdGroup" not in _group_for_add(group, 9, unified=False)


def test_copy_body_weekly_manual_budget_drops_daily_and_shared_sets_only_same_account():
    item = {
        "StartDate": "2020-01-01",
        "DailyBudget": {"Amount": 500_000_000, "Mode": "STANDARD"},
        "UnifiedCampaign": {
            "BiddingStrategy": {
                "Search": {"BiddingStrategyType": "HIGHEST_POSITION", "HighestPosition": {"WeeklySpendLimit": 7_000_000_000}},
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            },
            "NegativeKeywordSharedSetIds": {"Items": [123]},
        },
    }
    body, manual = _copy_body(item, "UNIFIED_CAMPAIGN", name="Копия", counter_id=None, same_account=True)
    assert manual is True and "DailyBudget" not in body
    assert body["UnifiedCampaign"]["NegativeKeywordSharedSetIds"] == {"Items": [123]}
    body, _ = _copy_body(item, "UNIFIED_CAMPAIGN", name="Копия", counter_id=None)
    assert "NegativeKeywordSharedSetIds" not in body["UnifiedCampaign"]


def test_image_download_only_from_yandex_hosts(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("не должно быть запроса")

    monkeypatch.setattr(actions.httpx, "Client", boom)
    assert _download_image("https://evil.example.com/a.png") is None
    assert _download_image("file:///etc/passwd") is None
    assert _download_image("https://avatars.yandex.net.evil.com/a.png") is None


def test_sandbox_token_fails_fast(monkeypatch):
    monkeypatch.setattr(direct_api, "post_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("запрос")))
    with pytest.raises(YandexError) as exc:
        direct_api.call(direct_api.AccessToken("t", sandbox=True), None, "campaigns", "get", {})
    assert exc.value.code == "sandbox"
    with pytest.raises(YandexError) as exc:
        create_oauth_state(purpose="connect", user_id=1, mode="sandbox")
    assert exc.value.code == "sandbox"


def test_keyword_bids_go_through_keywordbids(monkeypatch):
    calls = []

    def fake_call(token, login, service, method, params):
        calls.append((service, method, params))
        return {"SetResults": [{"KeywordId": 1}, {"Errors": [{"Code": 5005, "Message": "Ставка ниже минимальной"}]}]}

    monkeypatch.setattr(keywords.direct_api, "call", fake_call)
    errors = keywords._set_bids("t", "login", [(1, 5_000_000), (2, 1)])
    assert calls[0][:2] == ("keywordbids", "set")
    assert calls[0][2]["KeywordBids"][0] == {"KeywordId": 1, "SearchBid": 5_000_000}
    assert list(errors) == [1]
