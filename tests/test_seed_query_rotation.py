"""Unit tests for `app.autopilot.seed_queries`.

The DB-touching `pick_next_seed` wrapper is exercised via the pure
`_pick_pure` helper, which makes the rotation logic testable without
spinning up Postgres.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.autopilot import seed_queries
from app.autopilot.seed_queries import (
    SEED_QUERIES,
    SeedQuery,
    _pick_pure,
    by_slug,
    enabled_seeds,
)


def _t(offset_minutes: int) -> datetime:
    return datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc) + timedelta(
        minutes=offset_minutes
    )


def test_catalog_has_us_and_in_locales():
    locales = {s.locale for s in SEED_QUERIES}
    assert "en-US" in locales
    assert "en-IN" in locales


def test_catalog_slugs_are_unique():
    slugs = [s.slug for s in SEED_QUERIES]
    assert len(slugs) == len(set(slugs))


def test_by_slug_lookup():
    assert by_slug("ai-seo-platform-us") is not None
    assert by_slug("does-not-exist") is None


def test_enabled_seeds_filters_out_disabled():
    seeds = (
        SeedQuery("a", "a", "v", "en-US", enabled=True),
        SeedQuery("b", "b", "v", "en-US", enabled=False),
    )
    out = _pick_pure(seeds, usage={})
    assert out is not None
    assert out.slug == "a"
    # And the public helper filters the real catalog the same way.
    assert all(s.enabled for s in enabled_seeds())


def test_fresh_table_picks_highest_weight_first():
    seeds = (
        SeedQuery("lo", "lo", "v", "en-US", weight=1),
        SeedQuery("hi", "hi", "v", "en-US", weight=5),
        SeedQuery("mid", "mid", "v", "en-US", weight=3),
    )
    out = _pick_pure(seeds, usage={})
    assert out is not None
    assert out.slug == "hi"


def test_used_seeds_lose_to_unused_regardless_of_weight():
    seeds = (
        SeedQuery("used_hi", "u", "v", "en-US", weight=10),
        SeedQuery("unused_lo", "u2", "v", "en-US", weight=1),
    )
    out = _pick_pure(seeds, usage={"used_hi": _t(0)})
    assert out is not None
    assert out.slug == "unused_lo"


def test_oldest_used_wins_when_all_seen():
    seeds = (
        SeedQuery("a", "a", "v", "en-US", weight=1),
        SeedQuery("b", "b", "v", "en-US", weight=1),
        SeedQuery("c", "c", "v", "en-US", weight=1),
    )
    usage = {"a": _t(100), "b": _t(0), "c": _t(50)}  # b is oldest
    out = _pick_pure(seeds, usage=usage)
    assert out is not None
    assert out.slug == "b"


def test_weight_breaks_tie_on_same_timestamp():
    seeds = (
        SeedQuery("low", "l", "v", "en-US", weight=1),
        SeedQuery("high", "h", "v", "en-US", weight=5),
    )
    usage = {"low": _t(0), "high": _t(0)}
    out = _pick_pure(seeds, usage=usage)
    assert out is not None
    assert out.slug == "high"


def test_empty_catalog_returns_none():
    assert _pick_pure((), usage={}) is None


def test_all_disabled_returns_none():
    seeds = (
        SeedQuery("a", "a", "v", "en-US", enabled=False),
        SeedQuery("b", "b", "v", "en-US", enabled=False),
    )
    assert _pick_pure(seeds, usage={}) is None
