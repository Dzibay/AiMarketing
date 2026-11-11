"""Тарифы для сайта: только включённые, без служебных полей, единицы — во сколько раз больше младшего."""

from fastapi.testclient import TestClient

from app import plans
from app.api import site
from app.main import app


def _plan(pid, *, price, units, trial=False, active=True, projects=3, days=30):
    return {
        "id": pid,
        "name": f"Тариф {pid}",
        "price_rub": float(price),
        "days": days,
        "units_limit": units,
        "projects_limit": projects,
        "features": {k: True for k in plans.FEATURES},
        "is_trial": trial,
        "active": active,
        "position": pid,
        "discount_3_pct": 5.0,
        "discount_12_pct": 15.0,
        "users": 42,
    }


def test_catalog_hides_units_and_internal_fields():
    rows = [
        _plan(1, price=0, units=500, trial=True, days=7, projects=1),
        _plan(2, price=990, units=1000),
        _plan(3, price=2490, units=3000),
        _plan(4, price=9990, units=None, projects=None),
        _plan(5, price=1490, units=2000, active=False),
    ]
    out = plans.public_catalog_from(rows)

    assert [p["id"] for p in out["items"]] == [2, 3, 4]
    assert out["trial"] == {"days": 7, "projects_limit": 1}
    first, second, top = out["items"]
    assert first["units_factor"] == 1.0
    assert second["units_factor"] == 3.0
    assert top["units_unlimited"] is True and top["units_factor"] is None
    assert first["term_discounts"] == {"1": 0.0, "3": 5.0, "12": 15.0}
    for item in out["items"]:
        assert "units_limit" not in item and "users" not in item and "active" not in item


def test_catalog_without_trial_or_plans():
    assert plans.public_catalog_from([]) == {"items": [], "features": plans.FEATURES, "trial": None}


def test_site_plans_endpoint_is_public_and_cached(monkeypatch):
    calls = []

    def fake():
        calls.append(1)
        return {"items": [], "features": {}, "trial": None}

    monkeypatch.setattr(plans, "public_catalog", fake)
    monkeypatch.setitem(site._plans_cache, "data", None)
    client = TestClient(app)
    assert client.get("/api/site/plans").json() == {"items": [], "features": {}, "trial": None}
    assert client.get("/api/site/plans").status_code == 200
    assert len(calls) == 1
