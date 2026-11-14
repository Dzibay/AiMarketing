"""Настройки интерфейса в аккаунте (users.ui, PATCH /me/ui) на настоящем Postgres."""

import pytest
from fastapi.testclient import TestClient

from app import users
from app.main import app
from app.users import UsersError
from app.web_auth import make_session
from tests.dbutil import make_user


def _headers(user_id: int) -> dict:
    return {"Authorization": f"Bearer {make_session(user_id)}"}


def _stored(pool, user_id: int) -> dict:
    with pool.connection() as conn:
        return conn.execute("SELECT ui FROM users WHERE id = %s", (user_id,)).fetchone()["ui"]


def test_new_account_has_empty_ui(db):
    user = make_user(db)
    body = TestClient(app).get("/api/web/me", headers=_headers(user)).json()
    assert body["user"]["ui"] == {}


def test_patch_merges_keys_and_me_returns_them(db):
    user = make_user(db)
    client = TestClient(app)
    headers = _headers(user)

    res = client.patch("/api/web/me/ui", json={"expert": True}, headers=headers)
    assert res.status_code == 200
    assert res.json()["user"]["ui"] == {"expert": True}

    # Второе устройство шлёт только свой ключ — режим, выбранный на первом, остаётся.
    res = client.patch("/api/web/me/ui", json={"role": "pro"}, headers=headers)
    assert res.json()["user"]["ui"] == {"expert": True, "role": "pro"}

    res = client.patch("/api/web/me/ui", json={"expert": False, "whats_new_seen": "2026-11-14"}, headers=headers)
    assert res.json()["user"]["ui"] == {"expert": False, "role": "pro", "whats_new_seen": "2026-11-14"}

    assert client.get("/api/web/me", headers=headers).json()["user"]["ui"] == res.json()["user"]["ui"]
    assert _stored(db, user) == {"expert": False, "role": "pro", "whats_new_seen": "2026-11-14"}


def test_unknown_key_is_rejected_and_nothing_is_saved(db):
    user = make_user(db)
    client = TestClient(app)
    headers = _headers(user)

    res = client.patch("/api/web/me/ui", json={"theme": "dark"}, headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"] == "ui-unknown"

    # Чужой ключ рядом с правильным — не сохраняем ничего, а не «что получилось».
    res = client.patch("/api/web/me/ui", json={"expert": True, "anything": 1}, headers=headers)
    assert res.status_code == 400
    assert _stored(db, user) == {}


@pytest.mark.parametrize(
    "patch",
    [
        {"expert": "yes"},
        {"expert": 1},
        {"role": "admin"},
        {"role": ""},
        {"hints_seen": "ai-button"},
        {"hints_seen": ["Кнопка ИИ"]},
        {"hints_seen": ["ok", 5]},
        {"hints_seen": ["x" * 41]},
        {"hints_seen": [f"h{i}" for i in range(201)]},
        {"whats_new_seen": 20261114},
        {"whats_new_seen": ""},
    ],
)
def test_bad_value_is_rejected(db, patch):
    user = make_user(db)
    res = TestClient(app).patch("/api/web/me/ui", json=patch, headers=_headers(user))
    assert res.status_code == 400
    assert res.json()["detail"] == "ui-value"
    assert _stored(db, user) == {}


def test_hints_add_up_and_null_resets_a_key(db):
    user = make_user(db)
    client = TestClient(app)
    headers = _headers(user)

    client.patch("/api/web/me/ui", json={"hints_seen": ["ai-button", "pending"]}, headers=headers)
    # Телефон закрыл другую подсказку и про первые две не знает — они не теряются, повторы не копятся.
    res = client.patch("/api/web/me/ui", json={"hints_seen": ["mode-switch", "pending"]}, headers=headers)
    assert res.json()["user"]["ui"] == {"hints_seen": ["ai-button", "pending", "mode-switch"]}

    # «Показать подсказки снова»: null сбрасывает ключ, остальные не трогает.
    client.patch("/api/web/me/ui", json={"expert": True}, headers=headers)
    res = client.patch("/api/web/me/ui", json={"hints_seen": None}, headers=headers)
    assert res.json()["user"]["ui"] == {"expert": True}

    # Пустой запрос ничего не меняет.
    res = client.patch("/api/web/me/ui", json={}, headers=headers)
    assert res.status_code == 200
    assert res.json()["user"]["ui"] == {"expert": True}


def test_settings_belong_to_the_account(db):
    first = make_user(db, "a@example.com")
    second = make_user(db, "b@example.com")
    client = TestClient(app)

    client.patch("/api/web/me/ui", json={"expert": True, "role": "pro"}, headers=_headers(first))
    assert client.get("/api/web/me", headers=_headers(second)).json()["user"]["ui"] == {}
    assert _stored(db, first) == {"expert": True, "role": "pro"}

    assert client.patch("/api/web/me/ui", json={"expert": True}).status_code == 401
    assert client.patch("/api/web/me/ui", json=["expert"], headers=_headers(first)).status_code == 422


def test_login_and_register_return_ui(db):
    """Вход с телефона: режим, выбранный на компьютере, приходит уже в ответе на вход."""
    created = users.register_user(email="new@example.com", password="correct horse")
    assert users.public_user(created)["ui"] == {}

    users.set_ui(created["id"], {"expert": True})
    logged = users.login_user(email="new@example.com", password="correct horse")
    assert users.public_user(logged)["ui"] == {"expert": True}

    with pytest.raises(UsersError) as err:
        users.set_ui(created["id"] + 1000, {"expert": True})
    assert err.value.code == "not-found"
