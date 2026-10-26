"""Разбор ответов Директа без сети: отчёт статистики и баланс Live 4 (httpx.MockTransport)."""

import json
from datetime import date

import httpx
import pytest

from app.direct_balance import fetch_balances
from app.stats import fetch_direct_campaign_report
from app.yandex import YandexError

_RealClient = httpx.Client


@pytest.fixture
def respond(monkeypatch):
    """respond(handler): все httpx.Client в тесте отвечают через handler(request) -> Response."""
    requests: list[httpx.Request] = []

    def install(handler):
        def recording(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            httpx, "Client", lambda **kw: _RealClient(transport=httpx.MockTransport(recording), **kw)
        )
        return requests

    return install


def test_report_parses_revenue_and_missing_goal_data(respond):
    tsv = (
        "Date\tCampaignId\tImpressions\tClicks\tCost\tConversions\tRevenue\n"
        "2026-10-25\t11\t1000\t50\t1234.56\t3\t9000.5\n"
        "2026-10-25\t12\t10\t1\t15\t--\t--\n"
    )
    requests = respond(lambda request: httpx.Response(200, text=tsv))
    rows = fetch_direct_campaign_report("tok", "client", date(2026, 10, 25))
    assert rows == [
        {
            "campaign_id": 11,
            "impressions": 1000,
            "clicks": 50,
            "cost_kopecks": 123456,
            "conversions": 3,
            "revenue_kopecks": 900050,
        },
        {
            "campaign_id": 12,
            "impressions": 10,
            "clicks": 1,
            "cost_kopecks": 1500,
            "conversions": 0,
            "revenue_kopecks": 0,
        },
    ]
    params = json.loads(requests[0].content)["params"]
    assert "Revenue" in params["FieldNames"]
    assert requests[0].headers["Client-Login"] == "client"

    # Повторный запрос — новое имя отчёта, иначе Директ отдаст закэшированный.
    fetch_direct_campaign_report("tok", "client", date(2026, 10, 25))
    second = json.loads(requests[1].content)["params"]["ReportName"]
    assert second != params["ReportName"]
    assert len(second) <= 255


def test_balance_by_login(respond):
    body = {
        "data": {
            "Accounts": [
                {"Login": "Client-A", "Amount": "1500.25", "Currency": "RUB"},
                {"Login": "client-b", "Amount": "0", "Currency": "RUB"},
            ]
        }
    }
    requests = respond(lambda request: httpx.Response(200, json=body))
    balances = fetch_balances("tok", ["client-a", "client-b", "no-shared-account"])
    assert balances == {
        "client-a": {"amount_kopecks": 150025, "currency": "RUB"},
        "client-b": {"amount_kopecks": 0, "currency": "RUB"},
    }
    sent = json.loads(requests[0].content)
    assert sent["method"] == "AccountManagement"
    assert sent["param"]["SelectionCriteria"]["Logins"] == ["client-a", "client-b", "no-shared-account"]


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, json={"error_code": 53, "error_str": "Ошибка авторизации"}), "token-expired"),
        (httpx.Response(200, json={"error_code": 54, "error_str": "Нет прав"}), "direct-denied"),
        (httpx.Response(200, json={"error_code": 500, "error_str": "?"}), "direct"),
        (httpx.Response(502, text="bad gateway"), "direct"),
    ],
)
def test_balance_errors(respond, response, code):
    respond(lambda request: response)
    with pytest.raises(YandexError) as exc:
        fetch_balances("tok", ["client"])
    assert exc.value.code == code
