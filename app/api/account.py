"""Self-service account endpoints.

Today this hosts a single route — `POST /api/account/delete` — that lets
a signed-in user erase their own account. Postgres FKs declared in
`app/db/models.py` cascade the delete to:

  * `Site` (via `user_id` FK with ondelete="CASCADE")
  * `Subscription`, `ApiKey`, `BYOKValidation`
  * `OrgMember` rows (via SET NULL on user_id, so org survives)

This is the GDPR "Right to Erasure" path. We require the caller to
re-type their own email as a confirm-token; that protects against XSS
or session-fixation triggering an irreversible delete with a single
click.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import User
from app.services.auth import get_current_user

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/account/delete")
async def delete_account(
    request: Request,
    confirm_email: str = Form(...),
    current_user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    # Anonymous passwordless accounts (where User.email is NULL) shouldn't
    # be able to self-delete via this UI — they have no identifier to
    # type back. Force them to clear cookies + start fresh instead.
    stored = (current_user.email or "").strip().lower()
    typed = (confirm_email or "").strip().lower()
    if not stored or stored != typed:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "confirm_email_mismatch",
                "message": "Re-type the email associated with this account "
                "exactly. Anonymous sessions cannot self-delete.",
            },
        )

    user_id = current_user.id
    log.info("account_delete: user=%s confirmed=%s", user_id, stored)
    await db.delete(current_user)
    await db.flush()

    # Clear the session cookie so the next request sees the user logged
    # out. Cookie name lives in app/services/auth.py:26.
    from app.services.auth import COOKIE_NAME

    response = RedirectResponse(url="/?account_deleted=1", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
