"""Вордстат через Search API Yandex Cloud без сети: запрос, разбор ответа, ошибки и инструмент ИИ."""

from types import SimpleNamespace

import pytest

from app import ai, ai_agent, billing, wordstat
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
    """pool без БД: строка service_wordstat (id = 1) и журнал execute."""

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
    return fake


def test_credentials_come_from_service_row(db):
    db.row = {"api_key_enc": "enc:SERVICE", "folder_id": "b1g"}
    assert wordstat.credentials() == ("SERVICE", "b1g")
    assert db.executed[0][0] == "SELECT * FROM service_wordstat WHERE id = 1"


@pytest.mark.parametrize("row", [None, {"api_key_enc": "", "folder_id": "b1g"}, {"api_key_enc": "enc:K", "folder_id": ""}])
def test_credentials_without_key_or_folder(db, row):
    db.row = row
    with pytest.raises(YandexError) as err:
        wordstat.credentials()
    assert err.value.code == "wordstat-not-configured"
    assert "администратор" in err.value.message
    assert wordstat.is_configured() is False


def test_public_settings_hide_key(db, monkeypatch):
    monkeypatch.setattr(ai, "decrypt_token", lambda cipher: "AQVN-secret-1234")
    assert wordstat.get_settings()["configured"] is False
    db.row = {
        "api_key_enc": "enc:x",
        "folder_id": "b1g",
        "checked_at": None,
        "check_error": "",
        "units_per_request": 2,
        "cost_per_request": 0.02,
    }
    out = wordstat.get_settings()
    assert (out["configured"], out["has_key"], out["key_hint"]) == (True, True, "…1234")
    assert (out["units_per_request"], out["cost_per_request"]) == (2.0, 0.02)
    assert "AQVN-secret-1234" not in str(out)


def test_save_encrypts_new_key_and_resets_check(db):
    db.row = {"api_key_enc": "enc:old", "folder_id": "b1g", "units_per_request": 2, "cost_per_request": None}
    wordstat.save_settings(folder_id=" b1g ", api_key=" NEW ")
    sql, params = db.executed[1]
    assert sql.startswith("UPDATE service_wordstat SET") and sql.endswith("WHERE id = 1")
    assert params == ("b1g", "enc:NEW", 2.0, None, True, True)


def test_save_keeps_key_when_not_sent(db):
    db.row = {"api_key_enc": "enc:old", "folder_id": "b1g", "units_per_request": None, "cost_per_request": None}
    wordstat.save_settings(folder_id="b1g", api_key=None)
    assert db.executed[1][1] == ("b1g", "enc:old", None, None, False, False)


def test_save_prices_without_touching_key(db):
    db.row = {"api_key_enc": "enc:old", "folder_id": "b1g", "units_per_request": 1, "cost_per_request": 0.02}
    wordstat.save_settings(folder_id="b1g", units_per_request="2,5", cost_per_request="")
    assert db.executed[1][1] == ("b1g", "enc:old", 2.5, None, False, False)

    with pytest.raises(YandexError) as err:
        wordstat.save_settings(folder_id="b1g", units_per_request="-1")
    assert err.value.code == "ai-price"


@pytest.mark.parametrize("folder, key", [("", "KEY"), ("b1g folder", "KEY"), ("x" * 51, None)])
def test_save_rejects_bad_folder(db, folder, key):
    with pytest.raises(YandexError) as err:
        wordstat.save_settings(folder_id=folder, api_key=key)
    assert err.value.code == "wordstat-folder"
    assert not any(sql.startswith("UPDATE") for sql, _ in db.executed)


def test_check_connection_makes_one_request_and_remembers_result(db, api):
    db.row = {"api_key_enc": "enc:SERVICE", "folder_id": "b1g"}
    api.responses["купить"] = FakeResponse(
        200, {"totalCount": "900", "results": [{"phrase": f"купить {i}", "count": "10"} for i in range(5)]}
    )
    out = wordstat.check_connection()
    assert out == {"ok": True, "phrase": "купить", "total": 900, "sample": [{"phrase": f"купить {i}", "count": 10} for i in range(3)]}
    assert api.calls[0]["headers"]["Authorization"] == "Api-Key SERVICE" and api.calls[0]["body"]["numPhrases"] == 3
    assert db.executed[-1] == ("UPDATE service_wordstat SET checked_at = NOW(), check_error = %s WHERE id = 1", ("",))


def test_check_connection_remembers_error(db, api):
    db.row = {"api_key_enc": "enc:SERVICE", "folder_id": "b1g"}
    api.responses["купить"] = FakeResponse(403, {"message": "Permission denied"})
    with pytest.raises(YandexError) as err:
        wordstat.check_connection()
    assert err.value.code == "wordstat-denied"
    assert "search-api.webSearch.user" in db.executed[-1][1][0]


@pytest.fixture
def billed(monkeypatch):
    """Учёт единиц без БД: проверки и списания — в журнал; exhausted=True — лимит исчерпан."""
    state = SimpleNamespace(checks=[], records=[], exhausted=False)

    def check(user_id, need=0.0):
        state.checks.append((user_id, need))
        if state.exhausted:
            raise YandexError("units-exhausted", "Лимит ИИ на этот период исчерпан")

    monkeypatch.setattr(billing, "check", check)
    monkeypatch.setattr(billing, "record", lambda user_id, **kw: state.records.append({"user_id": user_id, **kw}))
    return state


@pytest.fixture
def tool(api, billed, monkeypatch):
    monkeypatch.setattr(wordstat, "credentials", lambda: ("KEY", "f"))
    monkeypatch.setattr(wordstat, "_row", lambda: {"units_per_request": 2, "cost_per_request": 0.02})
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


def test_tool_charges_units_for_each_request(api, billed, tool):
    tool({"phrases": ["диван", "кресло"]})
    assert billed.checks == [(1, 2.0), (1, 2.0)]
    assert [(r["user_id"], r["kind"], r["workspace_id"], r["units"], r["charged"], r["cost_rub"]) for r in billed.records] == [
        (1, "wordstat", 2, 1, 2.0, 0.02),
        (1, "wordstat", 2, 1, 2.0, 0.02),
    ]


def test_tool_stops_before_request_when_limit_is_exhausted(api, billed, tool):
    billed.exhausted = True
    with pytest.raises(YandexError) as err:
        tool({"phrases": ["диван"]})
    assert err.value.code == "units-exhausted"
    assert api.calls == [] and billed.records == []


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
    monkeypatch.setattr(ai_agent, "_image_gen_ready", lambda ctx: False)
    monkeypatch.setattr(ai_agent, "_running_experiments", lambda ctx: [])
    ctx = SimpleNamespace(user_id=1, ws_id=2, ws={"name": "Окна", "counter_id": 555, "accounts": []}, access=ai_agent.normalize_ai_access(None))
    prompt = ai_agent._system_prompt(ctx)
    assert "get_wordstat" in prompt
    assert "недоступен" not in prompt
