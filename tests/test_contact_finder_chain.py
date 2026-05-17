"""Unit tests for `CompositeContactFinder`."""

from __future__ import annotations

import pytest

from app.autopilot.contact_finders.composite import CompositeContactFinder
from app.db.models import Contact, Prospect


def _prospect() -> Prospect:
    p = Prospect(url="https://acme.test/", domain="acme.test", target_keyword="x")
    p.id = 1
    return p


class _StaticFinder:
    def __init__(self, name: str, contacts: list[Contact] | None = None,
                 raises: Exception | None = None):
        self.name = name
        self._contacts = contacts or []
        self._raises = raises
        self.calls = 0

    async def find(self, prospect: Prospect) -> list[Contact]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return list(self._contacts)


@pytest.mark.asyncio
async def test_first_hit_short_circuits_chain():
    f1 = _StaticFinder("mailto", [Contact(prospect_id=1, email="a@acme.test", source="mailto")])
    f2 = _StaticFinder("hunter", [Contact(prospect_id=1, email="b@acme.test", source="hunter")])
    chain = CompositeContactFinder([f1, f2])
    out = await chain.find(_prospect())
    assert [c.email for c in out] == ["a@acme.test"]
    assert f1.calls == 1
    assert f2.calls == 0


@pytest.mark.asyncio
async def test_empty_first_falls_through_to_second():
    f1 = _StaticFinder("mailto", [])
    f2 = _StaticFinder("hunter", [Contact(prospect_id=1, email="b@acme.test", source="hunter")])
    chain = CompositeContactFinder([f1, f2])
    out = await chain.find(_prospect())
    assert [c.email for c in out] == ["b@acme.test"]
    assert f1.calls == f2.calls == 1


@pytest.mark.asyncio
async def test_all_empty_returns_empty():
    f1 = _StaticFinder("mailto", [])
    f2 = _StaticFinder("hunter", [])
    chain = CompositeContactFinder([f1, f2])
    assert await chain.find(_prospect()) == []


@pytest.mark.asyncio
async def test_raising_finder_is_skipped():
    f1 = _StaticFinder("boom", raises=RuntimeError("blew up"))
    f2 = _StaticFinder("hunter", [Contact(prospect_id=1, email="b@acme.test", source="hunter")])
    chain = CompositeContactFinder([f1, f2])
    out = await chain.find(_prospect())
    assert [c.email for c in out] == ["b@acme.test"]
    assert f1.calls == 1


@pytest.mark.asyncio
async def test_names_property_lists_underlying_finders():
    f1 = _StaticFinder("alpha")
    f2 = _StaticFinder("beta")
    chain = CompositeContactFinder([f1, f2])
    assert chain.names == ["alpha", "beta"]
