"""Lazy module-level singleton for the sentence-transformer model.

`all-MiniLM-L6-v2` chosen for the gap analyzer: 384-dim embeddings,
~5x faster than `all-mpnet-base-v2` on CPU, and the spec explicitly
green-flags this choice (line 471). Loaded on first use so test imports
stay cheap and the ~80MB download / ~1.5s load only happens when actually
needed.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

_MODEL_NAME = "all-MiniLM-L6-v2"
_model: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(_MODEL_NAME)
    return _model
