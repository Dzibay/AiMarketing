"""Общие списки минус-фраз на настоящем Postgres; Директ — подделка в памяти."""

import json

import pytest
from fastapi.testclient import TestClient

from app import negative_lists as nl
from app.clients import assign_direct_account, create_client
from app.main import app
from app.web_auth import make_session
from app.worker_lock import try_worker_lock
from app.yandex import YandexError
from tests.dbutil import make_account, make_connection, make_user


class FakeDirect:
    """campaigns.get / update по логину и Id; fail — Id кампаний, которым update вернёт ошибку."""

    def __init__(self, state: dict[int, list[str]]):
        self.state = state
        self.updates: list[dict] = []
        self.fail: set[int] = set()

    def __call__(self, token, login, method, params):
        if method == "get":
            ids = params["SelectionCriteria"]["Ids"]
            return {
                "Campaigns": [
                    {"Id": i, "NegativeKeywords": {"Items": self.state[i]} if self.state[i] else None}
                    for i in ids
                    if i in self.state
                ]
            }
        assert method == "update"
        assert len(params["Campaigns"]) <= nl.UPDATE_BATCH
        results = []
        for item in params["Campaigns"]:
            self.updates.append(item)
            if item["Id"] in self.fail:
                results.append({"Errors": [{"Code": 8800, "Message": "Объект не найден"}]})
                continue
            negatives = item["NegativeKeywords"]
            self.state[item["Id"]] = list(negatives["Items"]) if negatives else []
            results.append({"Id": item["Id"]})
        return {"UpdateResults": results}


def _campaign(pool, user, account, campaign_id, name, negatives=(), state="ON", kind="TEXT_CAMPAIGN") -> int:
    with pool.connection() as conn:
        return conn.execute(
            """
            INSERT INTO direct_campaigns (user_id, account_id, campaign_id, name, state, campaign_type, negative_keywords)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (user, account, campaign_id, name, state, kind, json.dumps(list(negatives), ensure_ascii=False)),
        ).fetchone()["id"]


def _local_negatives(pool, campaign_row_id):
    with pool.connection() as conn:
        raw = conn.execute(
            "SELECT negative_keywords FROM direct_campaigns WHERE id = %s", (campaign_row_id,)
        ).fetchone()["negative_keywords"]
    return json.loads(raw)


def _owned(pool, list_id, campaign_row_id):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT added_phrases FROM negative_list_campaigns WHERE list_id = %s AND campaign_row_id = %s",
            (list_id, campaign_row_id),
        ).fetchone()
    return None if row is None else list(row["added_phrases"])


@pytest.fixture
def seeded(db, monkeypatch):
    user = make_user(db)
    connection = make_connection(db, user, "agency")
    romashka = create_client(user, name="Ромашка")
    vector = create_client(user, name="Вектор")
    acc_r = make_account(db, user, connection, name="Ромашка", login="romashka")
    acc_v = make_account(db, user, connection, name="Вектор", login="vector")
    assign_direct_account(user, acc_r, romashka["id"])
    assign_direct_account(user, acc_v, vector["id"])
    camps = {
        # Локальная копия устарела: в Директе уже «отзывы» — применение читает свежие фразы.
        "search": _campaign(db, user, acc_r, 11, "Поиск", ["Бесплатно"]),
        "archived": _campaign(db, user, acc_r, 12, "Архив", state="ARCHIVED"),
        "goods": _campaign(db, user, acc_v, 21, "Товары"),
        "banner": _campaign(db, user, acc_v, 22, "Баннер", kind="CPM_BANNER_CAMPAIGN"),
    }
    direct = FakeDirect({11: ["бесплатно", "отзывы"], 12: [], 21: [], 22: []})
    monkeypatch.setattr(nl, "get_connection_access_token", lambda *a: "tok")
    monkeypatch.setattr(nl, "_call_campaigns", direct)
    return {"user": user, "c": camps, "direct": direct}


def test_list_crud_and_validation(seeded, db):
    user = seeded["user"]
    created = nl.create_negative_list(user, name="  Стоп  ", phrases="-бесплатно -скачать\nработа, вакансия")
    assert (created["name"], created["phrases"], created["linked"]) == ("Стоп", ["бесплатно", "скачать", "работа", "вакансия"], 0)

    with pytest.raises(YandexError) as exc:
        nl.create_negative_list(user, name="", phrases="x")
    assert exc.value.code == "empty"
    with pytest.raises(YandexError) as exc:
        nl.update_negative_list(user, created["id"], phrases="раз два три четыре пять шесть семь восемь")
    assert exc.value.code == "phrase" and "больше 7 слов" in exc.value.message
    with pytest.raises(YandexError) as exc:
        nl.update_negative_list(user, created["id"], phrases=[f"слово{i:03d}" * 4 for i in range(700)])
    assert exc.value.code == "negatives-limit"

    other = make_user(db, "other@example.com")
    with pytest.raises(YandexError) as exc:
        nl.get_negative_list(other, created["id"])
    assert exc.value.code == "not-found"

    renamed = nl.update_negative_list(user, created["id"], name="Стоп-слова")
    assert renamed["name"] == "Стоп-слова"
    nl.delete_negative_list(user, created["id"])
    assert nl.list_negative_lists(user) == []


def test_targets_mark_unsupported_campaigns(seeded):
    targets = {t["name"]: t for t in nl.list_targets(seeded["user"])}
    assert targets["Поиск"]["client_name"] == "Ромашка" and targets["Поиск"]["negatives"] == 1
    assert targets["Архив"]["blocked"] == "archived"
    assert targets["Баннер"]["blocked"] == "unsupported"
    assert targets["Товары"]["blocked"] == ""


def test_preview_apply_update_and_detach(seeded, db):
    user, c, direct = seeded["user"], seeded["c"], seeded["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="-бесплатно -скачать\nработа\nвакансия")
    everything = list(c.values())

    preview = nl.preview_apply(user, stop["id"], everything)
    rows = {r["campaign_name"]: r for r in preview["campaigns"]}
    assert rows["Поиск"]["action"] == "attach"
    assert rows["Поиск"]["add"] == ["скачать", "работа", "вакансия"]  # «Бесплатно» уже есть (по локальной копии)
    assert rows["Архив"]["blocked"] == "archived"
    assert rows["Баннер"]["blocked"] == "unsupported"
    assert preview["totals"] == {
        "campaigns": 4, "changed": 2, "add": 7, "remove": 0, "blocked": 2, "attach": 2, "detach": 0,
    }
    assert direct.updates == []  # предпросмотр в Директ не пишет

    result = nl.apply_negative_list(user, stop["id"], everything)
    assert (result["status"], result["updated"], result["linked"], result["added"]) == ("ok", 2, 2, 7)
    assert {s["reason"] for s in result["skipped"]} == {"archived", "unsupported"}
    assert direct.state[11] == ["бесплатно", "отзывы", "скачать", "работа", "вакансия"]
    assert direct.state[21] == ["бесплатно", "скачать", "работа", "вакансия"]
    assert _local_negatives(db, c["search"]) == direct.state[11]
    assert _owned(db, stop["id"], c["search"]) == ["скачать", "работа", "вакансия"]

    # Второй список кладёт «работа» в ту же кампанию — фраза уже есть, её хозяин пока «Стоп».
    jobs = nl.create_negative_list(user, name="Работа", phrases="работа\nрезюме")
    nl.apply_negative_list(user, jobs["id"], [c["search"]])
    assert _owned(db, jobs["id"], c["search"]) == ["резюме"]

    # Из «Стоп» убрали «работа», добавили «торрент»: обе кампании ждут обновления.
    stop = nl.update_negative_list(user, stop["id"], phrases="бесплатно\nскачать\nвакансия\nторрент")
    assert stop["stale"] == 2
    result = nl.apply_negative_list(user, stop["id"], [c["search"], c["goods"]])
    assert result["removed"] == 1  # «работа» ушла только из «Товаров»
    assert "работа" in direct.state[11]
    assert direct.state[21] == ["бесплатно", "скачать", "вакансия", "торрент"]
    assert _owned(db, jobs["id"], c["search"]) == ["резюме", "работа"]  # «работа» перешла к «Работа»
    assert nl.get_negative_list(user, stop["id"])["stale"] == 0

    # Отвязка от «Поиска»: уходят только фразы «Стоп»; свои и чужие остаются.
    nl.apply_negative_list(user, stop["id"], [c["goods"]])
    assert direct.state[11] == ["бесплатно", "отзывы", "работа", "резюме"]
    assert _owned(db, stop["id"], c["search"]) is None

    with pytest.raises(YandexError) as exc:
        nl.delete_negative_list(user, stop["id"])
    assert exc.value.code == "in-use"

    # Последняя отвязка опустошает «Товары» — Директу уходит null.
    nl.apply_negative_list(user, stop["id"], [])
    assert direct.updates[-1] == {"Id": 21, "NegativeKeywords": None}
    assert direct.state[21] == []
    nl.delete_negative_list(user, stop["id"])


def test_apply_reports_direct_errors(seeded):
    user, c, direct = seeded["user"], seeded["c"], seeded["direct"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    direct.fail = {21}
    result = nl.apply_negative_list(user, stop["id"], [c["search"], c["goods"]])
    assert result["status"] == "partial"
    assert result["errors"] == [
        {"campaign_row_id": c["goods"], "client_login": "vector", "error": "direct", "message": "Объект не найден"}
    ]
    assert nl.get_negative_list(user, stop["id"])["linked"] == 1

    direct.fail = {11, 21}
    with pytest.raises(YandexError) as exc:
        nl.apply_negative_list(user, stop["id"], [c["goods"]])
    assert (exc.value.code, exc.value.message) == ("direct", "Объект не найден")


def test_parallel_apply_waits(seeded):
    user, c = seeded["user"], seeded["c"]
    stop = nl.create_negative_list(user, name="Стоп", phrases="скачать")
    with try_worker_lock(f"negative-lists:{user}") as locked:
        assert locked
        with pytest.raises(YandexError) as exc:
            nl.apply_negative_list(user, stop["id"], [c["search"]])
    assert exc.value.code == "busy"


def test_api(seeded, db):
    user, c = seeded["user"], seeded["c"]
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(user)}"}

    assert client.get("/api/web/negative-lists").status_code == 401
    start = client.get("/api/web/negative-lists", headers=auth).json()
    assert start["items"] == [] and start["templates"][0]["phrases"]

    created = client.post("/api/web/negative-lists", headers=auth, json={"name": "Стоп", "phrases": ["скачать"]})
    list_id = created.json()["id"]
    bad = client.patch(f"/api/web/negative-lists/{list_id}", headers=auth, json={"phrases": "купить -слона"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "phrase"

    targets = client.get("/api/web/negative-lists/targets", headers=auth).json()["items"]
    assert len(targets) == 4

    preview = client.post(f"/api/web/negative-lists/{list_id}/preview", headers=auth, json={"campaign_row_ids": [c["goods"]]})
    assert preview.json()["totals"]["add"] == 1

    foreign = client.post(f"/api/web/negative-lists/{list_id}/apply", headers=auth, json={"campaign_row_ids": [999999]})
    assert foreign.status_code == 404
    job = client.post(f"/api/web/negative-lists/{list_id}/apply", headers=auth, json={"campaign_row_ids": [c["goods"]]})
    assert job.json()["job"]["kind"] == "apply_negative_list"
    assert job.json()["job"]["payload"] == {"list_id": list_id, "campaign_row_ids": [c["goods"]]}
