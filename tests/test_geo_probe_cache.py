"""TTL-cache behaviour for GEO probes (Critical Decision #1).

Pure unit tests against the helper `_lookup_cached_probe` + a tiny
async stub of `AsyncSession` so the cache logic is exercised without
Postgres. The end-to-end behaviour through `probe_and_record` is covered
via FakeLLMClient-fed `OpenAIChatProbe`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.geo.citation_tracker import (
    DEFAULT_PROBE_TTL_DAYS,
    _lookup_cached_probe,
    probe_and_record,
)
from app.services.geo.probe import OpenAIChatProbe, ProbeResult
from tests.conftest import FakeLLMClient

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


@dataclass
class _Probe:
    provider: str
    model_name: str
    target_query: str
    cited_urls: tuple[str, ...]
    probed_at: datetime
    raw_response: str = ""

    async def probe(self, target_query: str, *, locale: str | None = None) -> ProbeResult:
        return ProbeResult(
            provider=self.provider,
            model_name=self.model_name,
            target_query=target_query,
            cited_urls=self.cited_urls,
            raw_response=self.raw_response,
            probed_at=self.probed_at,
        )


@dataclass
class _Record:
    id: int
    geo_query_id: int
    provider: str
    model_name: str
    cited_urls: list[str]
    contains_site: bool
    site_position: int | None
    probed_at: datetime
    raw_response: str | None = None


@dataclass
class _Query:
    id: int
    site_id: UUID
    target_query: str = "what is foo"
    locale: str = "en"
    last_probed_at: datetime | None = None
    last_contains_site: bool | None = None


@dataclass
class _Site:
    id: UUID
    root_url: str = "https://example.com"


class _FakeSession:
    """Minimal async session with just the methods citation_tracker uses."""

    def __init__(self, *, records: list[_Record], site: _Site):
        self._records = records
        self._site = site
        self._added: list[Any] = []
        self._next_id = max((r.id for r in records), default=0) + 1
        self.flush_calls = 0

    async def get(self, model, key):  # noqa: ARG002 — model used only for dispatch
        cls = getattr(model, "__name__", "")
        if cls == "Site":
            return self._site if self._site.id == key else None
        return None

    async def scalar(self, stmt):
        # Walk the records, apply the filters the helper builds.
        # Filters: geo_query_id == X, provider == X, model_name == X,
        # probed_at >= horizon. We extract these from the `whereclause`.
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql = str(compiled).lower()
        params = {}
        for token in sql.split():
            if "=" in token:
                k, _, v = token.partition("=")
                params[k.strip().rsplit(".", 1)[-1]] = v.strip().strip("'\"")
        # Simpler: just filter by the values stamped on the in-process records
        # and the most-recent probed_at within the implicit horizon. The
        # helper limits to 1 + orders desc, so we mimic.
        candidates = sorted(
            [
                r
                for r in self._records
                if r.geo_query_id == self._filter_geo_query_id
                and r.provider == self._filter_provider
                and r.model_name == self._filter_model_name
                and r.probed_at >= self._filter_horizon
            ],
            key=lambda r: r.probed_at,
            reverse=True,
        )
        return candidates[0] if candidates else None

    # The helper reads these instance attrs via the lookup call below.
    _filter_geo_query_id: int = 0
    _filter_provider: str = ""
    _filter_model_name: str = ""
    _filter_horizon: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def add(self, obj):
        # Translate ORM GEOProbeRecord into the in-memory _Record so the
        # next cache lookup against this fake session can find it.
        from app.db.models import GEOProbeRecord as _ORM

        if isinstance(obj, _ORM):
            translated = _Record(
                id=self._next_id,
                geo_query_id=obj.geo_query_id,
                provider=obj.provider,
                model_name=obj.model_name,
                cited_urls=list(obj.cited_urls or ()),
                contains_site=obj.contains_site,
                site_position=obj.site_position,
                probed_at=obj.probed_at,
                raw_response=obj.raw_response,
            )
            obj.id = self._next_id
            self._next_id += 1
            self._records.append(translated)
        elif isinstance(obj, _Record):
            obj.id = self._next_id
            self._next_id += 1
            self._records.append(obj)
        self._added.append(obj)

    async def flush(self):
        self.flush_calls += 1


def _seed_records(rows: list[tuple[int, str, str, datetime, bool]], geo_query_id: int) -> list[_Record]:
    out: list[_Record] = []
    for i, (gid, provider, model, when, contains) in enumerate(rows, start=1):
        out.append(
            _Record(
                id=i,
                geo_query_id=gid,
                provider=provider,
                model_name=model,
                cited_urls=["https://example.com/cached"] if contains else ["https://other.test/x"],
                contains_site=contains,
                site_position=1 if contains else None,
                probed_at=when,
            )
        )
    return out


# -------------------- _lookup_cached_probe primitive --------------------


@pytest.mark.asyncio
async def test_lookup_returns_none_when_cold():
    site_id = uuid4()
    session = _FakeSession(records=[], site=_Site(id=site_id))
    geo_query = _Query(id=42, site_id=site_id)
    probe = _Probe("openai", "gpt-4o-mini", "q", (), NOW)
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "gpt-4o-mini"
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    cached = await _lookup_cached_probe(
        session,  # type: ignore[arg-type]
        geo_query=geo_query,
        probe=probe,
        ttl_days=DEFAULT_PROBE_TTL_DAYS,
        now=NOW,
    )
    assert cached is None


@pytest.mark.asyncio
async def test_lookup_returns_recent_record_within_ttl():
    site_id = uuid4()
    records = _seed_records(
        [(42, "openai", "gpt-4o-mini", NOW - timedelta(days=2), True)], geo_query_id=42
    )
    session = _FakeSession(records=records, site=_Site(id=site_id))
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "gpt-4o-mini"
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    geo_query = _Query(id=42, site_id=site_id)
    probe = _Probe("openai", "gpt-4o-mini", "q", (), NOW)
    cached = await _lookup_cached_probe(
        session,  # type: ignore[arg-type]
        geo_query=geo_query,
        probe=probe,
        ttl_days=DEFAULT_PROBE_TTL_DAYS,
        now=NOW,
    )
    assert cached is not None
    assert cached.id == 1


@pytest.mark.asyncio
async def test_lookup_ignores_expired_record():
    site_id = uuid4()
    records = _seed_records(
        [(42, "openai", "gpt-4o-mini", NOW - timedelta(days=14), True)], geo_query_id=42
    )
    session = _FakeSession(records=records, site=_Site(id=site_id))
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "gpt-4o-mini"
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    geo_query = _Query(id=42, site_id=site_id)
    probe = _Probe("openai", "gpt-4o-mini", "q", (), NOW)
    cached = await _lookup_cached_probe(
        session,  # type: ignore[arg-type]
        geo_query=geo_query,
        probe=probe,
        ttl_days=DEFAULT_PROBE_TTL_DAYS,
        now=NOW,
    )
    assert cached is None


@pytest.mark.asyncio
async def test_lookup_distinguishes_model_name():
    site_id = uuid4()
    records = _seed_records(
        [(42, "openai", "gpt-old-model", NOW - timedelta(days=1), True)], geo_query_id=42
    )
    session = _FakeSession(records=records, site=_Site(id=site_id))
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "gpt-4o-mini"  # different model
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    geo_query = _Query(id=42, site_id=site_id)
    probe = _Probe("openai", "gpt-4o-mini", "q", (), NOW)
    cached = await _lookup_cached_probe(
        session,  # type: ignore[arg-type]
        geo_query=geo_query,
        probe=probe,
        ttl_days=DEFAULT_PROBE_TTL_DAYS,
        now=NOW,
    )
    assert cached is None


# -------------------- probe_and_record end-to-end --------------------


@pytest.mark.asyncio
async def test_probe_and_record_returns_cache_hit_without_calling_probe():
    site_id = uuid4()
    cached_at = NOW - timedelta(hours=4)
    records = _seed_records(
        [(42, "openai", "gpt-4o-mini", cached_at, True)], geo_query_id=42
    )
    session = _FakeSession(records=records, site=_Site(id=site_id))
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "gpt-4o-mini"
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    geo_query = _Query(id=42, site_id=site_id)

    class _ExplodingProbe(_Probe):
        async def probe(self, *_a, **_k):  # noqa: ANN002
            raise AssertionError("cache miss; live probe should not have been called")

    probe = _ExplodingProbe("openai", "gpt-4o-mini", "q", (), NOW)
    outcome = await probe_and_record(session, geo_query, probe, now=NOW)  # type: ignore[arg-type]
    assert outcome.from_cache is True
    assert outcome.geo_probe_id == 1
    assert outcome.cached_age_seconds == int((NOW - cached_at).total_seconds())


@pytest.mark.asyncio
async def test_probe_and_record_force_refresh_skips_cache():
    site_id = uuid4()
    cached_at = NOW - timedelta(hours=4)
    records = _seed_records(
        [(42, "openai", "gpt-4o-mini", cached_at, False)], geo_query_id=42
    )
    session = _FakeSession(records=records, site=_Site(id=site_id))
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "gpt-4o-mini"
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    geo_query = _Query(id=42, site_id=site_id)

    probe = _Probe(
        "openai", "gpt-4o-mini", "q",
        ("https://example.com/fresh",), NOW,
    )
    outcome = await probe_and_record(
        session, geo_query, probe, force_refresh=True, now=NOW  # type: ignore[arg-type]
    )
    assert outcome.from_cache is False
    assert outcome.cached_age_seconds is None
    assert outcome.cited_urls == ("https://example.com/fresh",)


@pytest.mark.asyncio
async def test_probe_and_record_ttl_zero_disables_cache():
    site_id = uuid4()
    records = _seed_records(
        [(42, "openai", "gpt-4o-mini", NOW - timedelta(hours=1), True)], geo_query_id=42
    )
    session = _FakeSession(records=records, site=_Site(id=site_id))
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "gpt-4o-mini"
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    geo_query = _Query(id=42, site_id=site_id)
    probe = _Probe(
        "openai", "gpt-4o-mini", "q",
        ("https://example.com/fresh",), NOW,
    )
    outcome = await probe_and_record(
        session, geo_query, probe, ttl_days=0, now=NOW  # type: ignore[arg-type]
    )
    assert outcome.from_cache is False


@pytest.mark.asyncio
async def test_openai_probe_wrapped_in_probe_and_record_caches_second_call():
    """Two back-to-back calls with the same FakeLLMClient — second one
    must hit cache. If it didn't, FakeLLMClient would raise
    `ran out of queued responses`."""
    site_id = uuid4()
    session = _FakeSession(records=[], site=_Site(id=site_id))
    # Set filter values so the lookup matches what `probe_and_record` searches for.
    session._filter_geo_query_id = 42
    session._filter_provider = "openai"
    session._filter_model_name = "fake-llm"
    session._filter_horizon = NOW - timedelta(days=DEFAULT_PROBE_TTL_DAYS)
    geo_query = _Query(id=42, site_id=site_id)
    fake = FakeLLMClient([
        json.dumps({"urls": ["https://example.com/x"]})
    ])
    probe = OpenAIChatProbe(client=fake)

    first = await probe_and_record(session, geo_query, probe, now=NOW)  # type: ignore[arg-type]
    second = await probe_and_record(session, geo_query, probe, now=NOW)  # type: ignore[arg-type]
    assert first.from_cache is False
    assert second.from_cache is True
    assert len(fake.calls) == 1  # only one LLM call total
