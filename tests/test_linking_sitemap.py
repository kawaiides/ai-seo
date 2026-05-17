"""Unit tests for sitemap fetcher (Phase B.3)."""

from __future__ import annotations

import httpx
import pytest

from app.services.content_parser import URLFetchError
from app.services.linking.sitemap_fetcher import fetch_sitemap_urls


def _urlset_xml(*urls: str) -> str:
    body = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>"
    )


def _sitemapindex_xml(*child_urls: str) -> str:
    body = "\n".join(f"  <sitemap><loc>{u}</loc></sitemap>" for u in child_urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</sitemapindex>"
    )


def _make_client(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in routes:
            return routes[url]
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="")


@pytest.mark.asyncio
async def test_flat_urlset_returns_locs_in_order():
    sitemap_url = "https://example.test/sitemap.xml"
    routes = {
        sitemap_url: httpx.Response(
            200, text=_urlset_xml(
                "https://example.test/a",
                "https://example.test/b",
                "https://example.test/c",
            )
        )
    }
    async with _make_client(routes) as client:
        urls = await fetch_sitemap_urls(sitemap_url, client=client)
    assert urls == [
        "https://example.test/a",
        "https://example.test/b",
        "https://example.test/c",
    ]


@pytest.mark.asyncio
async def test_sitemap_index_recurses_to_children():
    parent = "https://example.test/sitemap.xml"
    child = "https://example.test/posts.xml"
    routes = {
        parent: httpx.Response(200, text=_sitemapindex_xml(child)),
        child: httpx.Response(
            200,
            text=_urlset_xml("https://example.test/posts/1", "https://example.test/posts/2"),
        ),
    }
    async with _make_client(routes) as client:
        urls = await fetch_sitemap_urls(parent, client=client)
    assert urls == [
        "https://example.test/posts/1",
        "https://example.test/posts/2",
    ]


@pytest.mark.asyncio
async def test_max_urls_caps_result():
    sitemap_url = "https://example.test/sitemap.xml"
    routes = {
        sitemap_url: httpx.Response(
            200, text=_urlset_xml(*(f"https://example.test/p{i}" for i in range(10)))
        )
    }
    async with _make_client(routes) as client:
        urls = await fetch_sitemap_urls(sitemap_url, client=client, max_urls=3)
    assert urls == [
        "https://example.test/p0",
        "https://example.test/p1",
        "https://example.test/p2",
    ]


@pytest.mark.asyncio
async def test_broken_child_does_not_kill_walk():
    parent = "https://example.test/sitemap.xml"
    good_child = "https://example.test/good.xml"
    bad_child = "https://example.test/bad.xml"
    routes = {
        parent: httpx.Response(200, text=_sitemapindex_xml(bad_child, good_child)),
        bad_child: httpx.Response(500, text="boom"),
        good_child: httpx.Response(
            200, text=_urlset_xml("https://example.test/x")
        ),
    }
    async with _make_client(routes) as client:
        urls = await fetch_sitemap_urls(parent, client=client)
    assert urls == ["https://example.test/x"]


@pytest.mark.asyncio
async def test_parse_error_raises_url_fetch_error():
    sitemap_url = "https://example.test/sitemap.xml"
    routes = {sitemap_url: httpx.Response(200, text="<not xml>")}
    async with _make_client(routes) as client:
        with pytest.raises(URLFetchError):
            await fetch_sitemap_urls(sitemap_url, client=client)


@pytest.mark.asyncio
async def test_unknown_root_element_raises():
    sitemap_url = "https://example.test/sitemap.xml"
    routes = {
        sitemap_url: httpx.Response(
            200, text='<rss xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
        )
    }
    async with _make_client(routes) as client:
        with pytest.raises(URLFetchError):
            await fetch_sitemap_urls(sitemap_url, client=client)


@pytest.mark.asyncio
async def test_http_error_raises_url_fetch_error():
    sitemap_url = "https://example.test/sitemap.xml"
    routes = {sitemap_url: httpx.Response(404, text="missing")}
    async with _make_client(routes) as client:
        with pytest.raises(URLFetchError):
            await fetch_sitemap_urls(sitemap_url, client=client)


@pytest.mark.asyncio
async def test_seen_set_prevents_cycle():
    parent = "https://example.test/a.xml"
    # parent points to itself in a cycle — must not infinite-loop
    routes = {parent: httpx.Response(200, text=_sitemapindex_xml(parent))}
    async with _make_client(routes) as client:
        urls = await fetch_sitemap_urls(parent, client=client)
    assert urls == []
