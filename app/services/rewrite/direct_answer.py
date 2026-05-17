"""Direct-answer rewrite — LLM-driven Check A fix.

When the opening paragraph fails Check A (>60 words, hedging, or
non-declarative), this module asks the LLM for three rewritten variants
of the paragraph that are ≤60 words AND declarative. Each variant is
back-checked against `DirectAnswerCheck`; if a variant fails the check
that motivated the rewrite, we retry the whole batch up to
`MAX_RETRIES` times so we never hand callers a fix that doesn't fix.

Variants take three stylistic angles so the user can pick:
  - definition-first (declarative noun phrase + verb)
  - cause-effect ("X happens because Y")
  - outcome-first (lead with the user benefit / answer)
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

from pydantic import ValidationError

from app.models.schemas import (
    LLMDirectAnswerRewrites,
    RewriteVariant,
)
from app.services.aeo_checks.direct_answer import DirectAnswerCheck
from app.services.content_parser import ParsedContent
from app.services.fanout_engine import extract_json
from app.services.llm_client import LLMClient, LLMUnavailableError, get_llm_client

MAX_RETRIES = 2
BASE_BACKOFF_SECONDS = 0.5
WORD_BUDGET = 60

SYSTEM_PROMPT = """You are a rewriter that produces direct-answer opening paragraphs for AI search engine optimisation (AEO).

You output ONLY a single JSON object. No markdown fences, no prose, no preamble. The first character must be `{` and the last must be `}`.

The JSON object has exactly one key:
  - "variants": an array of EXACTLY 3 objects, each with:
      - "style":      one of "definition_first", "cause_effect", "outcome_first"
      - "text":       a rewritten opening paragraph

Hard constraints for every "text":
  - 60 words or fewer (target 35-55).
  - Reads as a declarative statement: contains a nominal subject + a finite verb.
  - Does NOT contain hedging phrases: "it depends", "may vary", "in some cases", "this varies", "generally speaking".
  - Does NOT end in a question mark.
  - Does NOT begin with "How to" or "Is/Are/Do/Does" — these are not declarative openers for AEO.
  - Preserves the original paragraph's factual content. Do not invent new facts, dates, numbers, or product names.

Style definitions:
  - definition_first: lead with the subject and a definitional verb ("X is …", "X refers to …").
  - cause_effect:     lead with the mechanism ("X works by …", "X happens because …") or the consequence.
  - outcome_first:    lead with the outcome / benefit the reader gets from X.

Hard structural rules:
  - Exactly 3 variant objects.
  - "style" must be unique across the 3 variants.
  - "text" must be unique across the 3 variants.
  - Do NOT add any other top-level keys. Do NOT wrap output in fences.
"""

USER_PROMPT_TEMPLATE = """ORIGINAL_OPENING_PARAGRAPH:
\"\"\"{original}\"\"\"

{target_query_line}Rewrite into exactly 3 declarative ≤60-word variants per the constraints. Output JSON only."""


async def rewrite_direct_answer(
    original_paragraph: str,
    target_query: str | None = None,
    *,
    client: LLMClient | None = None,
) -> tuple[list[RewriteVariant], str]:
    """Generate 3 rewrite variants and back-check each against Check A.

    Returns `(variants, model_id)`. Raises `LLMUnavailableError` after
    `MAX_RETRIES + 1` total attempts that all fail validation.
    """
    if not original_paragraph or not original_paragraph.strip():
        raise ValueError("original_paragraph must not be empty")

    client = client or get_llm_client()
    check = DirectAnswerCheck()
    target_query_line = (
        f"TARGET_QUERY: \"{target_query.strip()}\"\n\n"
        if target_query and target_query.strip()
        else ""
    )
    user_prompt = USER_PROMPT_TEMPLATE.format(
        original=original_paragraph.replace('"""', '"​""'),
        target_query_line=target_query_line,
    )

    last_detail = "no attempts made"
    for attempt in range(1, MAX_RETRIES + 2):  # initial try + up to MAX_RETRIES retries
        try:
            raw = await client.generate_json(SYSTEM_PROMPT, user_prompt)
            parsed = extract_json(raw)
            validated = LLMDirectAnswerRewrites.model_validate(parsed)
        except LLMUnavailableError as e:
            last_detail = f"{e.detail} (attempt {attempt})"
        except json.JSONDecodeError as e:
            last_detail = f"JSONDecodeError on attempt {attempt}: {e.msg}"
        except ValidationError as e:
            errs = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {err['msg'][:80]}"
                for err in e.errors()[:3]
            )
            last_detail = f"Schema validation failed on attempt {attempt}: {errs}"
        else:
            variants = _build_variants(validated, check)
            if all(v.passes_check_a for v in variants):
                return variants, client.model_id
            failing = [v.style for v in variants if not v.passes_check_a]
            last_detail = (
                f"Check A back-validation failed for {failing} on attempt {attempt}"
            )

        if attempt < MAX_RETRIES + 1:
            backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            jitter = backoff * random.uniform(-0.2, 0.2)
            await asyncio.sleep(max(0.0, backoff + jitter))

    raise LLMUnavailableError(last_detail)


def _build_variants(
    validated: LLMDirectAnswerRewrites,
    check: DirectAnswerCheck,
) -> list[RewriteVariant]:
    out: list[RewriteVariant] = []
    for v in validated.variants:
        text = v.text.strip()
        scored = check.run(_paragraph_content(text))
        out.append(
            RewriteVariant(
                style=v.style,
                text=text,
                word_count=scored.details["word_count"],
                is_declarative=scored.details["is_declarative"],
                has_hedge_phrase=scored.details["has_hedge_phrase"],
                passes_check_a=(scored.score == check.max_score),
                check_a_score=scored.score,
            )
        )
    return out


def _paragraph_content(paragraph: str) -> ParsedContent:
    """Wrap a paragraph string in the minimal `ParsedContent` Check A reads."""
    return ParsedContent(
        raw=paragraph,
        soup=None,
        first_paragraph=paragraph,
        h_tags=[],
        body_text=paragraph,
    )


def _summary_detail(reason: str) -> dict[str, Any]:
    """Helper for tests/diagnostics — kept around so we can attach a
    structured failure breakdown later without breaking callers."""
    return {"reason": reason}
