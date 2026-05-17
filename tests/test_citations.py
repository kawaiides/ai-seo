"""Unit tests for Check F — Citation Density."""

from __future__ import annotations

from app.services.aeo_checks.citations import (
    AUTHORITATIVE_DOMAINS,
    CitationsCheck,
    _is_authoritative,
)
from app.services.content_parser import parse


def _body(links: str, word_count: int = 1000) -> str:
    words = "word " * word_count
    return f"<html><body><p>{words}</p>{links}</body></html>"


def test_below_min_word_count_returns_zero():
    pc = parse(_body("", word_count=20), "text")
    result = CitationsCheck().run(pc)
    assert result.score == 0
    assert "Insufficient text" in result.details["note"]
    assert result.recommendation is not None


def test_no_links_returns_zero():
    pc = parse(_body(""), "text")
    result = CitationsCheck().run(pc)
    assert result.score == 0
    assert result.details["authoritative_count"] == 0
    assert "No links to authoritative sources" in (result.recommendation or "")


def test_only_internal_and_fragment_links_score_zero():
    pc = parse(_body(
        '<a href="/about">about</a>'
        '<a href="#section">section</a>'
        '<a href="mailto:x@y.com">mail</a>'
    ), "text")
    result = CitationsCheck().run(pc)
    assert result.score == 0
    assert result.details["total_external_links"] == 0


def test_non_authoritative_external_does_not_score():
    pc = parse(_body(
        '<a href="https://random-blog.example.com">a</a>'
        '<a href="https://my-other-site.com">b</a>'
    ), "text")
    result = CitationsCheck().run(pc)
    assert result.details["total_external_links"] == 2
    assert result.details["authoritative_count"] == 0
    assert result.score == 0


def test_one_authoritative_link_hits_low_band():
    pc = parse(_body('<a href="https://nasa.gov/x">a</a>'), "text")
    result = CitationsCheck().run(pc)
    assert result.details["authoritative_count"] == 1
    assert result.score == 8  # 1 count + density ≥ 0.5


def test_two_authoritative_links_hit_mid_band():
    pc = parse(_body(
        '<a href="https://en.wikipedia.org/x">a</a>'
        '<a href="https://nih.gov/y">b</a>'
    ), "text")
    result = CitationsCheck().run(pc)
    assert result.details["authoritative_count"] == 2
    assert result.score == 14


def test_four_authoritative_links_hit_max():
    pc = parse(_body(
        '<a href="https://en.wikipedia.org/A">a</a>'
        '<a href="https://nih.gov/x">b</a>'
        '<a href="https://nature.com/y">c</a>'
        '<a href="https://harvard.edu/z">d</a>'
    ), "text")
    result = CitationsCheck().run(pc)
    assert result.details["authoritative_count"] == 4
    assert result.score == 20
    assert result.passed is True
    assert result.recommendation is None


def test_subdomain_of_whitelisted_domain_matches():
    assert _is_authoritative("en.wikipedia.org")
    assert _is_authoritative("scholar.harvard.edu")


def test_tld_suffix_matching():
    assert _is_authoritative("cam.ac.uk")
    assert _is_authoritative("www.gov.uk")
    assert _is_authoritative("data.gov.in")


def test_evilgov_does_not_match_dot_gov_suffix():
    # ".gov" suffix must match on a dot boundary
    assert not _is_authoritative("evilgov.com")
    assert not _is_authoritative("notagov.example.com")


def test_plaintext_input_skips_scan():
    long_plain = " ".join(["word"] * 200)
    pc = parse(long_plain, "text")
    result = CitationsCheck().run(pc)
    assert result.score == 0
    assert "Plain-text" in result.details["note"]


def test_whitelist_is_non_empty_and_lowercase():
    assert len(AUTHORITATIVE_DOMAINS) >= 20
    for d in AUTHORITATIVE_DOMAINS:
        assert d == d.lower(), f"{d} is not lowercase"
