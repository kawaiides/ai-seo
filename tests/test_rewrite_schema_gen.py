"""Unit tests for schema_gen (Phase B.2)."""

from __future__ import annotations

import json

import pytest

from app.services.content_parser import parse
from app.services.llm_client import LLMUnavailableError
from app.services.rewrite.schema_gen import detect_intent, generate_schema
from tests.conftest import FakeLLMClient


def _faq_html() -> str:
    return (
        "<html><body>"
        "<h1>FAQ on quarterly tax filing</h1>"
        "<h2>When are quarterly taxes due?</h2>"
        "<p>Quarterly estimated taxes are due on the 15th of April, June, September, and January.</p>"
        "<h2>Who must pay quarterly taxes?</h2>"
        "<p>Self-employed individuals and many landlords are required to pay quarterly estimated taxes.</p>"
        "</body></html>"
    )


def _howto_html() -> str:
    return (
        "<html><body>"
        "<h1>How to install Docker on Ubuntu</h1>"
        "<h2>How to add the official Docker GPG key</h2>"
        "<p>Run curl to fetch the GPG key from download.docker.com and pipe it into apt-key.</p>"
        "<h2>How to configure the Docker apt repository</h2>"
        "<p>Add the official repository line to /etc/apt/sources.list.d/docker.list and refresh apt.</p>"
        "</body></html>"
    )


def _article_html() -> str:
    return (
        "<html><body>"
        "<h1>State of microservice deployment in 2026</h1>"
        "<p>Microservice deployment in 2026 leans heavily on Kubernetes operators "
        "and progressive delivery techniques. This article summarises the moves "
        "agencies should expect over the next year.</p>"
        "</body></html>"
    )


def _faq_payload() -> str:
    return json.dumps(
        {
            "json_ld": {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": "When are quarterly taxes due?",
                        "acceptedAnswer": {"@type": "Answer", "text": "April, June, September, January 15."},
                    },
                    {
                        "@type": "Question",
                        "name": "Who must pay quarterly taxes?",
                        "acceptedAnswer": {"@type": "Answer", "text": "Self-employed and many landlords."},
                    },
                    {
                        "@type": "Question",
                        "name": "How are penalties calculated?",
                        "acceptedAnswer": {"@type": "Answer", "text": "Based on underpaid amount and IRS rate."},
                    },
                ],
            }
        }
    )


def _stub_faq_payload() -> str:
    return json.dumps(
        {
            "json_ld": {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": "Only one question?"},
                ],
            }
        }
    )


def _howto_payload() -> str:
    return json.dumps(
        {
            "json_ld": {
                "@context": "https://schema.org",
                "@type": "HowTo",
                "name": "Install Docker on Ubuntu",
                "step": [
                    {"@type": "HowToStep", "name": "GPG key", "text": "curl the GPG key"},
                    {"@type": "HowToStep", "name": "Repo", "text": "add the apt repository"},
                    {"@type": "HowToStep", "name": "Install", "text": "apt-get install docker-ce"},
                ],
            }
        }
    )


def _article_payload() -> str:
    return json.dumps(
        {
            "json_ld": {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": "State of microservice deployment in 2026",
                "author": {"@type": "Person", "name": "AEGIS Editorial"},
                "articleBody": "Microservice deployment in 2026 leans heavily on Kubernetes…",
            }
        }
    )


def test_detect_intent_faqpage():
    pc = parse(_faq_html(), "text")
    assert detect_intent(pc) == "FAQPage"


def test_detect_intent_howto():
    pc = parse(_howto_html(), "text")
    assert detect_intent(pc) == "HowTo"


def test_detect_intent_article_fallback():
    pc = parse(_article_html(), "text")
    assert detect_intent(pc) == "Article"


@pytest.mark.asyncio
async def test_generate_schema_faqpage_happy_path():
    pc = parse(_faq_html(), "text")
    fake = FakeLLMClient([_faq_payload()])
    result = await generate_schema(pc, client=fake)
    assert result.intent == "FAQPage"
    assert result.populated is True
    assert "FAQPage" in result.types_emitted
    assert result.html_snippet.startswith('<script type="application/ld+json">')
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_generate_schema_howto_happy_path():
    pc = parse(_howto_html(), "text")
    fake = FakeLLMClient([_howto_payload()])
    result = await generate_schema(pc, client=fake)
    assert result.intent == "HowTo"
    assert result.populated is True


@pytest.mark.asyncio
async def test_generate_schema_article_happy_path():
    pc = parse(_article_html(), "text")
    fake = FakeLLMClient([_article_payload()])
    result = await generate_schema(pc, client=fake)
    assert result.intent == "Article"
    assert result.populated is True
    assert "headline" in result.json_ld


@pytest.mark.asyncio
async def test_generate_schema_back_check_retries_under_populated_stub():
    pc = parse(_faq_html(), "text")
    fake = FakeLLMClient([_stub_faq_payload(), _faq_payload()])
    result = await generate_schema(pc, client=fake)
    assert result.populated is True
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_generate_schema_exhausts_retries_raises():
    pc = parse(_faq_html(), "text")
    fake = FakeLLMClient([_stub_faq_payload()] * 3)
    with pytest.raises(LLMUnavailableError) as exc:
        await generate_schema(pc, client=fake)
    assert "Back-check failed" in exc.value.detail
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_generate_schema_explicit_intent_overrides_detection():
    pc = parse(_article_html(), "text")
    fake = FakeLLMClient([_faq_payload()])
    result = await generate_schema(pc, intent="FAQPage", client=fake)
    assert result.intent == "FAQPage"


@pytest.mark.asyncio
async def test_empty_body_raises_value_error():
    pc = parse("just a heading <h1>x</h1>", "text")
    # parse() will yield non-empty body_text from soup get_text; fabricate an empty pc instead.
    from app.services.content_parser import ParsedContent

    empty = ParsedContent(raw="", soup=None, first_paragraph="", h_tags=[], body_text="")
    with pytest.raises(ValueError):
        await generate_schema(empty, client=FakeLLMClient([]))
