"""Org / OrgMember service (Phase D).

CRUD + role checks for the multi-tenant container. Auth wiring uses the
existing `get_current_user` dependency from `app.services.auth`; this
module adds the *org-scoped* role enforcement layer on top of it.

The roles, ordered by privilege (descending):

  owner  → can do anything, including transfer + delete the org.
  editor → run audits, edit sites, add comments.
  viewer → read-only on reports + comments. Cannot trigger billable work.

`require_role(min_role)` returns a FastAPI dependency factory; route
handlers list it after `Depends(get_current_user)` to inherit the
authenticated user identity.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Org, OrgMember, OrgRole, User
from app.services.auth import get_current_user

# Role privilege ordering used by `require_role`.
ROLE_RANK: dict[OrgRole, int] = {
    OrgRole.viewer: 0,
    OrgRole.editor: 1,
    OrgRole.owner: 2,
}


@dataclass(frozen=True)
class OrgContext:
    """What route handlers receive: the org, the calling user's membership, and
    the user. Captured as a dataclass so route signatures stay short."""

    org: Org
    member: OrgMember
    user: User


# -------------------- CRUD --------------------


async def create_org(
    session: AsyncSession,
    *,
    owner: User,
    name: str,
    slug: str | None = None,
    logo_url: str | None = None,
) -> Org:
    """Create an org with the calling user as the sole `owner`."""
    final_slug = _validate_slug(slug) if slug else _generate_slug(name)
    org = Org(name=name.strip(), slug=final_slug, logo_url=logo_url)
    session.add(org)
    try:
        await session.flush()
    except IntegrityError as e:
        raise HTTPException(
            status_code=409, detail={"error": "slug_taken", "detail": str(e.orig)}
        ) from e
    session.add(
        OrgMember(
            org_id=org.id,
            user_id=owner.id,
            role=OrgRole.owner,
            accepted_at=datetime.now(tz=timezone.utc),
        )
    )
    await session.flush()
    return org


async def invite_member(
    session: AsyncSession,
    *,
    org_id: UUID,
    user: User,
    role: OrgRole,
) -> OrgMember:
    """Add an existing `User` to an org with `role`. Idempotent on
    `(org_id, user_id)` — re-inviting the same user updates the role."""
    if role not in ROLE_RANK:
        raise HTTPException(status_code=422, detail={"error": "invalid_role"})

    existing = await session.scalar(
        select(OrgMember).where(
            OrgMember.org_id == org_id, OrgMember.user_id == user.id
        )
    )
    if existing is not None:
        existing.role = role
        return existing

    member = OrgMember(
        org_id=org_id,
        user_id=user.id,
        role=role,
        invited_email=user.email,
        accepted_at=datetime.now(tz=timezone.utc),
    )
    session.add(member)
    await session.flush()
    return member


async def list_members(session: AsyncSession, org_id: UUID) -> list[OrgMember]:
    return (
        await session.scalars(
            select(OrgMember)
            .where(OrgMember.org_id == org_id)
            .order_by(OrgMember.created_at.asc())
        )
    ).all()


async def remove_member(
    session: AsyncSession,
    *,
    org_id: UUID,
    target_user_id: UUID,
) -> None:
    member = await session.scalar(
        select(OrgMember).where(
            OrgMember.org_id == org_id, OrgMember.user_id == target_user_id
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail={"error": "member_not_found"})
    if member.role == OrgRole.owner:
        # Block removing the last owner; an org must always have ≥1.
        remaining_owners = await session.scalar(
            select(OrgMember).where(
                OrgMember.org_id == org_id,
                OrgMember.role == OrgRole.owner,
                OrgMember.user_id != target_user_id,
            )
        )
        if remaining_owners is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "cannot_remove_last_owner"},
            )
    await session.delete(member)
    await session.flush()


# -------------------- Role checks --------------------


def has_min_role(member: OrgMember, min_role: OrgRole) -> bool:
    return ROLE_RANK[member.role] >= ROLE_RANK[min_role]


async def require_membership(
    session: AsyncSession, *, org_id: UUID, user: User
) -> OrgMember:
    member = await session.scalar(
        select(OrgMember).where(
            OrgMember.org_id == org_id, OrgMember.user_id == user.id
        )
    )
    if member is None:
        raise HTTPException(status_code=403, detail={"error": "not_a_member"})
    return member


def require_role(
    min_role: OrgRole,
) -> Callable[..., Awaitable[OrgContext]]:
    """FastAPI dependency factory enforcing `min_role` on the calling user.

    The route handler receives an `OrgContext` so it doesn't need to
    re-fetch the org or the member row.
    """

    async def _dep(
        org_id: UUID = Path(...),
        session: AsyncSession = Depends(get_session),
        user: User = Depends(get_current_user),
    ) -> OrgContext:
        if user is None:
            raise HTTPException(status_code=401, detail={"error": "auth_required"})
        org = await session.get(Org, org_id)
        if org is None:
            raise HTTPException(status_code=404, detail={"error": "org_not_found"})
        member = await require_membership(session, org_id=org_id, user=user)
        if not has_min_role(member, min_role):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "insufficient_role",
                    "required": min_role.value,
                    "held": member.role.value,
                },
            )
        return OrgContext(org=org, member=member, user=user)

    return _dep


# -------------------- Slug helpers --------------------


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not _SLUG_RE.fullmatch(slug) or len(slug) > 64:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_slug",
                "detail": "slug must be lowercase letters, digits, and hyphens",
            },
        )
    return slug


def _generate_slug(name: str) -> str:
    """Generate a slug from `name`; append a 6-char nonce so the chance
    of collision on a popular name stays low."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
    if not base:
        base = "org"
    return f"{base}-{secrets.token_hex(3)}"
