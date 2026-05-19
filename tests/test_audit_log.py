"""Unit tests for the AccountAuditLog writer.

DB-touching writer is exercised against a stub session that captures
the row before flush. End-to-end calls from the account/keys/orgs
routes are covered by their respective test modules.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services import audit_log
from app.services.audit_log import (
    ACTION_ACCOUNT_DELETE,
    ACTION_API_KEY_CREATE,
    record_audit_event,
)


class _StubSession:
    def __init__(self, *, flush_raises: Exception | None = None):
        self.added: list[Any] = []
        self.flushed = False
        self._flush_raises = flush_raises

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        if self._flush_raises:
            raise self._flush_raises
        self.flushed = True


class _FakeRequest:
    """Just enough Request shape for `_client_ip` + `_user_agent`."""

    def __init__(self, headers: dict[str, str], client_host: str | None = "1.2.3.4"):
        self.headers = headers

        class _C:
            host = client_host
        self.client = _C() if client_host else None


@pytest.mark.asyncio
async def test_record_event_persists_row_with_default_metadata():
    session = _StubSession()
    actor = uuid.uuid4()
    row = await record_audit_event(
        session,
        action=ACTION_ACCOUNT_DELETE,
        actor_user_id=actor,
        subject_user_id=actor,
        meta={"email": "alice@example.com"},
        request=_FakeRequest({"user-agent": "AegisTest/1.0"}),
    )
    assert session.added == [row]
    assert session.flushed
    assert row.action == ACTION_ACCOUNT_DELETE
    assert row.actor_user_id == actor
    assert row.subject_user_id == actor
    assert row.meta == {"email": "alice@example.com"}
    assert row.user_agent == "AegisTest/1.0"
    assert row.request_ip == "1.2.3.4"


@pytest.mark.asyncio
async def test_x_forwarded_for_first_hop_wins():
    session = _StubSession()
    row = await record_audit_event(
        session,
        action=ACTION_API_KEY_CREATE,
        actor_user_id=uuid.uuid4(),
        request=_FakeRequest({"x-forwarded-for": "9.9.9.9, 8.8.8.8"}, client_host="1.2.3.4"),
    )
    assert row.request_ip == "9.9.9.9"


@pytest.mark.asyncio
async def test_no_request_means_no_ip_or_ua():
    session = _StubSession()
    row = await record_audit_event(
        session,
        action=ACTION_API_KEY_CREATE,
        actor_user_id=uuid.uuid4(),
        request=None,
    )
    assert row.request_ip is None
    assert row.user_agent is None


@pytest.mark.asyncio
async def test_long_user_agent_is_truncated():
    session = _StubSession()
    long_ua = "x" * 1000
    row = await record_audit_event(
        session,
        action=ACTION_API_KEY_CREATE,
        actor_user_id=uuid.uuid4(),
        request=_FakeRequest({"user-agent": long_ua}),
    )
    assert row.user_agent is not None
    assert len(row.user_agent) == 512


@pytest.mark.asyncio
async def test_writer_swallows_flush_errors():
    """A failed audit-log write must never break the action it tracks."""
    session = _StubSession(flush_raises=RuntimeError("boom"))
    row = await record_audit_event(
        session,
        action=ACTION_ACCOUNT_DELETE,
        actor_user_id=uuid.uuid4(),
        request=_FakeRequest({}),
    )
    # Row was constructed + added (caller can still see it), but flush
    # raised — the helper swallowed it.
    assert row is not None
    assert session.added == [row]
    assert session.flushed is False
