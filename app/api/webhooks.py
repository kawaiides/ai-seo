"""Customer-configured outbound-webhook management endpoints (Phase E)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import OrgRole, Webhook
from app.models.schemas import (
    WebhookCreate,
    WebhookCreatedRecord,
    WebhookRecord,
)
from app.services.orgs import OrgContext, require_role
from app.services.webhooks_out import (
    SUPPORTED_EVENTS,
    create_webhook,
    delete_webhook,
    list_webhooks,
)

router = APIRouter()


@router.get("/supported-events", response_model=list[str])
async def supported_events() -> list[str]:
    return sorted(SUPPORTED_EVENTS)


@router.post(
    "", response_model=WebhookCreatedRecord, status_code=201
)
async def create(
    req: WebhookCreate,
    ctx: OrgContext = Depends(require_role(OrgRole.owner)),
    session: AsyncSession = Depends(get_session),
) -> WebhookCreatedRecord:
    webhook = await create_webhook(
        session, org_id=ctx.org.id, url=req.url, events=req.events
    )
    return WebhookCreatedRecord(
        id=webhook.id,
        org_id=webhook.org_id,
        url=webhook.url,
        events=list(webhook.events),
        enabled=webhook.enabled,
        created_at=webhook.created_at.isoformat() if webhook.created_at else "",
        secret=webhook.secret,
    )


@router.get("", response_model=list[WebhookRecord])
async def list_(
    ctx: OrgContext = Depends(require_role(OrgRole.viewer)),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookRecord]:
    rows = await list_webhooks(session, ctx.org.id)
    return [_serialise(w) for w in rows]


@router.delete("/{webhook_id}", status_code=204)
async def remove(
    webhook_id: UUID,
    ctx: OrgContext = Depends(require_role(OrgRole.owner)),
    session: AsyncSession = Depends(get_session),
) -> None:
    webhook = await session.get(Webhook, webhook_id)
    if webhook is None or webhook.org_id != ctx.org.id:
        raise HTTPException(status_code=404, detail={"error": "webhook_not_found"})
    await delete_webhook(session, webhook_id)


def _serialise(w: Webhook) -> WebhookRecord:
    return WebhookRecord(
        id=w.id,
        org_id=w.org_id,
        url=w.url,
        events=list(w.events),
        enabled=w.enabled,
        created_at=w.created_at.isoformat() if w.created_at else "",
    )
