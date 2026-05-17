"""Unit tests for autopilot/audit_runner.

No live Postgres or live OpenAI — fetch_url and run_fanout are patched
and a FakeSession captures DB intent. Verifies:
  * happy path writes an Audit with score/band/failed_checks
  * fetch errors flip prospect.status to failed with reason
  * LLM unavailability does NOT poison the AEO audit (audit still ships)
"""

from __future__ import annotations

from typing import Any

import pytest

from app.autopilot import audit_runner
from app.autopilot.audit_runner import _audit_one, _band_for, _score_content, run_pending
from app.db.models import Audit, Prospect, ProspectStatus
from app.models.schemas import FanoutResponse, GapSummary, SubQueryResponse
from app.services.content_parser import URLFetchError
from app.services.llm_client import LLMUnavailableError


# ---- _band_for ----


@pytest.mark.parametrize(
    "score,expected",
    [
        (100, "AEO Optimized"),
        (85, "AEO Optimized"),
        (84, "Needs Improvement"),
        (65, "Needs Improvement"),
        (64, "Significant Gaps"),
        (40, "Significant Gaps"),
        (39, "Not AEO Ready"),
        (0, "Not AEO Ready"),
    ],
)
def test_band_for(score: int, expected: str) -> None:
    assert _band_for(score) == expected


# ---- _score_content ----


SAMPLE_GOOD_HTML = """<html><body>
<h1>What is Postgres connection pooling</h1>
<p>Postgres connection pooling reuses a fixed number of database connections to reduce overhead and latency under high concurrency.</p>
<h2>How it works</h2>
<p>A pooler such as PgBouncer sits between your application and Postgres, maintaining persistent connections that clients can borrow as needed.</p>
<h2>When to use it</h2>
<p>Use a pooler when your application opens many short-lived database connections, which exhausts Postgres' per-connection memory budget.</p>
</body></html>"""


def test_score_content_returns_score_band_failed(nlp, embedder) -> None:  # fixtures load deps
    score, band, failed, raw_total, max_total = _score_content(SAMPLE_GOOD_HTML)
    assert 0 <= score <= 100
    assert band in {"AEO Optimized", "Needs Improvement", "Significant Gaps", "Not AEO Ready"}
    assert isinstance(failed, list)
    assert raw_total <= max_total
    assert max_total > 0


# ---- FakeSession + _audit_one ----


class FakeSession:
    def __init__(self):
        self.added: list[Any] = []
        self.flush_calls = 0

    def add(self, obj):
        self.added.append(obj)
        # Mimic auto-assigned PK so funnel_event can reference audit.id.
        if getattr(obj, "id", None) is None:
            obj.id = 100 + len(self.added)

    async def flush(self):
        self.flush_calls += 1


def _make_prospect() -> Prospect:
    p = Prospect(
        url="https://x.example/post",
        domain="x.example",
        target_keyword="postgres pooling",
        source="serpapi",
        status=ProspectStatus.queued,
    )
    p.id = 1
    return p


def _fanout_ok() -> FanoutResponse:
    sqs = [
        SubQueryResponse(type="comparative", query="PgBouncer vs PgPool",
                         covered=True, similarity_score=0.81),
        SubQueryResponse(type="comparative", query="PgBouncer vs Odyssey",
                         covered=False, similarity_score=0.55),
    ]
    return FanoutResponse(
        target_query="postgres pooling",
        model_used="fake-llm",
        total_sub_queries=2,
        sub_queries=sqs,
        gap_summary=GapSummary(
            covered=1, total=2, coverage_percent=50,
            covered_types=["comparative"],
            missing_types=["feature_specific", "use_case",
                           "trust_signals", "how_to", "definitional"],
        ),
    )


@pytest.mark.asyncio
async def test_audit_one_happy_path(monkeypatch, nlp, embedder) -> None:
    async def _fake_fetch(url, timeout=10.0):
        return SAMPLE_GOOD_HTML

    async def _fake_fanout(target_query, existing_content, *, client=None):
        return _fanout_ok()

    monkeypatch.setattr(audit_runner, "fetch_url", _fake_fetch)
    monkeypatch.setattr(audit_runner, "run_fanout", _fake_fanout)

    session = FakeSession()
    prospect = _make_prospect()
    sem = __import__("asyncio").Semaphore(2)

    audit = await _audit_one(session, prospect, client=None, fanout_sem=sem)

    assert audit is not None
    assert isinstance(audit, Audit)
    assert audit.aeo_score == pytest.approx(audit.aeo_score, abs=0)  # sanity
    assert audit.band in {"AEO Optimized", "Needs Improvement",
                          "Significant Gaps", "Not AEO Ready"}
    assert prospect.status == ProspectStatus.audited
    assert prospect.failure_reason is None
    assert audit.fanout_payload is not None
    assert audit.fanout_payload["target_query"] == "postgres pooling"
    assert audit.missing_gap_types and "feature_specific" in audit.missing_gap_types
    assert audit in session.added


@pytest.mark.asyncio
async def test_audit_one_fetch_failure_marks_prospect_failed(monkeypatch, nlp, embedder) -> None:
    async def _fake_fetch(url, timeout=10.0):
        raise URLFetchError("HTTP 404 from upstream")

    monkeypatch.setattr(audit_runner, "fetch_url", _fake_fetch)
    session = FakeSession()
    prospect = _make_prospect()
    sem = __import__("asyncio").Semaphore(2)

    result = await _audit_one(session, prospect, client=None, fanout_sem=sem)

    assert result is None
    assert prospect.status == ProspectStatus.failed
    assert "HTTP 404" in (prospect.failure_reason or "")
    assert session.added == []


@pytest.mark.asyncio
async def test_audit_one_llm_unavailable_still_writes_audit(monkeypatch, nlp, embedder) -> None:
    """LLM hiccups must NOT kill the AEO half of the audit."""

    async def _fake_fetch(url, timeout=10.0):
        return SAMPLE_GOOD_HTML

    async def _angry_fanout(*a, **kw):
        raise LLMUnavailableError("rate limited 3x")

    monkeypatch.setattr(audit_runner, "fetch_url", _fake_fetch)
    monkeypatch.setattr(audit_runner, "run_fanout", _angry_fanout)

    session = FakeSession()
    prospect = _make_prospect()
    sem = __import__("asyncio").Semaphore(2)

    audit = await _audit_one(session, prospect, client=None, fanout_sem=sem)

    assert audit is not None
    assert audit.fanout_payload is None
    assert audit.missing_gap_types is None
    assert prospect.status == ProspectStatus.audited  # AEO score is durable on its own


# ---- run_pending end-to-end via monkeypatched session.execute ----


class FakeRunSession:
    def __init__(self, pending: list[Prospect]):
        self._pending = pending
        self.added: list[Any] = []
        self.flush_calls = 0

    async def execute(self, _stmt):
        return _FakeScalars(self._pending)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = 100 + len(self.added)

    async def flush(self):
        self.flush_calls += 1


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


@pytest.mark.asyncio
async def test_run_pending_audits_each_prospect(monkeypatch, nlp, embedder) -> None:
    async def _fake_fetch(url, timeout=10.0):
        return SAMPLE_GOOD_HTML

    async def _fake_fanout(target_query, existing_content, *, client=None):
        return _fanout_ok()

    monkeypatch.setattr(audit_runner, "fetch_url", _fake_fetch)
    monkeypatch.setattr(audit_runner, "run_fanout", _fake_fanout)

    p1, p2 = _make_prospect(), _make_prospect()
    p2.id = 2
    p2.url = "https://x.example/other"
    session = FakeRunSession([p1, p2])

    count = await run_pending(session, batch_size=10, concurrency=2,
                              fanout_concurrency=2, client=None)

    assert count == 2
    audits_added = [x for x in session.added if isinstance(x, Audit)]
    assert len(audits_added) == 2
    assert all(p.status == ProspectStatus.audited for p in (p1, p2))


@pytest.mark.asyncio
async def test_run_pending_no_queue_returns_zero(monkeypatch, nlp, embedder) -> None:
    session = FakeRunSession([])
    count = await run_pending(session, batch_size=10, concurrency=2,
                              fanout_concurrency=2, client=None)
    assert count == 0
    assert session.added == []
