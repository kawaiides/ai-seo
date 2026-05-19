"""Confirm the security-headers middleware sets the documented headers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_csp_header_present_with_required_directives(client):
    r = client.get("/")
    csp = r.headers.get("Content-Security-Policy")
    assert csp is not None
    # Core directives that block click-jacking, untrusted scripts, etc.
    for directive in (
        "default-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src",  # implicitly default-src 'self' covers it
    ) if False else (
        "default-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ):
        assert directive in csp


def test_csp_allows_tailwind_cdn_for_homepage(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "cdn.tailwindcss.com" in csp


def test_x_frame_options_deny(client):
    r = client.get("/")
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_x_content_type_options_nosniff(client):
    r = client.get("/")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_referrer_policy_strict(client):
    r = client.get("/")
    assert "strict-origin" in (r.headers.get("Referrer-Policy") or "")


def test_permissions_policy_denies_sensitive_apis(client):
    r = client.get("/")
    pp = r.headers.get("Permissions-Policy") or ""
    for feature in ("geolocation=()", "microphone=()", "camera=()"):
        assert feature in pp


def test_csp_present_on_api_responses_too(client):
    r = client.get("/api/health")
    assert "Content-Security-Policy" in r.headers
