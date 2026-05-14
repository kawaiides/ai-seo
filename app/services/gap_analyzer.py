"""Semantic gap analysis for the fan-out engine.

Given a list of generated sub-queries and a piece of user-pasted content,
determine which sub-queries the content already covers (max cosine
similarity to any content sentence ≥ THRESHOLD).

Why MiniLM + normalized embeddings + matmul:
- MiniLM-L6-v2: 5x faster than mpnet, ~80MB, sufficient for fuzzy
  coverage scoring at this threshold range. Spec line 471 green-flags
  this choice.
- `normalize_embeddings=True` makes raw dot product equal cosine
  similarity, which lets us replace per-pair `cos_sim` calls with a
  single (N_queries, dim) @ (dim, N_chunks) matmul. Spec red-flag #3
  ("cosine computed incorrectly on non-normalized vectors") avoided.
"""

from __future__ import annotations

from collections import Counter

from app.models.schemas import (
    GapSummary,
    LLMSubQuery,
    SubQueryResponse,
)
from app.services.content_parser import ContentParseError
from app.services.embeddings import get_embedder
from app.services.nlp import get_nlp

THRESHOLD: float = 0.72
DEFAULT_MIN_WORDS: int = 4
DEFAULT_MAX_CHUNKS: int = 500


def chunk_content(
    text: str,
    *,
    min_words: int = DEFAULT_MIN_WORDS,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> list[str]:
    """Split content into sentence-level chunks for embedding.

    Drops sentences shorter than `min_words` (typically headers re-pasted
    as fragments, or "Yes."/"Hi." artifacts that produce noisy embeddings).
    Caps the result at `max_chunks` so a pathologically long input can't
    blow up encoding time.
    """
    if not text or not text.strip():
        raise ContentParseError("existing_content is empty")

    nlp = get_nlp()
    doc = nlp(text)
    chunks: list[str] = []
    for sent in doc.sents:
        s = sent.text.strip()
        if not s:
            continue
        if len(s.split()) < min_words:
            continue
        chunks.append(s)
        if len(chunks) >= max_chunks:
            break

    if not chunks:
        raise ContentParseError(
            "existing_content has no sentences with at least "
            f"{min_words} words"
        )
    return chunks


def score_subqueries(
    sub_queries: list[LLMSubQuery],
    content: str,
) -> list[tuple[bool, float]]:
    """Compute (covered, similarity_score) for each sub-query against content.

    Single batched encode + one matmul, max-reduced over chunks.
    """
    chunks = chunk_content(content)

    embedder = get_embedder()
    chunk_vecs = embedder.encode(
        chunks, normalize_embeddings=True, convert_to_numpy=True
    )
    query_vecs = embedder.encode(
        [sq.query for sq in sub_queries],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    # (N_queries, dim) @ (dim, N_chunks) → (N_queries, N_chunks)
    sims = query_vecs @ chunk_vecs.T
    max_sims = sims.max(axis=1)

    out: list[tuple[bool, float]] = []
    for s in max_sims:
        rounded = round(float(s), 2)
        out.append((rounded >= THRESHOLD, rounded))
    return out


def build_gap_summary(scored: list[SubQueryResponse]) -> GapSummary:
    """Build the gap_summary block from a list of scored sub-queries.

    `covered_types` = sorted set of types where ≥1 sub-query is covered.
    `missing_types` = sorted set of types that appear in `scored` but
    have 0 covered sub-queries.
    """
    total = len(scored)
    covered_count = sum(1 for sq in scored if sq.covered)
    coverage_percent = round(covered_count / total * 100) if total else 0

    types_present = {sq.type for sq in scored}
    covered_types = sorted({sq.type for sq in scored if sq.covered})
    missing_types = sorted(types_present - set(covered_types))

    return GapSummary(
        covered=covered_count,
        total=total,
        coverage_percent=coverage_percent,
        covered_types=covered_types,  # type: ignore[arg-type]
        missing_types=missing_types,  # type: ignore[arg-type]
    )


def _type_distribution(sub_queries: list[LLMSubQuery]) -> Counter[str]:
    """Helper for diagnostic logging — counts sub-queries per type."""
    return Counter(sq.type for sq in sub_queries)
