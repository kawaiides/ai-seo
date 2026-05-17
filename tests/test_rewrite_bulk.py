"""Unit tests for the bulk-rewrite orchestrator (Phase B.2)."""

from __future__ import annotations

import json

import pytest

from app.services.content_parser import parse
from app.services.rewrite.bulk import run_bulk_rewrite
from tests.conftest import FakeLLMClient


def _failing_doc_html() -> str:
    long_first_para = " ".join(["word"] * 110)
    return (
        "<html><head></head><body>"
        "<h2>Pre-H1 ad — when is this due?</h2>"
        f"<p>{long_first_para}</p>"
        "<h1>Real title</h1>"
        "<h2>Who must file this?</h2>"
        "<h2>What about late fees?</h2>"
        "<h4>Skipped section</h4>"
        "<p>Body paragraph with substantive content.</p>"
        "</body></html>"
    )


def _passing_doc_html() -> str:
    return (
        "<html><head>"
        '<script type="application/ld+json">'
        '{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"Q","acceptedAnswer":{"@type":"Answer","text":"A"}},{"@type":"Question","name":"Q2","acceptedAnswer":{"@type":"Answer","text":"A2"}},{"@type":"Question","name":"Q3","acceptedAnswer":{"@type":"Answer","text":"A3"}}]}'
        "</script>"
        "</head><body>"
        "<h1>FAQ</h1>"
        "<p>Python is a high-level programming language used for data science.</p>"
        "<h2>When is it useful?</h2>"
        "<p>Python is useful whenever readability and a large library ecosystem matter.</p>"
        "</body></html>"
    )


def _direct_answer_payload() -> str:
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


def _faq_payload() -> str:
    return json.dumps(
        {
            "json_ld": {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": "Q1", "acceptedAnswer": {"@type": "Answer", "text": "A1"}},
                    {"@type": "Question", "name": "Q2", "acceptedAnswer": {"@type": "Answer", "text": "A2"}},
                    {"@type": "Question", "name": "Q3", "acceptedAnswer": {"@type": "Answer", "text": "A3"}},
                ],
            }
        }
    )


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_bulk_applies_all_three_fixes_on_failing_doc():
    pc = parse(_failing_doc_html(), "text")
    fake = FakeLLMClient([_direct_answer_payload(), _faq_payload()])
    out = await run_bulk_rewrite(pc, client=fake)
    by_name = {s.name: s for s in out.sections}
    assert by_name["direct_answer"].status == "applied"
    assert by_name["headings"].status == "applied"
    assert by_name["schema"].status == "applied"
    assert out.aeo_score_after_estimate >= out.aeo_score_before
    assert "AEGIS — Bulk rewrite suggestions" in out.markdown_diff
    assert "<section class='aegis-bulk-rewrite'>" in out.html_diff


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_bulk_skips_passing_checks():
    pc = parse(_passing_doc_html(), "text")
    out = await run_bulk_rewrite(pc, client=FakeLLMClient([]))
    by_name = {s.name: s for s in out.sections}
    # direct_answer and schema should both already pass on the canned doc
    assert by_name["direct_answer"].status == "skipped"
    assert by_name["schema"].status == "skipped"


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_bulk_respects_disabled_flags():
    pc = parse(_failing_doc_html(), "text")
    out = await run_bulk_rewrite(
        pc,
        include_direct_answer=False,
        include_schema=False,
        client=FakeLLMClient([]),
    )
    by_name = {s.name: s for s in out.sections}
    assert by_name["direct_answer"].status == "disabled"
    assert by_name["schema"].status == "disabled"
    assert by_name["headings"].status == "applied"


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_bulk_marks_direct_answer_failed_when_llm_unavailable():
    from app.services.llm_client import LLMUnavailableError

    pc = parse(_failing_doc_html(), "text")
    fake = FakeLLMClient([
        LLMUnavailableError("net down"),
        LLMUnavailableError("net down"),
        LLMUnavailableError("net down"),
        _faq_payload(),
    ])
    out = await run_bulk_rewrite(pc, client=fake)
    by_name = {s.name: s for s in out.sections}
    assert by_name["direct_answer"].status == "failed"
    assert by_name["schema"].status == "applied"


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_bulk_markdown_diff_lists_operations_when_headings_fix_applied():
    pc = parse(_failing_doc_html(), "text")
    fake = FakeLLMClient([_direct_answer_payload(), _faq_payload()])
    out = await run_bulk_rewrite(pc, client=fake)
    assert "Heading hierarchy" in out.markdown_diff
    assert "Operations:" in out.markdown_diff


@pytest.mark.usefixtures("nlp")
@pytest.mark.asyncio
async def test_bulk_html_diff_escapes_content():
    article_payload = json.dumps(
        {
            "json_ld": {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": "Tag & Co",
                "author": {"@type": "Person", "name": "AEGIS"},
                "articleBody": "word " * 30,
            }
        }
    )
    pc = parse("<h2>Tag &amp; Co</h2><p>" + (" word" * 110).strip() + "</p>", "text")
    fake = FakeLLMClient([_direct_answer_payload(), article_payload])
    out = await run_bulk_rewrite(pc, client=fake)
    # Original tag text rendered after un-escaping by BS4 → "Tag & Co"; the HTML
    # diff must re-escape the ampersand so the snippet stays safe to inject.
    assert "&amp;" in out.html_diff
    assert "Tag & Co" not in out.html_diff  # only escaped form should appear
