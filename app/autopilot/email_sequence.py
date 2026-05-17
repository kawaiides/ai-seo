"""State-aware drip sequence.

Reads `outreach` (what has been sent) + `funnel_event` (what the
recipient did) and chooses the next variant per (contact, audit) pair.

State machine — variants ordered by precedence:

    cold_outreach      sent at T+0           (handled by outbox_mailer.send_pending)
    followup_competitor    if T+3d and not engaged
    followup_byok          if T+7d and not engaged
    breakup                if T+14d and not engaged
    trial_nudge            if engaged but not converted, T+2d after engage
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot import outbox_mailer
from app.db.models import Audit, Contact, FunnelEvent, FunnelStage, Outreach, Prospect

log = logging.getLogger(__name__)


# Variant timing rules. (variant, delay_after, predecessor_variant, requires_engaged)
SEQUENCE_RULES = [
    # variant_name, days_after_predecessor, predecessor_variant, requires_engaged
    ("followup_competitor", 3,  "cold_outreach",       False),
    ("followup_byok",       7,  "cold_outreach",       False),
    ("breakup",             14, "cold_outreach",       False),
    ("trial_nudge",         2,  "engaged",             True),
]


@dataclass(frozen=True)
class PlannedSend:
    contact: Contact
    audit: Audit
    prospect: Prospect
    variant: str


async def _all_audited_pairs(
    session: AsyncSession,
) -> list[tuple[Contact, Audit, Prospect]]:
    stmt = (
        select(Contact, Audit, Prospect)
        .join(Prospect, Prospect.id == Contact.prospect_id)
        .join(Audit, Audit.prospect_id == Prospect.id)
    )
    rows = await session.execute(stmt)
    return [tuple(r) for r in rows.all()]


async def _outreach_history(
    session: AsyncSession, contact_id: int, audit_id: int
) -> dict[str, Outreach]:
    rows = (
        await session.execute(
            select(Outreach)
            .where(Outreach.contact_id == contact_id)
            .where(Outreach.audit_id == audit_id)
        )
    ).scalars().all()
    return {o.template_variant: o for o in rows}


async def _engaged_at(
    session: AsyncSession, audit_id: int
) -> datetime | None:
    row = (
        await session.execute(
            select(FunnelEvent.occurred_at)
            .where(FunnelEvent.audit_id == audit_id)
            .where(FunnelEvent.stage == FunnelStage.engaged)
            .order_by(FunnelEvent.occurred_at.asc())
            .limit(1)
        )
    ).first()
    return row[0] if row else None


async def _converted_for(
    session: AsyncSession, audit_id: int
) -> bool:
    row = (
        await session.execute(
            select(FunnelEvent.id)
            .where(FunnelEvent.audit_id == audit_id)
            .where(FunnelEvent.stage == FunnelStage.converted)
            .limit(1)
        )
    ).first()
    return row is not None


async def pick_next_variant(
    session: AsyncSession,
    contact: Contact,
    audit: Audit,
    *,
    now: datetime | None = None,
) -> str | None:
    """Return the next variant due for this pair, or None if nothing fires."""
    now = now or datetime.now(tz=timezone.utc)
    history = await _outreach_history(session, contact.id, audit.id)
    engaged_at = await _engaged_at(session, audit.id)
    converted = await _converted_for(session, audit.id)

    if converted:
        return None  # never nudge a paid customer

    # cold_outreach must exist before any follow-up fires.
    cold = history.get("cold_outreach")
    if not cold or not cold.sent_at:
        return None

    # Engagement-based branch: trial_nudge.
    if engaged_at is not None:
        if "trial_nudge" not in history and now - engaged_at >= timedelta(days=2):
            return "trial_nudge"
        return None

    # Time-based, non-engagement branch.
    # Walk rules in order — first one whose timing is due AND not already sent wins.
    for variant, delay_days, predecessor, requires_engaged in SEQUENCE_RULES:
        if requires_engaged:
            continue
        if variant in history:
            continue
        ref = history.get(predecessor)
        if not ref or not ref.sent_at:
            continue
        if now - ref.sent_at < timedelta(days=delay_days):
            continue
        return variant
    return None


async def schedule(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[PlannedSend]:
    """Walk every audited (contact, audit) pair and decide what's due."""
    plans: list[PlannedSend] = []
    pairs = await _all_audited_pairs(session)
    for contact, audit, prospect in pairs:
        variant = await pick_next_variant(session, contact, audit, now=now)
        if variant:
            plans.append(PlannedSend(contact=contact, audit=audit,
                                     prospect=prospect, variant=variant))
    return plans


async def tick(
    session: AsyncSession,
    *,
    max_per_variant: int = 30,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, outbox_mailer.SendStats]:
    """Run the scheduler + dispatch each variant batch through the mailer.

    Returns per-variant SendStats so the CLI / cron can log per-step volume.
    """
    plans = await schedule(session, now=now)

    # Group by variant so we can batch the mailer per template.
    grouped: dict[str, list[PlannedSend]] = {}
    for p in plans:
        grouped.setdefault(p.variant, []).append(p)

    results: dict[str, outbox_mailer.SendStats] = {}
    for variant, items in grouped.items():
        candidates = [(p.contact, p.audit, p.prospect) for p in items[:max_per_variant]]
        stats = await outbox_mailer.send_pending(
            session,
            variant=variant,
            max_sends=max_per_variant,
            dry_run=dry_run,
            candidates=candidates,
        )
        results[variant] = stats
        log.info("email_sequence: variant=%s sent=%d eligible=%d",
                 variant, stats.sent, stats.eligible)
    return results
