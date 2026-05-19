"""Unit tests for the unsubscribe service + route + outbox header wiring."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_session
from app.main import app
from app.services import unsubscribe as svc


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("AEGIS_SECRET_KEY", "k" * 32)
    monkeypatch.setenv("AEGIS_ENV", "test")


def test_make_and_resolve_round_trip():
    tok = svc.make_token(contact_id=42, audit_id=99)
    out = svc.resolve_token(tok)
    assert out == (42, 99)


def test_make_token_no_audit_id_works():
    tok = svc.make_token(contact_id=7)
    out = svc.resolve_token(tok)
    assert out is not None
    assert out[0] == 7
    assert out[1] is None


def test_tampered_token_returns_none():
    tok = svc.make_token(contact_id=42)
    assert svc.resolve_token(tok + "x") is None


def test_unsubscribe_page_with_no_token_renders_explanation():
    with TestClient(app) as c:
        r = c.get("/unsubscribe")
    assert r.status_code == 200
    assert "No token supplied" in r.text


def test_unsubscribe_page_with_invalid_token():
    with TestClient(app) as c:
        r = c.get("/unsubscribe?token=garbage")
    assert r.status_code == 200
    assert "invalid" in r.text.lower()


def test_unsubscribe_page_with_valid_token_renders_confirm():
    tok = svc.make_token(contact_id=123, audit_id=4)
    with TestClient(app) as c:
        r = c.get(f"/unsubscribe?token={tok}")
    assert r.status_code == 200
    assert "Unsubscribe from AEGIS outreach?" in r.text
    assert tok in r.text  # token re-embedded for POST submission


# ---- outbox header wiring ----


def test_build_unsubscribe_headers_includes_url(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://aegis.example")
    from app.autopilot import outbox_mailer

    headers = outbox_mailer._build_unsubscribe_headers(contact_id=10, audit_id=20)
    assert "List-Unsubscribe" in headers
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "https://aegis.example/unsubscribe?token=" in headers["List-Unsubscribe"]


def test_build_unsubscribe_headers_adds_mailto_when_env_set(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("UNSUBSCRIBE_MAILTO", "unsubscribe@aegis.example")
    from app.autopilot import outbox_mailer

    headers = outbox_mailer._build_unsubscribe_headers(contact_id=10, audit_id=20)
    assert "mailto:unsubscribe@aegis.example" in headers["List-Unsubscribe"]


def test_build_message_threads_extra_headers_into_mime():
    from app.autopilot.outbox_mailer import RenderedEmail, _build_message

    rendered = RenderedEmail(subject="s", text="t", html="<p>h</p>")
    msg = _build_message(
        "from@a.com", "to@b.com", rendered,
        extra_headers={"List-Unsubscribe": "<https://x/unsub>"},
    )
    assert msg["List-Unsubscribe"] == "<https://x/unsub>"
