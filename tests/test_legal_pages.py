"""Sanity tests for the legal-page routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.mark.parametrize("path,marker", [
    ("/terms", "Terms of Service"),
    ("/privacy", "Privacy Policy"),
    ("/refund", "Refund Policy"),
])
def test_legal_page_renders(client, path, marker):
    r = client.get(path)
    assert r.status_code == 200
    assert marker in r.text


def test_legal_pages_cross_link_each_other(client):
    # Every legal page links to all three so a reviewer landing on any of
    # them can verify the trio without needing the footer.
    for path in ("/terms", "/privacy", "/refund"):
        html = client.get(path).text
        for link in ("/terms", "/privacy", "/refund"):
            assert link in html


def test_legal_pages_noindex_signal_absent(client):
    # Public legal copy should be indexable so SEO surfaces the policy.
    r = client.get("/terms")
    assert "noindex" not in r.text.split("</head>", 1)[0]


def test_homepage_footer_links_legal(client):
    r = client.get("/")
    html = r.text
    for link in ('href="/terms"', 'href="/privacy"', 'href="/refund"'):
        assert link in html
