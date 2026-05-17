"""Unit tests for GEO probe (Phase C.2)."""

from __future__ import annotations

import json

import pytest

from app.services.geo.probe import (
    OpenAIChatProbe,
    _safe_extract_urls,
)
from app.services.llm_client import LLMUnavailableError
from tests.conftest import FakeLLMClient


def test_strict_json_extraction_returns_unique_urls():
    raw = json.dumps(
        {
            "urls": [
                "https://en.wikipedia.org/wiki/Foo",
                "https://nih.gov/x",
                "https://en.wikipedia.org/wiki/Foo",  # dup
            ]
        }
    )
    urls = _safe_extract_urls(raw, max_urls=10)
    assert urls == ["https://en.wikipedia.org/wiki/Foo", "https://nih.gov/x"]


def test_regex_fallback_extracts_when_json_malformed():
    raw = (
        "I would cite https://en.wikipedia.org/wiki/Foo, https://nih.gov/x. "
        "Also see https://en.wikipedia.org/wiki/Foo for the same topic."
    )
    urls = _safe_extract_urls(raw, max_urls=10)
    assert "https://en.wikipedia.org/wiki/Foo" in urls
    assert "https://nih.gov/x" in urls


def test_regex_fallback_strips_trailing_punctuation():
    raw = "See https://example.com/page).  And https://another.com,"
    urls = _safe_extract_urls(raw, max_urls=10)
    assert "https://example.com/page" in urls
    assert "https://another.com" in urls


def test_no_urls_returns_empty():
    urls = _safe_extract_urls("nothing here", max_urls=10)
    assert urls == []


@pytest.mark.asyncio
async def test_openai_chat_probe_happy_path():
    fake = FakeLLMClient(
        [json.dumps({"urls": ["https://en.wikipedia.org/wiki/Foo", "https://nih.gov/x"]})]
    )
    probe = OpenAIChatProbe(client=fake)
    result = await probe.probe("what is foo")
    assert result.provider == "openai"
    assert result.model_name == "fake-llm"
    assert result.cited_urls == (
        "https://en.wikipedia.org/wiki/Foo",
        "https://nih.gov/x",
    )
    assert result.target_query == "what is foo"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_probe_retries_when_no_urls_extracted():
    fake = FakeLLMClient(
        [
            "definitely not JSON and zero URLs",  # nothing usable → retry
            json.dumps({"urls": ["https://en.wikipedia.org/wiki/Foo"]}),
        ]
    )
    probe = OpenAIChatProbe(client=fake)
    result = await probe.probe("foo")
    assert result.cited_urls == ("https://en.wikipedia.org/wiki/Foo",)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_probe_raises_after_max_retries():
    fake = FakeLLMClient(["no urls here"] * 3)
    probe = OpenAIChatProbe(client=fake)
    with pytest.raises(LLMUnavailableError):
        await probe.probe("foo")
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_probe_empty_query_raises_value_error():
    probe = OpenAIChatProbe(client=FakeLLMClient([]))
    with pytest.raises(ValueError):
        await probe.probe("  ")


@pytest.mark.asyncio
async def test_probe_propagates_llm_unavailable_after_retries():
    fake = FakeLLMClient(
        [
            LLMUnavailableError("err 1"),
            LLMUnavailableError("err 2"),
            LLMUnavailableError("err 3"),
        ]
    )
    probe = OpenAIChatProbe(client=fake)
    with pytest.raises(LLMUnavailableError) as exc:
        await probe.probe("foo")
    assert "err 3" in exc.value.detail
