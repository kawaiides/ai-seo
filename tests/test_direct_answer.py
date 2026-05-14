"""Unit tests for Check A — Direct Answer Detection."""

from __future__ import annotations

import pytest

from app.services.aeo_checks.direct_answer import DirectAnswerCheck
from app.services.content_parser import ParsedContent


def _content(paragraph: str) -> ParsedContent:
    return ParsedContent(
        raw=paragraph,
        soup=None,
        first_paragraph=paragraph,
        h_tags=[],
        body_text=paragraph,
    )


@pytest.mark.usefixtures("nlp")
def test_clean_declarative_passes():
    paragraph = (
        "Python is a high-level programming language widely used "
        "for data science, web development, and scripting."
    )
    assert len(paragraph.split()) <= 60
    result = DirectAnswerCheck().run(_content(paragraph))
    assert result.score == 20
    assert result.passed is True
    assert result.details["is_declarative"] is True
    assert result.details["has_hedge_phrase"] is False
    assert result.recommendation is None


@pytest.mark.usefixtures("nlp")
def test_hedge_phrase_drops_to_12():
    paragraph = (
        "Python is a popular programming language for data science. "
        "It depends on the project, but Python is typically the right pick."
    )
    assert len(paragraph.split()) <= 60
    result = DirectAnswerCheck().run(_content(paragraph))
    assert result.score == 12
    assert result.details["has_hedge_phrase"] is True
    assert result.recommendation is not None


@pytest.mark.usefixtures("nlp")
def test_fragment_not_declarative():
    paragraph = "Python. Fast. Reliable."
    result = DirectAnswerCheck().run(_content(paragraph))
    assert result.score == 12
    assert result.details["is_declarative"] is False


@pytest.mark.usefixtures("nlp")
def test_word_count_61_to_90():
    paragraph = " ".join(["word"] * 75)
    result = DirectAnswerCheck().run(_content(paragraph))
    assert result.details["word_count"] == 75
    assert result.score == 8


@pytest.mark.usefixtures("nlp")
def test_word_count_over_90():
    paragraph = " ".join(["word"] * 100)
    result = DirectAnswerCheck().run(_content(paragraph))
    assert result.details["word_count"] == 100
    assert result.score == 0


@pytest.mark.usefixtures("nlp")
def test_question_only_paragraph_not_declarative():
    paragraph = "Is Python a good programming language for data science?"
    result = DirectAnswerCheck().run(_content(paragraph))
    assert result.details["is_declarative"] is False
    assert result.score == 12
