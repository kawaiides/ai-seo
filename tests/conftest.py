"""Shared pytest fixtures."""

from __future__ import annotations

import json
import os
import sys
from collections import deque
from typing import Any

import pytest
from dotenv import load_dotenv

# Make `app.*` imports work when running pytest from the repo root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Load .env so opt-in integration tests can hit OpenAI. Real unit tests
# never use the live key; they use FakeLLMClient.
load_dotenv(override=False)

# Disable the public-page rate limiter for the test suite. TestClient
# reuses the IP "testclient", so the daily counter would accumulate across
# tests and trip 429s in unrelated assertions.
os.environ.setdefault("AEGIS_DISABLE_RATE_LIMIT", "1")


@pytest.fixture(scope="session")
def nlp():
    """Session-scoped spaCy model so we only pay the load cost once."""
    from app.services.nlp import get_nlp

    return get_nlp()


@pytest.fixture(scope="session")
def embedder():
    """Session-scoped sentence-transformer for gap-analyzer tests."""
    from app.services.embeddings import get_embedder

    return get_embedder()


@pytest.fixture
def parsed_factory():
    """Build a `ParsedContent` from raw HTML or plain text via the real parser."""
    from app.services.content_parser import parse

    def _build(raw: str, input_type: str = "text"):
        return parse(raw, input_type=input_type)

    return _build


class FakeLLMClient:
    """Test double for `LLMClient`.

    Configure with a queue of responses (strings or exception instances).
    Each call to `generate_json` pops the next item; if it's an Exception
    instance it gets raised, otherwise it's returned as the raw text.
    """

    def __init__(self, responses: list[Any] | None = None, model_id: str = "fake-llm"):
        self.model_id = model_id
        self._queue: deque[Any] = deque(responses or [])
        self.calls: list[tuple[str, str]] = []

    def queue(self, *items: Any) -> "FakeLLMClient":
        for item in items:
            self._queue.append(item)
        return self

    async def generate_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._queue:
            raise AssertionError("FakeLLMClient ran out of queued responses")
        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake_llm():
    return FakeLLMClient()


@pytest.fixture
def valid_payload_str():
    """A canonical valid LLM JSON payload for tests that just need
    'something the parser will accept'."""
    payload = {
        "target_query": "best AI writing tool for SEO",
        "sub_queries": [
            {"type": "comparative", "query": "Jasper vs Surfer SEO for content optimization"},
            {"type": "comparative", "query": "Clearscope vs MarketMuse for SEO writing"},
            {"type": "feature_specific", "query": "AI writing tool with real-time SERP analysis"},
            {"type": "feature_specific", "query": "AI writer with built-in keyword clustering"},
            {"type": "use_case", "query": "AI writing tool for agency content production at scale"},
            {"type": "use_case", "query": "AI writer for solo bloggers in niche markets"},
            {"type": "trust_signals", "query": "AI SEO writing tool reviews from marketing agencies 2025"},
            {"type": "trust_signals", "query": "Jasper case studies for ranking organic traffic"},
            {"type": "how_to", "query": "how to use AI to optimize blog content for SEO"},
            {"type": "how_to", "query": "how to combine AI drafting with human editing for SEO"},
            {"type": "definitional", "query": "what is AI assisted SEO content writing"},
            {"type": "definitional", "query": "what makes an AI writer suitable for SEO work"},
        ],
    }
    return json.dumps(payload)
