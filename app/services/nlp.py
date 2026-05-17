"""spaCy singletons, lazy per locale.

The default `get_nlp()` returns the English `en_core_web_lg` model used
everywhere prior to Phase F. The new `get_nlp_for_locale(code)` loader
keeps one Language instance per ISO-639-1 code in a process-wide cache;
if the per-locale model isn't installed we fall back to a `spacy.blank`
pipeline so callers downstream still get a tokeniser + sentence-splitter
instead of an ImportError.
"""

from __future__ import annotations

import logging
from typing import Final

import spacy
from spacy.language import Language

from app.services.i18n.locale import SPACY_MODEL_FOR_LOCALE

_MODEL_NAME = "en_core_web_lg"
_nlp: Language | None = None
_locale_cache: dict[str, Language] = {}
_blank_cache: dict[str, Language] = {}

log = logging.getLogger(__name__)

# A handful of locales that spaCy can build a `blank` tokeniser for
# without an installed model. Fed via `spacy.blank(code)`.
BLANK_TOKENISER_LOCALES: Final[frozenset[str]] = frozenset(
    {"en", "es", "fr", "de", "it", "pt", "nl", "sv", "da", "nb",
     "hi", "ta", "te", "kn", "bn", "ml", "xx"}
)


def get_nlp() -> Language:
    """Return the default English pipeline (full)."""
    global _nlp
    if _nlp is None:
        _nlp = spacy.load(_MODEL_NAME)
    return _nlp


def get_nlp_for_locale(code: str | None) -> Language:
    """Return the best-available spaCy pipeline for `code`.

    Order:
      1. cached instance for `code`
      2. installed full model named in `SPACY_MODEL_FOR_LOCALE`
      3. installed multi-language `xx_ent_wiki_sm`
      4. `spacy.blank(code)` (tokenisation + sentence segmentation only)
      5. default English `_MODEL_NAME`

    All fallbacks log at WARNING so deployment owners can see when a
    locale is degrading rather than guessing.
    """
    code = (code or "en").lower()
    if code in _locale_cache:
        return _locale_cache[code]

    model_name = SPACY_MODEL_FOR_LOCALE.get(code)
    if model_name:
        try:
            nlp = spacy.load(model_name)
            _locale_cache[code] = nlp
            return nlp
        except OSError:
            log.warning("nlp: model %s not installed; trying fallbacks for %s", model_name, code)

    # Multi-language model (good enough for tokenisation on Indic scripts).
    if model_name != "xx_ent_wiki_sm":
        try:
            nlp = spacy.load("xx_ent_wiki_sm")
            _locale_cache[code] = nlp
            return nlp
        except OSError:
            log.warning("nlp: xx_ent_wiki_sm not installed; falling further back for %s", code)

    if code in BLANK_TOKENISER_LOCALES:
        if code not in _blank_cache:
            try:
                blank = spacy.blank(code)
                # `spacy.blank` ships only a tokenizer; add a sentencizer so
                # downstream consumers (readability complex-sentence ranking,
                # gap-analyzer chunker) can iterate `doc.sents` without
                # raising.
                if "sentencizer" not in blank.pipe_names:
                    blank.add_pipe("sentencizer")
                _blank_cache[code] = blank
            except (ImportError, OSError, ValueError) as e:
                log.warning("nlp: spacy.blank(%s) failed (%s); using English fallback", code, e)
        if code in _blank_cache:
            _locale_cache[code] = _blank_cache[code]
            return _blank_cache[code]

    # Final fallback: hand back the default English pipeline so callers
    # don't have to write a None check; the locale-aware readability
    # check explicitly looks at the *locale* (not the model) when
    # choosing a metric, so this fallback is still safe.
    return get_nlp()


def _reset_locale_cache_for_tests() -> None:
    _locale_cache.clear()
    _blank_cache.clear()
