"""Unit tests for payments/razorpay_client."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.payments import razorpay_client
from app.payments.razorpay_client import (
    RazorpayNotConfigured,
    RazorpaySignatureError,
    create_subscription,
    extract_subscription_fields,
    verify_webhook,
)


# ----- env / config -----


def test_key_id_missing_raises(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    with pytest.raises(RazorpayNotConfigured, match="RAZORPAY_KEY_ID"):
        razorpay_client._key_id()


def test_webhook_secret_missing_raises(monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    with pytest.raises(RazorpayNotConfigured, match="RAZORPAY_WEBHOOK_SECRET"):
        razorpay_client._webhook_secret()


def test_plan_id_prefers_upi(monkeypatch):
    monkeypatch.setenv("RAZORPAY_PLAN_ID_UPI", "plan_upi_x")
    monkeypatch.setenv("RAZORPAY_PLAN_ID", "plan_generic")
    assert razorpay_client._plan_id() == "plan_upi_x"


def test_plan_id_falls_back_to_generic(monkeypatch):
    monkeypatch.delenv("RAZORPAY_PLAN_ID_UPI", raising=False)
    monkeypatch.setenv("RAZORPAY_PLAN_ID", "plan_generic")
    assert razorpay_client._plan_id() == "plan_generic"


def test_plan_id_missing_raises(monkeypatch):
    monkeypatch.delenv("RAZORPAY_PLAN_ID_UPI", raising=False)
    monkeypatch.delenv("RAZORPAY_PLAN_ID", raising=False)
    with pytest.raises(RazorpayNotConfigured):
        razorpay_client._plan_id()


# ----- create_subscription -----


def test_create_subscription_builds_correct_payload(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_key")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "rzp_secret")
    monkeypatch.setenv("RAZORPAY_PLAN_ID_UPI", "plan_upi")

    captured: dict = {}

    class _FakeSubResource:
        @staticmethod
        def create(data):
            captured.update(data)
            return {"id": "sub_test_1", "short_url": "https://rzp.test/sub_test_1"}

    class _FakeClient:
        subscription = _FakeSubResource

    monkeypatch.setattr(razorpay_client, "_client", lambda: _FakeClient)

    result = create_subscription(user_id="user-uuid-1", customer_email="a@x.com")

    assert result.subscription_id == "sub_test_1"
    assert result.short_url == "https://rzp.test/sub_test_1"
    assert captured["plan_id"] == "plan_upi"
    assert captured["customer_notify"] == 1
    assert captured["total_count"] == 120
    assert captured["notes"]["user_id"] == "user-uuid-1"
    assert captured["notes"]["email"] == "a@x.com"


# ----- verify_webhook -----


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_webhook_accepts_valid_signature(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "wh_secret")
    body = json.dumps({"event": "subscription.activated", "id": "ev_1"}).encode("utf-8")
    sig = _sign(body, "wh_secret")
    out = verify_webhook(body, sig)
    assert out["event"] == "subscription.activated"
    assert out["id"] == "ev_1"


def test_verify_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "wh_secret")
    body = b'{"event":"x"}'
    with pytest.raises(RazorpaySignatureError, match="bad signature"):
        verify_webhook(body, "deadbeef")


def test_verify_webhook_rejects_tampered_body(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "wh_secret")
    body = b'{"event":"x"}'
    sig = _sign(body, "wh_secret")
    with pytest.raises(RazorpaySignatureError):
        verify_webhook(body + b" ", sig)


def test_verify_webhook_missing_header_raises(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "wh_secret")
    with pytest.raises(RazorpaySignatureError, match="missing"):
        verify_webhook(b"{}", None)


def test_verify_webhook_uses_constant_time_compare(monkeypatch):
    """If we ever swap to plain `==`, this still passes — but the property
    we actually want is regression-tested by the call site using compare_digest."""
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "wh_secret")
    body = b'{"event":"x"}'
    sig = _sign(body, "wh_secret")
    # Right length, wrong content: must reject.
    bad = "f" * len(sig)
    with pytest.raises(RazorpaySignatureError):
        verify_webhook(body, bad)


# ----- extract_subscription_fields -----


def _activated_event(user_id: str = "user-1", sub_id: str = "sub_x") -> dict:
    return {
        "event": "subscription.activated",
        "payload": {
            "subscription": {
                "entity": {
                    "id": sub_id,
                    "current_end": 1_800_000_000,
                    "notes": {"user_id": user_id, "email": "a@x.com"},
                }
            }
        },
    }


def test_extract_activated_maps_to_active():
    out = extract_subscription_fields(_activated_event())
    assert out["external_id"] == "sub_x"
    assert out["status"] == "active"
    assert out["user_id"] == "user-1"
    assert out["customer_email"] == "a@x.com"
    assert out["current_period_end"] == 1_800_000_000


def test_extract_halted_maps_to_past_due():
    e = _activated_event()
    e["event"] = "subscription.halted"
    assert extract_subscription_fields(e)["status"] == "past_due"


def test_extract_cancelled_maps_to_canceled():
    e = _activated_event()
    e["event"] = "subscription.cancelled"
    assert extract_subscription_fields(e)["status"] == "canceled"


def test_extract_handles_payment_event_shape():
    e = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "subscription_id": "sub_p",
                    "email": "b@x.com",
                    "notes": {"user_id": "u-2"},
                }
            }
        },
    }
    out = extract_subscription_fields(e)
    assert out["external_id"] == "sub_p"
    assert out["status"] == "past_due"
    assert out["user_id"] == "u-2"
    assert out["customer_email"] == "b@x.com"


def test_extract_unknown_event_returns_none_status():
    e = _activated_event()
    e["event"] = "weird.unknown"
    assert extract_subscription_fields(e)["status"] is None
