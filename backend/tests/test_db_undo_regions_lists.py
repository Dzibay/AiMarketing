"""Откат смены регионов кампании (geo) и общих списков минус-фраз на настоящем Postgres; Директ — подделка."""

import json

import pytest

from app import adgroups, geo, negatives, undo
from app import negative_lists as nl
from app.adgroups import set_regions
from app.journal import acting_as, list_action_logs, log_action
from app.yandex import YandexError
from tests.dbutil import make_account, make_campaign, make_connection, make_user
from tests.test_db_negative_lists import FakeDirect as FakeCampaigns
from tests.test_db_negative_lists import _campaign, _owned
from tests.test_geo_resolve_unit import REGIONS


class FakeGroups:
    """Группы в «Директе»: CampaignIds или Ids → RegionIds; update меняет их; fail — Id, которые Директ отклонит."""

    def __init__(self, live: dict[int, list[int]]):
        self.live = live
        self.fail: set[int] = set()
        self.updates: list[tuple[str, list]] = []

    def get_all(self, token, login, service, params, key):
        assert (service, key) == ("adgroups", "AdGroups") and "RegionIds" in params["FieldNames"]
        where = params["SelectionCriteria"]
        ids = list(self.live) if "CampaignIds" in where else where["Ids"]
        return [
            {"Id": i, "RegionIds": self.live[i], "Name": {11: "Москва", 12: "Питер"}.get(i, f"Группа {i}"), "CampaignId": 101, "Type": "TEXT_AD_GROUP"}
            for i in ids
            if i in self.live
        ]

    def call(self, token, login, service, method, params, *, version="v5", **kw):
        assert (service, method) == ("adgroups", "update")
        self.updates.append((version, [(g["Id"], g["RegionIds"]) for g in params["AdGroups"]]))
        out = []
        for g in params["AdGroups"]:
            if g["Id"] in self.fail:
                out.append({"Errors": [{"Message": "Неверный регион"}]})
            else:
                self.live[g["Id"]] = list(g["RegionIds"])
                out.append({"Id": g["Id"]})
        return {"UpdateResults": out}


def _group(db, user, acc, camp, gid, name, regions):
    with db.connection() as conn:
        return conn.execute(
            "INSERT INTO direct_adgroups (user_id, account_id, campaign_row_id, adgroup_id, name, region_ids) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (user, acc, camp, gid, name, json.dumps(regions)),
        ).fetchone()["id"]


def _last(user):
    """Последняя запись журнала (список истории — от новых к старым)."""
    return max(list_action_logs(user), key=lambda e: e["id"])


def _local(db, row_id):
    with db.connection() as conn:
        raw = conn.execute("SELECT region_ids FROM direct_adgroups WHERE id = %s", (row_id,)).fetchone()["region_ids"]
    return json.loads(raw)


@pytest.fixture
def g(db, monkeypatch):
    user = make_user(db, "u@example.com")
    acc = make_account(db, user, make_connection(db, user))
    camp = make_campaign(db, user, acc, 101, "Кухни")
    fake = FakeGroups({11: [213], 12: [2, -10174]})
    monkeypatch.setattr(geo, "load_regions", lambda user_id: REGIONS)
    monkeypatch.setattr(geo, "get_connection_access_token", lambda *a: "tok")
    monkeypatch.setattr(adgroups, "get_connection_access_token", lambda *a: "tok")
    monkeypatch.setattr(geo.direct_api, "get_all", fake.get_all)
    monkeypatch.setattr(geo.direct_api, "call", fake.call)
    return {
        "user": user,
        "acc": acc,
        "camp": camp,
        "direct": fake,
        "g1": _group(db, user, acc, camp, 11, "Москва", [213]),
        "g2": _group(db, user, acc, camp, 12, "Питер", [2, -10174]),
    }


def test_campaign_regions_undo_restores_each_group(g, db):
    got = geo.set_campaign_regions(g["user"], g["camp"], [43])
    assert got["updated"] == 2 and g["direct"].live == {11: [43], 12: [43]}

    entry = _last(g["user"])
    assert entry["undo"]["label"] == "Вернуть прежние регионы"
    with db.connection() as conn:
        stored = conn.execute("SELECT undo FROM action_logs WHERE id = %s", (entry["id"],)).fetchone()["undo"]
    # Прежние регионы — у каждой группы свои, а не один общий список.
    assert stored["steps"] == [
        {"op": "campaign_group_regions", "args": {"campaign_row_id": g["camp"], "groups": {"11": [213], "12": [2, -10174]}}}
    ]
    preview = undo.undo_preview(g["user"], entry["id"])
    assert preview["available"] and preview["warnings"] == []
    assert preview["steps"] == ["Вернуть каждой группе прежние регионы: «Кухни»"]

    # Группу 11 потом вернули руками — её не трогаем, вторую возвращаем.
    g["direct"].live[11] = [213]
    undo.run_undo(g["user"], entry["id"])
    assert g["direct"].live == {11: [213], 12: [2, -10174]}
    assert g["direct"].updates[-1] == ("v5", [(12, [2, -10174])])
    assert _local(db, g["g1"]) == [43] and _local(db, g["g2"]) == [2, -10174]


def test_campaign_regions_undo_partial_failure_keeps_entry(g):
    geo.set_campaign_regions(g["user"], g["camp"], [43])
    entry = _last(g["user"])
    g["direct"].fail = {12}
    with pytest.raises(YandexError) as exc:
        undo.run_undo(g["user"], entry["id"])
    assert "вернулись у 1" in exc.value.message and "группа 12" in exc.value.message
    assert g["direct"].live[11] == [213]
    # Откат не удался целиком (единственный шаг с ошибкой) — запись снова можно откатить.
    g["direct"].fail = set()
    assert undo.undo_preview(g["user"], entry["id"])["available"]
    undo.run_undo(g["user"], entry["id"])
    assert g["direct"].live == {11: [213], 12: [2, -10174]}


def test_region_undo_warns_on_later_campaign_change(g):
    set_regions(g["user"], [43], campaign_row_id=g["camp"])
    entry = _last(g["user"])
    with acting_as("user"):
        # Позже поменяли регионы одной группы — запись по группе, а не по кампании.
        set_regions(g["user"], [2], adgroup_row_ids=[g["g1"]])
    later = _last(g["user"])
    preview = undo.undo_preview(g["user"], entry["id"])
    assert [x["id"] for x in preview["later"]] == [later["id"]]
    assert any("меняли ещё раз" in w for w in preview["warnings"])
    # И наоборот: правка группы предупреждает о более поздней правке всей кампании.
    log_action(g["user"], "pause", {"campaign_row_ids": [g["camp"]]}, {}, "ok")
    assert undo.undo_preview(g["user"], later["id"])["later"]


def test_ai_decision_picks_up_region_undo(g):
    """«Вернуть как было» у решения ИИ берёт его записи по метке ai_action:N — и откат регионов в них."""
    from app.journal import ai_action_logs, for_ai_action

    with for_ai_action(777):
        set_regions(g["user"], [43], campaign_row_id=g["camp"])
    ids = [int(r["id"]) for r in ai_action_logs(g["user"], [777])[777]]
    preview = undo.undo_group_preview(g["user"], ids, title="Действие ИИ", limit=undo.MAX_DECISION)
    assert preview["available"] and preview["steps"] == ["Вернуть каждой группе прежние регионы: «Москва», «Питер»"]
    undo.run_undo_group(g["user"], preview["log_ids"])
    assert g["direct"].live == {11: [213], 12: [2, -10174]}


# --- Общие списки минус-фраз ---------------------------------------------------------------------


@pytest.fixture
def lists(db, monkeypatch):
    user = make_user(db, "n@example.com")
    acc = make_account(db, user, make_connection(db, user, "agency"), name="Ромашка", login="romashka")
    camps = {
        "search": _campaign(db, user, acc, 11, "Поиск", ["бесплатно"]),
        "goods": _campaign(db, user, acc, 21, "Товары"),
    }
    direct = FakeCampaigns({11: ["бесплатно", "отзывы"], 21: []})
    for module in (nl, negatives):
        monkeypatch.setattr(module, "get_connection_access_token", lambda *a: "tok")
        monkeypatch.setattr(module, "_call_campaigns", direct)
    return {"user": user, "c": camps, "direct": direct}


def test_list_apply_undo_restores_phrases_and_links(lists, db):
    user, c, direct = lists["user"], lists["c"], lists["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="бесплатно\nскачать")
    nl.apply_negative_list(user, stop["id"], [c["search"], c["goods"]])
    assert direct.state == {11: ["бесплатно", "отзывы", "скачать"], 21: ["бесплатно", "скачать"]}
    entry = _last(user)
    assert entry["undo"]["label"] == "Вернуть как было"
    preview = undo.undo_preview(user, entry["id"])
    assert preview["steps"] == ["Список минус-фраз «Стоп»: снять с кампаний «Поиск», «Товары»"]

    undo.run_undo(user, entry["id"])
    # «бесплатно» было в «Поиске» до списка — осталось; добавленное списком ушло.
    assert direct.state == {11: ["бесплатно", "отзывы"], 21: []}
    assert _owned(db, stop["id"], c["search"]) is None and _owned(db, stop["id"], c["goods"]) is None
    assert nl.get_negative_list(user, stop["id"])["linked"] == 0


def test_list_detach_undo_with_handover(lists, db):
    user, c, direct = lists["user"], lists["c"], lists["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать\nработа")
    jobs = nl.create_negative_list(user, name="Работа", phrases="работа\nрезюме")
    nl.apply_negative_list(user, stop["id"], [c["search"], c["goods"]])
    nl.apply_negative_list(user, jobs["id"], [c["search"]])
    applied_at = nl.get_negative_list(user, stop["id"])["campaigns"][0]["applied_at"]

    # Снимаем «Стоп» с «Поиска»: «скачать» уходит, «работа» переходит к «Работа».
    nl.apply_negative_list(user, stop["id"], [c["goods"]])
    assert direct.state[11] == ["бесплатно", "отзывы", "работа", "резюме"]
    assert _owned(db, jobs["id"], c["search"]) == ["резюме", "работа"]
    entry = _last(user)
    preview = undo.undo_preview(user, entry["id"])
    # «Товары» не менялись — откату там делать нечего.
    assert preview["steps"] == ["Список минус-фраз «Стоп»: вернуть как было в кампаниях «Поиск»"]

    undo.run_undo(user, entry["id"])
    assert direct.state[11] == ["бесплатно", "отзывы", "работа", "резюме", "скачать"]
    assert _owned(db, stop["id"], c["search"]) == ["скачать", "работа"]
    assert _owned(db, jobs["id"], c["search"]) == ["резюме"]
    restored = {x["campaign_name"]: x for x in nl.get_negative_list(user, stop["id"])["campaigns"]}
    assert restored["Поиск"]["applied_at"] == applied_at


def test_list_undo_errors_and_later_warning(lists, db):
    user, c, direct = lists["user"], lists["c"], lists["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [c["search"], c["goods"]])
    entry = _last(user)
    log_action(user, "pause", {"campaign_row_ids": [c["goods"]]}, {}, "ok")
    assert any("меняли ещё раз" in w for w in undo.undo_preview(user, entry["id"])["warnings"])

    direct.fail = {21}
    with pytest.raises(YandexError):
        undo.run_undo(user, entry["id"])
    # Частично: «Поиск» вернулся, «Товары» — нет, привязка там осталась (фразы списка всё ещё в кампании).
    assert direct.state[11] == ["бесплатно", "отзывы"]
    assert _owned(db, stop["id"], c["search"]) is None
    assert _owned(db, stop["id"], c["goods"]) == ["скачать"]

    out = nl.restore_links(user, stop["id"], {})
    assert out == {"restored": 0, "missing": 0}


def test_list_undo_after_list_deleted(lists):
    user, c = lists["user"], lists["c"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [c["goods"]])
    nl.apply_negative_list(user, stop["id"], [])
    detach = _last(user)
    nl.delete_negative_list(user, stop["id"])
    with pytest.raises(YandexError) as exc:
        undo.run_undo(user, detach["id"])
    assert "уже удалён" in exc.value.message
    assert lists["direct"].state[21] == []


def test_list_undo_holds_one_lock_for_phrases_and_links(lists, monkeypatch):
    """Фразы в Директе и привязка у нас откатываются под одним замком: задача применения списка не вклинится между ними."""
    from app.worker_lock import try_worker_lock

    user, c, direct = lists["user"], lists["c"], lists["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    nl.apply_negative_list(user, stop["id"], [c["goods"]])
    entry = _last(user)

    def lock_free() -> bool:
        with try_worker_lock(f"negative-lists:{user}") as locked:
            return locked

    seen: list[tuple[str, bool]] = []

    def spy_direct(*a, **kw):
        seen.append(("direct", lock_free()))
        return direct(*a, **kw)

    real_rows = nl._restore_link_rows

    def spy_rows(*a, **kw):
        seen.append(("links", lock_free()))
        return real_rows(*a, **kw)

    monkeypatch.setattr(negatives, "_call_campaigns", spy_direct)
    monkeypatch.setattr(nl, "_restore_link_rows", spy_rows)
    released: list[bool] = []
    real_lock = negatives.negatives_lock

    def counting_lock(*a, **kw):
        released.append(True)
        return real_lock(*a, **kw)

    monkeypatch.setattr(negatives, "negatives_lock", counting_lock)
    undo.run_undo(user, entry["id"])
    assert direct.state[21] == []
    assert seen and all(not free for _, free in seen)
    assert ("links", False) in seen
    assert len(released) == 1  # замок берётся один раз на весь откат
    assert lock_free()
