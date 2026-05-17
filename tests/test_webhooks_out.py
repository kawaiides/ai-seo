"""Unit tests for webhooks_out signing + dispatch (Phase E)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import pytest

from app.integrations import notifier  # noqa: F401 — ensures import path is sane
from app.services.webhooks_out import (
    SIGNATURE_HEADER,
    SUPPORTED_EVENTS,
    filter_subscribers_for_event,
    generate_secret,
    sign_payload,
    verify_signature,
)


# -------------------- Signing primitives --------------------


def test_signature_deterministic_for_same_inputs():
    s = sign_payload("secret", b"hello")
    assert s.startswith("sha256=")
    assert sign_payload("secret", b"hello") == s


def test_signature_changes_with_secret():
    a = sign_payload("a", b"hello")
    b = sign_payload("b", b"hello")
    assert a != b


def test_signature_changes_with_body():
    a = sign_payload("secret", b"hello")
    b = sign_payload("secret", b"hello!")
    assert a != b


def test_verify_signature_matches():
    body = b'{"foo":"bar"}'
    sig = sign_payload("topsecret", body)
    assert verify_signature("topsecret", body, sig)


def test_verify_signature_rejects_tampered_body():
    body = b'{"foo":"bar"}'
    sig = sign_payload("topsecret", body)
    assert not verify_signature("topsecret", body + b" ", sig)


def test_verify_signature_rejects_wrong_secret():
    body = b"x"
    sig = sign_payload("a", body)
    assert not verify_signature("b", body, sig)


def test_verify_signature_handles_missing_header():
    assert not verify_signature("a", b"x", "")
    assert not verify_signature("a", b"x", None)  # type: ignore[arg-type]


def test_generate_secret_is_non_trivial_and_unique():
    s = generate_secret()
    assert len(s) >= 32
    assert s != generate_secret()


# -------------------- Filtering --------------------


@dataclass
class _W:
    enabled: bool
    events: list[str]


def test_filter_subscribers_skips_disabled():
    subs = [
        _W(enabled=False, events=["audit.completed"]),
        _W(enabled=True, events=["audit.completed"]),
    ]
    out = filter_subscribers_for_event(subs, "audit.completed")
    assert len(out) == 1
    assert out[0].enabled


def test_filter_subscribers_matches_event_list():
    subs = [
        _W(enabled=True, events=["audit.completed"]),
        _W(enabled=True, events=["score.dropped"]),
    ]
    out = filter_subscribers_for_event(subs, "score.dropped")
    assert [s.events for s in out] == [["score.dropped"]]


def test_supported_events_documented():
    # If a new event name lands, this assertion forces the dev to think
    # about whether the change is intentional (and to update the docs).
    expected = {
        "audit.completed",
        "audit.failed",
        "score.dropped",
        "geo.probe.completed",
        "site.ingested",
    }
    assert set(SUPPORTED_EVENTS) == expected
