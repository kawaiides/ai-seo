"""Unit tests for citation tracker helpers (Phase C.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.geo.citation_tracker import (
    compute_citation_rate,
    compute_weekly_citation_history,
    matches_site,
)


@dataclass
class _FakeProbe:
    contains_site: bool
    probed_at: datetime


def test_matches_site_exact_host():
    contains, position = matches_site(
        ["https://nih.gov/x", "https://example.com/blog/post"],
        "https://example.com/",
    )
    assert contains is True
    assert position == 2


def test_matches_site_subdomain_match():
    contains, position = matches_site(
        ["https://en.wikipedia.org/wiki/Foo", "https://blog.example.com/post"],
        "https://example.com",
    )
    assert contains is True
    assert position == 2


def test_matches_site_strips_www_prefix():
    contains, position = matches_site(
        ["https://www.example.com/page"],
        "https://example.com",
    )
    assert contains is True


def test_no_match_returns_false():
    contains, position = matches_site(
        ["https://nih.gov/x", "https://nature.com/y"],
        "https://example.com",
    )
    assert contains is False
    assert position is None


def test_compute_citation_rate_window():
    now = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    probes = [
        _FakeProbe(True, now - timedelta(days=2)),
        _FakeProbe(False, now - timedelta(days=5)),
        _FakeProbe(True, now - timedelta(days=10)),
        _FakeProbe(False, now - timedelta(days=40)),  # outside window
    ]
    rate = compute_citation_rate(probes, window_days=30, now=now)
    assert rate.probes == 3
    assert rate.cited == 2
    assert abs(rate.rate - (2 / 3)) < 1e-6


def test_compute_weekly_history_fills_quiet_weeks():
    now = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    probes = [
        _FakeProbe(True, now),
        _FakeProbe(False, now - timedelta(days=1)),
        _FakeProbe(True, now - timedelta(weeks=2)),
    ]
    weekly = compute_weekly_citation_history(probes, weeks=4, now=now)
    assert len(weekly) == 4
    # most recent week should have 2 probes, 1 cited
    last = weekly[-1]
    assert last.probes == 2
    assert last.cited == 1
    # 2 weeks ago: 1 probe, 1 cited
    two_weeks_ago = weekly[-3]
    assert two_weeks_ago.probes == 1
    assert two_weeks_ago.cited == 1
    # one of the four buckets should be empty (rate 0)
    quiet = [w for w in weekly if w.probes == 0]
    assert len(quiet) == 2


def test_compute_weekly_history_ignores_old_probes():
    now = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    probes = [_FakeProbe(True, now - timedelta(weeks=50))]
    weekly = compute_weekly_citation_history(probes, weeks=4, now=now)
    assert all(w.probes == 0 for w in weekly)


def test_compute_citation_rate_empty_input():
    rate = compute_citation_rate([], window_days=30)
    assert rate.probes == 0
    assert rate.cited == 0
    assert rate.rate == 0.0
