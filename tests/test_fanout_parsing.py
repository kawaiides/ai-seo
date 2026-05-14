"""Unit tests for Feature 2 — Query Fan-Out Engine.

Test groups:
  1. extract_json — parser robustness against fences / leading prose
  2. LLMFanoutResponse — strict schema validation
  3. generate_fanout — retry loop with FakeLLMClient
  4. gap_analyzer + endpoint integration
"""

from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.schemas import LLMFanoutResponse
from app.services import fanout_engine
from app.services.content_parser import ContentParseError
from app.services.fanout_engine import (
    extract_json,
    generate_fanout,
    run_fanout,
)
from app.services.gap_analyzer import (
    THRESHOLD,
    build_gap_summary,
    chunk_content,
    score_subqueries,
)
from app.services.llm_client import LLMUnavailableError


# ---------------------------------------------------------------------------
# Group 1 — extract_json
# ---------------------------------------------------------------------------

def test_extract_json_valid_directly():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fence():
    raw = '```json\n{"a": 1, "b": [2, 3]}\n```'
    assert extract_json(raw) == {"a": 1, "b": [2, 3]}


def test_extract_json_strips_plain_fence():
    raw = '```\n{"a": 1}\n```'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_handles_leading_prose_via_brace_slice():
    raw = 'Sure, here you go:\n{"a": 1}\nHope this helps.'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_unparseable_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("not json at all, no braces")


def test_extract_json_empty_raises():
    with pytest.raises(json.JSONDecodeError):
        extract_json("   ")


# ---------------------------------------------------------------------------
# Group 2 — LLMFanoutResponse schema validation
# ---------------------------------------------------------------------------

def _payload() -> dict:
    """Canonical valid payload as a dict, suitable for mutation in tests."""
    return {
        "target_query": "x",
        "sub_queries": [
            {"type": "comparative", "query": "a vs b"},
            {"type": "comparative", "query": "c vs d"},
            {"type": "feature_specific", "query": "feature one detail here"},
            {"type": "feature_specific", "query": "feature two detail here"},
            {"type": "use_case", "query": "use case one example here"},
            {"type": "use_case", "query": "use case two example here"},
            {"type": "trust_signals", "query": "trust review one item"},
            {"type": "trust_signals", "query": "trust review two item"},
            {"type": "how_to", "query": "how to do one thing"},
            {"type": "how_to", "query": "how to do another thing"},
            {"type": "definitional", "query": "what is one thing"},
            {"type": "definitional", "query": "what is another thing"},
        ],
    }


def test_valid_payload_accepts():
    obj = LLMFanoutResponse.model_validate(_payload())
    assert len(obj.sub_queries) == 12


def test_extra_top_level_field_rejected():
    p = _payload()
    p["explanation"] = "I added this"
    with pytest.raises(ValidationError):
        LLMFanoutResponse.model_validate(p)


def test_extra_subquery_field_rejected():
    p = _payload()
    p["sub_queries"][0]["id"] = 1
    with pytest.raises(ValidationError):
        LLMFanoutResponse.model_validate(p)


def test_invalid_type_value_rejected():
    p = _payload()
    p["sub_queries"][0]["type"] = "comparison"  # typo
    with pytest.raises(ValidationError):
        LLMFanoutResponse.model_validate(p)


def test_only_9_subqueries_rejected():
    p = _payload()
    p["sub_queries"] = p["sub_queries"][:9]
    with pytest.raises(ValidationError):
        LLMFanoutResponse.model_validate(p)


def test_one_type_with_one_subquery_rejected():
    p = _payload()
    # Drop one of the two how_to entries; total drops to 11 but type
    # how_to falls below 2.
    p["sub_queries"] = [sq for sq in p["sub_queries"] if sq["query"] != "how to do another thing"]
    with pytest.raises(ValidationError):
        LLMFanoutResponse.model_validate(p)


def test_duplicate_query_strings_rejected():
    p = _payload()
    p["sub_queries"][1]["query"] = p["sub_queries"][0]["query"]
    with pytest.raises(ValidationError):
        LLMFanoutResponse.model_validate(p)


def test_too_many_subqueries_rejected():
    p = _payload()
    extras = [
        {"type": "comparative", "query": f"extra one {i}"} for i in range(5)
    ]
    p["sub_queries"] = p["sub_queries"] + extras
    with pytest.raises(ValidationError):
        LLMFanoutResponse.model_validate(p)


# ---------------------------------------------------------------------------
# Group 3 — generate_fanout retry loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_attempt_succeeds(fake_llm, valid_payload_str):
    fake_llm.queue(valid_payload_str)
    response, model_id = await generate_fanout("x", client=fake_llm)
    assert len(response.sub_queries) == 12
    assert model_id == "fake-llm"
    assert len(fake_llm.calls) == 1


@pytest.mark.asyncio
async def test_first_attempt_bad_then_second_attempt_good(
    fake_llm, valid_payload_str
):
    fake_llm.queue("not valid json", valid_payload_str)
    response, model_id = await generate_fanout("x", client=fake_llm)
    assert len(response.sub_queries) == 12
    assert len(fake_llm.calls) == 2


@pytest.mark.asyncio
async def test_three_failures_raise_503_error(fake_llm):
    fake_llm.queue("not json 1", "not json 2", "not json 3")
    with pytest.raises(LLMUnavailableError) as exc_info:
        await generate_fanout("x", client=fake_llm)
    assert "JSONDecodeError" in exc_info.value.detail
    assert len(fake_llm.calls) == 3


@pytest.mark.asyncio
async def test_schema_validation_failure_retries_then_503(fake_llm):
    # Returns valid JSON with only 9 sub-queries → Pydantic validation fails.
    bad = deepcopy(_payload())
    bad["sub_queries"] = bad["sub_queries"][:9]
    bad_json = json.dumps(bad)
    fake_llm.queue(bad_json, bad_json, bad_json)
    with pytest.raises(LLMUnavailableError) as exc_info:
        await generate_fanout("x", client=fake_llm)
    assert "Schema validation failed" in exc_info.value.detail
    assert len(fake_llm.calls) == 3


@pytest.mark.asyncio
async def test_network_error_wraps_to_llm_unavailable(fake_llm):
    network_err = LLMUnavailableError("OpenAI connection error: timeout")
    fake_llm.queue(network_err, network_err, network_err)
    with pytest.raises(LLMUnavailableError) as exc_info:
        await generate_fanout("x", client=fake_llm)
    assert "connection error" in exc_info.value.detail
    assert len(fake_llm.calls) == 3


@pytest.mark.asyncio
async def test_extra_field_retried_once_then_succeeds(
    fake_llm, valid_payload_str
):
    bad = deepcopy(_payload())
    bad["explanation"] = "I added this"
    fake_llm.queue(json.dumps(bad), valid_payload_str)
    response, _ = await generate_fanout("x", client=fake_llm)
    assert len(response.sub_queries) == 12


@pytest.mark.asyncio
async def test_markdown_fenced_response_parsed(fake_llm, valid_payload_str):
    fenced = f"```json\n{valid_payload_str}\n```"
    fake_llm.queue(fenced)
    response, _ = await generate_fanout("x", client=fake_llm)
    assert len(response.sub_queries) == 12


# Speed up the retry loop in tests by zeroing the backoff.
@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(fanout_engine, "BASE_BACKOFF_SECONDS", 0.0)


# ---------------------------------------------------------------------------
# Group 4 — Gap analyzer
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("nlp")
def test_chunk_content_filters_short_sentences():
    text = "Yes. Hi. This is a longer sentence with substance and meaning."
    chunks = chunk_content(text)
    assert len(chunks) == 1
    assert "longer sentence" in chunks[0]


@pytest.mark.usefixtures("nlp")
def test_chunk_content_caps_at_max_chunks():
    text = " ".join(
        f"This is sample sentence number {i} with several words." for i in range(600)
    )
    chunks = chunk_content(text, max_chunks=500)
    assert len(chunks) == 500


@pytest.mark.usefixtures("nlp")
def test_chunk_content_empty_raises_parse_error():
    with pytest.raises(ContentParseError):
        chunk_content("Hi. Yes.")


@pytest.mark.usefixtures("embedder", "nlp")
def test_score_subqueries_marks_obvious_overlap_covered():
    from app.models.schemas import LLMSubQuery

    content = (
        "Jasper AI is a popular AI writing tool used for SEO. "
        "It includes built-in keyword clustering and SERP analysis features. "
        "Many marketing agencies have adopted it for content workflows."
    )
    # Near-verbatim query — chosen deliberately so the test is robust
    # against MiniLM's somewhat conservative similarity scores. Real
    # tuning observations are documented in SETUP.md.
    sub_queries = [
        LLMSubQuery(type="feature_specific", query="keyword clustering and SERP analysis features"),
        LLMSubQuery(type="comparative", query="quantum entanglement in particle physics"),
    ]
    scored = score_subqueries(sub_queries, content)
    assert len(scored) == 2
    assert scored[0][0] is True, f"expected covered, got similarity {scored[0][1]}"
    assert scored[0][1] >= THRESHOLD
    assert scored[1][0] is False  # not covered (disjoint topic)
    assert scored[1][1] < THRESHOLD


def test_build_gap_summary_correct():
    from app.models.schemas import SubQueryResponse

    scored = [
        SubQueryResponse(type="comparative", query="a", covered=True, similarity_score=0.9),
        SubQueryResponse(type="comparative", query="b", covered=False, similarity_score=0.4),
        SubQueryResponse(type="feature_specific", query="c", covered=True, similarity_score=0.85),
        SubQueryResponse(type="how_to", query="d", covered=False, similarity_score=0.3),
        SubQueryResponse(type="how_to", query="e", covered=False, similarity_score=0.2),
    ]
    summary = build_gap_summary(scored)
    assert summary.covered == 2
    assert summary.total == 5
    assert summary.coverage_percent == 40
    assert summary.covered_types == ["comparative", "feature_specific"]
    assert summary.missing_types == ["how_to"]


# ---------------------------------------------------------------------------
# Endpoint integration tests (FastAPI TestClient + mocked LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def app_with_fake_llm(fake_llm, monkeypatch):
    """FastAPI app with the fanout engine wired to use FakeLLMClient.

    We patch `get_llm_client` (used by `generate_fanout` when no client
    is passed explicitly) so the endpoint exercises the same path it
    would in production.
    """
    from app.services import llm_client

    monkeypatch.setattr(llm_client, "get_llm_client", lambda: fake_llm)
    monkeypatch.setattr(fanout_engine, "get_llm_client", lambda: fake_llm)
    fanout_engine.reset_cache()

    from app.main import app

    return app, fake_llm


def test_endpoint_no_content_omits_gap_fields(app_with_fake_llm, valid_payload_str):
    app, fake_llm = app_with_fake_llm
    fake_llm.queue(valid_payload_str)
    with TestClient(app) as client:
        resp = client.post(
            "/api/fanout/generate",
            json={"target_query": "best AI writing tool for SEO"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_query"] == "best AI writing tool for SEO"
    assert body["model_used"] == "fake-llm"
    assert body["total_sub_queries"] == 12
    assert "gap_summary" not in body
    for sq in body["sub_queries"]:
        assert "covered" not in sq
        assert "similarity_score" not in sq


@pytest.mark.usefixtures("embedder", "nlp")
def test_endpoint_with_content_includes_gap_fields(
    app_with_fake_llm, valid_payload_str
):
    app, fake_llm = app_with_fake_llm
    fake_llm.queue(valid_payload_str)
    content = (
        "Jasper AI is a popular AI writing tool used for SEO. "
        "It includes built-in keyword clustering and SERP analysis. "
        "Marketing agencies have adopted it widely for content production. "
        "Solo bloggers also use it to draft long-form posts quickly."
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/fanout/generate",
            json={
                "target_query": "best AI writing tool for SEO",
                "existing_content": content,
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "gap_summary" in body
    summary = body["gap_summary"]
    assert summary["total"] == 12
    assert "coverage_percent" in summary
    for sq in body["sub_queries"]:
        assert isinstance(sq["covered"], bool)
        assert isinstance(sq["similarity_score"], float)


def test_endpoint_503_envelope_shape(app_with_fake_llm):
    app, fake_llm = app_with_fake_llm
    fake_llm.queue("not json", "still not json", "nope")
    with TestClient(app) as client:
        resp = client.post(
            "/api/fanout/generate",
            json={"target_query": "x"},
        )
    assert resp.status_code == 503
    body = resp.json()
    assert set(body.keys()) == {"error", "message", "detail"}
    assert body["error"] == "llm_unavailable"
    assert "3 retries" in body["message"]
    assert "JSONDecodeError" in body["detail"]


def test_endpoint_validation_error_for_empty_query(app_with_fake_llm):
    app, _ = app_with_fake_llm
    with TestClient(app) as client:
        resp = client.post("/api/fanout/generate", json={"target_query": "   "})
    # Pydantic validation → default 422, not our 503 envelope.
    assert resp.status_code == 422


def test_endpoint_network_failure_503(app_with_fake_llm):
    app, fake_llm = app_with_fake_llm
    err = LLMUnavailableError("OpenAI connection error: simulated")
    fake_llm.queue(err, err, err)
    with TestClient(app) as client:
        resp = client.post("/api/fanout/generate", json={"target_query": "x"})
    assert resp.status_code == 503
    assert "connection error" in resp.json()["detail"]
