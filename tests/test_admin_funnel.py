"""HTTP tests for /admin/funnel."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_session
from app.db.models import FunnelStage
from app.main import app


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "topsecret")
    monkeypatch.setenv("REPORT_SIGNING_KEY", "k" * 32)


class FakeAdminSession:
    """Returns a small set of synthetic funnel events when queried."""

    def __init__(self):
        self.events = [
            (FunnelStage.discovered, 1),
            (FunnelStage.discovered, 2),
            (FunnelStage.discovered, 3),
            (FunnelStage.audited, 4),
            (FunnelStage.audited, 5),
            (FunnelStage.contacted, 6),
            (FunnelStage.engaged, 7),
        ]

    async def execute(self, _stmt):
        return _Rows(self.events)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _Rows:
    def __init__(self, items): self._items = items
    def all(self): return list(self._items)
    def scalar_one(self): return 0


@pytest.fixture
def client():
    fake = FakeAdminSession()

    async def _override():
        yield fake

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_funnel_json_returns_counts_and_rates(client):
    resp = client.get("/admin/funnel?days=30", headers={"X-Admin-Token": "topsecret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"]["days"] == 30
    assert body["counts"]["discovered"] == 3
    assert body["counts"]["audited"] == 2
    assert body["counts"]["contacted"] == 1
    assert body["counts"]["engaged"] == 1
    assert body["counts"]["converted"] == 0
    # contact_to_engage = 1/1 = 1.0
    assert body["rates"]["contact_to_engage"] == 1.0


def test_funnel_json_requires_token(client):
    resp = client.get("/admin/funnel")
    assert resp.status_code == 401


def test_funnel_json_wrong_token(client):
    resp = client.get("/admin/funnel", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401


def test_funnel_json_fails_closed_without_env(client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    resp = client.get("/admin/funnel", headers={"X-Admin-Token": "anything"})
    assert resp.status_code == 503
