"""Renders the homepage and confirms the BYOK + paywall markup is present.

Pure template smoke — JS behaviour (localStorage, fetch wrapper, dialog
open/close) is not exercised; that would need a real browser. We check
the markers a Playwright spec would target if we ever add one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_homepage_renders_byok_panel(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Panel surface markers.
    assert 'id="byok-panel"' in html
    assert 'id="byok-openai"' in html
    assert 'id="byok-gemini"' in html
    assert 'id="byok-active-badge"' in html
    # Clear-keys affordance.
    assert 'id="byok-clear"' in html


def test_homepage_renders_paywall_modal(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert 'id="paywall-modal"' in html
    assert 'id="paywall-headline"' in html
    assert 'id="paywall-price"' in html
    assert 'id="paywall-upgrade-cta"' in html
    assert 'id="paywall-byok-cta"' in html
    # Modal upgrade CTA points at /pricing.
    assert '"/pricing"' in html


def test_byok_panel_warns_anonymous_users(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Anonymous landing → "Sign up to link your key" banner.
    assert "Sign up" in html
    assert "anonymous sessions can&#39;t validate" in html or \
           "anonymous sessions can't validate" in html


def test_homepage_includes_byok_localstorage_keys(client):
    r = client.get("/")
    html = r.text
    # The JS reads/writes under these specific localStorage keys; the
    # gating dep reads matching headers (X-BYOK-OpenAI-Key / X-BYOK-Gemini-Key).
    assert "aegis_byok_openai" in html
    assert "aegis_byok_gemini" in html
    assert "X-BYOK-OpenAI-Key" in html
    assert "X-BYOK-Gemini-Key" in html


def test_homepage_calls_paywall_on_429(client):
    r = client.get("/")
    html = r.text
    # The fetch handlers must invoke window.openPaywallModal on 429.
    assert "openPaywallModal" in html
    # And the entry point is defined at module scope.
    assert "window.openPaywallModal" in html
