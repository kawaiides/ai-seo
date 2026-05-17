"""In-memory page index for internal-linking suggestions.

Each `PageRecord` carries the URL, an optional title, and a body excerpt
trimmed to a configurable character cap. The `PageIndex` embeds the
title + excerpt pair into a normalised sentence-transformer vector;
top-K retrieval is a single dense matmul against a query vector.

Two builders are exposed:

  - `build_page_index_from_records(records)`  — synchronous, no network.
    Used by tests and by callers that already have content in hand.
  - `build_page_index_from_urls(urls, …)`     — async; concurrently
    fetches each URL through `content_parser.fetch_url` and parses out
    title + body. Failures on individual pages are recorded in
    `PageIndex.failed_urls` instead of poisoning the whole batch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from app.services.content_parser import URLFetchError, fetch_url, parse
from app.services.embeddings import get_embedder

EXCERPT_CHAR_CAP = 2000
DEFAULT_FETCH_CONCURRENCY = 8
DEFAULT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class PageRecord:
    url: str
    title: str | None
    excerpt: str

    @property
    def embedding_text(self) -> str:
        """The text we feed the embedder.

        Title first, then excerpt — answer engines weight titles heavily,
        and so do MiniLM-style sentence embeddings on short docs.
        """
        title = (self.title or "").strip()
        excerpt = self.excerpt.strip()
        if title and excerpt:
            return f"{title}\n\n{excerpt}"
        return title or excerpt


@dataclass
class PageIndex:
    pages: list[PageRecord]
    vectors: np.ndarray  # shape (N, dim), L2-normalised
    failed_urls: list[tuple[str, str]] = field(default_factory=list)

    def top_k(self, query_vec: np.ndarray, k: int = 3) -> list[tuple[PageRecord, float]]:
        """Return `(record, similarity)` pairs sorted descending."""
        if len(self.pages) == 0:
            return []
        sims = self.vectors @ query_vec
        # `argsort` ascending → reverse for descending; cap to k.
        order = np.argsort(-sims)[: max(k, 0)]
        return [(self.pages[i], float(sims[i])) for i in order]


def build_page_index_from_records(records: Iterable[PageRecord]) -> PageIndex:
    pages = [r for r in records if (r.embedding_text or "").strip()]
    if not pages:
        return PageIndex(pages=[], vectors=np.empty((0, 1), dtype=np.float32))
    embedder = get_embedder()
    vectors = embedder.encode(
        [p.embedding_text for p in pages],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    return PageIndex(pages=pages, vectors=vectors)


async def build_page_index_from_urls(
    urls: list[str],
    *,
    concurrency: int = DEFAULT_FETCH_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    excerpt_char_cap: int = EXCERPT_CHAR_CAP,
) -> PageIndex:
    """Fetch + parse each URL concurrently, then build the index.

    Individual URL failures are logged onto `PageIndex.failed_urls` —
    a sitemap of 200 pages with two broken links must still produce a
    usable index for the other 198.
    """
    if not urls:
        return PageIndex(pages=[], vectors=np.empty((0, 1), dtype=np.float32))

    semaphore = asyncio.Semaphore(max(concurrency, 1))
    results: list[PageRecord | tuple[str, str]] = []

    async def fetch_one(url: str) -> None:
        async with semaphore:
            try:
                raw = await fetch_url(url, timeout=timeout)
            except URLFetchError as e:
                results.append((url, e.detail))
                return
        try:
            parsed = parse(raw, input_type="url")
        except Exception as e:  # noqa: BLE001 — narrow parser errors don't need to fail the batch
            results.append((url, f"parse error: {type(e).__name__}"))
            return
        title = _extract_title(parsed.soup)
        excerpt = (parsed.body_text or "")[:excerpt_char_cap]
        if not (title or excerpt.strip()):
            results.append((url, "no usable content"))
            return
        results.append(
            PageRecord(url=url, title=title, excerpt=excerpt)
        )

    await asyncio.gather(*(fetch_one(u) for u in urls))

    records: list[PageRecord] = [r for r in results if isinstance(r, PageRecord)]
    failed: list[tuple[str, str]] = [r for r in results if not isinstance(r, PageRecord)]
    index = build_page_index_from_records(records)
    index.failed_urls = failed
    return index


def _extract_title(soup: object | None) -> str | None:
    if soup is None:
        return None
    # Prefer the first <h1>, fall back to <title>.
    h1 = soup.find("h1")  # type: ignore[attr-defined]
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text
    title_tag = soup.find("title")  # type: ignore[attr-defined]
    if title_tag is not None:
        text = title_tag.get_text(strip=True)
        if text:
            return text
    return None
