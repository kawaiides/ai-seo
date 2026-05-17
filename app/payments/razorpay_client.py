"""Razorpay SDK wrapper.

Subscriptions only — one-off payments not used here. The Indian-market
play is UPI Autopay: NPCI rails, single-PIN consent, zero MDR on UPI,
and (critically) sidesteps the RBI card e-mandate's 24-hour pre-debit SMS
+ AFA-per-renewal friction that kills card-on-file renewal rates.

Three responsibilities:
  * Create a Subscription against the pre-provisioned `RAZORPAY_PLAN_ID_UPI`.
  * Verify webhook signatures (HMAC-SHA256 of raw body vs `X-Razorpay-Signature`).
  * Map Razorpay event types to our internal Subscription state machine.

Vendor types are contained — callers see plain dicts.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from typing import Any

import razorpay

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubscriptionResult:
    subscription_id: str
    short_url: str | None


class RazorpayNotConfigured(RuntimeError):
    """Missing env var for the requested operation."""


class RazorpaySignatureError(Exception):
    """Webhook HMAC verification failed."""


def _key_id() -> str:
    key = os.environ.get("RAZORPAY_KEY_ID")
    if not key:
        raise RazorpayNotConfigured("RAZORPAY_KEY_ID is not set")
    return key


def _key_secret() -> str:
    secret = os.environ.get("RAZORPAY_KEY_SECRET")
    if not secret:
        raise RazorpayNotConfigured("RAZORPAY_KEY_SECRET is not set")
    return secret


def _webhook_secret() -> str:
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise RazorpayNotConfigured("RAZORPAY_WEBHOOK_SECRET is not set")
    return secret


def _plan_id() -> str:
    plan_id = os.environ.get("RAZORPAY_PLAN_ID_UPI") or os.environ.get(
        "RAZORPAY_PLAN_ID"
    )
    if not plan_id:
        raise RazorpayNotConfigured("RAZORPAY_PLAN_ID_UPI is not set")
    return plan_id


def _client() -> razorpay.Client:
    client = razorpay.Client(auth=(_key_id(), _key_secret()))
    return client


def create_subscription(
    *,
    user_id: str,
    customer_email: str | None = None,
    total_count: int = 120,  # 10 years of monthly renewals
    notify: bool = True,
) -> SubscriptionResult:
    """Create a Razorpay Subscription on the UPI Autopay plan.

    `notes` carries our internal `User.id` (UUID string) so the webhook
    handler can join back without a parallel mapping table — same pattern
    we use for Stripe's `client_reference_id`.
    """
    notes: dict[str, str] = {"user_id": user_id}
    if customer_email:
        notes["email"] = customer_email
    payload: dict[str, Any] = {
        "plan_id": _plan_id(),
        "total_count": int(total_count),
        "customer_notify": 1 if notify else 0,
        "notes": notes,
    }
    sub = _client().subscription.create(data=payload)
    return SubscriptionResult(
        subscription_id=sub["id"],
        short_url=sub.get("short_url"),
    )


def verify_webhook(raw_body: bytes, sig_header: str | None) -> dict[str, Any]:
    """Verify Razorpay's HMAC-SHA256 signature and return the parsed event.

    Constant-time comparison via `hmac.compare_digest`. Raises
    `RazorpaySignatureError` on bad header / bad sig / malformed body.
    """
    if not sig_header:
        raise RazorpaySignatureError("missing X-Razorpay-Signature header")
    secret = _webhook_secret().encode("utf-8")
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_header.strip()):
        raise RazorpaySignatureError("bad signature")
    import json
    try:
        return json.loads(raw_body)
    except ValueError as e:
        raise RazorpaySignatureError(f"malformed payload: {e}") from e


# ---- Event → internal state mapping ----

ACTIVATING_EVENTS = {
    "subscription.activated",
    "subscription.charged",
    "subscription.resumed",
}
FAILING_EVENTS = {
    "subscription.halted",
    "subscription.pending",
    "payment.failed",
}
CANCELING_EVENTS = {
    "subscription.cancelled",
    "subscription.completed",
}


def extract_subscription_fields(event: dict[str, Any]) -> dict[str, Any]:
    """Map a Razorpay event to the common shape used by payment_webhooks."""
    payload = event.get("payload", {})
    sub_entity = payload.get("subscription", {}).get("entity", {}) or {}
    payment_entity = payload.get("payment", {}).get("entity", {}) or {}
    event_type = event.get("event", "")

    external_id = sub_entity.get("id") or payment_entity.get("subscription_id")
    notes = sub_entity.get("notes") or payment_entity.get("notes") or {}
    if isinstance(notes, list):  # razorpay legacy quirk
        notes = {}
    user_id = notes.get("user_id")
    customer_email = notes.get("email") or payment_entity.get("email")
    period_end = sub_entity.get("current_end") or sub_entity.get("end_at")

    if event_type in ACTIVATING_EVENTS:
        status = "active"
    elif event_type in FAILING_EVENTS:
        status = "past_due"
    elif event_type in CANCELING_EVENTS:
        status = "canceled"
    else:
        status = None

    return {
        "external_id": external_id,
        "user_id": user_id,
        "customer_email": customer_email,
        "status": status,
        "current_period_end": period_end,
    }
