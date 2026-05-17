"""Bulk-rewrite orchestrator (Phase B.2).

Runs the Phase B rewriters against a single document in one call and
produces a unified markdown + HTML diff plus a per-fix status envelope.

Score estimation: re-run the AEO checks against a synthetic
`ParsedContent` that incorporates the fixes (rewritten paragraph,
restructured h-tags, injected JSON-LD). The `_after` score is a best
estimate; it does not re-fetch the URL or re-parse the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from app.api.aeo import _band_for, _build_response
from app.models.schemas import (
    BulkRewriteSection,
    HeadingOperation,
    HeadingPair,
    RewriteVariant,
    SchemaGenResult,
)
from app.services.aeo_checks import default_checks
from app.services.content_parser import ParsedContent
from app.services.llm_client import LLMClient, LLMUnavailableError
from app.services.rewrite.direct_answer import rewrite_direct_answer
from app.services.rewrite.headings import autofix_headings, to_html
from app.services.rewrite.schema_gen import generate_schema


@dataclass
class BulkRewriteOutcome:
    aeo_score_before: int
    aeo_score_after_estimate: int
    band_before: str
    band_after_estimate: str
    sections: list[BulkRewriteSection]
    markdown_diff: str
    html_diff: str
    model_used: str | None


async def run_bulk_rewrite(
    parsed: ParsedContent,
    *,
    target_query: str | None = None,
    include_direct_answer: bool = True,
    include_headings: bool = True,
    include_schema: bool = True,
    client: LLMClient | None = None,
) -> BulkRewriteOutcome:
    before_results = [c.run(parsed) for c in default_checks()]
    before_envelope = _build_response(before_results)

    sections: list[BulkRewriteSection] = []
    model_used: str | None = None
    by_id = {r.check_id: r for r in before_results}

    # --- direct_answer ---
    direct_answer_variant: RewriteVariant | None = None
    if not include_direct_answer:
        sections.append(BulkRewriteSection(name="direct_answer", status="disabled"))
    elif by_id["direct_answer"].passed:
        sections.append(
            BulkRewriteSection(
                name="direct_answer",
                status="skipped",
                detail="Check A already passes — no rewrite needed.",
            )
        )
    else:
        try:
            variants, mid = await rewrite_direct_answer(
                parsed.first_paragraph or "",
                target_query=target_query,
                client=client,
            )
            model_used = mid
            direct_answer_variant = variants[0]  # default to the first passing variant
            sections.append(
                BulkRewriteSection(
                    name="direct_answer",
                    status="applied",
                    payload={"variants": [v.model_dump() for v in variants]},
                )
            )
        except (LLMUnavailableError, ValueError) as e:
            sections.append(
                BulkRewriteSection(
                    name="direct_answer",
                    status="failed",
                    detail=getattr(e, "detail", str(e)),
                )
            )

    # --- headings ---
    headings_fix: dict[str, Any] | None = None
    if not include_headings:
        sections.append(BulkRewriteSection(name="headings", status="disabled"))
    elif by_id["htag_hierarchy"].passed:
        sections.append(
            BulkRewriteSection(
                name="headings",
                status="skipped",
                detail="Check B already passes — no restructure needed.",
            )
        )
    else:
        result = autofix_headings(parsed.h_tags)
        headings_fix = result
        sections.append(
            BulkRewriteSection(
                name="headings",
                status="applied",
                payload={
                    "original_h_tags": [
                        HeadingPair(level=l, text=t).model_dump()
                        for l, t in result["original"]
                    ],
                    "fixed_h_tags": [
                        HeadingPair(level=l, text=t).model_dump()
                        for l, t in result["fixed"]
                    ],
                    "operations": [
                        HeadingOperation(**op).model_dump()
                        for op in result["operations"]
                    ],
                    "fixed_html": to_html(result["fixed"]),
                },
            )
        )

    # --- schema_gen ---
    schema_result: SchemaGenResult | None = None
    if not include_schema:
        sections.append(BulkRewriteSection(name="schema", status="disabled"))
    elif by_id["schema_markup"].score == 20:
        sections.append(
            BulkRewriteSection(
                name="schema",
                status="skipped",
                detail="Check E already populated — no injection needed.",
            )
        )
    elif not (parsed.body_text and parsed.body_text.strip()):
        sections.append(
            BulkRewriteSection(
                name="schema",
                status="failed",
                detail="body_text is empty; cannot generate schema",
            )
        )
    else:
        try:
            schema_result = await generate_schema(parsed, client=client)
            model_used = model_used or schema_result.model_used
            sections.append(
                BulkRewriteSection(
                    name="schema",
                    status="applied",
                    payload=schema_result.model_dump(),
                )
            )
        except (LLMUnavailableError, ValueError) as e:
            sections.append(
                BulkRewriteSection(
                    name="schema",
                    status="failed",
                    detail=getattr(e, "detail", str(e)),
                )
            )

    # --- estimate the after-score ---
    after_parsed = _apply_fixes(parsed, direct_answer_variant, headings_fix, schema_result)
    after_results = [c.run(after_parsed) for c in default_checks()]
    after_envelope = _build_response(after_results)

    markdown_diff = _build_markdown_diff(
        parsed, direct_answer_variant, headings_fix, schema_result
    )
    html_diff = _build_html_diff(
        parsed, direct_answer_variant, headings_fix, schema_result
    )

    return BulkRewriteOutcome(
        aeo_score_before=before_envelope.aeo_score,
        aeo_score_after_estimate=after_envelope.aeo_score,
        band_before=before_envelope.band,
        band_after_estimate=_band_for(after_envelope.aeo_score),
        sections=sections,
        markdown_diff=markdown_diff,
        html_diff=html_diff,
        model_used=model_used,
    )


def _apply_fixes(
    parsed: ParsedContent,
    direct_answer_variant: RewriteVariant | None,
    headings_fix: dict[str, Any] | None,
    schema_result: SchemaGenResult | None,
) -> ParsedContent:
    """Build a synthetic `ParsedContent` reflecting all applied fixes.

    Heading restructure rebuilds the h_tags list. Direct-answer rewrite
    replaces `first_paragraph` AND prepends it to `body_text` (so
    Readability / Entity-Coverage / Citations measure the post-rewrite
    body). Schema injection adds a JSON-LD `<script>` to the soup so
    Check E sees a populated block.
    """
    new_first_para = (
        direct_answer_variant.text if direct_answer_variant else parsed.first_paragraph
    )
    new_h_tags = headings_fix["fixed"] if headings_fix else parsed.h_tags
    new_body = parsed.body_text or ""
    if direct_answer_variant and parsed.first_paragraph:
        # Swap the original opening paragraph for the rewrite in body_text.
        new_body = new_body.replace(parsed.first_paragraph, direct_answer_variant.text, 1)

    new_soup = parsed.soup
    if schema_result is not None and parsed.soup is not None:
        # Clone soup so the original is not mutated.
        new_soup = BeautifulSoup(str(parsed.soup), "html.parser")
        script = new_soup.new_tag("script", type="application/ld+json")
        script.string = _json_dumps(schema_result.json_ld)
        head = new_soup.find("head")
        target = head or new_soup
        target.append(script)

    return ParsedContent(
        raw=parsed.raw,
        soup=new_soup,
        first_paragraph=new_first_para or "",
        h_tags=new_h_tags,
        body_text=new_body,
    )


def _json_dumps(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, indent=2, ensure_ascii=False)


def _build_markdown_diff(
    parsed: ParsedContent,
    direct_answer_variant: RewriteVariant | None,
    headings_fix: dict[str, Any] | None,
    schema_result: SchemaGenResult | None,
) -> str:
    blocks: list[str] = ["# AEGIS — Bulk rewrite suggestions", ""]
    if direct_answer_variant:
        blocks.extend(
            [
                "## Opening paragraph (Check A fix)",
                "",
                "**Before**",
                "",
                f"> {parsed.first_paragraph}",
                "",
                f"**After** (`{direct_answer_variant.style}`, "
                f"{direct_answer_variant.word_count} words, "
                f"Check A {direct_answer_variant.check_a_score}/20)",
                "",
                f"> {direct_answer_variant.text}",
                "",
            ]
        )
    if headings_fix:
        blocks.extend(
            [
                "## Heading hierarchy (Check B fix)",
                "",
                "| # | Before | After |",
                "|---|---|---|",
            ]
        )
        for i, ((bl, bt), (al, at)) in enumerate(
            zip(headings_fix["original"], headings_fix["fixed"])
        ):
            blocks.append(f"| {i} | H{bl} {bt} | H{al} {at} |")
        blocks.append("")
        if headings_fix["operations"]:
            blocks.append("Operations:")
            for op in headings_fix["operations"]:
                blocks.append(f"- `{op['rule']}` — {op['description']}")
            blocks.append("")
    if schema_result:
        blocks.extend(
            [
                f"## Schema.org JSON-LD (Check E fix — {schema_result.intent})",
                "",
                "Paste this into the page `<head>`:",
                "",
                "```html",
                schema_result.html_snippet,
                "```",
                "",
            ]
        )
    if len(blocks) == 2:
        blocks.append("_No rewrites necessary — all targeted checks already pass._")
    return "\n".join(blocks).rstrip() + "\n"


def _build_html_diff(
    parsed: ParsedContent,
    direct_answer_variant: RewriteVariant | None,
    headings_fix: dict[str, Any] | None,
    schema_result: SchemaGenResult | None,
) -> str:
    """Minimal HTML rendition of the markdown diff for easy embedding.

    Not a markdown→HTML conversion of the markdown above — we hand-build
    so the result is small, dependency-free, and safe to inject directly.
    """
    sections: list[str] = ["<section class='aegis-bulk-rewrite'>"]
    sections.append("<h1>AEGIS — Bulk rewrite suggestions</h1>")
    if direct_answer_variant:
        sections.append("<section data-fix='direct_answer'>")
        sections.append("<h2>Opening paragraph (Check A fix)</h2>")
        sections.append(f"<p><strong>Before</strong></p><blockquote>{_e(parsed.first_paragraph)}</blockquote>")
        sections.append(
            f"<p><strong>After</strong> ({_e(direct_answer_variant.style)}, "
            f"{direct_answer_variant.word_count} words, Check A "
            f"{direct_answer_variant.check_a_score}/20)</p>"
            f"<blockquote>{_e(direct_answer_variant.text)}</blockquote>"
        )
        sections.append("</section>")
    if headings_fix:
        sections.append("<section data-fix='headings'>")
        sections.append("<h2>Heading hierarchy (Check B fix)</h2>")
        sections.append("<table><thead><tr><th>#</th><th>Before</th><th>After</th></tr></thead><tbody>")
        for i, ((bl, bt), (al, at)) in enumerate(
            zip(headings_fix["original"], headings_fix["fixed"])
        ):
            sections.append(
                f"<tr><td>{i}</td><td>H{bl} {_e(bt)}</td><td>H{al} {_e(at)}</td></tr>"
            )
        sections.append("</tbody></table>")
        sections.append("</section>")
    if schema_result:
        sections.append("<section data-fix='schema'>")
        sections.append(
            f"<h2>Schema.org JSON-LD (Check E fix — {_e(schema_result.intent)})</h2>"
        )
        sections.append(f"<pre><code>{_e(schema_result.html_snippet)}</code></pre>")
        sections.append("</section>")
    if len(sections) == 2:
        sections.append("<p><em>No rewrites necessary — all targeted checks already pass.</em></p>")
    sections.append("</section>")
    return "".join(sections)


def _e(s: str | None) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
