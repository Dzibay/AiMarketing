from app import direct_api, yandex
from app.yandex import YandexError, direct_api_error, direct_sync_error


def test_direct_api_error_with_detail():
    exc = direct_api_error(
        {"error": {"error_code": 52, "error_string": "Invalid", "error_detail": "Нет прав"}},
        403,
    )
    assert isinstance(exc, YandexError)
    assert exc.code == "direct-denied"
    assert exc.message == "Нет прав"


def test_direct_api_error_add_results():
    exc = direct_api_error(
        {
            "AddResults": [
                {"Errors": [{"Message": "Фраза уже есть", "Code": 5000}]},
            ]
        },
        200,
    )
    assert exc.code == "direct"
    assert "Фраза" in (exc.message or "")


def test_direct_api_error_fallback():
    exc = direct_api_error({}, 500)
    assert exc.code == "direct"
    assert exc.message is None


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = b"x"
        self.text = str(payload)

    def json(self):
        return self._payload


def _fake_httpx(monkeypatch, responses):
    calls = []

    class Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            service = url.rsplit("/", 1)[-1]
            calls.append((service, json["params"]))
            return responses[service]

    monkeypatch.setattr(direct_api.httpx, "Client", Client)
    return calls


def test_direct_sync_error_keeps_direct_answer():
    err = {
        "error_code": 58,
        "error_string": "Незавершенная регистрация",
        "error_detail": "Необходимо создать заявку на доступ к API; дождаться подтверждения",
    }
    assert direct_sync_error(err, 200) == (
        "direct-denied|Незавершенная регистрация (ошибка 58). "
        "Необходимо создать заявку на доступ к API, дождаться подтверждения"
    )
    assert direct_sync_error({"error_code": 53, "error_string": "Ошибка авторизации"}, 200) == (
        "direct|Ошибка авторизации (ошибка 53)"
    )
    assert direct_sync_error(None, 502) == "direct|сервис не ответил (HTTP 502)"
    assert direct_sync_error(None, 400) == "direct|HTTP 400"
    assert direct_sync_error(None, 200) == "direct"


def test_fetch_direct_clients_stops_on_clients_error(monkeypatch):
    calls = _fake_httpx(
        monkeypatch,
        {
            "clients": _Resp(200, {"error": {"error_code": 58, "error_string": "Незавершенная регистрация"}}),
            "agencyclients": _Resp(200, {"error": {"error_code": 3001, "error_string": "Нет доступа к методу"}}),
        },
    )
    clients, error = yandex.fetch_direct_clients("token")
    assert clients == []
    assert error == "direct-denied|Незавершенная регистрация (ошибка 58)"
    assert [c[0] for c in calls] == ["clients"]


def test_fetch_direct_clients_reads_error_from_http_4xx(monkeypatch):
    _fake_httpx(
        monkeypatch,
        {
            "clients": _Resp(400, {"error": {"error_code": 513, "error_detail": "Логин не подключен к Директу"}}),
            "agencyclients": _Resp(400, {}),
        },
    )
    _, error = yandex.fetch_direct_clients("token")
    assert error == "direct-denied|Ошибка 513. Логин не подключен к Директу"


def test_fetch_direct_clients_ok(monkeypatch):
    calls = _fake_httpx(
        monkeypatch,
        {"clients": _Resp(200, {"result": {"Clients": [{"Login": "shop", "ClientId": 7, "ClientInfo": "Магазин"}]}})},
    )
    clients, error = yandex.fetch_direct_clients("token")
    assert error == ""
    assert clients == [{"client_login": "shop", "client_id": 7, "name": "Магазин", "currency": ""}]
    # clients.get не принимает SelectionCriteria: живой Директ отвечает на него ошибкой 8000.
    assert calls == [("clients", {"FieldNames": yandex.CLIENT_FIELDS})]


def test_fetch_direct_clients_agency_lists_its_clients(monkeypatch):
    calls = _fake_httpx(
        monkeypatch,
        {
            "clients": _Resp(200, {"result": {"Clients": [{"Login": "agency", "ClientId": 1, "Type": "AGENCY"}]}}),
            "agencyclients": _Resp(
                200,
                {
                    "result": {
                        "Clients": [
                            {"Login": "shop-a", "ClientId": 11, "ClientInfo": "Магазин А", "Currency": "RUB"},
                            {"Login": "shop-b", "ClientId": 12, "ClientInfo": "Магазин Б", "Currency": "RUB"},
                        ]
                    }
                },
            ),
        },
    )
    clients, error = yandex.fetch_direct_clients("token")
    assert error == ""
    assert [c["client_login"] for c in clients] == ["shop-a", "shop-b"]
    assert calls[1] == ("agencyclients", {"SelectionCriteria": {}, "FieldNames": yandex.CLIENT_FIELDS})


def test_fetch_direct_clients_agency_error(monkeypatch):
    _fake_httpx(
        monkeypatch,
        {
            "clients": _Resp(200, {"result": {"Clients": [{"Login": "agency", "Type": "AGENCY"}]}}),
            "agencyclients": _Resp(200, {"error": {"error_code": 54, "error_string": "Нет прав"}}),
        },
    )
    assert yandex.fetch_direct_clients("token") == ([], "direct-denied|Нет прав (ошибка 54)")


def _sequence_client(monkeypatch, responses):
    """Каждый post забирает следующий ответ из списка."""
    pending = list(responses)
    calls = []

    class Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            calls.append(url.rsplit("/", 1)[-1])
            return pending.pop(0)

    monkeypatch.setattr(direct_api.httpx, "Client", Client)
    monkeypatch.setattr(direct_api.time, "sleep", lambda _seconds: None)
    return calls


def test_fetch_direct_clients_retries_gateway_504(monkeypatch):
    calls = _sequence_client(
        monkeypatch,
        [
            _Resp(504, {}),
            _Resp(504, {}),
            _Resp(200, {"result": {"Clients": [{"Login": "shop", "ClientId": 7, "ClientInfo": "Магазин"}]}}),
        ],
    )
    clients, error = yandex.fetch_direct_clients("token")
    assert error == ""
    assert clients[0]["client_login"] == "shop"
    assert calls == ["clients", "clients", "clients"]


def test_fetch_direct_clients_reports_504_after_retries(monkeypatch):
    calls = _sequence_client(monkeypatch, [_Resp(504, {}), _Resp(504, {}), _Resp(504, {})])
    clients, error = yandex.fetch_direct_clients("token")
    assert clients == []
    assert error == "direct|сервис не ответил (HTTP 504)"
    assert len(calls) == 3


def test_call_retries_get_but_not_add(monkeypatch):
    calls = _sequence_client(
        monkeypatch,
        [
            _Resp(504, {}),
            _Resp(200, {"result": {"Campaigns": []}}),
        ],
    )
    result = direct_api.call("token", None, "campaigns", "get", {"FieldNames": ["Id"]})
    assert result == {"Campaigns": []}
    assert calls == ["campaigns", "campaigns"]

    one = _sequence_client(monkeypatch, [_Resp(504, {})])
    try:
        direct_api.call("token", None, "campaigns", "add", {"Campaigns": []})
    except YandexError as exc:
        assert exc.code == "direct"
    else:
        raise AssertionError("add при HTTP 504 должен упасть")
    assert one == ["campaigns"]
