"""Смена регионов готовой кампании и групп на настоящем Postgres: Директ и справочник регионов подменены."""

import json

import pytest
from fastapi.testclient import TestClient

from app import adgroups, geo, undo
from app.journal import list_action_logs
from app.main import app
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user
from tests.test_geo_resolve_unit import REGIONS


def _group(db, s, gid, name, regions):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name, region_ids) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (s["user"], s["acc"], s["camp"], gid, name, json.dumps(regions)),
        ).fetchone()["id"]


class FakeDirect:
    """Группы в «Директе»: читаем RegionIds, update меняет их; fail — id групп, которые Директ отклонит.

    Группы кампании (CampaignIds) — с названием и кампанией из names / campaigns; list_error — Директ не отдал список.
    """

    def __init__(self, live):
        self.live = live
        self.names = {11: "Москва", 12: "Питер"}
        self.campaigns = {11: 101, 12: 101}
        self.list_error: YandexError | None = None
        self.fail: set[int] = set()
        self.updates: list[tuple[str, list]] = []

    def get_all(self, token, login, service, params, key):
        assert (service, key) == ("adgroups", "AdGroups")
        where = params["SelectionCriteria"]
        if "CampaignIds" in where:
            if self.list_error is not None:
                raise self.list_error
            assert params["FieldNames"] == adgroups.ADGROUP_FIELDS
            return [
                {"Id": i, "Name": self.names[i], "CampaignId": self.campaigns[i], "RegionIds": regions, "Type": "TEXT_AD_GROUP"}
                for i, regions in self.live.items()
                if self.campaigns.get(i) in where["CampaignIds"]
            ]
        assert params["FieldNames"] == ["Id", "RegionIds"]
        return [{"Id": i, "RegionIds": self.live[i]} for i in where["Ids"] if i in self.live]

    def call(self, token, login, service, method, params, *, version="v5", **kw):
        assert (service, method) == ("adgroups", "update")
        items = params["AdGroups"]
        self.updates.append((version, [(g["Id"], g["RegionIds"]) for g in items]))
        out = []
        for g in items:
            if g["Id"] in self.fail:
                out.append({"Errors": [{"Message": "Неверный регион", "Details": "нельзя"}]})
            else:
                self.live[g["Id"]] = list(g["RegionIds"])
                out.append({"Id": g["Id"]})
        return {"UpdateResults": out}


@pytest.fixture
def s(db, monkeypatch):
    user = make_user(db, "u@example.com")
    acc = make_account(db, user, make_connection(db, user))
    camp = make_campaign(db, user, acc, 101, "Кухни")
    out = {"db": db, "user": user, "acc": acc, "camp": camp}
    out["g1"] = _group(db, out, 11, "Москва", [213])
    out["g2"] = _group(db, out, 12, "Питер", [2])
    out["direct"] = FakeDirect({11: [213], 12: [2]})
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    monkeypatch.setattr(adgroups, "get_connection_access_token", lambda conn_id, user_id: "tok")
    monkeypatch.setattr(adgroups.direct_api, "get_all", out["direct"].get_all)
    monkeypatch.setattr(adgroups.direct_api, "call", out["direct"].call)
    return out


def _local(s, row_id):
    with s["db"].connection() as conn:
        return json.loads(conn.execute("SELECT region_ids FROM direct_adgroups WHERE id = %s", (row_id,)).fetchone()["region_ids"])


def test_overview_and_plan(s):
    got = adgroups.regions_overview(s["user"], s["camp"])
    assert got["region_ids"] == [213, 2] and got["same"] is False
    assert got["label"] == "Москва, Санкт-Петербург"
    assert [(g["name"], g["label"]) for g in got["groups"]] == [("Москва", "Москва"), ("Питер", "Санкт-Петербург")]
    assert [(r["name"], r["minus"]) for r in got["selected"]] == [("Москва", False), ("Санкт-Петербург", False)]

    plan = adgroups.regions_plan(s["user"], [1, -213], campaign_row_id=s["camp"])
    assert plan["what"] == "кампании «Кухни»" and plan["groups"] == 2 and plan["changed"] == 2 and plan["mixed"]
    assert plan["before"] == "Москва, Санкт-Петербург"
    assert plan["after"] == "Москва и Московская область, кроме: Москва"
    # Только посмотреть — в Директ ничего не ушло.
    assert s["direct"].updates == []

    one = adgroups.regions_plan(s["user"], [213], adgroup_row_ids=[s["g1"]])
    assert one["what"] == "группы «Москва»" and one["changed"] == 0


def test_set_campaign_regions_and_undo(s):
    # В Директе у первой группы регионы уже поменяли руками — откат вернёт их, а не устаревшую копию.
    s["direct"].live[11] = [213, 1]
    got = adgroups.set_regions(s["user"], [43], campaign_row_id=s["camp"])
    assert got["updated"] == 2 and got["unchanged"] == 0 and got["errors"] == []
    assert got["after"] == "Казань"
    assert s["direct"].live == {11: [43], 12: [43]}
    assert s["direct"].updates[0][0] == "v5"
    assert _local(s, s["g1"]) == [43] and _local(s, s["g2"]) == [43]

    entry = list_action_logs(s["user"])[-1]
    assert entry["action"] == "adgroups" and entry["title"].startswith("Регионы кампании «Кухни»:")
    assert entry["title"].endswith("→ Казань")
    assert entry["undo"]["label"] == "Вернуть прежние регионы"

    preview = undo.undo_preview(s["user"], entry["id"])
    assert preview["available"]
    assert preview["steps"] == ["Вернуть каждой группе прежние регионы: «Москва», «Питер»"]

    undo.run_undo(s["user"], entry["id"])
    assert s["direct"].live == {11: [213, 1], 12: [2]}
    assert _local(s, s["g1"]) == [213, 1] and _local(s, s["g2"]) == [2]
    # Откат пишет одну запись «Откат: …», без вложенных записей смены регионов.
    titles = [e["title"] for e in list_action_logs(s["user"])]
    assert sum(t.startswith("Регионы") for t in titles) == 1
    assert sum(t.startswith("Откат: Регионы") for t in titles) == 1 and len(titles) == 2


def test_same_regions_skipped(s):
    s["direct"].live[12] = [43]
    got = adgroups.set_regions(s["user"], [43], campaign_row_id=s["camp"])
    assert got["updated"] == 1 and got["unchanged"] == 1
    assert s["direct"].updates == [("v5", [(11, [43])])]
    # Локальная копия второй группы догнала Директ; в откат — только то, что поменяли.
    assert _local(s, s["g2"]) == [43]
    entry = list_action_logs(s["user"])[-1]
    assert undo.undo_preview(s["user"], entry["id"])["steps"] == ["Вернуть регионы Москва: «Москва»"]


def test_errors(s):
    s["direct"].fail = {11, 12}
    with pytest.raises(YandexError) as err:
        adgroups.set_regions(s["user"], [43], campaign_row_id=s["camp"])
    assert "Неверный регион" in err.value.message
    assert list_action_logs(s["user"]) == [] and _local(s, s["g1"]) == [213]

    # Частичный успех: одна группа обновилась, вторую Директ отклонил.
    s["direct"].fail = {12}
    got = adgroups.set_regions(s["user"], [43], campaign_row_id=s["camp"])
    assert got["updated"] == 1 and got["errors"] == ["группа «Питер»: Неверный регион: нельзя"]
    entry = list_action_logs(s["user"])[-1]
    assert entry["status"] == "partial"
    assert undo.undo_preview(s["user"], entry["id"])["steps"] == ["Вернуть регионы Москва: «Москва»"]

    # Группы нет в Директе.
    del s["direct"].live[12]
    s["direct"].fail = set()
    got = adgroups.set_regions(s["user"], [15], adgroup_row_ids=[s["g1"], s["g2"]])
    assert got["updated"] == 1 and "её нет в Директе" in got["errors"][0]

    for bad in ([-43], [43, 999999]):
        with pytest.raises(YandexError) as err:
            adgroups.set_regions(s["user"], bad, campaign_row_id=s["camp"])
        assert err.value.code == "args"
    with pytest.raises(YandexError) as err:
        adgroups.set_regions(s["user"], [43], adgroup_row_ids=[s["g1"], 999999])
    assert err.value.code == "not-found"
    empty = make_campaign(s["db"], s["user"], s["acc"], 102, "Пустая")
    with pytest.raises(YandexError) as err:
        adgroups.set_regions(s["user"], [43], campaign_row_id=empty)
    assert "нет групп" in err.value.message


def test_unified_campaign_uses_v501(s):
    with s["db"].connection() as conn:
        conn.execute("UPDATE direct_campaigns SET campaign_type = 'UNIFIED_CAMPAIGN' WHERE id = %s", (s["camp"],))
    adgroups.set_regions(s["user"], [43], adgroup_row_ids=[s["g1"]])
    assert s["direct"].updates == [("v501", [(11, [43])])]


def test_campaign_groups_come_from_direct(s):
    # Группу добавили в Директе после синка, а «Питер» там удалили: план и смена идут по группам Директа.
    s["direct"].live[13] = [225]
    s["direct"].names[13], s["direct"].campaigns[13] = "Новая", 101
    del s["direct"].live[12]
    plan = adgroups.regions_plan(s["user"], [43], campaign_row_id=s["camp"])
    assert plan["groups"] == 2 and plan["changed"] == 2
    assert [i["name"] for i in plan["items"]] == ["Москва", "Новая"]
    got = adgroups.set_regions(s["user"], [43], campaign_row_id=s["camp"])
    assert got["updated"] == 2 and got["errors"] == []
    assert s["direct"].live == {11: [43], 13: [43]}
    # Новая группа — в копии и в откате: «Отменить» вернёт ей «Россию».
    with s["db"].connection() as conn:
        new = conn.execute("SELECT id, name FROM direct_adgroups WHERE adgroup_id = 13").fetchone()
    assert new["name"] == "Новая" and _local(s, new["id"]) == [43]
    entry = list_action_logs(s["user"])[-1]
    undo.run_undo(s["user"], entry["id"])
    assert s["direct"].live == {11: [213], 13: [225]}


def test_campaign_groups_fall_back_to_copy(s):
    # Директ не отдал список групп — работаем по последней синхронизации, как раньше.
    s["direct"].list_error = YandexError("direct", "Директ временно недоступен")
    plan = adgroups.regions_plan(s["user"], [43], campaign_row_id=s["camp"])
    assert plan["groups"] == 2 and plan["changed"] == 2
    got = adgroups.set_regions(s["user"], [43], campaign_row_id=s["camp"])
    assert got["updated"] == 2 and s["direct"].live == {11: [43], 12: [43]}


def test_second_account_failure_keeps_journal(s, monkeypatch):
    # Группы двух кабинетов; токен второго истёк — первый уже обновлён, значит запись и откат нужны.
    other_conn = make_connection(s["db"], s["user"], "login2")
    other_acc = make_account(s["db"], s["user"], other_conn, login="acc2")
    other_camp = make_campaign(s["db"], s["user"], other_acc, 201, "Окна")
    with s["db"].connection() as conn:
        far = conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name, region_ids) "
            "VALUES (%s, %s, %s, 21, 'Ярославль', '[16]') RETURNING id",
            (s["user"], other_acc, other_camp),
        ).fetchone()["id"]
    s["direct"].live[21] = [16]

    def token(conn_id, user_id):
        if conn_id == other_conn:
            raise YandexError("token", "Доступ к Директу истёк — переподключите аккаунт")
        return "tok"

    monkeypatch.setattr(adgroups, "get_connection_access_token", token)
    got = adgroups.set_regions(s["user"], [43], adgroup_row_ids=[s["g1"], far])
    assert got["updated"] == 1 and got["errors"] == ["группа «Ярославль»: Доступ к Директу истёк — переподключите аккаунт"]
    assert s["direct"].live[11] == [43] and s["direct"].live[21] == [16]
    entry = list_action_logs(s["user"])[-1]
    assert entry["status"] == "partial" and undo.undo_preview(s["user"], entry["id"])["available"]
    undo.run_undo(s["user"], entry["id"])
    assert s["direct"].live[11] == [213]

    # Не обновилось ничего — ошибка, без записи в журнале.
    before = len(list_action_logs(s["user"]))
    with pytest.raises(YandexError) as err:
        adgroups.set_regions(s["user"], [43], adgroup_row_ids=[far])
    assert err.value.code == "token" and len(list_action_logs(s["user"])) == before


def test_undo_partial_failure_is_reported(s):
    adgroups.set_regions(s["user"], [43], campaign_row_id=s["camp"])
    entry = list_action_logs(s["user"])[-1]
    # «Москве» регионы вернулись, «Питер» Директ отклонил: откат — с ошибкой и понятным текстом, запись можно повторить.
    s["direct"].fail = {12}
    with pytest.raises(YandexError) as err:
        undo.run_undo(s["user"], entry["id"])
    assert err.value.message.startswith("Прежние регионы вернулись у 1 группы, но не везде:")
    assert "«Питер»" in err.value.message
    assert s["direct"].live == {11: [213], 12: [43]}
    assert undo.undo_preview(s["user"], entry["id"])["available"]
    # Повтор: «Москва» уже на месте и не трогается, «Питер» возвращается.
    s["direct"].fail = set()
    s["direct"].updates.clear()
    undo.run_undo(s["user"], entry["id"])
    assert s["direct"].live == {11: [213], 12: [2]}
    assert s["direct"].updates == [("v5", [(12, [2])])]


def test_api(s):
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    found = client.get("/api/web/direct/regions/search", params={"q": "казань"}, headers=auth).json()
    assert found["regions"][0]["id"] == 43
    overview = client.get(f"/api/web/direct/campaigns/{s['camp']}/regions", headers=auth).json()
    assert overview["region_ids"] == [213, 2]

    url = f"/api/web/direct/campaigns/{s['camp']}/regions"
    dry = client.post(url, json={"region_ids": [43], "dry_run": True}, headers=auth)
    assert dry.status_code == 200 and dry.json()["changed"] == 2 and "X-Undo-Log" not in dry.headers
    assert s["direct"].updates == []

    done = client.post(url, json={"region_ids": [43]}, headers=auth)
    assert done.status_code == 200 and done.json()["updated"] == 2
    assert done.headers.get("X-Undo-Log") == str(list_action_logs(s["user"])[-1]["id"])

    one = client.post(f"/api/web/direct/adgroups/{s['g1']}/regions", json={"region_ids": [-43]}, headers=auth)
    assert one.status_code == 400 and "хотя бы один" in one.json()["detail"]["message"]

    stranger = {"Authorization": f"Bearer {make_session(make_user(s['db'], 'x@example.com'))}"}
    assert client.post(url, json={"region_ids": [43], "dry_run": True}, headers=stranger).status_code == 404
