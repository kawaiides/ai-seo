"""Funnel-event recorder.

Single import point for every pipeline module + API route. Calls are
fire-and-forget: a write here must never block or fail the parent
transaction — if event insertion errors, log and continue.

`funnel_event` is append-only. Never UPDATE / DELETE — cohort math
relies on the timeline staying immutable.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FunnelEvent, FunnelStage

log = logging.getLogger(__name__)


async def record(
    session: AsyncSession,
    stage: FunnelStage,
    *,
    user_id: uuid.UUID | None = None,
    prospect_id: int | None = None,
    audit_id: int | None = None,
    contact_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Insert a single funnel event. Swallows + logs errors so callers
    never need to defensively try/except around it."""
    try:
        session.add(
            FunnelEvent(
                user_id=user_id,
                prospect_id=prospect_id,
                audit_id=audit_id,
                contact_id=contact_id,
                stage=stage,
                meta=meta,
            )
        )
        await session.flush()
    except Exception:  # noqa: BLE001
        log.exception("funnel: failed to record stage=%s", stage)


async def cohort_counts(
    session: AsyncSession,
    *,
    since: Any = None,
    until: Any = None,
) -> dict[str, int]:
    """Return `{stage_name: count}` for the given window.

    Drives the admin dashboard. Single GROUP BY pass over `funnel_event`.
    """
    stmt = select(FunnelEvent.stage, FunnelEvent.id)
    if since is not None:
        stmt = stmt.where(FunnelEvent.occurred_at >= since)
    if until is not None:
        stmt = stmt.where(FunnelEvent.occurred_at <= until)

    rows = (await session.execute(stmt)).all()
    counts: dict[str, int] = {stage.value: 0 for stage in FunnelStage}
    for stage, _id in rows:
        counts[stage.value] += 1
    return counts


def compute_rates(counts: dict[str, int]) -> dict[str, float]:
    """Stage-to-stage conversion rates. Safe against divide-by-zero."""

    def _r(num: str, den: str) -> float:
        d = counts.get(den, 0)
        return round(counts.get(num, 0) / d, 4) if d else 0.0

    return {
        "visit_to_scan":    _r("scanned",   "visited"),
        "scan_to_paywall":  _r("paywalled", "scanned"),
        "paywall_to_trial": _r("trialing",  "paywalled"),
        "trial_to_paid":    _r("converted", "trialing"),
        "discover_to_paid": _r("converted", "discovered"),
        "contact_to_engage": _r("engaged",  "contacted"),
    }
