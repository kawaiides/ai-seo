"""Plan-gating dependency for LLM-spend endpoints.

Order of precedence for letting a request through:
  1. Request carries a valid BYOK header — they pay their own LLM cost.
  2. Logged-in user has an active Subscription — Pro plan unlocks all.
  3. Under the daily free-scan quota (per session_id or IP).

Otherwise 429 with the paywall envelope:
  {
    "error": "quota_exhausted",
    "byok_supported": true,
    "processor": "stripe" | "razorpay",
    "currency": "USD" | "INR",
    "price": 4900 | 99900,
    "price_display": "$49/mo" | "₹999/mo"
  }
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import BYOKProvider, Subscription, SubscriptionStatus, User
from app.services.auth import get_current_user
from app.services.byok import is_key_valid_cached

BYOK_HEADER_OPENAI = "X-BYOK-OpenAI-Key"
BYOK_HEADER_GEMINI = "X-BYOK-Gemini-Key"

DAILY_FREE_LIMIT = int(os.environ.get("AEGIS_FREE_DAILY_LIMIT", "3"))


# Process-local quota counter, keyed by (identity, UTC date). The Scan table
# replaces this in production — keeping in-memory for now so unit tests run
# without Postgres. UTC-day rollover handled by the key tuple.
_quota: dict[tuple[str, date], int] = {}
_quota_lock = threading.Lock()


def _client_identity(request: Request, user: Optional[User]) -> str:
    """One id per visitor. Prefer authed session_id (deduped across IPs);
    fall back to forwarded-for then peer IP."""
    if user is not None:
        return f"sid:{user.session_id}"
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return f"ip:{fwd.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


@dataclass(frozen=True)
class PaywallContext:
    """Returned to the request handler so it knows whether to inject the
    BYOK key into the LLM client and skip the quota counter."""

    user: Optional[User]
    byok_key: Optional[str]
    has_active_subscription: bool

    @property
    def using_byok(self) -> bool:
        return self.byok_key is not None


def _paywall_envelope(country_hint: str | None) -> dict[str, Any]:
    """Lightweight version of /api/geo's response — full geo routing
    lives in app/api/geo.py."""
    in_india = (country_hint or "").upper() == "IN"
    return {
        "error": "quota_exhausted",
        "message": (
            f"Free daily limit reached ({DAILY_FREE_LIMIT}/day). "
            "Add your own API key for unlimited scans, or upgrade to Pro."
        ),
        "byok_supported": True,
        "processor": "razorpay" if in_india else "stripe",
        "currency": "INR" if in_india else "USD",
        "price": 99900 if in_india else 4900,
        "price_display": "₹999/mo" if in_india else "$49/mo",
    }


async def _has_active_subscription(db: AsyncSession, user: User) -> bool:
    row = (
        await db.execute(
            select(Subscription.id)
            .where(Subscription.user_id == user.id)
            .where(Subscription.status == SubscriptionStatus.active)
            .limit(1)
        )
    ).first()
    return row is not None


async def require_pro_or_byok_or_quota(
    request: Request,
    x_byok_openai_key: Optional[str] = Header(default=None, alias=BYOK_HEADER_OPENAI),
    x_byok_gemini_key: Optional[str] = Header(default=None, alias=BYOK_HEADER_GEMINI),
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PaywallContext:
    # 1. BYOK first — cheapest path, never counts against quota.
    if user is not None and x_byok_openai_key:
        if await is_key_valid_cached(db, user, x_byok_openai_key, BYOKProvider.openai):
            return PaywallContext(user=user, byok_key=x_byok_openai_key,
                                  has_active_subscription=False)
        # Header present but cache miss / invalid → fall through to other paths.

    if user is not None and x_byok_gemini_key:
        if await is_key_valid_cached(db, user, x_byok_gemini_key, BYOKProvider.gemini):
            return PaywallContext(user=user, byok_key=x_byok_gemini_key,
                                  has_active_subscription=False)

    # 2. Active subscription bypasses quota.
    has_sub = False
    if user is not None:
        has_sub = await _has_active_subscription(db, user)
        if has_sub:
            return PaywallContext(user=user, byok_key=None,
                                  has_active_subscription=True)

    # 3. Free quota.
    if os.environ.get("AEGIS_DISABLE_RATE_LIMIT") == "1":
        return PaywallContext(user=user, byok_key=None, has_active_subscription=has_sub)

    identity = _client_identity(request, user)
    today = date.today()
    key = (identity, today)
    with _quota_lock:
        used = _quota.get(key, 0)
        if used >= DAILY_FREE_LIMIT:
            country = request.headers.get("CF-IPCountry") or (
                user.ip_country if user else None
            )
            raise HTTPException(
                status_code=429,
                detail=_paywall_envelope(country),
            )
        _quota[key] = used + 1

    return PaywallContext(user=user, byok_key=None, has_active_subscription=has_sub)


def reset_quota() -> None:
    """Test helper — wipe the in-memory counter."""
    with _quota_lock:
        _quota.clear()


async def require_pro_or_byok_or_quota_no_count(
    request: Request,
    x_byok_openai_key: Optional[str] = Header(default=None, alias=BYOK_HEADER_OPENAI),
    x_byok_gemini_key: Optional[str] = Header(default=None, alias=BYOK_HEADER_GEMINI),
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PaywallContext:
    """Resolve plan tier without touching the free-scan quota counter.

    Used by endpoints that gate *features* (Pro-only checks) rather than
    *budget* (LLM spend). The caller decides what to show based on
    `ctx.has_active_subscription` / `ctx.using_byok`; nothing 429s.
    """
    if user is not None and x_byok_openai_key:
        if await is_key_valid_cached(db, user, x_byok_openai_key, BYOKProvider.openai):
            return PaywallContext(
                user=user,
                byok_key=x_byok_openai_key,
                has_active_subscription=False,
            )

    if user is not None and x_byok_gemini_key:
        if await is_key_valid_cached(db, user, x_byok_gemini_key, BYOKProvider.gemini):
            return PaywallContext(
                user=user,
                byok_key=x_byok_gemini_key,
                has_active_subscription=False,
            )

    has_sub = False
    if user is not None:
        has_sub = await _has_active_subscription(db, user)

    return PaywallContext(user=user, byok_key=None, has_active_subscription=has_sub)
