"""Tracked-query orchestration for GEO probes.

`probe_and_record(session, query)` runs a single `GEOProbe` for a stored
`GEOQuery` row, persists a `GEOProbeRecord`, and refreshes the query's
denormalised `last_*` columns. Plus pure helpers for the dashboard
side: `compute_citation_rate(probes, window_days)` returns the fraction
of probes in the window where the site appeared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GEOProbeRecord, GEOQuery, Site
from app.services.geo.probe import GEOProbe, ProbeResult

DEFAULT_HISTORY_WINDOW_DAYS = 30

# Critical Decision #1 in the roadmap: GEO probes are LLM-priced; cache
# 7 days by default to keep gross margin north of 90% on a busy account.
# Per-call override exposed on `probe_and_record` so an admin "force refresh"
# button can bypass the cache.
DEFAULT_PROBE_TTL_DAYS = 7


@dataclass(frozen=True)
class ProbeOutcome:
    geo_probe_id: int
    geo_query_id: int
    cited_urls: tuple[str, ...]
    contains_site: bool
    site_position: int | None
    from_cache: bool = False
    cached_age_seconds: int | None = None


@dataclass(frozen=True)
class CitationRatePoint:
    week_start: datetime
    probes: int
    cited: int

    @property
    def rate(self) -> float:
        return (self.cited / self.probes) if self.probes else 0.0


async def probe_and_record(
    session: AsyncSession,
    geo_query: GEOQuery,
    probe: GEOProbe,
    *,
    ttl_days: int | None = DEFAULT_PROBE_TTL_DAYS,
    force_refresh: bool = False,
    now: datetime | None = None,
) -> ProbeOutcome:
    """Run `probe` for `geo_query.target_query`, persist the result.

    TTL behaviour:
      - When `force_refresh` is False (default) and `ttl_days > 0`, we
        check `GEOProbeRecord` for a row matching `(geo_query, provider,
        model_name)` younger than the TTL. On a hit we return that row's
        data with `from_cache=True` — no LLM call is made.
      - When `force_refresh` is True or `ttl_days` is 0/None, we always
        spend an LLM call and append a fresh row.

    The site's root host is read from `geo_query.site` (eager-loaded by
    the caller, or via `session.get(Site, geo_query.site_id)`).
    """
    site = await session.get(Site, geo_query.site_id)
    if site is None:
        raise ValueError(f"site {geo_query.site_id} not found for geo_query {geo_query.id}")

    now = now or datetime.now(tz=timezone.utc)

    if not force_refresh and ttl_days and ttl_days > 0:
        cached = await _lookup_cached_probe(
            session, geo_query=geo_query, probe=probe, ttl_days=ttl_days, now=now
        )
        if cached is not None:
            age = max(0, int((now - cached.probed_at).total_seconds()))
            return ProbeOutcome(
                geo_probe_id=cached.id,
                geo_query_id=geo_query.id,
                cited_urls=tuple(cached.cited_urls or ()),
                contains_site=cached.contains_site,
                site_position=cached.site_position,
                from_cache=True,
                cached_age_seconds=age,
            )

    result = await probe.probe(geo_query.target_query, locale=geo_query.locale)
    contains, position = _matches_site(result.cited_urls, site.root_url)

    record = GEOProbeRecord(
        geo_query_id=geo_query.id,
        provider=result.provider,
        model_name=result.model_name,
        cited_urls=list(result.cited_urls),
        contains_site=contains,
        site_position=position,
        raw_response=result.raw_response,
        probed_at=result.probed_at,
    )
    session.add(record)
    geo_query.last_probed_at = result.probed_at
    geo_query.last_contains_site = contains
    await session.flush()
    return ProbeOutcome(
        geo_probe_id=record.id,
        geo_query_id=geo_query.id,
        cited_urls=result.cited_urls,
        contains_site=contains,
        site_position=position,
        from_cache=False,
        cached_age_seconds=None,
    )


async def _lookup_cached_probe(
    session: AsyncSession,
    *,
    geo_query: GEOQuery,
    probe: GEOProbe,
    ttl_days: int,
    now: datetime,
) -> GEOProbeRecord | None:
    """Return the most-recent `GEOProbeRecord` matching `(query, provider,
    model_name)` within the TTL window, or None."""
    horizon = now - timedelta(days=ttl_days)
    stmt = (
        select(GEOProbeRecord)
        .where(
            GEOProbeRecord.geo_query_id == geo_query.id,
            GEOProbeRecord.provider == probe.provider,
            GEOProbeRecord.model_name == probe.model_name,
            GEOProbeRecord.probed_at >= horizon,
        )
        .order_by(GEOProbeRecord.probed_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


async def probe_all_for_site(
    session: AsyncSession,
    site_id: UUID,
    probe: GEOProbe,
    *,
    ttl_days: int | None = DEFAULT_PROBE_TTL_DAYS,
    force_refresh: bool = False,
) -> list[ProbeOutcome]:
    queries = (
        await session.scalars(
            select(GEOQuery).where(
                GEOQuery.site_id == site_id, GEOQuery.enabled.is_(True)
            )
        )
    ).all()
    out: list[ProbeOutcome] = []
    for q in queries:
        out.append(
            await probe_and_record(
                session, q, probe, ttl_days=ttl_days, force_refresh=force_refresh
            )
        )
    return out


def matches_site(cited_urls: Sequence[str], site_root_url: str) -> tuple[bool, int | None]:
    """Public alias for the test surface — calls the private helper."""
    return _matches_site(cited_urls, site_root_url)


def compute_citation_rate(
    probes: Iterable[GEOProbeRecord],
    *,
    window_days: int = DEFAULT_HISTORY_WINDOW_DAYS,
    now: datetime | None = None,
) -> CitationRatePoint:
    """Single-window citation rate: cited probes ÷ total probes."""
    now = now or datetime.now(tz=timezone.utc)
    horizon = now - timedelta(days=window_days)
    total = 0
    cited = 0
    for p in probes:
        if p.probed_at < horizon:
            continue
        total += 1
        if p.contains_site:
            cited += 1
    return CitationRatePoint(week_start=horizon, probes=total, cited=cited)


def compute_weekly_citation_history(
    probes: Iterable[GEOProbeRecord],
    *,
    weeks: int = 4,
    now: datetime | None = None,
) -> list[CitationRatePoint]:
    """Per-week citation rate for the last `weeks` weeks.

    Useful for the dashboard's "are we trending up or down" chart.
    """
    now = now or datetime.now(tz=timezone.utc)
    # Week buckets keyed by week-start (Monday) — make `weeks` empty
    # buckets so quiet weeks render as 0% rather than disappearing.
    week_starts: list[datetime] = []
    for i in range(weeks - 1, -1, -1):
        week_starts.append(_week_start_utc(now - timedelta(weeks=i)))
    buckets: dict[datetime, tuple[int, int]] = {ws: (0, 0) for ws in week_starts}
    for p in probes:
        ws = _week_start_utc(p.probed_at)
        if ws not in buckets:
            continue
        total, cited = buckets[ws]
        buckets[ws] = (total + 1, cited + (1 if p.contains_site else 0))
    return [
        CitationRatePoint(week_start=ws, probes=total, cited=cited)
        for ws, (total, cited) in sorted(buckets.items())
    ]


def _matches_site(
    cited_urls: Sequence[str], site_root_url: str
) -> tuple[bool, int | None]:
    site_host = _registrable(site_root_url)
    if not site_host:
        return False, None
    for i, url in enumerate(cited_urls):
        host = _registrable(url)
        if host and host.endswith(site_host):
            return True, i + 1  # 1-indexed position to match human conventions
    return False, None


def _registrable(url: str) -> str:
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return ""
    host = (parts.netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def _week_start_utc(dt: datetime) -> datetime:
    """Round `dt` down to the most recent Monday 00:00 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    monday = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday
