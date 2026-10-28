from app import ad_stats
from app.ad_stats import _finish_net, clean_suggestion, flag_low


def _ad(ad_id, group, campaign=1, **nets):
    return {
        "ad_id": ad_id,
        "adgroup_id": group,
        "campaign_id": campaign,
        "nets": {k: _finish_net({"impressions": v[0], "clicks": v[1], "cost_kopecks": 0, "conversions": 0}) for k, v in nets.items()},
    }


def test_flag_low_against_group():
    weak = _ad(1, 10, search=(1000, 20))
    good = _ad(2, 10, search=(1000, 60))
    rows = [weak, good]
    flag_low(rows)
    assert weak["nets"]["search"]["low"] is True
    assert weak["nets"]["search"]["ref_ctr"] == 6.0
    assert weak["nets"]["search"]["best_ad_id"] == 2
    assert "группы" in weak["nets"]["search"]["reasons"][0]
    assert good["nets"]["search"]["low"] is False
    assert "best_ad_id" not in good["nets"]["search"]


def test_flag_low_falls_back_to_campaign_and_floor():
    alone = _ad(1, 10, search=(1000, 15))
    other = _ad(2, 11, search=(1000, 50))
    flag_low([alone, other])
    assert "кампании" in alone["nets"]["search"]["reasons"][0]
    # Нет соседей вообще: на поиске — порог 1 %.
    single = _ad(3, 12, campaign=2, search=(600, 3))
    flag_low([single])
    assert single["nets"]["search"]["low"] and "ниже 1" in single["nets"]["search"]["reasons"][0]
    # В сетях порога нет; мало показов — не судим.
    net_only = _ad(4, 13, campaign=3, network=(5000, 5))
    few = _ad(5, 13, campaign=3, network=(300, 0))
    flag_low([net_only, few])
    assert net_only["nets"]["network"]["low"] is False
    assert "low" not in few["nets"]["network"]


def test_flag_low_networks_separate():
    a = _ad(1, 10, search=(1000, 50), network=(5000, 10))
    b = _ad(2, 10, search=(1000, 50), network=(5000, 40))
    flag_low([a, b])
    assert a["nets"]["search"]["low"] is False
    assert a["nets"]["network"]["low"] is True


def test_clean_suggestion():
    responsive = {"type": "RESPONSIVE_AD", "titles": ["Старый"], "texts": ["Старый текст"], "title2": ""}
    got = clean_suggestion(
        responsive,
        {"titles": ["Новый заголовок", "x" * 57, "Новый заголовок", "Второй"], "texts": ["Текст"], "title2": "лишнее", "why": "w"},
    )
    assert got == {"titles": ["Новый заголовок", "Второй"], "texts": ["Текст"], "title2": "", "why": "w"}
    text_ad = {"type": "TEXT_AD", "titles": ["Старый"], "texts": ["Старый текст"], "title2": "Доп"}
    got = clean_suggestion(text_ad, {"titles": ["А", "Б"], "texts": ["Т1", "Т2"], "title2": "Второй"})
    assert got["titles"] == ["А"] and got["texts"] == ["Т1"] and got["title2"] == "Второй"
    # Слишком длинное слово, пустые тексты и повтор старого — не предложение.
    assert clean_suggestion(text_ad, {"titles": ["Сверхдлинноесловобезпробеловвообще"], "texts": ["Т"]}) is None
    assert clean_suggestion(text_ad, {"titles": ["А"], "texts": []}) is None
    assert clean_suggestion(text_ad, {"titles": ["Старый"], "texts": ["Старый текст"], "title2": "Доп"}) is None


def test_create_ab_ads(monkeypatch):
    src = {
        "id": 5,
        "type": "RESPONSIVE_AD",
        "titles": ["Старый"],
        "texts": ["Старый текст"],
        "title2": "",
        "href": "https://site.ru",
        "adgroup_row_id": 9,
        "adgroup_name": "Группа",
        "campaign_row_id": 3,
    }
    created_calls = []
    logged = {}
    monkeypatch.setattr(ad_stats, "require_workspace", lambda u, w: {})
    monkeypatch.setattr(ad_stats, "_workspace_ads", lambda u, w, ids: {5: src})

    def fake_create(user_id, **kw):
        created_calls.append(kw)
        return {"id": 77, "ad_id": 1000, "adgroup_row_id": kw["adgroup_row_id"], "type": "RESPONSIVE_AD", "titles": kw["titles"]}

    monkeypatch.setattr(ad_stats.ads_mod, "create_text_ad", fake_create)
    monkeypatch.setattr(ad_stats.ads_mod, "_ad_row", lambda u, i: {"connection_id": 1, "client_login": "c"})
    monkeypatch.setattr(ad_stats, "log_action", lambda *a, **kw: logged.update(kw))
    moderated = []
    monkeypatch.setattr(ad_stats, "_moderate", lambda u, created: (moderated.extend(created), (len(created), []))[1])

    res = ad_stats.create_ab_ads(
        1, {"workspace_id": 2, "items": [{"ad_row_id": 5, "titles": ["Новый"], "texts": ["Новый текст"]}]}
    )
    assert created_calls == [
        {"adgroup_row_id": 9, "href": "https://site.ru", "titles": ["Новый"], "texts": ["Новый текст"], "title2": ""}
    ]
    assert res["status"] == "ok" and res["created"][0]["source_ad_row_id"] == 5
    assert not moderated
    assert logged["undo"]["steps"] == [{"op": "ads_action", "args": {"ad_row_ids": [77], "action": "delete"}}]
    assert logged["workspace_id"] == 2

    ad_stats.create_ab_ads(
        1, {"workspace_id": 2, "moderate": True, "items": [{"ad_row_id": 5, "titles": ["Новый"], "texts": ["Новый текст"]}]}
    )
    assert len(moderated) == 1
