"""Bring-Your-Own-Key validation.

Customers paste their OpenAI/Gemini key into the tool and we route LLM
calls through it instead of our singleton. The raw key is **never**
persisted — we store `sha256(key)` in `byok_validation` purely as a
cache for "this key worked at time T, valid for the next 6 hours."

Security model:
  - Key only reaches the server via `X-BYOK-OpenAI-Key` header (never query string).
  - Hash-at-rest. Raw key lives only in process memory for the duration
    of one request.
  - Structured log redaction at the middleware layer (caller responsibility).
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BYOKProvider, BYOKValidation, User

log = logging.getLogger(__name__)

OPENAI_VALIDATE_URL = "https://api.openai.com/v1/models"
GEMINI_VALIDATE_URL = "https://generativelanguage.googleapis.com/v1/models"

VALIDATION_TIMEOUT = 5.0


def _ttl_seconds() -> int:
    return int(os.environ.get("BYOK_VALIDATION_TTL_SECONDS", str(6 * 3600)))


def hash_key(raw_key: str) -> str:
    """Lower-cased sha256 hex. Length-64, deterministic."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def _live_check_openai(key: str) -> bool:
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT) as client:
            resp = await client.get(OPENAI_VALIDATE_URL, headers=headers)
    except httpx.HTTPError as e:
        log.info("byok: openai live-check network error: %s", e)
        return False
    return resp.status_code == 200


async def _live_check_gemini(key: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT) as client:
            resp = await client.get(f"{GEMINI_VALIDATE_URL}?key={key}")
    except httpx.HTTPError as e:
        log.info("byok: gemini live-check network error: %s", e)
        return False
    return resp.status_code == 200


async def validate_key(
    session: AsyncSession,
    user: User,
    raw_key: str,
    provider: BYOKProvider = BYOKProvider.openai,
) -> bool:
    """Verify the key works, upsert the cached result.

    Cache rules:
      - Same (user, key_hash) within TTL → return cached `valid` without
        round-tripping the provider.
      - Otherwise hit the provider's models endpoint; success → valid=True,
        non-2xx or network error → valid=False. Either way we cache.
    """
    if not raw_key or not raw_key.strip():
        return False

    key_hash = hash_key(raw_key)
    now = datetime.now(tz=timezone.utc)
    ttl = timedelta(seconds=_ttl_seconds())

    existing = (
        await session.execute(
            select(BYOKValidation)
            .where(BYOKValidation.user_id == user.id)
            .where(BYOKValidation.key_hash == key_hash)
        )
    ).scalar_one_or_none()

    if existing is not None and now - existing.last_verified_at < ttl:
        return existing.valid

    valid = await (
        _live_check_openai(raw_key)
        if provider == BYOKProvider.openai
        else _live_check_gemini(raw_key)
    )

    if existing is None:
        session.add(
            BYOKValidation(
                user_id=user.id,
                key_hash=key_hash,
                provider=provider,
                valid=valid,
                last_verified_at=now,
            )
        )
    else:
        existing.valid = valid
        existing.last_verified_at = now

    await session.flush()
    return valid


async def is_key_valid_cached(
    session: AsyncSession,
    user: User,
    raw_key: str,
    provider: BYOKProvider = BYOKProvider.openai,
) -> bool:
    """Return True if a cached, still-fresh validation says this key works.

    Does NOT live-check. Used on the hot path (every fanout request) to
    keep latency low — explicit `validate_key` call is what populates the
    cache from the validation endpoint.
    """
    if not raw_key:
        return False
    key_hash = hash_key(raw_key)
    now = datetime.now(tz=timezone.utc)
    ttl = timedelta(seconds=_ttl_seconds())
    existing = (
        await session.execute(
            select(BYOKValidation)
            .where(BYOKValidation.user_id == user.id)
            .where(BYOKValidation.key_hash == key_hash)
            .where(BYOKValidation.provider == provider)
        )
    ).scalar_one_or_none()
    if existing is None:
        return False
    if now - existing.last_verified_at >= ttl:
        return False
    return existing.valid
