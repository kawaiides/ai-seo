"""Unit tests for POST /api/account/delete."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_session
from app.db.models import User
from app.main import app
from app.services.auth import COOKIE_NAME, get_current_user


class _DeletingSession:
    """FakeSession that records `delete()` + `flush()` calls."""

    def __init__(self):
        self.deleted: list[Any] = []
        self.flushed = False

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushed = True


@pytest.fixture
def authed(monkeypatch):
    user = User(
        id=uuid.uuid4(),
        session_id="sid-1",
        email="alice@example.com",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session = _DeletingSession()

    async def _user():
        return user

    async def _session():
        yield session

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    try:
        with TestClient(app) as c:
            yield c, user, session
    finally:
        app.dependency_overrides.clear()


def test_delete_with_matching_email_succeeds(authed):
    client, user, session = authed
    r = client.post(
        "/api/account/delete",
        data={"confirm_email": "alice@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?account_deleted=")
    assert session.deleted == [user]
    assert session.flushed is True
    # Session cookie must be cleared so the next page load is anonymous.
    set_cookie = r.headers.get("set-cookie", "")
    assert COOKIE_NAME in set_cookie


def test_delete_with_case_insensitive_email_succeeds(authed):
    client, _, session = authed
    r = client.post(
        "/api/account/delete",
        data={"confirm_email": "  Alice@Example.com  "},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(session.deleted) == 1


def test_delete_with_mismatched_email_400(authed):
    client, _, session = authed
    r = client.post(
        "/api/account/delete",
        data={"confirm_email": "wrong@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    body = r.json()
    # The app's content-negotiated error handler flattens dict-shaped
    # HTTPException detail into the envelope root (see app/main.py).
    assert body["error"] == "confirm_email_mismatch"
    assert session.deleted == []


def test_delete_anonymous_user_with_no_email_400(monkeypatch):
    """A passwordless session with email=NULL cannot self-delete."""
    anon = User(
        id=uuid.uuid4(),
        session_id="anon",
        email=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    async def _user():
        return anon

    class _S:
        async def delete(self, *a): self.touched = True
        async def flush(self): pass

    s = _S()

    async def _session():
        yield s

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_session] = _session
    try:
        with TestClient(app) as c:
            r = c.post("/api/account/delete", data={"confirm_email": "anyone@example.com"})
        assert r.status_code == 400
        assert r.json()["error"] == "confirm_email_mismatch"
        assert not hasattr(s, "touched")
    finally:
        app.dependency_overrides.clear()


def test_delete_unauthenticated_redirects_to_login():
    async def _no_user():
        return None

    async def _session():
        yield None

    app.dependency_overrides[get_current_user] = _no_user
    app.dependency_overrides[get_session] = _session
    try:
        with TestClient(app) as c:
            r = c.post(
                "/api/account/delete",
                data={"confirm_email": "anyone@example.com"},
                follow_redirects=False,
            )
        assert r.status_code == 303
        assert r.headers["location"] == "/login"
    finally:
        app.dependency_overrides.clear()
