"""Unit tests for the content parser."""

from __future__ import annotations

import pytest

from app.services.content_parser import ContentParseError, parse


def test_html_extracts_first_paragraph_and_h_tags():
    html = """
    <html><body>
      <h1>Title</h1>
      <p>This is the first paragraph.</p>
      <h2>Section</h2>
      <p>Second paragraph.</p>
    </body></html>
    """
    parsed = parse(html, input_type="text")
    assert parsed.first_paragraph == "This is the first paragraph."
    assert parsed.h_tags == [(1, "Title"), (2, "Section")]


def test_boilerplate_stripped_from_body_text():
    html = """
    <html><body>
      <nav>NAV_JUNK skip me</nav>
      <article>
        <h1>Title</h1>
        <p>Real article content goes here.</p>
      </article>
      <footer>FOOTER_JUNK skip me</footer>
    </body></html>
    """
    parsed = parse(html, input_type="text")
    assert "Real article content" in parsed.body_text
    assert "NAV_JUNK" not in parsed.body_text
    assert "FOOTER_JUNK" not in parsed.body_text


def test_plain_text_input_no_h_tags():
    text = "First paragraph here.\n\nSecond paragraph here."
    parsed = parse(text, input_type="text")
    assert parsed.first_paragraph == "First paragraph here."
    assert parsed.h_tags == []
    assert "First paragraph" in parsed.body_text
    assert "Second paragraph" in parsed.body_text


def test_empty_input_raises_parse_error():
    with pytest.raises(ContentParseError):
        parse("   ", input_type="text")


def test_main_used_when_no_article():
    html = """
    <html><body>
      <header>HEADER_JUNK</header>
      <main>
        <h1>Title</h1>
        <p>Main content.</p>
      </main>
    </body></html>
    """
    parsed = parse(html, input_type="text")
    assert "Main content" in parsed.body_text
    assert "HEADER_JUNK" not in parsed.body_text
