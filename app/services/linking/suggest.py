"""Top-K internal-link suggestions for a list of missing sub-queries.

Embeds each sub-query (or cluster label), runs top-K against the
`PageIndex`, and returns a list of `LinkSuggestion`s with a suggested
anchor text. Source URL (the page being audited) is excluded from
candidates so we don't suggest self-links.

`anchor_text` heuristic, v0:
  1. Prefer the page's own title when available (shorter, cleaner).
  2. Fall back to the sub-query itself, truncated to a sensible length.

A real product would A/B between these and an LLM-rewritten anchor —
left as a Sprint 7+ improvement once we have click telemetry.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import numpy as np

from app.models.schemas import (
    IntentCluster,
    LinkSuggestion,
    SubQueryResponse,
)
from app.services.embeddings import get_embedder
from app.services.linking.page_index import PageIndex

DEFAULT_TOP_K = 3
DEFAULT_MIN_SIMILARITY = 0.40  # below this, we'd be linking on noise
DEFAULT_ANCHOR_MAX_LEN = 72


def suggest_links_for_subqueries(
    sub_queries: list[str],
    index: PageIndex,
    *,
    source_url: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    cluster_ids: list[int | None] | None = None,
) -> list[LinkSuggestion]:
    """Produce up to `top_k` link suggestions per sub-query.

    `cluster_ids` (optional, must match `sub_queries` in length) lets a
    caller tag each suggestion back to the intent cluster it came from —
    used by `suggest_links_for_clusters` to keep cluster context intact.
    """
    if not sub_queries or len(index.pages) == 0:
        return []
    if cluster_ids is not None and len(cluster_ids) != len(sub_queries):
        raise ValueError(
            "cluster_ids must be the same length as sub_queries when provided"
        )

    embedder = get_embedder()
    query_vecs = embedder.encode(
        sub_queries, normalize_embeddings=True, convert_to_numpy=True
    )

    source_normalised = _normalise_url(source_url)

    out: list[LinkSuggestion] = []
    for i, sq in enumerate(sub_queries):
        vec = query_vecs[i]
        scored = index.top_k(vec, k=top_k + 1)  # +1 to allow dropping source
        for page, score in scored:
            if source_normalised and _normalise_url(page.url) == source_normalised:
                continue
            if score < min_similarity:
                continue
            anchor = _anchor_for(page.title, sq)
            out.append(
                LinkSuggestion(
                    target_url=page.url,
                    title=page.title,
                    anchor_text=anchor,
                    similarity_score=round(float(score), 3),
                    matched_subquery=sq,
                    matched_cluster_id=(
                        cluster_ids[i] if cluster_ids is not None else None
                    ),
                )
            )
            if (
                sum(1 for s in out if s.matched_subquery == sq)
                >= top_k
            ):
                break
    return out


def suggest_links_for_clusters(
    clusters: list[IntentCluster],
    sub_queries_resolved: list[SubQueryResponse],
    index: PageIndex,
    *,
    source_url: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[LinkSuggestion]:
    """Produce link suggestions for the *missing* clusters only.

    A cluster is "missing" when `covered_count` is 0 (or unknown, when
    no scoring context was supplied). For each missing cluster we query
    on the cluster's label rather than every sub-query inside it — one
    suggestion batch per intent gap is the unit our UI wants.
    """
    target_queries: list[str] = []
    target_cluster_ids: list[int | None] = []
    for cluster in clusters:
        if cluster.covered_count is not None and cluster.covered_count > 0:
            continue
        target_queries.append(cluster.label)
        target_cluster_ids.append(cluster.cluster_id)
    return suggest_links_for_subqueries(
        target_queries,
        index,
        source_url=source_url,
        top_k=top_k,
        min_similarity=min_similarity,
        cluster_ids=target_cluster_ids,
    )


def _anchor_for(page_title: str | None, sub_query: str) -> str:
    title = (page_title or "").strip()
    if title:
        return _truncate(title)
    return _truncate(sub_query.strip())


def _truncate(text: str, max_len: int = DEFAULT_ANCHOR_MAX_LEN) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _normalise_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().rstrip("/")
    # Drop fragments; lowercase host; strip trailing slash on path.
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))
