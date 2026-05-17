"""Unit tests for services/gating.

Exercises require_pro_or_byok_or_quota directly with a stub Request.
The three precedence branches (BYOK / active sub / quota / paywall) are
each covered, plus the paywall envelope shape.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import pytest
from fastapi import HTTPException, Request

from app.db.models import (
    BYOKProvider,
    BYOKValidation,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.services import gating
from app.services.byok import hash_key
from app.services.gating import (
    PaywallContext,
    consume_quota_slot_or_paywall,
    require_pro_or_byok_or_quota,
)


def _request(headers: dict[str, str] | None = None) -> Request:
    """Minimal ASGI Request — just enough for the dep code paths we hit."""
    h = headers or {}
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in h.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/fanout/generate",
        "headers": raw_headers,
        "query_string": b"",
        "client": ("127.0.0.1", 0),
    }
    return Request(scope)


def _user() -> User:
    return User(id=uuid.uuid4(), session_id="sid-test")


class FakeSession:
    """Routes by SQL fragment to fake BYOK cache + Subscription lookups."""

    def __init__(self, *,
                 byok_valid: bool = False,
                 subscription_active: bool = False):
        self._byok_valid = byok_valid
        self._sub_active = subscription_active

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "byok_validation" in text:
            return _R("ok" if self._byok_valid else None)
        if "subscription" in text:
            return _R(("sub",) if self._sub_active else None)
        return _R(None)


class _R:
    def __init__(self, v): self._v = v
    def scalar_one_or_none(self): return self._v
    def first(self): return self._v


@pytest.fixture(autouse=True)
def _isolated_quota(monkeypatch):
    gating.reset_quota()
    monkeypatch.delenv("AEGIS_DISABLE_RATE_LIMIT", raising=False)


# ---- BYOK precedence ----


@pytest.mark.asyncio
async def test_valid_byok_header_bypasses_quota(monkeypatch):
    user = _user()
    session = FakeSession(byok_valid=True)

    async def _cached_true(*a, **kw):
        return True

    monkeypatch.setattr(gating, "is_key_valid_cached", _cached_true)

    ctx = await require_pro_or_byok_or_quota(
        _request({"X-BYOK-OpenAI-Key": "sk-byok"}),
        x_byok_openai_key="sk-byok",
        x_byok_gemini_key=None,
        user=user,
        db=session,
    )
    assert ctx.using_byok is True
    assert ctx.byok_key == "sk-byok"
    assert ctx.has_active_subscription is False
    # Quota counter must not have moved.
    assert sum(gating._quota.values()) == 0


@pytest.mark.asyncio
async def test_invalid_byok_falls_through_to_quota(monkeypatch):
    user = _user()
    session = FakeSession(byok_valid=False)

    async def _cached_false(*a, **kw):
        return False

    monkeypatch.setattr(gating, "is_key_valid_cached", _cached_false)

    ctx = await require_pro_or_byok_or_quota(
        _request(),
        x_byok_openai_key="sk-bad",
        x_byok_gemini_key=None,
        user=user,
        db=session,
    )
    assert ctx.using_byok is False
    assert sum(gating._quota.values()) == 1


# ---- Active subscription precedence ----


@pytest.mark.asyncio
async def test_active_subscription_bypasses_quota():
    user = _user()
    session = FakeSession(subscription_active=True)
    ctx = await require_pro_or_byok_or_quota(
        _request(),
        x_byok_openai_key=None,
        x_byok_gemini_key=None,
        user=user,
        db=session,
    )
    assert ctx.has_active_subscription is True
    assert ctx.using_byok is False
    assert sum(gating._quota.values()) == 0


# ---- Quota path ----


@pytest.mark.asyncio
async def test_first_three_anonymous_requests_pass():
    session = FakeSession()
    for _ in range(3):
        ctx = await require_pro_or_byok_or_quota(
            _request(),
            x_byok_openai_key=None,
            x_byok_gemini_key=None,
            user=None,
            db=session,
        )
        assert ctx.using_byok is False
        assert ctx.has_active_subscription is False
    assert sum(gating._quota.values()) == 3


@pytest.mark.asyncio
async def test_fourth_request_returns_paywall_envelope():
    session = FakeSession()
    # Burn the three free scans.
    for _ in range(3):
        await require_pro_or_byok_or_quota(
            _request(), x_byok_openai_key=None, x_byok_gemini_key=None,
            user=None, db=session,
        )
    with pytest.raises(HTTPException) as exc:
        await require_pro_or_byok_or_quota(
            _request(), x_byok_openai_key=None, x_byok_gemini_key=None,
            user=None, db=session,
        )
    assert exc.value.status_code == 429
    env = exc.value.detail
    assert env["error"] == "quota_exhausted"
    assert env["byok_supported"] is True
    assert env["processor"] == "stripe"
    assert env["currency"] == "USD"
    assert env["price"] == 4900
    assert env["price_display"] == "$49/mo"


@pytest.mark.asyncio
async def test_paywall_envelope_routes_india_to_razorpay():
    session = FakeSession()
    for _ in range(3):
        await require_pro_or_byok_or_quota(
            _request({"CF-IPCountry": "IN"}),
            x_byok_openai_key=None, x_byok_gemini_key=None,
            user=None, db=session,
        )
    with pytest.raises(HTTPException) as exc:
        await require_pro_or_byok_or_quota(
            _request({"CF-IPCountry": "IN"}),
            x_byok_openai_key=None, x_byok_gemini_key=None,
            user=None, db=session,
        )
    env = exc.value.detail
    assert env["processor"] == "razorpay"
    assert env["currency"] == "INR"
    assert env["price"] == 99900
    assert env["price_display"] == "₹999/mo"


@pytest.mark.asyncio
async def test_disable_rate_limit_env_skips_quota(monkeypatch):
    monkeypatch.setenv("AEGIS_DISABLE_RATE_LIMIT", "1")
    session = FakeSession()
    for _ in range(50):
        ctx = await require_pro_or_byok_or_quota(
            _request(), x_byok_openai_key=None, x_byok_gemini_key=None,
            user=None, db=session,
        )
        assert ctx.byok_key is None
    assert sum(gating._quota.values()) == 0  # bypassed entirely


# ---- consume_quota_slot_or_paywall (used by GEO probe cache-miss path) ----


def _ctx(*, byok: bool = False, sub: bool = False, user: Optional[User] = None) -> PaywallContext:
    return PaywallContext(
        user=user,
        byok_key="sk-byok" if byok else None,
        has_active_subscription=sub,
    )


def test_consume_slot_bypassed_by_byok():
    consume_quota_slot_or_paywall(_request(), _ctx(byok=True))
    assert sum(gating._quota.values()) == 0


def test_consume_slot_bypassed_by_subscription():
    consume_quota_slot_or_paywall(_request(), _ctx(sub=True))
    assert sum(gating._quota.values()) == 0


def test_consume_slot_bypassed_by_disable_env(monkeypatch):
    monkeypatch.setenv("AEGIS_DISABLE_RATE_LIMIT", "1")
    for _ in range(50):
        consume_quota_slot_or_paywall(_request(), _ctx())
    assert sum(gating._quota.values()) == 0


def test_consume_slot_counts_and_429s():
    for _ in range(3):
        consume_quota_slot_or_paywall(_request(), _ctx())
    assert sum(gating._quota.values()) == 3
    with pytest.raises(HTTPException) as exc:
        consume_quota_slot_or_paywall(_request(), _ctx())
    assert exc.value.status_code == 429
    env = exc.value.detail
    assert env["error"] == "quota_exhausted"
    assert env["price_display"] == "$49/mo"


def test_consume_slot_routes_india_to_razorpay():
    for _ in range(3):
        consume_quota_slot_or_paywall(_request({"CF-IPCountry": "IN"}), _ctx())
    with pytest.raises(HTTPException) as exc:
        consume_quota_slot_or_paywall(_request({"CF-IPCountry": "IN"}), _ctx())
    env = exc.value.detail
    assert env["processor"] == "razorpay"
    assert env["price"] == 99900
