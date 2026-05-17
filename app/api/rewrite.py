"""Rewrite endpoints (Phase B.1).

Two sibling endpoints:

  POST /api/rewrite/direct_answer  — LLM-driven, 3 back-validated variants
  POST /api/rewrite/headings       — deterministic, no LLM, free for users

The direct-answer endpoint shares the existing `LLMUnavailableError`
exception handler so the spec's 503 envelope is reused.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    BulkRewriteRequest,
    BulkRewriteResponse,
    DirectAnswerRewriteRequest,
    DirectAnswerRewriteResponse,
    HeadingOperation,
    HeadingPair,
    HeadingsFixRequest,
    HeadingsFixResponse,
    SchemaGenRequest,
    SchemaGenResult,
)
from app.services.content_parser import fetch_url, parse
from app.services.rewrite.bulk import run_bulk_rewrite
from app.services.rewrite.direct_answer import rewrite_direct_answer
from app.services.rewrite.headings import autofix_headings, to_html
from app.services.rewrite.schema_gen import generate_schema

router = APIRouter()


@router.post(
    "/direct_answer",
    response_model=DirectAnswerRewriteResponse,
)
async def direct_answer(
    req: DirectAnswerRewriteRequest,
) -> DirectAnswerRewriteResponse:
    raw = await fetch_url(req.input_value) if req.input_type == "url" else req.input_value
    parsed = parse(raw, input_type=req.input_type)
    paragraph = parsed.first_paragraph or ""
    if not paragraph.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "no_opening_paragraph",
                "message": "Could not find an opening paragraph to rewrite.",
                "detail": "first_paragraph was empty after parsing",
            },
        )
    variants, model_id = await rewrite_direct_answer(
        paragraph, target_query=req.target_query
    )
    return DirectAnswerRewriteResponse(
        original_paragraph=paragraph,
        variants=variants,
        model_used=model_id,
    )


@router.post(
    "/headings",
    response_model=HeadingsFixResponse,
)
async def headings(req: HeadingsFixRequest) -> HeadingsFixResponse:
    raw = await fetch_url(req.input_value) if req.input_type == "url" else req.input_value
    parsed = parse(raw, input_type=req.input_type)
    result = autofix_headings(parsed.h_tags)
    return HeadingsFixResponse(
        original_h_tags=[HeadingPair(level=l, text=t) for l, t in result["original"]],
        fixed_h_tags=[HeadingPair(level=l, text=t) for l, t in result["fixed"]],
        operations=[HeadingOperation(**op) for op in result["operations"]],
        fixed_html=to_html(result["fixed"]),
    )


@router.post(
    "/schema_gen",
    response_model=SchemaGenResult,
)
async def schema_gen(req: SchemaGenRequest) -> SchemaGenResult:
    raw = await fetch_url(req.input_value) if req.input_type == "url" else req.input_value
    parsed = parse(raw, input_type=req.input_type)
    if not parsed.body_text or not parsed.body_text.strip():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_body",
                "message": "Could not find body content to generate schema from.",
                "detail": "parsed.body_text was empty",
            },
        )
    return await generate_schema(parsed, intent=req.intent)


@router.post(
    "/bulk",
    response_model=BulkRewriteResponse,
)
async def bulk(req: BulkRewriteRequest) -> BulkRewriteResponse:
    raw = await fetch_url(req.input_value) if req.input_type == "url" else req.input_value
    parsed = parse(raw, input_type=req.input_type)
    outcome = await run_bulk_rewrite(
        parsed,
        target_query=req.target_query,
        include_direct_answer=req.include_direct_answer,
        include_headings=req.include_headings,
        include_schema=req.include_schema,
    )
    return BulkRewriteResponse(**outcome.__dict__)
