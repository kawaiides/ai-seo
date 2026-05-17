"""Unit tests for `app.autopilot.contact_finders.hunter.HunterContactFinder`."""

from __future__ import annotations

import httpx
import pytest

from app.autopilot.contact_finders.hunter import (
    HUNTER_DOMAIN_SEARCH_URL,
    HUNTER_EMAIL_FINDER_URL,
    HunterContactFinder,
)
from app.db.models import Prospect


def _prospect(meta: dict | None = None) -> Prospect:
    p = Prospect(url="https://acme.test/blog/foo", domain="acme.test", target_keyword="x")
    p.id = 1
    if meta is not None:
        p.meta = meta
    return p


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)


@pytest.mark.asyncio
async def test_disabled_when_no_key():
    n = HunterContactFinder(api_key=None)
    assert n.enabled is False
    assert await n.find(_prospect()) == []


@pytest.mark.asyncio
async def test_domain_search_returns_senior_email():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={
            "data": {
                "emails": [
                    {
                        "value": "founder@acme.test",
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "position": "Founder",
                        "confidence": 95,
                    },
                ],
            },
        })

    async with _mock_client(handler) as client:
        n = HunterContactFinder(api_key="hk_test", client=client)
        out = await n.find(_prospect())
    assert len(out) == 1
    assert out[0].email == "founder@acme.test"
    assert out[0].name == "Ada Lovelace"
    assert out[0].role == "Founder"
    assert out[0].source == "hunter"
    assert out[0].verified is True
    # Confirms the URL targeted is domain-search.
    assert captured[0].url.path == httpx.URL(HUNTER_DOMAIN_SEARCH_URL).path
    assert captured[0].url.params["domain"] == "acme.test"
    assert "senior" in captured[0].url.params["seniority"]


@pytest.mark.asyncio
async def test_low_confidence_is_dropped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "data": {
                "emails": [
                    {"value": "guess@acme.test", "confidence": 30},
                ],
            },
        })

    async with _mock_client(handler) as client:
        n = HunterContactFinder(api_key="hk_test", client=client)
        out = await n.find(_prospect())
    assert out == []


@pytest.mark.asyncio
async def test_404_returns_empty_silently():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"details": "not indexed"}]})

    async with _mock_client(handler) as client:
        n = HunterContactFinder(api_key="hk_test", client=client)
        assert await n.find(_prospect()) == []


@pytest.mark.asyncio
async def test_email_finder_fallback_when_domain_empty():
    """When domain-search is empty BUT the prospect carries an author
    byline, hit /v2/email-finder with the name."""

    seq: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seq.append(request.url.path)
        if request.url.path == httpx.URL(HUNTER_DOMAIN_SEARCH_URL).path:
            return httpx.Response(200, json={"data": {"emails": []}})
        return httpx.Response(200, json={
            "data": {
                "email": "ada@acme.test",
                "score": 92,
                "position": "Author",
            },
        })

    async with _mock_client(handler) as client:
        n = HunterContactFinder(api_key="hk_test", client=client)
        out = await n.find(_prospect(meta={"author_first": "Ada", "author_last": "Lovelace"}))

    assert len(out) == 1
    assert out[0].email == "ada@acme.test"
    assert out[0].verified is True
    assert seq == [
        httpx.URL(HUNTER_DOMAIN_SEARCH_URL).path,
        httpx.URL(HUNTER_EMAIL_FINDER_URL).path,
    ]


@pytest.mark.asyncio
async def test_no_fallback_without_author():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == httpx.URL(HUNTER_EMAIL_FINDER_URL).path:
            raise AssertionError("email-finder should not be called without author")
        return httpx.Response(200, json={"data": {"emails": []}})

    async with _mock_client(handler) as client:
        n = HunterContactFinder(api_key="hk_test", client=client)
        assert await n.find(_prospect()) == []


@pytest.mark.asyncio
async def test_network_error_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    async with _mock_client(handler) as client:
        n = HunterContactFinder(api_key="hk_test", client=client)
        assert await n.find(_prospect()) == []


@pytest.mark.asyncio
async def test_strips_www_from_domain():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.params["domain"])
        return httpx.Response(200, json={"data": {"emails": []}})

    p = Prospect(url="https://www.acme.test/", domain="acme.test", target_keyword="x")
    p.id = 1
    async with _mock_client(handler) as client:
        n = HunterContactFinder(api_key="hk_test", client=client)
        await n.find(p)
    assert captured[0] == "acme.test"
