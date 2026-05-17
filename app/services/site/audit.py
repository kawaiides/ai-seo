"""Audit every `SitePage` in a `Site` and append history rows.

The runner is a thin orchestrator on top of the existing AEO checks +
content_parser. It:

  1. Pulls each `SitePage` to audit (`limit` cap so we never lock too
     long; the scheduled runner in Phase C.2 will pass a small limit
     and iterate).
  2. Fetches + parses each URL via the existing pipeline.
  3. Runs `default_checks()` and aggregates the score.
  4. Inserts a `SitePageAudit` row + refreshes the page's `last_*`
     rollup columns.

Per-page fetch/parse failures are logged into the result envelope but
do not abort the batch — a 200-page site with two broken URLs must
still produce 198 fresh audits.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aeo import _build_response
from app.db.models import SitePage, SitePageAudit
from app.services.aeo_checks import default_checks
from app.services.content_parser import (
    ContentParseError,
    URLFetchError,
    fetch_url,
    parse,
)


@dataclass(frozen=True)
class PageAuditResult:
    page_id: int
    url: str
    score: int | None
    band: str | None
    error: str | None


@dataclass(frozen=True)
class SiteAuditOutcome:
    audited: list[PageAuditResult]
    failed: list[PageAuditResult]

    @property
    def total(self) -> int:
        return len(self.audited) + len(self.failed)


async def audit_site_pages(
    session: AsyncSession,
    site_id: UUID,
    *,
    limit: int = 50,
    concurrency: int = 4,
    timeout: float = 8.0,
) -> SiteAuditOutcome:
    """Audit up to `limit` pages of `site_id` (oldest `last_audited_at` first)."""
    pages = await _select_pages_to_audit(session, site_id, limit)
    if not pages:
        return SiteAuditOutcome(audited=[], failed=[])

    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def _one(page: SitePage) -> PageAuditResult:
        async with semaphore:
            return await _audit_single_page(session, page, timeout=timeout)

    raw_results = await asyncio.gather(*(_one(p) for p in pages))

    audited: list[PageAuditResult] = []
    failed: list[PageAuditResult] = []
    for r in raw_results:
        (audited if r.error is None else failed).append(r)

    await session.flush()
    return SiteAuditOutcome(audited=audited, failed=failed)


async def _select_pages_to_audit(
    session: AsyncSession, site_id: UUID, limit: int
) -> Sequence[SitePage]:
    stmt = (
        select(SitePage)
        .where(SitePage.site_id == site_id)
        # NULL last_audited_at first (never audited), then oldest first.
        .order_by(SitePage.last_audited_at.asc().nullsfirst())
        .limit(limit)
    )
    return (await session.scalars(stmt)).all()


async def _audit_single_page(
    session: AsyncSession, page: SitePage, *, timeout: float
) -> PageAuditResult:
    try:
        raw = await fetch_url(page.url, timeout=timeout)
        parsed = parse(raw, input_type="url")
    except URLFetchError as e:
        return PageAuditResult(
            page_id=page.id, url=page.url, score=None, band=None, error=e.detail
        )
    except ContentParseError as e:
        return PageAuditResult(
            page_id=page.id, url=page.url, score=None, band=None, error=e.detail
        )

    results = [c.run(parsed) for c in default_checks()]
    envelope = _build_response(results)
    failed_check_ids = [r.check_id for r in results if not r.passed]
    title = _extract_title(parsed.soup)

    now = datetime.now(tz=timezone.utc)
    session.add(
        SitePageAudit(
            site_page_id=page.id,
            aeo_score=envelope.aeo_score,
            band=envelope.band,
            failed_checks=failed_check_ids,
            missing_types=[],  # filled by Phase C.2 fan-out integration
            audited_at=now,
        )
    )
    page.last_audited_at = now
    page.last_score = envelope.aeo_score
    page.last_band = envelope.band
    page.last_failed_checks = failed_check_ids
    page.last_missing_types = []
    if title:
        page.title = title

    return PageAuditResult(
        page_id=page.id,
        url=page.url,
        score=envelope.aeo_score,
        band=envelope.band,
        error=None,
    )


def _extract_title(soup: object | None) -> str | None:
    if soup is None:
        return None
    h1 = soup.find("h1")  # type: ignore[attr-defined]
    if h1:
        text = h1.get_text(strip=True)
        if text:
            return text[:512]
    title_tag = soup.find("title")  # type: ignore[attr-defined]
    if title_tag:
        text = title_tag.get_text(strip=True)
        if text:
            return text[:512]
    return None


def site_domain(root_url: str) -> str:
    parts = urlparse(root_url)
    return parts.netloc.lower()
