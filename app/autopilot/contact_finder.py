"""Contact discovery.

Defines a `ContactFinder` Protocol so we can swap providers without
touching the mailer. Ships three concrete impls:

  StubContactFinder           — no-op; tests + dev default
  HomepageMailtoFinder        — scrapes mailto: links from the prospect's homepage
  ManualContactFinder         — reads a CSV (id,email,name?) for back-filling

Real Hunter.io / Apollo plugins land later behind the same Protocol.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Iterable, Protocol
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from app.db.models import Contact, Prospect

log = logging.getLogger(__name__)


class ContactFinder(Protocol):
    async def find(self, prospect: Prospect) -> list[Contact]:
        ...


class StubContactFinder:
    """No-op. Use when contact discovery is disabled or upstream provider is down."""

    async def find(self, prospect: Prospect) -> list[Contact]:
        return []


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _root_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


def _extract_mailto(html: str) -> set[str]:
    """Pull every `mailto:` from anchor tags, plus raw email regex in text."""
    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            addr = href.split(":", 1)[1].split("?", 1)[0].strip()
            if _EMAIL_RE.fullmatch(addr):
                found.add(addr.lower())
    # Free-text emails (footer, "contact us" pages, etc.)
    for match in _EMAIL_RE.finditer(soup.get_text(separator=" ")):
        found.add(match.group(0).lower())
    return found


# Emails to exclude — generic mailboxes that rarely get human replies and
# usually trip spam filters when targeted en masse.
_BANLIST_LOCALS = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "postmaster", "abuse", "webmaster", "privacy", "security",
}


def _is_useful_email(addr: str) -> bool:
    local = addr.split("@", 1)[0].lower()
    return local not in _BANLIST_LOCALS


class HomepageMailtoFinder:
    """Fetch the prospect's homepage and pull mailto/inline addresses.

    Bounded: one HTTP GET per prospect, 10s timeout, no follow-on /contact
    crawl. Stops at the first usable address — the cold email goes to a
    real human, not a generic alias.
    """

    def __init__(self, *, timeout: float = 10.0):
        self._timeout = timeout

    async def find(self, prospect: Prospect) -> list[Contact]:
        root = _root_of(prospect.url)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                resp = await client.get(root)
        except httpx.HTTPError as e:
            log.info("contact_finder: %s fetch failed: %s", root, e)
            return []
        if resp.status_code >= 400:
            log.info("contact_finder: %s returned %s", root, resp.status_code)
            return []

        emails = _extract_mailto(resp.text)
        useful = sorted(addr for addr in emails if _is_useful_email(addr))
        if not useful:
            return []

        return [
            Contact(
                prospect_id=prospect.id,
                email=addr,
                source="homepage_mailto",
                verified=False,
            )
            for addr in useful[:1]  # one human-target per prospect
        ]


class ManualContactFinder:
    """Read a CSV of pre-curated contacts. Useful for back-filling outreach
    or for hand-verified founder-led campaigns."""

    def __init__(self, csv_path: Path | str):
        self._path = Path(csv_path)

    async def find(self, prospect: Prospect) -> list[Contact]:
        if not self._path.exists():
            return []
        rows = self._read()
        out: list[Contact] = []
        for row in rows:
            if str(row.get("prospect_id")) != str(prospect.id):
                continue
            email = (row.get("email") or "").strip().lower()
            if not _EMAIL_RE.fullmatch(email):
                continue
            out.append(Contact(
                prospect_id=prospect.id,
                email=email,
                name=row.get("name") or None,
                role=row.get("role") or None,
                source="manual_csv",
                verified=False,
            ))
        return out

    def _read(self) -> list[dict[str, str]]:
        with self._path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))


def default_finder() -> ContactFinder:
    """Production default: composite chain.

    Order matters — cheaper signals first so we don't burn Hunter credit
    on prospects whose homepage already exposes a `mailto:`:

      1. HomepageMailtoFinder  — free, one HTTP GET per prospect
      2. HunterContactFinder   — paid; skipped if HUNTER_API_KEY unset

    If neither hits, returns an empty list and the mailer skips this
    prospect on the next pass.
    """
    # Local import to keep the optional Hunter dependency lazy.
    from app.autopilot.contact_finders import (
        CompositeContactFinder,
        HunterContactFinder,
    )

    return CompositeContactFinder([
        HomepageMailtoFinder(),
        HunterContactFinder(),
    ])
