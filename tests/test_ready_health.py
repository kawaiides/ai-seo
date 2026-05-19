"""Sanity tests for /api/health + /api/ready.

The DB probe inside /api/ready needs a real engine, which isn't safe in
the unit test environment (no Postgres). We assert behaviour around the
JSON envelope shape + skip semantics for OpenAI without a key.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_health_returns_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_returns_envelope_with_probes(client, monkeypatch):
    # No OPENAI_API_KEY → openai probe is skipped, not run. DB probe will
    # fail in this env (no Postgres) → ready returns 503. That's the
    # exact contract we want a load-balancer to use.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.get("/api/ready")
    body = r.json()
    assert r.status_code in (200, 503)
    assert "probes" in body
    assert "postgres" in body["probes"]
    assert body["probes"]["openai"]["status"] == "skipped"


def test_ready_envelope_carries_elapsed_ms(client):
    r = client.get("/api/ready")
    body = r.json()
    assert isinstance(body.get("elapsed_ms"), int)
    assert body["elapsed_ms"] >= 0
