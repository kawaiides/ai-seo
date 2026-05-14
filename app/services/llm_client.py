"""LLM client abstraction.

Single `LLMClient` Protocol with one concrete `OpenAIClient` implementation.
Designed to be portable to a `GeminiClient` later by adding another class
that satisfies the Protocol — the fan-out engine never knows which provider
is in use.

The OpenAI call uses `response_format={"type":"json_object"}` (JSON mode).
This is belt-and-suspenders alongside the prompt's "output JSON only"
instructions; either one should produce JSON, but combined the failure
modes are dramatically narrowed.

`temperature` is intentionally not passed — gpt-5 / o-series reject it,
and gpt-4o-mini's default sampling produces enough diversity for our
sub-query generation use case.
"""

from __future__ import annotations

import os
from typing import Protocol

import openai


class LLMUnavailableError(Exception):
    """Raised when the LLM cannot produce a usable response.

    The `detail` string is what the API surfaces in the 503 envelope, so
    keep it informative (e.g. "JSONDecodeError on attempt 3", "missing
    OPENAI_API_KEY", "openai.RateLimitError after 3 attempts").
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LLMClient(Protocol):
    model_id: str

    async def generate_json(self, system: str, user: str) -> str:
        """Send the prompt and return the raw assistant text.

        Implementations should NOT parse the JSON — that's the engine's job
        (so the engine can apply its own fallback extraction + Pydantic
        validation in one place).
        """
        ...


class OpenAIClient:
    """OpenAI-backed implementation of `LLMClient`.

    Reads `OPENAI_API_KEY` and `OPENAI_MODEL` from the environment at
    construction time. Wraps SDK errors in `LLMUnavailableError` so the
    retry loop in `fanout_engine` only has to catch one exception type.
    """

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMUnavailableError("OPENAI_API_KEY is not set")
        self.model_id = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def generate_json(self, system: str, user: str) -> str:
        try:
            resp = await self._client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
        except openai.APIConnectionError as e:
            raise LLMUnavailableError(f"OpenAI connection error: {e}") from e
        except openai.RateLimitError as e:
            raise LLMUnavailableError(f"OpenAI rate limit: {e}") from e
        except openai.APIStatusError as e:
            raise LLMUnavailableError(
                f"OpenAI HTTP {e.status_code}: {e.message}"
            ) from e
        except openai.OpenAIError as e:
            raise LLMUnavailableError(f"OpenAI SDK error: {type(e).__name__}: {e}") from e

        content = resp.choices[0].message.content if resp.choices else None
        if not content:
            raise LLMUnavailableError("OpenAI returned empty content")
        return content


_client_singleton: OpenAIClient | None = None


def get_llm_client() -> LLMClient:
    """Return the process-wide singleton OpenAIClient.

    Raises `LLMUnavailableError` immediately if the env is not configured —
    callers can let it propagate to the 503 handler.
    """
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = OpenAIClient()
    return _client_singleton


def reset_client_singleton() -> None:
    """Test helper: drop the cached client so a fresh env can be picked up."""
    global _client_singleton
    _client_singleton = None
