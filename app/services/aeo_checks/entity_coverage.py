"""Check D — Entity Coverage.

Extracts named entities from the body via spaCy NER and scores two
axes: density (distinct entities per 1k words) and diversity (count of
distinct entity types). LLM answer engines preferentially cite content
that names the people, organisations, products, places, and works
relevant to a query — sparse or mono-typed entity coverage is a
strong negative signal.

A `KnowledgeBase` Protocol is exposed for a future Wikidata/DBpedia
resolver and competitor-set comparator; the default implementation is a
no-op so the check runs deterministically offline.
"""

from __future__ import annotations

from typing import ClassVar, Protocol

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent
from app.services.nlp import get_nlp

SCORED_ENTITY_LABELS: frozenset[str] = frozenset(
    {
        "PERSON",
        "ORG",
        "PRODUCT",
        "GPE",
        "LOC",
        "WORK_OF_ART",
        "EVENT",
        "FAC",
        "NORP",
        "LAW",
    }
)

MIN_WORDS_FOR_SCORE = 30


class KnowledgeBase(Protocol):
    """Resolver hook for grounding entities against an external KB.

    Returns True if `entity_text` resolves to a canonical entry
    (Wikidata QID, DBpedia URI, internal catalogue, etc). Default
    implementation is a no-op; a real resolver is wired in Phase C
    alongside the competitor-set comparator.
    """

    def resolves(self, entity_text: str, label: str) -> bool: ...


class _NullKB:
    def resolves(self, entity_text: str, label: str) -> bool:  # noqa: ARG002
        return False


class EntityCoverageCheck(BaseCheck):
    check_id: ClassVar[str] = "entity_coverage"
    name: ClassVar[str] = "Entity Coverage"
    max_score: ClassVar[int] = 20

    def __init__(self, kb: KnowledgeBase | None = None) -> None:
        self._kb = kb or _NullKB()

    def run(self, content: ParsedContent) -> CheckResultModel:
        body = content.body_text or ""
        word_count = len(body.split())

        if word_count < MIN_WORDS_FOR_SCORE:
            return self._result(
                score=0,
                details={
                    "word_count": word_count,
                    "min_words_required": MIN_WORDS_FOR_SCORE,
                    "entities": [],
                    "distinct_entities": 0,
                    "distinct_types": 0,
                    "density_per_1k": 0.0,
                    "grounded_count": 0,
                    "note": "Insufficient text to extract entity signals.",
                },
                recommendation=(
                    "Add at least a couple of paragraphs of substantive content "
                    "so entity-coverage signals can be measured."
                ),
            )

        entities = _extract_entities(body)
        distinct = _dedupe(entities)
        types = sorted({label for _, label in distinct})
        density = (len(distinct) / word_count) * 1000.0
        grounded = sum(
            1 for text, label in distinct if self._kb.resolves(text, label)
        )

        density_pts = _score_density(len(distinct), density)
        diversity_pts = _score_diversity(len(types))
        score = density_pts + diversity_pts

        details = {
            "word_count": word_count,
            "entities": [{"text": t, "label": lbl} for t, lbl in distinct],
            "distinct_entities": len(distinct),
            "distinct_types": len(types),
            "types_present": types,
            "density_per_1k": round(density, 2),
            "grounded_count": grounded,
            "density_points": density_pts,
            "diversity_points": diversity_pts,
        }
        return self._result(score, details, _recommendation(score, len(distinct), types))


def _extract_entities(body: str) -> list[tuple[str, str]]:
    nlp = get_nlp()
    doc = nlp(body)
    out: list[tuple[str, str]] = []
    for ent in doc.ents:
        if ent.label_ not in SCORED_ENTITY_LABELS:
            continue
        text = ent.text.strip()
        if not text:
            continue
        out.append((text, ent.label_))
    return out


def _dedupe(entities: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for text, label in entities:
        key = (text.lower(), label)
        if key in seen:
            continue
        seen.add(key)
        out.append((text, label))
    return out


def _score_density(distinct_count: int, density_per_1k: float) -> int:
    """Score by both distinct count AND density; the ratio alone misleads
    on short text where one entity already clears any per-1k threshold."""
    if distinct_count == 0:
        return 0
    if distinct_count >= 8 and density_per_1k >= 8.0:
        return 10
    if distinct_count >= 5 and density_per_1k >= 5.0:
        return 7
    if distinct_count >= 3 and density_per_1k >= 2.0:
        return 4
    return 2


def _score_diversity(distinct_types: int) -> int:
    if distinct_types >= 4:
        return 10
    if distinct_types == 3:
        return 7
    if distinct_types == 2:
        return 4
    if distinct_types == 1:
        return 2
    return 0


def _recommendation(
    score: int, distinct_count: int, types: list[str]
) -> str | None:
    if score == 20:
        return None
    if distinct_count == 0:
        return (
            "No named entities detected. Name the people, products, "
            "organisations, and places relevant to the topic so answer engines "
            "can ground citations."
        )
    fixes: list[str] = []
    if distinct_count < 5:
        fixes.append(
            f"raise distinct named-entity count (currently {distinct_count}) "
            "by naming more specific people, products, or organisations"
        )
    if len(types) < 4:
        present = ", ".join(types) if types else "none"
        fixes.append(
            f"broaden entity diversity (types present: {present}); "
            "include at least one product, organisation, person, and place"
        )
    return "Entity coverage gaps: " + "; ".join(fixes) + "."
