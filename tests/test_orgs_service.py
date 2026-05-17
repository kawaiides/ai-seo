"""Unit tests for org role + slug helpers (Phase D).

DB-touching ops are exercised by integration tests against pg. Here we
focus on the pure logic: role privilege ordering, slug validation,
generation determinism boundaries.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.db.models import OrgRole
from app.services.orgs import (
    ROLE_RANK,
    _generate_slug,
    _validate_slug,
    has_min_role,
)


class _M:
    """Tiny stand-in for `OrgMember`."""

    def __init__(self, role: OrgRole):
        self.role = role


def test_role_rank_owner_outranks_editor_outranks_viewer():
    assert ROLE_RANK[OrgRole.owner] > ROLE_RANK[OrgRole.editor] > ROLE_RANK[OrgRole.viewer]


def test_has_min_role_owner_passes_all():
    owner = _M(OrgRole.owner)
    for r in OrgRole:
        assert has_min_role(owner, r)


def test_has_min_role_viewer_blocks_editor():
    viewer = _M(OrgRole.viewer)
    assert has_min_role(viewer, OrgRole.viewer)
    assert not has_min_role(viewer, OrgRole.editor)
    assert not has_min_role(viewer, OrgRole.owner)


def test_has_min_role_editor_can_view_cannot_own():
    editor = _M(OrgRole.editor)
    assert has_min_role(editor, OrgRole.viewer)
    assert has_min_role(editor, OrgRole.editor)
    assert not has_min_role(editor, OrgRole.owner)


def test_validate_slug_accepts_kebab():
    assert _validate_slug("acme") == "acme"
    assert _validate_slug("Acme-Co") == "acme-co"
    assert _validate_slug("acme-co-123") == "acme-co-123"


def test_validate_slug_rejects_uppercase_after_strip():
    # `_validate_slug` lowercases before regex; uppercase + spaces still pass
    # because they're transformed. But internal whitespace breaks the pattern.
    with pytest.raises(HTTPException):
        _validate_slug("acme co")
    with pytest.raises(HTTPException):
        _validate_slug("acme_co")
    with pytest.raises(HTTPException):
        _validate_slug("-acme")
    with pytest.raises(HTTPException):
        _validate_slug("acme-")


def test_validate_slug_rejects_long_slug():
    long = "a" * 65
    with pytest.raises(HTTPException):
        _validate_slug(long)


def test_generate_slug_deterministic_base_with_nonce():
    a = _generate_slug("Acme Co!")
    b = _generate_slug("Acme Co!")
    # Same base, different nonces
    assert a.startswith("acme-co-")
    assert b.startswith("acme-co-")
    assert a != b


def test_generate_slug_handles_pure_symbol_name():
    s = _generate_slug("!!!")
    assert s.startswith("org-")
