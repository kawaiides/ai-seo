"""Stripe SDK wrapper.

Three responsibilities:
  * Build a Checkout Session URL the front-end can redirect to.
  * Verify webhook signatures from raw body bytes.
  * Map provider events to our internal Subscription state machine.

Vendor SDK types stay inside this module; callers see plain dicts.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import stripe

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckoutResult:
    session_id: str
    url: str


class StripeNotConfigured(RuntimeError):
    """Raised when an env var is missing for the requested operation."""


class StripeSignatureError(Exception):
    """Raised when a webhook signature fails verification."""


def _secret_key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise StripeNotConfigured("STRIPE_SECRET_KEY is not set")
    return key


def _webhook_secret() -> str:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise StripeNotConfigured("STRIPE_WEBHOOK_SECRET is not set")
    return secret


def _price_id(plan: str, cycle: str) -> str:
    """Resolve the Stripe price id from env. Plan = 'pro_usd'; cycle = 'monthly'|'annual'."""
    if cycle not in ("monthly", "annual"):
        raise ValueError(f"unknown cycle {cycle!r}")
    env_var = f"STRIPE_PRICE_{plan.upper()}_{cycle.upper()}"
    # Fallback to the monthly USD price for the common case so a single env
    # var (`STRIPE_PRICE_ID`) works for solo dev setups.
    return os.environ.get(env_var) or os.environ.get("STRIPE_PRICE_ID") or ""


def create_checkout_session(
    *,
    user_id: str,
    plan: str,
    cycle: str,
    success_url: str,
    cancel_url: str,
    customer_email: str | None = None,
) -> CheckoutResult:
    """Create a Stripe Checkout Session for a subscription purchase.

    `client_reference_id` carries our internal `User.id` (UUID string) so
    the webhook handler can join the resulting customer/subscription back
    to our row without needing a parallel mapping table.
    """
    stripe.api_key = _secret_key()
    price = _price_id(plan, cycle)
    if not price:
        raise StripeNotConfigured(
            f"No Stripe price id configured for plan={plan} cycle={cycle}"
        )

    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price, "quantity": 1}],
        "client_reference_id": user_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {"plan": plan, "cycle": cycle, "user_id": user_id},
        "allow_promotion_codes": True,
    }
    if customer_email:
        params["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**params)
    return CheckoutResult(session_id=session["id"], url=session["url"])


def verify_webhook(raw_body: bytes, sig_header: str | None) -> dict[str, Any]:
    """Parse + verify a webhook payload. Returns the Stripe Event dict.

    Raises `StripeSignatureError` on bad signature, expired timestamp, or
    a tampered body. The caller must read the **raw** body before any
    framework body-parser runs (FastAPI consumes the stream once).
    """
    if not sig_header:
        raise StripeSignatureError("missing Stripe-Signature header")
    try:
        event = stripe.Webhook.construct_event(
            payload=raw_body,
            sig_header=sig_header,
            secret=_webhook_secret(),
        )
    except stripe.SignatureVerificationError as e:
        raise StripeSignatureError(f"bad signature: {e}") from e
    except ValueError as e:
        raise StripeSignatureError(f"malformed payload: {e}") from e
    # `event` is a `stripe.Event` (StripeObject); coerce to a plain dict so
    # downstream code never touches vendor types.
    if hasattr(event, "to_dict_recursive"):
        return event.to_dict_recursive()
    if hasattr(event, "to_dict"):
        return event.to_dict()
    return json.loads(raw_body)


# ---- Event → internal state mapping ----

ACTIVATING_EVENTS = {
    "checkout.session.completed",
    "invoice.payment_succeeded",
    "customer.subscription.created",
    "customer.subscription.updated",
}
FAILING_EVENTS = {"invoice.payment_failed"}
CANCELING_EVENTS = {"customer.subscription.deleted"}


def extract_subscription_fields(event: dict[str, Any]) -> dict[str, Any]:
    """Pull the bits we need from any of the handled event types.

    Returns a dict with: external_id, user_id (from client_reference_id
    when present, else None), status, current_period_end (epoch seconds
    when known), customer_email.
    """
    obj = event.get("data", {}).get("object", {})
    event_type = event.get("type", "")

    external_id = obj.get("subscription") or obj.get("id")
    user_id = obj.get("client_reference_id") or obj.get("metadata", {}).get("user_id")
    customer_email = obj.get("customer_email") or obj.get("customer_details", {}).get("email")
    period_end = obj.get("current_period_end") or obj.get("lines", {}).get(
        "data", [{}]
    )[0].get("period", {}).get("end")

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
