"""Per-hour abuse rate-limit on /api/auth/signup + /api/auth/login.

Exercises the in-memory counter directly so the test doesn't depend on
the auth router's full surface (DB session, email validation, etc.).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import (
    ABUSE_LIMITED_PATHS,
    ABUSE_PER_HOUR_LIMIT,
    app,
    reset_abuse_counter,
)


@pytest.fixture(autouse=True)
def _wipe_counter(monkeypatch):
    reset_abuse_counter()
    monkeypatch.delenv("AEGIS_DISABLE_RATE_LIMIT", raising=False)
    yield
    reset_abuse_counter()


@pytest.fixture
def client():
    return TestClient(app)


def test_abuse_limited_paths_cover_signup_and_login():
    assert "/api/auth/signup" in ABUSE_LIMITED_PATHS
    assert "/api/auth/login" in ABUSE_LIMITED_PATHS


def test_first_n_requests_get_through(client):
    # Even malformed bodies (missing email) reach the auth router and
    # come back with 422 — the abuse limiter doesn't reject them.
    for _ in range(ABUSE_PER_HOUR_LIMIT):
        r = client.post("/api/auth/signup", data={})
        assert r.status_code != 429


def test_extra_request_returns_429_with_envelope(client):
    for _ in range(ABUSE_PER_HOUR_LIMIT):
        client.post("/api/auth/signup", data={})
    r = client.post("/api/auth/signup", data={})
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "auth_abuse_limit"
    assert "signup" in body["message"].lower()


def test_login_path_has_separate_counter(client):
    # Exhaust signup; login should still be allowed (separate path key).
    for _ in range(ABUSE_PER_HOUR_LIMIT):
        client.post("/api/auth/signup", data={})
    r = client.post("/api/auth/login", data={})
    assert r.status_code != 429


def test_disable_env_bypasses_limit(client, monkeypatch):
    monkeypatch.setenv("AEGIS_DISABLE_RATE_LIMIT", "1")
    for _ in range(ABUSE_PER_HOUR_LIMIT * 3):
        r = client.post("/api/auth/signup", data={})
        assert r.status_code != 429


def test_hour_bucket_helper_floors_minute_seconds():
    from datetime import datetime, timezone

    now = datetime(2026, 5, 17, 13, 47, 23, 999_000, tzinfo=timezone.utc)
    floored = main._hour_bucket(now)
    assert floored.minute == 0 and floored.second == 0 and floored.microsecond == 0
    assert floored.hour == 13
