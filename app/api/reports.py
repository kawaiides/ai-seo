"""Public report URLs.

Endpoints:
  GET /r/{token}        → renders the audit report HTML (lead magnet).
  GET /r/{token}/p.gif  → 1×1 transparent gif, increments Outreach.opens.

Both routes are unauthenticated — only the signed token gates access.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.report_builder import build_context, render_html
from app.db.base import get_session
from app.db.models import Audit, FunnelStage, Outreach, Prospect
from app.services.funnel import record as record_funnel
from app.services.tokens import TokenError, decode_report

log = logging.getLogger(__name__)

router = APIRouter()

# Minimal 1x1 transparent GIF (43 bytes).
PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c000000000100010000020144003b"
)


async def _load_audit(token: str, session: AsyncSession) -> tuple[Audit, Prospect]:
    try:
        payload = decode_report(token)
    except TokenError as e:
        raise HTTPException(status_code=404, detail=str(e))

    audit_id = payload.get("aid")
    jti = payload.get("jti")
    if not isinstance(audit_id, int) or not isinstance(jti, str):
        raise HTTPException(status_code=404, detail="malformed report token")

    audit = (
        await session.execute(select(Audit).where(Audit.id == audit_id))
    ).scalar_one_or_none()
    if audit is None or str(audit.token_jti) != jti:
        raise HTTPException(status_code=404, detail="audit not found")

    prospect = (
        await session.execute(select(Prospect).where(Prospect.id == audit.prospect_id))
    ).scalar_one_or_none()
    if prospect is None:
        raise HTTPException(status_code=404, detail="prospect not found")
    return audit, prospect


@router.get("/r/{token}", response_class=HTMLResponse)
async def view_report(token: str, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    audit, prospect = await _load_audit(token, session)
    # HTML-view counts as a "click" — pixel handles "open".
    await session.execute(
        update(Outreach)
        .where(Outreach.audit_id == audit.id)
        .values(clicks=Outreach.clicks + 1)
    )
    await record_funnel(
        session,
        FunnelStage.engaged,
        audit_id=audit.id,
        prospect_id=prospect.id,
        meta={"via": "click"},
    )
    ctx = build_context(audit, prospect)
    return HTMLResponse(content=render_html(ctx))


@router.get("/r/{token}/p.gif")
async def open_pixel(token: str, session: AsyncSession = Depends(get_session)) -> Response:
    audit, prospect = await _load_audit(token, session)
    await session.execute(
        update(Outreach)
        .where(Outreach.audit_id == audit.id)
        .values(opens=Outreach.opens + 1)
    )
    await record_funnel(
        session,
        FunnelStage.engaged,
        audit_id=audit.id,
        prospect_id=prospect.id,
        meta={"via": "pixel"},
    )
    return Response(
        content=PIXEL_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
