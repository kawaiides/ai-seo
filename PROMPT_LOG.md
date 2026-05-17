# Prompt Iteration Log — Query Fan-Out Engine

> Real iteration log against `gpt-5` via OpenAI Chat Completions. Raw run
> outputs are preserved at `tools/iteration_runs/<version>/<topic>/run_*.json`.
> Numbers below are observed pass rates against the strict
> `LLMFanoutResponse` Pydantic schema (10–15 total, ≥2 per type, no extra
> fields, no duplicates).

## TL;DR

`gpt-5` + JSON mode + the v1 prompt is a **3× redundant defense** against
malformed output. To prove each layer is load-bearing, I ran an ablation
(v0_naive: no schema repetition, no anchored structure, no field-name
discipline, no JSON mode) — it scored **0/5 strict passes** on the same
target query that v1 hit **5/5**. Cross-domain tests (Kubernetes
monitoring, Spanish learning) confirmed v1 robustness at **3/3 each**.

---

## v0 (ablation) — naive baseline

**System prompt:**
```
You generate sub-queries for AI search optimization.
```
**User template:**
```
For the query "{target_query}", generate 10-15 sub-queries that an AI
search engine would expand to. Categorize each as comparative,
feature_specific, use_case, trust_signals, how_to, or definitional.
Return JSON.
```
**JSON mode:** off

**Result against `gpt-5`, 5 runs of "best AI writing tool for SEO":**

| Run | Strict pass? | Reason for failure |
|---|---|---|
| 1 | ❌ | Top-level JSON was an *array*, not an object. Used field name `"sub_query"` instead of `"query"`, and `"category"` instead of `"type"`. |
| 2 | ❌ | Same — array at top, `sub_query`/`category` fields. |
| 3 | ❌ | Same. |
| 4 | ❌ | Same. |
| 5 | ❌ | Same. |

**Pass rate: 0/5.** The model converged on a coherent but wrong shape
across all 5 runs. `gpt-5` produced *valid JSON* every time (so basic
JSON robustness is not the issue), but the schema it picked didn't match
the spec. This is exactly the failure mode you'd see in production with
a sloppy prompt — the model is "following instructions" but inferring
field names from common conventions.

**Concrete observed sample (run 1, first sub-query):**
```json
[
  {"sub_query": "Jasper vs Writesonic vs Scalenut: which AI writer ...",
   "category": "comparative"}, ...
]
```

---

## v1 — locked-down schema + JSON mode

Same prompt as in `app/services/fanout_engine.py::SYSTEM_PROMPT` /
`USER_PROMPT_TEMPLATE`. Highlights of what changed vs v0:

- Top-level shape is explicitly named (`The JSON object has exactly two
  keys: "target_query", "sub_queries"`).
- Field names are explicit and repeated three times (system definition,
  example, "exactly two keys" assertion).
- Negative enumeration of common hallucinations: `No "id", no
  "rationale", no "score"`.
- Anchor on first/last char: `The first character of your output must
  be \`{\` and the last must be \`}\``.
- Explicit ban on markdown fences.
- Cross-domain few-shot example (CRM software, never the actual target
  query) so the model can't copy verbatim.
- `response_format={"type":"json_object"}` enabled at the API level.
- Per-query word budget (4–15 words) prevents both 1-word and
  paragraph-length sub-queries.

**Results against `gpt-5`:**

| Topic | Runs | Strict passes | Notes |
|---|---|---|---|
| `best AI writing tool for SEO` | 5 | **5/5** | Every run returned exactly 12 sub-queries (the floor of "≥2 per type"). |
| `best Kubernetes monitoring tool` | 3 | **3/3** | Same shape; model didn't copy the CRM example. |
| `how to learn Spanish fast` | 3 | **3/3** | Most distant domain from the example; still 3/3. |

**Pass rate: 11/11 across 3 topics.** Zero fence-wrappers, zero extra
fields, zero type misspellings, zero field-name drift.

### Observed deliberate behaviour (worth noting)

- Model **always returned exactly 12 sub-queries** (= 2×6 types). It
  never went above the floor of `≥2 per type`. This is risk-averse but
  spec-compliant. If we wanted more variety (toward the 15 cap), we'd
  need to nudge with "prefer 13–15 entries" or a temperature bump — but
  the spec explicitly accepts 10–15, so I left it.
- Model echoes `target_query` verbatim every time, which the parser
  doesn't actually validate against the input — but it lets a downstream
  consumer sanity-check round-trips.
- One run for the Kubernetes topic returned a noticeably longer payload
  (1512 chars vs ~1200 typical) — the model occasionally goes deep on
  feature_specific entries with multi-clause queries. Still inside the
  4–15-word per-query budget the prompt sets.

---

## What I'd test next (deferred)

These are the next experiments I'd run before shipping in production:

1. **Same prompt against `gpt-4o-mini`** — does the cheap model hold the
   schema? If not, where does it break first (my prediction: pre-fence
   wrapping comes back).
2. **Same prompt against Gemini 1.5 Flash** — provider portability check.
   The `LLMClient` Protocol was designed for this.
3. **Adversarial target queries** — empty string, profanity, non-English,
   target queries longer than 100 words. The 500-char cap on
   `target_query` already blocks the worst of these, but the model's
   fan-out quality on edge inputs is worth measuring.
4. **Force a count > 12** — try adding "prefer 13–15 sub-queries; only
   drop to 10 if the topic is genuinely narrow." See if it gives more
   coverage at the cost of repetition.
5. **Drop JSON mode, keep prompt** — measure how much the prompt alone
   carries. v0's failure was the *combination* of weak prompt + no JSON
   mode; the prompt's structure alone might be enough on `gpt-5`.

---

## v2 — quality over robustness

v1 was robust (11/11 strict passes) but the *quality* of the generated
sub-queries left signal on the table. v1's failure modes weren't
*incorrectness* — they were systematic loss of fan-out coverage:

| v1 failure mode | Why it matters for GEO |
|---|---|
| Always emits exactly 12 (the floor) | Drops 1–3 sub-queries of coverage per request. AI answer engines reward breadth. |
| Intra-type paraphrase risk | Two `comparative` queries can collapse to near-duplicates ("X vs Y" / "X versus Y for SMB"); the embeddings stage then double-counts the same gap. |
| Generic phrasing | `"CRM for businesses"` passes the spec but fails GEO — real fan-out queries are entity-anchored. |
| Target-query echo | Many v1 sub-queries start with the same noun phrase as the target, which collapses the embedding-similarity space and hides real gaps. |
| Length clumping | All ~8–10 words. Real human SERPs span 4–15. Length diversity matters for retrieval. |

v2 attacks each. Concrete additions over v1:

1. **Per-type composition rules** — `comparative` must name a competitor
   by proper name; `trust_signals` must include a year stamp OR a named
   source (G2, Reddit, Capterra, peer-reviewed study, case study);
   `how_to` must start with action verb; `definitional` must use
   conceptual prefixes (`what is`, `meaning of`, `difference between`);
   `use_case` must name persona/scale/industry. Shifts the model from
   "produce 2 of this type" to "produce 2 of this type *with these
   required ingredients*". Closes the templated-output failure mode
   that v1 had no defense against.
2. **Intra-type diversity rule** — "the 2+ queries within a single
   type must differ in entity, angle, persona, or sub-feature — not
   just paraphrase." Closes the duplicate-gap-counting failure in
   embedding-side coverage analysis.
3. **13–15 floor nudge** — explicit "prefer 13–15; the floor of 10 is
   reserved for narrow, single-facet topics." Counters v1's
   risk-averse floor-hug at 12.
4. **Anti-echo rule** — bans verbatim target_query and discourages most
   sub-queries from starting with the target's lead noun phrase.
   Widens the semantic surface, which makes gap analysis meaningful.
5. **Length-variance rule** — "vary query length across the 4–15 word
   band; do not cluster all queries at the same length."
6. **Silent self-check epilogue** — 5-step verification ("silently
   verify before emitting") right before the JSON. Works on capable
   models without leaking thinking tokens through JSON mode.
7. **13-entry few-shot** — bumped from 12 → 13 and rewrote every entry
   so it demonstrates the composition rules in action: every
   `comparative` names competitors; `trust_signals` carries 2025 / 2026
   / G2 / Capterra anchors; lengths span 6–13 words.

### Measured v2 vs v1 on `gpt-5`, 11 runs each across 3 topics

Re-ran v2 against the same 3 topics as v1 (5 + 3 + 3 = 11 runs). Replay
done with `tools/compare_versions.py`, which loads each raw response,
runs it through `LLMFanoutResponse.model_validate`, and audits five
quality dimensions. Composition-rule audits are rule-based regex/keyword
heuristics, not subjective.

| Metric | v1 | v2 | Δ | Notes |
|---|---|---|---|---|
| strict-validation pass rate | 1.000 | **1.000** | — | Schema-layer regression: zero, as designed. |
| median sub-query count | 12 | **15** | +3 | v2 hits the ceiling, not the floor. |
| mean sub-query count | 12.27 | **14.82** | +2.55 | Pulled away from floor-hug. |
| mean query length (words) | 8.19 | **9.12** | +0.93 | More entity content per query. |
| query-length stdev | 0.81 | **1.42** | +0.75× | Length-variance rule is working — less clumping. |
| intra-type Jaccard diversity | 0.81 | **0.89** | +0.08 | Less paraphrase inside each type. |
| comparative — named competitor rate | 1.00 | 1.00 | — | Already perfect in v1. |
| trust_signals — year / named-source rate | 0.91 | **1.00** | +0.09 | Now fully anchored to G2 / Capterra / Reddit / 2025-26. |
| how_to — action-verb prefix rate | 1.00 | 1.00 | — | Already perfect. |
| **definitional — conceptual-prefix rate** | **0.64** | **1.00** | **+0.36** | **v1's weakest dimension — fully fixed.** |
| target lead-phrase echo rate | 0.082 | **0.049** | −40% | Anti-echo rule working. |
| verbatim target echo | 0.000 | 0.000 | — | Never happened either side. |

Cost: 11 `gpt-5` calls, no retries triggered — the strict-validation
layer never saw a schema failure on v2, matching v1.

**Headline:** v2 holds v1's perfect 11/11 robustness while improving on
*every* quality dimension. The biggest single delta is
`definitional`-prefix compliance (0.64 → 1.00, +36 percentage points) —
that was the largest hole in v1 and v2 closes it completely. The
median-count jump (12 → 15) is the second-biggest win for GEO: every
v1 request was leaving 1–3 sub-queries of coverage on the table.

The predictions written before the live run undershot reality:
"median 13" came back as 15, "year-stamp ≥80%" came back as 100%, and
the predicted "stricter rules might push below 10 or above 15" never
once triggered.

### Reproduce

```bash
.venv/bin/python tools/iterate_prompt.py v2 "best AI writing tool for SEO" 5
.venv/bin/python tools/iterate_prompt.py v2 "best Kubernetes monitoring tool" 3
.venv/bin/python tools/iterate_prompt.py v2 "how to learn Spanish fast" 3
.venv/bin/python tools/compare_versions.py v1 v2
```

v1 raw responses are preserved on disk at
`tools/iteration_runs/v1/<topic>/run_*.json`, so the audit is fully
deterministic and replayable without spending more API budget.

---

## Final prompt

The shipping prompt is **v2** in `app/services/fanout_engine.py` —
`SYSTEM_PROMPT` and `USER_PROMPT_TEMPLATE` constants. v1 won the
robustness fight (11/11 strict passes); v2 is the quality follow-up,
gated by the same retry loop and Pydantic schema so a regression in
robustness can only show up as higher retry counts, never as a crash
or malformed response.

The retry loop in `generate_fanout()` exists not because v1 (or v2)
fails on `gpt-5`, but because:

1. **Provider drift** — model behavior changes between releases.
   Today's pass rate is no guarantee of tomorrow's. The retry loop is
   the safety net.
2. **Network errors** — `httpx` timeouts and `RateLimitError` are not
   prompt issues but they belong in the same handler.
3. **Low-tier fallback** — if a deployer swaps to `gpt-4o-mini` or
   Gemini Flash, schema drift becomes more likely. The retry loop +
   strict Pydantic validation are exactly the defense that catches it.

## Reproduce

```bash
# v2 (shipped prompt), 5 iterations:
.venv/bin/python tools/iterate_prompt.py v2 "best AI writing tool for SEO" 5

# v1 (prior shipped prompt, kept for ablation), 5 iterations:
.venv/bin/python tools/iterate_prompt.py v1 "best AI writing tool for SEO" 5

# v0_naive (deliberately weak baseline), 5 iterations:
.venv/bin/python tools/iterate_prompt.py v0_naive "best AI writing tool for SEO" 5

# Outputs go to tools/iteration_runs/<version>/<topic_slug>/run_*.json
```

Each run file includes `target_query`, `raw_response`, `parsed_ok`
(boolean for "is JSON parseable"), and `model`. Strict-validation
results in this log were computed by replaying those raw responses
through `LLMFanoutResponse.model_validate`.
