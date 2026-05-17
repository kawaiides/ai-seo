"""Ordered chain of contact finders.

Each prospect is tried against finders in order; the first one to return
a non-empty result wins. This keeps Hunter.io credit consumption pinned
to prospects where the cheap `mailto:` scrape returned nothing.
"""

from __future__ import annotations

import logging
from typing import Sequence

from app.db.models import Contact, Prospect

log = logging.getLogger(__name__)


class CompositeContactFinder:
    """Try each underlying finder in order; stop at the first hit."""

    def __init__(self, finders: Sequence) -> None:
        # Keep references regardless of `enabled` so a finder whose env
        # is wired mid-flight (test monkey-patches, hot reloads) still runs.
        self._finders = list(finders)

    @property
    def names(self) -> list[str]:
        return [getattr(f, "name", type(f).__name__) for f in self._finders]

    async def find(self, prospect: Prospect) -> list[Contact]:
        for finder in self._finders:
            try:
                results = await finder.find(prospect)
            except Exception as e:  # noqa: BLE001 — one bad provider mustn't kill the chain
                log.warning(
                    "composite: finder %s raised: %s",
                    getattr(finder, "name", type(finder).__name__), e,
                )
                continue
            if results:
                log.info(
                    "composite: prospect=%s matched via %s (%d contacts)",
                    prospect.id, getattr(finder, "name", type(finder).__name__),
                    len(results),
                )
                return results
        return []
