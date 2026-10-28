"""Объявления: версия API для add/update."""

from app import ads


def test_responsive_ads_go_to_v501(monkeypatch):
    """Комбинаторные add/update v5 отклоняет: «не поддерживается в v5, используйте v501»."""
    versions: list[tuple] = []
    monkeypatch.setattr(
        ads.direct_api, "call", lambda token, login, service, method, params, *, version="v5": versions.append((method, version)) or {}
    )
    ads._call("t", "login", "add", {"Ads": [{"AdGroupId": 1, "ResponsiveAd": {"Titles": ["Слоны"]}}]})
    ads._call("t", "login", "update", {"Ads": [{"Id": 2, "ResponsiveAd": {"Href": "https://example.com"}}]})
    ads._call("t", "login", "add", {"Ads": [{"AdGroupId": 1, "TextAd": {"Title": "Слоны"}}]})
    ads._call("t", "login", "suspend", {"SelectionCriteria": {"Ids": [2]}})
    assert versions == [("add", "v501"), ("update", "v501"), ("add", "v5"), ("suspend", "v5")]
