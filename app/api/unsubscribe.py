"""One-click unsubscribe routes.

`GET /unsubscribe?token=...` renders a confirm page (CAN-SPAM allows
either a confirm step or a one-click GET; we offer both via the same
endpoint for friendlier UX).

`POST /unsubscribe` is the RFC 8058 one-click form-data target; bots
hitting this from the `List-Unsubscribe-Post` header succeed without
any user interaction.

Both surfaces are idempotent: re-clicking an already-unsubscribed link
shows the same confirmation page without errors. Tokens never expire so
old archived emails still work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.services.unsubscribe import mark_unsubscribed, resolve_token

log = logging.getLogger(__name__)

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _render(
    request: Request,
    *,
    status: str,
    token: str | None = None,
    contact_id: int | None = None,
) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
        "emails/unsubscribe.html",
        {
            "current_user": None,
            "status": status,
            "token": token,
            "contact_id": contact_id,
        },
    )


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(
    request: Request,
    token: Optional[str] = None,
) -> HTMLResponse:
    if not token:
        return _render(request, status="missing_token")
    resolved = resolve_token(token)
    if resolved is None:
        return _render(request, status="invalid_token")
    contact_id, _ = resolved
    return _render(request, status="confirm", token=token, contact_id=contact_id)


@router.post("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_submit(
    request: Request,
    token: str = Form(...),
    # RFC 8058: `List-Unsubscribe=One-Click` is the canonical body when
    # the user agent fires this from the `List-Unsubscribe-Post` header.
    # We accept any POST that carries a valid token, so missing this
    # field isn't an error.
    list_unsubscribe: Optional[str] = Form(default=None, alias="List-Unsubscribe"),
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    resolved = resolve_token(token)
    if resolved is None:
        return _render(request, status="invalid_token")
    contact_id, audit_id = resolved
    newly_unsubscribed = await mark_unsubscribed(db, contact_id=contact_id)
    log.info(
        "unsubscribe: contact=%s audit=%s new=%s",
        contact_id, audit_id, newly_unsubscribed,
    )
    return _render(
        request,
        status="done" if newly_unsubscribed else "already",
        contact_id=contact_id,
    )
