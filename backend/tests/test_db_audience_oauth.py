"""Яндекс Аудитории вторым OAuth-приложением на настоящем Postgres: вход у логина, токен, обновление, выключенное."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import audiences, yandex
from app.config import settings
from app.main import app
from app.token_crypto import decrypt_token
from app.web_auth import make_session
from app.yandex import YandexError
from tests.dbutil import make_account, make_connection, make_user


@pytest.fixture
def oauth(monkeypatch):
    """Оба приложения заданы; обмен кода и профиль — без сети (exchanges — флаг audience каждого обмена)."""
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-audience-secret")
    monkeypatch.setattr(settings, "yandex_client_id", "main-id")
    monkeypatch.setattr(settings, "yandex_client_secret", "main-secret")
    monkeypatch.setattr(settings, "yandex_audience_client_id", "aud-id")
    monkeypatch.setattr(settings, "yandex_audience_client_secret", "aud-secret")
    monkeypatch.setattr(yandex, "public_base_url", lambda: "https://ex.test")
    state = {"uid": "uid-login", "exchanges": [], "refreshes": [], "refresh_error": None}

    def exchange(code, *, audience=False):
        state["exchanges"].append(audience)
        return {"access_token": "aud-access", "refresh_token": "aud-refresh", "expires_in": 3600}

    def refresh(token, *, audience=False):
        state["refreshes"].append((token, audience))
        if state["refresh_error"]:
            raise state["refresh_error"]
        return {"access_token": "aud-fresh", "expires_in": 3600}

    monkeypatch.setattr(yandex, "exchange_code", exchange)
    monkeypatch.setattr(yandex, "refresh_access_token", refresh)
    monkeypatch.setattr(
        yandex, "fetch_yandex_profile", lambda access: {"uid": state["uid"], "email": "", "name": "", "login": "login"}
    )
    return state


def _seed(db) -> dict:
    user = make_user(db, yandex_uid="uid-login")
    conn = make_connection(db, user)
    return {"user": user, "conn": conn, "account": make_account(db, user, conn)}


def _start(user: int, conn: int) -> str:
    url = yandex.start_oauth(purpose="audience", user_id=user, connection_id=conn)
    return parse_qs(urlparse(url).query)["state"][0]


def _row(db, conn: int) -> dict:
    with db.connection() as c:
        return c.execute("SELECT * FROM yandex_connections WHERE id = %s", (conn,)).fetchone()


def test_audience_connect_and_token(db, oauth):
    s = _seed(db)
    assert audiences.account_rights(s["user"], s["account"]) == {
        "login": "login", "connection_id": s["conn"], "enabled": True, "rights": False,
    }
    url = yandex.start_oauth(purpose="audience", user_id=s["user"], connection_id=s["conn"])
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert (query["client_id"], query["scope"], query["login_hint"]) == ("aud-id", "audience:read audience:write", "login")
    with db.connection() as c:
        saved = c.execute("SELECT purpose, connection_id FROM oauth_states WHERE state = %s", (query["state"],)).fetchone()
    assert saved == {"purpose": "audience", "connection_id": s["conn"]}

    # Вошли другим логином Яндекса — Аудитории не привязываются.
    oauth["uid"] = "uid-other"
    with pytest.raises(YandexError) as err:
        yandex.complete_oauth(code="c", state=query["state"])
    assert err.value.code == "audience-login" and _row(db, s["conn"])["audience_token_enc"] == ""

    oauth["uid"] = "uid-login"
    user, job = yandex.complete_oauth(code="c", state=_start(s["user"], s["conn"]))
    assert (user["id"], job) == (s["user"], None) and oauth["exchanges"] == [True, True]
    row = _row(db, s["conn"])
    assert decrypt_token(row["audience_token_enc"]) == "aud-access" and decrypt_token(row["audience_refresh_enc"]) == "aud-refresh"
    assert row["audience_scopes"] == "audience:read audience:write"
    # Основное подключение не тронуто: ни нового логина, ни синка.
    with db.connection() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM yandex_connections").fetchone()["n"] == 1
        assert c.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0
    assert [c["audience"] for c in yandex.list_connections(s["user"])] == [True]
    rights = audiences.account_rights(s["user"], s["account"])
    assert rights["rights"] and audiences.audience_token(rights) == "aud-access"

    # Токен истекает — обновление ключами второго приложения, refresh-токен остаётся прежним.
    with db.connection() as c:
        c.execute("UPDATE yandex_connections SET audience_expires_at = NOW() - INTERVAL '1 hour' WHERE id = %s", (s["conn"],))
    assert yandex.get_audience_access_token(s["conn"]) == "aud-fresh"
    assert oauth["refreshes"] == [("aud-refresh", True)]
    row = _row(db, s["conn"])
    assert decrypt_token(row["audience_token_enc"]) == "aud-fresh" and decrypt_token(row["audience_refresh_enc"]) == "aud-refresh"
    assert row["audience_expires_at"] > datetime.now(timezone.utc) + timedelta(minutes=30)

    # Доступ отозван — понятный отказ для ИИ.
    with db.connection() as c:
        c.execute("UPDATE yandex_connections SET audience_expires_at = NOW() WHERE id = %s", (s["conn"],))
    oauth["refresh_error"] = YandexError("token-expired")
    with pytest.raises(YandexError) as err:
        audiences.audience_token(rights)
    assert err.value.code == "audience-scope" and "@login" in err.value.message


def test_audience_connect_checks(db, oauth):
    s = _seed(db)
    other = make_user(db, "x@example.com")
    sandbox = make_connection(db, s["user"], "sandbox-login")
    with db.connection() as c:
        c.execute("UPDATE yandex_connections SET sandbox = TRUE WHERE id = %s", (sandbox,))
    for user, conn, code in ((other, s["conn"], "not-found"), (s["user"], sandbox, "sandbox"), (s["user"], None, "not-found")):
        with pytest.raises(YandexError) as err:
            yandex.start_oauth(purpose="audience", user_id=user, connection_id=conn)
        assert err.value.code == code
    with pytest.raises(YandexError) as err:
        yandex.get_audience_access_token(s["conn"])
    assert err.value.code == "audience-scope"


def test_audience_api(db, oauth):
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    me = client.get("/api/web/me", headers=auth).json()
    assert me["audience_oauth"] is True and me["connections"][0]["audience"] is False
    assert client.get("/api/web/config").json()["audience_oauth"] is True
    path = f"/api/web/yandex/start?purpose=audience&connection_id={s['conn']}"
    assert client.get(path).status_code == 401
    url = client.get(path, headers=auth).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    done = client.get(f"/api/web/yandex/callback?code=c&state={state}", follow_redirects=False)
    assert done.status_code == 302 and "/oauth/done?code=" in done.headers["location"]
    assert client.get("/api/web/me", headers=auth).json()["connections"][0]["audience"] is True
    # Чужой логин — ошибка на странице возврата.
    oauth["uid"] = "uid-other"
    state = parse_qs(urlparse(client.get(path, headers=auth).json()["url"]).query)["state"][0]
    failed = client.get(f"/api/web/yandex/callback?code=c&state={state}", follow_redirects=False)
    assert failed.headers["location"].endswith("/oauth/done?error=audience-login")


def test_without_audience_app_nothing_changes(db, oauth, monkeypatch):
    monkeypatch.setattr(settings, "yandex_audience_client_id", "")
    monkeypatch.setattr(settings, "yandex_audience_client_secret", "")
    s = _seed(db)
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {make_session(s['user'])}"}
    me = client.get("/api/web/me", headers=auth).json()
    assert me["audience_oauth"] is False and me["connections"][0]["audience"] is False
    started = client.get(f"/api/web/yandex/start?purpose=audience&connection_id={s['conn']}", headers=auth)
    assert started.status_code == 400 and started.json()["detail"] == "audience-config"
    # Основное подключение работает как раньше.
    url = client.get("/api/web/yandex/start?purpose=connect", headers=auth).json()["url"]
    assert "client_id=main-id" in url and "audience" not in url
    assert audiences.account_rights(s["user"], s["account"]) == {
        "login": "login", "connection_id": s["conn"], "enabled": False, "rights": False,
    }
    with db.connection() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM oauth_states WHERE purpose = 'audience'").fetchone()["n"] == 0
