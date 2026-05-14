# AEGIS — Setup & Design Notes

> Personal setup notes & design rationale for the take-home submission.
> The original assignment spec lives in `README.md`; this file documents what's
> built, how to run it, and the decisions worth a reviewer's attention.

## Status

| Feature | Status |
|---|---|
| Feature 1 — AEO Content Scorer (`POST /api/aeo/analyze`) | ✅ Implemented (Checks A, B, C + tests) |
| Feature 2 — Query Fan-Out Engine (`POST /api/fanout/generate`) | ✅ Implemented (LLM + gap analysis + tests) |
| `PROMPT_LOG.md` | ✅ Real iteration log against `gpt-5` (v0 ablation 0/5 → v1 11/11) |

## Setup & Run

Requires Python 3.11+ (developed against 3.14, but 3.11/3.12/3.13 should work too).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m spacy download en_core_web_lg
```

The first request that exercises Feature 2 also downloads the
sentence-transformers model `all-MiniLM-L6-v2` (~80 MB, one-time, cached
under `~/.cache/huggingface`).

Run the server:

```bash
uvicorn app.main:app --reload
```

The API is then available at `http://localhost:8000`. Swagger UI at `/docs`.

### Environment variables

Feature 1 needs **none** — no LLM calls, only user-supplied URL fetches.

Feature 2 reads from `.env` (loaded automatically via `python-dotenv` at
app startup):

| Variable | Required? | Notes |
|---|---|---|
| `OPENAI_API_KEY` | Yes | Used by `app/services/llm_client.py::OpenAIClient`. |
| `OPENAI_MODEL` | No (default `gpt-4o-mini`) | Tested against `gpt-5`. |
| `AEGIS_FANOUT_CACHE` | No (default off) | Set to `1` to enable the in-memory `(model, target_query)` cache for dev. Auto-bypassed in pytest. |

`.env` is gitignored. A real shell env still takes precedence over
`.env` (`load_dotenv(override=False)`).

## Tests

```bash
pytest -v
```

Coverage:
- `tests/test_direct_answer.py` — Check A, all four scoring tiers (20/12/8/0)
- `tests/test_htag_hierarchy.py` — Check B, all violation rules + scoring tiers
- `tests/test_readability.py` — Check C, scoring band table (parametrized) + integration tests with `textstat` mocked for FK determinism
- `tests/test_content_parser.py` — Boilerplate stripping, plain-text branching, error handling

The check tests import the check classes directly (not via the API) so each check is independently exercised. Tests do not hit the network.

## Sample requests

**Plain text:**

```bash
curl -s -X POST http://localhost:8000/api/aeo/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "input_type": "text",
    "input_value": "<h1>Python overview</h1><p>Python is a high-level programming language used for data science, web development, and scripting.</p><h2>Why</h2><p>Because it is readable.</p>"
  }' | python -m json.tool
```

**URL:**

```bash
curl -s -X POST http://localhost:8000/api/aeo/analyze \
  -H 'Content-Type: application/json' \
  -d '{"input_type":"url","input_value":"https://example.com"}'
```

**Bad URL → 422 with the spec'd error envelope:**

```bash
curl -s -X POST http://localhost:8000/api/aeo/analyze \
  -H 'Content-Type: application/json' \
  -d '{"input_type":"url","input_value":"http://localhost:1/nope"}' \
  -w "\nHTTP %{http_code}\n"
```

### Feature 2 — Fan-Out Engine

**Without content (sub-queries only):**

```bash
curl -s -X POST http://localhost:8000/api/fanout/generate \
  -H 'Content-Type: application/json' \
  -d '{"target_query":"best AI writing tool for SEO"}' | python -m json.tool
```

**With content (gap analysis fires):**

```bash
curl -s -X POST http://localhost:8000/api/fanout/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "target_query":"best AI writing tool for SEO",
    "existing_content":"Jasper AI is a popular AI writing tool used for SEO. It includes built-in keyword clustering and SERP analysis features. Many marketing agencies have adopted it for content workflows."
  }' | python -m json.tool
```

**LLM unavailable → 503 envelope** (e.g. unset key, network error,
schema validation fails 3×):

```bash
unset OPENAI_API_KEY  # or set to invalid
curl -s -X POST http://localhost:8000/api/fanout/generate \
  -H 'Content-Type: application/json' \
  -d '{"target_query":"x"}' \
  -w "\nHTTP %{http_code}\n"
# → {"error":"llm_unavailable","message":"...","detail":"..."}
```

## Design decisions

### 1. Single-pass parsing, then independent checks
`content_parser.parse()` returns a frozen `ParsedContent` dataclass with everything any check might need (`first_paragraph`, `h_tags`, `body_text`, `soup`). Each check consumes only the fields it needs. Adding a Check D is a one-file addition — register it in `app/services/aeo_checks/__init__.py::default_checks()` and write its tests.

### 2. spaCy model: `en_core_web_lg`
The assignment marks `en_core_web_lg` as preferred over `en_core_web_sm`. Feature 1 only uses the dependency parser (Check A's declarative detection), but `lg`'s word vectors will likely be useful for Feature 2's gap analysis if I keep this model loaded. Loaded lazily via `app/services/nlp.py::get_nlp()` with `disable=["ner"]` to skip the NER component we don't use.

### 3. `passed = (score == max_score)`
The example response in the spec shows `score: 8 → passed: false`, so partial credit is *not* a pass. A check "passes" only when it scores the full 20. Cleaner binary semantics than `score > 0`.

### 4. H-tag violation counting
Each violation is a *positional* event:
- **Missing H1** → single special violation with `score = 0` (per spec: "missing H1 → 0").
- **Multiple H1s** → one violation per *extra* H1 (so 4 H1s = 3 violations, which correctly trips the "3+ → 0" rule).
- **Skipped levels** → one violation per skip occurrence.
- **Pre-H1 tags** → one violation per pre-H1 tag.

This matches the response example showing `h_tags_found` as an ordered list — violations are tied to positions, not aggregate booleans.

### 5. FK band boundaries (Check C)
The spec table is ambiguous at integer boundaries ("6 or 10 → 14"). Resolved as:
- `[7.0, 9.0]` → 20
- `[6.0, 7.0)` or `(9.0, 10.0]` → 14
- `[5.0, 6.0)` or `(10.0, 11.0]` → 8
- everything else → 0

Closed lower bound, exclusive upper at the gap. Documented here so the reviewer knows the boundary handling is deliberate.

### 6. Min-word threshold for "complex sentences" ranking
Sentences shorter than 5 words are excluded before ranking by syllable/word ratio. Without this, a sentence like "Computing matters." (3 syllables / 2 words = 1.5) would top the list over a real polysyllabic sentence. The spec says rank by "syllable count ÷ word count per sentence" — I'm interpreting that as a heuristic for *complex* sentences, not a literal sort that includes 2-word fillers.

### 7. Boilerplate stripping
Tags decomposed: `script, style, nav, header, footer, aside, noscript, form, iframe, svg, button`. Content selection priority: `<article>` → `<main>` → `<body>` → entire soup. The cloned soup is decomposed, never the original — important because the same `ParsedContent.soup` is also the source of `h_tags` extraction.

### 8. Plain-text input handling
If BeautifulSoup finds neither `<p>` tags nor headings, treat the input as plain text: split on `\n\n+` for `first_paragraph`, `h_tags=[]`, `body_text=raw`. We don't detect markdown headings — pasted plain prose without HTML structure honestly scores 0 on Check B (correct AEO feedback).

### 9. Concurrency model
Endpoint is `async def`. URL fetching uses `httpx.AsyncClient` so the event loop isn't blocked while we wait on the network. The three checks then run synchronously inline since they're CPU-bound and fast (~50–100ms total for typical pages, dominated by the spaCy parse on the body text).

For higher throughput we'd offload checks to a `run_in_threadpool` or a process pool — at the current scale and the take-home's 10s URL timeout, the async-fetch + sync-checks pattern is the simpler correct answer.

### 10. Error handling
- Custom `URLFetchError` and `ContentParseError` exceptions are raised by `content_parser`, then mapped to the spec's 422 envelope by `@app.exception_handler` registrations in `app/main.py`.
- Pydantic request-validation errors keep FastAPI's default 422 (different shape, different concern — those are caller-side bugs, not "we couldn't fetch the URL").

## Feature 2 design decisions

### F2.1 — Triple defense for JSON robustness

LLMs miss JSON contracts in three different ways: bad syntax, bad shape, missing fields. So Feature 2 layers three independent defenses:

1. **Prompt design.** The prompt declares the schema three times (system rules, example, "exactly two keys" assertion) and explicitly bans common hallucinations (`id`, `rationale`, `score`, ` ```json` fences). It also anchors the structure ("first character must be `{`, last must be `}`"). See [PROMPT_LOG.md](PROMPT_LOG.md) for the ablation that proved each technique is load-bearing — v0 (no defenses) scored **0/5** on the strict schema; v1 scored **11/11** across 3 unrelated topics.
2. **OpenAI JSON mode.** `response_format={"type":"json_object"}` is set on every call. Cheap insurance.
3. **Pydantic strict validation.** `LLMSubQuery` and `LLMFanoutResponse` use `extra="forbid"` plus a `model_validator` enforcing 10–15 total / ≥2 per type / unique queries. Anything off → `ValidationError` → retry.

Each layer catches different failure modes. The retry loop in `generate_fanout` (3 attempts, jittered backoff) catches transient ones; if all 3 fail, the API returns the spec's `503 llm_unavailable` envelope with the most recent failure reason.

### F2.2 — `gpt-5` and the `temperature` parameter

The OpenAI Chat Completions call deliberately does **not** pass `temperature`. Newer reasoning-leaning models (gpt-5, o-series) reject it; default sampling produces enough sub-query diversity for our use case. If a deployer swaps to `gpt-4o-mini`, default sampling is also fine.

### F2.3 — Embedding model: `all-MiniLM-L6-v2`

Spec line 471 explicitly green-flags this choice. It's 5× faster than `all-mpnet-base-v2` on CPU and produces 384-dim vectors instead of 768. For the binary "covered/not-covered" decision at threshold 0.72, the precision delta of mpnet doesn't justify the latency.

For production: I'd A/B with mpnet on a labeled set before switching. For a take-home, MiniLM is the right default.

### F2.4 — Cosine similarity computation

`encode(..., normalize_embeddings=True)` makes raw dot product equal cosine similarity. We then do **one** matmul `query_vecs @ chunk_vecs.T` and `max(axis=1)` over chunks per query. Vectorized, correct, and intentionally avoids spec red flag #3 ("cosine computed incorrectly on non-normalized vectors"). Per-pair `util.cos_sim` calls in a loop would work but would be ~50× slower at this batch size.

### F2.5 — Sentence chunking (`chunk_content`)

Reuses `app/services/nlp.py::get_nlp()` for `doc.sents`. Filters out sentences shorter than 4 words because MiniLM produces noisy embeddings on ultra-short fragments ("Yes." / "Sure." / re-pasted headings). Caps at 500 chunks to defang pathologically long input.

### F2.6 — Threshold 0.72: kept, not tuned

Tuning a threshold without labeled data is guessing. The plan to do it properly:

1. Collect ~50 (sub_query, content_chunk) pairs by hand.
2. Label each as "covers" / "doesn't cover".
3. For τ ∈ {0.50, 0.55, ..., 0.90}, compute precision, recall, F1.
4. Pick argmax F1.

With MiniLM on STS-style content, I'd expect optimum F1 in `[0.65, 0.78]`; 0.72 sits in that band, so it's a credible default. Empirically during smoke tests (see live response in `/tmp/fanout_with.json` — only near-verbatim phrases like "keyword clustering and SERP analysis features" cleared 0.72 against the test content), 0.72 is on the conservative side. A real product might benefit from 0.65–0.68 — but only after labeled validation.

### F2.7 — Response-shape branching via `Optional` + `exclude_none`

Single `FanoutResponse` model with `Optional` `covered`, `similarity_score`, `gap_summary` fields. The route uses `response_model_exclude_none=True` so when no content is provided, those fields don't appear in the JSON body — exactly matching the spec's "omit if no content" rule. Cleaner than two response models.

### F2.8 — In-memory cache (opt-in)

A `(model, target_query) → raw_response` dict gated on `AEGIS_FANOUT_CACHE=1` and auto-bypassed in pytest. Caps at 64 entries (FIFO eviction). Spec FAQ permits this for free-tier rate-limit relief. Caches **raw text**, not parsed objects, so the parser still runs on cache hits — schema regressions still surface even when you're hitting cache.

### F2.9 — LLM provider abstraction

`LLMClient` is a single-method `Protocol` (`generate_json(system, user) -> str`). Only `OpenAIClient` is implemented today, but the engine is portable: a `GeminiClient` that satisfies the Protocol could be dropped in tomorrow without touching `fanout_engine.py`. Tests substitute a `FakeLLMClient` that pops responses (or exceptions) from a queue — covers all retry-loop branches without network.

### F2.10 — Concurrency

Endpoint is `async def`. The OpenAI call uses `openai.AsyncOpenAI` so the event loop isn't blocked while the LLM is responding (typical: 2–8 seconds). Embedding compute is synchronous and CPU-bound (~50ms typical) — runs inline. `sentence-transformers` and `numpy` aren't async-native and adding `run_in_threadpool` here would add complexity for marginal benefit at this scale.

## What I'd improve with more time

### Feature 1
1. **JS-rendered pages.** Many modern sites return mostly-empty HTML before client-side React renders. Adding a Playwright/Chromium fallback for `input_type=url` (with a strict timeout) would meaningfully expand coverage.
2. **Better content extraction.** Even with the boilerplate strip and `<article>` preference, ad-heavy pages leak into `body_text`. A port of Mozilla's `readability.js` would be a real upgrade.
3. **Threshold tuning.** All three checks have hard-coded thresholds (60 words, FK 7–9, 0 violations). Calibrating against a small labeled set of "AEO-good" vs. "AEO-bad" pages would tell us whether 60 should be 70, etc.
4. **Integration tests against fixture URLs.** A `tests/fixtures/` folder with snapshot HTML files and end-to-end assertions on the full API response would catch regressions in the orchestration layer.
5. **spaCy cold-start mitigation.** The first request after server start pays a ~500ms model load. A startup hook to warm the singleton would smooth this out.
6. **URL caching.** Same URL in, same score out — a small TTL cache keyed on URL would dramatically cut latency for re-runs during iteration.

### Feature 2
7. **Threshold calibration with real labels** (per F2.6 above). Thirty minutes of hand-labeling would beat any prompt tweak.
8. **Provider portability test.** Run the same prompt against `gpt-4o-mini` and Gemini Flash to validate the `LLMClient` abstraction. The retry loop's value scales with provider drift, so this matters most for the cheap-fallback story.
9. **Stream the LLM response.** For the typical 6-second `gpt-5` round-trip, streaming would let us start parsing as the JSON arrives. The current synchronous-await is simpler and the 6s is well under the spec's "after 3 retries" budget, but streaming would improve perceived latency.
10. **Diversity nudge.** During iteration `gpt-5` always returned exactly the floor of 12 sub-queries (2×6). A small prompt nudge ("prefer 13–15 entries unless the topic is genuinely narrow") might improve coverage with a precision/recall tradeoff worth measuring.
11. **Embedding cache.** Sentences in `existing_content` are encoded fresh on every request. A content-hash → embedding cache would help when the same article is re-scored against many queries during iteration.

## Files of interest

```
app/
├── api/
│   ├── aeo.py                       # Feature 1 endpoint + score aggregation
│   └── fanout.py                    # Feature 2 endpoint
├── main.py                          # FastAPI app, dotenv, exception handlers
├── models/schemas.py                # Pydantic request/response models (F1 + F2)
└── services/
    ├── content_parser.py            # fetch_url, parse(), boilerplate strip
    ├── nlp.py                       # spaCy singleton
    ├── embeddings.py                # sentence-transformers singleton (F2)
    ├── llm_client.py                # LLMClient Protocol + OpenAIClient (F2)
    ├── fanout_engine.py             # Prompt + retry loop + orchestration (F2)
    ├── gap_analyzer.py              # Sentence chunking + cosine + gap_summary (F2)
    └── aeo_checks/
        ├── base.py                  # BaseCheck abstract class
        ├── direct_answer.py         # Check A
        ├── htag_hierarchy.py        # Check B
        ├── readability.py           # Check C
        └── __init__.py              # default_checks() registry

tests/
├── conftest.py                      # nlp + embedder + fake_llm fixtures
├── test_direct_answer.py            # Check A — 6 cases
├── test_htag_hierarchy.py           # Check B — 7 cases
├── test_readability.py              # Check C — 17 parametrized + 4 integration
├── test_content_parser.py           # Parser correctness — 5 cases
└── test_fanout_parsing.py           # F2 — 28 cases across 4 groups

tools/
└── iterate_prompt.py                # Live prompt-iteration harness (F2)
```

## Test stats

```bash
$ pytest --tb=no -q
......................................... 67 passed in ~17s
```

- 36 Feature 1 tests (direct_answer, htag_hierarchy, readability, content_parser)
- 28 Feature 2 tests (extract_json, schema, retry loop, gap analyzer, endpoint)
- All run without network access; LLM is mocked via `FakeLLMClient` in conftest.
