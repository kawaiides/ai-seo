"""Legal-page routes.

Three static templates (`terms`, `privacy`, `refund`) that any
credit-card-accepting SaaS legally needs surfaced. They render
plain `_base.html` shells so the look matches the marketing surface
without dragging in the scanner JS.

All three intentionally avoid jurisdiction-specific clauses — the
operator must review with counsel before turning on real billing.
The pages exist primarily so Stripe + Razorpay reviewers can verify
that a "Terms of Service" + "Privacy Policy" + "Refund Policy" link
is reachable from the homepage and checkout flow.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.auth import get_current_user

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("/terms", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(
        request, "legal/terms.html", {"current_user": None}
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(
        request, "legal/privacy.html", {"current_user": None}
    )


@router.get("/refund", response_class=HTMLResponse)
async def refund(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(
        request, "legal/refund.html", {"current_user": None}
    )
