"""Check C — Snippet Readability.

Computes Flesch-Kincaid grade level on the boilerplate-stripped body
and surfaces the three most syllable-dense sentences.
"""

from __future__ import annotations

from typing import ClassVar

import textstat

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent
from app.services.nlp import get_nlp

TARGET_RANGE = "7-9"
MIN_WORDS_FOR_COMPLEXITY_RANK = 5


class ReadabilityCheck(BaseCheck):
    check_id: ClassVar[str] = "readability"
    name: ClassVar[str] = "Snippet Readability"
    max_score: ClassVar[int] = 20

    def run(self, content: ParsedContent) -> CheckResultModel:
        body = content.body_text or ""
        if not body.strip() or len(body.split()) < 10:
            details = {
                "fk_grade_level": None,
                "target_range": TARGET_RANGE,
                "complex_sentences": [],
                "note": "Insufficient text to compute a reliable Flesch-Kincaid grade.",
            }
            return self._result(
                score=0,
                details=details,
                recommendation="Add more body text — at least a couple of paragraphs — to enable a reliable readability score.",
            )

        fk_raw = textstat.flesch_kincaid_grade(body)
        fk = round(float(fk_raw), 1)
        score = _score_for_grade(fk)
        complex_sentences = _top_complex_sentences(body, k=3)

        details = {
            "fk_grade_level": fk,
            "target_range": TARGET_RANGE,
            "complex_sentences": complex_sentences,
        }
        recommendation = _recommendation(score, fk)
        return self._result(score, details, recommendation)


def _score_for_grade(fk: float) -> int:
    if 7.0 <= fk <= 9.0:
        return 20
    if (6.0 <= fk < 7.0) or (9.0 < fk <= 10.0):
        return 14
    if (5.0 <= fk < 6.0) or (10.0 < fk <= 11.0):
        return 8
    return 0


def _top_complex_sentences(
    body: str,
    k: int = 3,
    min_words: int = MIN_WORDS_FOR_COMPLEXITY_RANK,
) -> list[str]:
    nlp = get_nlp()
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


def _recommendation(score: int, fk: float) -> str | None:
    if score == 20:
        return None
    if fk < 5.0:
        return (
            f"Content reads at Grade {fk:g}, which is too simple for credible AI extraction. "
            "Add more substantive technical detail to reach Grade 7–9."
        )
    if fk < 7.0:
        return (
            f"Content reads at Grade {fk:g}, slightly below the AEO target. "
            "Add a touch more depth and varied sentence structure to reach Grade 7–9."
        )
    if fk > 11.0:
        return (
            f"Content reads at Grade {fk:g}. Shorten sentences and replace technical jargon "
            "with plain language to reach Grade 7–9."
        )
    return (
        f"Content reads at Grade {fk:g}. Tighten the most complex sentences to reach Grade 7–9."
    )
