"""AEO Content Scorer endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import (
    AEOAnalyzeRequest,
    AEOAnalyzeResponse,
    CheckResultModel,
)
from app.services.aeo_checks import default_checks
from app.services.content_parser import (
    ContentParseError,
    URLFetchError,
    fetch_url,
    parse,
)

router = APIRouter()

BAND_THRESHOLDS = (
    (85, "AEO Optimized"),
    (65, "Needs Improvement"),
    (40, "Significant Gaps"),
    (0, "Not AEO Ready"),
)


@router.post("/analyze", response_model=AEOAnalyzeResponse)
async def analyze(req: AEOAnalyzeRequest) -> AEOAnalyzeResponse:
    if req.input_type == "url":
        raw = await fetch_url(req.input_value)
    else:
        raw = req.input_value

    parsed = parse(raw, input_type=req.input_type)

    results = [check.run(parsed) for check in default_checks()]
    return _build_response(results)


def _build_response(results: list[CheckResultModel]) -> AEOAnalyzeResponse:
    raw_total = sum(r.score for r in results)
    max_total = sum(r.max_score for r in results) or 60
    aeo_score = round((raw_total / max_total) * 100)
    band = _band_for(aeo_score)
    return AEOAnalyzeResponse(aeo_score=aeo_score, band=band, checks=results)


def _band_for(score: int) -> str:
    for threshold, label in BAND_THRESHOLDS:
        if score >= threshold:
            return label
    return "Not AEO Ready"


# Re-export for the global exception handler in app.main
__all__ = ["router", "URLFetchError", "ContentParseError"]
