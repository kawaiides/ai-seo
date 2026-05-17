"""Bring-Your-Own-Key endpoints.

  POST /api/byok/validate    test a key against the provider; cache result
  DELETE /api/byok           drop the cached validation (logout-of-BYOK)

The key MUST arrive in the request body — never as a query param. The
endpoint stores only sha256 of the key.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import BYOKProvider, BYOKValidation, User
from app.services.auth import require_user
from app.services.byok import hash_key, validate_key

router = APIRouter(tags=["byok"])


class BYOKValidateRequest(BaseModel):
    key: str = Field(..., min_length=8, max_length=512)
    provider: Literal["openai", "gemini"] = "openai"


class BYOKValidateResponse(BaseModel):
    valid: bool
    provider: str
    key_hash_prefix: str  # first 12 chars of sha256 — lets the UI fingerprint without echoing


@router.post("/api/byok/validate", response_model=BYOKValidateResponse)
async def validate(
    req: BYOKValidateRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_session),
) -> BYOKValidateResponse:
    provider = BYOKProvider(req.provider)
    is_valid = await validate_key(db, user, req.key, provider=provider)
    if not is_valid:
        # 200 with valid=False would let curl scripts spam-brute. Surface a 401
        # so the UI can flip the badge red and clear localStorage immediately.
        raise HTTPException(status_code=401, detail="provider rejected key")
    return BYOKValidateResponse(
        valid=True,
        provider=req.provider,
        key_hash_prefix=hash_key(req.key)[:12],
    )


@router.delete("/api/byok", status_code=204)
async def revoke(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_session),
) -> None:
    await db.execute(delete(BYOKValidation).where(BYOKValidation.user_id == user.id))
