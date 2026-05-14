"""Abstract base class for AEO checks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.models.schemas import CheckResultModel
from app.services.content_parser import ParsedContent


class BaseCheck(ABC):
    """Base class for an AEO check.

    Subclasses set the class-level metadata (`check_id`, `name`, `max_score`)
    and implement `run(content)` which consumes a shared `ParsedContent`
    and returns a `CheckResultModel`.
    """

    check_id: ClassVar[str]
    name: ClassVar[str]
    max_score: ClassVar[int] = 20

    @abstractmethod
    def run(self, content: ParsedContent) -> CheckResultModel: ...

    def _result(
        self,
        score: int,
        details: dict,
        recommendation: str | None,
    ) -> CheckResultModel:
        return CheckResultModel(
            check_id=self.check_id,
            name=self.name,
            passed=(score == self.max_score),
            score=score,
            max_score=self.max_score,
            details=details,
            recommendation=recommendation,
        )
