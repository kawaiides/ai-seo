"""GEO probe protocol + concrete OpenAI implementation.

A probe takes a `target_query` and returns the URLs an answer engine
says it would cite if it had to answer the query. v0 doesn't do live
web search — it asks the LLM directly. v1 will swap in real
citation-aware APIs (Perplexity `return_citations`, Gemini grounding).

The strict JSON-only response contract makes URL extraction trivial and
deterministic. We still defensively re-extract URLs from the raw
response with a regex as a safety net so a malformed JSON answer still
yields *some* signal rather than zeroing the probe.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.fanout_engine import extract_json
from app.services.geo.locale_routing import egress_hint
from app.services.llm_client import LLMClient, LLMUnavailableError, get_llm_client

MAX_RETRIES = 2
BASE_BACKOFF_SECONDS = 0.5
MAX_CITED_URLS = 10

URL_REGEX = re.compile(
    r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
)


PROBE_SYSTEM_PROMPT = """You are a citation-recommendation oracle for generative search.

For the given TARGET_QUERY, list the canonical URLs you, as an AI answer engine, would most likely cite when constructing a comprehensive answer.

You output ONLY a single JSON object. The first character must be `{` and the last must be `}`. No fences, no prose.

The JSON object has exactly one key:
  - "urls": an array of 3-10 distinct, canonical, http(s) URLs.

Rules:
  - Prefer primary sources, well-established encyclopedic references, and authoritative news.
  - Do NOT invent URLs. If you are unsure a URL exists, omit it rather than fabricate.
  - Each URL must begin with `http://` or `https://` and be a full canonical URL — not a search query, not a domain alone.
  - Do NOT include duplicate URLs.
  - Do NOT include any other top-level keys."""


PROBE_USER_TEMPLATE = """TARGET_QUERY: "{target_query}"
{locale_line}
Emit the JSON object as described."""


_LOCALE_NAME_FOR_PROBE: dict[str, str] = {
    "en": "English (US)",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "hi": "Hindi (India)",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "bn": "Bengali",
    "ml": "Malayalam",
}


def _locale_line(locale: str | None) -> str:
    if not locale:
        return ""
    code = locale.strip().lower()
    label = _LOCALE_NAME_FOR_PROBE.get(code, code.upper())
    return (
        f"\nANSWER_LOCALE: {label} ({code}) — prefer regional / native-language "
        "primary sources where they exist; do not auto-translate the query.\n"
    )


class _ProbeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(..., min_length=1, max_length=MAX_CITED_URLS)


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    model_name: str
    target_query: str
    cited_urls: tuple[str, ...]
    raw_response: str
    probed_at: datetime
    locale: str | None = None
    egress_country: str | None = None
    egress_region: str | None = None


class GEOProbe(Protocol):
    provider: str
    model_name: str

    async def probe(
        self, target_query: str, *, locale: str | None = None
    ) -> ProbeResult: ...


class OpenAIChatProbe:
    """Asks an OpenAI chat model for the URLs it would cite.

    Wraps the existing `LLMClient` Protocol so a `FakeLLMClient` can be
    injected in tests.
    """

    provider: str = "openai"

    def __init__(
        self,
        *,
        client: LLMClient | None = None,
        max_urls: int = MAX_CITED_URLS,
    ) -> None:
        self._client = client or get_llm_client()
        self._max_urls = max_urls
        self.model_name = self._client.model_id

    async def probe(
        self, target_query: str, *, locale: str | None = None
    ) -> ProbeResult:
        if not target_query or not target_query.strip():
            raise ValueError("target_query must not be empty")

        user_prompt = PROBE_USER_TEMPLATE.format(
            target_query=target_query.replace('"', '\\"'),
            locale_line=_locale_line(locale),
        )
        last_detail = "no attempts made"
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                raw = await self._client.generate_json(PROBE_SYSTEM_PROMPT, user_prompt)
            except LLMUnavailableError as e:
                last_detail = f"{e.detail} (attempt {attempt})"
                if attempt < MAX_RETRIES + 1:
                    await _backoff(attempt)
                    continue
                raise
            urls = _safe_extract_urls(raw, max_urls=self._max_urls)
            if urls:
                hint = egress_hint(locale)
                return ProbeResult(
                    provider=self.provider,
                    model_name=self.model_name,
                    target_query=target_query,
                    cited_urls=tuple(urls),
                    raw_response=raw,
                    probed_at=datetime.now(tz=timezone.utc),
                    locale=(locale.strip().lower() if locale else None),
                    egress_country=hint.country if hint else None,
                    egress_region=hint.region_header if hint else None,
                )
            last_detail = (
                f"no URLs extracted on attempt {attempt}; raw={raw[:200]!r}"
            )
            if attempt < MAX_RETRIES + 1:
                await _backoff(attempt)
        raise LLMUnavailableError(last_detail)


def _safe_extract_urls(raw: str, *, max_urls: int) -> list[str]:
    """Strict JSON parse first, regex fallback if that fails."""
    try:
        parsed = extract_json(raw)
        validated = _ProbeResponse.model_validate(parsed)
        seen: set[str] = set()
        out: list[str] = []
        for u in validated.urls:
            u = u.strip()
            if not URL_REGEX.fullmatch(u):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
            if len(out) >= max_urls:
                break
        if out:
            return out
    except (json.JSONDecodeError, ValidationError):
        pass

    # Regex fallback over the entire raw response.
    found = URL_REGEX.findall(raw or "")
    cleaned: list[str] = []
    seen2: set[str] = set()
    for u in found:
        u = u.rstrip(".,;:)\"]'")
        if u in seen2:
            continue
        seen2.add(u)
        cleaned.append(u)
        if len(cleaned) >= max_urls:
            break
    return cleaned


async def _backoff(attempt: int) -> None:
    delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
    jitter = delay * random.uniform(-0.2, 0.2)
    await asyncio.sleep(max(0.0, delay + jitter))
