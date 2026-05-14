"""Unit tests for Check B — H-tag Hierarchy."""

from __future__ import annotations

from app.services.aeo_checks.htag_hierarchy import HTagHierarchyCheck
from app.services.content_parser import ParsedContent


def _content(h_tags: list[tuple[int, str]]) -> ParsedContent:
    return ParsedContent(
        raw="",
        soup=None,
        first_paragraph="",
        h_tags=h_tags,
        body_text="",
    )


def test_perfect_hierarchy():
    h_tags = [(1, "Title"), (2, "Section"), (3, "Sub"), (2, "Section 2")]
    result = HTagHierarchyCheck().run(_content(h_tags))
    assert result.score == 20
    assert result.passed is True
    assert result.details["violations"] == []
    assert result.details["h_tags_found"] == ["h1", "h2", "h3", "h2"]
    assert result.recommendation is None


def test_skipped_level_one_violation():
    h_tags = [(1, "T"), (3, "Sub")]
    result = HTagHierarchyCheck().run(_content(h_tags))
    assert result.score == 12
    rules = [v["rule"] for v in result.details["violations"]]
    assert rules == ["level_skipped"]


def test_two_h1s_one_extra():
    h_tags = [(1, "T1"), (2, "S"), (1, "T2")]
    result = HTagHierarchyCheck().run(_content(h_tags))
    assert result.score == 12
    rules = [v["rule"] for v in result.details["violations"]]
    assert rules == ["multiple_h1"]


def test_missing_h1_zero_score():
    h_tags = [(2, "S"), (3, "Sub")]
    result = HTagHierarchyCheck().run(_content(h_tags))
    assert result.score == 0
    assert result.passed is False
    rules = [v["rule"] for v in result.details["violations"]]
    assert rules == ["missing_h1"]


def test_pre_h1_tag_one_violation():
    h_tags = [(2, "X"), (1, "T"), (2, "S")]
    result = HTagHierarchyCheck().run(_content(h_tags))
    assert result.score == 12
    rules = [v["rule"] for v in result.details["violations"]]
    assert rules == ["pre_h1_tag"]


def test_three_or_more_violations_zero():
    # h2 (pre-h1) + h1 + h3 (skip from h1->h3) + h1 (extra) = 3 violations
    h_tags = [(2, "X"), (1, "T"), (3, "Sub"), (1, "T2")]
    result = HTagHierarchyCheck().run(_content(h_tags))
    assert result.score == 0
    assert len(result.details["violations"]) >= 3


def test_empty_htags_treated_as_missing_h1():
    result = HTagHierarchyCheck().run(_content([]))
    assert result.score == 0
    rules = [v["rule"] for v in result.details["violations"]]
    assert rules == ["missing_h1"]
