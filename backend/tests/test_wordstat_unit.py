"""Вордстат через Search API Yandex Cloud без сети: запрос, разбор ответа, ошибки и инструмент ИИ."""

from types import SimpleNamespace

import pytest

from app import ai, ai_agent, wordstat
from app.config import settings
from app.yandex import YandexError


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None):
        self.status_code = status
        self._payload = payload
        self.content = b"x" if payload is not None else b""
        self.text = str(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def api(monkeypatch):
    """Подменяет HTTP: ответ по фразе из api.responses, запросы — в api.calls."""
    state = SimpleNamespace(calls=[], responses={})

    def post_json(url, *, headers, body, timeout, attempts):
        state.calls.append({"url": url, "headers": headers, "body": body, "attempts": attempts})
        return state.responses.get(body["phrase"]) or FakeResponse(200, {"totalCount": "0", "results": []})

    monkeypatch.setattr(wordstat, "post_json", post_json)
    return state


def test_top_requests_builds_cloud_request_and_parses_string_counts(api):
    api.responses["диван"] = FakeResponse(
        200,
        {
            "totalCount": "120500",
            "results": [{"phrase": "диван", "count": "120500"}, {"phrase": "купить диван", "count": "40100"}, {"phrase": ""}],
            "associations": [{"phrase": "кровать", "count": "9000"}],
        },
    )
    out = wordstat.top_requests("KEY", "b1gfolder", "  диван ", regions=[213, 2], device="phone", num_phrases=5000)

    call = api.calls[0]
    assert call["url"] == "https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests"
    assert call["headers"]["Authorization"] == "Api-Key KEY"
    assert call["body"] == {
        "phrase": "диван",
        "numPhrases": 2000,
        "folderId": "b1gfolder",
        "regions": ["213", "2"],
        "devices": ["DEVICE_PHONE"],
    }
    assert call["attempts"] == 3
    assert out == {
        "phrase": "диван",
        "total": 120500,
        "results": [{"phrase": "диван", "count": 120500}, {"phrase": "купить диван", "count": 40100}],
        "associations": [{"phrase": "кровать", "count": 9000}],
    }


def test_top_requests_all_devices_and_regions_are_not_sent(api):
    wordstat.top_requests("KEY", "f", "диван")
    assert set(api.calls[0]["body"]) == {"phrase", "numPhrases", "folderId"}


@pytest.mark.parametrize(
    "status, payload, code",
    [
        (403, {"code": 7, "message": "Permission denied"}, "wordstat-denied"),
        (401, None, "wordstat-denied"),
        (429, {"message": "quota"}, "wordstat-quota"),
        (400, {"code": 3, "message": "phrase is too long"}, "args"),
        (500, None, "wordstat"),
    ],
)
def test_top_requests_errors(api, status, payload, code):
    api.responses["диван"] = FakeResponse(status, payload)
    with pytest.raises(YandexError) as err:
        wordstat.top_requests("KEY", "f", "диван")
    assert err.value.code == code
    if status == 403:
        assert "search-api.webSearch.user" in err.value.message and "Permission denied" in err.value.message
    if status == 400:
        assert "phrase is too long" in err.value.message


@pytest.mark.parametrize("phrase, device", [("  ", "all"), ("д" * 401, "all"), ("диван", "tv")])
def test_top_requests_rejects_bad_args_without_request(api, phrase, device):
    with pytest.raises(YandexError) as err:
        wordstat.top_requests("KEY", "f", phrase, device=device)
    assert err.value.code == "args"
    assert api.calls == []


class FakeDb:
    """pool без БД: строка wordstat_settings по user_id и журнал execute."""

    def __init__(self, row: dict | None = None):
        self.row = row
        self.executed = []

    def connection(self):
        db = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                db.executed.append((" ".join(sql.split()), params))
                return SimpleNamespace(fetchone=lambda: db.row)

        return Conn()


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(wordstat, "pool", fake)
    monkeypatch.setattr(wordstat, "encrypt_token", lambda plain: f"enc:{plain}")
    monkeypatch.setattr(wordstat, "decrypt_token", lambda cipher: cipher.removeprefix("enc:"))
    monkeypatch.setattr(settings, "wordstat_api_key", "")
    monkeypatch.setattr(settings, "wordstat_folder_id", "")
    return fake


def test_credentials_prefer_own_key_over_service(db, monkeypatch):
    monkeypatch.setattr(settings, "wordstat_api_key", "SERVICE")
    monkeypatch.setattr(settings, "wordstat_folder_id", "svc-folder")
    db.row = {"api_key_enc": "enc:OWN", "folder_id": "b1g"}
    assert wordstat.credentials(1) == ("OWN", "b1g")

    db.row = {"api_key_enc": "enc:OWN", "folder_id": ""}
    assert wordstat.credentials(1) == ("SERVICE", "svc-folder")


def test_credentials_without_any_key(db):
    with pytest.raises(YandexError) as err:
        wordstat.credentials(1)
    assert err.value.code == "wordstat-not-configured"
    assert "Настройки" in err.value.message


def test_public_settings_hide_key_and_show_source(db, monkeypatch):
    monkeypatch.setattr(ai, "decrypt_token", lambda cipher: "AQVN-secret-1234")
    assert wordstat.get_settings(1)["configured"] is False
    db.row = {"api_key_enc": "enc:x", "folder_id": "b1g", "checked_at": None, "check_error": ""}
    out = wordstat.get_settings(1)
    assert (out["configured"], out["source"], out["key_hint"]) == (True, "own", "…1234")
    assert "AQVN-secret-1234" not in str(out)

    monkeypatch.setattr(settings, "wordstat_api_key", "SERVICE")
    monkeypatch.setattr(settings, "wordstat_folder_id", "svc")
    db.row = None
    assert wordstat.get_settings(1)["source"] == "service"


def test_save_encrypts_new_key_and_resets_check(db):
    db.row = {"api_key_enc": "enc:old", "folder_id": "b1g"}
    wordstat.save_settings(1, folder_id=" b1g ", api_key=" NEW ")
    sql, params = db.executed[1]
    assert sql.startswith("INSERT INTO wordstat_settings")
    assert params == (1, "b1g", "enc:NEW", True, True)


def test_save_keeps_key_when_not_sent(db):
    db.row = {"api_key_enc": "enc:old", "folder_id": "b1g"}
    wordstat.save_settings(1, folder_id="b1g", api_key=None)
    assert db.executed[1][1] == (1, "b1g", "enc:old", False, False)


@pytest.mark.parametrize("folder, key", [("", "KEY"), ("b1g folder", "KEY"), ("x" * 51, None)])
def test_save_rejects_bad_folder(db, folder, key):
    with pytest.raises(YandexError) as err:
        wordstat.save_settings(1, folder_id=folder, api_key=key)
    assert err.value.code == "wordstat-folder"
    assert not any(sql.startswith("INSERT") for sql, _ in db.executed)


def test_check_connection_makes_one_request_and_remembers_result(db, api):
    db.row = {"api_key_enc": "enc:OWN", "folder_id": "b1g"}
    api.responses["купить"] = FakeResponse(
        200, {"totalCount": "900", "results": [{"phrase": f"купить {i}", "count": "10"} for i in range(5)]}
    )
    out = wordstat.check_connection(1)
    assert out == {"ok": True, "phrase": "купить", "total": 900, "sample": [{"phrase": f"купить {i}", "count": 10} for i in range(3)]}
    assert api.calls[0]["headers"]["Authorization"] == "Api-Key OWN" and api.calls[0]["body"]["numPhrases"] == 3
    assert db.executed[-1] == ("UPDATE wordstat_settings SET checked_at = NOW(), check_error = %s WHERE user_id = %s", ("", 1))


def test_check_connection_remembers_error(db, api):
    db.row = {"api_key_enc": "enc:OWN", "folder_id": "b1g"}
    api.responses["купить"] = FakeResponse(403, {"message": "Permission denied"})
    with pytest.raises(YandexError) as err:
        wordstat.check_connection(1)
    assert err.value.code == "wordstat-denied"
    assert "search-api.webSearch.user" in db.executed[-1][1][0]


@pytest.fixture
def tool(api, monkeypatch):
    monkeypatch.setattr(wordstat, "credentials", lambda user_id: ("KEY", "f"))
    return lambda args: ai_agent._t_wordstat(SimpleNamespace(user_id=1, ws_id=2), args)


def test_tool_queries_each_phrase_once_and_trims(api, tool):
    api.responses["диван"] = FakeResponse(
        200,
        {
            "totalCount": "100",
            "results": [{"phrase": f"диван {i}", "count": str(100 - i)} for i in range(3)],
            "associations": [{"phrase": f"кровать {i}", "count": "5"} for i in range(5)],
        },
    )
    out = tool({"phrases": ["диван", " диван ", "кресло"], "region_ids": [213, -1, 213], "limit": 3})

    assert [c["body"]["phrase"] for c in api.calls] == ["диван", "кресло"]
    assert all(c["body"]["regions"] == ["213"] and c["body"]["numPhrases"] == 3 for c in api.calls)
    assert out["regions"] == [213]
    assert "исключения" in out["note"]
    assert len(out["items"][0]["associations"]) == 3
    assert out["items"][0]["total"] == 100
    assert "errors" not in out


def test_tool_keeps_going_after_rejected_phrase(api, tool):
    api.responses["плохая"] = FakeResponse(400, {"message": "bad phrase"})
    out = tool({"phrases": ["плохая", "диван"]})
    assert [i["phrase"] for i in out["items"]] == ["диван"]
    assert out["errors"] == [{"phrase": "плохая", "error": "Вордстат отклонил запрос: bad phrase"}]


def test_tool_stops_on_access_error(api, tool):
    api.responses["диван"] = FakeResponse(403, {"message": "denied"})
    with pytest.raises(YandexError) as err:
        tool({"phrases": ["диван", "кресло"]})
    assert err.value.code == "wordstat-denied"
    assert len(api.calls) == 1


@pytest.mark.parametrize(
    "args",
    [{"phrases": []}, {"phrases": ["  "]}, {"phrases": [f"фраза {i}" for i in range(6)]}, {"phrases": ["a"], "region_ids": ["x"]}],
)
def test_tool_rejects_bad_args_before_requests(api, tool, args):
    with pytest.raises(YandexError) as err:
        tool(args)
    assert err.value.code == "args"
    assert api.calls == []


def test_tool_is_registered_and_prompt_points_to_it(monkeypatch):
    assert ai_agent.READ_TOOLS["get_wordstat"] is ai_agent._t_wordstat
    assert ai_agent.TOOL_LABELS["get_wordstat"] == "Вордстат"
    assert "get_wordstat" in {t["function"]["name"] for t in ai_agent.TOOLS}

    monkeypatch.setattr(ai_agent, "memory_examples", lambda ws_id, limit: {})
    monkeypatch.setattr(ai_agent, "_running_experiments", lambda ctx: [])
    ctx = SimpleNamespace(user_id=1, ws_id=2, ws={"name": "Окна", "counter_id": 555, "accounts": []}, access=ai_agent.normalize_ai_access(None))
    prompt = ai_agent._system_prompt(ctx)
    assert "get_wordstat" in prompt
    assert "недоступен" not in prompt
