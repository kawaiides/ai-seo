"""Account audit-log writer.

One helper — `record_audit_event` — that every mutation handler in
`app/api/{account,keys,orgs,billing}.py` calls right after the mutation
itself. The log is append-only; we never offer an update or delete API
for it (compliance trail).

We capture the request IP + truncated user agent so an operator
investigating an incident has the network context without having to
correlate against access logs.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccountAuditLog

log = logging.getLogger(__name__)

# Canonical action ids. Keep this list short + stable; downstream
# dashboards key off these strings. New events should append, never
# rename.
ACTION_ACCOUNT_DELETE = "account.delete"
ACTION_API_KEY_CREATE = "api_key.create"
ACTION_API_KEY_REVOKE = "api_key.revoke"
ACTION_ORG_CREATE = "org.create"
ACTION_ORG_MEMBER_INVITE = "org.member.invite"
ACTION_ORG_MEMBER_REMOVE = "org.member.remove"
ACTION_SUBSCRIPTION_CANCEL = "subscription.cancel"


def _client_ip(request: Optional[Request]) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _user_agent(request: Optional[Request]) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:512]


async def record_audit_event(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: UUID | None = None,
    subject_user_id: UUID | None = None,
    subject_org_id: UUID | None = None,
    meta: dict[str, Any] | None = None,
    request: Optional[Request] = None,
) -> AccountAuditLog:
    """Append an audit-log row. Never raises (logs + swallows) so a
    write failure doesn't break the user-facing action it was tracking.
    A retry runner can replay missed events from access logs later.
    """
    row = AccountAuditLog(
        actor_user_id=actor_user_id,
        subject_user_id=subject_user_id,
        subject_org_id=subject_org_id,
        action=action,
        request_ip=_client_ip(request),
        user_agent=_user_agent(request),
        meta=meta,
    )
    try:
        session.add(row)
        await session.flush()
    except Exception as e:  # noqa: BLE001 — don't let the audit log break the action
        log.warning("audit_log: failed to record %s: %s", action, e)
    return row
