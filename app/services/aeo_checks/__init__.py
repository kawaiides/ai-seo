"""AEO check registry."""

from __future__ import annotations

from app.services.aeo_checks.base import BaseCheck
from app.services.aeo_checks.citations import CitationsCheck
from app.services.aeo_checks.direct_answer import DirectAnswerCheck
from app.services.aeo_checks.entity_coverage import EntityCoverageCheck
from app.services.aeo_checks.freshness import FreshnessCheck
from app.services.aeo_checks.htag_hierarchy import HTagHierarchyCheck
from app.services.aeo_checks.readability import ReadabilityCheck
from app.services.aeo_checks.schema_markup import SchemaMarkupCheck

__all__ = [
    "BaseCheck",
    "CitationsCheck",
    "DirectAnswerCheck",
    "EntityCoverageCheck",
    "FreshnessCheck",
    "HTagHierarchyCheck",
    "ReadabilityCheck",
    "SchemaMarkupCheck",
    "FREE_CHECK_IDS",
    "PRO_CHECK_IDS",
    "default_checks",
    "free_checks",
    "pro_checks",
    "checks_for_plan",
]


FREE_CHECK_IDS: frozenset[str] = frozenset({
    DirectAnswerCheck.check_id,
    HTagHierarchyCheck.check_id,
    ReadabilityCheck.check_id,
})

PRO_CHECK_IDS: frozenset[str] = frozenset({
    EntityCoverageCheck.check_id,
    SchemaMarkupCheck.check_id,
    CitationsCheck.check_id,
    FreshnessCheck.check_id,
})


def default_checks() -> list[BaseCheck]:
    """Build a fresh list of all checks (free + Pro).

    Returned as a function (not a module-level constant) so check
    instances aren't created at import time, which keeps test imports
    fast and avoids surprising shared state across requests.
    """
    return [
        DirectAnswerCheck(),
        HTagHierarchyCheck(),
        ReadabilityCheck(),
        EntityCoverageCheck(),
        SchemaMarkupCheck(),
        CitationsCheck(),
        FreshnessCheck(),
    ]


def free_checks() -> list[BaseCheck]:
    """Checks A/B/C — always run, returned to every caller."""
    return [DirectAnswerCheck(), HTagHierarchyCheck(), ReadabilityCheck()]


def pro_checks() -> list[BaseCheck]:
    """Checks D/E/F/G — Pro-tier deep checks."""
    return [
        EntityCoverageCheck(),
        SchemaMarkupCheck(),
        CitationsCheck(),
        FreshnessCheck(),
    ]


def checks_for_plan(is_pro: bool) -> list[BaseCheck]:
    """Return the set of checks the caller is entitled to run."""
    if is_pro:
        return default_checks()
    return free_checks()
