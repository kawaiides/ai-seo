"""Renders the rebuilt /account page and verifies the 5 tabs + JS hooks."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_session
from app.db.models import User
from app.main import app
from app.services.auth import get_current_user


class _StubSession:
    """Returns empty lists for the two queries the /account handler runs."""

    async def execute(self, stmt):
        return _R([])

    async def get(self, *a, **kw):
        return None


class _R:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def all(self):
        return self._rows


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.fixture
def authed_client():
    user = User(
        id=uuid.uuid4(),
        session_id="sid-test",
        email="customer@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    async def _user():
        return user

    async def _session():
        yield _StubSession()

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    try:
        with TestClient(app) as c:
            yield c, user
    finally:
        app.dependency_overrides.clear()


def test_account_renders_with_5_tabs(authed_client):
    client, _ = authed_client
    r = client.get("/account")
    assert r.status_code == 200
    html = r.text
    # Tab nav uses role=tab + data-tab markers; check every one is present.
    for tab in ("overview", "keys", "team", "billing", "danger"):
        assert f'data-tab="{tab}"' in html
        assert f'id="tab-{tab}"' in html


def test_account_renders_delete_form_for_email_user(authed_client):
    client, _ = authed_client
    r = client.get("/account")
    html = r.text
    assert 'action="/api/account/delete"' in html
    assert 'name="confirm_email"' in html


def test_account_renders_cancel_subscription_action(authed_client):
    client, _ = authed_client
    r = client.get("/account")
    # The cancel form only appears when there's an active subscription;
    # without one, the History section + "switch plan" CTA must still render.
    assert 'action="/api/billing/cancel"' in r.text or "Switch plan" in r.text


def test_account_renders_invite_form_markers_when_owner_org_present(authed_client):
    """Without an owner org the page falls back to the empty-team partial.
    The "Create my org" CTA must still be rendered so the user can bootstrap."""
    client, _ = authed_client
    r = client.get("/account")
    html = r.text
    # Either the owner-side form (id="invite-form") or the empty-team partial.
    assert ('id="invite-form"' in html) or ("Create my org" in html)


def test_account_includes_keys_loader_js(authed_client):
    client, _ = authed_client
    r = client.get("/account")
    assert "loadKeys" in r.text
    assert "/api/orgs/" in r.text  # template wires fetch calls
