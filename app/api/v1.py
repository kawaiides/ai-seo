"""API-key-gated v1 surface (Phase E).

Mirrors the cookie-authenticated `/api/aeo/analyze` endpoint behind
Bearer-token auth + scoped permissions so external CI/CMS integrations
can run audits programmatically.

Versioned under `/api/v1` so we can keep the existing browser-facing
endpoints stable while iterating on the public contract.

Pro-only surfaces (Bearer key = org with paid plan):

  POST /api/v1/audit              single-URL/text audit (all 7 checks)
  POST /api/v1/audit/bulk         batch audit up to 50 inputs concurrently
  GET  /api/v1/sites/{id}/export  site rollup + competitors export
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.aeo import _build_response
from app.db.base import get_session
from app.db.models import Site, SitePage
from app.models.schemas import AEOAnalyzeRequest, AEOAnalyzeResponse, InputType
from app.services.aeo_checks import default_checks
from app.services.api_keys import AuthorizedKey, require_scope
from app.services.content_parser import (
    ContentParseError,
    URLFetchError,
    fetch_url,
    parse,
)
from app.services.site.competitors import summarize_existing
from app.services.site.dashboard import (
    PageRow,
    compute_missing_clusters,
    compute_summary,
    compute_top_worst,
)

router = APIRouter()

BULK_MAX = 50
BULK_CONCURRENCY = 8


class BulkAuditItem(BaseModel):
    input_type: InputType
    input_value: str = Field(..., min_length=1)
    label: str | None = Field(default=None, max_length=128)


class BulkAuditRequest(BaseModel):
    items: list[BulkAuditItem] = Field(..., min_length=1, max_length=BULK_MAX)


class BulkAuditItemResult(BaseModel):
    label: str | None
    input_value: str
    ok: bool
    audit: AEOAnalyzeResponse | None = None
    error: str | None = None


class BulkAuditResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[BulkAuditItemResult]


class SiteExportPage(BaseModel):
    page_id: int
    url: str
    title: str | None
    last_score: int | None
    last_band: str | None
    last_audited_at: str | None
    last_missing_types: list[str]
    last_failed_checks: list[str]


class SiteExportResponse(BaseModel):
    site_id: UUID
    root_url: str
    org_id: UUID | None
    pages_total: int
    pages_audited: int
    mean_score: float | None
    median_score: float | None
    band_counts: dict[str, int]
    top_worst: list[dict]
    missing_clusters: list[dict]
    pages: list[SiteExportPage]
    competitors: dict
    exported_at: str


@router.post("/audit", response_model=AEOAnalyzeResponse)
async def audit(
    req: AEOAnalyzeRequest,
    authorized: AuthorizedKey = Depends(require_scope("audit:write")),
) -> AEOAnalyzeResponse:
    raw = await fetch_url(req.input_value) if req.input_type == "url" else req.input_value
    parsed = parse(raw, input_type=req.input_type)
    results = [check.run(parsed) for check in default_checks()]
    return _build_response(results, parsed, is_pro=True)


@router.post("/audit/bulk", response_model=BulkAuditResponse)
async def audit_bulk(
    req: BulkAuditRequest,
    authorized: AuthorizedKey = Depends(require_scope("audit:write")),
) -> BulkAuditResponse:
    """Pro-only batch audit endpoint.

    Concurrency capped at `BULK_CONCURRENCY` so a 50-URL batch can't
    saturate the worker. Per-item failures are isolated; the response
    includes both `ok=True` and `ok=False` rows so the plugin can render
    a partial-success table without retrying the whole batch.
    """
    sem = asyncio.Semaphore(BULK_CONCURRENCY)

    async def _one(item: BulkAuditItem) -> BulkAuditItemResult:
        async with sem:
            try:
                raw = (
                    await fetch_url(item.input_value)
                    if item.input_type == "url"
                    else item.input_value
                )
                parsed = parse(raw, input_type=item.input_type)
                results = [check.run(parsed) for check in default_checks()]
                resp = _build_response(results, parsed, is_pro=True)
                return BulkAuditItemResult(
                    label=item.label,
                    input_value=item.input_value,
                    ok=True,
                    audit=resp,
                )
            except (URLFetchError, ContentParseError) as e:
                return BulkAuditItemResult(
                    label=item.label,
                    input_value=item.input_value,
                    ok=False,
                    error=getattr(e, "detail", str(e)),
                )
            except Exception as e:
                return BulkAuditItemResult(
                    label=item.label,
                    input_value=item.input_value,
                    ok=False,
                    error=f"{type(e).__name__}: {e}",
                )

    results = await asyncio.gather(*[_one(i) for i in req.items])
    succeeded = sum(1 for r in results if r.ok)
    return BulkAuditResponse(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=list(results),
    )


@router.get("/sites/{site_id}/export", response_model=SiteExportResponse)
async def site_export(
    site_id: UUID,
    session: AsyncSession = Depends(get_session),
    authorized: AuthorizedKey = Depends(require_scope("site:read")),
) -> SiteExportResponse:
    """Pro-only site rollup + per-page export for offline analysis.

    Caller's API key org MUST own the site. Other-org sites return 404
    rather than 403 to avoid existence-leak.
    """
    site = await session.get(Site, site_id)
    if site is None or site.org_id != authorized.org.id:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})

    pages = (
        await session.scalars(select(SitePage).where(SitePage.site_id == site_id))
    ).all()
    rows = [
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
    summary = compute_summary(rows)
    worst = compute_top_worst(rows, k=10)
    missing = compute_missing_clusters(rows, k=10)
    bench = await summarize_existing(session, site_id)

    return SiteExportResponse(
        site_id=site_id,
        root_url=site.root_url,
        org_id=site.org_id,
        pages_total=summary.pages_total,
        pages_audited=summary.pages_audited,
        mean_score=summary.mean_score,
        median_score=summary.median_score,
        band_counts=summary.band_counts,
        top_worst=[
            {
                "page_id": w.page_id,
                "url": w.url,
                "score": w.score,
                "band": w.band,
                "failed_checks": list(w.failed_checks),
            }
            for w in worst
        ],
        missing_clusters=[
            {"type": m.type, "pages_missing": m.pages_missing} for m in missing
        ],
        pages=[
            SiteExportPage(
                page_id=p.id,
                url=p.url,
                title=p.title,
                last_score=p.last_score,
                last_band=p.last_band,
                last_audited_at=(
                    p.last_audited_at.isoformat() if p.last_audited_at else None
                ),
                last_missing_types=list(p.last_missing_types or ()),
                last_failed_checks=list(p.last_failed_checks or ()),
            )
            for p in pages
        ],
        competitors={
            "delta_mean_score": bench.delta_mean_score,
            "intent_gap": bench.intent_gap,
            "rows": [
                {
                    "root_url": c.root_url,
                    "mean_score": c.mean_score,
                    "median_score": c.median_score,
                    "band_counts": c.band_counts,
                    "status": c.status,
                    "pages_total": c.pages_total,
                    "pages_audited": c.pages_audited,
                }
                for c in bench.competitors
            ],
        },
        exported_at=datetime.now(tz=timezone.utc).isoformat(),
    )


@router.get("/ping")
async def ping(
    authorized: AuthorizedKey = Depends(require_scope("audit:read")),
) -> dict:
    """Cheap health check that exercises Bearer auth without spending
    audit cost. Used by CI plugins to validate key before running."""
    return {
        "ok": True,
        "org_id": str(authorized.org.id),
        "org_slug": authorized.org.slug,
        "key_prefix": authorized.api_key.prefix,
        "scopes": list(authorized.api_key.scopes),
        "now": datetime.now(tz=timezone.utc).isoformat(),
    }
