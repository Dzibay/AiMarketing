"""Страница «Реф-ссылки» админки: группы, таблица за период, каскад, мини-график, личные ссылки, действия."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import admin_refs, admin_users, referrals
from app.clock import MSK, now_msk
from app.main import app
from tests.dbutil import admin_headers, make_plan, make_user


def _sql(pool, query, params=()):
    with pool.connection() as conn:
        return conn.execute(query, params)


def _pay(pool, uid, plan, amount, paid_at, *, refunded=0, test=False):
    _sql(
        pool,
        """
        INSERT INTO payments (user_id, plan_id, plan_name, days, kind, amount_rub, status, refunded_rub, test, paid_at)
        VALUES (%s, %s, %s, 30, 'renew', %s, 'succeeded', %s, %s, %s)
        """,
        (uid, plan["id"], plan["name"], amount, refunded, test, paid_at),
    )


def _user(pool, email, link_id, ago_days, now):
    uid = make_user(pool, email)
    _sql(pool, "UPDATE users SET ref_link_id = %s, created_at = %s WHERE id = %s", (link_id, now - timedelta(days=ago_days, minutes=1), uid))
    return uid


@pytest.fixture
def client(db):
    return TestClient(app)


@pytest.fixture
def world(db):
    """Ссылка blog: a (10 дней назад, две оплаты), b (60 дней назад, оплата 50 дней назад и 5 дней назад),
    c (2 дня назад, без оплат). У a личная ссылка — по ней пришёл f (3 дня назад, оплатил). Ссылка tg пустая."""
    now = now_msk()
    today = now.astimezone(MSK).date()
    pro = make_plan(name="Про", price_rub=1000, days=30)
    blog = referrals.save_link(None, {"code": "blog", "name": "Блог"})
    tg = referrals.save_link(None, {"code": "tg", "name": "Телеграм"})
    a = _user(db, "a@example.com", blog["id"], 10, now)
    b = _user(db, "b@example.com", blog["id"], 60, now)
    _user(db, "c@example.com", blog["id"], 2, now)
    personal = referrals.my_link(a, create=True)
    pid = _sql(db, "SELECT id FROM ref_links WHERE code = %s", (personal["code"],)).fetchone()["id"]
    f = _user(db, "f@example.com", pid, 3, now)

    _pay(db, a, pro, 1000, now - timedelta(days=9))
    _pay(db, a, pro, 1000, now - timedelta(days=1), refunded=200)
    _pay(db, a, pro, 1000, now - timedelta(days=1), refunded=1000)  # возвращён целиком — не оплата
    _pay(db, a, pro, 1000, now - timedelta(days=1), test=True)  # тестовый — не оплата
    _pay(db, b, pro, 1000, now - timedelta(days=50))
    _pay(db, b, pro, 1000, now - timedelta(days=5))
    _pay(db, f, pro, 500, now - timedelta(days=2))

    _sql(db, "UPDATE ref_links SET visits = 40 WHERE id = %s", (blog["id"],))
    _sql(
        db,
        "INSERT INTO ref_link_clicks_daily (link_id, day, clicks) VALUES (%s, %s, 3), (%s, %s, 5), (%s, %s, 7)",
        (blog["id"], today, blog["id"], today - timedelta(days=6), blog["id"], today - timedelta(days=20)),
    )
    return {"blog": blog, "tg": tg, "a": a, "b": b, "f": f, "personal_id": pid, "now": now}


def _items(data):
    return {i["code"]: i for i in data["items"]}


def test_table_all_time(world, client):
    data = client.get("/api/admin/refs", headers=admin_headers()).json()
    assert data["period"]["preset"] == "all"
    assert data["personal_count"] == 1
    items = _items(data)
    assert set(items) == {"blog", "tg"}  # личные — только переключателем
    blog = items["blog"]
    assert blog["clicks"] == 40  # «всё время» — общий счётчик
    assert (blog["registrations"], blog["converted"]) == (3, 2)
    assert (blog["payers"], blog["payments"], blog["repeat_payments"]) == (2, 4, 2)
    assert blog["revenue_rub"] == 3800.0
    assert (blog["cascade"], blog["cascade_rub"]) == (1, 500.0)
    assert blog["spark"] == [5, 0, 0, 0, 0, 0, 3]
    assert items["tg"]["registrations"] == 0 and items["tg"]["spark"] == [0] * 7


def test_table_period_by_event_date(world, client):
    data = client.get("/api/admin/refs", headers=admin_headers(), params={"p": "7d"}).json()
    blog = _items(data)["blog"]
    assert data["period"]["preset"] == "7d"
    assert blog["clicks"] == 8  # сегодня 3 + 6 дней назад 5; 20 дней назад — мимо
    assert (blog["registrations"], blog["converted"]) == (1, 0)  # c ещё не платил
    # Оплаты за неделю: a (1 день назад, повторная), b (5 дней назад, повторная).
    assert (blog["payers"], blog["payments"], blog["repeat_payments"]) == (2, 2, 2)
    assert blog["revenue_rub"] == 1800.0
    assert (blog["cascade"], blog["cascade_rub"]) == (1, 500.0)

    month = _items(client.get("/api/admin/refs", headers=admin_headers(), params={"p": "30d"}).json())["blog"]
    assert month["registrations"] == 2 and month["converted"] == 1 and month["payments"] == 3 and month["repeat_payments"] == 2

    # Свой диапазон 70–40 дней назад: только b и его первая оплата.
    today = world["now"].astimezone(MSK).date()
    params = {"from": (today - timedelta(days=70)).isoformat(), "to": (today - timedelta(days=40)).isoformat()}
    old = _items(client.get("/api/admin/refs", headers=admin_headers(), params=params).json())["blog"]
    assert (old["registrations"], old["payments"], old["repeat_payments"], old["cascade"]) == (1, 1, 0, 0)


def test_personal_links_behind_toggle(world, client):
    data = client.get("/api/admin/refs", headers=admin_headers(), params={"personal": "1"}).json()
    personal = [i for i in data["items"] if i["personal"]]
    assert len(personal) == 1
    row = personal[0]
    assert row["owner_email"] == "a@example.com" and row["owner_id"] == world["a"]
    assert (row["registrations"], row["payers"], row["revenue_rub"]) == (1, 1, 500.0)


def test_groups_crud_order_and_colour(world, client):
    h = admin_headers()
    assert client.post("/api/admin/ref-groups", headers=h, json={"name": " "}).status_code == 400
    assert client.post("/api/admin/ref-groups", headers=h, json={"name": "Блогеры", "color": "red"}).status_code == 400
    g1 = client.post("/api/admin/ref-groups", headers=h, json={"name": "Блогеры", "color": "#AA3355"}).json()
    g2 = client.post("/api/admin/ref-groups", headers=h, json={"name": "Реклама"}).json()
    assert (g1["color"], g1["position"], g2["position"]) == ("#aa3355", 1, 2)

    res = client.put(f"/api/admin/ref-groups/{g2['id']}", headers=h, json={"name": "Реклама VK", "color": "#112233"})
    assert res.json()["name"] == "Реклама VK"
    assert client.put("/api/admin/ref-groups/999999", headers=h, json={"name": "x"}).status_code == 404

    groups = client.put("/api/admin/ref-groups/order", headers=h, json={"ids": [g2["id"]]}).json()["groups"]
    assert [g["id"] for g in groups] == [g2["id"], g1["id"]]

    # Ссылка в группе: при создании и правке; без group_id в запросе группа не трогается.
    link = client.post("/api/admin/refs", headers=h, json={"code": "anna", "group_id": g1["id"]}).json()
    assert link["group_id"] == g1["id"]
    link = client.put(f"/api/admin/refs/{link['id']}", headers=h, json={"code": "anna", "name": "Анна"}).json()
    assert link["group_id"] == g1["id"] and link["name"] == "Анна"
    assert client.put(f"/api/admin/refs/{link['id']}", headers=h, json={"code": "anna", "group_id": 999999}).status_code == 404
    link = client.put(f"/api/admin/refs/{link['id']}", headers=h, json={"code": "anna", "group_id": None}).json()
    assert link["group_id"] is None

    # Источник пользователя в списке пользователей несёт цвет группы.
    client.put(f"/api/admin/refs/{world['blog']['id']}", headers=h, json={"code": "blog", "name": "Блог", "group_id": g1["id"]})
    row = next(r for r in admin_users.list_page()["items"] if r["email"] == "b@example.com")
    assert row["source"]["group_color"] == "#aa3355"

    # Группа удалена — ссылки остаются без группы.
    assert client.delete(f"/api/admin/ref-groups/{g1['id']}", headers=h).status_code == 200
    assert client.delete(f"/api/admin/ref-groups/{g1['id']}", headers=h).status_code == 404
    items = _items(client.get("/api/admin/refs", headers=h).json())
    assert items["blog"]["group_id"] is None


def test_bulk_actions(world, client, db):
    h = admin_headers()
    g = admin_refs.save_group(None, {"name": "Все"})
    blog, tg, pid = world["blog"]["id"], world["tg"]["id"], world["personal_id"]

    assert client.post("/api/admin/refs/bulk", headers=h, json={"ids": [blog], "action": "zap"}).status_code == 400
    assert client.post("/api/admin/refs/bulk", headers=h, json={"ids": [], "action": "move"}).status_code == 400
    # Личную ссылку в группу не переносим и не удаляем — только выключаем.
    res = client.post("/api/admin/refs/bulk", headers=h, json={"ids": [blog, tg, pid], "action": "move", "group_id": g["id"]})
    assert res.json() == {"done": 2}
    res = client.post("/api/admin/refs/bulk", headers=h, json={"ids": [blog, pid], "action": "deactivate"})
    assert res.json() == {"done": 2}
    rows = {r["id"]: r for r in _sql(db, "SELECT id, active, group_id FROM ref_links").fetchall()}
    assert rows[blog]["group_id"] == g["id"] and rows[pid]["group_id"] is None
    assert not rows[blog]["active"] and not rows[pid]["active"] and rows[tg]["active"]
    assert client.post("/api/admin/refs/bulk", headers=h, json={"ids": [pid], "action": "activate"}).json() == {"done": 1}

    res = client.post("/api/admin/refs/bulk", headers=h, json={"ids": [tg, blog], "action": "move", "group_id": None})
    assert res.json() == {"done": 2}
    res = client.post("/api/admin/refs/bulk", headers=h, json={"ids": [tg, pid], "action": "delete"})
    assert res.json() == {"done": 1}
    left = {r["id"] for r in _sql(db, "SELECT id FROM ref_links").fetchall()}
    assert left == {blog, pid}

