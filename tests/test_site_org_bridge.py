"""Critical Decision #4 — Site.user_id → Site.org_id bridge tests.

Pure unit tests against the upsert helper using a tiny async stub of
`AsyncSession` so we exercise the precedence rules without Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.site.ingest import _upsert_site


@dataclass
class _Site:
    id: UUID = field(default_factory=uuid4)
    user_id: UUID | None = None
    org_id: UUID | None = None
    root_url: str = ""
    sitemap_url: str | None = None
    competitor_root_urls: list[str] | None = None


class _FakeSession:
    """Stand-in for AsyncSession; supports the subset _upsert_site uses."""

    def __init__(self, rows: list[_Site] | None = None):
        self.rows: list[_Site] = rows or []
        self._added: list[Any] = []
        self.match: dict[str, Any] = {}

    async def scalar(self, _stmt):
        # Inspect filters via the `match` attrs the test sets up.
        for r in self.rows:
            if (
                r.root_url == self.match["root_url"]
                and r.org_id == self.match.get("org_id")
                and (
                    "user_id" not in self.match
                    or r.user_id == self.match["user_id"]
                )
            ):
                return r
        return None

    def add(self, obj):
        from app.db.models import Site as _ORM

        if isinstance(obj, _ORM):
            translated = _Site(
                user_id=obj.user_id,
                org_id=obj.org_id,
                root_url=obj.root_url,
                sitemap_url=obj.sitemap_url,
                competitor_root_urls=obj.competitor_root_urls,
            )
            obj.id = translated.id
            self.rows.append(translated)
        elif isinstance(obj, _Site):
            self.rows.append(obj)
        self._added.append(obj)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_upsert_creates_new_row_when_org_id_present_and_no_match():
    session = _FakeSession()
    org = uuid4()
    session.match = {"root_url": "https://a/", "org_id": org}
    site = await _upsert_site(
        session,  # type: ignore[arg-type]
        user_id=None,
        org_id=org,
        root_url="https://a/",
        sitemap_url="https://a/sitemap.xml",
        competitor_root_urls=None,
    )
    assert site.org_id == org
    assert site.user_id is None


@pytest.mark.asyncio
async def test_upsert_finds_existing_org_owned_row():
    org = uuid4()
    existing = _Site(org_id=org, root_url="https://a/")
    session = _FakeSession(rows=[existing])
    session.match = {"root_url": "https://a/", "org_id": org}
    site = await _upsert_site(
        session,  # type: ignore[arg-type]
        user_id=None,
        org_id=org,
        root_url="https://a/",
        sitemap_url="https://a/sitemap.xml",
        competitor_root_urls=None,
    )
    assert site is existing
    assert site.sitemap_url == "https://a/sitemap.xml"  # updated


@pytest.mark.asyncio
async def test_upsert_falls_back_to_user_owned_legacy_row():
    user = uuid4()
    legacy = _Site(user_id=user, root_url="https://a/")
    session = _FakeSession(rows=[legacy])
    # Caller comes back without org context (pre-Phase-D path).
    session.match = {"root_url": "https://a/", "user_id": user, "org_id": None}
    site = await _upsert_site(
        session,  # type: ignore[arg-type]
        user_id=user,
        org_id=None,
        root_url="https://a/",
        sitemap_url=None,
        competitor_root_urls=None,
    )
    assert site is legacy
    assert site.user_id == user
    assert site.org_id is None


@pytest.mark.asyncio
async def test_upsert_backfills_org_id_onto_legacy_user_row():
    user = uuid4()
    org = uuid4()
    legacy = _Site(user_id=user, root_url="https://a/")
    session = _FakeSession(rows=[legacy])
    # Org-first lookup misses; we fall through and create a new row.
    # The test pins the documented behaviour: when an org-scoped caller
    # comes through and finds nothing, the legacy row is NOT silently
    # mutated — we'd rather create a fresh org-owned row than steal a
    # user's existing one.
    session.match = {"root_url": "https://a/", "org_id": org}
    site = await _upsert_site(
        session,  # type: ignore[arg-type]
        user_id=None,
        org_id=org,
        root_url="https://a/",
        sitemap_url=None,
        competitor_root_urls=None,
    )
    assert site.org_id == org
    assert site is not legacy
    assert legacy.org_id is None  # legacy untouched


@pytest.mark.asyncio
async def test_upsert_legacy_lookup_excludes_org_owned_rows():
    """A legacy (user-id-only) caller must NOT silently match an
    org-owned row that happens to share the same root_url."""
    user = uuid4()
    org = uuid4()
    org_owned = _Site(org_id=org, root_url="https://a/")
    session = _FakeSession(rows=[org_owned])
    session.match = {"root_url": "https://a/", "user_id": user, "org_id": None}
    site = await _upsert_site(
        session,  # type: ignore[arg-type]
        user_id=user,
        org_id=None,
        root_url="https://a/",
        sitemap_url=None,
        competitor_root_urls=None,
    )
    assert site is not org_owned
    assert site.org_id is None
    assert site.user_id == user


@pytest.mark.asyncio
async def test_upsert_org_caller_does_not_match_user_legacy_row_without_backfill():
    """Org callers shouldn't pull a legacy user-only row into their
    tenancy via the lookup — they get a fresh row instead."""
    user = uuid4()
    org = uuid4()
    legacy = _Site(user_id=user, root_url="https://a/")
    session = _FakeSession(rows=[legacy])
    session.match = {"root_url": "https://a/", "org_id": org}
    site = await _upsert_site(
        session,  # type: ignore[arg-type]
        user_id=None,
        org_id=org,
        root_url="https://a/",
        sitemap_url=None,
        competitor_root_urls=None,
    )
    assert site is not legacy
    assert site.org_id == org


@pytest.mark.asyncio
async def test_upsert_anonymous_caller_creates_orphan_row():
    """No org, no user — the Phase-A free-tier path. We still create a
    row so the pre-conversion flow keeps working."""
    session = _FakeSession()
    session.match = {"root_url": "https://a/", "org_id": None}
    site = await _upsert_site(
        session,  # type: ignore[arg-type]
        user_id=None,
        org_id=None,
        root_url="https://a/",
        sitemap_url=None,
        competitor_root_urls=None,
    )
    assert site.org_id is None
    assert site.user_id is None
