"""Unit tests for page_index (Phase B.3)."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.linking.page_index import (
    PageRecord,
    build_page_index_from_records,
)


def _r(url: str, title: str | None, excerpt: str) -> PageRecord:
    return PageRecord(url=url, title=title, excerpt=excerpt)


@pytest.mark.usefixtures("embedder")
def test_empty_records_yield_empty_index():
    index = build_page_index_from_records([])
    assert index.pages == []
    assert index.vectors.shape == (0, 1)
    assert index.top_k(np.zeros(384), k=3) == []


@pytest.mark.usefixtures("embedder")
def test_records_with_blank_text_are_dropped():
    index = build_page_index_from_records(
        [
            _r("https://a/", "", ""),
            _r("https://b/", "Real title", "Real body text"),
        ]
    )
    urls = [p.url for p in index.pages]
    assert urls == ["https://b/"]


@pytest.mark.usefixtures("embedder")
def test_top_k_retrieves_semantically_close_pages():
    records = [
        _r(
            "https://a/python-vs-go",
            "Python vs Go for backend services",
            "Python and Go are popular choices for backend services; performance and developer experience differ markedly.",
        ),
        _r(
            "https://a/baking",
            "Classic sourdough bread recipe",
            "Sourdough relies on a wild yeast starter and a long fermentation for its flavour and crust.",
        ),
        _r(
            "https://a/django-tutorial",
            "Build your first Django app",
            "Django is a Python web framework with batteries-included models, views, and templates for web apps.",
        ),
    ]
    index = build_page_index_from_records(records)
    from app.services.embeddings import get_embedder

    qv = get_embedder().encode(
        ["best Python web frameworks"], normalize_embeddings=True, convert_to_numpy=True
    )[0]
    top = index.top_k(qv, k=2)
    top_urls = [p.url for p, _ in top]
    # Django and Python-vs-Go are both clearly closer than the bread page
    assert "https://a/baking" not in top_urls
    assert top[0][1] > top[1][1]  # sorted descending


@pytest.mark.usefixtures("embedder")
def test_top_k_zero_returns_empty_list():
    index = build_page_index_from_records(
        [_r("https://a/", "title", "body content here")]
    )
    qv = index.vectors[0]
    assert index.top_k(qv, k=0) == []


@pytest.mark.usefixtures("embedder")
def test_record_embedding_text_joins_title_and_excerpt():
    rec = _r("https://a/", "Title", "Body")
    assert "Title" in rec.embedding_text
    assert "Body" in rec.embedding_text


def test_record_embedding_text_handles_missing_title():
    rec = _r("https://a/", None, "Body only")
    assert rec.embedding_text == "Body only"


def test_record_embedding_text_handles_missing_excerpt():
    rec = _r("https://a/", "Title only", "")
    assert rec.embedding_text == "Title only"
