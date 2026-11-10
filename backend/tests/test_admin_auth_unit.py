"""Админка: отдельный вход по паролю, токен не совпадает с сессией кабинета, перебор ограничен."""

import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.main import app
from app.web_auth import make_session


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "correct horse")
    monkeypatch.setattr(settings, "admin_token_secret", "unit-admin-secret")
    monkeypatch.setattr(settings, "web_session_secret", "unit-web-secret")
    auth.reset_failures()
    yield
    auth.reset_failures()


def test_token_roundtrip_and_tampering(monkeypatch):
    token = auth.make_token()
    assert auth.verify_token(token)
    expires, _, sig = token.partition(".")
    assert not auth.verify_token(f"{int(expires) + 1}.{sig}")
    assert not auth.verify_token(f"{expires}.{'0' * len(sig)}")
    assert not auth.verify_token("9" * 5000 + ".abc")
    assert not auth.verify_token("")
    # Сессия пользователя кабинета — не токен админки.
    assert not auth.verify_token(make_session(1))
    monkeypatch.setattr(time, "time", lambda: int(expires) + 1)
    assert not auth.verify_token(token)


def test_login_checks_password_and_limits_guessing():
    with pytest.raises(auth.LoginError) as exc:
        auth.login("wrong", "1.1.1.1")
    assert exc.value.code == "unauthorized"
    assert auth.verify_token(auth.login("correct horse", "1.1.1.1"))

    for _ in range(auth.MAX_FAILURES):
        with pytest.raises(auth.LoginError):
            auth.login("guess", "2.2.2.2")
    # Даже верный пароль с этого адреса не принимается до конца окна; с другого — принимается.
    with pytest.raises(auth.LoginError) as exc:
        auth.login("correct horse", "2.2.2.2")
    assert exc.value.code == "too-many-attempts"
    assert auth.login("correct horse", "3.3.3.3")


def test_login_closed_without_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "")
    with pytest.raises(auth.LoginError) as exc:
        auth.login("", "1.1.1.1")
    assert exc.value.code == "unauthorized"


def test_global_cap_stops_spread_guessing():
    for i in range(auth.FAILURES_TOTAL):
        with pytest.raises(auth.LoginError):
            auth.login("guess", f"10.0.0.{i}")
    with pytest.raises(auth.LoginError) as exc:
        auth.login("correct horse", "10.0.1.1")
    assert exc.value.code == "too-many-attempts"


def test_api_login_and_guard():
    client = TestClient(app)
    assert client.get("/api/admin/me").status_code == 401
    assert client.get("/api/admin/me", headers={"Authorization": f"Bearer {make_session(1)}"}).status_code == 401

    bad = client.post("/api/admin/login", json={"password": "nope"}, headers={"X-Forwarded-For": "5.5.5.5, 172.18.0.2"})
    assert bad.status_code == 401 and bad.json()["detail"] == "unauthorized"
    ok = client.post("/api/admin/login", json={"password": "correct horse"})
    assert ok.status_code == 200 and auth.verify_token(ok.json()["token"])

    for _ in range(auth.MAX_FAILURES):
        client.post("/api/admin/login", json={"password": "nope"}, headers={"X-Forwarded-For": "6.6.6.6"})
    blocked = client.post("/api/admin/login", json={"password": "correct horse"}, headers={"X-Forwarded-For": "6.6.6.6"})
    assert blocked.status_code == 429 and blocked.json()["detail"] == "too-many-attempts"
