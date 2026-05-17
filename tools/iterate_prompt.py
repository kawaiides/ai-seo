"""Live prompt-iteration harness.

Runs a given prompt N times against the real OpenAI model configured in
.env, and writes raw responses to disk so we can analyse failure modes
for PROMPT_LOG.md.

Not part of the API. Run with:
    .venv/bin/python tools/iterate_prompt.py <version> <target_query> [n]

Outputs to tools/iteration_runs/<version>/run_<i>.json containing the
target_query, raw_response, and a quick parsed/validated boolean.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.llm_client import OpenAIClient  # noqa: E402

load_dotenv()


PROMPTS = {
    "v0_naive": {
        # Deliberately weak — no schema repetition, no fence ban, no
        # negative enumeration of common hallucinations. Used as an
        # ablation baseline so PROMPT_LOG can show what each defense buys.
        "system": "You generate sub-queries for AI search optimization.",
        "user_template": (
            'For the query "{target_query}", generate 10-15 sub-queries '
            "that an AI search engine would expand to. Categorize each as "
            "comparative, feature_specific, use_case, trust_signals, "
            "how_to, or definitional. Return JSON."
        ),
        "use_json_mode": False,
    },
    "v2": {
        # Iteration over v1 — adds per-type composition rules, intra-type
        # diversity, 13-15 nudge, target-echo ban, length-variance rule,
        # silent self-check epilogue, and a 13-entry few-shot. Mirrors
        # the prompt actually shipped in app/services/fanout_engine.py.
        "system": """You are a query decomposition engine for an AI search optimization tool.
Given a single TARGET_QUERY, you generate sub-queries that an AI search
engine (Perplexity, Google AI Overviews, ChatGPT Search) would fan out
to when constructing a comprehensive answer.

You output ONLY a single JSON object. No markdown fences, no prose, no
preamble, no explanation. The first character of your output must be `{`
and the last must be `}`.

The JSON object has exactly two keys:
  - "target_query":  string, echoing the input verbatim
  - "sub_queries":   array of 10 to 15 objects (prefer 13-15)

Each sub-query object has exactly two keys:
  - "type":   one of "comparative", "feature_specific", "use_case",
              "trust_signals", "how_to", "definitional"
  - "query":  a natural-language search query string, 4-15 words

Type definitions AND per-type composition rules:
  - comparative:      compares the target against named alternatives.
                      MUST name >=1 specific competitor, product, or
                      method by proper name. Prefer "X vs Y" or
                      "X or Y" phrasing.
  - feature_specific: focuses on one specific capability, integration,
                      or technical attribute. MUST name the capability
                      concretely (e.g. "with SAML SSO", "supporting
                      Postgres 16", "real-time SERP analysis"), not a
                      vague descriptor ("with good features").
  - use_case:         a concrete real-world application bound to a
                      persona, team size, industry, or scenario
                      (e.g. "for a 5-person agency", "for solo
                      real-estate agents"). Generic "for businesses"
                      is NOT acceptable.
  - trust_signals:    reviews, case studies, ratings, expert
                      credibility. MUST include either a year stamp
                      (2025 or 2026) OR a named source/medium
                      (G2, Reddit, Capterra, peer-reviewed study,
                      analyst report, case study).
  - how_to:           procedural / instructional. MUST start with
                      "how to" followed by an action verb (set up,
                      migrate, configure, integrate, evaluate, etc.).
  - definitional:     conceptual. MUST start with one of "what is",
                      "what are", "define", "meaning of", or
                      "difference between". No buying-intent phrasing.

Hard constraints:
  - Total sub-queries: 10-15 inclusive. Prefer 13-15; the floor of 10
    is reserved for narrow, single-facet topics.
  - Every type above must appear AT LEAST 2 times.
  - Within a single type, the 2+ queries must differ in entity,
    angle, persona, or sub-feature - not just paraphrase. If you
    cannot produce a genuinely different second query, switch to a
    different angle entirely; do not pad.
  - Each "query" string must be unique across the whole array.
  - Do NOT echo the TARGET_QUERY verbatim as a sub-query, and avoid
    starting most sub-queries with the same noun phrase as the target.
  - Vary query length across the 4-15 word band; do not cluster all
    queries at the same length.
  - Do NOT add any other top-level keys. Do NOT add keys inside
    sub-query objects. No "id", no "rationale", no "score", no
    "category", no "sub_query".
  - Do NOT wrap the JSON in ```json fences or any other markup.

Before emitting, silently verify (do not show your work):
  1. Total is 10-15 (preferably 13-15).
  2. Each of the 6 types appears >=2 times.
  3. Every per-type composition rule above is satisfied.
  4. No duplicates; no verbatim target_query as a sub-query.
  5. JSON is well-formed with exactly the keys specified.
""",
        "user_template": """TARGET_QUERY: "{target_query}"

Example for the unrelated target query "best CRM software for small business" (13 sub-queries; note the variety in length, phrasing, and named entities):
{{
  "target_query": "best CRM software for small business",
  "sub_queries": [
    {{"type": "comparative",      "query": "HubSpot vs Salesforce Starter for a 10-seat team"}},
    {{"type": "comparative",      "query": "Pipedrive or Zoho CRM under $30 per user"}},
    {{"type": "comparative",      "query": "Folk CRM versus Attio for early-stage founders"}},
    {{"type": "feature_specific", "query": "CRM with native QuickBooks two-way sync"}},
    {{"type": "feature_specific", "query": "small business CRM offering SOC 2 Type II compliance"}},
    {{"type": "use_case",         "query": "CRM for a solo real-estate agent juggling 200 active leads"}},
    {{"type": "use_case",         "query": "CRM for a 6-person B2B SaaS startup running outbound"}},
    {{"type": "trust_signals",    "query": "highest-rated SMB CRMs on G2 in 2025"}},
    {{"type": "trust_signals",    "query": "Capterra reviews of CRMs for under-50-employee firms 2026"}},
    {{"type": "how_to",           "query": "how to migrate contacts from spreadsheets to a CRM in a weekend"}},
    {{"type": "how_to",           "query": "how to configure a 3-stage B2B sales pipeline end to end"}},
    {{"type": "definitional",     "query": "what is a CRM and how does it differ from a contact database"}},
    {{"type": "definitional",     "query": "meaning of pipeline velocity in small-business sales software"}}
  ]
}}

Now generate the equivalent JSON object for TARGET_QUERY. Output JSON only.""",
        "use_json_mode": True,
    },
    "v1": {
        "system": """You are a query decomposition engine for an AI search optimization tool.
Given a single TARGET_QUERY, you generate sub-queries that an AI search
engine (Perplexity, Google AI Overviews, ChatGPT Search) would internally
fan out to in order to construct a comprehensive answer.

You output ONLY a single JSON object. No markdown fences, no prose, no
preamble, no explanation. The first character of your output must be `{`
and the last must be `}`.

The JSON object has exactly two keys:
  - "target_query":  string, echoing the input verbatim
  - "sub_queries":   array of 10 to 15 objects

Each sub-query object has exactly two keys:
  - "type":   one of "comparative", "feature_specific", "use_case",
              "trust_signals", "how_to", "definitional"
  - "query":  a natural-language search query string, 4-15 words

Type definitions:
  - comparative:      compares the target against named alternatives
  - feature_specific: focuses on a specific capability or technical attribute
  - use_case:         a concrete real-world application, persona, or scenario
  - trust_signals:    reviews, case studies, ratings, year-stamped credibility
  - how_to:           procedural, instructional, "how to ..." phrasing
  - definitional:     conceptual, "what is ...", "define ..." phrasing

Hard constraints:
  - Total sub-queries: between 10 and 15 inclusive.
  - Every type above must appear AT LEAST 2 times.
  - Each "query" string must be unique.
  - Do NOT add any other top-level keys. Do NOT add keys inside
    sub-query objects. No "id", no "rationale", no "score".
  - Do NOT wrap the JSON in ```json fences or any other markup.
""",
        "user_template": """TARGET_QUERY: "{target_query}"

Example for the unrelated target query "best CRM software for small business":
{{
  "target_query": "best CRM software for small business",
  "sub_queries": [
    {{"type": "comparative",      "query": "HubSpot vs Salesforce for small business CRM"}},
    {{"type": "comparative",      "query": "Pipedrive or Zoho CRM for 10-person sales team"}},
    {{"type": "feature_specific", "query": "small business CRM with built-in email sequencing"}},
    {{"type": "feature_specific", "query": "CRM software with QuickBooks integration"}},
    {{"type": "use_case",         "query": "CRM for a solo real estate agent managing 200 leads"}},
    {{"type": "use_case",         "query": "CRM for a 5-person B2B SaaS startup"}},
    {{"type": "trust_signals",    "query": "small business CRM reviews on G2 in 2025"}},
    {{"type": "trust_signals",    "query": "CRM software case studies for sub-50 employee firms"}},
    {{"type": "how_to",           "query": "how to migrate from spreadsheets to a small business CRM"}},
    {{"type": "how_to",           "query": "how to set up a sales pipeline in a small business CRM"}},
    {{"type": "definitional",     "query": "what is a CRM and what does it do for small businesses"}},
    {{"type": "definitional",     "query": "what features define an SMB-focused CRM platform"}}
  ]
}}

Now generate the equivalent JSON object for TARGET_QUERY. Output JSON only.""",
        "use_json_mode": True,
    },
}


async def run_one(
    client: OpenAIClient, system: str, user: str, use_json_mode: bool
) -> str:
    """Direct OpenAI call so we can toggle JSON mode for the ablation."""
    import openai

    kwargs = {
        "model": client.model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if use_json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client._client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content
    if not content:
        raise openai.OpenAIError("empty content")
    return content


async def main() -> None:
    if len(sys.argv) < 3:
        print("usage: iterate_prompt.py <version> <target_query> [n]", file=sys.stderr)
        sys.exit(2)
    version = sys.argv[1]
    target_query = sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) >= 4 else 5

    if version not in PROMPTS:
        print(f"unknown version {version}; available: {list(PROMPTS)}", file=sys.stderr)
        sys.exit(2)

    p = PROMPTS[version]
    user = p["user_template"].format(target_query=target_query)

    safe_topic = "".join(c if c.isalnum() else "_" for c in target_query)[:40]
    out_dir = Path(__file__).parent / "iteration_runs" / version / safe_topic
    out_dir.mkdir(parents=True, exist_ok=True)

    client = OpenAIClient()
    print(f"[model={client.model_id}] running {n} iterations of {version} for {target_query!r}")

    use_json_mode = p.get("use_json_mode", True)
    for i in range(1, n + 1):
        try:
            raw = await run_one(client, p["system"], user, use_json_mode)
            parsed_ok = True
            try:
                json.loads(raw)
            except json.JSONDecodeError:
                parsed_ok = False
            (out_dir / f"run_{i}.json").write_text(
                json.dumps(
                    {
                        "target_query": target_query,
                        "raw_response": raw,
                        "parsed_ok": parsed_ok,
                        "model": client.model_id,
                    },
                    indent=2,
                )
            )
            print(f"  run {i}: parsed_ok={parsed_ok}, len={len(raw)}")
        except Exception as e:
            (out_dir / f"run_{i}.error.txt").write_text(f"{type(e).__name__}: {e}")
            print(f"  run {i}: ERROR {type(e).__name__}: {e}")

    print(f"saved to {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
