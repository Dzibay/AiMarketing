import pytest

from app import slices
from app.slices import (
    _clean_pct,
    _parse_modifier,
    _row_key,
    adjustable_key,
    placement_flags,
    placement_kind,
    suggest_pct,
)
from app.yandex import YandexError


def test_placement_kind():
    assert placement_kind("com.playrix.township") == "app"
    assert placement_kind("ru.yandex.searchplugin") == "app"
    assert placement_kind("id1234567890") == "app"
    assert placement_kind("solitaire-klondike.ru") == "game"
    assert placement_kind("com.block.puzzle.free") == "game"
    assert placement_kind("igry-online.net") == "game"
    assert placement_kind("yandex.ru") == "yandex"
    assert placement_kind("news.yandex.ru") == "yandex"
    assert placement_kind("avito.ru") == ""
    assert placement_kind("drive2.ru") == ""
    assert placement_kind("m.avito.ru") == ""
    assert placement_kind("app.source.getcontact") == "app"
    assert placement_kind("where.my.seat.taken") == "app"
    # «merge» и «idle» — только целым словом, не внутри «emergency».
    assert placement_kind("emergency-help.ru") == ""


def test_excludable():
    assert slices.excludable("com.playrix.township")
    assert slices.excludable("minigames.mail.ru")
    assert not slices.excludable("1234567890")
    assert not slices.excludable("yandex.ru")


def _row(**kw):
    row = {"kind": "", "clicks": 0, "cost_kopecks": 0, "conversions": 0, "ctr": None, "bounce_rate": None, "cpa_kopecks": None}
    row.update(kw)
    return row


def test_placement_flags():
    ref = dict(ref_cpa=100_000, avg_ctr=0.8, waste_kopecks=100_000)
    assert placement_flags(_row(kind="game", cost_kopecks=500), **ref) == ["игра без конверсий"]
    assert placement_flags(_row(kind="yandex", cost_kopecks=900_000), **ref) == []
    assert placement_flags(_row(cost_kopecks=50_000, clicks=5), **ref) == []
    assert "без конверсий" in placement_flags(_row(cost_kopecks=150_000, clicks=40), **ref)[0]
    why = placement_flags(_row(cost_kopecks=10_000, clicks=30, ctr=5.0), **ref)
    assert why and "CTR" in why[0]
    why = placement_flags(_row(cost_kopecks=10_000, clicks=30, bounce_rate=80.0), **ref)
    assert why == ["отказы 80 %"]
    # С конверсиями — только если CPA втрое выше цели и потрачено заметно.
    assert placement_flags(_row(cost_kopecks=400_000, conversions=1, cpa_kopecks=400_000), **ref)
    assert placement_flags(_row(cost_kopecks=200_000, conversions=2, cpa_kopecks=100_000), **ref) == []


def test_suggest_pct():
    # Мало кликов — подсказки нет.
    assert suggest_pct(1, 10, 0.05, min_base=30) is None
    assert suggest_pct(5, 100, None, min_base=30) is None
    # Ровно средний CR — 100 %.
    assert suggest_pct(10, 200, 0.05, min_base=30) == 100
    # Сильно лучше — упирается в +50 %.
    assert suggest_pct(60, 200, 0.05, min_base=30) == 150
    # Без конверсий на большом объёме — до −50 %.
    assert suggest_pct(0, 1000, 0.05, min_base=30) == 50
    # Сглаживание: 0 конверсий на 40 кликах при среднем 2 % — не обвал.
    assert 60 <= suggest_pct(0, 40, 0.02, min_base=30) < 100
    assert suggest_pct(12, 200, 0.05, min_base=30, step=10) % 10 == 0


def test_adjustable_key_and_row_key():
    assert adjustable_key("device", "MOBILE")
    assert not adjustable_key("device", "MOBILE:IOS")
    assert adjustable_key("demography", "GENDER_MALE|AGE_25_34")
    assert not adjustable_key("demography", "UNKNOWN|AGE_25_34")
    assert adjustable_key("region", "213")
    assert not adjustable_key("region", "0")
    assert adjustable_key("hour", "23") and not adjustable_key("hour", "24")
    assert _row_key("device", {"Device": "TABLET"}) == ("TABLET", "Планшеты")
    assert _row_key("demography", {"Gender": "GENDER_FEMALE", "Age": "AGE_55"}) == ("GENDER_FEMALE|AGE_55", "Женщины, 55+")
    assert _row_key("placement", {"Placement": "--"}) is None
    assert _row_key("placement", {"Placement": "Avito.ru"}) == ("avito.ru", "Avito.ru")
    assert _row_key("region", {"LocationOfPresenceId": "2", "LocationOfPresenceName": "Санкт-Петербург"}) == ("2", "Санкт-Петербург")


def test_parse_modifier():
    m = _parse_modifier({"Id": 1, "CampaignId": 5, "MobileAdjustment": {"BidModifier": 80}})
    assert m == {"id": 1, "campaign_id": 5, "key": "MOBILE", "pct": 80, "enabled": True}
    m = _parse_modifier({"Id": 2, "CampaignId": 5, "MobileAdjustment": {"BidModifier": 80, "OperatingSystemType": "IOS"}})
    assert m["key"] == "MOBILE:IOS"
    assert _parse_modifier({"Id": 7, "CampaignId": 5, "TabletAdjustment": {"BidModifier": 0}})["pct"] == 0
    assert _parse_modifier({"Id": 5, "CampaignId": 5, "DesktopAdjustment": {"BidModifier": 90}})["key"] == "DESKTOP+TABLET"
    assert _parse_modifier({"Id": 6, "CampaignId": 5, "DesktopOnlyAdjustment": {"BidModifier": 90}})["key"] == "DESKTOP"
    m = _parse_modifier(
        {"Id": 3, "CampaignId": 5, "DemographicsAdjustment": {"Gender": "GENDER_MALE", "Age": "AGE_18_24", "BidModifier": 50, "Enabled": "NO"}}
    )
    assert m["key"] == "GENDER_MALE|AGE_18_24" and m["enabled"] is False
    m = _parse_modifier({"Id": 4, "CampaignId": 5, "RegionalAdjustment": {"RegionId": 213, "BidModifier": 120, "Enabled": "YES"}})
    assert m["key"] == "213" and m["pct"] == 120


def test_clean_pct():
    assert _clean_pct("device", 0) == 0
    assert _clean_pct("hour", 84) == 80
    assert _clean_pct("hour", 3) == 10
    with pytest.raises(YandexError):
        _clean_pct("region", 0)
    with pytest.raises(YandexError):
        _clean_pct("hour", 250)
    with pytest.raises(YandexError):
        _clean_pct("device", "abc")


def test_set_bid_modifiers_plan(monkeypatch):
    calls = []
    existing = [
        {"id": 10, "campaign_id": 7, "key": "MOBILE", "pct": 80, "enabled": True},
        {"id": 11, "campaign_id": 7, "key": "GENDER_MALE|AGE_18_24", "pct": 50, "enabled": False},
    ]
    monkeypatch.setattr(
        "app.adgroups._campaign_row",
        lambda user_id, row_id: {"connection_id": 1, "client_login": "c", "campaign_id": 7},
    )
    monkeypatch.setattr(slices, "get_connection_access_token", lambda *_a: "t")
    monkeypatch.setattr(slices, "get_modifiers", lambda token, login, ids, dim: existing)

    def fake_call(token, login, service, method, params):
        calls.append((method, params))
        return {}

    monkeypatch.setattr(slices.direct_api, "call", fake_call)

    res = slices.set_bid_modifiers(1, 99, "device", {"MOBILE": 100, "TABLET": 70, "DESKTOP": 120})
    assert res["previous"] == {"MOBILE": 80, "TABLET": 100, "DESKTOP": 100}
    assert calls[0] == ("delete", {"SelectionCriteria": {"Ids": [10]}})
    # Одно поле на элемент; компьютеры — DesktopOnly, иначе пересекаются с планшетами.
    assert calls[1] == (
        "add",
        {
            "BidModifiers": [
                {"CampaignId": 7, "TabletAdjustment": {"BidModifier": 70}},
                {"CampaignId": 7, "DesktopOnlyAdjustment": {"BidModifier": 120}},
            ]
        },
    )

    calls.clear()
    res = slices.set_bid_modifiers(1, 99, "demography", {"GENDER_MALE|AGE_18_24": 60, "GENDER_FEMALE|AGE_55": 120})
    # Выключенная корректировка удаляется и ставится заново; новые — одним элементом на кампанию.
    assert res["previous"] == {"GENDER_MALE|AGE_18_24": 100, "GENDER_FEMALE|AGE_55": 100}
    assert calls[0] == ("delete", {"SelectionCriteria": {"Ids": [11]}})
    method, params = calls[1]
    assert method == "add" and len(params["BidModifiers"]) == 1
    assert len(params["BidModifiers"][0]["DemographicsAdjustments"]) == 2

    calls.clear()
    existing[0]["pct"] = 80
    res = slices.set_bid_modifiers(1, 99, "device", {"MOBILE": 90})
    assert calls == [("set", {"BidModifiers": [{"Id": 10, "BidModifier": 90}]})]
    assert res["previous"] == {"MOBILE": 80}


def test_set_hours_keeps_off_hours(monkeypatch):
    grid = [[0] * 8 + [100] * 12 + [0] * 4 for _ in range(7)]
    saved = {}
    monkeypatch.setattr(slices.actions, "get_campaign_settings", lambda u, r: {"schedule": grid, "consider_working_weekends": False})
    monkeypatch.setattr(slices.actions, "update_campaign_settings", lambda u, r, ch: saved.update(ch))
    res = slices._set_hours(1, 5, {"9": 50, "22": 150, "10": 100})
    assert saved["schedule"][0][9] == 50
    # Выключенный час не включается, а неизменённые остаются.
    assert saved["schedule"][0][22] == 0 and saved["schedule"][3][10] == 100
    assert saved["consider_working_weekends"] is False
    assert res["previous"] == {"schedule": grid, "consider_working_weekends": False}
    assert slices._set_hours(1, 5, {"22": 150})["previous"] is None


def test_exclude_placements_merges(monkeypatch):
    updates = {}
    logged = {}
    monkeypatch.setattr(slices, "require_workspace", lambda u, w: {})
    monkeypatch.setattr(
        slices, "_campaigns", lambda u, w, ids: [{"id": i, "name": f"К{i}"} for i in ids]
    )
    monkeypatch.setattr(
        slices.actions, "get_campaign_settings", lambda u, r: {"excluded_sites": ["old.ru"] if r == 1 else []}
    )
    monkeypatch.setattr(slices.actions, "update_campaign_settings", lambda u, r, ch: updates.update({r: ch}))
    monkeypatch.setattr(slices, "log_action", lambda *a, **kw: logged.update(kw))
    res = slices.exclude_placements(
        1,
        {
            "workspace_id": 3,
            "items": [
                {"placement": "com.game.puzzle", "campaign_ids": [1, 2]},
                {"placement": "https://www.Bad-Site.ru/page", "campaign_ids": [1]},
                {"placement": "1234567890", "campaign_ids": [2]},
            ],
        },
    )
    assert updates[1] == {"excluded_sites": ["old.ru", "com.game.puzzle", "bad-site.ru"]}
    assert updates[2] == {"excluded_sites": ["com.game.puzzle"]}
    assert res["sites"] == 2 and res["applied"] == 2
    steps = logged["undo"]["steps"]
    assert {"op": "campaign_settings", "args": {"campaign_row_id": 1, "changes": {"excluded_sites": ["old.ru"]}}} in steps
