"""Unit tests for API key minting, scoping, parsing (Phase E)."""

from __future__ import annotations

import pytest

from app.services.api_keys import (
    KEY_PREFIX,
    PREFIX_RANDOM_LEN,
    _extract_prefix,
    constant_time_compare,
    has_scope,
    hash_token,
    mint_token,
    parse_bearer_token,
)


def test_mint_token_format():
    prefix, secret, full = mint_token()
    assert len(prefix) == PREFIX_RANDOM_LEN
    assert all(c in "0123456789abcdef" for c in prefix)
    assert full.startswith(f"{KEY_PREFIX}_{prefix}_")
    assert secret in full


def test_mint_token_is_unique():
    seen = {mint_token()[2] for _ in range(50)}
    assert len(seen) == 50  # vanishingly unlikely collision otherwise


def test_hash_token_deterministic():
    _, _, full = mint_token()
    assert hash_token(full) == hash_token(full)
    assert hash_token(full) != hash_token(full + "x")


def test_constant_time_compare_works():
    assert constant_time_compare("abc", "abc")
    assert not constant_time_compare("abc", "abd")


def test_parse_bearer_token():
    assert parse_bearer_token("Bearer abc123") == "abc123"
    assert parse_bearer_token("bearer abc123") == "abc123"  # case insensitive
    assert parse_bearer_token("Token abc") is None
    assert parse_bearer_token(None) is None
    assert parse_bearer_token("") is None
    assert parse_bearer_token("Bearer ") is None


def test_extract_prefix_round_trip():
    prefix, _, full = mint_token()
    assert _extract_prefix(full) == prefix


def test_extract_prefix_rejects_malformed():
    assert _extract_prefix("just-some-string") is None
    assert _extract_prefix("aegis_ak_short_x") is None  # prefix wrong length
    assert _extract_prefix("aegis_ak_zzzzzz_secret") is None  # non-hex prefix


def test_has_scope_strict():
    assert has_scope(["audit:read"], "audit:read")
    assert not has_scope(["audit:read"], "audit:write")
    assert not has_scope([], "audit:read")


def test_has_scope_wildcard_all():
    assert has_scope(["*"], "audit:read")
    assert has_scope(["*"], "geo:write")


def test_has_scope_resource_wildcard():
    assert has_scope(["audit:*"], "audit:read")
    assert has_scope(["audit:*"], "audit:write")
    assert not has_scope(["audit:*"], "site:read")


def test_has_scope_rejects_invalid_required():
    assert not has_scope(["audit:read"], "audit")  # no colon
    assert not has_scope(["audit:read"], "")
