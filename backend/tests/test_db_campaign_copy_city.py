"""Окно «Копия» на настоящем Postgres: предпросмотр замен города и сайта и поля задачи копии. Директ подменён."""

import pytest
from fastapi.testclient import TestClient

from app import actions, geo
from app.main import app
from app.web_auth import make_session
from tests.dbutil import make_account, make_campaign, make_connection, make_user, make_workspace
from tests.test_geo_resolve_unit import REGIONS

DIRECT = {
    "adgroups": [{"Id": 1, "Name": "Окна", "Type": "TEXT_AD_GROUP", "RegionIds": [213],
                  "NegativeKeywords": {"Items": ["казань"]}}],
    "keywords": [{"Id": 31, "AdGroupId": 1, "Keyword": "окна москва"}, {"Id": 32, "AdGroupId": 1, "Keyword": "---autotargeting"}],
    "ads": [{"Id": 51, "AdGroupId": 1, "State": "ON", "TextAd": {
        "Title": "Окна в Москве", "Text": "Замер по Москве", "Href": "https://old.ru/okna", "SitelinkSetId": 7}}],
    "sitelinks": [{"Id": 7, "Sitelinks": [{"Title": "Цены", "Href": "https://old.ru/price"}]}],
    "bidmodifiers": [{"CampaignId": 101, "MobileAdjustment": {"BidModifier": 120}},
                     {"CampaignId": 101, "RegionalAdjustment": {"RegionId": 2, "BidModifier": 50, "Enabled": "NO"}}],
    "negativekeywordsharedsets": [{"Id": 3, "Name": "Общие", "NegativeKeywords": {"Items": ["бесплатно"]}}],
}


@pytest.fixture
def s(db, monkeypatch):
    user = make_user(db, "u@example.com")
    conn_id = make_connection(db, user)
    acc = make_account(db, user, conn_id, login="src")
    other = make_account(db, user, conn_id, login="dst")
    camp = make_campaign(db, user, acc, 101, "Окна Москва")
    with db.connection() as conn:
        conn.execute("UPDATE direct_campaigns SET campaign_type = 'TEXT_CAMPAIGN' WHERE id = %s", (camp,))
    ws = make_workspace(db, user, 555, "Окна", accounts=[acc], campaigns=[camp])
    ws2 = make_workspace(db, user, 556, "Окна Казань", accounts=[other])
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET site = 'old.ru' WHERE id = %s", (ws,))
        conn.execute("UPDATE workspaces SET site = 'https://new.ru/' WHERE id = %s", (ws2,))
    reads: list[str] = []

    def get_all(token, login, service, params, key, *, version="v5"):
        reads.append(service)
        return DIRECT[service]

    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    monkeypatch.setattr("app.yandex.get_connection_access_token", lambda *a: "tok")
    monkeypatch.setattr(actions, "_fetch_campaign", lambda *a: {"TextCampaign": {
        "NegativeKeywordSharedSetIds": {"Items": [3]}}})
    monkeypatch.setattr(actions, "_get_all", get_all)

    def no_writes(*a, **kw):
        raise AssertionError("предпросмотр ничего не пишет в Директ")

    monkeypatch.setattr(actions.direct_api, "call", no_writes)
    return {"db": db, "user": user, "acc": acc, "other": other, "camp": camp, "ws": ws, "ws2": ws2, "reads": reads,
            "auth": {"Authorization": f"Bearer {make_session(user)}"}}


def _url(s, tail=""):
    return f"/api/web/workspaces/{s['ws']}/campaigns/{s['camp']}/duplicate{tail}"


def test_preview_proposes_city_and_site_lines(s):
    client = TestClient(app)
    got = client.post(
        _url(s, "/preview"), headers=s["auth"],
        json={"target_account_id": s["other"], "target_workspace_id": s["ws2"], "region_ids": [43]},
    )
    assert got.status_code == 200, got.text
    data = got.json()
    assert (data["from_city"], data["to_city"], data["from_site"], data["to_site"]) == ("Москва", "Казань", "old.ru", "new.ru")
    lines = {i["key"]: (i["before"], i["after"]) for i in data["items"]}
    assert lines == {
        "ad:51:Title": ("Окна в Москве", "Окна в Казани"),
        "ad:51:Text": ("Замер по Москве", "Замер по Казани"),
        "ad:51:Href": ("https://old.ru/okna", "https://new.ru/okna"),
        "kw:31": ("окна москва", "окна казань"),
        "sl:7:0:Href": ("https://old.ru/price", "https://new.ru/price"),
    }
    assert data["suggested_name"] == "Окна Казань"
    assert data["bid_modifiers"] == {"count": 1, "skipped": 1}
    assert data["shared_sets"] == {"count": 1, "names": ["Общие"]}
    assert data["same_account"] is False
    assert any("«казань»" in w for w in data["warnings"])

    # Человек поправил город сам — строки по его слову.
    again = client.post(
        _url(s, "/preview"), headers=s["auth"],
        json={"region_ids": [43], "replace": {"to_city": "Тула", "to_site": ""}},
    ).json()
    assert again["to_city"] == "Тула"
    assert {i["key"]: i["after"] for i in again["items"]}["ad:51:Title"] == "Окна в Туле"
    assert "ad:51:Href" not in {i["key"] for i in again["items"]}
    assert again["shared_sets"] is None  # тот же кабинет — наборы и так подключены


def test_preview_and_duplicate_check_regions_and_owner(s, db):
    client = TestClient(app)
    bad = client.post(_url(s, "/preview"), headers=s["auth"], json={"region_ids": [-43]})
    assert bad.status_code == 400
    unknown = client.post(_url(s), headers=s["auth"], json={"region_ids": [43, 999999]})
    assert unknown.status_code == 400
    stranger = make_user(db, "x@example.com")
    foreign = client.post(_url(s, "/preview"), headers={"Authorization": f"Bearer {make_session(stranger)}"}, json={})
    assert foreign.status_code == 404


def test_duplicate_job_carries_regions_replacements_and_shared_sets(s, db):
    client = TestClient(app)
    job = client.post(
        _url(s), headers=s["auth"],
        json={
            "target_account_id": s["other"], "target_workspace_id": s["ws2"], "name": "Окна Казань",
            "region_ids": [43], "copy_shared_sets": True,
            "replace": {"from_city": "Москва", "to_city": "Казань", "skip": ["kw:31"]},
        },
    ).json()["job"]
    with db.connection() as conn:
        payload = conn.execute("SELECT payload FROM jobs WHERE id = %s", (job["id"],)).fetchone()["payload"]
    assert payload["region_ids"] == [43]
    assert payload["copy_shared_sets"] is True
    assert payload["replace"] == {"from_city": "Москва", "to_city": "Казань", "from_site": None, "to_site": None, "skip": ["kw:31"]}
    assert payload["workspace_id"] == s["ws2"]

    # Без новых полей задача прежняя.
    plain = client.post(_url(s), headers=s["auth"], json={}).json()["job"]
    with db.connection() as conn:
        payload = conn.execute("SELECT payload FROM jobs WHERE id = %s", (plain["id"],)).fetchone()["payload"]
    assert not {"region_ids", "replace", "copy_shared_sets"} & set(payload)
