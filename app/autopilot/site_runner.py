"""Weekly site re-audit runner + score-drop alerts (Phase C.2).

Iterates every `Site`, calls `audit_site_pages` (shared with the API
endpoint), then compares each page's freshest two `SitePageAudit` rows.
Anything that dropped by `SCORE_DROP_ALERT_THRESHOLD` or more is
returned in the per-site `SiteRunResult.alerts` envelope so a downstream
notifier can post to Slack / Linear / email.

The runner is invoked from `app.autopilot.__main__` (`python -m
app.autopilot site_reaudit`) and could be wired to a weekly OS cron or
the project's eventual job scheduler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Site, SitePage, SitePageAudit
from app.services.site.audit import SiteAuditOutcome, audit_site_pages

log = logging.getLogger(__name__)

SCORE_DROP_ALERT_THRESHOLD = 10
DEFAULT_PAGES_PER_SITE = 50
DEFAULT_AUDIT_CONCURRENCY = 4


@dataclass(frozen=True)
class ScoreDropAlert:
    """A page whose latest audit dropped >= threshold compared to its
    previous audit. The previous-audit reference is the second-most-recent
    `SitePageAudit` row, not the prior `SitePage.last_score` rollup — we
    want to compare the two NEW audits, not an audit against its own
    rollup it just overwrote."""

    site_id: UUID
    page_id: int
    url: str
    prior_score: int
    new_score: int
    delta: int
    failed_checks: tuple[str, ...]


@dataclass
class SiteRunResult:
    site_id: UUID
    root_url: str
    outcome: SiteAuditOutcome
    alerts: list[ScoreDropAlert] = field(default_factory=list)


async def run_weekly_reaudit(
    session: AsyncSession,
    *,
    pages_per_site: int = DEFAULT_PAGES_PER_SITE,
    concurrency: int = DEFAULT_AUDIT_CONCURRENCY,
    site_ids: Sequence[UUID] | None = None,
) -> list[SiteRunResult]:
    """Re-audit each `Site`'s pages and compute score-drop alerts.

    `site_ids` lets a caller scope a single run to one site (used by the
    admin "re-audit now" button in Phase D). Pass `None` to iterate all.
    """
    sites = await _select_sites(session, site_ids)
    results: list[SiteRunResult] = []
    for site in sites:
        log.info("site_reaudit: %s (%s)", site.id, site.root_url)
        outcome = await audit_site_pages(
            session, site.id, limit=pages_per_site, concurrency=concurrency
        )
        # We must flush before reading history rows so the alerts pick up
        # the rows that audit_site_pages just appended.
        await session.flush()
        alerts = await compute_score_drop_alerts(session, site.id)
        results.append(
            SiteRunResult(
                site_id=site.id,
                root_url=site.root_url,
                outcome=outcome,
                alerts=alerts,
            )
        )
    return results


async def _select_sites(
    session: AsyncSession, site_ids: Sequence[UUID] | None
) -> Sequence[Site]:
    stmt = select(Site)
    if site_ids is not None:
        if not site_ids:
            return []
        stmt = stmt.where(Site.id.in_(site_ids))
    stmt = stmt.order_by(Site.created_at.asc())
    return (await session.scalars(stmt)).all()


async def compute_score_drop_alerts(
    session: AsyncSession,
    site_id: UUID,
    *,
    threshold: int = SCORE_DROP_ALERT_THRESHOLD,
) -> list[ScoreDropAlert]:
    """For each page in `site_id`, compare its two most recent audits.

    Pure read-side helper — separable from `run_weekly_reaudit` so the
    admin UI and tests can call it without re-running the audit batch.
    """
    pages = (
        await session.scalars(select(SitePage).where(SitePage.site_id == site_id))
    ).all()
    alerts: list[ScoreDropAlert] = []
    for page in pages:
        recent = (
            await session.scalars(
                select(SitePageAudit)
                .where(SitePageAudit.site_page_id == page.id)
                .order_by(SitePageAudit.audited_at.desc())
                .limit(2)
            )
        ).all()
        if len(recent) < 2:
            continue
        new_audit, prior_audit = recent[0], recent[1]
        delta = new_audit.aeo_score - prior_audit.aeo_score
        if delta <= -threshold:
            alerts.append(
                ScoreDropAlert(
                    site_id=site_id,
                    page_id=page.id,
                    url=page.url,
                    prior_score=prior_audit.aeo_score,
                    new_score=new_audit.aeo_score,
                    delta=delta,
                    failed_checks=tuple(new_audit.failed_checks or ()),
                )
            )
    return alerts


def compute_score_drop_alerts_from_rows(
    page_audit_pairs: list[tuple[int, str, int, int]],
    *,
    threshold: int = SCORE_DROP_ALERT_THRESHOLD,
) -> list[ScoreDropAlert]:
    """Pure-function variant for tests + ad-hoc CLI tooling.

    Input rows are `(page_id, url, prior_score, new_score)`. The
    DB-touching wrapper above is the production path; this one keeps the
    delta math testable in isolation.
    """
    out: list[ScoreDropAlert] = []
    for page_id, url, prior, new in page_audit_pairs:
        delta = new - prior
        if delta <= -threshold:
            out.append(
                ScoreDropAlert(
                    site_id=UUID(int=0),
                    page_id=page_id,
                    url=url,
                    prior_score=prior,
                    new_score=new,
                    delta=delta,
                    failed_checks=(),
                )
            )
    return out
