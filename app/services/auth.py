"""Session-cookie auth.

Passwordless: signup/login take an email, upsert a `User` row, and set a
signed cookie containing the user's `session_id`. No password column means
no migration. The `itsdangerous` serializer prevents tampering.

For production, replace this with magic-link confirmation (email the user
a single-use token before issuing the cookie).
"""

from __future__ import annotations

import os
import secrets
import uuid
from typing import Optional

from fastapi import Cookie, Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import User, UserPlan

COOKIE_NAME = "aegis_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


class SessionSecretMissing(RuntimeError):
    """Raised when no signing key is configured in a non-dev environment."""


def _is_dev_env() -> bool:
    return os.environ.get("AEGIS_ENV", "prod").lower() in {"dev", "test", "local"}


def _secret() -> str:
    """Session-signing secret.

    Production deploys MUST set `AEGIS_SECRET_KEY` (or the legacy alias
    `SESSION_SIGNING_KEY`). The previous hardcoded dev fallback has been
    removed — silently signing prod cookies with a public string was a
    full authentication bypass.

    Dev/test (`AEGIS_ENV=dev|test|local`) still gets a stable per-process
    auto-generated secret so the test suite doesn't need to provision one.
    """
    key = os.environ.get("AEGIS_SECRET_KEY") or os.environ.get("SESSION_SIGNING_KEY")
    if key:
        return key
    if _is_dev_env():
        return _ephemeral_dev_secret()
    raise SessionSecretMissing(
        "AEGIS_SECRET_KEY (or SESSION_SIGNING_KEY) is not set. "
        "Generate one with `python -c 'import secrets; print(secrets.token_urlsafe(48))'` "
        "and inject it via Secrets Manager / .env."
    )


_DEV_SECRET_CACHE: str | None = None


def _ephemeral_dev_secret() -> str:
    """Per-process random secret used only when AEGIS_ENV ∈ {dev,test,local}.

    Stable across calls within the process so cookies survive within a
    test run, but rotated on every process start so leaked dev cookies
    never carry over.
    """
    global _DEV_SECRET_CACHE
    if _DEV_SECRET_CACHE is None:
        _DEV_SECRET_CACHE = secrets.token_urlsafe(48)
    return _DEV_SECRET_CACHE


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="aegis-session-v2")


def sign_session(session_id: str) -> str:
    return _serializer().dumps({"sid": session_id})


def unsign_session(cookie_value: str) -> str | None:
    try:
        data = _serializer().loads(cookie_value, max_age=COOKIE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("sid") if isinstance(data, dict) else None


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


async def get_or_create_user_by_email(
    session: AsyncSession,
    email: str,
) -> tuple[User, bool]:
    """Idempotent upsert. Returns (user, created)."""
    email_norm = email.strip().lower()
    existing = await session.execute(select(User).where(User.email == email_norm))
    user = existing.scalar_one_or_none()
    if user is not None:
        return user, False
    user = User(
        id=uuid.uuid4(),
        session_id=new_session_id(),
        email=email_norm,
        plan=UserPlan.free,
    )
    session.add(user)
    await session.flush()
    return user, True


async def rotate_session_id(session: AsyncSession, user: User) -> str:
    """Issue a fresh session_id (e.g. on every login) and persist it."""
    user.session_id = new_session_id()
    await session.flush()
    return user.session_id


async def get_current_user(
    request: Request,
    aegis_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
    db: AsyncSession = Depends(get_session),
) -> Optional[User]:
    if not aegis_session:
        return None
    sid = unsign_session(aegis_session)
    if not sid:
        return None
    res = await db.execute(select(User).where(User.session_id == sid))
    return res.scalar_one_or_none()


async def require_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    from fastapi import HTTPException

    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user
