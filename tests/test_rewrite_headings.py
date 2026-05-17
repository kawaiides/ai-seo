"""Unit tests for heading-hierarchy auto-fix (Phase B.1)."""

from __future__ import annotations

from app.services.rewrite.headings import autofix_headings, to_html


def test_empty_input_is_noop():
    result = autofix_headings([])
    assert result["fixed"] == []
    assert result["operations"] == []


def test_clean_hierarchy_unchanged():
    tags = [(1, "Title"), (2, "Section"), (3, "Sub"), (2, "Other")]
    result = autofix_headings(tags)
    assert result["fixed"] == tags
    assert result["operations"] == []


def test_missing_h1_promotes_first_heading():
    tags = [(2, "Intro"), (2, "Body"), (3, "Detail")]
    result = autofix_headings(tags)
    assert result["fixed"][0] == (1, "Intro")
    rules = [op["rule"] for op in result["operations"]]
    assert rules == ["promote_to_h1"]


def test_multiple_h1_demotes_all_but_first():
    tags = [(1, "Title"), (1, "Other"), (2, "Sub"), (1, "Another")]
    result = autofix_headings(tags)
    assert result["fixed"][0] == (1, "Title")
    h1_count = sum(1 for level, _ in result["fixed"] if level == 1)
    assert h1_count == 1
    demotions = [op for op in result["operations"] if op["rule"] == "demote_extra_h1"]
    assert len(demotions) == 2


def test_pre_h1_headings_demoted_to_h2():
    tags = [(3, "Ad block"), (1, "Real title"), (2, "Section")]
    result = autofix_headings(tags)
    assert result["fixed"] == [(2, "Ad block"), (1, "Real title"), (2, "Section")]
    demotions = [op for op in result["operations"] if op["rule"] == "demote_pre_h1"]
    assert len(demotions) == 1


def test_pre_h1_h2_already_at_target_is_silent():
    # Pre-H1 H2 is technically a violation in Check B's view, but the
    # auto-fix's *level* change is a no-op. Don't fabricate an op entry.
    tags = [(2, "Sidebar"), (1, "Title"), (2, "Section")]
    result = autofix_headings(tags)
    assert result["fixed"] == [(2, "Sidebar"), (1, "Title"), (2, "Section")]
    assert result["operations"] == []


def test_skipped_levels_collapse_one_step_at_a_time():
    tags = [(1, "Title"), (3, "Sub-sub"), (5, "Way deep")]
    result = autofix_headings(tags)
    assert result["fixed"] == [(1, "Title"), (2, "Sub-sub"), (3, "Way deep")]
    skips = [op for op in result["operations"] if op["rule"] == "collapse_skip"]
    assert len(skips) == 2


def test_combined_violations_resolve_in_dependency_order():
    # Missing H1 + skip + pre-h1-as-H3 — should yield a clean ladder.
    tags = [(3, "Promote me"), (2, "Body"), (4, "Deep")]
    result = autofix_headings(tags)
    # promote_to_h1: (3,"Promote me") → (1,"Promote me")
    # then (1,"Promote me"), (2,"Body"), (4,"Deep")
    # collapse_skip: 2 → 4 collapses to 3
    assert result["fixed"] == [(1, "Promote me"), (2, "Body"), (3, "Deep")]
    rules = [op["rule"] for op in result["operations"]]
    assert "promote_to_h1" in rules
    assert "collapse_skip" in rules


def test_to_html_escapes_special_chars():
    fixed = [(1, "AT&T <Inc.>"), (2, "Notes")]
    html = to_html(fixed)
    assert "<h1>AT&amp;T &lt;Inc.&gt;</h1>" in html
    assert "<h2>Notes</h2>" in html


def test_to_html_empty_input():
    assert to_html([]) == ""
