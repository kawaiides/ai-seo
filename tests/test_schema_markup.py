"""Unit tests for Check E — Schema.org Markup."""

from __future__ import annotations

from app.services.aeo_checks.schema_markup import SchemaMarkupCheck
from app.services.content_parser import parse


def _wrap(inner: str) -> str:
    """Wrap inner HTML with body content so content_parser keeps the soup
    (it switches to plain-text mode when no <p> or h-tag is present)."""
    return f"<html><body>{inner}<p>some body content to keep soup alive.</p></body></html>"


def test_no_schema_returns_zero():
    pc = parse(_wrap(""), "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 0
    assert result.passed is False
    assert result.details["types_found"] == []
    assert result.recommendation is not None
    assert "No Schema.org markup" in result.recommendation


def test_generic_type_only_scores_eight():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"WebPage","name":"My Page"}'
        '</script>'
    ), "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 8
    assert result.details["types_found"] == ["WebPage"]
    assert result.details["high_value_types"] == []
    assert "Generic schema" in (result.recommendation or "")


def test_high_value_type_empty_scores_fourteen():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"FAQPage"}'
        '</script>'
    ), "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 14
    assert result.details["high_value_types"] == ["FAQPage"]
    assert result.details["populated_types"] == []


def test_high_value_type_populated_scores_max():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"FAQPage",'
        '"mainEntity":[{"@type":"Question","name":"Q1",'
        '"acceptedAnswer":{"@type":"Answer","text":"A1"}}]}'
        '</script>'
    ), "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 20
    assert result.passed is True
    assert result.details["populated_types"] == ["FAQPage"]
    assert result.recommendation is None


def test_microdata_high_value_scores_fourteen():
    pc = parse(
        '<html><body><div itemscope itemtype="https://schema.org/Article">'
        '<h1 itemprop="headline">Hi</h1></div><p>body content here.</p></body></html>',
        "text",
    )
    result = SchemaMarkupCheck().run(pc)
    # microdata can't be introspected for property population in this check
    assert result.score == 14
    assert "Article" in result.details["high_value_types"]


def test_graph_container_unwrapped():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"Article","headline":"Hi"},'
        '{"@type":"Person","name":"Author"}]}'
        '</script>'
    ), "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 20
    assert "Article" in result.details["populated_types"]


def test_multi_type_array_recognised():
    pc = parse(_wrap(
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":["Article","NewsArticle"],'
        '"headline":"Breaking news"}'
        '</script>'
    ), "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 20
    assert set(result.details["high_value_types"]) == {"Article", "NewsArticle"}


def test_broken_block_does_not_poison_others():
    pc = parse(_wrap(
        '<script type="application/ld+json">{not valid json</script>'
        '<script type="application/ld+json">'
        '{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"Q"}]}'
        '</script>'
    ), "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 20
    assert result.details["json_ld_blocks"] == 1  # only valid one counted


def test_plaintext_input_skips_scan():
    pc = parse("just plain text with no html structure at all.", "text")
    result = SchemaMarkupCheck().run(pc)
    assert result.score == 0
    assert "Plain-text" in result.details["note"]
