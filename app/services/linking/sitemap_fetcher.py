"""Sitemap.xml fetcher.

Supports the two common sitemap shapes:
  - `<urlset>`     — a flat list of `<url><loc>…</loc></url>` entries.
  - `<sitemapindex>` — points to one or more child sitemaps; we recurse
                       up to `MAX_SITEMAP_DEPTH` levels deep.

The fetcher is async and accepts an injectable `httpx.AsyncClient` so
tests can stub network calls. The HTTP fetch reuses the same default
user-agent + redirect policy as `content_parser.fetch_url`.
"""

from __future__ import annotations

from typing import Iterable
from xml.etree import ElementTree

import httpx

from app.services.content_parser import DEFAULT_USER_AGENT, URLFetchError

MAX_SITEMAP_DEPTH = 3
DEFAULT_MAX_URLS = 1000
NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


async def fetch_sitemap_urls(
    sitemap_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_urls: int = DEFAULT_MAX_URLS,
    timeout: float = 10.0,
) -> list[str]:
    """Return the list of page URLs declared by a sitemap.

    Raises `URLFetchError` on any HTTP / network / parse failure so the
    API layer can surface the spec's 422 envelope unchanged.
    """
    seen: set[str] = set()
    out: list[str] = []
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    )
    try:
        await _walk(client, sitemap_url, depth=0, seen=seen, out=out, max_urls=max_urls)
    finally:
        if owns_client:
            await client.aclose()
    return out


async def _walk(
    client: httpx.AsyncClient,
    url: str,
    *,
    depth: int,
    seen: set[str],
    out: list[str],
    max_urls: int,
) -> None:
    if depth > MAX_SITEMAP_DEPTH:
        return
    if url in seen:
        return
    seen.add(url)

    body = await _fetch_text(client, url)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as e:
        raise URLFetchError(f"sitemap XML parse error: {e}") from e

    tag = _localname(root.tag)
    if tag == "urlset":
        for loc in _locs(root):
            if loc not in out:
                out.append(loc)
            if len(out) >= max_urls:
                return
    elif tag == "sitemapindex":
        for child_url in _locs(root):
            if len(out) >= max_urls:
                return
            try:
                await _walk(
                    client,
                    child_url,
                    depth=depth + 1,
                    seen=seen,
                    out=out,
                    max_urls=max_urls,
                )
            except URLFetchError:
                # Skip broken children rather than poisoning the whole walk.
                continue
    else:
        raise URLFetchError(
            f"unknown sitemap root element <{tag}>; expected <urlset> or <sitemapindex>"
        )


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text
    except httpx.TimeoutException as e:
        raise URLFetchError(f"sitemap fetch timeout: {url}") from e
    except httpx.HTTPStatusError as e:
        raise URLFetchError(
            f"sitemap HTTP {e.response.status_code} from {url}"
        ) from e
    except httpx.HTTPError as e:
        raise URLFetchError(f"sitemap network error: {type(e).__name__}: {e}") from e


def _locs(root: ElementTree.Element) -> Iterable[str]:
    for loc in root.iter(f"{NAMESPACE}loc"):
        text = (loc.text or "").strip()
        if text:
            yield text


def _localname(tag: str) -> str:
    return tag.split("}", 1)[-1]
