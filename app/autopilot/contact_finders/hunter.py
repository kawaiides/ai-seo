"""Hunter.io contact finder.

Two-stage lookup:

  1. `GET /v2/domain-search?domain=<prospect_domain>&seniority=senior,executive`
     — returns the most authoritative emails (founders, heads of content,
     content directors). Filtered server-side by seniority so we skip
     the support@ noise.
  2. If stage 1 returns zero emails, fall back to `/v2/email-finder`
     with `first_name=&last_name=` if the prospect's `meta.author` is
     populated (set by the SERP parser).

Hunter's free tier is 25 searches/month, paid starts at $49/mo for 500.
That budget aligns with the autopilot's daily-cap of 30 sends, so we
spend Hunter credit only on prospects whose homepage didn't expose a
mailto:/inline email — `CompositeContactFinder` enforces that ordering.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.db.models import Contact, Prospect

log = logging.getLogger(__name__)

HUNTER_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
HUNTER_EMAIL_FINDER_URL = "https://api.hunter.io/v2/email-finder"

_DEFAULT_SENIORITY = "senior,executive"
_DEFAULT_DEPARTMENTS = "marketing,content,engineering"


def _domain_of(url: str) -> str:
    netloc = urlsplit(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


class HunterContactFinder:
    """Hunter.io domain-search + email-finder lookup.

    Skips silently when `HUNTER_API_KEY` is unset (no-op return) so the
    composite chain can fall through to the next finder.
    """

    name = "hunter"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
        seniority: str = _DEFAULT_SENIORITY,
        departments: str = _DEFAULT_DEPARTMENTS,
        max_contacts: int = 1,
    ) -> None:
        self._api_key = api_key or os.environ.get("HUNTER_API_KEY")
        self._timeout = timeout
        self._client = client
        self._seniority = seniority
        self._departments = departments
        self._max_contacts = max_contacts

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def find(self, prospect: Prospect) -> list[Contact]:
        if not self._api_key:
            return []
        domain = _domain_of(prospect.url)
        if not domain:
            return []
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._client is None
        try:
            emails = await self._domain_search(client, domain)
            if not emails:
                emails = await self._email_finder_fallback(client, domain, prospect)
        except httpx.HTTPError as e:
            log.info("hunter: %s lookup failed: %s", domain, e)
            return []
        finally:
            if owns_client:
                await client.aclose()
        return [
            Contact(
                prospect_id=prospect.id,
                email=item["email"],
                name=item.get("name") or None,
                role=item.get("position") or None,
                source="hunter",
                verified=item.get("verified", False),
            )
            for item in emails[: self._max_contacts]
        ]

    async def _domain_search(
        self, client: httpx.AsyncClient, domain: str
    ) -> list[dict[str, Any]]:
        params = {
            "domain": domain,
            "api_key": self._api_key,
            "seniority": self._seniority,
            "department": self._departments,
            "limit": 10,
        }
        resp = await client.get(HUNTER_DOMAIN_SEARCH_URL, params=params)
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            log.info(
                "hunter: domain-search %s returned %s: %s",
                domain, resp.status_code, resp.text[:200],
            )
            return []
        data = resp.json().get("data", {}) or {}
        raw_emails = data.get("emails", []) or []
        out: list[dict[str, Any]] = []
        for entry in raw_emails:
            email = (entry.get("value") or "").strip().lower()
            if not email:
                continue
            confidence = entry.get("confidence") or 0
            if confidence < 50:  # Hunter's own threshold for "likely deliverable"
                continue
            name_parts = [entry.get("first_name"), entry.get("last_name")]
            name = " ".join(p for p in name_parts if p) or None
            out.append({
                "email": email,
                "name": name,
                "position": entry.get("position"),
                "verified": confidence >= 90,
            })
        return out

    async def _email_finder_fallback(
        self, client: httpx.AsyncClient, domain: str, prospect: Prospect
    ) -> list[dict[str, Any]]:
        """Hit /v2/email-finder if we know a candidate name (e.g. from
        the SERP byline). Burns 1 Hunter credit per call, so we only run
        when a domain-search returned nothing."""
        meta = getattr(prospect, "meta", None) or {}
        first = (meta.get("author_first") or "").strip()
        last = (meta.get("author_last") or "").strip()
        if not first or not last:
            return []
        params = {
            "domain": domain,
            "api_key": self._api_key,
            "first_name": first,
            "last_name": last,
        }
        resp = await client.get(HUNTER_EMAIL_FINDER_URL, params=params)
        if resp.status_code >= 400:
            return []
        data = resp.json().get("data", {}) or {}
        email = (data.get("email") or "").strip().lower()
        if not email:
            return []
        confidence = data.get("score") or 0
        return [{
            "email": email,
            "name": f"{first} {last}",
            "position": data.get("position"),
            "verified": confidence >= 90,
        }]
