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
