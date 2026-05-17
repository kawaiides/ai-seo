"""Unit tests for payments/stripe_client."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from app.payments import stripe_client
from app.payments.stripe_client import (
    StripeNotConfigured,
    StripeSignatureError,
    create_checkout_session,
    extract_subscription_fields,
    verify_webhook,
)


# ----- _price_id / config errors -----


def test_secret_key_missing_raises(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(StripeNotConfigured, match="STRIPE_SECRET_KEY"):
        stripe_client._secret_key()


def test_webhook_secret_missing_raises(monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    with pytest.raises(StripeNotConfigured, match="STRIPE_WEBHOOK_SECRET"):
        stripe_client._webhook_secret()


def test_price_id_uses_specific_env(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_PRO_USD_MONTHLY", "price_specific")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_fallback")
    assert stripe_client._price_id("pro_usd", "monthly") == "price_specific"


def test_price_id_falls_back_to_generic(monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_PRO_USD_MONTHLY", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_fallback")
    assert stripe_client._price_id("pro_usd", "monthly") == "price_fallback"


def test_price_id_unknown_cycle_raises():
    with pytest.raises(ValueError, match="cycle"):
        stripe_client._price_id("pro_usd", "weekly")


# ----- create_checkout_session -----


def test_create_checkout_session_builds_correct_params(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_xyz")

    captured: dict = {}

    class _FakeSession:
        @staticmethod
        def create(**kw):
            captured.update(kw)
            return {"id": "cs_test_123", "url": "https://stripe.test/c/cs_test_123"}

    monkeypatch.setattr(stripe_client.stripe.checkout, "Session", _FakeSession)

    result = create_checkout_session(
        user_id="user-uuid-1",
        plan="pro_usd",
        cycle="monthly",
        success_url="https://app/success",
        cancel_url="https://app/cancel",
        customer_email="alice@x.com",
    )

    assert result.session_id == "cs_test_123"
    assert result.url == "https://stripe.test/c/cs_test_123"
    assert captured["mode"] == "subscription"
    assert captured["client_reference_id"] == "user-uuid-1"
    assert captured["line_items"] == [{"price": "price_xyz", "quantity": 1}]
    assert captured["customer_email"] == "alice@x.com"
    assert captured["metadata"]["plan"] == "pro_usd"
    assert captured["metadata"]["user_id"] == "user-uuid-1"


def test_create_checkout_session_without_price_raises(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.delenv("STRIPE_PRICE_PRO_USD_MONTHLY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    with pytest.raises(StripeNotConfigured, match="price id"):
        create_checkout_session(
            user_id="u",
            plan="pro_usd",
            cycle="monthly",
            success_url="x", cancel_url="y",
        )


# ----- verify_webhook signature handling -----


def _sign(body: bytes, secret: str, ts: int | None = None) -> str:
    """Build a valid Stripe-Signature header value for the given body."""
    ts = ts or int(time.time())
    signed = f"{ts}.{body.decode('utf-8')}".encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _event_body(event_id: str = "evt_1", event_type: str = "ping") -> bytes:
    # Stripe SDK requires the top-level "object":"event" marker.
    return json.dumps({
        "id": event_id,
        "object": "event",
        "type": event_type,
        "data": {"object": {}},
    }).encode("utf-8")


def test_verify_webhook_accepts_valid_signature(monkeypatch):
    secret = "whsec_test"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    body = _event_body("evt_1", "ping")
    sig = _sign(body, secret)

    event = verify_webhook(body, sig)
    assert event["id"] == "evt_1"
    assert event["type"] == "ping"


def test_verify_webhook_rejects_tampered_body(monkeypatch):
    secret = "whsec_test"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    body = _event_body()
    sig = _sign(body, secret)
    tampered = body + b" "  # one extra space — MAC won't match
    with pytest.raises(StripeSignatureError):
        verify_webhook(tampered, sig)


def test_verify_webhook_missing_header_raises(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    with pytest.raises(StripeSignatureError, match="missing"):
        verify_webhook(b"{}", None)


# ----- extract_subscription_fields -----


def test_extract_active_event_pulls_user_and_period():
    event = {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_1",
                "subscription": "sub_abc",
                "client_reference_id": "user-uuid",
                "customer_email": "x@y.com",
                "current_period_end": 1_700_000_000,
            }
        },
    }
    out = extract_subscription_fields(event)
    assert out["external_id"] == "sub_abc"
    assert out["user_id"] == "user-uuid"
    assert out["customer_email"] == "x@y.com"
    assert out["status"] == "active"
    assert out["current_period_end"] == 1_700_000_000


def test_extract_failing_event_maps_to_past_due():
    event = {
        "id": "evt_2",
        "type": "invoice.payment_failed",
        "data": {"object": {"subscription": "sub_x", "customer_email": "z@y.com"}},
    }
    assert extract_subscription_fields(event)["status"] == "past_due"


def test_extract_canceling_event_maps_to_canceled():
    event = {
        "id": "evt_3",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_x"}},
    }
    out = extract_subscription_fields(event)
    assert out["status"] == "canceled"
    assert out["external_id"] == "sub_x"


def test_extract_unknown_event_returns_none_status():
    event = {"id": "x", "type": "weird.event", "data": {"object": {"id": "y"}}}
    assert extract_subscription_fields(event)["status"] is None
