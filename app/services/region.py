"""IP-based country resolution + price routing.

Resolver chain:
  1. `CF-IPCountry` header (always trusted when behind Cloudflare).
  2. `X-Forwarded-For` first hop → `https://ipapi.co/{ip}/country/` (cached 24h).
  3. Fallback to `"US"`.

The map is intentionally narrow: India routes to Razorpay+INR; everywhere
else routes to Stripe+USD. Expand the table when we add more local
processors.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request

log = logging.getLogger(__name__)

DEFAULT_COUNTRY = "US"
IPAPI_TIMEOUT = 2.0
CACHE_TTL_SECONDS = 24 * 3600


# country code → routing decision
_PRICE_MAP: dict[str, dict[str, Any]] = {
    "IN": {
        "country": "IN",
        "currency": "INR",
        "price": 99900,
        "price_display": "₹999/mo",
        "processor": "razorpay",
    },
}
_DEFAULT = {
    "country": "US",
    "currency": "USD",
    "price": 4900,
    "price_display": "$49/mo",
    "processor": "stripe",
}


# Process-local IP → (country, fetched_at) cache.
_ip_cache: dict[str, tuple[str, float]] = {}


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


async def _ipapi_lookup(ip: str) -> str | None:
    """Hit ipapi.co for an IP. Returns ISO country or None on failure."""
    now = time.monotonic()
    cached = _ip_cache.get(ip)
    if cached and now - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]
    try:
        async with httpx.AsyncClient(timeout=IPAPI_TIMEOUT) as client:
            resp = await client.get(f"https://ipapi.co/{ip}/country/")
    except httpx.HTTPError as e:
        log.info("region: ipapi network error for %s: %s", ip, e)
        return None
    if resp.status_code != 200:
        return None
    country = (resp.text or "").strip().upper()
    # ipapi returns "Undefined" for private/invalid IPs.
    if not country or len(country) != 2:
        return None
    _ip_cache[ip] = (country, now)
    return country


async def resolve_country(request: Request) -> str:
    """Pick a 2-letter country code for the caller.

    Order: CF-IPCountry → ipapi-on-XFF → ipapi-on-peer-IP → US fallback.
    """
    cf = request.headers.get("cf-ipcountry")
    if cf and len(cf) == 2:
        return cf.upper()

    ip = _client_ip(request)
    if ip and ip not in {"127.0.0.1", "::1", "testclient"}:
        country = await _ipapi_lookup(ip)
        if country:
            return country
    return DEFAULT_COUNTRY


def route_for_country(country: str) -> dict[str, Any]:
    """Map a country code to the price/processor envelope."""
    return dict(_PRICE_MAP.get(country.upper(), _DEFAULT))


async def resolve_region(request: Request) -> dict[str, Any]:
    """One-shot helper: returns the full envelope used by `/api/region`."""
    country = await resolve_country(request)
    envelope = route_for_country(country)
    envelope["country"] = country
    return envelope


def reset_cache() -> None:
    """Test helper."""
    _ip_cache.clear()
