"""Unit tests for site dashboard rollup helpers (Phase C.1).

Pure functions over plain dataclasses — no database required, so these
run in the standard suite alongside the other check-level tests.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services.site.dashboard import (
    AuditRow,
    PageRow,
    compute_missing_clusters,
    compute_summary,
    compute_top_worst,
    compute_trend,
)


def _page(
    page_id: int,
    *,
    score: int | None = None,
    band: str | None = None,
    audited_at: datetime | None = None,
    missing_types: tuple[str, ...] = (),
    failed_checks: tuple[str, ...] = (),
    url: str | None = None,
) -> PageRow:
    return PageRow(
        page_id=page_id,
        url=url or f"https://example.com/page-{page_id}",
        title=f"Page {page_id}",
        last_score=score,
        last_band=band,
        last_audited_at=audited_at,
        last_missing_types=missing_types,
        last_failed_checks=failed_checks,
    )


def test_summary_empty():
    s = compute_summary([])
    assert s.pages_total == 0
    assert s.pages_audited == 0
    assert s.mean_score is None
    assert s.median_score is None
    assert s.band_counts == {}
    assert s.last_audited_at is None


def test_summary_mixes_audited_and_unaudited():
    now = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    pages = [
        _page(1, score=80, band="AEO Optimized", audited_at=now),
        _page(2, score=60, band="Needs Improvement", audited_at=now - timedelta(days=2)),
        _page(3, score=None),  # unaudited
        _page(4, score=40, band="Significant Gaps", audited_at=now - timedelta(days=1)),
    ]
    s = compute_summary(pages)
    assert s.pages_total == 4
    assert s.pages_audited == 3
    assert s.mean_score == 60.0
    assert s.median_score == 60.0
    assert s.band_counts == {
        "AEO Optimized": 1,
        "Needs Improvement": 1,
        "Significant Gaps": 1,
    }
    assert s.last_audited_at == now


def test_trend_fills_zero_days_for_missing_dates():
    today = date(2026, 5, 17)
    audits = [
        AuditRow(
            page_id=1,
            score=70,
            audited_at=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            missing_types=(),
        ),
        AuditRow(
            page_id=2,
            score=90,
            audited_at=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            missing_types=(),
        ),
        AuditRow(
            page_id=3,
            score=50,
            audited_at=datetime.combine(
                today - timedelta(days=3), datetime.min.time(), tzinfo=timezone.utc
            ),
            missing_types=(),
        ),
    ]
    trend = compute_trend(audits, days=7, today=today)
    assert len(trend) == 7
    # today has 2 audits with mean 80
    assert trend[-1].day == today
    assert trend[-1].audits == 2
    assert trend[-1].mean_score == 80.0
    # 3 days back has 1 audit at 50
    assert trend[-4].day == today - timedelta(days=3)
    assert trend[-4].audits == 1
    assert trend[-4].mean_score == 50.0
    # remaining days have no audits and report 0
    quiet_days = [t for t in trend if t.audits == 0]
    assert len(quiet_days) == 5


def test_top_worst_sorted_ascending_capped_at_k():
    pages = [
        _page(1, score=80, band="AEO Optimized"),
        _page(2, score=30, band="Not AEO Ready", failed_checks=("readability", "citations")),
        _page(3, score=55, band="Significant Gaps"),
        _page(4, score=None),  # unaudited should be excluded
        _page(5, score=10, band="Not AEO Ready"),
    ]
    worst = compute_top_worst(pages, k=2)
    assert [w.page_id for w in worst] == [5, 2]
    assert worst[0].score == 10
    assert worst[1].failed_checks == ("readability", "citations")


def test_missing_clusters_counts_descending():
    pages = [
        _page(1, missing_types=("comparative", "how_to")),
        _page(2, missing_types=("comparative",)),
        _page(3, missing_types=("comparative", "trust_signals")),
        _page(4, missing_types=("how_to",)),
    ]
    clusters = compute_missing_clusters(pages, k=10)
    types_in_order = [c.type for c in clusters]
    assert types_in_order[0] == "comparative"  # 3 pages
    assert types_in_order[1] in {"how_to"}  # 2 pages
    counts = {c.type: c.pages_missing for c in clusters}
    assert counts["comparative"] == 3
    assert counts["how_to"] == 2
    assert counts["trust_signals"] == 1


def test_missing_clusters_capped_at_k():
    pages = [
        _page(i, missing_types=(f"type_{i}",)) for i in range(1, 6)
    ]
    clusters = compute_missing_clusters(pages, k=3)
    assert len(clusters) == 3


def test_trend_ignores_audits_outside_window():
    today = date(2026, 5, 17)
    audits = [
        AuditRow(
            page_id=1,
            score=99,
            audited_at=datetime.combine(
                today - timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc
            ),
            missing_types=(),
        )
    ]
    trend = compute_trend(audits, days=7, today=today)
    # No bucket should reflect the 30-day-old audit
    assert all(t.audits == 0 for t in trend)


def test_top_worst_breaks_ties_lexically_by_url():
    pages = [
        _page(1, score=20, band="Not AEO Ready", url="https://example.com/z"),
        _page(2, score=20, band="Not AEO Ready", url="https://example.com/a"),
    ]
    worst = compute_top_worst(pages, k=2)
    assert worst[0].url.endswith("/a")
    assert worst[1].url.endswith("/z")


def test_summary_tracks_latest_audit_across_pages():
    times = [
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
    ]
    pages = [
        _page(i, score=50, band="Needs Improvement", audited_at=t)
        for i, t in enumerate(times, start=1)
    ]
    s = compute_summary(pages)
    assert s.last_audited_at == datetime(2026, 3, 1, tzinfo=timezone.utc)
