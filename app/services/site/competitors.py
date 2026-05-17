"""Competitor benchmarking (Phase C.2).

Given a `Site` with its `competitor_root_urls` list populated, compute a
side-by-side rollup: mean AEO score, band distribution, top missing
fan-out types, and per-cluster gap vs the primary site.

Two execution modes:

  - `summarize_existing(...)`: fast path. Only reads `Site`/`SitePage`
    rows that already exist; competitor sites must have been ingested +
    audited separately. Returns whatever data is on disk.

  - `ingest_and_summarize(...)`: full path. For each competitor root URL
    not already tracked, creates a stub `Site` (no sitemap, no audits)
    so the dashboard can render "queued" rows. The actual audit runs
    are kicked off by the caller via the standard `/api/site/{id}/audit`
    endpoint — this module never makes outbound LLM calls itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Site, SitePage, SitePageAudit
from app.services.site.dashboard import (
    PageRow,
    compute_missing_clusters,
    compute_summary,
)


@dataclass(frozen=True)
class CompetitorRow:
    root_url: str
    site_id: UUID | None
    pages_total: int
    pages_audited: int
    mean_score: float | None
    median_score: float | None
    band_counts: dict[str, int]
    top_missing_types: list[tuple[str, int]]
    last_audited_at: datetime | None
    status: str  # "tracked" | "queued"


@dataclass(frozen=True)
class CompetitorBenchmark:
    site_id: UUID
    root_url: str
    primary: CompetitorRow
    competitors: list[CompetitorRow]
    delta_mean_score: dict[str, float | None]
    intent_gap: dict[str, list[str]]


async def summarize_existing(
    session: AsyncSession,
    site_id: UUID,
) -> CompetitorBenchmark:
    """Read-only rollup. Competitors not yet ingested show `status='queued'`."""
    site = await session.get(Site, site_id)
    if site is None:
        raise ValueError(f"site {site_id} not found")

    primary_pages = await _page_rows(session, site_id)
    primary = _row_for(
        root_url=site.root_url,
        site_id=site.id,
        pages=primary_pages,
        status="tracked",
    )

    competitors: list[CompetitorRow] = []
    for c_url in site.competitor_root_urls or []:
        canonical = c_url.strip().rstrip("/")
        c_site = await session.scalar(
            select(Site).where(Site.root_url == canonical)
        )
        if c_site is None:
            competitors.append(
                CompetitorRow(
                    root_url=canonical,
                    site_id=None,
                    pages_total=0,
                    pages_audited=0,
                    mean_score=None,
                    median_score=None,
                    band_counts={},
                    top_missing_types=[],
                    last_audited_at=None,
                    status="queued",
                )
            )
            continue
        c_pages = await _page_rows(session, c_site.id)
        competitors.append(
            _row_for(
                root_url=c_site.root_url,
                site_id=c_site.id,
                pages=c_pages,
                status="tracked",
            )
        )

    delta = {
        c.root_url: (
            None
            if (primary.mean_score is None or c.mean_score is None)
            else round(primary.mean_score - c.mean_score, 1)
        )
        for c in competitors
    }

    primary_missing = {t for t, _ in primary.top_missing_types}
    intent_gap = {
        c.root_url: sorted(
            primary_missing - {t for t, _ in c.top_missing_types}
        )
        for c in competitors
    }

    return CompetitorBenchmark(
        site_id=site.id,
        root_url=site.root_url,
        primary=primary,
        competitors=competitors,
        delta_mean_score=delta,
        intent_gap=intent_gap,
    )


async def ingest_and_summarize(
    session: AsyncSession,
    site_id: UUID,
    *,
    page_urls_per_competitor: dict[str, list[str]] | None = None,
) -> CompetitorBenchmark:
    """Create stub `Site` rows for any competitor URL not yet tracked.

    `page_urls_per_competitor` lets the caller pre-supply the list of
    pages to track for each competitor — useful when the caller already
    scraped the competitor's sitemap. Pages are added but not audited
    here; the dashboard shows them as queued until the next audit cron
    run picks them up.
    """
    from app.services.site.ingest import ingest_site

    site = await session.get(Site, site_id)
    if site is None:
        raise ValueError(f"site {site_id} not found")

    for c_url in site.competitor_root_urls or []:
        canonical = c_url.strip().rstrip("/")
        existing = await session.scalar(
            select(Site).where(Site.root_url == canonical)
        )
        if existing is not None:
            continue
        page_urls = (page_urls_per_competitor or {}).get(canonical) or [canonical]
        await ingest_site(
            session,
            root_url=canonical,
            org_id=site.org_id,
            page_urls=page_urls,
        )
    await session.flush()
    return await summarize_existing(session, site_id)


async def _page_rows(session: AsyncSession, site_id: UUID) -> list[PageRow]:
    rows = (
        await session.scalars(select(SitePage).where(SitePage.site_id == site_id))
    ).all()
    return [
        PageRow(
            page_id=p.id,
            url=p.url,
            title=p.title,
            last_score=p.last_score,
            last_band=p.last_band,
            last_audited_at=p.last_audited_at,
            last_missing_types=tuple(p.last_missing_types or ()),
            last_failed_checks=tuple(p.last_failed_checks or ()),
        )
        for p in rows
    ]


def _row_for(
    *,
    root_url: str,
    site_id: UUID,
    pages: Iterable[PageRow],
    status: str,
) -> CompetitorRow:
    pages_list = list(pages)
    summary = compute_summary(pages_list)
    missing = compute_missing_clusters(pages_list, k=5)
    return CompetitorRow(
        root_url=root_url,
        site_id=site_id,
        pages_total=summary.pages_total,
        pages_audited=summary.pages_audited,
        mean_score=summary.mean_score,
        median_score=summary.median_score,
        band_counts=summary.band_counts,
        top_missing_types=[(m.type, m.pages_missing) for m in missing],
        last_audited_at=summary.last_audited_at,
        status=status,
    )


async def recent_audit_window(
    session: AsyncSession, site_id: UUID, days: int = 30
) -> int:
    horizon = datetime.now(tz=timezone.utc) - timedelta(days=days)
    count = await session.scalar(
        select(SitePageAudit.id)
        .join(SitePage, SitePage.id == SitePageAudit.site_page_id)
        .where(SitePage.site_id == site_id, SitePageAudit.audited_at >= horizon)
    )
    return int(count or 0)
