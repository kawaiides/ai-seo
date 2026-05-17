"""REST API key management endpoints (Phase E)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import ApiKey, OrgRole
from app.models.schemas import (
    ApiKeyCreate,
    ApiKeyMintedRecord,
    ApiKeyRecord,
)
from app.services.api_keys import (
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.services.orgs import OrgContext, require_role

router = APIRouter()


@router.post("", response_model=ApiKeyMintedRecord, status_code=201)
async def create(
    req: ApiKeyCreate,
    ctx: OrgContext = Depends(require_role(OrgRole.owner)),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyMintedRecord:
    minted = await create_api_key(
        session, org_id=ctx.org.id, name=req.name, scopes=req.scopes
    )
    return ApiKeyMintedRecord(
        id=minted.api_key_id,
        org_id=minted.org_id,
        name=minted.name,
        prefix=minted.prefix,
        scopes=list(minted.scopes),
        plaintext=minted.plaintext,
    )


@router.get("", response_model=list[ApiKeyRecord])
async def list_keys(
    ctx: OrgContext = Depends(require_role(OrgRole.viewer)),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyRecord]:
    rows = await list_api_keys(session, ctx.org.id)
    return [_serialise(k) for k in rows]


@router.delete("/{api_key_id}", status_code=204)
async def revoke(
    api_key_id: UUID,
    ctx: OrgContext = Depends(require_role(OrgRole.owner)),
    session: AsyncSession = Depends(get_session),
) -> None:
    key = await session.get(ApiKey, api_key_id)
    if key is None or key.org_id != ctx.org.id:
        raise HTTPException(status_code=404, detail={"error": "api_key_not_found"})
    await revoke_api_key(session, api_key_id)


def _serialise(k: ApiKey) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=k.id,
        name=k.name,
        prefix=k.prefix,
        scopes=list(k.scopes),
        last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
        revoked_at=k.revoked_at.isoformat() if k.revoked_at else None,
        created_at=k.created_at.isoformat() if k.created_at else "",
    )
