"""Unit tests for site ingest pure helpers (Phase C.1).

The DB-touching paths are exercised by integration tests (alembic + pg).
Here we test the canonicalisation + dedupe primitives the service uses.
"""

from __future__ import annotations

import pytest

from app.services.site.ingest import (
    _canonical_root,
    _dedupe_preserving_order,
)


def test_canonical_root_lowercases_host():
    assert (
        _canonical_root("HTTPS://Example.COM/")
        == "https://example.com"
    )


def test_canonical_root_strips_trailing_slash():
    assert _canonical_root("https://example.com/blog/") == "https://example.com/blog"


def test_canonical_root_drops_query_and_fragment():
    assert (
        _canonical_root("https://example.com/blog?utm=x#nav")
        == "https://example.com/blog"
    )


def test_canonical_root_requires_scheme():
    with pytest.raises(ValueError):
        _canonical_root("example.com/blog")


def test_dedupe_preserves_order():
    urls = [
        "https://a.com/1",
        "https://a.com/2",
        "https://a.com/1",
        "https://a.com/3",
    ]
    assert _dedupe_preserving_order(urls) == [
        "https://a.com/1",
        "https://a.com/2",
        "https://a.com/3",
    ]


def test_dedupe_drops_blank_strings():
    urls = ["", "  ", "https://a.com/1", "  https://a.com/1  "]
    out = _dedupe_preserving_order(urls)
    # whitespace-padded URL stripped → matches the prior entry → dedupes
    assert out == ["https://a.com/1"]


def test_dedupe_handles_empty_input():
    assert _dedupe_preserving_order([]) == []
