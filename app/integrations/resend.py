"""Resend API mailer.

Drop-in alternative to the SMTP/aiosmtplib path. Implements the same
`MailTransport` Protocol that `outbox_mailer` dispatches against, so the
outbox doesn't care which carrier is active.

Selection happens in `outbox_mailer._select_transport()`:

  RESEND_API_KEY set   → ResendMailer (this module)
  SMTP_HOST    set     → SMTPTransport
  neither              → DryRunTransport

Resend was chosen over a self-managed SMTP relay because the autopilot's
cold-outreach volume during warm-up (~30/day) lives well inside Resend's
free-tier 3k/month, and Resend handles SPF/DKIM/DMARC alignment for the
sender domain without operator effort.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single send. `retryable` flags transient 5xx so the
    outbox can leave the Outreach row un-stamped and retry on the next
    tick instead of marking it permanently failed."""

    delivered: bool
    detail: str | None = None
    retryable: bool = False
    provider_id: str | None = None


class ResendMailer:
    """HTTPS POST to Resend's REST API.

    Honours `RESEND_API_KEY` env var; emits a not-enabled `SendResult`
    when missing so the outbox can fall through to SMTP without raising.
    """

    name = "resend"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("RESEND_API_KEY")
        self._timeout = timeout
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        from_addr: str,
        reply_to: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> SendResult:
        if not self._api_key:
            return SendResult(delivered=False, detail="resend api key not configured")
        payload: dict[str, object] = {
            "from": from_addr,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if reply_to:
            payload["reply_to"] = reply_to
        if extra_headers:
            # Resend's API accepts arbitrary RFC 5322 headers via the
            # `headers` object — used here to carry List-Unsubscribe +
            # List-Unsubscribe-Post for CAN-SPAM / RFC 8058 compliance.
            payload["headers"] = dict(extra_headers)
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._client is None
        try:
            resp = await client.post(
                RESEND_API_URL,
                content=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            # Connect/read errors are usually transient — let the outbox
            # retry on the next tick.
            return SendResult(
                delivered=False,
                detail=f"resend network error: {type(e).__name__}: {e}",
                retryable=True,
            )
        finally:
            if owns_client:
                await client.aclose()

        if 200 <= resp.status_code < 300:
            try:
                provider_id = resp.json().get("id")
            except ValueError:
                provider_id = None
            return SendResult(delivered=True, provider_id=provider_id)

        body_preview = resp.text[:300] if resp.text else ""
        retryable = resp.status_code >= 500 or resp.status_code in (408, 429)
        return SendResult(
            delivered=False,
            detail=f"resend HTTP {resp.status_code}: {body_preview}",
            retryable=retryable,
        )
