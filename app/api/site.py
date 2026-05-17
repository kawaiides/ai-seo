"""Site ingest + dashboard endpoints (Phase C.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import OrgRole, Site, SitePage, SitePageAudit, User
from app.services.auth import get_current_user
from app.services.orgs import enforce_site_role
from app.models.schemas import (
    CompetitorBenchmarkResponse,
    CompetitorIngestRequest,
    CompetitorRowResponse,
    SiteAuditPageResult,
    SiteAuditRequest,
    SiteAuditResponse,
    SiteDashboardMissingCluster,
    SiteDashboardResponse,
    SiteDashboardTrendPoint,
    SiteDashboardWorstPage,
    SiteIngestRequest,
    SiteIngestResponse,
)
from app.services.site.audit import audit_site_pages
from app.services.site.competitors import (
    ingest_and_summarize,
    summarize_existing,
)
from app.services.site.dashboard import (
    AuditRow,
    PageRow,
    compute_missing_clusters,
    compute_summary,
    compute_top_worst,
    compute_trend,
)
from app.services.site.ingest import ingest_site

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.post("/ingest", response_model=SiteIngestResponse)
async def ingest(
    req: SiteIngestRequest,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user),
) -> SiteIngestResponse:
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
    target_org_id = req.org_id
    if target_org_id is not None:
        # Caller must prove editor+ membership on the org they want this
        # Site attached to. Previously this trusted whatever org_id came
        # in on the request body — a cross-tenant write primitive.
        await enforce_site_role(
            session,
            site_org_id=target_org_id,
            user=user,
            min_role=OrgRole.editor,
        )
    result = await ingest_site(
        session,
        root_url=req.root_url,
        user_id=user.id if target_org_id is None else None,
        org_id=target_org_id,
        sitemap_url=req.sitemap_url,
        page_urls=req.page_urls,
        competitor_root_urls=req.competitor_root_urls,
        max_pages=req.max_pages,
    )
    return SiteIngestResponse(
        site_id=result.site_id,
        root_url=result.root_url,
        pages_total=result.pages_total,
        pages_added=result.pages_added,
        pages_existing=result.pages_existing,
        org_id=result.org_id,
    )


@router.post("/{site_id}/audit", response_model=SiteAuditResponse)
async def audit(
    site_id: UUID,
    req: SiteAuditRequest,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user),
) -> SiteAuditResponse:
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    await enforce_site_role(
        session,
        site_org_id=site.org_id,
        site_user_id=site.user_id,
        user=user,
        min_role=OrgRole.editor,
    )
    outcome = await audit_site_pages(
        session, site_id, limit=req.limit, concurrency=req.concurrency
    )
    return SiteAuditResponse(
        site_id=site_id,
        audited=[SiteAuditPageResult(**r.__dict__) for r in outcome.audited],
        failed=[SiteAuditPageResult(**r.__dict__) for r in outcome.failed],
    )


@router.get("/{site_id}/dashboard", response_model=SiteDashboardResponse)
async def dashboard(
    site_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user),
) -> SiteDashboardResponse:
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    await enforce_site_role(
        session,
        site_org_id=site.org_id,
        site_user_id=site.user_id,
        user=user,
        min_role=OrgRole.viewer,
    )

    page_rows = await _fetch_page_rows(session, site_id)
    audit_rows = await _fetch_recent_audits(session, site_id, days=7)

    summary = compute_summary(page_rows)
    trend = compute_trend(audit_rows, days=7)
    worst = compute_top_worst(page_rows, k=10)
    missing = compute_missing_clusters(page_rows, k=10)

    return SiteDashboardResponse(
        site_id=site_id,
        root_url=site.root_url,
        pages_total=summary.pages_total,
        pages_audited=summary.pages_audited,
        mean_score=summary.mean_score,
        median_score=summary.median_score,
        band_counts=summary.band_counts,
        last_audited_at=summary.last_audited_at.isoformat() if summary.last_audited_at else None,
        trend_7d=[
            SiteDashboardTrendPoint(
                day=t.day.isoformat(), mean_score=t.mean_score, audits=t.audits
            )
            for t in trend
        ],
        worst_pages=[
            SiteDashboardWorstPage(
                page_id=w.page_id,
                url=w.url,
                title=w.title,
                score=w.score,
                band=w.band,
                failed_checks=list(w.failed_checks),
            )
            for w in worst
        ],
        missing_clusters=[
            SiteDashboardMissingCluster(type=c.type, pages_missing=c.pages_missing)
            for c in missing
        ],
    )


def _row_to_response(row) -> CompetitorRowResponse:
    return CompetitorRowResponse(
        root_url=row.root_url,
        site_id=row.site_id,
        pages_total=row.pages_total,
        pages_audited=row.pages_audited,
        mean_score=row.mean_score,
        median_score=row.median_score,
        band_counts=row.band_counts,
        top_missing_types=row.top_missing_types,
        last_audited_at=(
            row.last_audited_at.isoformat() if row.last_audited_at else None
        ),
        status=row.status,
    )


def _benchmark_to_response(b) -> CompetitorBenchmarkResponse:
    return CompetitorBenchmarkResponse(
        site_id=b.site_id,
        root_url=b.root_url,
        primary=_row_to_response(b.primary),
        competitors=[_row_to_response(c) for c in b.competitors],
        delta_mean_score=b.delta_mean_score,
        intent_gap=b.intent_gap,
    )


@router.get("/{site_id}/competitors", response_model=CompetitorBenchmarkResponse)
async def competitors_summary(
    site_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user),
) -> CompetitorBenchmarkResponse:
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    await enforce_site_role(
        session,
        site_org_id=site.org_id,
        site_user_id=site.user_id,
        user=user,
        min_role=OrgRole.viewer,
    )
    benchmark = await summarize_existing(session, site_id)
    return _benchmark_to_response(benchmark)


@router.post("/{site_id}/competitors/ingest", response_model=CompetitorBenchmarkResponse)
async def competitors_ingest(
    site_id: UUID,
    req: CompetitorIngestRequest,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user),
) -> CompetitorBenchmarkResponse:
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    await enforce_site_role(
        session,
        site_org_id=site.org_id,
        site_user_id=site.user_id,
        user=user,
        min_role=OrgRole.editor,
    )
    benchmark = await ingest_and_summarize(
        session,
        site_id,
        page_urls_per_competitor=req.page_urls_per_competitor,
    )
    return _benchmark_to_response(benchmark)


@router.get("/dashboard/{site_id}", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_html(
    site_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_current_user),
) -> HTMLResponse:
    data = await dashboard(site_id, session, user)
    return templates.TemplateResponse(
        request, "dashboard/site.html", {"data": data.model_dump()}
    )


async def _fetch_page_rows(
    session: AsyncSession, site_id: UUID
) -> list[PageRow]:
    stmt = select(SitePage).where(SitePage.site_id == site_id)
    pages = (await session.scalars(stmt)).all()
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
        for p in pages
    ]


async def _fetch_recent_audits(
    session: AsyncSession, site_id: UUID, *, days: int = 7
) -> list[AuditRow]:
    horizon = datetime.now(tz=timezone.utc) - timedelta(days=days)
    stmt = (
        select(SitePageAudit, SitePage.id)
        .join(SitePage, SitePage.id == SitePageAudit.site_page_id)
        .where(SitePage.site_id == site_id, SitePageAudit.audited_at >= horizon)
        .order_by(SitePageAudit.audited_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        AuditRow(
            page_id=page_id,
            score=audit.aeo_score,
            audited_at=audit.audited_at,
            missing_types=tuple(audit.missing_types or ()),
        )
        for audit, page_id in rows
    ]
