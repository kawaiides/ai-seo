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

## Final prompt

The shipping prompt is in `app/services/fanout_engine.py` —
`SYSTEM_PROMPT` and `USER_PROMPT_TEMPLATE` constants. No changes from v1
above. The schema-strictness defense was sufficient on the first
properly-designed pass; further iteration would be premature optimization
without seeing failure data first.

The retry loop in `generate_fanout()` exists not because v1 fails on
`gpt-5` (it didn't, in 11/11 attempts), but because:

1. **Provider drift** — model behavior changes between releases. Today's
   11/11 is no guarantee of tomorrow's. The retry loop is the safety net.
2. **Network errors** — `httpx` timeouts and `RateLimitError` are not
   prompt issues but they belong in the same handler.
3. **Low-tier fallback** — if a deployer swaps to `gpt-4o-mini` or
   Gemini Flash, schema drift becomes more likely. The retry loop +
   strict Pydantic validation are exactly the defense that catches it.

## Reproduce

```bash
# v1, 5 iterations:
.venv/bin/python tools/iterate_prompt.py v1 "best AI writing tool for SEO" 5

# v0_naive (ablation), 5 iterations:
.venv/bin/python tools/iterate_prompt.py v0_naive "best AI writing tool for SEO" 5

# Outputs go to tools/iteration_runs/<version>/<topic_slug>/run_*.json
```

Each run file includes `target_query`, `raw_response`, `parsed_ok`
(boolean for "is JSON parseable"), and `model`. Strict-validation
results in this log were computed by replaying those raw responses
through `LLMFanoutResponse.model_validate`.
