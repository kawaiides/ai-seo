"""Unit tests for services/byok."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import respx
from httpx import Response

from app.db.models import BYOKProvider, BYOKValidation, User
from app.services import byok
from app.services.byok import (
    OPENAI_VALIDATE_URL,
    hash_key,
    is_key_valid_cached,
    validate_key,
)


def _user() -> User:
    u = User(id=uuid.uuid4(), session_id="sess-x")
    return u


def test_hash_key_is_deterministic_64char_hex():
    h1 = hash_key("sk-fake-12345")
    h2 = hash_key("sk-fake-12345")
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_key_changes_with_input():
    assert hash_key("a") != hash_key("b")


class FakeSession:
    def __init__(self, *, existing: list[BYOKValidation] | None = None):
        self.existing = list(existing or [])
        self.added: list[Any] = []
        self.flush_calls = 0

    async def execute(self, stmt):
        return _R(self.existing[0] if self.existing else None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_calls += 1


class _R:
    def __init__(self, v): self._v = v
    def scalar_one_or_none(self): return self._v


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_live_check_success_caches():
    respx.get(OPENAI_VALIDATE_URL).mock(return_value=Response(200, json={"data": []}))
    session = FakeSession()
    user = _user()

    ok = await validate_key(session, user, "sk-real")

    assert ok is True
    assert len(session.added) == 1
    rec = session.added[0]
    assert isinstance(rec, BYOKValidation)
    assert rec.valid is True
    assert rec.key_hash == hash_key("sk-real")
    # Raw key never persisted anywhere.
    assert "sk-real" not in str(vars(rec))


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_live_check_401_caches_invalid():
    respx.get(OPENAI_VALIDATE_URL).mock(return_value=Response(401, json={"error": "nope"}))
    session = FakeSession()
    user = _user()

    ok = await validate_key(session, user, "sk-fake")

    assert ok is False
    rec = session.added[0]
    assert rec.valid is False


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_uses_cached_value_within_ttl(monkeypatch):
    # Respx mock would explode if we hit the network — proving the cache hit.
    respx.get(OPENAI_VALIDATE_URL).mock(side_effect=AssertionError("must not hit network"))

    user = _user()
    fresh = BYOKValidation(
        user_id=user.id,
        key_hash=hash_key("sk-cached"),
        provider=BYOKProvider.openai,
        valid=True,
        last_verified_at=datetime.now(tz=timezone.utc) - timedelta(minutes=10),
    )
    session = FakeSession(existing=[fresh])

    ok = await validate_key(session, user, "sk-cached")

    assert ok is True
    assert session.added == []  # used existing row


@pytest.mark.asyncio
@respx.mock
async def test_validate_key_refreshes_stale_cache():
    respx.get(OPENAI_VALIDATE_URL).mock(return_value=Response(200))
    user = _user()
    stale = BYOKValidation(
        user_id=user.id,
        key_hash=hash_key("sk-stale"),
        provider=BYOKProvider.openai,
        valid=False,
        last_verified_at=datetime.now(tz=timezone.utc) - timedelta(days=2),
    )
    session = FakeSession(existing=[stale])

    ok = await validate_key(session, user, "sk-stale")

    assert ok is True
    # Existing row updated, not duplicated.
    assert stale.valid is True
    assert session.added == []


@pytest.mark.asyncio
async def test_validate_key_empty_raw_returns_false():
    session = FakeSession()
    assert await validate_key(session, _user(), "") is False
    assert session.added == []


@pytest.mark.asyncio
async def test_is_key_valid_cached_returns_false_when_no_row():
    session = FakeSession(existing=[])
    assert await is_key_valid_cached(session, _user(), "sk-anything") is False


@pytest.mark.asyncio
async def test_is_key_valid_cached_returns_false_when_stale():
    user = _user()
    rec = BYOKValidation(
        user_id=user.id,
        key_hash=hash_key("sk-x"),
        provider=BYOKProvider.openai,
        valid=True,
        last_verified_at=datetime.now(tz=timezone.utc) - timedelta(days=2),
    )
    session = FakeSession(existing=[rec])
    assert await is_key_valid_cached(session, user, "sk-x") is False


@pytest.mark.asyncio
async def test_is_key_valid_cached_honors_valid_flag():
    user = _user()
    rec = BYOKValidation(
        user_id=user.id,
        key_hash=hash_key("sk-x"),
        provider=BYOKProvider.openai,
        valid=False,
        last_verified_at=datetime.now(tz=timezone.utc) - timedelta(minutes=5),
    )
    session = FakeSession(existing=[rec])
    assert await is_key_valid_cached(session, user, "sk-x") is False
