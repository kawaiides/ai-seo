"""Unit tests for Check C — Snippet Readability."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.aeo_checks import readability as r
from app.services.aeo_checks.readability import ReadabilityCheck, _score_for_grade
from app.services.content_parser import ParsedContent


def _content(body: str) -> ParsedContent:
    return ParsedContent(
        raw=body,
        soup=None,
        first_paragraph="",
        h_tags=[],
        body_text=body,
    )


# -- Score-band table is deterministic; test it directly. --

@pytest.mark.parametrize(
    "fk,expected",
    [
        (7.0, 20),
        (8.0, 20),
        (9.0, 20),
        (6.5, 14),
        (9.5, 14),
        (10.0, 14),
        (5.5, 8),
        (10.5, 8),
        (11.0, 8),
        (4.9, 0),
        (11.1, 0),
        (15.0, 0),
        (3.0, 0),
    ],
)
def test_score_for_grade_bands(fk, expected):
    assert _score_for_grade(fk) == expected


# -- Integration tests with mocked textstat keep FK deterministic. --

@pytest.mark.usefixtures("nlp")
def test_grade_8_passes():
    body = (
        "The cat sat on the mat. It was warm in the sun. "
        "She watched birds fly by. The afternoon was quiet. "
        "Children played in the yard nearby."
    )
    with patch.object(r.textstat, "flesch_kincaid_grade", return_value=8.0):
        result = ReadabilityCheck().run(_content(body))
    assert result.score == 20
    assert result.details["fk_grade_level"] == 8.0
    assert result.passed is True


@pytest.mark.usefixtures("nlp")
def test_grade_10_partial_credit():
    body = (
        "Researchers analyzed data from a longitudinal study. "
        "Participants completed surveys at quarterly intervals. "
        "Results showed measurable improvements across cohorts. "
        "Further investigation will refine these conclusions."
    )
    with patch.object(r.textstat, "flesch_kincaid_grade", return_value=10.0):
        result = ReadabilityCheck().run(_content(body))
    assert result.score == 14


@pytest.mark.usefixtures("nlp")
def test_grade_12_complex_zero():
    body = (
        "The multifaceted nature of contemporary content optimization necessitates "
        "comprehensive evaluation methodologies. Heuristic approaches frequently "
        "underperform when juxtaposed against rigorously validated frameworks."
    )
    with patch.object(r.textstat, "flesch_kincaid_grade", return_value=12.5):
        result = ReadabilityCheck().run(_content(body))
    assert result.score == 0
    assert result.recommendation is not None


@pytest.mark.usefixtures("nlp")
def test_complex_sentences_top3_excludes_short_filler():
    short_filler = "Hi. Yes. Go now."
    long_complex = (
        "The multifaceted nature of contemporary content optimization "
        "necessitates comprehensive evaluation methodologies."
    )
    medium_complex = (
        "Researchers analyzed longitudinal datasets across diverse "
        "demographic populations to understand patterns."
    )
    extra_complex = (
        "Heuristically speaking, syntactic structures that incorporate "
        "polysyllabic vocabulary frequently impede extraction."
    )
    body = " ".join([short_filler, long_complex, medium_complex, extra_complex])

    with patch.object(r.textstat, "flesch_kincaid_grade", return_value=12.0):
        result = ReadabilityCheck().run(_content(body))

    sentences = result.details["complex_sentences"]
    assert len(sentences) == 3
    # Short fillers (< 5 words) must be excluded.
    for s in sentences:
        assert len(s.split()) >= 5
    assert "Hi." not in sentences
    assert "Yes." not in sentences


@pytest.mark.usefixtures("nlp")
def test_too_little_text_returns_zero_with_note():
    result = ReadabilityCheck().run(_content("Tiny."))
    assert result.score == 0
    assert result.details["fk_grade_level"] is None
    assert "note" in result.details
