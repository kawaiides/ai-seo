"""HTTP tests for /r/{token} + /r/{token}/p.gif.

Uses FastAPI TestClient with `get_session` dependency overridden to
return a fake AsyncSession that resolves a hard-coded Audit/Prospect.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import reports
from app.db.base import get_session
from app.db.models import Audit, Prospect
from app.main import app
from app.services.tokens import sign_report


@pytest.fixture(autouse=True)
def _signing_keys(monkeypatch):
    monkeypatch.setenv("REPORT_SIGNING_KEY", "k" * 32)


def _audit() -> Audit:
    a = Audit(
        prospect_id=1,
        aeo_score=55,
        band="Significant Gaps",
        failed_checks=[
            {"check_id": "direct_answer", "name": "Direct answer", "score": 5,
             "max_score": 20, "recommendation": "Shorten lede.", "details": {}}
        ],
        missing_gap_types=["how_to"],
        fanout_payload={
            "sub_queries": [
                {"type": "comparative", "query": "X vs Y", "covered": False}
            ]
        },
    )
    a.id = 1001
    a.token_jti = uuid.UUID("11111111-2222-3333-4444-555555555555")
    return a


def _prospect() -> Prospect:
    p = Prospect(
        url="https://aegis.test/post",
        domain="aegis.test",
        target_keyword="X",
    )
    p.id = 1
    return p


class FakeSession:
    """Replays a fixed Audit/Prospect; logs UPDATE counts."""

    def __init__(self):
        self.audit = _audit()
        self.prospect = _prospect()
        self.updates: list[str] = []

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "update outreach" in text:
            if "opens" in text:
                self.updates.append("opens")
            elif "clicks" in text:
                self.updates.append("clicks")
            return _UpdateResult()
        # SELECT statements
        return _SelectResult(self.audit if "audit" in text else self.prospect)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


class _SelectResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj

    def scalar_one(self):
        return self._obj


class _UpdateResult:
    pass


@pytest.fixture
def client_with_fake_db():
    fake = FakeSession()

    async def _override():
        yield fake

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app), fake
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_view_report_returns_html(client_with_fake_db):
    client, fake = client_with_fake_db
    token = sign_report(fake.audit.id, str(fake.audit.token_jti))

    resp = client.get(f"/r/{token}")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "aegis.test" in resp.text
    assert "X vs Y" in resp.text
    assert "clicks" in fake.updates  # HTML view increments clicks


def test_view_report_invalid_token_returns_404(client_with_fake_db):
    client, _ = client_with_fake_db
    resp = client.get("/r/totally-not-a-real-token")
    assert resp.status_code == 404


def test_view_report_wrong_jti_returns_404(client_with_fake_db):
    client, fake = client_with_fake_db
    bad_token = sign_report(fake.audit.id, "11111111-2222-3333-4444-666666666666")
    resp = client.get(f"/r/{bad_token}")
    assert resp.status_code == 404


def test_pixel_returns_gif_and_bumps_opens(client_with_fake_db):
    client, fake = client_with_fake_db
    token = sign_report(fake.audit.id, str(fake.audit.token_jti))

    resp = client.get(f"/r/{token}/p.gif")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/gif"
    assert resp.headers["cache-control"].startswith("no-store")
    assert resp.content[:6] in (b"GIF87a", b"GIF89a")
    assert "opens" in fake.updates
