"""Query Fan-Out Engine.

Generates 10–15 typed sub-queries via LLM with strict schema validation
and a 3-attempt retry loop. Optionally runs gap analysis against pasted
content.

Design summary (full rationale in PROMPT_LOG.md and SETUP.md):
- Triple defense for JSON robustness:
    1. Prompt explicitly demands JSON-only output with anchored structure.
    2. OpenAI JSON mode (`response_format={"type":"json_object"}`).
    3. Pydantic strict schema with `extra="forbid"` and a model_validator
       enforcing 10–15 / ≥2-per-type / unique queries.
- On any failure (parse, schema, network), retry up to 3 attempts total
  with jittered exponential backoff. After the third failure raise
  `LLMUnavailableError` with the most recent failure reason — the API's
  exception handler turns this into the spec's 503 envelope.
- Cross-domain few-shot example in the prompt: the example uses CRM
  software, never the actual target query, to deter verbatim copying.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re

from pydantic import ValidationError

from app.models.schemas import (
    FanoutResponse,
    LLMFanoutResponse,
    SubQueryResponse,
)
from app.services.llm_client import LLMClient, LLMUnavailableError, get_llm_client


SYSTEM_PROMPT = """You are a query decomposition engine for an AI search optimization tool.
Given a single TARGET_QUERY, you generate sub-queries that an AI search
engine (Perplexity, Google AI Overviews, ChatGPT Search) would internally
fan out to in order to construct a comprehensive answer.

You output ONLY a single JSON object. No markdown fences, no prose, no
preamble, no explanation. The first character of your output must be `{`
and the last must be `}`.

The JSON object has exactly two keys:
  - "target_query":  string, echoing the input verbatim
  - "sub_queries":   array of 10 to 15 objects

Each sub-query object has exactly two keys:
  - "type":   one of "comparative", "feature_specific", "use_case",
              "trust_signals", "how_to", "definitional"
  - "query":  a natural-language search query string, 4-15 words

Type definitions:
  - comparative:      compares the target against named alternatives
  - feature_specific: focuses on a specific capability or technical attribute
  - use_case:         a concrete real-world application, persona, or scenario
  - trust_signals:    reviews, case studies, ratings, year-stamped credibility
  - how_to:           procedural, instructional, "how to ..." phrasing
  - definitional:     conceptual, "what is ...", "define ..." phrasing

Hard constraints:
  - Total sub-queries: between 10 and 15 inclusive.
  - Every type above must appear AT LEAST 2 times.
  - Each "query" string must be unique.
  - Do NOT add any other top-level keys. Do NOT add keys inside
    sub-query objects. No "id", no "rationale", no "score".
  - Do NOT wrap the JSON in ```json fences or any other markup.
"""


# The example deliberately uses an UNRELATED domain (CRM software) so the
# model can't copy it verbatim when the actual target query is something
# else. Verified via live ablation — see PROMPT_LOG.md.
USER_PROMPT_TEMPLATE = """TARGET_QUERY: "{target_query}"

Example for the unrelated target query "best CRM software for small business":
{{
  "target_query": "best CRM software for small business",
  "sub_queries": [
    {{"type": "comparative",      "query": "HubSpot vs Salesforce for small business CRM"}},
    {{"type": "comparative",      "query": "Pipedrive or Zoho CRM for 10-person sales team"}},
    {{"type": "feature_specific", "query": "small business CRM with built-in email sequencing"}},
    {{"type": "feature_specific", "query": "CRM software with QuickBooks integration"}},
    {{"type": "use_case",         "query": "CRM for a solo real estate agent managing 200 leads"}},
    {{"type": "use_case",         "query": "CRM for a 5-person B2B SaaS startup"}},
    {{"type": "trust_signals",    "query": "small business CRM reviews on G2 in 2025"}},
    {{"type": "trust_signals",    "query": "CRM software case studies for sub-50 employee firms"}},
    {{"type": "how_to",           "query": "how to migrate from spreadsheets to a small business CRM"}},
    {{"type": "how_to",           "query": "how to set up a sales pipeline in a small business CRM"}},
    {{"type": "definitional",     "query": "what is a CRM and what does it do for small businesses"}},
    {{"type": "definitional",     "query": "what features define an SMB-focused CRM platform"}}
  ]
}}

Now generate the equivalent JSON object for TARGET_QUERY. Output JSON only."""


MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5  # 0.5s, 1.5s with jitter


# Strip a fenced ```json ... ``` wrapper, optional language tag.
_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def extract_json(raw: str) -> dict:
    """Best-effort JSON extraction.

    1. Try `json.loads` directly.
    2. If wrapped in a ```json ... ``` fence, strip and retry.
    3. Fall back to first-`{` / last-`}` slice (handles leading/trailing prose).

    Raises `json.JSONDecodeError` if all three fail.
    """
    text = raw.strip()
    if not text:
        raise json.JSONDecodeError("empty response", text, 0)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence_match = _FENCE_RE.match(text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            pass

    # Re-raise with the original text so the retry detail is informative.
    raise json.JSONDecodeError("could not extract JSON object", text, 0)


async def _attempt(
    client: LLMClient, target_query: str
) -> LLMFanoutResponse:
    """One LLM call → JSON parse → Pydantic validate.

    Each step's failure surfaces as a different exception type, which the
    retry loop translates into a single informative `detail` string.
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(target_query=target_query)
    raw = await _generate_raw(client, target_query, SYSTEM_PROMPT, user_prompt)
    parsed = extract_json(raw)
    return LLMFanoutResponse.model_validate(parsed)


async def _generate_raw(
    client: LLMClient, target_query: str, system: str, user: str
) -> str:
    """Optional in-memory cache layer over the LLM call.

    Cache is opt-in via `AEGIS_FANOUT_CACHE=1` and auto-disabled inside
    pytest so test isolation isn't broken. Cache key is (model_id,
    target_query); values are raw response strings, so the parser still
    runs on cache hits and validation regressions still surface.
    """
    cache_enabled = (
        os.getenv("AEGIS_FANOUT_CACHE") == "1"
        and "PYTEST_CURRENT_TEST" not in os.environ
    )
    if not cache_enabled:
        return await client.generate_json(system, user)

    return await _cached_call(client, client.model_id, target_query, system, user)


_cache_store: dict[tuple[str, str], str] = {}


async def _cached_call(
    client: LLMClient, model_id: str, target_query: str, system: str, user: str
) -> str:
    key = (model_id, target_query)
    if key in _cache_store:
        return _cache_store[key]
    result = await client.generate_json(system, user)
    # Crude FIFO eviction at 64 entries to bound memory.
    if len(_cache_store) >= 64:
        _cache_store.pop(next(iter(_cache_store)))
    _cache_store[key] = result
    return result


async def generate_fanout(
    target_query: str,
    *,
    client: LLMClient | None = None,
) -> tuple[LLMFanoutResponse, str]:
    """Generate sub-queries with retries.

    Returns `(validated_response, model_id)`. Raises `LLMUnavailableError`
    with an informative `detail` after `MAX_ATTEMPTS` failures.
    """
    client = client or get_llm_client()
    last_detail = "no attempts made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await _attempt(client, target_query)
            return response, client.model_id
        except LLMUnavailableError as e:
            # Network / SDK error — already wrapped with detail. Retry.
            last_detail = f"{e.detail} (attempt {attempt})"
        except json.JSONDecodeError as e:
            last_detail = f"JSONDecodeError on attempt {attempt}: {e.msg}"
        except ValidationError as e:
            # Compact the validation errors to a single line.
            errs = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {err['msg'][:80]}"
                for err in e.errors()[:3]
            )
            last_detail = f"Schema validation failed on attempt {attempt}: {errs}"

        if attempt < MAX_ATTEMPTS:
            backoff = BASE_BACKOFF_SECONDS * (3 ** (attempt - 1))
            jitter = backoff * random.uniform(-0.2, 0.2)
            await asyncio.sleep(max(0.0, backoff + jitter))

    raise LLMUnavailableError(last_detail)


async def run_fanout(
    target_query: str,
    existing_content: str | None,
    *,
    client: LLMClient | None = None,
) -> FanoutResponse:
    """End-to-end orchestrator used by the API endpoint.

    Generates sub-queries, then if content is provided, runs gap analysis
    and assembles the full response. When no content is provided,
    `covered`/`similarity_score`/`gap_summary` are all None — the route
    uses `response_model_exclude_none=True` to drop them from the JSON.
    """
    llm_response, model_id = await generate_fanout(target_query, client=client)

    if existing_content is None or not existing_content.strip():
        sub_queries = [
            SubQueryResponse(type=sq.type, query=sq.query)
            for sq in llm_response.sub_queries
        ]
        return FanoutResponse(
            target_query=llm_response.target_query,
            model_used=model_id,
            total_sub_queries=len(sub_queries),
            sub_queries=sub_queries,
            gap_summary=None,
        )

    # Lazy import to avoid pulling sentence-transformers when not needed.
    from app.services.gap_analyzer import build_gap_summary, score_subqueries

    scores = score_subqueries(llm_response.sub_queries, existing_content)
    sub_queries = [
        SubQueryResponse(
            type=sq.type,
            query=sq.query,
            covered=covered,
            similarity_score=score,
        )
        for sq, (covered, score) in zip(llm_response.sub_queries, scores)
    ]
    gap_summary = build_gap_summary(sub_queries)
    return FanoutResponse(
        target_query=llm_response.target_query,
        model_used=model_id,
        total_sub_queries=len(sub_queries),
        sub_queries=sub_queries,
        gap_summary=gap_summary,
    )


def reset_cache() -> None:
    """Test helper: clear the in-memory cache."""
    _cache_store.clear()
