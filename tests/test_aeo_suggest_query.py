"""Unit tests for `_suggest_target_query` in app/api/aeo.py."""

from __future__ import annotations

from app.api.aeo import _normalise_suggested, _suggest_target_query
from app.services.content_parser import parse


def test_suggest_uses_first_h1_when_present():
    html = "<html><body><h1>Best Python web frameworks for 2026</h1><p>Body content.</p></body></html>"
    pc = parse(html, "text")
    suggested = _suggest_target_query(pc)
    assert suggested == "Best Python web frameworks for 2026"


def test_suggest_strips_common_prefixes():
    html = "<html><body><h1>How to migrate a CRM to Python</h1><p>x.</p></body></html>"
    pc = parse(html, "text")
    suggested = _suggest_target_query(pc)
    assert suggested == "migrate a CRM to Python"


def test_suggest_falls_back_to_title_tag():
    html = (
        "<html><head><title>State of microservice deployment in 2026</title></head>"
        "<body><h2>Sub</h2><p>body</p></body></html>"
    )
    pc = parse(html, "text")
    suggested = _suggest_target_query(pc)
    assert suggested == "State of microservice deployment in 2026"


def test_suggest_falls_back_to_first_sentence():
    html = (
        "<html><body><p>Python is a high-level programming language. It is used widely.</p></body></html>"
    )
    pc = parse(html, "text")
    suggested = _suggest_target_query(pc)
    assert suggested is not None
    assert suggested.startswith("Python is a high-level")
    # First sentence only — second sentence dropped
    assert "It is used widely" not in suggested


def test_suggest_returns_none_when_signal_too_weak():
    html = "<html><body><h1>Hi</h1></body></html>"  # only 1 word after strip
    pc = parse(html, "text")
    assert _suggest_target_query(pc) is None


def test_suggest_truncates_at_max_length():
    long = "x " * 150
    assert _normalise_suggested(long).endswith("…")
    assert len(_normalise_suggested(long)) <= 200


def test_suggest_returns_none_for_empty_parsed_content():
    from app.services.content_parser import ParsedContent

    empty = ParsedContent(raw="", soup=None, first_paragraph="", h_tags=[], body_text="")
    assert _suggest_target_query(empty) is None


def test_suggest_skips_h2_h3_only_documents():
    html = "<html><body><h2>This is a section</h2><h3>Subsection</h3><p>body content.</p></body></html>"
    pc = parse(html, "text")
    suggested = _suggest_target_query(pc)
    # No H1, no <title>, but first_paragraph has "body content." → 2 words → returned
    assert suggested == "body content."


def test_normalise_collapses_internal_whitespace():
    assert _normalise_suggested("foo   bar   baz") == "foo bar baz"


def test_normalise_returns_none_on_single_word():
    assert _normalise_suggested("Hello") is None
    assert _normalise_suggested("") is None
    assert _normalise_suggested(None) is None  # type: ignore[arg-type]
