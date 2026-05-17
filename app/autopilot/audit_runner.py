"""Audit runner — drains queued prospects through AEO + fan-out in-process.

Reuses the same services the HTTP API uses (no self-call). Heavy CPU work
(spaCy parsing inside checks, sentence-transformer embedding inside the
gap analyzer) is wrapped in `asyncio.to_thread` so multiple LLM calls
can overlap with embedding work in a single event loop.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Audit, FunnelStage, Prospect, ProspectStatus
from app.services.aeo_checks import default_checks
from app.services.content_parser import (
    ContentParseError,
    URLFetchError,
    fetch_url,
    parse,
)
from app.services.fanout_engine import run_fanout
from app.services.funnel import record as record_funnel
from app.services.llm_client import LLMClient, LLMUnavailableError

log = logging.getLogger(__name__)

# Mirrors the API band table in app/api/aeo.py.
BAND_THRESHOLDS = (
    (85, "AEO Optimized"),
    (65, "Needs Improvement"),
    (40, "Significant Gaps"),
    (0, "Not AEO Ready"),
)


def _band_for(score: int) -> str:
    for threshold, label in BAND_THRESHOLDS:
        if score >= threshold:
            return label
    return "Not AEO Ready"


def _score_content(raw: str) -> tuple[int, str, list[dict], int, int]:
    """Run all default AEO checks. CPU-bound; call via `asyncio.to_thread`."""
    parsed = parse(raw, input_type="url")
    results = [check.run(parsed) for check in default_checks()]
    raw_total = sum(r.score for r in results)
    max_total = sum(r.max_score for r in results) or 60
    aeo_score = round((raw_total / max_total) * 100)
    band = _band_for(aeo_score)
    failed = [
        {
            "check_id": r.check_id,
            "name": r.name,
            "score": r.score,
            "max_score": r.max_score,
            "recommendation": r.recommendation,
            "details": r.details,
        }
        for r in results
        if not r.passed
    ]
    return aeo_score, band, failed, raw_total, max_total


async def _audit_one(
    session: AsyncSession,
    prospect: Prospect,
    *,
    client: LLMClient | None,
    fanout_sem: asyncio.Semaphore,
) -> Audit | None:
    """Audit a single prospect end-to-end. Errors flip status to `failed`."""
    try:
        raw = await fetch_url(prospect.url)
    except URLFetchError as e:
        prospect.status = ProspectStatus.failed
        prospect.failure_reason = f"fetch: {e.detail}"
        log.warning("audit_runner: fetch failed for %s: %s", prospect.url, e.detail)
        return None

    try:
        aeo_score, band, failed_checks, _, _ = await asyncio.to_thread(_score_content, raw)
    except ContentParseError as e:
        prospect.status = ProspectStatus.failed
        prospect.failure_reason = f"parse: {e.detail}"
        log.warning("audit_runner: parse failed for %s: %s", prospect.url, e.detail)
        return None
    except Exception as e:  # noqa: BLE001 — never let one bad page kill the batch
        prospect.status = ProspectStatus.failed
        prospect.failure_reason = f"score: {type(e).__name__}: {e}"
        log.exception("audit_runner: score error for %s", prospect.url)
        return None

    # Fanout is rate-limited by an external API; cap concurrency separately
    # from the per-prospect outer concurrency.
    fanout_payload: dict | None = None
    missing_gap_types: list[str] = []
    try:
        async with fanout_sem:
            fr = await run_fanout(
                prospect.target_keyword,
                raw,
                client=client,
            )
        fanout_payload = fr.model_dump(exclude_none=True)
        if fr.gap_summary:
            missing_gap_types = list(fr.gap_summary.missing_types)
    except LLMUnavailableError as e:
        # LLM hiccups are not a reason to discard the AEO score — log
        # and ship the audit with empty fanout.
        log.warning("audit_runner: fanout LLM unavailable for %s: %s",
                    prospect.url, e.detail)
    except Exception as e:  # noqa: BLE001
        log.exception("audit_runner: fanout error for %s", prospect.url)

    audit = Audit(
        prospect_id=prospect.id,
        aeo_score=aeo_score,
        band=band,
        fanout_payload=fanout_payload,
        missing_gap_types=missing_gap_types or None,
        failed_checks=failed_checks or None,
    )
    session.add(audit)
    await session.flush()
    prospect.status = ProspectStatus.audited
    await record_funnel(
        session,
        FunnelStage.audited,
        prospect_id=prospect.id,
        audit_id=audit.id,
        meta={"score": aeo_score, "band": band,
              "missing_gap_types": missing_gap_types},
    )
    return audit


async def run_pending(
    session: AsyncSession,
    *,
    batch_size: int = 20,
    concurrency: int = 5,
    fanout_concurrency: int = 2,
    client: LLMClient | None = None,
) -> int:
    """Audit up to `batch_size` queued prospects. Returns rows audited."""
    pending = (
        await session.execute(
            select(Prospect)
            .where(Prospect.status == ProspectStatus.queued)
            .order_by(Prospect.discovered_at.asc())
            .limit(batch_size)
        )
    ).scalars().all()

    if not pending:
        log.info("audit_runner: no queued prospects")
        return 0

    outer_sem = asyncio.Semaphore(concurrency)
    fanout_sem = asyncio.Semaphore(fanout_concurrency)

    async def _gated(p: Prospect):
        async with outer_sem:
            return await _audit_one(session, p, client=client, fanout_sem=fanout_sem)

    results = await asyncio.gather(*(_gated(p) for p in pending), return_exceptions=False)
    audited = sum(1 for r in results if r is not None)
    log.info("audit_runner: audited %d / %d prospects", audited, len(pending))
    return audited
