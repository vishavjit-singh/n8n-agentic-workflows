import importlib.util

import pytest

from n8nkit import asgi, mocks


def test_kb_search_ranks_relevant_articles():
    r = asgi.request(mocks.create_app(api_key=""), "GET", "/kb/search?q=reset+my+password")
    assert r.status == 200
    results = r.json()["results"]
    assert results[0]["id"] == "KB-104" and results[0]["score"] == 1.0
    assert asgi.request(mocks.create_app(api_key=""), "GET", "/kb/search?q=").status == 400


def test_orders_and_metrics_are_deterministic():
    app = mocks.create_app(api_key="")
    order = asgi.request(app, "GET", "/orders/ORD-10003").json()
    assert order["status"] == "delivered" and order["synthetic"] is True
    assert asgi.request(app, "GET", "/orders/ORD-99999").status == 404
    a = asgi.request(app, "GET", "/metrics/orders?date=2026-11-15").json()
    assert a == asgi.request(app, "GET", "/metrics/orders?date=2026-11-15").json()
    assert {t["team"] for t in a["orders_by_team"]} == set(mocks.TEAMS)
    assert asgi.request(app, "GET", "/metrics/support?date=not-a-date").status == 400


def test_api_key_is_enforced_when_configured():
    app = mocks.create_app(api_key="s3cret")
    assert asgi.request(app, "GET", "/health").status == 200
    assert asgi.request(app, "GET", "/orders/ORD-10003").status == 401
    assert asgi.request(app, "GET", "/orders/ORD-10003", headers={"X-API-Key": "s3cret"}).status == 200


@pytest.mark.skipif(importlib.util.find_spec("httpx") is None, reason="httpx not installed (CI installs it)")
def test_in_process_client_matches_starlette_testclient():
    from starlette.testclient import TestClient

    app = mocks.create_app(api_key="")
    client = TestClient(app)
    for path in ("/kb/search?q=parcel+lost", "/orders/ORD-10004", "/metrics/support?date=2026-11-16"):
        ours = asgi.request(app, "GET", path)
        theirs = client.get(path)
        assert (ours.status, ours.json()) == (theirs.status_code, theirs.json())
