"""Unit tests for signed report/session tokens."""

from __future__ import annotations

import time

import pytest

from app.services import tokens
from app.services.tokens import TokenError, decode_report, sign_report


@pytest.fixture(autouse=True)
def _signing_keys(monkeypatch):
    monkeypatch.setenv("REPORT_SIGNING_KEY", "k" * 32)
    monkeypatch.setenv("SESSION_SIGNING_KEY", "s" * 32)


def test_report_roundtrip() -> None:
    tok = sign_report(42, "deadbeef-0000-0000-0000-000000000000")
    payload = decode_report(tok)
    assert payload == {"aid": 42, "jti": "deadbeef-0000-0000-0000-000000000000"}


def test_report_bad_signature_raises_tokenerror() -> None:
    tok = sign_report(1, "x")
    with pytest.raises(TokenError, match="invalid"):
        decode_report(tok + "junk")


def test_report_expired_raises_tokenerror() -> None:
    tok = sign_report(1, "x")
    # itsdangerous compares with second-granularity timestamps; sleep past the bound.
    time.sleep(2.1)
    with pytest.raises(TokenError, match="expired"):
        decode_report(tok, max_age=1)


def test_missing_key_raises_runtimeerror(monkeypatch) -> None:
    monkeypatch.delenv("REPORT_SIGNING_KEY", raising=False)
    with pytest.raises(RuntimeError, match="REPORT_SIGNING_KEY"):
        sign_report(1, "x")


def test_session_roundtrip() -> None:
    tok = tokens.sign_session("00000000-0000-0000-0000-000000000001")
    payload = tokens.decode_session(tok)
    assert payload == {"uid": "00000000-0000-0000-0000-000000000001"}


def test_session_and_report_have_different_salts() -> None:
    """Cross-namespace tokens must not validate."""
    report_tok = sign_report(1, "x")
    with pytest.raises(TokenError):
        tokens.decode_session(report_tok)
