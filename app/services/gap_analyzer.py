"""Semantic gap analysis for the fan-out engine.

Given a list of generated sub-queries and a piece of user-pasted content,
determine which sub-queries the content already covers (max cosine
similarity to any content sentence ≥ THRESHOLD).

v2 adds *heading-aware passage chunking*: when the input is HTML with
`<h1>`/`<h2>`/`<h3>` section breaks, sentences are grouped into
passages rooted at the most recent heading. This lets the gap-detector
match a sub-query against a *section topic* rather than against the
single best sentence — which catches passages that answer the intent
holistically without any one sentence being a high-similarity hit.

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
from typing import Any

from bs4 import BeautifulSoup

from app.models.schemas import (
    GapSummary,
    LLMSubQuery,
    SubQueryResponse,
)
from app.services.content_parser import ContentParseError
from app.services.embeddings import get_embedder
from app.services.nlp import get_nlp

THRESHOLD: float = 0.72
PASSAGE_THRESHOLD: float = 0.58
DEFAULT_MIN_WORDS: int = 4
DEFAULT_MAX_CHUNKS: int = 500
DEFAULT_MAX_PASSAGES: int = 200
PASSAGE_MAX_SENTENCES: int = 5


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


def chunk_passages(
    text: str,
    *,
    max_sentences: int = PASSAGE_MAX_SENTENCES,
    max_passages: int = DEFAULT_MAX_PASSAGES,
) -> list[dict[str, Any]]:
    """Group sentences into heading-rooted passages.

    For HTML inputs, a new passage starts at every `<h1>`/`<h2>`/`<h3>`.
    For plain text or HTML without headings, every `max_sentences`-block
    becomes a passage. Each passage is at most `max_sentences` sentences
    (~30–80 words) so embedding granularity stays coherent.

    Returns a list of dicts so callers can attach heading context to the
    similarity score (used by the dashboard "found in section X" tooltip).
    """
    if not text or not text.strip():
        raise ContentParseError("existing_content is empty")

    nlp = get_nlp()

    has_html = "<" in text and ">" in text
    sections: list[tuple[str | None, str]] = []
    if has_html:
        soup = BeautifulSoup(text, "html.parser")
        current_heading: str | None = None
        buf: list[str] = []
        for el in soup.find_all(["h1", "h2", "h3", "p", "li"]):
            if el.name in ("h1", "h2", "h3"):
                if buf:
                    sections.append((current_heading, " ".join(buf)))
                    buf = []
                current_heading = el.get_text(" ", strip=True)
            else:
                t = el.get_text(" ", strip=True)
                if t:
                    buf.append(t)
        if buf:
            sections.append((current_heading, " ".join(buf)))

    if not sections:
        sections = [(None, text)]

    passages: list[dict[str, Any]] = []
    for heading, block in sections:
        doc = nlp(block)
        sentences: list[str] = []
        for sent in doc.sents:
            s = sent.text.strip()
            if not s:
                continue
            if len(s.split()) < DEFAULT_MIN_WORDS:
                continue
            sentences.append(s)
        for i in range(0, len(sentences), max_sentences):
            window = sentences[i : i + max_sentences]
            passages.append({
                "heading": heading,
                "text": " ".join(window),
                "sentence_count": len(window),
            })
            if len(passages) >= max_passages:
                return passages
    if not passages:
        raise ContentParseError(
            f"existing_content has no sentences with at least "
            f"{DEFAULT_MIN_WORDS} words"
        )
    return passages


def score_subqueries(
    sub_queries: list[LLMSubQuery],
    content: str,
    *,
    query_vecs: Any | None = None,
) -> list[tuple[bool, float]]:
    """Compute (covered, similarity_score) for each sub-query against content.

    Single batched encode + one matmul, max-reduced over chunks. The
    optional `query_vecs` argument lets a caller pass a pre-computed
    (N, dim) array of normalised query embeddings — used by Fan-Out v2
    to share the encode across clustering and gap analysis.
    """
    chunks = chunk_content(content)

    embedder = get_embedder()
    chunk_vecs = embedder.encode(
        chunks, normalize_embeddings=True, convert_to_numpy=True
    )
    if query_vecs is None:
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


def score_subqueries_v2(
    sub_queries: list[LLMSubQuery],
    content: str,
    *,
    query_vecs: Any | None = None,
) -> list[dict[str, Any]]:
    """Heading-aware passage-level scoring.

    For each sub-query returns a dict with:
      - `covered` (bool): True if max-passage similarity ≥ PASSAGE_THRESHOLD.
      - `similarity_score` (float): rounded max similarity over passages.
      - `matched_heading` (str | None): heading of the best-matching passage.
      - `matched_passage_index` (int): position in passages list.

    Threshold is lower than v1 (0.58 vs 0.72) because passages have richer
    context — a coherent multi-sentence section can answer an intent with
    lower per-token similarity than a single tight sentence.
    """
    passages = chunk_passages(content)
    embedder = get_embedder()
    passage_vecs = embedder.encode(
        [p["text"] for p in passages],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    if query_vecs is None:
        query_vecs = embedder.encode(
            [sq.query for sq in sub_queries],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
    sims = query_vecs @ passage_vecs.T
    best_idx = sims.argmax(axis=1)
    out: list[dict[str, Any]] = []
    for q_i in range(len(sub_queries)):
        idx = int(best_idx[q_i])
        score = round(float(sims[q_i, idx]), 2)
        out.append({
            "covered": score >= PASSAGE_THRESHOLD,
            "similarity_score": score,
            "matched_heading": passages[idx]["heading"],
            "matched_passage_index": idx,
        })
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
