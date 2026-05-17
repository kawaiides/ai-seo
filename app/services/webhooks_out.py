"""Customer-configured outbound webhooks (Phase E).

Customers register a `Webhook(url, events, secret)` per `Org`; we POST
the event payload to that URL when one of the subscribed events fires.
Mirrors the processor-webhook pattern in `app.api.billing` so the
operational story is consistent: signed body, idempotency-friendly
event IDs, append-only `WebhookDelivery` history.

Signing:
  - SHA256 HMAC over the raw JSON body, hex-encoded.
  - Sent as `X-Aegis-Signature: sha256=<hex>` so receivers can verify.
  - Plus `X-Aegis-Event` (event name) + `X-Aegis-Delivery` (UUID).

Retry policy is *not* baked into this module — the public `dispatch`
function attempts exactly once and records the outcome on
`WebhookDelivery`. A scheduled retry runner can scan
`WebhookDelivery.where(delivered=False, attempt<MAX)` later and call
`dispatch` again with `attempt+1`. Keeping retry out of the hot path
means the audit endpoint can fire-and-forget without coupling its
latency to the customer's webhook receiver.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Webhook, WebhookDelivery

SUPPORTED_EVENTS: frozenset[str] = frozenset(
    {
        "audit.completed",
        "audit.failed",
        "score.dropped",
        "geo.probe.completed",
        "site.ingested",
    }
)
SIGNATURE_HEADER = "X-Aegis-Signature"
EVENT_HEADER = "X-Aegis-Event"
DELIVERY_HEADER = "X-Aegis-Delivery"
DEFAULT_TIMEOUT_SECONDS = 5.0
SECRET_BYTES = 32
MAX_RESPONSE_BODY_CAPTURED = 512


@dataclass(frozen=True)
class DispatchResult:
    delivery_id: int
    delivered: bool
    response_status: int | None
    error: str | None


def generate_secret() -> str:
    return secrets.token_urlsafe(SECRET_BYTES)


def sign_payload(secret: str, raw_body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, raw_body: bytes, header_value: str) -> bool:
    expected = sign_payload(secret, raw_body)
    return hmac.compare_digest(expected, header_value or "")


def _normalise_events(events: Sequence[str]) -> list[str]:
    if not events:
        raise HTTPException(status_code=422, detail={"error": "no_events"})
    out: list[str] = []
    seen: set[str] = set()
    for e in events:
        e = (e or "").strip().lower()
        if e and e not in seen and e in SUPPORTED_EVENTS:
            out.append(e)
            seen.add(e)
        elif e and e not in SUPPORTED_EVENTS:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_event", "event": e},
            )
    if not out:
        raise HTTPException(status_code=422, detail={"error": "no_events"})
    return out


async def create_webhook(
    session: AsyncSession,
    *,
    org_id: UUID,
    url: str,
    events: Sequence[str],
) -> Webhook:
    url = (url or "").strip()
    if not url or not (url.startswith("https://") or url.startswith("http://")):
        raise HTTPException(status_code=422, detail={"error": "invalid_url"})
    normalised = _normalise_events(events)
    webhook = Webhook(
        org_id=org_id,
        url=url,
        secret=generate_secret(),
        events=normalised,
        enabled=True,
    )
    session.add(webhook)
    await session.flush()
    return webhook


async def list_webhooks(session: AsyncSession, org_id: UUID) -> list[Webhook]:
    return (
        await session.scalars(
            select(Webhook)
            .where(Webhook.org_id == org_id)
            .order_by(Webhook.created_at.asc())
        )
    ).all()


async def delete_webhook(session: AsyncSession, webhook_id: UUID) -> None:
    wh = await session.get(Webhook, webhook_id)
    if wh is None:
        raise HTTPException(status_code=404, detail={"error": "webhook_not_found"})
    await session.delete(wh)
    await session.flush()


async def dispatch_event(
    session: AsyncSession,
    *,
    org_id: UUID,
    event: str,
    payload: dict[str, Any],
    client: httpx.AsyncClient | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempt: int = 1,
) -> list[DispatchResult]:
    """Fan out `event`/`payload` to every enabled webhook on `org_id`.

    Returns one `DispatchResult` per subscribed webhook. Per-receiver
    failures are recorded on `WebhookDelivery` and surfaced in the
    result; they don't abort the fan-out.
    """
    if event not in SUPPORTED_EVENTS:
        raise HTTPException(
            status_code=422,
            detail={"error": "unsupported_event", "event": event},
        )
    subs = (
        await session.scalars(
            select(Webhook).where(
                Webhook.org_id == org_id, Webhook.enabled.is_(True)
            )
        )
    ).all()
    if not subs:
        return []
    raw_body = json.dumps(
        _envelope(event, payload), separators=(",", ":"), sort_keys=True
    ).encode("utf-8")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    results: list[DispatchResult] = []
    try:
        for webhook in subs:
            if event not in (webhook.events or []):
                continue
            result = await _post_single(
                session,
                webhook=webhook,
                event=event,
                raw_body=raw_body,
                payload=payload,
                client=client,
                attempt=attempt,
            )
            results.append(result)
    finally:
        if owns_client:
            await client.aclose()
    await session.flush()
    return results


def _envelope(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "event": event,
        "occurred_at": datetime.now(tz=timezone.utc).isoformat(),
        "data": payload,
    }


async def _post_single(
    session: AsyncSession,
    *,
    webhook: Webhook,
    event: str,
    raw_body: bytes,
    payload: dict[str, Any],
    client: httpx.AsyncClient,
    attempt: int,
) -> DispatchResult:
    signature = sign_payload(webhook.secret, raw_body)
    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        event=event,
        payload=payload,
        attempt=attempt,
        attempted_at=datetime.now(tz=timezone.utc),
    )
    session.add(delivery)
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature,
        EVENT_HEADER: event,
        DELIVERY_HEADER: str(uuid.uuid4()),
    }
    try:
        resp = await client.post(webhook.url, content=raw_body, headers=headers)
        delivery.response_status = resp.status_code
        delivery.response_body = resp.text[:MAX_RESPONSE_BODY_CAPTURED]
        if 200 <= resp.status_code < 300:
            delivery.delivered = True
            return DispatchResult(
                delivery_id=delivery.id or 0,
                delivered=True,
                response_status=resp.status_code,
                error=None,
            )
        delivery.delivered = False
        return DispatchResult(
            delivery_id=delivery.id or 0,
            delivered=False,
            response_status=resp.status_code,
            error=f"HTTP {resp.status_code}",
        )
    except httpx.HTTPError as e:
        delivery.error = f"{type(e).__name__}: {e}"
        return DispatchResult(
            delivery_id=delivery.id or 0,
            delivered=False,
            response_status=None,
            error=delivery.error,
        )


def filter_subscribers_for_event(
    webhooks: Iterable[Webhook], event: str
) -> list[Webhook]:
    """Pure helper for tests + admin tooling."""
    return [w for w in webhooks if w.enabled and event in (w.events or [])]
