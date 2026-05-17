"""GEO citation tracking endpoints (Phase C.2)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import GEOProbeRecord, GEOQuery, Site
from app.models.schemas import (
    GEOCitationHistoryResponse,
    GEOProbeResultRecord,
    GEOQueryCreate,
    GEOQueryRecord,
    GEOWeeklyRate,
)
from app.services.geo.citation_tracker import (
    DEFAULT_HISTORY_WINDOW_DAYS,
    DEFAULT_PROBE_TTL_DAYS,
    compute_citation_rate,
    compute_weekly_citation_history,
    probe_and_record,
)
from app.services.geo.probe import OpenAIChatProbe

router = APIRouter()


@router.post("/{site_id}/queries", response_model=GEOQueryRecord, status_code=201)
async def add_query(
    site_id: UUID,
    req: GEOQueryCreate,
    session: AsyncSession = Depends(get_session),
) -> GEOQueryRecord:
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})

    existing = await session.scalar(
        select(GEOQuery).where(
            GEOQuery.site_id == site_id,
            GEOQuery.target_query == req.target_query,
            GEOQuery.locale == req.locale,
        )
    )
    if existing is not None:
        return _serialise_query(existing)

    geo_query = GEOQuery(
        site_id=site_id,
        target_query=req.target_query,
        locale=req.locale,
    )
    session.add(geo_query)
    await session.flush()
    return _serialise_query(geo_query)


@router.get("/{site_id}/queries", response_model=list[GEOQueryRecord])
async def list_queries(
    site_id: UUID, session: AsyncSession = Depends(get_session)
) -> list[GEOQueryRecord]:
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    rows = (
        await session.scalars(
            select(GEOQuery).where(GEOQuery.site_id == site_id).order_by(GEOQuery.created_at.asc())
        )
    ).all()
    return [_serialise_query(q) for q in rows]


@router.post(
    "/queries/{query_id}/probe", response_model=GEOProbeResultRecord, status_code=201
)
async def probe_query(
    query_id: int,
    force_refresh: bool = False,
    ttl_days: int = DEFAULT_PROBE_TTL_DAYS,
    session: AsyncSession = Depends(get_session),
) -> GEOProbeResultRecord:
    """Run (or hit cache) a single GEO probe for `query_id`.

    `force_refresh=true` bypasses the 7-day TTL cache and always spends
    an LLM call. `ttl_days` overrides the window for ad-hoc admin runs.
    """
    geo_query = await session.get(GEOQuery, query_id)
    if geo_query is None:
        raise HTTPException(status_code=404, detail={"error": "geo_query_not_found"})
    if not geo_query.enabled:
        raise HTTPException(status_code=409, detail={"error": "geo_query_disabled"})
    probe = OpenAIChatProbe()
    outcome = await probe_and_record(
        session,
        geo_query,
        probe,
        ttl_days=ttl_days,
        force_refresh=force_refresh,
    )
    record = await session.get(GEOProbeRecord, outcome.geo_probe_id)
    if record is None:
        raise HTTPException(status_code=500, detail={"error": "probe_persistence_failed"})
    return _serialise_probe(
        record,
        from_cache=outcome.from_cache,
        cached_age_seconds=outcome.cached_age_seconds,
    )


@router.get(
    "/queries/{query_id}/history", response_model=GEOCitationHistoryResponse
)
async def query_history(
    query_id: int,
    weeks: int = 4,
    window_days: int = DEFAULT_HISTORY_WINDOW_DAYS,
    session: AsyncSession = Depends(get_session),
) -> GEOCitationHistoryResponse:
    geo_query = await session.get(GEOQuery, query_id)
    if geo_query is None:
        raise HTTPException(status_code=404, detail={"error": "geo_query_not_found"})
    probes = (
        await session.scalars(
            select(GEOProbeRecord)
            .where(GEOProbeRecord.geo_query_id == query_id)
            .order_by(GEOProbeRecord.probed_at.asc())
        )
    ).all()
    weekly = compute_weekly_citation_history(probes, weeks=weeks)
    window = compute_citation_rate(probes, window_days=window_days)
    return GEOCitationHistoryResponse(
        site_id=geo_query.site_id,
        geo_query_id=geo_query.id,
        target_query=geo_query.target_query,
        weekly=[
            GEOWeeklyRate(
                week_start=p.week_start.isoformat(),
                probes=p.probes,
                cited=p.cited,
                rate=round(p.rate, 3),
            )
            for p in weekly
        ],
        overall_window_days=window_days,
        overall_probes=window.probes,
        overall_cited=window.cited,
        overall_rate=round(window.rate, 3),
    )


def _serialise_query(q: GEOQuery) -> GEOQueryRecord:
    return GEOQueryRecord(
        id=q.id,
        site_id=q.site_id,
        target_query=q.target_query,
        locale=q.locale,
        enabled=q.enabled,
        last_probed_at=q.last_probed_at.isoformat() if q.last_probed_at else None,
        last_contains_site=q.last_contains_site,
    )


def _serialise_probe(
    r: GEOProbeRecord,
    *,
    from_cache: bool = False,
    cached_age_seconds: int | None = None,
) -> GEOProbeResultRecord:
    return GEOProbeResultRecord(
        id=r.id,
        geo_query_id=r.geo_query_id,
        provider=r.provider,
        model_name=r.model_name,
        cited_urls=list(r.cited_urls),
        contains_site=r.contains_site,
        site_position=r.site_position,
        probed_at=r.probed_at.isoformat(),
        from_cache=from_cache,
        cached_age_seconds=cached_age_seconds,
    )
