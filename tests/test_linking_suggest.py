"""Unit tests for link suggestion (Phase B.3)."""

from __future__ import annotations

import pytest

from app.models.schemas import IntentCluster, SubQueryResponse
from app.services.linking.page_index import (
    PageRecord,
    build_page_index_from_records,
)
from app.services.linking.suggest import (
    _normalise_url,
    suggest_links_for_clusters,
    suggest_links_for_subqueries,
)


def _r(url: str, title: str | None, excerpt: str) -> PageRecord:
    return PageRecord(url=url, title=title, excerpt=excerpt)


def _site_index():
    return build_page_index_from_records(
        [
            _r(
                "https://example.com/python-vs-go",
                "Python vs Go for backend services",
                "Python and Go are popular choices for backend services; performance and developer experience differ markedly.",
            ),
            _r(
                "https://example.com/django-guide",
                "Build your first Django app",
                "Django is a Python web framework with batteries-included models, views, and templates.",
            ),
            _r(
                "https://example.com/sourdough",
                "Classic sourdough bread recipe",
                "Sourdough relies on a wild yeast starter and long fermentation for flavour and crust.",
            ),
            _r(
                "https://example.com/kubernetes",
                "Kubernetes for beginners",
                "Kubernetes orchestrates container deployment across clusters of nodes for cloud-native systems.",
            ),
        ]
    )


@pytest.mark.usefixtures("embedder")
def test_empty_inputs_return_empty():
    index = _site_index()
    assert suggest_links_for_subqueries([], index) == []
    empty = build_page_index_from_records([])
    assert suggest_links_for_subqueries(["query"], empty) == []


@pytest.mark.usefixtures("embedder")
def test_top_k_returns_at_most_k_per_subquery():
    suggestions = suggest_links_for_subqueries(
        ["best Python web frameworks for beginners"],
        _site_index(),
        top_k=2,
    )
    assert 0 < len(suggestions) <= 2


@pytest.mark.usefixtures("embedder")
def test_source_url_is_excluded_from_suggestions():
    suggestions = suggest_links_for_subqueries(
        ["learn Django step by step"],
        _site_index(),
        source_url="https://example.com/django-guide",
        top_k=3,
    )
    assert all(s.target_url != "https://example.com/django-guide" for s in suggestions)


@pytest.mark.usefixtures("embedder")
def test_min_similarity_filters_noise():
    suggestions = suggest_links_for_subqueries(
        ["random unrelated cricket scoring rules"],
        _site_index(),
        min_similarity=0.95,
        top_k=3,
    )
    assert suggestions == []


@pytest.mark.usefixtures("embedder")
def test_anchor_text_prefers_page_title():
    suggestions = suggest_links_for_subqueries(
        ["Python web framework Django tutorial"],
        _site_index(),
        top_k=1,
        min_similarity=0.30,
    )
    assert suggestions
    assert suggestions[0].anchor_text == suggestions[0].title


@pytest.mark.usefixtures("embedder")
def test_cluster_helper_skips_covered_clusters():
    index = _site_index()
    clusters = [
        IntentCluster(
            cluster_id=0,
            dominant_type="how_to",
            label="how to install Kubernetes on bare metal",
            member_indices=[0],
            member_count=1,
            covered_count=0,
        ),
        IntentCluster(
            cluster_id=1,
            dominant_type="definitional",
            label="what is a CRM",
            member_indices=[1],
            member_count=1,
            covered_count=2,  # already covered → skip
        ),
    ]
    suggestions = suggest_links_for_clusters(
        clusters, sub_queries_resolved=[], index=index, top_k=2
    )
    cluster_ids = {s.matched_cluster_id for s in suggestions}
    assert cluster_ids == {0}


@pytest.mark.usefixtures("embedder")
def test_cluster_ids_length_mismatch_raises():
    with pytest.raises(ValueError):
        suggest_links_for_subqueries(
            ["a", "b"], _site_index(), cluster_ids=[1]
        )


def test_normalise_url_handles_trailing_slash_and_fragment():
    a = _normalise_url("https://Example.com/path/")
    b = _normalise_url("https://example.com/path")
    c = _normalise_url("https://example.com/path#section")
    assert a == b == c
