"""Admin endpoints. Gated by a single shared secret.

`ADMIN_TOKEN` env var. Passed via `X-Admin-Token` header on JSON routes,
or via `?token=...` query param on page routes (so a human can hit
`/admin?token=xxx` in the browser).

JSON routes:
  GET /admin/funnel              JSON {counts, rates, window}

Page routes:
  GET /admin                     hub w/ links + topline KPIs
  GET /admin/funnel/view         Chart.js bar chart
  GET /admin/users               recent users table
  GET /admin/subscriptions       recent subscriptions table
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Subscription, SubscriptionStatus, User, UserPlan
from app.services.blog_generator import (
    INTERVAL_HOURS,
    generate_one,
    is_due,
    read_state,
    save_generated,
)
from app.services.funnel import cohort_counts, compute_rates

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin_header(x_admin_token: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="admin not configured")
    if not x_admin_token or x_admin_token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


def _require_admin_token(request: Request) -> str:
    """Page-route guard. Accepts ?token=... OR the X-Admin-Token header.
    Returns the validated token so templates can rebuild self-referencing
    links without leaking it through Jinja context unnecessarily."""
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="admin not configured")
    supplied = (
        request.query_params.get("token")
        or request.headers.get("x-admin-token")
        or request.cookies.get("admin_token")
    )
    if not supplied or supplied != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
    return supplied


def _window(days: int) -> tuple[datetime, datetime]:
    now = datetime.now(tz=timezone.utc)
    return now - timedelta(days=days), now


@router.get("/funnel")
async def funnel_json(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_require_admin_header),
) -> dict[str, Any]:
    since, until = _window(days)
    counts = await cohort_counts(session, since=since, until=until)
    return {
        "window": {"days": days, "since": since.isoformat(), "until": until.isoformat()},
        "counts": counts,
        "rates": compute_rates(counts),
    }


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_hub(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    token = _require_admin_token(request)
    now = datetime.now(tz=timezone.utc)
    since_30 = now - timedelta(days=30)

    user_total = (await db.execute(select(func.count(User.id)))).scalar_one()
    user_30d = (
        await db.execute(
            select(func.count(User.id)).where(User.created_at >= since_30)
        )
    ).scalar_one()
    sub_active = (
        await db.execute(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.active
            )
        )
    ).scalar_one()
    sub_30d = (
        await db.execute(
            select(func.count(Subscription.id)).where(Subscription.created_at >= since_30)
        )
    ).scalar_one()
    paying = (
        await db.execute(
            select(func.count(User.id)).where(User.plan != UserPlan.free)
        )
    ).scalar_one()

    return _templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "token": token,
            "current_user": None,
            "kpis": {
                "user_total": user_total or 0,
                "user_30d": user_30d or 0,
                "sub_active": sub_active or 0,
                "sub_30d": sub_30d or 0,
                "paying": paying or 0,
            },
        },
    )


@router.get("/funnel/view", response_class=HTMLResponse)
async def funnel_view(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    token = _require_admin_token(request)
    since, until = _window(days)
    counts = await cohort_counts(session, since=since, until=until)
    return _templates.TemplateResponse(
        request,
        "admin/funnel.html",
        {
            "counts": counts,
            "rates": compute_rates(counts),
            "days": days,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "token": token,
        },
    )


@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    token = _require_admin_token(request)
    rows = (
        await db.execute(
            select(User).order_by(User.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return _templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "users": rows,
            "limit": limit,
            "token": token,
            "current_user": None,
        },
    )


@router.get("/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    token = _require_admin_token(request)
    rows = (
        await db.execute(
            select(Subscription, User)
            .join(User, Subscription.user_id == User.id)
            .order_by(Subscription.created_at.desc())
            .limit(limit)
        )
    ).all()
    by_status = {s.value: 0 for s in SubscriptionStatus}
    for sub, _ in rows:
        by_status[sub.status.value] += 1
    return _templates.TemplateResponse(
        request,
        "admin/subscriptions.html",
        {
            "rows": rows,
            "by_status": by_status,
            "limit": limit,
            "token": token,
            "current_user": None,
        },
    )


# ---------- Blog auto-generator controls ----------


@router.get("/blog", response_class=HTMLResponse)
async def admin_blog(request: Request) -> HTMLResponse:
    token = _require_admin_token(request)
    state = read_state()
    return _templates.TemplateResponse(
        request,
        "admin/blog.html",
        {
            "current_user": None,
            "token": token,
            "state": state,
            "interval_hours": INTERVAL_HOURS,
            "due_now": is_due(INTERVAL_HOURS),
        },
    )


@router.post("/blog/generate")
async def admin_blog_generate(request: Request) -> Any:
    """Trigger an immediate generation, ignoring the interval. Returns the
    saved article slug + score."""
    _require_admin_token(request)
    article = await generate_one()
    save_generated(article)
    return {
        "slug": article["slug"],
        "title": article["title"],
        "score": article["_meta"]["final_score"],
        "revisions": article["_meta"]["revisions"],
    }
