"""Audit-comment endpoints (Phase D).

Authorization model: a comment lives under a `SitePageAudit`, which lives
under a `SitePage`, which lives under a `Site`. The Site is owned by an
`Org` (Phase D / Critical Decision #4). The caller MUST be a member of
that org. Role required per verb:

  GET    /audits/{audit_id}/comments        → viewer
  POST   /audits/{audit_id}/comments        → editor
  POST   /comments/{comment_id}/resolve     → editor
  DELETE /comments/{comment_id}             → owner

This module previously checked only "is the caller authenticated", which
let any signed-up user iterate sequential `audit_id` integers and read /
write / delete every other tenant's comments (IDOR).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AuditComment, OrgRole, User
from app.models.schemas import (
    CommentCreateRequest,
    CommentRecord,
    CommentResolveRequest,
)
from app.services.auth import get_current_user
from app.services.comments import (
    add_comment,
    delete_comment,
    list_comments,
    org_id_for_audit,
    resolve_comment,
    serialise_comment,
)
from app.services.orgs import has_min_role, require_membership

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


async def _require_audit_role(
    session: AsyncSession,
    *,
    audit_id: int,
    user: User | None,
    min_role: OrgRole,
) -> None:
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
    org_id = await org_id_for_audit(session, audit_id)
    if org_id is None:
        # Audit does not exist, or its owning Site has no `org_id` (legacy
        # user-owned row). In both cases the caller cannot prove
        # membership — refuse without leaking whether the audit exists.
        raise HTTPException(status_code=404, detail={"error": "audit_not_found"})
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


async def _require_comment_role(
    session: AsyncSession,
    *,
    comment_id: int,
    user: User | None,
    min_role: OrgRole,
) -> AuditComment:
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
    comment = await session.get(AuditComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail={"error": "comment_not_found"})
    await _require_audit_role(
        session,
        audit_id=comment.site_page_audit_id,
        user=user,
        min_role=min_role,
    )
    return comment


@router.post("/audits/{audit_id}/comments", response_model=CommentRecord, status_code=201)
async def post_comment(
    audit_id: int,
    req: CommentCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CommentRecord:
    await _require_audit_role(
        session, audit_id=audit_id, user=user, min_role=OrgRole.editor
    )
    comment = await add_comment(
        session,
        audit_id=audit_id,
        author=user,
        body=req.body,
        check_id=req.check_id,
    )
    return CommentRecord(**serialise_comment(comment))


@router.get("/audits/{audit_id}/comments", response_model=list[CommentRecord])
async def get_comments(
    audit_id: int,
    include_resolved: bool = True,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[CommentRecord]:
    await _require_audit_role(
        session, audit_id=audit_id, user=user, min_role=OrgRole.viewer
    )
    comments = await list_comments(
        session, audit_id=audit_id, include_resolved=include_resolved
    )
    return [CommentRecord(**serialise_comment(c)) for c in comments]


@router.post(
    "/comments/{comment_id}/resolve",
    response_model=CommentRecord,
)
async def resolve(
    comment_id: int,
    req: CommentResolveRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CommentRecord:
    await _require_comment_role(
        session, comment_id=comment_id, user=user, min_role=OrgRole.editor
    )
    comment = await resolve_comment(
        session, comment_id=comment_id, resolved=req.resolved
    )
    return CommentRecord(**serialise_comment(comment))


@router.delete("/comments/{comment_id}", status_code=204)
async def delete(
    comment_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    await _require_comment_role(
        session, comment_id=comment_id, user=user, min_role=OrgRole.owner
    )
    await delete_comment(session, comment_id=comment_id)


@router.get("/audits/{audit_id}/comments/view", response_class=HTMLResponse)
async def comments_thread_view(
    audit_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Server-rendered comments thread.

    Embeds inside the site dashboard or the public report page. Renders
    the existing comments and a "new comment" form; submitting POSTs to
    the JSON endpoint via fetch and re-renders inline. The form is hidden
    when the caller lacks editor role.
    """
    await _require_audit_role(
        session, audit_id=audit_id, user=user, min_role=OrgRole.viewer
    )
    org_id = await org_id_for_audit(session, audit_id)
    member = (
        await require_membership(session, org_id=org_id, user=user)
        if org_id is not None
        else None
    )
    can_edit = (
        member is not None and has_min_role(member, OrgRole.editor)
    )
    can_resolve = can_edit
    can_delete = (
        member is not None and has_min_role(member, OrgRole.owner)
    )
    comments = await list_comments(session, audit_id=audit_id, include_resolved=True)
    return _templates.TemplateResponse(
        request,
        "comments/thread.html",
        {
            "audit_id": audit_id,
            "comments": [serialise_comment(c) for c in comments],
            "can_edit": can_edit,
            "can_resolve": can_resolve,
            "can_delete": can_delete,
            "current_user": user,
        },
    )
