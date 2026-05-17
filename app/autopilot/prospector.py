"""Prospector — fetch SERP results and queue them as `Prospect` rows.

One SerpAPI call per seed keyword. Dedup against existing prospect URLs
so re-runs don't double-queue the same page.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.db.models import FunnelStage, Prospect, ProspectStatus
from app.services.funnel import record as record_funnel

log = logging.getLogger(__name__)

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_LIMIT = 10
HTTP_TIMEOUT = 15.0


class SerpAPIError(RuntimeError):
    """Raised when SerpAPI returns a non-2xx or a malformed payload."""


@dataclass(frozen=True)
class SerpHit:
    url: str
    title: str | None
    position: int


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1.0, max=8.0),
    retry=retry_if_exception_type((httpx.HTTPError, SerpAPIError)),
    reraise=True,
)
async def _fetch_serp(query: str, limit: int, *, api_key: str) -> list[SerpHit]:
    params = {
        "engine": "google",
        "q": query,
        "num": limit,
        "api_key": api_key,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(SERPAPI_ENDPOINT, params=params)
    if resp.status_code != 200:
        raise SerpAPIError(f"SerpAPI HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if "error" in data:
        raise SerpAPIError(f"SerpAPI error: {data['error']}")
    organic = data.get("organic_results") or []
    return [
        SerpHit(
            url=item["link"],
            title=item.get("title"),
            position=int(item.get("position") or i + 1),
        )
        for i, item in enumerate(organic)
        if item.get("link")
    ]


def _domain_of(url: str) -> str:
    netloc = urlsplit(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


async def discover(
    session: AsyncSession,
    seed: str,
    *,
    limit: int = DEFAULT_LIMIT,
    api_key: str | None = None,
) -> list[Prospect]:
    """Run one SerpAPI search, insert new prospects, return all inserted rows.

    Dedup: skip any URL already present in `prospect`. Returns only the
    rows we actually inserted, so callers can chain straight into the
    audit runner without re-querying.
    """
    api_key = api_key or os.environ.get("SERPAPI_KEY")
    if not api_key:
        raise RuntimeError(
            "SERPAPI_KEY is not set. Add it to .env or pass api_key= explicitly."
        )

    hits = await _fetch_serp(seed, limit, api_key=api_key)
    if not hits:
        log.info("prospector: zero SERP hits for seed %r", seed)
        return []

    urls = [h.url for h in hits]
    existing_urls = set(
        (
            await session.execute(
                select(Prospect.url).where(Prospect.url.in_(urls))
            )
        ).scalars()
    )

    fresh = [
        Prospect(
            url=h.url,
            domain=_domain_of(h.url),
            target_keyword=seed,
            source="serpapi",
            status=ProspectStatus.queued,
        )
        for h in hits
        if h.url not in existing_urls
    ]
    if not fresh:
        log.info("prospector: all %d SerpAPI URLs already queued for seed %r",
                 len(hits), seed)
        return []

    session.add_all(fresh)
    await session.flush()  # populate PKs without committing
    for p in fresh:
        await record_funnel(
            session,
            FunnelStage.discovered,
            prospect_id=p.id,
            meta={"seed": seed, "domain": p.domain},
        )
    log.info("prospector: inserted %d new prospects for seed %r", len(fresh), seed)
    return fresh
