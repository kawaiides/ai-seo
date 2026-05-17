"""Query Fan-Out endpoint.

Gating order (see `app/services/gating.py`):
  1. valid BYOK header → use that key, no quota
  2. active Subscription → use server key, no quota
  3. free quota under DAILY_FREE_LIMIT → use server key, count one
  4. else 429 paywall envelope

When the request carries a valid BYOK key we build a per-request
OpenAIClient rather than the singleton — the singleton would leak the
customer's key spend onto a process-wide cache.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.schemas import FanoutRequest, FanoutResponse
from app.services import llm_client as llm_client_module
from app.services.fanout_engine import run_fanout
from app.services.gating import PaywallContext, require_pro_or_byok_or_quota
from app.services.llm_client import LLMClient

router = APIRouter()


def _client_for(ctx: PaywallContext) -> LLMClient:
    if ctx.byok_key:
        # Per-request client; never enters the singleton cache.
        return llm_client_module.OpenAIClient(api_key=ctx.byok_key)
    # Call via module attr so test monkeypatches reach this code path.
    return llm_client_module.get_llm_client()


@router.post(
    "/generate",
    response_model=FanoutResponse,
    response_model_exclude_none=True,
)
async def generate(
    req: FanoutRequest,
    ctx: PaywallContext = Depends(require_pro_or_byok_or_quota),
) -> FanoutResponse:
    client = _client_for(ctx)
    return await run_fanout(
        req.target_query,
        req.existing_content,
        client=client,
        target_locale=req.target_locale,
    )
