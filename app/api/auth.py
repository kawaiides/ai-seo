"""Auth endpoints — signup, login, logout, account pages."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Org, OrgMember, OrgRole, Subscription, SubscriptionStatus, User
from app.services.auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    get_current_user,
    get_or_create_user_by_email,
    rotate_session_id,
    sign_session,
)

router = APIRouter(tags=["auth"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not email or not _EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="Invalid email address")
    return email


def _cookie_secure() -> bool:
    """Set the Secure flag whenever the public origin uses HTTPS.

    Explicit override via `AEGIS_COOKIE_SECURE=0|1` wins. Otherwise we
    derive it from `APP_BASE_URL` — the production Terraform sets this to
    `https://...`, so prod gets Secure cookies by default. Dev (`http://
    localhost:...`) keeps the flag off so login still works in the browser.
    """
    override = os.environ.get("AEGIS_COOKIE_SECURE")
    if override is not None:
        return override.lower() in {"1", "true", "yes", "on"}
    base = os.environ.get("APP_BASE_URL", "")
    return base.lower().startswith("https://")


def _redirect_with_session(target: str, session_token: str) -> RedirectResponse:
    resp = RedirectResponse(url=target, status_code=303)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    return resp


# ---------- Page routes ----------


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
) -> HTMLResponse:
    if current_user is not None:
        return RedirectResponse(url="/account", status_code=303)
    next_url = request.query_params.get("next", "/account")
    return _templates.TemplateResponse(
        request,
        "auth/signup.html",
        {"current_user": None, "next_url": next_url, "error": None},
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
) -> HTMLResponse:
    if current_user is not None:
        return RedirectResponse(url="/account", status_code=303)
    next_url = request.query_params.get("next", "/account")
    return _templates.TemplateResponse(
        request,
        "auth/login.html",
        {"current_user": None, "next_url": next_url, "error": None},
    )


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if current_user is None:
        return RedirectResponse(url="/login?next=/account", status_code=303)
    subs = (
        await db.execute(
            select(Subscription)
            .where(Subscription.user_id == current_user.id)
            .order_by(Subscription.created_at.desc())
        )
    ).scalars().all()
    active_sub = next(
        (s for s in subs if s.status == SubscriptionStatus.active),
        None,
    )

    # Preload the user's orgs + their role in each so the Team tab can
    # render without a round-trip on tab-switch. The Customer Console
    # picks the owner-role org as "primary" (the one whose API keys +
    # members the user can manage); if none, falls back to the most
    # recent membership.
    org_rows = (
        await db.execute(
            select(Org, OrgMember)
            .join(OrgMember, OrgMember.org_id == Org.id)
            .where(OrgMember.user_id == current_user.id)
            .order_by(Org.created_at.desc())
        )
    ).all()
    user_orgs = [(o, m) for (o, m) in org_rows]
    primary_org = None
    primary_role = None
    for org, member in user_orgs:
        if member.role == OrgRole.owner:
            primary_org = org
            primary_role = member.role
            break
    if primary_org is None and user_orgs:
        primary_org, member = user_orgs[0]
        primary_role = member.role

    return _templates.TemplateResponse(
        request,
        "auth/account.html",
        {
            "current_user": current_user,
            "subscriptions": subs,
            "active_sub": active_sub,
            "user_orgs": user_orgs,
            "primary_org": primary_org,
            "primary_role": primary_role.value if primary_role else None,
        },
    )


# ---------- API routes ----------


@router.post("/api/auth/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    next: str = Form(default="/account"),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    email = _validate_email(email)
    user, _created = await get_or_create_user_by_email(db, email)
    await rotate_session_id(db, user)
    token = sign_session(user.session_id)
    return _redirect_with_session(next or "/account", token)


@router.post("/api/auth/login")
async def login(
    request: Request,
    email: str = Form(...),
    next: str = Form(default="/account"),
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    # Passwordless: same flow as signup. In prod, gate this behind a
    # magic-link confirmation step.
    email = _validate_email(email)
    user, _ = await get_or_create_user_by_email(db, email)
    await rotate_session_id(db, user)
    token = sign_session(user.session_id)
    return _redirect_with_session(next or "/account", token)


@router.post("/api/auth/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp
