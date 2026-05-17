"""Unit tests for autopilot/report_builder."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.autopilot import report_builder
from app.autopilot.report_builder import (
    RenderContext,
    _readability_warning,
    _top_comparative,
    build_context,
    render_html,
)
from app.db.models import Audit, Prospect


@pytest.fixture(autouse=True)
def _signing_keys(monkeypatch):
    monkeypatch.setenv("REPORT_SIGNING_KEY", "k" * 32)


def _make_prospect() -> Prospect:
    p = Prospect(
        url="https://example.com/post",
        domain="example.com",
        target_keyword="best vector db",
    )
    p.id = 7
    return p


def _make_audit(*, score: int = 55, failed_checks=None, fanout=None) -> Audit:
    a = Audit(
        prospect_id=7,
        aeo_score=score,
        band="Significant Gaps",
        failed_checks=failed_checks,
        missing_gap_types=["how_to", "trust_signals"],
        fanout_payload=fanout,
    )
    a.id = 99
    a.token_jti = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
    return a


# ---- helpers ----


def test_readability_warning_with_grade() -> None:
    failed = [
        {
            "check_id": "readability",
            "name": "Snippet readability",
            "score": 0,
            "max_score": 20,
            "recommendation": "Lower the grade.",
            "details": {"flesch_kincaid_grade": 12.8},
        }
    ]
    msg = _readability_warning(failed)
    assert msg and "12.8" in msg


def test_readability_warning_falls_back_to_recommendation() -> None:
    failed = [
        {
            "check_id": "readability",
            "name": "Snippet readability",
            "score": 0,
            "max_score": 20,
            "recommendation": "Shorten sentences.",
            "details": {},
        }
    ]
    msg = _readability_warning(failed)
    assert msg == "Shorten sentences."


def test_readability_warning_none_when_no_readability_check() -> None:
    failed = [{"check_id": "h_tag_hierarchy", "name": "x", "score": 0,
               "max_score": 20, "recommendation": "y", "details": {}}]
    assert _readability_warning(failed) is None


def test_top_comparative_picks_first_three_uncovered() -> None:
    payload = {
        "sub_queries": [
            {"type": "comparative", "query": "A vs B", "covered": True},
            {"type": "comparative", "query": "C vs D", "covered": False},
            {"type": "feature_specific", "query": "with X", "covered": False},
            {"type": "comparative", "query": "E vs F", "covered": False},
            {"type": "comparative", "query": "G vs H", "covered": False},
            {"type": "comparative", "query": "I vs J", "covered": False},
        ]
    }
    out = _top_comparative(payload)
    assert [q["query"] for q in out] == ["C vs D", "E vs F", "G vs H"]


def test_top_comparative_empty_payload_returns_empty() -> None:
    assert _top_comparative(None) == []
    assert _top_comparative({}) == []


# ---- build_context + render_html ----


def test_build_context_assembles_all_fields(monkeypatch) -> None:
    failed = [
        {"check_id": "direct_answer", "name": "Direct answer", "score": 10,
         "max_score": 20, "recommendation": "Shorten the lede.", "details": {}}
    ]
    fanout = {
        "target_query": "best vector db",
        "sub_queries": [
            {"type": "comparative", "query": "pgvector vs Pinecone", "covered": False},
            {"type": "comparative", "query": "Weaviate vs Qdrant", "covered": True},
        ],
    }
    audit = _make_audit(score=42, failed_checks=failed, fanout=fanout)
    prospect = _make_prospect()

    ctx = build_context(audit, prospect, report_base_url="https://aegis.test")

    assert ctx.aeo_score == 42
    assert ctx.domain == "example.com"
    assert ctx.target_keyword == "best vector db"
    assert ctx.failed_checks == failed
    assert ctx.top_missing_comparative[0]["query"] == "pgvector vs Pinecone"
    assert ctx.missing_gap_types == ["how_to", "trust_signals"]
    assert ctx.report_url.startswith("https://aegis.test/r/")
    assert "audit=99" in ctx.checkout_url


def test_render_html_contains_key_signals() -> None:
    fanout = {
        "sub_queries": [
            {"type": "comparative", "query": "pgvector vs Pinecone", "covered": False},
            {"type": "comparative", "query": "Weaviate vs Qdrant", "covered": False},
            {"type": "comparative", "query": "Milvus vs Chroma", "covered": False},
        ]
    }
    failed = [
        {"check_id": "readability", "name": "Snippet readability", "score": 0,
         "max_score": 20, "recommendation": "Shorten sentences",
         "details": {"flesch_kincaid_grade": 12.4}},
    ]
    audit = _make_audit(score=55, failed_checks=failed, fanout=fanout)
    ctx = build_context(audit, _make_prospect(), report_base_url="https://aegis.test")
    html = render_html(ctx)

    assert "example.com" in html
    assert "best vector db" in html
    assert ">55<" in html  # gauge-num renders the score
    assert "Snippet readability" in html
    assert "pgvector vs Pinecone" in html
    assert "Weaviate vs Qdrant" in html
    assert "Milvus vs Chroma" in html
    assert "12.4" in html
    assert "<!doctype html>" in html.lower() or "<!DOCTYPE html>" in html


def test_render_html_handles_empty_audit_gracefully() -> None:
    audit = _make_audit(score=92, failed_checks=None, fanout=None)
    audit.band = "AEO Optimized"
    audit.missing_gap_types = None
    ctx = build_context(audit, _make_prospect(), report_base_url="https://aegis.test")
    html = render_html(ctx)

    assert "All structural checks passed" in html
    assert "No uncovered comparative queries" in html
    # No empty "Missing query types" section when list is empty.
    assert "Missing query types" not in html


def test_pixel_gif_is_valid_gif_header() -> None:
    from app.api.reports import PIXEL_GIF

    assert PIXEL_GIF[:6] in (b"GIF87a", b"GIF89a")
    assert PIXEL_GIF.endswith(b";")
