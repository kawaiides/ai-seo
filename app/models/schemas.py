"""Pydantic request/response models for the AEGIS API."""

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

InputType = Literal["url", "text"]

SubQueryType = Literal[
    "comparative",
    "feature_specific",
    "use_case",
    "trust_signals",
    "how_to",
    "definitional",
]

ALL_SUB_QUERY_TYPES: tuple[SubQueryType, ...] = (
    "comparative",
    "feature_specific",
    "use_case",
    "trust_signals",
    "how_to",
    "definitional",
)


class AEOAnalyzeRequest(BaseModel):
    input_type: InputType
    input_value: str = Field(..., min_length=1)

    @field_validator("input_value")
    @classmethod
    def _strip_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("input_value must not be empty or whitespace only")
        return v


class CheckResultModel(BaseModel):
    check_id: str
    name: str
    passed: bool
    score: int
    max_score: int
    details: dict[str, Any]
    recommendation: str | None = None


class AEOAnalyzeResponse(BaseModel):
    aeo_score: int
    band: str
    checks: list[CheckResultModel]


class URLFetchErrorResponse(BaseModel):
    error: str
    message: str
    detail: str


# -- Feature 2 — Query Fan-Out Engine --

# Strict parser-side schema for the LLM's raw JSON response.
# `extra="forbid"` makes hallucinated fields fail validation and trigger
# a retry, which is the core of the defensive parsing strategy.

class LLMSubQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SubQueryType
    query: str = Field(..., min_length=1)


class LLMFanoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_query: str
    sub_queries: list[LLMSubQuery]

    @model_validator(mode="after")
    def _validate_constraints(self) -> "LLMFanoutResponse":
        n = len(self.sub_queries)
        if not 10 <= n <= 15:
            raise ValueError(
                f"sub_queries must have 10–15 entries, got {n}"
            )
        type_counts = Counter(sq.type for sq in self.sub_queries)
        for required_type in ALL_SUB_QUERY_TYPES:
            if type_counts[required_type] < 2:
                raise ValueError(
                    f"type '{required_type}' must appear at least 2 times, "
                    f"got {type_counts[required_type]}"
                )
        seen: set[str] = set()
        for sq in self.sub_queries:
            if sq.query in seen:
                raise ValueError(f"duplicate sub-query string: {sq.query!r}")
            seen.add(sq.query)
        return self


# API-side schemas — what the endpoint actually returns.

class FanoutRequest(BaseModel):
    target_query: str = Field(..., min_length=1, max_length=500)
    existing_content: str | None = Field(default=None, max_length=50_000)

    @field_validator("target_query")
    @classmethod
    def _strip_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("target_query must not be empty or whitespace only")
        return v


class SubQueryResponse(BaseModel):
    type: SubQueryType
    query: str
    covered: bool | None = None
    similarity_score: float | None = None


class GapSummary(BaseModel):
    covered: int
    total: int
    coverage_percent: int
    covered_types: list[SubQueryType]
    missing_types: list[SubQueryType]


class FanoutResponse(BaseModel):
    target_query: str
    model_used: str
    total_sub_queries: int
    sub_queries: list[SubQueryResponse]
    gap_summary: GapSummary | None = None


class LLMUnavailableErrorResponse(BaseModel):
    error: str
    message: str
    detail: str
