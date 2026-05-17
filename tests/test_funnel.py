"""Unit tests for services/funnel."""

from __future__ import annotations

from typing import Any

import pytest

from app.db.models import FunnelEvent, FunnelStage
from app.services import funnel
from app.services.funnel import compute_rates, record


class FakeSession:
    def __init__(self):
        self.events: list[FunnelEvent] = []
        self.flush_called = 0
        self.raise_on_flush = False

    def add(self, obj):
        self.events.append(obj)

    async def flush(self):
        self.flush_called += 1
        if self.raise_on_flush:
            raise RuntimeError("simulated db failure")


@pytest.mark.asyncio
async def test_record_inserts_event():
    s = FakeSession()
    await record(s, FunnelStage.discovered, prospect_id=42, meta={"x": 1})
    assert len(s.events) == 1
    e = s.events[0]
    assert e.stage == FunnelStage.discovered
    assert e.prospect_id == 42
    assert e.meta == {"x": 1}
    assert s.flush_called == 1


@pytest.mark.asyncio
async def test_record_swallows_db_errors():
    s = FakeSession()
    s.raise_on_flush = True
    # Must not raise — funnel writes are fire-and-forget.
    await record(s, FunnelStage.audited, audit_id=99)


def test_compute_rates_handles_zero_denominators():
    counts = {stage.value: 0 for stage in FunnelStage}
    rates = compute_rates(counts)
    assert all(r == 0.0 for r in rates.values())


def test_compute_rates_basic():
    counts = {
        "visited": 1000,
        "scanned": 400,
        "paywalled": 200,
        "trialing": 80,
        "converted": 30,
        "discovered": 500,
        "contacted": 300,
        "engaged": 60,
    }
    # Fill in all required stages with zeros.
    for stage in FunnelStage:
        counts.setdefault(stage.value, 0)
    rates = compute_rates(counts)
    assert rates["visit_to_scan"] == 0.4
    assert rates["scan_to_paywall"] == 0.5
    assert rates["paywall_to_trial"] == 0.4
    assert rates["trial_to_paid"] == 0.375
    assert rates["discover_to_paid"] == 0.06
    assert rates["contact_to_engage"] == 0.2


class FakeQuerySession:
    """Mimics session.execute for cohort_counts test."""

    def __init__(self, events: list[tuple[FunnelStage, int]]):
        self._events = events

    async def execute(self, _stmt):
        return _Rows([(stage, idx) for idx, (stage, _) in enumerate(self._events)])


class _Rows:
    def __init__(self, items): self._items = items
    def all(self): return list(self._items)


@pytest.mark.asyncio
async def test_cohort_counts_groups_by_stage():
    events = [
        (FunnelStage.discovered, 1),
        (FunnelStage.discovered, 2),
        (FunnelStage.audited, 3),
        (FunnelStage.engaged, 4),
        (FunnelStage.engaged, 5),
        (FunnelStage.engaged, 6),
    ]
    s = FakeQuerySession(events)
    counts = await funnel.cohort_counts(s)
    assert counts["discovered"] == 2
    assert counts["audited"] == 1
    assert counts["engaged"] == 3
    assert counts["converted"] == 0  # zero-filled
