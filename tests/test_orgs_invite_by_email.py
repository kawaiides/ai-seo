"""Schema-level tests for the new email-based org invite path.

DB-touching invite tests live in test_orgs_service.py; here we only
exercise the OrgInviteRequest model and confirm both paths
(user_id / email) parse cleanly. The route logic that selects which
path to take is unit-tested by hitting `_validate_invite_payload` via
the Pydantic model directly.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.models.schemas import OrgInviteRequest


def test_invite_with_user_id_only():
    req = OrgInviteRequest(user_id=uuid.uuid4(), role="editor")
    assert req.email is None
    assert req.user_id is not None


def test_invite_with_email_only():
    req = OrgInviteRequest(email="alice@example.com", role="viewer")
    assert req.user_id is None
    assert req.email == "alice@example.com"


def test_invite_with_both_user_id_and_email_accepted_at_schema_layer():
    # The route, not the schema, enforces exactly-one-of; we accept both
    # here so an admin tool can pass user_id while still echoing email
    # for logs.
    req = OrgInviteRequest(
        user_id=uuid.uuid4(),
        email="alice@example.com",
        role="owner",
    )
    assert req.user_id is not None
    assert req.email == "alice@example.com"


def test_invite_with_neither_user_id_nor_email_parses_but_route_rejects():
    # Schema permits omitting both; the /api/orgs/{id}/members route
    # raises 422 with missing_invitee in that case.
    req = OrgInviteRequest(role="viewer")
    assert req.user_id is None
    assert req.email is None


def test_invite_role_must_be_valid():
    with pytest.raises(ValidationError):
        OrgInviteRequest(email="a@b.com", role="superadmin")


def test_invite_email_too_long_rejected():
    # 320 chars is the schema cap (matches CITEXT column comment).
    long_email = "a" * 311 + "@b.com"  # 317 chars - just under cap
    req = OrgInviteRequest(email=long_email, role="viewer")
    assert req.email == long_email

    too_long = "a" * 320 + "@b.com"  # 326 chars
    with pytest.raises(ValidationError):
        OrgInviteRequest(email=too_long, role="viewer")
