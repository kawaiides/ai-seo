"""Unit tests for Fan-Out v2 — Intent clustering."""

from __future__ import annotations

import numpy as np
import pytest

from app.models.schemas import LLMSubQuery, SubQueryResponse
from app.services.fanout_v2 import (
    CLUSTER_THRESHOLD,
    _single_link_cluster,
    cluster_subqueries,
)


def _q(type_: str, query: str) -> LLMSubQuery:
    return LLMSubQuery.model_validate({"type": type_, "query": query})


def test_empty_input_returns_empty():
    assert cluster_subqueries([], query_vecs=np.empty((0, 1))) == []


def test_single_link_union_find_threshold():
    # 4 vectors: 0~1 above threshold, 2~3 above threshold, but 0/1 vs 2/3 below.
    sim = np.array(
        [
            [1.0, 0.7, 0.2, 0.1],
            [0.7, 1.0, 0.2, 0.1],
            [0.2, 0.2, 1.0, 0.8],
            [0.1, 0.1, 0.8, 1.0],
        ]
    )
    cluster_of = _single_link_cluster(sim, threshold=0.5)
    # Two distinct clusters: {0,1}, {2,3}.
    assert cluster_of[0] == cluster_of[1]
    assert cluster_of[2] == cluster_of[3]
    assert cluster_of[0] != cluster_of[2]


def test_single_link_chains_through_intermediate():
    # 0—1 link, 1—2 link, 0—2 below threshold → all three still cluster (single-link).
    sim = np.array(
        [
            [1.0, 0.7, 0.3],
            [0.7, 1.0, 0.7],
            [0.3, 0.7, 1.0],
        ]
    )
    cluster_of = _single_link_cluster(sim, threshold=0.5)
    assert cluster_of[0] == cluster_of[1] == cluster_of[2]


def test_no_edges_means_singletons():
    sim = np.eye(3)
    cluster_of = _single_link_cluster(sim, threshold=0.5)
    assert len(set(cluster_of)) == 3


@pytest.mark.usefixtures("embedder")
def test_real_embeddings_cluster_semantically_similar_queries():
    sub_queries = [
        _q("comparative", "HubSpot vs Salesforce for small business"),
        _q("comparative", "HubSpot or Salesforce as a CRM for SMB teams"),
        _q("how_to", "how to migrate contact data into a new CRM"),
        _q("how_to", "how to import CSV contacts into a CRM tool"),
        _q("definitional", "what is a CRM"),
    ]
    clusters = cluster_subqueries(sub_queries)
    # HubSpot/Salesforce pair should land in the same cluster.
    by_member = {ci: c.cluster_id for c in clusters for ci in c.member_indices}
    assert by_member[0] == by_member[1]
    # Migrate-vs-import pair should also cluster together.
    assert by_member[2] == by_member[3]


@pytest.mark.usefixtures("embedder")
def test_dominant_type_reflects_most_common_member_type():
    sub_queries = [
        _q("comparative", "alpha vs beta"),
        _q("comparative", "alpha or beta head to head"),
        _q("how_to", "how to use alpha"),  # likely separate cluster
    ]
    clusters = cluster_subqueries(sub_queries)
    # Find the cluster containing index 0 (alpha vs beta), its dominant_type
    # should be "comparative".
    for c in clusters:
        if 0 in c.member_indices:
            assert c.dominant_type == "comparative"
            assert "alpha" in c.label.lower()
            break
    else:
        pytest.fail("expected a cluster containing query index 0")


@pytest.mark.usefixtures("embedder")
def test_coverage_stats_attached_when_scored_provided():
    sub_queries = [
        _q("comparative", "HubSpot vs Salesforce"),
        _q("comparative", "HubSpot or Salesforce head to head"),
        _q("how_to", "how to migrate CRM data"),
    ]
    scored = [
        SubQueryResponse(type="comparative", query="HubSpot vs Salesforce",
                         covered=True, similarity_score=0.81),
        SubQueryResponse(type="comparative", query="HubSpot or Salesforce head to head",
                         covered=False, similarity_score=0.62),
        SubQueryResponse(type="how_to", query="how to migrate CRM data",
                         covered=False, similarity_score=0.40),
    ]
    clusters = cluster_subqueries(sub_queries, scored=scored)
    for c in clusters:
        assert c.covered_count is not None
        assert c.coverage_percent is not None
        assert 0 <= c.coverage_percent <= 100


@pytest.mark.usefixtures("embedder")
def test_label_truncates_to_max_length():
    long_query = "x " * 100
    sub_queries = [_q("definitional", long_query.strip())]
    clusters = cluster_subqueries(sub_queries)
    assert len(clusters) == 1
    assert len(clusters[0].label) <= 60


def test_threshold_constant_in_documented_range():
    # Roadmap commits to threshold-cut clustering; a value below 0 or above 1
    # would silently misbehave (single cluster / all singletons).
    assert 0.3 <= CLUSTER_THRESHOLD <= 0.8
