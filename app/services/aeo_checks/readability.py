"""Check C — Snippet Readability.

Locale-aware (Phase F): the metric chosen depends on the detected (or
caller-supplied) ISO-639-1 code. English defaults to Flesch-Kincaid
grade level; Spanish/Portuguese use Fernandez-Huerta; French uses
Crawford; German uses a Flesch-tuned reading-ease; Italian uses
Gulpease; Scandinavian + Dutch use LIX; Indic scripts use a
syllable-density heuristic.

The complex-sentence surfacing logic stays unchanged because spaCy's
sentence splitter handles every supported locale (via the per-locale
loader in `app/services/nlp.py`).
"""

from __future__ import annotations

from typing import ClassVar

import textstat

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent
from app.services.i18n.lang_detect import detect
from app.services.i18n.locale import get_locale_config
from app.services.i18n.readability_metrics import (
    compute_metric,
    score_for_metric,
)
from app.services.nlp import get_nlp, get_nlp_for_locale

TARGET_RANGE = "7-9"
MIN_WORDS_FOR_COMPLEXITY_RANK = 5


class ReadabilityCheck(BaseCheck):
    check_id: ClassVar[str] = "readability"
    name: ClassVar[str] = "Snippet Readability"
    max_score: ClassVar[int] = 20

    def __init__(self, locale: str | None = None) -> None:
        """`locale` overrides automatic detection; pass an ISO-639-1 code
        when the caller already knows which language the content is in."""
        self._forced_locale = locale

    def run(self, content: ParsedContent) -> CheckResultModel:
        body = content.body_text or ""
        if not body.strip() or len(body.split()) < 10:
            details = {
                "fk_grade_level": None,
                "target_range": TARGET_RANGE,
                "complex_sentences": [],
                "locale": self._forced_locale or "en",
                "locale_detection": "forced" if self._forced_locale else "default",
                "note": "Insufficient text to compute a reliable readability score.",
            }
            return self._result(
                score=0,
                details=details,
                recommendation="Add more body text — at least a couple of paragraphs — to enable a reliable readability score.",
            )

        if self._forced_locale:
            locale_code = self._forced_locale.lower()
            detection_method = "forced"
        else:
            result = detect(body)
            locale_code = result.code
            detection_method = result.method

        config = get_locale_config(locale_code)
        value = compute_metric(config.readability_metric, body)
        score = score_for_metric(config.readability_metric, value, config.fk_target_range)
        complex_sentences = _top_complex_sentences(body, locale_code, k=3)

        details = {
            # Keep `fk_grade_level` for backward-compat on EN callers; for
            # non-EN locales it carries the locale's metric value.
            "fk_grade_level": value,
            "metric": config.readability_metric,
            "metric_value": value,
            "target_range": f"{config.fk_target_range[0]}-{config.fk_target_range[1]}",
            "locale": locale_code,
            "locale_detection": detection_method,
            "complex_sentences": complex_sentences,
        }
        recommendation = _recommendation(score, value, config.readability_metric, config.fk_target_range)
        return self._result(score, details, recommendation)


def _score_for_grade(fk: float) -> int:
    """Retained for backward compat (English-only). Phase F dispatches
    through `i18n.readability_metrics.score_for_metric` instead."""
    if 7.0 <= fk <= 9.0:
        return 20
    if (6.0 <= fk < 7.0) or (9.0 < fk <= 10.0):
        return 14
    if (5.0 <= fk < 6.0) or (10.0 < fk <= 11.0):
        return 8
    return 0


def _top_complex_sentences(
    body: str,
    locale_code: str = "en",
    k: int = 3,
    min_words: int = MIN_WORDS_FOR_COMPLEXITY_RANK,
) -> list[str]:
    nlp = get_nlp_for_locale(locale_code) if locale_code != "en" else get_nlp()
    doc = nlp(body)
    scored: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for sent in doc.sents:
        text = sent.text.strip()
        if not text or text in seen:
            continue
        words = text.split()
        wc = len(words)
        if wc < min_words:
            continue
        try:
            sylls = textstat.syllable_count(text)
        except Exception:
            continue
        ratio = sylls / max(wc, 1)
        scored.append((ratio, wc, text))
        seen.add(text)

    # Sort by descending complexity, ties broken by sentence length (longer first)
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [text for _, _, text in scored[:k]]


def _recommendation(
    score: int,
    value: float,
    metric: str,
    target_range: tuple[float, float],
) -> str | None:
    if score == 20:
        return None
    low, high = target_range
    target_phrase = f"{low:g}–{high:g}"
    metric_label = metric.replace("_", " ")
    if value < low:
        return (
            f"Content scores {value:g} on {metric_label}; below the AEO target band ({target_phrase}). "
            "Add more substantive detail or vary sentence structure to lift the score into range."
        )
    if value > high:
        return (
            f"Content scores {value:g} on {metric_label}; above the AEO target band ({target_phrase}). "
            "Shorten sentences and replace jargon to settle inside the band."
        )
    return (
        f"Content scores {value:g} on {metric_label}. Tighten the most complex sentences "
        f"to land inside the target band ({target_phrase})."
    )
