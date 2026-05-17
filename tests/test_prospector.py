"""Unit tests for autopilot/prospector.

No live Postgres — uses a fake AsyncSession that records calls. SerpAPI
is mocked with respx so no network or API key is consumed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx
from httpx import Response

from app.autopilot import prospector
from app.autopilot.prospector import SerpAPIError, SerpHit, _domain_of, discover
from app.db.models import Prospect, ProspectStatus


# ---- _domain_of ----


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.example.com/foo/bar", "example.com"),
        ("https://example.com", "example.com"),
        ("http://EXAMPLE.COM/path", "example.com"),
        ("https://sub.example.com/x", "sub.example.com"),
        ("https://www.example.com:8443/x", "example.com:8443"),
    ],
)
def test_domain_of(url: str, expected: str) -> None:
    assert _domain_of(url) == expected


# ---- _fetch_serp ----


@pytest.mark.asyncio
@respx.mock
async def test_fetch_serp_happy_path() -> None:
    body = {
        "organic_results": [
            {"link": "https://a.example/post", "title": "A", "position": 1},
            {"link": "https://b.example/post", "title": "B", "position": 2},
            {"link": "https://c.example/post", "title": "C"},
        ]
    }
    respx.get(prospector.SERPAPI_ENDPOINT).mock(return_value=Response(200, json=body))

    hits = await prospector._fetch_serp("best seo tool", 10, api_key="fake")

    assert [h.url for h in hits] == [
        "https://a.example/post",
        "https://b.example/post",
        "https://c.example/post",
    ]
    assert hits[2].position == 3  # falls back to enumerate index


@pytest.mark.asyncio
@respx.mock
async def test_fetch_serp_skips_entries_without_link() -> None:
    body = {
        "organic_results": [
            {"title": "no link here", "position": 1},
            {"link": "https://b.example/post", "title": "B", "position": 2},
        ]
    }
    respx.get(prospector.SERPAPI_ENDPOINT).mock(return_value=Response(200, json=body))

    hits = await prospector._fetch_serp("x", 10, api_key="fake")
    assert [h.url for h in hits] == ["https://b.example/post"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_serp_error_payload_retries_and_raises(monkeypatch) -> None:
    # Disable tenacity backoff in tests.
    monkeypatch.setattr(prospector, "_fetch_serp",
                        prospector._fetch_serp.retry_with(stop=__import__("tenacity").stop_after_attempt(1)))
    respx.get(prospector.SERPAPI_ENDPOINT).mock(
        return_value=Response(200, json={"error": "Your API key is invalid."})
    )
    with pytest.raises(SerpAPIError, match="SerpAPI error"):
        await prospector._fetch_serp("x", 10, api_key="fake")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_serp_http_500_retries_then_raises(monkeypatch) -> None:
    monkeypatch.setattr(prospector, "_fetch_serp",
                        prospector._fetch_serp.retry_with(stop=__import__("tenacity").stop_after_attempt(1)))
    respx.get(prospector.SERPAPI_ENDPOINT).mock(return_value=Response(500, text="boom"))
    with pytest.raises(SerpAPIError, match="HTTP 500"):
        await prospector._fetch_serp("x", 10, api_key="fake")


# ---- discover() against a FakeSession ----


class _FakeExecute:
    def __init__(self, existing_urls: list[str]):
        self._existing = existing_urls

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._existing)


class FakeSession:
    """Minimal AsyncSession stub: records add_all/flush, returns canned execute."""

    def __init__(self, existing_urls: list[str] | None = None):
        self.existing = existing_urls or []
        self.added: list[Any] = []
        self.flush_called = 0

    async def execute(self, _stmt):
        return _FakeExecute(self.existing)

    def add_all(self, items):
        self.added.extend(items)

    def add(self, item):
        """Funnel-event recorder calls this for individual rows."""
        self.added.append(item)

    async def flush(self):
        self.flush_called += 1
        # Mimic DB-assigned PKs so callers can chain.
        for i, p in enumerate(self.added, start=1):
            if getattr(p, "id", None) is None:
                p.id = i


@pytest.mark.asyncio
async def test_discover_inserts_only_new_urls(monkeypatch) -> None:
    async def _fake_fetch(query, limit, *, api_key):
        return [
            SerpHit(url=f"https://x.example/{i}", title=f"t{i}", position=i)
            for i in range(1, 4)
        ]

    monkeypatch.setattr(prospector, "_fetch_serp", _fake_fetch)
    session = FakeSession(existing_urls=["https://x.example/2"])

    inserted = await discover(session, "best seo tool", limit=3, api_key="fake")

    assert [p.url for p in inserted] == [
        "https://x.example/1",
        "https://x.example/3",
    ]
    assert all(isinstance(p, Prospect) for p in inserted)
    assert all(p.status == ProspectStatus.queued for p in inserted)
    assert all(p.target_keyword == "best seo tool" for p in inserted)
    assert all(p.source == "serpapi" for p in inserted)
    assert all(p.domain == "x.example" for p in inserted)
    # Initial bulk flush plus one flush per funnel_event recorded.
    assert session.flush_called >= 1


@pytest.mark.asyncio
async def test_discover_no_hits_returns_empty(monkeypatch) -> None:
    async def _empty(query, limit, *, api_key):
        return []

    monkeypatch.setattr(prospector, "_fetch_serp", _empty)
    session = FakeSession()
    inserted = await discover(session, "x", limit=10, api_key="fake")
    assert inserted == []
    assert session.flush_called == 0


@pytest.mark.asyncio
async def test_discover_all_duplicates_returns_empty(monkeypatch) -> None:
    async def _two(query, limit, *, api_key):
        return [
            SerpHit(url="https://a.example/1", title="A", position=1),
            SerpHit(url="https://b.example/1", title="B", position=2),
        ]

    monkeypatch.setattr(prospector, "_fetch_serp", _two)
    session = FakeSession(existing_urls=["https://a.example/1", "https://b.example/1"])

    inserted = await discover(session, "x", limit=10, api_key="fake")
    assert inserted == []
    assert session.flush_called == 0


@pytest.mark.asyncio
async def test_discover_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    session = FakeSession()
    with pytest.raises(RuntimeError, match="SERPAPI_KEY"):
        await discover(session, "x")
