"""Automated SEO blog post generator.

Cycle:
  1. Pick a topic by asking the LLM to propose a new AEO-adjacent SEO
     topic that is NOT already covered by the existing article corpus.
  2. Draft the article body + metadata as strict JSON (Pydantic-validated).
  3. Score the draft against the local AEO check pipeline (no HTTP).
  4. If the score is < THRESHOLD, ask the LLM to revise the failing
     checks specifically and re-score. Up to MAX_REVISIONS rounds.
  5. Persist the final article as a JSON file in `app/content/generated/`.

Append-only JSON storage so the cron / scheduler can write safely without
mutating Python source files. `app.content.articles` reads the directory
at import and merges the files into `ARTICLES`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services.aeo_checks import default_checks
from app.services.content_parser import parse
from app.services.llm_client import LLMClient, LLMUnavailableError, OpenAIClient

log = logging.getLogger(__name__)

GENERATED_DIR = Path(__file__).resolve().parent.parent / "content" / "generated"
STATE_FILE = GENERATED_DIR / "_state.json"

MIN_SCORE = 85
MAX_REVISIONS = 2
INTERVAL_HOURS = 72  # "every 3 days"


# ---------- Strict LLM schemas ----------


class LLMTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=4, max_length=80)
    title: str = Field(..., min_length=10, max_length=110)
    target_keyword: str = Field(..., min_length=3, max_length=80)
    angle: str = Field(..., min_length=20, max_length=1200)
    category: str = Field(..., min_length=3, max_length=40)

    @field_validator("slug")
    @classmethod
    def _slugify(cls, v: str) -> str:
        v = re.sub(r"[^a-z0-9-]+", "-", v.lower()).strip("-")
        v = re.sub(r"-+", "-", v)
        if not v:
            raise ValueError("slug must contain at least one alphanumeric character")
        return v


class LLMFAQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(..., min_length=8, max_length=180)
    a: str = Field(..., min_length=20, max_length=900)


class LLMArticle(BaseModel):
    """Strict schema for an LLM-drafted article.

    `body_html` must be a complete, well-formed HTML fragment using h2/h3/p/
    ul/ol/li/a/blockquote/code/pre tags only. No <html>/<head>/<body>; the
    template handles those. Inline outbound links to authoritative sources
    are encouraged (citation density check rewards them).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=10, max_length=110)
    description: str = Field(..., min_length=80, max_length=170)
    excerpt: str = Field(..., min_length=80, max_length=320)
    keywords: list[str] = Field(..., min_length=5, max_length=12)
    hero_emoji: str = Field(..., min_length=1, max_length=4)
    reading_minutes: int = Field(..., ge=4, le=20)
    body_html: str = Field(..., min_length=1500)
    faqs: list[LLMFAQ] = Field(..., min_length=4, max_length=8)


# ---------- Prompts ----------


_SYSTEM_TOPIC = """You are an expert AI SEO content strategist for AEGIS Autopilot — a pre-publish AEO (Answer Engine Optimization) scanner. You propose new long-form blog topics that:
- Target high-intent keywords AI search users actually search for
- Have NOT been covered by the existing corpus
- Are genuinely useful (not list-bait or AI-generated filler)
- Are timely for 2026 (the current year is 2026)

Output strict JSON only. No prose. No code fences."""


_SYSTEM_ARTICLE = """You are AEGIS Autopilot's senior content editor. You write long-form SEO articles that score 85+ on AEGIS's six AEO structural checks:
1. Direct answer paragraph — first paragraph after the H1 is a 40–80 word declarative answer, no hedging.
2. Heading hierarchy — one logical H1 (the title), then H2 sections, H3 sub-sections. No level skips.
3. JSON-LD friendly — content structures cleanly into Article schema.
4. Citations — 4–6 outbound links to AUTHORITATIVE domains (Wikipedia, arXiv, W3C, IETF/RFC, .gov, .edu, Reuters, BBC, Nature, Science). Use real, plausible URLs.
5. Freshness — written for 2026, mention recent context.
6. Entity coverage — name 8–15 distinct named entities (products, organizations, methods, concepts) with non-trivial context.

Style:
- Conversational but dense. 9th-grade readability target. Short sentences (12–20 words). Active voice.
- Open every article with <p class="lead">DIRECT ANSWER HERE</p> as the first body element.
- Use <h2 id="kebab-slug"> for sections so anchor links work.
- Use lists, tables, and blockquote examples liberally.
- Include a "Sources and further reading" section at the bottom with 4–6 authoritative outbound links as a final <h2 id="sources"> + <ul>.
- Never invent URLs that don't plausibly exist. Prefer Wikipedia, arXiv, W3C.
- No marketing fluff. No "in this article we'll explore." No "let's dive in."

Output strict JSON only matching the requested schema. No code fences, no preamble."""


# ---------- Generation pipeline ----------


def _llm() -> LLMClient:
    return OpenAIClient()


def _existing_titles() -> list[str]:
    from app.content.articles import list_articles

    return [a["title"] for a in list_articles()]


def _existing_slugs() -> set[str]:
    from app.content.articles import list_articles

    return {a["slug"] for a in list_articles()}


def _strip_code_fence(text: str) -> str:
    """Defensive: strip ```json fences if the model emits them despite the
    JSON-mode instruction."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


async def propose_topic(client: LLMClient) -> LLMTopic:
    """Ask the LLM for one new topic that doesn't duplicate the existing corpus."""
    existing_titles = _existing_titles()
    bullet_list = "\n".join(f"- {t}" for t in existing_titles)
    user = f"""Existing AEGIS blog topics (do NOT duplicate or rehash these):
{bullet_list}

Propose ONE new long-form article topic that:
- Has NOT been covered above
- Targets a high-volume AEO/GEO/AI-search-related search intent
- Would score well as a 2026-dated piece
- Is concrete enough to write 1500+ words on without filler

Return JSON: {{"slug": "...", "title": "...", "target_keyword": "...", "angle": "...", "category": "..."}}.
Slug must be kebab-case. Title <= 110 chars. Category one of: Fundamentals, Strategy, Tactics, Writing, Tooling, Case Study."""
    raw = await client.generate_json(_SYSTEM_TOPIC, user)
    return LLMTopic.model_validate_json(_strip_code_fence(raw))


async def draft_article(
    client: LLMClient, topic: LLMTopic, *, revision_note: str | None = None
) -> LLMArticle:
    """Draft (or revise) the full article body + metadata."""
    revision_block = ""
    if revision_note:
        revision_block = (
            f"\n\n--- REVISION REQUIRED ---\n"
            f"The previous draft failed these AEGIS AEO checks:\n{revision_note}\n"
            f"Address each failure in this revision while keeping the same topic.\n"
        )
    user = f"""Topic to write about:
  slug: {topic.slug}
  title: {topic.title}
  target keyword: {topic.target_keyword}
  category: {topic.category}
  angle: {topic.angle}
{revision_block}

Write the full article. Output strict JSON only:
{{
  "title": "Final on-page title (≤ 110 chars, can refine the proposed title)",
  "description": "120–160 char meta description, declarative, no hedging",
  "excerpt": "1–2 sentence excerpt for the blog index card",
  "keywords": ["5–12 keywords/phrases"],
  "hero_emoji": "single emoji",
  "reading_minutes": <int 6–14>,
  "body_html": "<p class='lead'>40–80 word direct answer.</p>\\n<h2 id='...'>...</h2>\\n... full HTML body ending with <h2 id='sources'>Sources and further reading</h2><ul><li><a href='...'>...</a></li>...</ul>",
  "faqs": [{{"q": "...", "a": "..."}}, ...4–8 entries]
}}

Constraints:
- body_html ≥ 1500 chars (aim for 4000–7000)
- 4+ outbound <a href> links to authoritative sources (Wikipedia, arXiv, W3C, RFC, .gov, .edu, Nature, Science, Reuters, BBC) inside body_html
- 8–15 distinct named entities mentioned with context
- First <p class='lead'> paragraph = 40–80 words, declarative, no "let's explore" / "depends" / "might"
- All sentences 12–20 words average. Plain language. Grade 9 readability target.
- Use <h2 id='...'> and <h3> for structure. No level skips."""
    raw = await client.generate_json(_SYSTEM_ARTICLE, user)
    return LLMArticle.model_validate_json(_strip_code_fence(raw))


def score_article_html(article_html: str) -> tuple[int, list[dict[str, Any]]]:
    """Run the AEGIS AEO check pipeline locally on the article HTML.

    Returns (score, failed_checks_dicts).
    """
    parsed = parse(article_html, input_type="text")
    results = [check.run(parsed) for check in default_checks()]
    raw = sum(r.score for r in results)
    max_total = sum(r.max_score for r in results) or 100
    score = round((raw / max_total) * 100)
    failed = [
        {"name": r.name, "score": r.score, "max_score": r.max_score, "recommendation": r.recommendation}
        for r in results
        if not r.passed
    ]
    return score, failed


def _build_renderable_html(article: LLMArticle, slug: str, topic: LLMTopic) -> str:
    """Wrap the body in the same shell `tools/score_articles.py` uses so the
    scorer sees the same structural signals the live page will."""
    today = date.today().isoformat()
    head = (
        f"<title>{article.title}</title>\n"
        f'<meta name="description" content="{article.description}">\n'
        f'<link rel="canonical" href="https://aegis-autopilot.com/blog/{slug}">\n'
    )
    faq_section = ""
    if article.faqs:
        faq_items = "\n".join(f"<h3>{f.q}</h3>\n<p>{f.a}</p>" for f in article.faqs)
        faq_section = f"<section><h2>Frequently asked</h2>\n{faq_items}\n</section>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{head}
</head>
<body>
<article>
  <header>
    <h1>{article.title}</h1>
    <p><time datetime="{today}">Published {today}</time> · {article.reading_minutes} min read</p>
  </header>
  {article.body_html}
  {faq_section}
</article>
</body>
</html>"""


async def generate_one(
    client: LLMClient | None = None,
    *,
    min_score: int = MIN_SCORE,
    max_revisions: int = MAX_REVISIONS,
) -> dict[str, Any]:
    """End-to-end: pick topic → draft → score-and-revise loop → return article dict.

    Does NOT persist. Use `save_generated` to write to disk.
    """
    client = client or _llm()
    topic = await propose_topic(client)

    if topic.slug in _existing_slugs():
        # one defensive retry with a stronger "don't duplicate" hint
        log.info("blog_generator: proposed slug %s already exists, retrying", topic.slug)
        topic = await propose_topic(client)
        if topic.slug in _existing_slugs():
            raise RuntimeError(
                f"LLM kept proposing existing slug {topic.slug!r}; expand corpus diversity or seed manually."
            )

    article = await draft_article(client, topic)
    score, failed = score_article_html(_build_renderable_html(article, topic.slug, topic))

    revisions = 0
    while score < min_score and revisions < max_revisions:
        revisions += 1
        note = "\n".join(
            f"- {f['name']} ({f['score']}/{f['max_score']}): {f.get('recommendation') or '(no detail)'}"
            for f in failed
        )
        log.info("blog_generator: revision %d (score=%d) — failures: %s", revisions, score, [f['name'] for f in failed])
        article = await draft_article(client, topic, revision_note=note)
        score, failed = score_article_html(_build_renderable_html(article, topic.slug, topic))

    today = date.today().isoformat()
    return {
        "slug": topic.slug,
        "title": article.title,
        "description": article.description,
        "keywords": article.keywords,
        "category": topic.category,
        "author": "AEGIS Autopilot",
        "published": today,
        "updated": today,
        "reading_minutes": article.reading_minutes,
        "hero_emoji": article.hero_emoji,
        "excerpt": article.excerpt,
        "body_html": article.body_html,
        "faqs": [f.model_dump() for f in article.faqs],
        "related": [],
        "_meta": {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "final_score": score,
            "revisions": revisions,
            "final_failed_checks": [f["name"] for f in failed],
            "target_keyword": topic.target_keyword,
            "angle": topic.angle,
        },
    }


# ---------- Persistence ----------


def save_generated(article: dict[str, Any]) -> Path:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    fname = GENERATED_DIR / f"{article['slug']}.json"
    fname.write_text(json.dumps(article, indent=2, ensure_ascii=False))
    _update_state(article)
    return fname


def _update_state(article: dict[str, Any]) -> None:
    state = read_state()
    state["last_generated_at"] = datetime.now(tz=timezone.utc).isoformat()
    state["last_slug"] = article["slug"]
    state["total_generated"] = state.get("total_generated", 0) + 1
    history = state.get("history", [])
    history.append(
        {
            "slug": article["slug"],
            "title": article["title"],
            "score": article.get("_meta", {}).get("final_score"),
            "generated_at": state["last_generated_at"],
        }
    )
    state["history"] = history[-50:]
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def is_due(interval_hours: int = INTERVAL_HOURS) -> bool:
    """Has enough time elapsed since the last successful generation?"""
    state = read_state()
    last = state.get("last_generated_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    delta = datetime.now(tz=timezone.utc) - last_dt
    return delta.total_seconds() >= interval_hours * 3600


async def run_if_due(interval_hours: int = INTERVAL_HOURS) -> dict[str, Any] | None:
    """Generate one article iff the interval has elapsed. Returns the article
    or None when skipped. Errors are logged + swallowed so the scheduler
    survives transient LLM failures."""
    if not is_due(interval_hours):
        log.info("blog_generator: not due yet (interval=%dh)", interval_hours)
        return None
    try:
        article = await generate_one()
    except (LLMUnavailableError, ValidationError, RuntimeError) as e:
        log.exception("blog_generator: generation failed: %s", e)
        return None
    save_generated(article)
    log.info(
        "blog_generator: saved %s (score=%s, revisions=%s)",
        article["slug"],
        article.get("_meta", {}).get("final_score"),
        article.get("_meta", {}).get("revisions"),
    )
    return article


def load_generated_articles() -> list[dict[str, Any]]:
    """Read every JSON file in `generated/`. Skips state + malformed entries."""
    if not GENERATED_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(GENERATED_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            log.warning("blog_generator: skipping malformed %s", p)
    return out
