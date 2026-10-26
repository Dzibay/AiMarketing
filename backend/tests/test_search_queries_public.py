from datetime import date

from app.search_queries import _public


def test_search_query_public_metrics():
    row = {
        "id": 1,
        "account_id": 2,
        "campaign_row_id": 3,
        "adgroup_row_id": 4,
        "adgroup_id": 55,
        "adgroup_name": "G1",
        "query": "купить слона",
        "matched_keyword": "слон",
        "impressions": 100,
        "clicks": 4,
        "cost_kopecks": 800,
        "conversions": 2,
        "date_from": date(2026, 1, 1),
        "date_to": date(2026, 1, 14),
        "synced_at": None,
    }
    out = _public(row)
    assert out["cost_rub"] == 8.0
    assert out["cpc_rub"] == 2.0
    assert out["cpa_rub"] == 4.0
    assert out["query"] == "купить слона"
