"""Site ingest: root URL + sitemap → Site / SitePage rows.

Idempotent: ingesting the same root URL twice updates the existing
`Site` rather than producing duplicates; URLs already present in
`SitePage` are left alone so we don't reset their audit history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Site, SitePage
from app.services.linking.sitemap_fetcher import fetch_sitemap_urls

DEFAULT_PAGE_CAP = 200


@dataclass(frozen=True)
class IngestResult:
    site_id: UUID
    root_url: str
    pages_total: int
    pages_added: int
    pages_existing: int
    org_id: UUID | None = None


async def ingest_site(
    session: AsyncSession,
    *,
    root_url: str,
    user_id: UUID | None = None,
    org_id: UUID | None = None,
    sitemap_url: str | None = None,
    page_urls: Iterable[str] | None = None,
    competitor_root_urls: list[str] | None = None,
    max_pages: int = DEFAULT_PAGE_CAP,
) -> IngestResult:
    """Persist or update a `Site` row + child `SitePage` rows.

    Exactly one of `sitemap_url` (we'll fetch it) or `page_urls` (caller
    pre-supplies the URL list — used by tests) must be provided. Phase D
    callers should pass `org_id`; pre-Phase-D callers can stick with
    `user_id`. Sites may carry both (org_id wins for authorisation).
    """
    if not (sitemap_url or page_urls):
        raise ValueError("either sitemap_url or page_urls must be provided")
    if sitemap_url and page_urls:
        raise ValueError("sitemap_url and page_urls are mutually exclusive")

    root_url = _canonical_root(root_url)

    site = await _upsert_site(
        session,
        user_id=user_id,
        org_id=org_id,
        root_url=root_url,
        sitemap_url=sitemap_url,
        competitor_root_urls=competitor_root_urls,
    )

    if page_urls is None:
        page_urls = await fetch_sitemap_urls(sitemap_url, max_urls=max_pages)
    else:
        page_urls = list(page_urls)[:max_pages]

    distinct_urls = _dedupe_preserving_order(page_urls)

    existing_q = await session.execute(
        select(SitePage.url).where(SitePage.site_id == site.id)
    )
    existing_urls = {row[0] for row in existing_q.all()}

    added = 0
    for url in distinct_urls:
        if url in existing_urls:
            continue
        session.add(SitePage(site_id=site.id, url=url))
        added += 1

    await session.flush()
    return IngestResult(
        site_id=site.id,
        root_url=site.root_url,
        pages_total=len(distinct_urls),
        pages_added=added,
        pages_existing=len(distinct_urls) - added,
        org_id=site.org_id,
    )


async def _upsert_site(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    org_id: UUID | None,
    root_url: str,
    sitemap_url: str | None,
    competitor_root_urls: list[str] | None,
) -> Site:
    """Find-or-create with org-first lookup.

    Lookup precedence:
      1. (org_id, root_url) — Phase D and onwards.
      2. (user_id, root_url) with org_id IS NULL — legacy rows.
      3. None of the above → create fresh row, stamping whichever owner
         IDs the caller supplied.
    """
    stmt = select(Site).where(Site.root_url == root_url)
    if org_id is not None:
        stmt = stmt.where(Site.org_id == org_id)
    elif user_id is not None:
        stmt = stmt.where(Site.user_id == user_id, Site.org_id.is_(None))
    else:
        stmt = stmt.where(Site.user_id.is_(None), Site.org_id.is_(None))
    existing = await session.scalar(stmt)
    if existing is not None:
        if sitemap_url and existing.sitemap_url != sitemap_url:
            existing.sitemap_url = sitemap_url
        if competitor_root_urls is not None:
            existing.competitor_root_urls = competitor_root_urls
        # Backfill org_id on a legacy user-owned row when the caller
        # supplies one. Never overwrite an existing org owner.
        if org_id is not None and existing.org_id is None:
            existing.org_id = org_id
        return existing

    site = Site(
        user_id=user_id,
        org_id=org_id,
        root_url=root_url,
        sitemap_url=sitemap_url,
        competitor_root_urls=competitor_root_urls,
    )
    session.add(site)
    await session.flush()
    return site


def _canonical_root(url: str) -> str:
    parts = urlparse(url.strip())
    if not parts.scheme:
        raise ValueError(f"root_url must include a scheme: {url!r}")
    return urlunparse(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "",
            "",
            "",
            "",
        )
    )


def _dedupe_preserving_order(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out
