"""Синхронизация кампаний: настоящий тип кампании знает только API v501."""

import pytest

from app import direct_sync
from app.yandex import YandexError


def _item(cid: int, ctype: str, block: str) -> dict:
    return {
        "Id": cid,
        "Name": f"Кампания {cid}",
        "Type": ctype,
        "State": "ON",
        "Status": "ACCEPTED",
        block: {"BiddingStrategy": {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}}, "TrackingParams": "utm_source=yandex"},
    }


def test_fetch_campaigns_reads_type_through_v501(monkeypatch):
    calls = []

    def fake(token, login, service, method, params, *, version="v5", **kw):
        calls.append((version, "UnifiedCampaignFieldNames" in params))
        return {"Campaigns": [_item(1, "UNIFIED_CAMPAIGN", "UnifiedCampaign")]}

    monkeypatch.setattr(direct_sync.direct_api, "call", fake)
    items = direct_sync.fetch_campaigns("token", "login")
    assert calls == [("v501", True)]
    assert items[0]["campaign_type"] == "UNIFIED_CAMPAIGN" and items[0]["type_exact"] is True
    assert items[0]["strategy"] == "HIGHEST_POSITION" and items[0]["tracking_params"] == "utm_source=yandex"


def test_fetch_campaigns_falls_back_to_v5_with_inexact_type(monkeypatch):
    calls = []

    def fake(token, login, service, method, params, *, version="v5", **kw):
        calls.append((version, "UnifiedCampaignFieldNames" in params))
        if version == "v501":
            raise YandexError("direct", "версия недоступна")
        return {"Campaigns": [_item(1, "TEXT_CAMPAIGN", "TextCampaign")]}

    monkeypatch.setattr(direct_sync.direct_api, "call", fake)
    items = direct_sync.fetch_campaigns("token", "login")
    # Обе попытки с ЕПК идут в v501, без ЕПК — в v5; тип оттуда устаревший.
    assert calls == [("v501", True), ("v501", True), ("v5", False)]
    assert items[0]["campaign_type"] == "TEXT_CAMPAIGN" and items[0]["type_exact"] is False


def test_fetch_campaigns_does_not_retry_when_units_are_over(monkeypatch):
    def fake(*args, **kw):
        raise YandexError("units")

    monkeypatch.setattr(direct_sync.direct_api, "call", fake)
    with pytest.raises(YandexError) as exc:
        direct_sync.fetch_campaigns("token", "login")
    assert exc.value.code == "units"
