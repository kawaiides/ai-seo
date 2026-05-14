"""Check A — Direct Answer Detection.

Tests whether the first paragraph answers the likely primary query in
60 words or fewer with a clear declarative statement.
"""

from __future__ import annotations

from typing import ClassVar

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent
from app.services.nlp import get_nlp

HEDGE_PHRASES: tuple[str, ...] = (
    "it depends",
    "may vary",
    "in some cases",
    "this varies",
    "generally speaking",
)


class DirectAnswerCheck(BaseCheck):
    check_id: ClassVar[str] = "direct_answer"
    name: ClassVar[str] = "Direct Answer Detection"
    max_score: ClassVar[int] = 20

    def run(self, content: ParsedContent) -> CheckResultModel:
        paragraph = content.first_paragraph or ""
        word_count = len(paragraph.split())
        has_hedge = _has_hedge(paragraph)
        is_declarative = _is_declarative(paragraph) if paragraph else False

        score = _score(word_count, has_hedge, is_declarative)

        details = {
            "word_count": word_count,
            "threshold": 60,
            "is_declarative": is_declarative,
            "has_hedge_phrase": has_hedge,
        }
        recommendation = _recommendation(
            score, word_count, has_hedge, is_declarative
        )
        return self._result(score, details, recommendation)


def _score(word_count: int, has_hedge: bool, is_declarative: bool) -> int:
    if word_count == 0:
        return 0
    if word_count > 90:
        return 0
    if word_count > 60:
        return 8
    # word_count is 1..60
    if has_hedge or not is_declarative:
        return 12
    return 20


def _has_hedge(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in HEDGE_PHRASES)


def _is_declarative(text: str) -> bool:
    """Return True if the paragraph contains at least one sentence that
    looks like a declarative statement: has a nominal subject, a verb or
    auxiliary as ROOT, and does not end in a question mark.
    """
    nlp = get_nlp()
    doc = nlp(text)
    for sent in doc.sents:
        sent_text = sent.text.strip()
        if not sent_text:
            continue
        if sent_text.endswith("?"):
            continue
        has_subject = any(
            tok.dep_ in {"nsubj", "nsubjpass"} for tok in sent
        )
        root = next((tok for tok in sent if tok.dep_ == "ROOT"), None)
        if not has_subject or root is None:
            continue
        if root.pos_ in {"VERB", "AUX"}:
            return True
    return False


def _recommendation(
    score: int, word_count: int, has_hedge: bool, is_declarative: bool
) -> str | None:
    if score == 20:
        return None
    if word_count == 0:
        return "No opening paragraph was detected. Add a direct, declarative answer in the first paragraph."
    if word_count > 90:
        return (
            f"Your opening paragraph is {word_count} words. "
            "Trim it to under 60 words with a direct, declarative answer."
        )
    if word_count > 60:
        return (
            f"Your opening paragraph is {word_count} words. "
            "Trim it to under 60 words with a direct, declarative answer."
        )
    issues = []
    if has_hedge:
        issues.append("remove hedging phrases (e.g. 'it depends', 'may vary')")
    if not is_declarative:
        issues.append("rewrite as a complete declarative statement (subject + verb)")
    fixes = " and ".join(issues) if issues else "tighten the phrasing"
    return f"Opening paragraph is {word_count} words; {fixes} to score full marks."
