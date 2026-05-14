"""Query Fan-Out endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import FanoutRequest, FanoutResponse
from app.services.fanout_engine import run_fanout

router = APIRouter()


@router.post(
    "/generate",
    response_model=FanoutResponse,
    response_model_exclude_none=True,
)
async def generate(req: FanoutRequest) -> FanoutResponse:
    return await run_fanout(req.target_query, req.existing_content)
