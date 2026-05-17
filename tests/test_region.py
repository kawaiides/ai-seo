"""Unit tests for services/region + GET /api/region."""

from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.main import app
from app.services import region
from app.services.region import (
    _DEFAULT,
    _PRICE_MAP,
    route_for_country,
)


@pytest.fixture(autouse=True)
def _signing(monkeypatch):
    monkeypatch.setenv("REPORT_SIGNING_KEY", "k" * 32)


@pytest.fixture(autouse=True)
def _reset_ip_cache():
    region.reset_cache()


# ---- route_for_country ----


def test_route_india_returns_inr_razorpay():
    out = route_for_country("IN")
    assert out["currency"] == "INR"
    assert out["price"] == 99900
    assert out["price_display"] == "₹999/mo"
    assert out["processor"] == "razorpay"


def test_route_us_returns_usd_stripe():
    out = route_for_country("US")
    assert out["currency"] == "USD"
    assert out["price"] == 4900
    assert out["price_display"] == "$49/mo"
    assert out["processor"] == "stripe"


def test_route_unknown_falls_back_to_default():
    out = route_for_country("ZZ")
    assert out["currency"] == "USD"
    assert out["processor"] == "stripe"


def test_route_case_insensitive():
    assert route_for_country("in")["currency"] == "INR"
    assert route_for_country("us")["currency"] == "USD"


# ---- GET /api/region via TestClient ----


def test_region_endpoint_uses_cf_ipcountry():
    with TestClient(app) as client:
        resp = client.get("/api/region", headers={"CF-IPCountry": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["country"] == "IN"
    assert body["currency"] == "INR"
    assert body["processor"] == "razorpay"
    assert body["price_display"] == "₹999/mo"


def test_region_endpoint_us_default_when_no_signal():
    with TestClient(app) as client:
        resp = client.get("/api/region")
    body = resp.json()
    # TestClient peer ip is "testclient" → skipped, fallback to default US.
    assert body["country"] == "US"
    assert body["currency"] == "USD"
    assert body["processor"] == "stripe"


def test_region_endpoint_ignores_bad_cf_country():
    with TestClient(app) as client:
        # Wrong length: not 2 chars → fall back.
        resp = client.get("/api/region", headers={"CF-IPCountry": "INDIA"})
    assert resp.json()["country"] == "US"


@respx.mock
def test_region_endpoint_uses_ipapi_via_xff():
    respx.get("https://ipapi.co/8.8.8.8/country/").mock(
        return_value=Response(200, text="US")
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/region",
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
    body = resp.json()
    assert body["country"] == "US"


@respx.mock
def test_region_endpoint_uses_ipapi_routes_india_through_xff():
    respx.get("https://ipapi.co/103.0.0.1/country/").mock(
        return_value=Response(200, text="IN")
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/region",
            headers={"X-Forwarded-For": "103.0.0.1"},
        )
    body = resp.json()
    assert body["country"] == "IN"
    assert body["processor"] == "razorpay"


@respx.mock
def test_region_endpoint_ipapi_failure_falls_back_to_us():
    respx.get("https://ipapi.co/8.8.8.8/country/").mock(
        return_value=Response(500, text="boom")
    )
    with TestClient(app) as client:
        resp = client.get("/api/region", headers={"X-Forwarded-For": "8.8.8.8"})
    assert resp.json()["country"] == "US"
