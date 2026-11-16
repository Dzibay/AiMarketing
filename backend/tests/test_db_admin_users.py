"""Список пользователей в админке: поиск, фильтры, сортировка, страницы, итоги — и карточка для боковой панели."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import admin_users, plans
from app.clock import now_msk
from app.main import app
from app.yandex import YandexError
from tests.dbutil import admin_headers, make_account, make_campaign, make_connection, make_plan, make_user, make_workspace


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _pay(pool, uid, plan, amount, *, days=30, refunded=0, test=False, status="succeeded", ago=0):
    _sql(
        pool,
        """
        INSERT INTO payments (user_id, plan_id, plan_name, days, kind, amount_rub, status, refunded_rub, test, paid_at)
        VALUES (%s, %s, %s, %s, 'renew', %s, %s, %s, %s, NOW() - %s * INTERVAL '1 day')
        """,
        (uid, plan["id"], plan["name"], days, amount, status, refunded, test, ago),
    )


def _spend(pool, uid, units, cost, *, ago=0, task="chat", chat=None, ws=None):
    _sql(
        pool,
        """
        INSERT INTO ai_usage (user_id, workspace_id, chat_id, kind, task, model, model_class, charged, cost_rub, created_at)
        VALUES (%s, %s, %s, 'model', %s, 'gpt', 'standard', %s, %s, NOW() - %s * INTERVAL '1 day')
        """,
        (uid, ws, chat, task, units, cost, ago),
    )


def _seen(pool, uid, days_ago):
    _sql(pool, "UPDATE users SET last_seen_at = %s WHERE id = %s", (now_msk() - timedelta(days=days_ago), uid))


@pytest.fixture
def world(db):
    pro = make_plan(name="Про", price_rub=990, days=30, units_limit=100)
    trial = make_plan(name="Пробный", price_rub=0, days=7, is_trial=True)
    group = _sql(db, "INSERT INTO ref_link_groups (name) VALUES ('Реклама') RETURNING id").fetchone()["id"]
    link = _sql(db, "INSERT INTO ref_links (code, name, group_id) VALUES ('vk-ads', 'ВК', %s) RETURNING id", (group,)).fetchone()["id"]

    paying = make_user(db, "anna@example.com")
    _sql(db, "UPDATE users SET name = 'Анна', ref_link_id = %s, email_verified_at = NOW() WHERE id = %s", (link, paying))
    plans.set_subscription(paying, plan_id=pro["id"])
    _pay(db, paying, pro, 990, ago=40)
    _pay(db, paying, pro, 2970, days=90, ago=1)
    _pay(db, paying, pro, 990, refunded=990)  # возвращён целиком — не считается
    _pay(db, paying, pro, 500, test=True)  # тестовый — не считается
    _spend(db, paying, 30, 12.5)
    _seen(db, paying, 0)

    tried = make_user(db, "boris@example.com")
    owner_link = _sql(
        db, "INSERT INTO ref_links (code, name, owner_user_id) VALUES ('anna1', '', %s) RETURNING id", (paying,)
    ).fetchone()["id"]
    _sql(db, "UPDATE users SET ref_link_id = %s WHERE id = %s", (owner_link, tried))
    plans.set_subscription(tried, plan_id=trial["id"])
    _spend(db, tried, 5, 40.0)
    _seen(db, tried, 5)

    lapsed = make_user(db, "vera@example.com")
    _sql(db, "UPDATE users SET first_utm = %s, visitor_id = 'v1' WHERE id = %s", (Jsonb({"utm_source": "yandex"}), lapsed))
    plans.set_subscription(lapsed, plan_id=pro["id"], until=(now_msk() - timedelta(days=3)).date().isoformat())
    _seen(db, lapsed, 20)

    blocked = make_user(db, "gleb@example.com")
    _sql(db, "UPDATE users SET blocked_at = NOW(), visitor_id = 'v2' WHERE id = %s", (blocked,))
    plans.set_subscription(blocked, plan_id=None)
    return {"pro": pro, "trial": trial, "group": group, "link": link, "ids": (paying, tried, lapsed, blocked)}


def _emails(out):
    return [i["email"] for i in out["items"]]


def test_list_rows_and_summary(world):
    paying, tried, lapsed, blocked = world["ids"]
    out = admin_users.list_page()
    assert out["summary"] == {"found": 4, "paying": 1, "today": 1}
    assert (out["page"], out["pages"], out["per_page"]) == (1, 1, 50)
    assert _emails(out) == ["gleb@example.com", "vera@example.com", "boris@example.com", "anna@example.com"]

    rows = {i["id"]: i for i in out["items"]}
    anna = rows[paying]
    assert anna["paid"] == {"rub": 3960.0, "count": 2, "days": 120}
    assert anna["ai_cost_rub"] == 12.5 and anna["margin_rub"] == 3947.5
    assert anna["usage"] == {"spent": 30.0, "limit": 100.0}
    assert anna["last_payment"]["amount_rub"] == 2970.0 and anna["last_payment"]["days"] == 90
    assert anna["source"]["kind"] == "ref" and anna["source"]["code"] == "vk-ads" and anna["source"]["group_name"] == "Реклама"
    assert anna["subscription"]["plan"]["name"] == "Про" and anna["email_verified"]

    boris = rows[tried]
    assert boris["source"]["owner_email"] == "anna@example.com"
    assert boris["margin_rub"] == -40.0 and boris["paid"] == {"rub": 0.0, "count": 0, "days": 0}
    assert rows[lapsed]["source"] == {"kind": "utm", "utm": {"utm_source": "yandex"}}
    assert rows[lapsed]["subscription"]["expired"]
    assert rows[blocked]["source"] == {"kind": "direct"} and rows[blocked]["blocked"]
    assert rows[blocked]["last_seen_at"] is None


def test_filters_and_search(world):
    paying, tried, lapsed, blocked = world["ids"]

    def find(**f):
        return sorted(i["id"] for i in admin_users.list_page(f)["items"])

    assert find(sub="paying") == [paying]
    assert find(sub="trial") == [tried]
    assert find(sub="expired") == [lapsed]
    assert find(sub="none") == [blocked]
    assert find(plan=world["pro"]["id"]) == [paying, lapsed]
    assert find(activity="today") == [paying]
    assert find(activity="week") == [paying, tried]
    assert find(activity="lost") == [lapsed]
    assert find(activity="never") == [blocked]
    assert find(source=f"link:{world['link']}") == [paying]
    assert find(source=f"group:{world['group']}") == [paying]
    assert find(source="personal") == [tried]
    assert find(source="ref") == [paying, tried]
    assert find(source="none") == [lapsed, blocked]
    assert find(paid="yes") == [paying]
    assert find(paid="no") == [tried, lapsed, blocked]
    assert find(blocked=True) == [blocked]
    assert find(unverified=True) == [tried, lapsed, blocked]
    assert find(q="АННА") == [paying]
    assert find(q="boris@") == [tried]
    assert find(q="vk-ads") == [paying]
    assert find(q=f"#{lapsed}") == [lapsed]
    assert find(q="100%") == []
    assert find(sub="paying", activity="lost") == []


def test_sort_and_pages(world, monkeypatch):
    paying, tried, lapsed, blocked = world["ids"]

    def order(sort, desc=True):
        return [i["id"] for i in admin_users.list_page(sort=sort, desc=desc)["items"]]

    assert order("paid")[0] == paying
    assert order("ai") == [tried, paying, blocked, lapsed]
    assert order("seen") == [paying, tried, lapsed, blocked]  # ни разу — в конце
    assert order("seen", desc=False) == [lapsed, tried, paying, blocked]
    assert order("ends", desc=False)[0] == lapsed
    assert order("bogus") == order("created")

    monkeypatch.setattr(admin_users, "PER_PAGE", 3)
    first = admin_users.list_page(page=1)
    last = admin_users.list_page(page=9)  # за пределами — последняя
    assert (first["pages"], len(first["items"]), last["page"], len(last["items"])) == (2, 3, 2, 1)
    assert first["summary"]["found"] == 4


def test_card(db, world):
    paying = world["ids"][0]
    acc = make_account(db, paying, make_connection(db, paying), login="anna-direct")
    camp = make_campaign(db, paying, acc, 11, "Кухни")
    ws = make_workspace(db, paying, 777, "Кухни", accounts=[acc], campaigns=[camp])
    chat = _sql(db, "INSERT INTO ai_chats (user_id, workspace_id, title) VALUES (%s, %s, 'План') RETURNING id", (paying, ws)).fetchone()["id"]
    _sql(db, "INSERT INTO ai_messages (chat_id, role, content) VALUES (%s, 'user', 'привет'), (%s, 'assistant', 'ок')", (chat, chat))
    _spend(db, paying, 3, 2.0, task="chat", chat=chat, ws=ws)
    _spend(db, paying, 1, 9.0, ago=45)  # за пределами 30 дней
    today = now_msk().date()
    _sql(
        db,
        "INSERT INTO user_activity_days (user_id, day, visits, actions) VALUES (%s, %s, 2, 1), (%s, %s, 1, 0)",
        (paying, today, paying, today - timedelta(days=3)),
    )
    _sql(db, "INSERT INTO support_threads (user_id) VALUES (%s)", (paying,))
    _sql(
        db,
        "INSERT INTO support_messages (thread_id, author, body) SELECT id, 'user', 'помогите' FROM support_threads WHERE user_id = %s",
        (paying,),
    )

    c = admin_users.card(paying)
    assert c["user"]["id"] == paying and c["user"]["support_unread"] == 1
    assert c["user"]["projects"] == {"count": 1, "direct": True, "campaigns": 1}
    assert len(c["days"]) == 30 and c["days"][-1] == {"day": today.isoformat(), "visits": 2, "actions": 1}
    assert c["days"][-4]["visits"] == 1 and sum(d["visits"] for d in c["days"]) == 3
    assert len(c["ai_days"]) == 30 and c["ai_days"][-1]["cost_rub"] == 14.5
    assert sum(d["cost_rub"] for d in c["ai_days"]) == 14.5
    assert [(t["task"], t["calls"], t["cost_rub"]) for t in c["ai_tasks"]] == [("chat", 2, 14.5)]
    assert c["chats"] == [
        {
            "id": chat, "title": "План", "project": "Кухни", "general": False, "archived": False,
            "messages": 2, "cost_rub": 2.0, "created_at": c["chats"][0]["created_at"], "last_at": c["chats"][0]["last_at"],
        }
    ]
    assert c["project_list"][0]["direct"] == "anna-direct" and c["project_list"][0]["campaigns"] == 1
    with pytest.raises(YandexError):
        admin_users.card(999999)


def test_api(world):
    paying = world["ids"][0]
    client = TestClient(app)
    h = admin_headers()
    assert client.get("/api/admin/users").status_code == 401
    out = client.get("/api/admin/users?sub=paying&sort=paid&dir=asc&blocked=false", headers=h).json()
    assert [i["id"] for i in out["items"]] == [paying] and out["summary"]["found"] == 1
    assert {p["name"] for p in out["options"]["plans"]} == {"Про", "Пробный"}
    assert [l["code"] for l in out["options"]["links"]] == ["vk-ads"]  # личные ссылки — без списка
    assert out["options"]["groups"][0]["name"] == "Реклама"
    # Неизвестные значения фильтров не ломают выборку.
    assert client.get("/api/admin/users?sub=x&activity=y&source=link:abc", headers=h).json()["summary"]["found"] == 4
    assert client.get(f"/api/admin/users/{paying}/card", headers=h).json()["user"]["email"] == "anna@example.com"
    assert client.get("/api/admin/users/999999/card", headers=h).status_code == 404
