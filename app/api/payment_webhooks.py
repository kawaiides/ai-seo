"""Inbound payment-processor webhooks (Stripe; Razorpay lands in M7).

Critical contract:
  * Raw body is read BEFORE any pydantic / json parsing. FastAPI's body
    parser consumes the stream once; if we parse first, HMAC verification
    fails.
  * `webhook_event.id` is the provider's event id; UNIQUE constraint
    makes replays idempotent — second insert collides → 200 no-op.
  * Funnel events (converted/at_risk/churned) are emitted by status
    transition, not by event type, so partial replays self-correct.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import (
    Audit,
    Contact,
    FunnelStage,
    Outreach,
    Processor,
    Subscription,
    SubscriptionStatus,
    User,
    UserPlan,
    WebhookEvent,
)
from app.payments import razorpay_client as razorpay_mod
from app.payments import stripe_client as stripe_mod
from app.payments.stripe_client import (
    CANCELING_EVENTS,
    FAILING_EVENTS,
    StripeSignatureError,
    extract_subscription_fields,
    verify_webhook,
)
from app.services.funnel import record as record_funnel

log = logging.getLogger(__name__)

router = APIRouter(tags=["payment-webhooks"])


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------

async def _record_event(
    db: AsyncSession,
    *,
    event_id: str,
    processor: Processor,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    """Try to insert the webhook log row. Returns True if first-time,
    False if already processed."""
    stmt = (
        pg_insert(WebhookEvent)
        .values(
            id=event_id,
            processor=processor,
            event_type=event_type,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(WebhookEvent.id)
    )
    try:
        row = (await db.execute(stmt)).first()
    except IntegrityError:
        return False
    return row is not None


async def _mark_processed(db: AsyncSession, event_id: str, error: str | None = None) -> None:
    evt = (
        await db.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
    ).scalar_one_or_none()
    if evt is None:
        return
    evt.processed_at = datetime.now(tz=timezone.utc)
    if error is not None:
        evt.error = error


# ----------------------------------------------------------------------
# Subscription upsert
# ----------------------------------------------------------------------

async def _resolve_user(
    db: AsyncSession, fields: dict[str, Any]
) -> User | None:
    user_id = fields.get("user_id")
    if user_id:
        try:
            uid = uuid.UUID(user_id)
        except (TypeError, ValueError):
            uid = None
        if uid:
            row = (
                await db.execute(select(User).where(User.id == uid))
            ).scalar_one_or_none()
            if row is not None:
                return row
    email = fields.get("customer_email")
    if email:
        row = (
            await db.execute(select(User).where(User.email == email.lower()))
        ).scalar_one_or_none()
        if row is not None:
            return row
    return None


async def _upsert_subscription(
    db: AsyncSession,
    user: User,
    fields: dict[str, Any],
    processor: Processor,
) -> Subscription:
    external_id = fields["external_id"]
    status_str = fields.get("status") or "active"
    status = SubscriptionStatus(status_str)
    period_end_raw = fields.get("current_period_end")
    period_end = (
        datetime.fromtimestamp(period_end_raw, tz=timezone.utc)
        if isinstance(period_end_raw, (int, float))
        else None
    )

    existing = (
        await db.execute(
            select(Subscription).where(Subscription.external_id == external_id)
        )
    ).scalar_one_or_none()
    if existing is None:
        sub = Subscription(
            id=uuid.uuid4(),
            user_id=user.id,
            processor=processor,
            external_id=external_id,
            status=status,
            current_period_end=period_end,
        )
        db.add(sub)
        await db.flush()
        return sub

    existing.status = status
    if period_end is not None:
        existing.current_period_end = period_end
    return existing


async def _enqueue_dunning(db: AsyncSession, user: User) -> None:
    """Best-effort dunning row. Targets the user's most recent (contact, audit)
    pair if one exists; silently no-ops otherwise."""
    if not user.email:
        return
    row = (
        await db.execute(
            select(Contact, Audit)
            .join(Audit, Audit.prospect_id == Contact.prospect_id)
            .where(Contact.email == user.email.lower())
            .order_by(Audit.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return
    contact, audit = row
    stmt = (
        pg_insert(Outreach)
        .values(
            contact_id=contact.id,
            audit_id=audit.id,
            template_variant="dunning",
            subject="Payment issue — keep your AEGIS audits running",
        )
        .on_conflict_do_nothing(
            index_elements=["contact_id", "audit_id", "template_variant"]
        )
    )
    await db.execute(stmt)


# ----------------------------------------------------------------------
# Stripe endpoint
# ----------------------------------------------------------------------

@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    body = await request.body()
    try:
        event = verify_webhook(body, stripe_signature)
    except StripeSignatureError as e:
        log.warning("stripe: signature rejected: %s", e)
        raise HTTPException(status_code=400, detail="bad signature")

    event_id = event.get("id")
    event_type = event.get("type", "unknown")
    if not event_id:
        raise HTTPException(status_code=400, detail="missing event id")

    first_time = await _record_event(
        db,
        event_id=event_id,
        processor=Processor.stripe,
        event_type=event_type,
        payload=event,
    )
    if not first_time:
        log.info("stripe: replayed event %s (%s); skipping", event_id, event_type)
        return JSONResponse({"received": True, "duplicate": True})

    try:
        await _handle_stripe_event(db, event, event_type)
    except Exception as e:  # noqa: BLE001
        log.exception("stripe: handler error for %s (%s)", event_id, event_type)
        await _mark_processed(db, event_id, error=f"{type(e).__name__}: {e}")
        # Re-raise so Stripe retries on transient failures.
        raise HTTPException(status_code=500, detail="handler error")
    else:
        await _mark_processed(db, event_id)

    return JSONResponse({"received": True})


async def _handle_stripe_event(
    db: AsyncSession, event: dict[str, Any], event_type: str
) -> None:
    fields = extract_subscription_fields(event)
    if not fields.get("external_id"):
        log.info("stripe: event %s has no subscription id; skipping", event_type)
        return

    user = await _resolve_user(db, fields)
    if user is None:
        log.warning("stripe: cannot resolve user for event %s", event.get("id"))
        return

    sub = await _upsert_subscription(db, user, fields, Processor.stripe)

    if sub.status == SubscriptionStatus.active:
        user.plan = UserPlan.pro_usd
    elif sub.status == SubscriptionStatus.canceled:
        user.plan = UserPlan.free

    # Funnel.
    if sub.status == SubscriptionStatus.active:
        stage = (
            FunnelStage.retained
            if event_type == "invoice.payment_succeeded"
            else FunnelStage.converted
        )
        await record_funnel(
            db, stage, user_id=user.id,
            meta={"processor": "stripe", "external_id": fields["external_id"]},
        )
    elif event_type in FAILING_EVENTS:
        await record_funnel(
            db, FunnelStage.at_risk, user_id=user.id,
            meta={"processor": "stripe", "reason": "payment_failed"},
        )
        await _enqueue_dunning(db, user)
    elif event_type in CANCELING_EVENTS:
        await record_funnel(
            db, FunnelStage.churned, user_id=user.id,
            meta={"processor": "stripe"},
        )


# ----------------------------------------------------------------------
# Razorpay endpoint
# ----------------------------------------------------------------------


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    body = await request.body()
    try:
        event = razorpay_mod.verify_webhook(body, x_razorpay_signature)
    except razorpay_mod.RazorpaySignatureError as e:
        log.warning("razorpay: signature rejected: %s", e)
        raise HTTPException(status_code=400, detail="bad signature")

    event_type = event.get("event", "")
    # Razorpay docs: payload may carry an `id` at root for some events. Fall
    # back to a stable composite for events that lack one.
    event_id = (
        event.get("id")
        or event.get("payload", {}).get("subscription", {}).get("entity", {}).get("id")
        or event.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
        or ""
    )
    event_id = f"rzp_{event_type}_{event_id}" if event_id else None
    if not event_id:
        raise HTTPException(status_code=400, detail="missing event id")

    first_time = await _record_event(
        db,
        event_id=event_id,
        processor=Processor.razorpay,
        event_type=event_type,
        payload=event,
    )
    if not first_time:
        log.info("razorpay: replayed event %s (%s); skipping", event_id, event_type)
        return JSONResponse({"received": True, "duplicate": True})

    try:
        await _handle_razorpay_event(db, event, event_type)
    except Exception as e:  # noqa: BLE001
        log.exception("razorpay: handler error for %s (%s)", event_id, event_type)
        await _mark_processed(db, event_id, error=f"{type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="handler error")
    else:
        await _mark_processed(db, event_id)

    return JSONResponse({"received": True})


async def _handle_razorpay_event(
    db: AsyncSession, event: dict[str, Any], event_type: str
) -> None:
    fields = razorpay_mod.extract_subscription_fields(event)
    if not fields.get("external_id"):
        log.info("razorpay: event %s has no subscription id; skipping", event_type)
        return

    user = await _resolve_user(db, fields)
    if user is None:
        log.warning("razorpay: cannot resolve user for event %s", event.get("id"))
        return

    sub = await _upsert_subscription(db, user, fields, Processor.razorpay)

    if sub.status == SubscriptionStatus.active:
        user.plan = UserPlan.pro_inr
    elif sub.status == SubscriptionStatus.canceled:
        user.plan = UserPlan.free

    if sub.status == SubscriptionStatus.active:
        # `subscription.charged` = renewal; `subscription.activated` = first activation.
        stage = (
            FunnelStage.retained
            if event_type == "subscription.charged"
            else FunnelStage.converted
        )
        await record_funnel(
            db, stage, user_id=user.id,
            meta={"processor": "razorpay", "external_id": fields["external_id"]},
        )
    elif event_type in razorpay_mod.FAILING_EVENTS:
        await record_funnel(
            db, FunnelStage.at_risk, user_id=user.id,
            meta={"processor": "razorpay", "reason": event_type},
        )
        await _enqueue_dunning(db, user)
    elif event_type in razorpay_mod.CANCELING_EVENTS:
        await record_funnel(
            db, FunnelStage.churned, user_id=user.id,
            meta={"processor": "razorpay"},
        )
