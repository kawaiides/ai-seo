"""Auth endpoints — signup, login, logout, account pages."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Subscription, SubscriptionStatus, User
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


def _redirect_with_session(target: str, session_token: str) -> RedirectResponse:
    resp = RedirectResponse(url=target, status_code=303)
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session_token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # flip in prod behind HTTPS
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
    return _templates.TemplateResponse(
        request,
        "auth/account.html",
        {
            "current_user": current_user,
            "subscriptions": subs,
            "active_sub": active_sub,
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
