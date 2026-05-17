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
    "default_checks",
]


def default_checks() -> list[BaseCheck]:
    """Build a fresh list of default checks.

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
