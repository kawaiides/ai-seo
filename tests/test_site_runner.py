"""Unit tests for the weekly site re-audit runner (Phase C.2)."""

from __future__ import annotations

from app.autopilot.site_runner import (
    SCORE_DROP_ALERT_THRESHOLD,
    compute_score_drop_alerts_from_rows,
)


def test_no_drop_no_alerts():
    rows = [
        (1, "https://a/", 70, 75),
        (2, "https://b/", 80, 80),
    ]
    assert compute_score_drop_alerts_from_rows(rows) == []


def test_drop_below_threshold_is_ignored():
    rows = [(1, "https://a/", 80, 75)]  # delta = -5
    assert compute_score_drop_alerts_from_rows(rows) == []


def test_drop_at_threshold_triggers_alert():
    rows = [(1, "https://a/", 80, 70)]  # delta = -10 (== threshold)
    out = compute_score_drop_alerts_from_rows(rows)
    assert len(out) == 1
    assert out[0].delta == -10
    assert out[0].prior_score == 80
    assert out[0].new_score == 70


def test_drop_well_past_threshold_triggers_alert():
    rows = [(1, "https://a/", 90, 30)]
    out = compute_score_drop_alerts_from_rows(rows)
    assert len(out) == 1
    assert out[0].delta == -60


def test_threshold_constant_within_documented_range():
    # roadmap says "alert on score drop"; threshold should be a useful magnitude
    assert 5 <= SCORE_DROP_ALERT_THRESHOLD <= 25


def test_custom_threshold_changes_alert_behaviour():
    rows = [(1, "https://a/", 80, 75)]  # delta = -5
    assert compute_score_drop_alerts_from_rows(rows, threshold=5) != []
    assert compute_score_drop_alerts_from_rows(rows, threshold=6) == []


def test_multiple_drops_all_reported():
    rows = [
        (1, "https://a/", 80, 60),
        (2, "https://b/", 75, 74),
        (3, "https://c/", 90, 40),
    ]
    out = compute_score_drop_alerts_from_rows(rows)
    assert [a.page_id for a in out] == [1, 3]
