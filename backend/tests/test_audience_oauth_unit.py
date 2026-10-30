"""Второе OAuth-приложение (Яндекс Аудитории): какие ключи идут в Яндекс и что без него — без БД и сети."""

from urllib.parse import parse_qs, urlparse

import pytest

from app import yandex
from app.config import settings
from app.yandex import YandexError


@pytest.fixture
def main_app(monkeypatch):
    monkeypatch.setattr(settings, "token_encrypt_secret", "test-secret")
    monkeypatch.setattr(settings, "yandex_client_id", "main-id")
    monkeypatch.setattr(settings, "yandex_client_secret", "main-secret")
    monkeypatch.setattr(settings, "yandex_audience_client_id", "")
    monkeypatch.setattr(settings, "yandex_audience_client_secret", "")
    monkeypatch.setattr(yandex, "public_base_url", lambda: "https://ex.test")


@pytest.fixture
def both_apps(main_app, monkeypatch):
    monkeypatch.setattr(settings, "yandex_audience_client_id", "aud-id")
    monkeypatch.setattr(settings, "yandex_audience_client_secret", "aud-secret")


@pytest.fixture
def token_posts(monkeypatch):
    """Запросы к oauth.yandex.ru/token: data каждого запроса."""
    posts = []

    class Response:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, data):
            posts.append(data)
            return Response()

    monkeypatch.setattr(yandex.httpx, "Client", Client)
    return posts


def _query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_without_audience_app_main_flow_unchanged(main_app, token_posts):
    assert yandex.yandex_configured() and not yandex.audience_configured()
    main = _query(yandex.build_authorize_url("s1"))
    assert (main["client_id"], main["scope"]) == ("main-id", settings.yandex_oauth_scopes)
    assert "audience" not in main["scope"] and "login_hint" not in main
    yandex.exchange_code("c")
    yandex.refresh_access_token("r")
    assert [(p["client_id"], p["client_secret"]) for p in token_posts] == [("main-id", "main-secret")] * 2
    # Аудитории без приложения — понятный код, в Яндекс ничего не уходит.
    for call in (
        lambda: yandex.build_authorize_url("s2", audience=True),
        lambda: yandex.exchange_code("c", audience=True),
        lambda: yandex.refresh_access_token("r", audience=True),
        lambda: yandex.start_oauth(purpose="audience", user_id=1, connection_id=2),
    ):
        with pytest.raises(YandexError) as err:
            call()
        assert err.value.code == "audience-config"
    assert len(token_posts) == 2


def test_audience_app_keys_and_scopes(both_apps, token_posts):
    assert yandex.audience_configured()
    url = yandex.build_authorize_url("s1", audience=True, login_hint="acc-a")
    query = _query(url)
    assert query == {
        "response_type": "code",
        "client_id": "aud-id",
        "redirect_uri": "https://ex.test/api/web/yandex/callback",
        "scope": "audience:read audience:write",
        "state": "s1",
        "force_confirm": "yes",
        "login_hint": "acc-a",
    }
    yandex.exchange_code("c", audience=True)
    yandex.refresh_access_token("r", audience=True)
    yandex.exchange_code("c")
    assert [(p["client_id"], p["client_secret"]) for p in token_posts] == [
        ("aud-id", "aud-secret"),
        ("aud-id", "aud-secret"),
        ("main-id", "main-secret"),
    ]
    # Основной вход по-прежнему без прав Аудиторий: у приложения входа нет места на четвёртую группу.
    assert "audience" not in _query(yandex.build_authorize_url("s2"))["scope"]


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"purpose": "other"}, "purpose"),
        ({"purpose": "audience", "user_id": 1, "connection_id": 2, "mode": "direct"}, "mode"),
        ({"purpose": "audience", "connection_id": 2}, "unauthorized"),
        ({"purpose": "audience", "user_id": 1}, "not-found"),
    ],
)
def test_audience_state_checks(kwargs, code):
    with pytest.raises(YandexError) as err:
        yandex.create_oauth_state(**kwargs)
    assert err.value.code == code
