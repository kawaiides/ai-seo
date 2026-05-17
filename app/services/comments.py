"""Audit comments service (Phase D).

Stores inline notes on a `SitePageAudit`, optionally scoped to a single
check by `check_id`. Editors create + resolve; viewers read; owners can
delete. Authorization is enforced by the route handler via
`require_role`; this service only checks that the comment targets a
real audit and that the targeted check_id is a known one.
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditComment, Site, SitePage, SitePageAudit, User
from app.services.aeo_checks import default_checks

MAX_BODY_LEN = 4000


async def add_comment(
    session: AsyncSession,
    *,
    audit_id: int,
    author: User | None,
    body: str,
    check_id: str | None = None,
) -> AuditComment:
    body = (body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail={"error": "empty_body"})
    if len(body) > MAX_BODY_LEN:
        raise HTTPException(
            status_code=422,
            detail={"error": "body_too_long", "max": MAX_BODY_LEN},
        )
    if check_id is not None and check_id not in _known_check_ids():
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_check_id", "check_id": check_id},
        )
    audit = await session.get(SitePageAudit, audit_id)
    if audit is None:
        raise HTTPException(status_code=404, detail={"error": "audit_not_found"})

    comment = AuditComment(
        site_page_audit_id=audit_id,
        author_user_id=author.id if author is not None else None,
        check_id=check_id,
        body=body,
    )
    session.add(comment)
    await session.flush()
    return comment


async def list_comments(
    session: AsyncSession,
    *,
    audit_id: int,
    include_resolved: bool = True,
) -> list[AuditComment]:
    stmt = (
        select(AuditComment)
        .where(AuditComment.site_page_audit_id == audit_id)
        .order_by(AuditComment.created_at.asc())
    )
    if not include_resolved:
        stmt = stmt.where(AuditComment.resolved.is_(False))
    return (await session.scalars(stmt)).all()


async def resolve_comment(
    session: AsyncSession, *, comment_id: int, resolved: bool = True
) -> AuditComment:
    comment = await session.get(AuditComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail={"error": "comment_not_found"})
    comment.resolved = resolved
    await session.flush()
    return comment


async def delete_comment(session: AsyncSession, *, comment_id: int) -> None:
    comment = await session.get(AuditComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail={"error": "comment_not_found"})
    await session.delete(comment)
    await session.flush()


async def org_id_for_audit(
    session: AsyncSession, audit_id: int
) -> UUID | None:
    """Return the org that owns the site backing `audit_id`, if any.

    Phase D + Critical Decision #4: prefer `Site.org_id`; fall back to
    `None` rather than the legacy `Site.user_id` so the gating helper
    can short-circuit a 403 instead of accidentally treating an
    individual user id as an org id."""
    stmt = (
        select(Site.org_id)
        .join(SitePage, SitePage.site_id == Site.id)
        .join(SitePageAudit, SitePageAudit.site_page_id == SitePage.id)
        .where(SitePageAudit.id == audit_id)
    )
    return await session.scalar(stmt)


async def user_id_for_audit(
    session: AsyncSession, audit_id: int
) -> UUID | None:
    """Legacy user-id projection. Kept for routes that still gate on a
    user owner before the Phase D migration finishes routing them onto
    the org gate."""
    stmt = (
        select(Site.user_id)
        .join(SitePage, SitePage.site_id == Site.id)
        .join(SitePageAudit, SitePageAudit.site_page_id == SitePage.id)
        .where(SitePageAudit.id == audit_id)
    )
    return await session.scalar(stmt)


def _known_check_ids() -> set[str]:
    return {check.check_id for check in default_checks()}


def serialise_comment(c: AuditComment) -> dict:
    return {
        "id": c.id,
        "site_page_audit_id": c.site_page_audit_id,
        "author_user_id": str(c.author_user_id) if c.author_user_id else None,
        "check_id": c.check_id,
        "body": c.body,
        "resolved": c.resolved,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def serialise_comments(comments: Iterable[AuditComment]) -> list[dict]:
    return [serialise_comment(c) for c in comments]
