"""Locale registry + readability metric routing.

A `LocaleConfig` describes (a) the ISO-639-1 code, (b) the spaCy model
name we'd load if one is installed, and (c) which readability metric
makes sense for that language.

The `readability_metric` is a string keyword instead of a callable so
the readability check can dispatch *without* importing this module
during its critical path — keeps the import graph from leaking
NLP-heavy deps into every check call.
"""

from __future__ import annotations

from dataclasses import dataclass

# Roadmap-target locales. Easy to extend; the readability check's
# dispatch table is the only other place that needs to know about
# additions.
SUPPORTED_LOCALES: tuple[str, ...] = (
    "en", "es", "fr", "de", "it", "pt", "nl",
    "sv", "da", "no",
    "hi", "ta", "te", "kn", "bn", "ml",
)

# ISO-639-1 → spaCy model name. `en_core_web_lg` is loaded by default;
# others are best-effort and require `python -m spacy download …`.
SPACY_MODEL_FOR_LOCALE: dict[str, str] = {
    "en": "en_core_web_lg",
    "es": "es_core_news_lg",
    "fr": "fr_core_news_lg",
    "de": "de_core_news_lg",
    "it": "it_core_news_lg",
    "pt": "pt_core_news_lg",
    "nl": "nl_core_news_lg",
    "sv": "sv_core_news_lg",
    "da": "da_core_news_lg",
    "nb": "nb_core_news_lg",  # spaCy ships "nb" not "no"
    "no": "nb_core_news_lg",
    # Indic languages: spaCy has only tokenisers for most; we'll fall
    # back to the multi-language `xx_ent_wiki_sm` when present.
    "hi": "xx_ent_wiki_sm",
    "ta": "xx_ent_wiki_sm",
    "te": "xx_ent_wiki_sm",
    "kn": "xx_ent_wiki_sm",
    "bn": "xx_ent_wiki_sm",
    "ml": "xx_ent_wiki_sm",
}


@dataclass(frozen=True)
class LocaleConfig:
    code: str
    name: str
    spacy_model: str
    readability_metric: str
    fk_target_range: tuple[float, float]


_BY_CODE: dict[str, LocaleConfig] = {
    "en": LocaleConfig("en", "English",  "en_core_web_lg",  "flesch_kincaid",   (7.0, 9.0)),
    "es": LocaleConfig("es", "Spanish",  "es_core_news_lg", "fernandez_huerta", (60.0, 70.0)),
    "fr": LocaleConfig("fr", "French",   "fr_core_news_lg", "kandel_moles",     (60.0, 80.0)),
    "de": LocaleConfig("de", "German",   "de_core_news_lg", "flesch_german",    (60.0, 80.0)),
    "it": LocaleConfig("it", "Italian",  "it_core_news_lg", "gulpease",         (60.0, 80.0)),
    "pt": LocaleConfig("pt", "Portuguese","pt_core_news_lg","fernandez_huerta", (60.0, 70.0)),
    "nl": LocaleConfig("nl", "Dutch",    "nl_core_news_lg", "lix",              (35.0, 45.0)),
    "sv": LocaleConfig("sv", "Swedish",  "sv_core_news_lg", "lix",              (30.0, 45.0)),
    "da": LocaleConfig("da", "Danish",   "da_core_news_lg", "lix",              (30.0, 45.0)),
    "no": LocaleConfig("no", "Norwegian","nb_core_news_lg", "lix",              (30.0, 45.0)),
    "hi": LocaleConfig("hi", "Hindi",    "xx_ent_wiki_sm",  "syllable_density", (1.4, 2.0)),
    "ta": LocaleConfig("ta", "Tamil",    "xx_ent_wiki_sm",  "syllable_density", (1.4, 2.0)),
    "te": LocaleConfig("te", "Telugu",   "xx_ent_wiki_sm",  "syllable_density", (1.4, 2.0)),
    "kn": LocaleConfig("kn", "Kannada",  "xx_ent_wiki_sm",  "syllable_density", (1.4, 2.0)),
    "bn": LocaleConfig("bn", "Bengali",  "xx_ent_wiki_sm",  "syllable_density", (1.4, 2.0)),
    "ml": LocaleConfig("ml", "Malayalam","xx_ent_wiki_sm",  "syllable_density", (1.4, 2.0)),
}


def get_locale_config(code: str) -> LocaleConfig:
    """Return the config for `code`, falling back to English on unknown
    codes so the rest of the pipeline degrades rather than crashing."""
    return _BY_CODE.get((code or "en").lower(), _BY_CODE["en"])


def is_indic(code: str) -> bool:
    return code in {"hi", "ta", "te", "kn", "bn", "ml"}
