"""Billing endpoints — pricing page, checkout, success.

Designed to integrate with Stripe (USD) and Razorpay (INR) when the
respective secret keys are present in the environment. With no keys set,
the checkout endpoint runs in DEMO MODE: it inserts a mock `Subscription`
row directly and redirects to `/checkout/success` so the full UI flow can
be exercised end-to-end without external services.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import (
    Processor,
    Subscription,
    SubscriptionStatus,
    User,
    UserPlan,
)
from app.services.auth import get_current_user

router = APIRouter(tags=["billing"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

log = logging.getLogger(__name__)


PLAN_CATALOG = {
    "pro_usd": {
        "id": "pro_usd",
        "label": "Pro",
        "tagline": "For teams shipping content at speed",
        "currency": "USD",
        "symbol": "$",
        "price_monthly": 29,
        "price_annual": 290,
        "processor": Processor.stripe,
        "user_plan": UserPlan.pro_usd,
    },
    "pro_inr": {
        "id": "pro_inr",
        "label": "Pro (India)",
        "tagline": "Same Pro plan, billed in INR",
        "currency": "INR",
        "symbol": "₹",
        "price_monthly": 1999,
        "price_annual": 19990,
        "processor": Processor.razorpay,
        "user_plan": UserPlan.pro_inr,
    },
}


_DEMO_OK_ENVS = {"dev", "test", "local"}


def _is_demo_env() -> bool:
    """Demo mode is only allowed in explicitly-marked dev/test environments.

    Previously demo mode kicked in any time the Stripe + Razorpay secrets
    were both unset, which silently let any signed-up user grant themselves
    a free Pro subscription in production deploys that hadn't wired billing
    yet. Now an operator must opt in by setting `AEGIS_ENV=dev|test|local`.
    """
    return os.environ.get("AEGIS_ENV", "prod").lower() in _DEMO_OK_ENVS


def _demo_mode() -> bool:
    """Run the mocked end-to-end flow when no processor is wired AND we're
    in a dev/test environment. Production hits a 503 instead so a
    misconfigured deploy fails closed."""
    if not _is_demo_env():
        return False
    return not (
        os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("RAZORPAY_KEY_SECRET")
    )


def _billing_configured() -> bool:
    return bool(
        os.environ.get("STRIPE_SECRET_KEY")
        or os.environ.get("RAZORPAY_KEY_SECRET")
    )


@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
        "billing/pricing.html",
        {
            "current_user": current_user,
            "plans": list(PLAN_CATALOG.values()),
            "demo_mode": _demo_mode(),
        },
    )


@router.post("/api/billing/checkout")
async def create_checkout(
    request: Request,
    plan: str = Form(...),
    cycle: str = Form(default="monthly"),
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if current_user is None:
        return RedirectResponse(url=f"/signup?next=/pricing", status_code=303)

    if plan not in PLAN_CATALOG:
        raise HTTPException(status_code=400, detail="Unknown plan")
    if cycle not in ("monthly", "annual"):
        raise HTTPException(status_code=400, detail="Unknown cycle")

    plan_def = PLAN_CATALOG[plan]

    # Fail closed: if no payment processor is wired AND we're running in a
    # production environment, refuse the checkout outright. The previous
    # behaviour silently inserted an `active` Subscription, which let any
    # signed-up user grant themselves Pro for free.
    if not _billing_configured() and not _is_demo_env():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "billing_not_configured",
                "message": "Billing is not available. Please contact support.",
            },
        )

    # In real prod we'd build a Stripe Checkout Session or Razorpay Order here.
    # Demo mode short-circuits to a mock Subscription so the UI flow works
    # without external secrets, but only when AEGIS_ENV ∈ {dev,test,local}.
    if _demo_mode():
        external_id = f"mock_{plan}_{secrets.token_hex(8)}"
        period_days = 365 if cycle == "annual" else 30
        sub = Subscription(
            id=uuid.uuid4(),
            user_id=current_user.id,
            processor=plan_def["processor"],
            external_id=external_id,
            status=SubscriptionStatus.active,
            current_period_end=datetime.now(tz=timezone.utc)
            + timedelta(days=period_days),
        )
        db.add(sub)
        # Upgrade the user's plan tier.
        current_user.plan = plan_def["user_plan"]
        await db.flush()
        return RedirectResponse(
            url=f"/checkout/success?ref={external_id}",
            status_code=303,
        )

    # Real-processor branch.
    if plan_def["processor"] == Processor.stripe:
        if not os.environ.get("STRIPE_SECRET_KEY"):
            raise HTTPException(
                status_code=501,
                detail="Stripe not configured (set STRIPE_SECRET_KEY).",
            )
        from app.payments.stripe_client import (
            StripeNotConfigured,
            create_checkout_session,
        )

        base = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")
        try:
            result = create_checkout_session(
                user_id=str(current_user.id),
                plan=plan,
                cycle=cycle,
                success_url=f"{base}/checkout/success?session={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{base}/pricing?cancelled=1",
                customer_email=current_user.email,
            )
        except StripeNotConfigured as e:
            raise HTTPException(status_code=501, detail=str(e))
        return RedirectResponse(url=result.url, status_code=303)

    # Razorpay branch — UPI Autopay subscription.
    if plan_def["processor"] == Processor.razorpay:
        if not os.environ.get("RAZORPAY_KEY_SECRET"):
            raise HTTPException(
                status_code=501,
                detail="Razorpay not configured (set RAZORPAY_KEY_SECRET).",
            )
        from app.payments.razorpay_client import (
            RazorpayNotConfigured,
            create_subscription,
        )

        try:
            result = create_subscription(
                user_id=str(current_user.id),
                customer_email=current_user.email,
            )
        except RazorpayNotConfigured as e:
            raise HTTPException(status_code=501, detail=str(e))
        # Razorpay returns a hosted short_url the customer completes UPI Autopay on.
        # If it's missing, fall back to the success page so the row exists.
        return RedirectResponse(
            url=result.short_url
            or f"/checkout/success?ref={result.subscription_id}",
            status_code=303,
        )

    raise HTTPException(status_code=501, detail="Unknown processor")


@router.get("/checkout/success", response_class=HTMLResponse)
async def checkout_success(
    request: Request,
    ref: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    sub = None
    if ref:
        sub = (
            await db.execute(
                select(Subscription).where(Subscription.external_id == ref)
            )
        ).scalar_one_or_none()

    return _templates.TemplateResponse(
        request,
        "billing/success.html",
        {
            "current_user": current_user,
            "subscription": sub,
            "demo_mode": _demo_mode(),
        },
    )


@router.post("/api/billing/cancel")
async def cancel_subscription(
    request: Request,
    subscription_id: str = Form(...),
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if current_user is None:
        return RedirectResponse(url="/login", status_code=303)

    try:
        sub_uuid = uuid.UUID(subscription_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid subscription id")

    sub = (
        await db.execute(
            select(Subscription).where(
                Subscription.id == sub_uuid,
                Subscription.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    sub.status = SubscriptionStatus.canceled
    current_user.plan = UserPlan.free
    await db.flush()
    from app.services.audit_log import (
        ACTION_SUBSCRIPTION_CANCEL,
        record_audit_event,
    )
    await record_audit_event(
        db,
        action=ACTION_SUBSCRIPTION_CANCEL,
        actor_user_id=current_user.id,
        subject_user_id=current_user.id,
        meta={
            "subscription_id": str(sub.id),
            "external_id": sub.external_id,
            "processor": sub.processor.value,
        },
        request=request,
    )
    return RedirectResponse(url="/account", status_code=303)
