"""Unit tests for Check D — Entity Coverage."""

from __future__ import annotations

import pytest

from app.services.aeo_checks.entity_coverage import (
    EntityCoverageCheck,
    KnowledgeBase,
)
from app.services.content_parser import ParsedContent, parse


def _content(body: str) -> ParsedContent:
    return parse(body, "text")


@pytest.mark.usefixtures("nlp")
def test_below_min_word_count_returns_zero():
    result = EntityCoverageCheck().run(_content("Python rocks."))
    assert result.score == 0
    assert result.passed is False
    assert result.details["entities"] == []
    assert "Insufficient text" in result.details["note"]
    assert result.recommendation is not None


@pytest.mark.usefixtures("nlp")
def test_no_entities_returns_zero():
    body = (
        "The quick brown fox jumps over the lazy dog. This sentence has no "
        "proper nouns whatsoever. It is purely descriptive. Cats are common "
        "pets. Dogs bark at intruders. Apples are fruits. The sky is blue "
        "today and the grass is green tomorrow."
    )
    result = EntityCoverageCheck().run(_content(body))
    assert result.score == 0
    assert result.details["distinct_entities"] == 0
    assert result.details["distinct_types"] == 0
    assert result.recommendation is not None
    assert "No named entities" in result.recommendation


@pytest.mark.usefixtures("nlp")
def test_single_entity_low_density_scores_minimum_band():
    body = (
        "Python is a programming language. It is used widely. Many developers "
        "write code with it. Functions are first class. Lists are flexible. "
        "Dictionaries are mappings. Sets are unordered. Strings are immutable. "
        "Numbers can be integers or floats."
    )
    result = EntityCoverageCheck().run(_content(body))
    assert result.details["distinct_entities"] == 1
    assert result.details["distinct_types"] == 1
    # density(2) + diversity(2) = 4
    assert result.score == 4
    assert result.recommendation is not None


@pytest.mark.usefixtures("nlp")
def test_mid_density_three_types_hits_seventeen():
    body = (
        "Python is a programming language created by Guido van Rossum at "
        "Centrum Wiskunde Informatica in the Netherlands. Microsoft acquired "
        "GitHub in 2018. Google released TensorFlow in 2015 and PyTorch was "
        "developed at Meta AI in New York. Linus Torvalds maintains Linux. "
        "The Apache Foundation hosts Hadoop and Kafka in Maryland."
    )
    result = EntityCoverageCheck().run(_content(body))
    # spaCy on en_core_web_lg picks up ORG/PERSON/GPE on this text
    assert result.details["distinct_entities"] >= 8
    assert set(result.details["types_present"]) >= {"PERSON", "ORG", "GPE"}
    # density(10) + diversity(7) = 17
    assert result.score == 17


@pytest.mark.usefixtures("nlp")
def test_highly_varied_four_plus_types_hits_max():
    body = (
        "The European Union passed the GDPR law in 2018. Apple released the "
        "iPhone 15 in September. The Olympics in Paris drew millions. "
        "Beethoven composed the Ninth Symphony in Vienna. Italians celebrate "
        "Christmas with panettone. The Empire State Building in New York "
        "remains iconic."
    )
    result = EntityCoverageCheck().run(_content(body))
    assert result.details["distinct_types"] >= 4
    assert result.score == 20
    assert result.passed is True
    assert result.recommendation is None


@pytest.mark.usefixtures("nlp")
def test_repeated_mentions_deduplicate():
    body = (
        "Microsoft released a new product. Microsoft is large. Microsoft "
        "competes with Google. Google is also a competitor. Google offers "
        "many services. Apple is in the same race. Apple makes phones. "
        "Many companies compete here. Microsoft remains dominant in cloud."
    )
    result = EntityCoverageCheck().run(_content(body))
    surface = {e["text"].lower() for e in result.details["entities"]}
    # 3 distinct orgs, despite many repeats
    assert surface == {"microsoft", "google", "apple"}
    assert result.details["distinct_entities"] == 3


@pytest.mark.usefixtures("nlp")
def test_kb_hook_invoked_per_distinct_entity():
    calls: list[tuple[str, str]] = []

    class RecordingKB:
        def resolves(self, entity_text: str, label: str) -> bool:
            calls.append((entity_text, label))
            return entity_text.lower() == "microsoft"

    body = (
        "Microsoft acquired GitHub in 2018 for 7.5 billion dollars. Microsoft "
        "is based in Redmond and competes with Google in the cloud market. "
        "Sundar Pichai runs Google from Mountain View. Apple ships iPhones "
        "from Cupertino every September. Many companies operate in this space."
    )
    kb: KnowledgeBase = RecordingKB()
    result = EntityCoverageCheck(kb=kb).run(_content(body))
    distinct = result.details["distinct_entities"]
    assert distinct > 0
    assert len(calls) == distinct
    assert result.details["grounded_count"] == 1
