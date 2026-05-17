"""Unit tests for Check G — Freshness Signals."""

from __future__ import annotations

from datetime import date

from app.services.aeo_checks.freshness import FreshnessCheck
from app.services.content_parser import parse

TODAY = date(2026, 5, 17)


def _wrap(head_inner: str, body_inner: str = "body content for parsing.") -> str:
    return f"<html><head>{head_inner}</head><body><p>{body_inner}</p></body></html>"


def test_no_date_signals_returns_zero():
    pc = parse(_wrap(""), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 0
    assert result.details["date_modified"] is None
    assert result.details["date_published"] is None
    assert result.recommendation is not None


def test_modified_within_six_months_scores_max():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@type":"Article","dateModified":"2026-03-01",'
        '"datePublished":"2024-01-01"}'
        '</script>'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 20
    assert result.passed is True
    assert result.details["source"] == "json_ld.dateModified"


def test_modified_within_twelve_months_scores_fourteen():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@type":"Article","dateModified":"2025-08-15"}'
        '</script>'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 14


def test_modified_within_two_years_scores_eight():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@type":"Article","dateModified":"2024-06-01"}'
        '</script>'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 8


def test_modified_older_than_two_years_scores_two():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@type":"Article","dateModified":"2020-01-01"}'
        '</script>'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 2


def test_only_published_caps_max_at_fourteen():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@type":"Article","datePublished":"2026-04-01"}'
        '</script>'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    # Recent but datePublished only — capped at 14 to reward dateModified.
    assert result.score == 14
    assert result.details["date_modified"] is None
    assert result.details["date_published"] == "2026-04-01"


def test_meta_tag_falls_back_when_no_json_ld():
    pc = parse(_wrap(
        '<meta property="article:modified_time" content="2026-04-15T10:00:00Z">'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 20
    assert result.details["source"] == "meta.article:modified_time"


def test_time_tag_used_when_no_metadata():
    pc = parse(
        '<html><body><time datetime="2026-01-15"></time>'
        '<p>body text content here for parsing</p></body></html>',
        "text",
    )
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.details["source"] == "time_tag"
    assert result.details["date_published"] == "2026-01-15"


def test_body_year_drift_surfaced_in_details():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@type":"Article","dateModified":"2026-04-01"}'
        '</script>',
        body_inner="As of 2019, this guide is current. See 2020 for context.",
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 20  # metadata is recent
    assert result.details["stalest_body_year"] == 2019
    # body-year drift hint still appears in details for surfacing
    assert 2019 in result.details["body_years"]


def test_graph_container_unwrapped_for_dates():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":[{"@type":"Article",'
        '"dateModified":"2026-02-10"}]}'
        '</script>'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 20
    assert result.details["source"] == "json_ld.dateModified"


def test_future_dated_modified_treated_as_today():
    # Editorial pipelines that pre-date content shouldn't flip the metric.
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@type":"Article","dateModified":"2027-01-01"}'
        '</script>'
    ), "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 20
    assert result.details["age_days"] < 0


def test_plaintext_input_returns_zero():
    pc = parse("just plain prose with no html markers.", "text")
    result = FreshnessCheck(today=TODAY).run(pc)
    assert result.score == 0
