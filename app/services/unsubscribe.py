"""One-click unsubscribe service.

Implements RFC 8058 + CAN-SPAM list-unsubscribe support for the
autopilot's cold-outreach mail. Two surfaces:

  * `make_token(contact_id, audit_id) -> str`
        signed payload that goes into the `List-Unsubscribe` URL.
  * `resolve_token(token) -> (contact_id, audit_id) | None`
        called by the `/unsubscribe` route to look up which Contact to
        mark unsubscribed. Tokens never expire (CAN-SPAM doesn't put a
        TTL on the right to opt out), but we still sign them with the
        same secret stack as the session cookie so they can't be forged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Contact
from app.services.auth import _secret  # reuse the same prod-vs-test secret loader


_SALT = "aegis-unsubscribe-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt=_SALT)


def make_token(contact_id: int, audit_id: int | None = None) -> str:
    """Sign `{contact_id, audit_id}` so the unsubscribe link can't be
    forged. audit_id is optional; included so we can log *which* send
    triggered the unsubscribe for compliance reporting."""
    payload = {"c": contact_id}
    if audit_id is not None:
        payload["a"] = audit_id
    return _serializer().dumps(payload)


def resolve_token(token: str) -> tuple[int, int | None] | None:
    """Reverse `make_token`. Returns None on tamper / wrong-salt input.

    No max_age — RFC 8058 + CAN-SPAM require unsubscribe links to remain
    functional for at least 30 days after a send, and there's no spec
    ceiling. Operators who need to rotate tokens can rotate
    `AEGIS_SECRET_KEY` (every old link breaks).
    """
    try:
        data = _serializer().loads(token, max_age=None)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    contact_id = data.get("c")
    if not isinstance(contact_id, int):
        return None
    audit_id = data.get("a")
    if not isinstance(audit_id, int):
        audit_id = None
    return contact_id, audit_id


async def mark_unsubscribed(
    session: AsyncSession,
    *,
    contact_id: int,
) -> bool:
    """Stamp `contact.unsubscribed_at` if not already set. Returns True
    on the first call, False on every subsequent call (so callers can
    log a single funnel event per contact)."""
    now = datetime.now(tz=timezone.utc)
    result = await session.execute(
        update(Contact)
        .where(Contact.id == contact_id, Contact.unsubscribed_at.is_(None))
        .values(unsubscribed_at=now)
    )
    return result.rowcount > 0
