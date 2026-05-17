"""AEO Content Scorer endpoint."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends

from app.models.schemas import (
    AEOAnalyzeRequest,
    AEOAnalyzeResponse,
    CheckResultModel,
    LockedCheckModel,
)
from app.services.aeo_checks import (
    checks_for_plan,
    free_checks,
    pro_checks,
)
from app.services.content_parser import (
    ContentParseError,
    ParsedContent,
    URLFetchError,
    fetch_url,
    parse,
)
from app.services.gating import (
    PaywallContext,
    require_pro_or_byok_or_quota_no_count,
)

router = APIRouter()

BAND_THRESHOLDS = (
    (85, "AEO Optimized"),
    (65, "Needs Improvement"),
    (40, "Significant Gaps"),
    (0, "Not AEO Ready"),
)


SUGGESTED_QUERY_MAX_LEN = 200
_STRIP_PREFIX_RE = re.compile(
    r"^(the|a|an|how to|why|what is|what are|guide to|introduction to)\s+",
    re.IGNORECASE,
)


@router.post(
    "/analyze",
    response_model=AEOAnalyzeResponse,
    response_model_exclude_none=True,
)
async def analyze(
    req: AEOAnalyzeRequest,
    ctx: PaywallContext = Depends(require_pro_or_byok_or_quota_no_count),
) -> AEOAnalyzeResponse:
    if req.input_type == "url":
        raw = await fetch_url(req.input_value)
    else:
        raw = req.input_value

    parsed = parse(raw, input_type=req.input_type)

    is_pro = ctx.has_active_subscription or ctx.using_byok
    results = [check.run(parsed) for check in checks_for_plan(is_pro)]

    locked: list[LockedCheckModel] | None = None
    if not is_pro:
        locked = [
            LockedCheckModel(check_id=c.check_id, name=c.name)
            for c in pro_checks()
        ]
    return _build_response(results, parsed, is_pro=is_pro, locked=locked)


def _build_response(
    results: list[CheckResultModel],
    parsed: ParsedContent | None = None,
    *,
    is_pro: bool = True,
    locked: list[LockedCheckModel] | None = None,
) -> AEOAnalyzeResponse:
    raw_total = sum(r.score for r in results)
    max_total = sum(r.max_score for r in results) or 60
    aeo_score = round((raw_total / max_total) * 100)
    band = _band_for(aeo_score)
    suggested = _suggest_target_query(parsed) if parsed is not None else None
    return AEOAnalyzeResponse(
        aeo_score=aeo_score,
        band=band,
        checks=results,
        suggested_target_query=suggested,
        locked_checks=locked,
        plan="pro" if is_pro else "free",
    )


def _suggest_target_query(parsed: ParsedContent) -> str | None:
    """Derive a default search-query suggestion the user can edit.

    Source priority:
      1. First H1 in `parsed.h_tags` (intent-bearing page title).
      2. Document `<title>` tag from the soup (often the H1 if absent).
      3. First non-empty sentence of `first_paragraph`, truncated.

    Returns None when no signal is strong enough.
    """
    for level, text in parsed.h_tags or []:
        if level == 1:
            normalised = _normalise_suggested(text)
            if normalised:
                return normalised

    if parsed.soup is not None:
        title_tag = parsed.soup.find("title")  # type: ignore[attr-defined]
        if title_tag is not None:
            text = title_tag.get_text(strip=True)
            normalised = _normalise_suggested(text)
            if normalised:
                return normalised

    first_para = (parsed.first_paragraph or "").strip()
    if first_para:
        sentence = re.split(r"(?<=[.!?])\s+", first_para, maxsplit=1)[0]
        normalised = _normalise_suggested(sentence)
        if normalised:
            return normalised
    return None


def _normalise_suggested(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    text = _STRIP_PREFIX_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > SUGGESTED_QUERY_MAX_LEN:
        text = text[: SUGGESTED_QUERY_MAX_LEN - 1].rstrip() + "…"
    if len(text.split()) < 2:
        return None
    return text


def _band_for(score: int) -> str:
    for threshold, label in BAND_THRESHOLDS:
        if score >= threshold:
            return label
    return "Not AEO Ready"


# Re-export for the global exception handler in app.main
__all__ = ["router", "URLFetchError", "ContentParseError"]
