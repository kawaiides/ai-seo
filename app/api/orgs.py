"""Org management endpoints (Phase D)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Org, OrgMember, OrgRole, User
from app.models.schemas import (
    OrgCreate,
    OrgInviteRequest,
    OrgMemberRecord,
    OrgRecord,
)
from app.services.audit_log import (
    ACTION_ORG_CREATE,
    ACTION_ORG_MEMBER_INVITE,
    ACTION_ORG_MEMBER_REMOVE,
    record_audit_event,
)
from app.services.auth import get_current_user, get_or_create_user_by_email
from app.services.orgs import (
    OrgContext,
    create_org,
    invite_member,
    list_members,
    remove_member,
    require_role,
)

router = APIRouter()


@router.post("", response_model=OrgRecord, status_code=201)
async def create(
    req: OrgCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> OrgRecord:
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
    org = await create_org(
        session, owner=user, name=req.name, slug=req.slug, logo_url=req.logo_url
    )
    await record_audit_event(
        session,
        action=ACTION_ORG_CREATE,
        actor_user_id=user.id,
        subject_org_id=org.id,
        meta={"name": org.name, "slug": org.slug},
        request=request,
    )
    return _serialise_org(org)


@router.get("", response_model=list[OrgRecord])
async def list_my_orgs(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[OrgRecord]:
    """Every org the caller is a member of, newest first.

    Powers the org-switcher in the Customer Console. Includes orgs in
    every role (viewer / editor / owner) — the UI shows the user's role
    chip per org so they know which they can manage.
    """
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
    rows = (
        await session.execute(
            select(Org)
            .join(OrgMember, OrgMember.org_id == Org.id)
            .where(OrgMember.user_id == user.id)
            .order_by(Org.created_at.desc())
        )
    ).scalars().all()
    return [_serialise_org(o) for o in rows]


@router.get("/{org_id}", response_model=OrgRecord)
async def get_org(
    ctx: OrgContext = Depends(require_role(OrgRole.viewer)),
) -> OrgRecord:
    return _serialise_org(ctx.org)


@router.post("/{org_id}/members", response_model=OrgMemberRecord, status_code=201)
async def invite(
    org_id: UUID,
    req: OrgInviteRequest,
    request: Request,
    ctx: OrgContext = Depends(require_role(OrgRole.owner)),
    session: AsyncSession = Depends(get_session),
) -> OrgMemberRecord:
    # Resolve which User row this invite points at. Two paths:
    # (a) explicit user_id  → legacy callers + tests
    # (b) email             → Customer Console UI; upserts a placeholder
    #                         user when no account exists yet, so the
    #                         invitee can claim it on their first sign-in.
    if req.user_id is None and not req.email:
        raise HTTPException(
            status_code=422,
            detail={"error": "missing_invitee", "message": "Provide user_id or email."},
        )
    if req.user_id is not None:
        target = await session.get(User, req.user_id)
        if target is None:
            raise HTTPException(status_code=404, detail={"error": "user_not_found"})
    else:
        target, _ = await get_or_create_user_by_email(session, req.email)

    member = await invite_member(
        session, org_id=org_id, user=target, role=OrgRole(req.role)
    )
    await record_audit_event(
        session,
        action=ACTION_ORG_MEMBER_INVITE,
        actor_user_id=ctx.user.id,
        subject_user_id=target.id,
        subject_org_id=org_id,
        meta={"role": req.role, "email": target.email},
        request=request,
    )
    return _serialise_member(member)


@router.get("/{org_id}/members", response_model=list[OrgMemberRecord])
async def members(
    org_id: UUID,
    ctx: OrgContext = Depends(require_role(OrgRole.viewer)),
    session: AsyncSession = Depends(get_session),
) -> list[OrgMemberRecord]:
    rows = await list_members(session, org_id)
    return [_serialise_member(m) for m in rows]


@router.delete("/{org_id}/members/{user_id}", status_code=204)
async def remove(
    org_id: UUID,
    user_id: UUID,
    request: Request,
    ctx: OrgContext = Depends(require_role(OrgRole.owner)),
    session: AsyncSession = Depends(get_session),
) -> None:
    await remove_member(session, org_id=org_id, target_user_id=user_id)
    await record_audit_event(
        session,
        action=ACTION_ORG_MEMBER_REMOVE,
        actor_user_id=ctx.user.id,
        subject_user_id=user_id,
        subject_org_id=org_id,
        request=request,
    )


def _serialise_org(org: Org) -> OrgRecord:
    return OrgRecord(
        id=org.id,
        name=org.name,
        slug=org.slug,
        logo_url=org.logo_url,
        branded_subdomain=org.branded_subdomain,
        created_at=org.created_at.isoformat() if org.created_at else "",
    )


def _serialise_member(m: OrgMember) -> OrgMemberRecord:
    return OrgMemberRecord(
        id=m.id,
        user_id=m.user_id,
        role=m.role.value,
        invited_email=m.invited_email,
        accepted_at=m.accepted_at.isoformat() if m.accepted_at else None,
        created_at=m.created_at.isoformat() if m.created_at else "",
    )
