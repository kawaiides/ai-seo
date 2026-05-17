"""Fan-Out v2 — Intent clustering for generated sub-queries.

Single-link agglomerative clustering over MiniLM embeddings, with cosine
similarity as the link criterion. Threshold tuned to favour recall on
intent grouping (0.55) — produces clusters that surface "comparative
across competitor X axis" or "how-to for migration" as distinct intent
buckets, rather than the per-type bag the v1 summary returns.

Why not UMAP+HDBSCAN as the roadmap suggests:

  - At N=10–15 sub-queries per request, the centroidal density that
    HDBSCAN exploits doesn't have enough samples to be reliable.
  - UMAP's dimensionality reduction is overhead with no payoff at this
    scale; cosine on 384-d MiniLM vectors is already a meaningful metric.
  - Single-link agglomerative on a similarity-threshold cut is
    deterministic, dependency-free, and easy to test.

The roadmap upgrade path (richer corpora, batched cross-request
clustering for site-level dashboards) keeps UMAP+HDBSCAN on the table —
this v2 just covers the per-request case cleanly.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

import numpy as np

from app.models.schemas import (
    IntentCluster,
    LLMSubQuery,
    SubQueryResponse,
    SubQueryType,
)
from app.services.embeddings import get_embedder

CLUSTER_THRESHOLD: float = 0.55
LABEL_MAX_LEN: int = 60


def cluster_subqueries(
    sub_queries: list[LLMSubQuery],
    *,
    scored: list[SubQueryResponse] | None = None,
    query_vecs: np.ndarray | None = None,
) -> list[IntentCluster]:
    """Group sub-queries into intent clusters.

    `query_vecs` is an optional pre-computed (N, dim) array of normalised
    embeddings — pass it when the caller has already encoded the queries
    (e.g. the gap analyzer) to avoid encoding twice.
    """
    if not sub_queries:
        return []

    if query_vecs is None:
        query_vecs = embed_queries(sub_queries)

    similarity = query_vecs @ query_vecs.T
    cluster_of = _single_link_cluster(similarity, CLUSTER_THRESHOLD)
    return _build_clusters(sub_queries, cluster_of, scored)


def embed_queries(sub_queries: list[LLMSubQuery]) -> np.ndarray:
    embedder = get_embedder()
    return embedder.encode(
        [sq.query for sq in sub_queries],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )


def _single_link_cluster(similarity: np.ndarray, threshold: float) -> list[int]:
    """Union-find groups where any pair sims ≥ threshold ends up linked."""
    n = similarity.shape[0]
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if similarity[i, j] >= threshold:
                union(i, j)
    return [find(i) for i in range(n)]


def _build_clusters(
    sub_queries: list[LLMSubQuery],
    cluster_of: list[int],
    scored: list[SubQueryResponse] | None,
) -> list[IntentCluster]:
    groups: dict[int, list[int]] = {}
    for idx, root in enumerate(cluster_of):
        groups.setdefault(root, []).append(idx)

    # Order clusters by descending size, then by first-member index for
    # stable output across runs with identical similarity matrices.
    ordered_roots = sorted(
        groups, key=lambda r: (-len(groups[r]), min(groups[r]))
    )

    out: list[IntentCluster] = []
    for cluster_id, root in enumerate(ordered_roots):
        members = sorted(groups[root])
        dominant_type = _dominant_type(sub_queries[i].type for i in members)
        label = _label_for(sub_queries, members)
        covered_count: int | None = None
        coverage_percent: int | None = None
        if scored is not None:
            covered_count = sum(
                1 for i in members if scored[i].covered is True
            )
            coverage_percent = (
                round(covered_count / len(members) * 100) if members else 0
            )
        out.append(
            IntentCluster(
                cluster_id=cluster_id,
                dominant_type=dominant_type,
                label=label,
                member_indices=members,
                member_count=len(members),
                covered_count=covered_count,
                coverage_percent=coverage_percent,
            )
        )
    return out


def _dominant_type(types: Iterable[SubQueryType]) -> SubQueryType:
    counts = Counter(types)
    # `most_common(1)` ties are broken by first occurrence — fine, but to
    # make output deterministic on tie, fall back to lexical ordering.
    top_count = max(counts.values())
    candidates = sorted(t for t, c in counts.items() if c == top_count)
    return candidates[0]


def _label_for(sub_queries: list[LLMSubQuery], members: list[int]) -> str:
    """Use the first (lowest-index) member's query as the label.

    A real production label would benefit from an LLM summary, but at
    N≤4 members per cluster the first-member query is already a good
    human-readable handle and adds zero LLM cost.
    """
    rep = sub_queries[members[0]].query.strip()
    if len(rep) <= LABEL_MAX_LEN:
        return rep
    return rep[: LABEL_MAX_LEN - 1].rstrip() + "…"
