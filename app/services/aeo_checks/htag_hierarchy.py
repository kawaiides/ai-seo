"""Check B — H-tag Hierarchy.

Validates: exactly one H1, no skipped levels, no H-tag before the H1.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.models.schemas import CheckResultModel
from app.services.aeo_checks.base import BaseCheck
from app.services.content_parser import ParsedContent


class HTagHierarchyCheck(BaseCheck):
    check_id: ClassVar[str] = "htag_hierarchy"
    name: ClassVar[str] = "H-tag Hierarchy"
    max_score: ClassVar[int] = 20

    def run(self, content: ParsedContent) -> CheckResultModel:
        h_tags = list(content.h_tags)
        h_tags_found = [f"h{level}" for level, _ in h_tags]
        h1_count = sum(1 for level, _ in h_tags if level == 1)

        violations: list[dict[str, Any]] = []

        if h1_count == 0:
            violations.append(
                {
                    "rule": "missing_h1",
                    "description": "No <h1> tag found in the document.",
                }
            )
            score = 0
            return self._result(
                score=score,
                details={
                    "violations": violations,
                    "h_tags_found": h_tags_found,
                },
                recommendation=_recommendation(violations, h_tags_found),
            )

        # Pre-H1 tags
        first_h1_index = next(
            (i for i, (lvl, _) in enumerate(h_tags) if lvl == 1), None
        )
        if first_h1_index is not None and first_h1_index > 0:
            for i in range(first_h1_index):
                violations.append(
                    {
                        "rule": "pre_h1_tag",
                        "description": (
                            f"H{h_tags[i][0]} appears before the first H1 (position {i})."
                        ),
                        "index": i,
                    }
                )

        # Multiple H1s — count each extra H1 as its own violation
        if h1_count > 1:
            extra_indices = [
                i for i, (lvl, _) in enumerate(h_tags) if lvl == 1
            ][1:]
            for i in extra_indices:
                violations.append(
                    {
                        "rule": "multiple_h1",
                        "description": (
                            f"Extra H1 at position {i}; documents should have exactly one."
                        ),
                        "index": i,
                    }
                )

        # Skipped levels (each skip occurrence is a violation)
        prev_level: int | None = None
        for i, (level, _) in enumerate(h_tags):
            if prev_level is not None and level > prev_level + 1:
                violations.append(
                    {
                        "rule": "level_skipped",
                        "description": (
                            f"Heading skipped from H{prev_level} to H{level} at position {i}."
                        ),
                        "index": i,
                    }
                )
            prev_level = level

        n = len(violations)
        if n == 0:
            score = 20
        elif n <= 2:
            score = 12
        else:
            score = 0

        return self._result(
            score=score,
            details={
                "violations": violations,
                "h_tags_found": h_tags_found,
            },
            recommendation=_recommendation(violations, h_tags_found),
        )


def _recommendation(
    violations: list[dict[str, Any]], h_tags_found: list[str]
) -> str | None:
    if not violations:
        return None
    rules = {v["rule"] for v in violations}
    fixes: list[str] = []
    if "missing_h1" in rules:
        fixes.append("add a single <h1> at the top of the page")
    if "multiple_h1" in rules:
        fixes.append("collapse extra <h1> tags into <h2> or another level")
    if "pre_h1_tag" in rules:
        fixes.append("move all subheadings to appear after the <h1>")
    if "level_skipped" in rules:
        fixes.append("avoid skipping heading levels (e.g. H1 → H3)")
    return "Heading structure issues: " + "; ".join(fixes) + "."
