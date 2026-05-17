"""Unit tests for direct-answer rewrite (Phase B.1)."""

from __future__ import annotations

import json

import pytest

from app.services.llm_client import LLMUnavailableError
from app.services.rewrite.direct_answer import rewrite_direct_answer
from tests.conftest import FakeLLMClient


def _good_payload() -> str:
    return json.dumps(
        {
            "variants": [
                {
                    "style": "definition_first",
                    "text": "Python is a high-level programming language used for data science, web development, and scripting.",
                },
                {
                    "style": "cause_effect",
                    "text": "Python thrives because its readable syntax and large ecosystem cut development time for data and web work.",
                },
                {
                    "style": "outcome_first",
                    "text": "Python lets teams ship data pipelines and web apps faster than most alternatives thanks to its readable syntax and broad libraries.",
                },
            ]
        }
    )


def _bad_word_count_payload() -> str:
    long_text = "Python is " + ("very interesting " * 40)
    return json.dumps(
        {
            "variants": [
                {"style": "definition_first", "text": long_text.strip()},
                {"style": "cause_effect", "text": "Python wins because it is great."},
                {"style": "outcome_first", "text": "Python helps you ship things faster."},
            ]
        }
    )


def _hedged_payload() -> str:
    return json.dumps(
        {
            "variants": [
                {
                    "style": "definition_first",
                    "text": "Python is a programming language. It depends on the project whether you use it for web or data science workloads.",
                },
                {
                    "style": "cause_effect",
                    "text": "Python wins because its ecosystem is broad.",
                },
                {
                    "style": "outcome_first",
                    "text": "Python helps teams ship products faster.",
                },
            ]
        }
    )


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_happy_path_returns_three_passing_variants():
    fake = FakeLLMClient([_good_payload()])
    paragraph = " ".join(["word"] * 100)  # original fails Check A
    variants, model_id = await rewrite_direct_answer(paragraph, client=fake)
    assert len(variants) == 3
    assert {v.style for v in variants} == {
        "definition_first",
        "cause_effect",
        "outcome_first",
    }
    assert all(v.passes_check_a for v in variants)
    assert all(v.word_count <= 60 for v in variants)
    assert model_id == "fake-llm"
    assert len(fake.calls) == 1


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_retries_on_back_check_failure_then_succeeds():
    fake = FakeLLMClient([_bad_word_count_payload(), _good_payload()])
    variants, _ = await rewrite_direct_answer("paragraph", client=fake)
    assert all(v.passes_check_a for v in variants)
    assert len(fake.calls) == 2


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_exhausts_retries_raises_llm_unavailable():
    fake = FakeLLMClient([
        _bad_word_count_payload(),
        _hedged_payload(),
        _bad_word_count_payload(),
    ])
    with pytest.raises(LLMUnavailableError) as exc:
        await rewrite_direct_answer("paragraph", client=fake)
    assert "Check A back-validation failed" in exc.value.detail
    assert len(fake.calls) == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_empty_paragraph_raises_value_error():
    with pytest.raises(ValueError):
        await rewrite_direct_answer("   ", client=FakeLLMClient([]))


@pytest.mark.asyncio
async def test_schema_validation_failure_retries():
    bad_schema = json.dumps({"variants": [{"style": "definition_first", "text": "ok"}]})
    fake = FakeLLMClient([bad_schema, _good_payload()])
    variants, _ = await rewrite_direct_answer("paragraph", client=fake)
    assert len(variants) == 3
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_json_decode_failure_retries():
    fake = FakeLLMClient(["not json at all", _good_payload()])
    variants, _ = await rewrite_direct_answer("paragraph", client=fake)
    assert len(variants) == 3
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_llm_error_propagates_after_retries():
    fake = FakeLLMClient([
        LLMUnavailableError("first network err"),
        LLMUnavailableError("second network err"),
        LLMUnavailableError("third network err"),
    ])
    with pytest.raises(LLMUnavailableError) as exc:
        await rewrite_direct_answer("paragraph", client=fake)
    assert "third network err" in exc.value.detail


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_target_query_included_in_prompt_when_provided():
    fake = FakeLLMClient([_good_payload()])
    await rewrite_direct_answer(
        "paragraph", target_query="best Python tutorials for beginners", client=fake
    )
    system, user = fake.calls[0]
    assert "best Python tutorials for beginners" in user
