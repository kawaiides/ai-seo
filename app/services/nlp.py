"""Module-level spaCy singleton.

Lazy-loaded so test imports stay cheap and the model only pays its
~500ms load cost on first use.
"""

from __future__ import annotations

import spacy
from spacy.language import Language

_MODEL_NAME = "en_core_web_lg"
_nlp: Language | None = None


def get_nlp() -> Language:
    global _nlp
    if _nlp is None:
        _nlp = spacy.load(_MODEL_NAME, disable=["ner"])
    return _nlp
