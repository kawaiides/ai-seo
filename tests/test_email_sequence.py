"""Unit tests for autopilot/email_sequence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.autopilot import email_sequence
from app.autopilot.email_sequence import pick_next_variant, schedule
from app.db.models import Audit, Contact, FunnelStage, Outreach, Prospect


NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _make(score: int = 42) -> tuple[Contact, Audit, Prospect]:
    p = Prospect(url="https://ex.com/a", domain="ex.com", target_keyword="x")
    p.id = 1
    a = Audit(prospect_id=p.id, aeo_score=score, band="Significant Gaps")
    a.id = 10
    a.token_jti = uuid.UUID("00000000-0000-0000-0000-000000000001")
    a.fanout_payload = None
    a.missing_gap_types = None
    a.failed_checks = None
    c = Contact(prospect_id=p.id, email="x@ex.com")
    c.id = 5
    return c, a, p


def _outreach(variant: str, sent_at: datetime | None) -> Outreach:
    o = Outreach(
        contact_id=5, audit_id=10, template_variant=variant,
        sent_at=sent_at,
    )
    o.id = hash(variant) & 0xFFFFFFFF
    return o


@dataclass
class _Row:
    value: Any
    def __iter__(self): return iter([self.value])
    def __getitem__(self, i): return [self.value][i]


class _Result:
    def __init__(self, items):
        self._items = items
    def scalars(self):
        return self
    def all(self):
        return list(self._items)
    def first(self):
        return _Row(self._items[0]) if self._items else None


class FakeSeqSession:
    """Routes by SQL fragment so pick_next_variant can be tested in isolation."""

    def __init__(self, *, outreach: list[Outreach], engaged_at: datetime | None = None,
                 converted: bool = False, pairs=None):
        self._outreach = outreach
        self._engaged_at = engaged_at
        self._converted = converted
        self._pairs = pairs or []

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "from outreach" in text:
            return _Result(self._outreach)
        # _engaged_at selects FunnelEvent.occurred_at; _converted_for selects FunnelEvent.id.
        if "funnel_event" in text and "occurred_at" in text:
            return _Result([self._engaged_at] if self._engaged_at else [])
        if "funnel_event" in text:
            return _Result(["sentinel"] if self._converted else [])
        if "from contact" in text:
            return _Result(self._pairs)
        return _Result([])


# ---- pick_next_variant ----


@pytest.mark.asyncio
async def test_no_cold_no_followup():
    c, a, _ = _make()
    s = FakeSeqSession(outreach=[])
    assert await pick_next_variant(s, c, a, now=NOW) is None


@pytest.mark.asyncio
async def test_too_early_after_cold():
    c, a, _ = _make()
    s = FakeSeqSession(outreach=[_outreach("cold_outreach", NOW - timedelta(days=1))])
    # 1 day post-cold, nothing due yet (followup_competitor needs T+3d).
    assert await pick_next_variant(s, c, a, now=NOW) is None


@pytest.mark.asyncio
async def test_competitor_due_at_t_plus_3():
    c, a, _ = _make()
    s = FakeSeqSession(outreach=[_outreach("cold_outreach", NOW - timedelta(days=3, hours=1))])
    assert await pick_next_variant(s, c, a, now=NOW) == "followup_competitor"


@pytest.mark.asyncio
async def test_byok_due_at_t_plus_7():
    c, a, _ = _make()
    s = FakeSeqSession(outreach=[
        _outreach("cold_outreach", NOW - timedelta(days=8)),
        _outreach("followup_competitor", NOW - timedelta(days=5)),
    ])
    assert await pick_next_variant(s, c, a, now=NOW) == "followup_byok"


@pytest.mark.asyncio
async def test_breakup_due_at_t_plus_14():
    c, a, _ = _make()
    s = FakeSeqSession(outreach=[
        _outreach("cold_outreach", NOW - timedelta(days=15)),
        _outreach("followup_competitor", NOW - timedelta(days=12)),
        _outreach("followup_byok", NOW - timedelta(days=8)),
    ])
    assert await pick_next_variant(s, c, a, now=NOW) == "breakup"


@pytest.mark.asyncio
async def test_no_more_after_breakup():
    c, a, _ = _make()
    s = FakeSeqSession(outreach=[
        _outreach("cold_outreach", NOW - timedelta(days=30)),
        _outreach("followup_competitor", NOW - timedelta(days=27)),
        _outreach("followup_byok", NOW - timedelta(days=23)),
        _outreach("breakup", NOW - timedelta(days=16)),
    ])
    assert await pick_next_variant(s, c, a, now=NOW) is None


@pytest.mark.asyncio
async def test_engaged_disables_time_based_followups():
    """Once engaged, only trial_nudge is on the table."""
    c, a, _ = _make()
    s = FakeSeqSession(
        outreach=[_outreach("cold_outreach", NOW - timedelta(days=10))],
        engaged_at=NOW - timedelta(days=1),
    )
    # Only 1 day after engage; trial_nudge wants T+2d.
    assert await pick_next_variant(s, c, a, now=NOW) is None


@pytest.mark.asyncio
async def test_trial_nudge_after_engage_plus_2():
    c, a, _ = _make()
    s = FakeSeqSession(
        outreach=[_outreach("cold_outreach", NOW - timedelta(days=10))],
        engaged_at=NOW - timedelta(days=3),
    )
    assert await pick_next_variant(s, c, a, now=NOW) == "trial_nudge"


@pytest.mark.asyncio
async def test_trial_nudge_only_fires_once():
    c, a, _ = _make()
    s = FakeSeqSession(
        outreach=[
            _outreach("cold_outreach", NOW - timedelta(days=10)),
            _outreach("trial_nudge", NOW - timedelta(days=1)),
        ],
        engaged_at=NOW - timedelta(days=3),
    )
    assert await pick_next_variant(s, c, a, now=NOW) is None


@pytest.mark.asyncio
async def test_converted_kills_all_followups():
    c, a, _ = _make()
    s = FakeSeqSession(
        outreach=[_outreach("cold_outreach", NOW - timedelta(days=20))],
        engaged_at=NOW - timedelta(days=10),
        converted=True,
    )
    assert await pick_next_variant(s, c, a, now=NOW) is None


# ---- schedule (top-level) ----


@pytest.mark.asyncio
async def test_schedule_walks_all_pairs(monkeypatch):
    c1, a1, p1 = _make()
    c2, a2, p2 = _make()
    c2.id = 7
    a2.id = 11

    async def _fake_pick(session, contact, audit, *, now=None):
        return "followup_competitor" if contact.id == 5 else None

    monkeypatch.setattr(email_sequence, "pick_next_variant", _fake_pick)

    s = FakeSeqSession(outreach=[], pairs=[(c1, a1, p1), (c2, a2, p2)])
    plans = await schedule(s, now=NOW)
    assert len(plans) == 1
    assert plans[0].contact.id == 5
    assert plans[0].variant == "followup_competitor"
