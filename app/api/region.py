"""Public region/price-routing endpoint.

  GET /api/region   →   {country, currency, price, price_display, processor}

Frontend hits this on page load to decide which checkout button (Stripe
USD vs Razorpay INR UPI Autopay) to render in the paywall modal.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services.region import resolve_region

router = APIRouter(tags=["region"])


@router.get("/api/region")
async def region(request: Request) -> dict[str, Any]:
    return await resolve_region(request)
