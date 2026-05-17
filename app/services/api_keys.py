"""REST API keys (Phase E).

Bearer token format: `aegis_ak_<prefix6>_<secret32>`. We store only the
sha256 hash of the full token; the raw token is shown ONCE at creation
and never persisted. Token prefix is stored unhashed for lookup speed +
fingerprint display in the UI.

Scopes are `"resource:action"` strings. The check is a strict membership
test against the granted set — no implicit hierarchy. Wildcard `"*"`
grants everything; `"resource:*"` grants every action on `resource`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import ApiKey, Org

KEY_PREFIX = "aegis_ak"
PREFIX_RANDOM_LEN = 6
SECRET_LEN = 32
HEADER_NAME = "authorization"

SUPPORTED_SCOPES: frozenset[str] = frozenset(
    {
        "*",
        "audit:read",
        "audit:write",
        "site:read",
        "site:write",
        "linking:read",
        "linking:write",
        "rewrite:read",
        "rewrite:write",
        "geo:read",
        "geo:write",
    }
)


@dataclass(frozen=True)
class MintedKey:
    api_key_id: UUID
    org_id: UUID
    name: str
    prefix: str
    plaintext: str  # `aegis_ak_<prefix6>_<secret32>` — shown ONCE
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class AuthorizedKey:
    api_key: ApiKey
    org: Org


def _normalise_scopes(scopes: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in scopes:
        if not isinstance(s, str):
            raise HTTPException(status_code=422, detail={"error": "scope_not_str"})
        s = s.strip().lower()
        if not s or s in seen:
            continue
        if s not in SUPPORTED_SCOPES:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_scope", "scope": s},
            )
        seen.add(s)
        out.append(s)
    if not out:
        raise HTTPException(status_code=422, detail={"error": "no_scopes"})
    return out


def mint_token() -> tuple[str, str, str]:
    """Return `(prefix, secret, full_token)` triple. `prefix` is the
    visible fingerprint; `full_token` is the value the customer copies."""
    prefix = secrets.token_hex(PREFIX_RANDOM_LEN // 2)  # 6 hex chars
    secret = secrets.token_urlsafe(SECRET_LEN)
    full = f"{KEY_PREFIX}_{prefix}_{secret}"
    return prefix, secret, full


def hash_token(full_token: str) -> str:
    return hashlib.sha256(full_token.encode("utf-8")).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def create_api_key(
    session: AsyncSession,
    *,
    org_id: UUID,
    name: str,
    scopes: Sequence[str],
) -> MintedKey:
    if not name or not name.strip():
        raise HTTPException(status_code=422, detail={"error": "name_required"})
    normalised = _normalise_scopes(scopes)
    prefix, _, full_token = mint_token()
    api_key = ApiKey(
        org_id=org_id,
        name=name.strip(),
        prefix=prefix,
        key_hash=hash_token(full_token),
        scopes=normalised,
    )
    session.add(api_key)
    await session.flush()
    return MintedKey(
        api_key_id=api_key.id,
        org_id=org_id,
        name=api_key.name,
        prefix=prefix,
        plaintext=full_token,
        scopes=tuple(normalised),
    )


async def revoke_api_key(session: AsyncSession, api_key_id: UUID) -> None:
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None:
        raise HTTPException(status_code=404, detail={"error": "api_key_not_found"})
    if api_key.revoked_at is not None:
        return
    api_key.revoked_at = datetime.now(tz=timezone.utc)
    await session.flush()


async def list_api_keys(
    session: AsyncSession, org_id: UUID
) -> list[ApiKey]:
    return (
        await session.scalars(
            select(ApiKey)
            .where(ApiKey.org_id == org_id)
            .order_by(ApiKey.created_at.asc())
        )
    ).all()


# -------------------- Auth dependency --------------------


def parse_bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    parts = authorization_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


async def resolve_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthorizedKey:
    """FastAPI dependency: pull `Authorization: Bearer …`, look up the
    `ApiKey`, validate hash, refresh `last_used_at`. Raises 401 / 403 on
    any failure path."""
    token = parse_bearer_token(request.headers.get("authorization"))
    if not token:
        raise HTTPException(
            status_code=401, detail={"error": "missing_bearer_token"}
        )
    prefix = _extract_prefix(token)
    if prefix is None:
        raise HTTPException(
            status_code=401, detail={"error": "malformed_bearer_token"}
        )
    api_key = await session.scalar(
        select(ApiKey).where(ApiKey.prefix == prefix)
    )
    if api_key is None or not constant_time_compare(api_key.key_hash, hash_token(token)):
        # Same error envelope for both branches to avoid leaking whether
        # the prefix exists.
        raise HTTPException(status_code=401, detail={"error": "invalid_api_key"})
    if api_key.revoked_at is not None:
        raise HTTPException(status_code=401, detail={"error": "api_key_revoked"})
    org = await session.get(Org, api_key.org_id)
    if org is None:
        raise HTTPException(status_code=401, detail={"error": "org_not_found"})
    api_key.last_used_at = datetime.now(tz=timezone.utc)
    return AuthorizedKey(api_key=api_key, org=org)


def _extract_prefix(token: str) -> str | None:
    parts = token.split("_")
    if len(parts) < 4 or f"{parts[0]}_{parts[1]}" != KEY_PREFIX:
        return None
    prefix = parts[2]
    if len(prefix) != PREFIX_RANDOM_LEN or not all(
        c in "0123456789abcdef" for c in prefix
    ):
        return None
    return prefix


def has_scope(granted: Sequence[str], required: str) -> bool:
    """Strict scope membership with `*` and `resource:*` wildcards."""
    if not required or ":" not in required:
        return False
    resource, _, _action = required.partition(":")
    granted_set = set(granted)
    if "*" in granted_set:
        return True
    if f"{resource}:*" in granted_set:
        return True
    return required in granted_set


def require_scope(scope: str):
    """Dependency factory. Use after `Depends(resolve_api_key)` to gate a
    route on a specific scope."""

    async def _dep(
        authorized: AuthorizedKey = Depends(resolve_api_key),
    ) -> AuthorizedKey:
        if not has_scope(authorized.api_key.scopes, scope):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "scope_missing",
                    "required": scope,
                    "granted": list(authorized.api_key.scopes),
                },
            )
        return authorized

    return _dep
