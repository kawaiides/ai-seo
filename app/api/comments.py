"""Audit-comment endpoints (Phase D)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import AuditComment, User
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
    resolve_comment,
    serialise_comment,
)

router = APIRouter()


@router.post("/audits/{audit_id}/comments", response_model=CommentRecord, status_code=201)
async def post_comment(
    audit_id: int,
    req: CommentCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CommentRecord:
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
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
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
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
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
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
    if user is None:
        raise HTTPException(status_code=401, detail={"error": "auth_required"})
    await delete_comment(session, comment_id=comment_id)
