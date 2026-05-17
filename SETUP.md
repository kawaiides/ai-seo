# AEGIS — Setup & Design Notes

> Personal setup notes & design rationale for the take-home submission.
> The original assignment spec lives in `README.md`; this file documents what's
> built, how to run it, and the decisions worth a reviewer's attention.

## Status

| Feature | Status |
|---|---|
| Feature 1 — AEO Content Scorer (`POST /api/aeo/analyze`) | ✅ Implemented (Checks A, B, C, D, E, F, G + tests) |
| Feature 2 — Query Fan-Out Engine (`POST /api/fanout/generate`) | ✅ Implemented (v1 gap analysis + v2 intent clustering) |
| Phase B.1 — Rewrite assistance (`POST /api/rewrite/*`) | ✅ Implemented (heading auto-fix + LLM direct-answer rewrites with Check A back-validation) |
| Phase B.2 — Schema-gen + bulk rewrite (`POST /api/rewrite/schema_gen`, `/bulk`) | ✅ Implemented (intent-aware JSON-LD with Check E back-check + unified diff envelope) |
| Phase B.3 — Internal-linking suggestions (`POST /api/linking/suggest`) | ✅ Implemented (sitemap fetch + MiniLM page index + top-K retrieval per missing sub-query/cluster) |
| Phase C.1 — Site ingest + dashboard (`POST /api/site/ingest`, `/{id}/audit`, `GET /{id}/dashboard`, `GET /api/site/dashboard/{id}`) | ✅ Implemented (DB models + Alembic 0002 + ingest/audit/dashboard services + HTML dashboard) |
| Phase C.2 — Re-audit scheduling + GEO v0 (`POST /api/geo/{site}/queries`, `/queries/{id}/probe`, `/queries/{id}/history`) | ✅ Implemented (weekly site_runner with score-drop alerts + GEO probe Protocol + citation history aggregator + Alembic 0003) |
| Phase D — Org + role-based access + audit comments + Slack/Linear notifiers | ✅ Implemented (`Org`/`OrgMember`/`AuditComment` models + Alembic 0004 + `require_role` dep + Slack/Linear notifiers w/ FakeNotifier) |
| Phase E — REST API keys + outbound webhooks + CI plugin + `/api/v1` | ✅ Implemented (`ApiKey`/`Webhook`/`WebhookDelivery` models + Alembic 0005 + Bearer auth w/ scoped permissions + HMAC-signed dispatch + `cli/aegis_ci.py` GitHub-Action script) |
| Phase F — Multi-language NLP + locale-aware readability + localized fan-out + GEO locale | ✅ Implemented (script + stop-word language detector + per-locale spaCy loader + Fernandez-Huerta/LIX/Gulpease/syllable-density routing + locale-threaded fan-out and GEO probe prompts) |
| `PROMPT_LOG.md` | ✅ Real iteration log against `gpt-5` (v0 ablation 0/5 → v1 11/11) |

### Score weighting (`/api/aeo/analyze`)

The aggregate `aeo_score` is `round(sum(check.score) / sum(check.max_score) * 100)`. Every check carries equal weight (`max_score = 20`). With seven checks active the denominator is 140; adding or removing a check rebalances automatically — no per-check weights to maintain.

| Check | ID | Max | Internal axes |
|---|---|---|---|
| A — Direct Answer Detection | `direct_answer` | 20 | word count, declarative, hedging |
| B — H-tag Hierarchy | `htag_hierarchy` | 20 | violation count over (missing/multiple/pre-H1/skipped) |
| C — Snippet Readability | `readability` | 20 | Flesch-Kincaid grade vs `[7.0, 9.0]` |
| D — Entity Coverage | `entity_coverage` | 20 | density per 1k words (10 pts) + distinct entity types (10 pts) |
| E — Schema.org Markup | `schema_markup` | 20 | presence (8) + high-value type (14) + populated required-properties (20) |
| F — Citation Density | `citations` | 20 | authoritative-root link count gated by per-1k density (.gov/.edu/whitelist) |
| G — Freshness Signals | `freshness` | 20 | age of `dateModified` (preferred) or `datePublished` from JSON-LD / meta / `<time>` |

### Fan-Out v2 — Intent clustering

`POST /api/fanout/generate` now returns an `intent_clusters` array alongside the per-query `sub_queries`. Each cluster contains the indices of the sub-queries it groups, a `dominant_type`, a human label (the cluster's representative query), and — when content is provided — `covered_count` / `coverage_percent` so a UI can flag "you're missing this whole intent" gaps. The clustering is single-link agglomerative on MiniLM cosine similarity (threshold 0.55); the roadmap's UMAP+HDBSCAN upgrade path is preserved for future site-level corpora where N is large enough to justify it.

### Phase B.1 — Rewrite assistance

Two new endpoints turn the audit signals into actionable fixes:

| Endpoint | Cost | What it does |
|---|---|---|
| `POST /api/rewrite/direct_answer` | LLM (Pro path) | When Check A fails, emits 3 declarative ≤60-word rewrites in three styles (`definition_first` / `cause_effect` / `outcome_first`). Each variant is back-validated against `DirectAnswerCheck`; if any variant fails the check that motivated the rewrite, the whole batch retries (up to 2 retries) so we never hand back a "fix" that doesn't fix. Set `target_query` to thread the user's keyword through the prompt. |
| `POST /api/rewrite/headings` | Free (deterministic) | Resolves all four H-tag violations without an LLM: promotes the first heading to H1 if missing, demotes extra H1s, demotes pre-H1 headings to H2, and collapses skipped levels one step at a time. Returns the original tag list, the fixed tag list, an `operations` log of what changed and why, and a copy-pasteable `<h1>…</h1>` HTML snippet. |
| `POST /api/rewrite/schema_gen` | LLM (Pro path) | Detects the document's intent (FAQPage / HowTo / Article — heuristic on heading shape) and asks the LLM for a populated JSON-LD block keyed off the document's own sentences. Back-validated against `SchemaMarkupCheck`'s "populated_types" rule — never returns a stub that would still score `14` on Check E. Returns the parsed object **and** a paste-ready `<script type="application/ld+json">` snippet. |
| `POST /api/rewrite/bulk` | LLM (Pro path) | One-shot orchestrator. Runs all three Phase B rewriters against a single document, skips any whose corresponding check already passes, and returns: per-fix `status` envelope (`applied` / `skipped` / `failed` / `disabled`), AEO score before + after-estimate (the orchestrator re-runs the seven checks against a synthetic `ParsedContent` that incorporates every applied fix), a unified `markdown_diff`, and an injectable `html_diff`. PDF export is the natural Sprint-6 follow-up; we deliberately skipped pulling in a PDF rendering dependency until billing is wired. |
| `POST /api/linking/suggest` | Free (deterministic, embeddings only) | Internal-linking suggestions. Caller supplies the missing sub-queries plus *one* of `sitemap_url` (we'll fetch + parse it, recursing through `<sitemapindex>`) or a pre-fetched `pages` array. The service builds an in-memory MiniLM-embedded `PageIndex` (capped by `max_pages`), runs top-K cosine retrieval per sub-query, excludes `source_url` from candidates, and returns suggestions with `anchor_text` (page title preferred, sub-query fallback), `similarity_score`, and `matched_cluster_id` when the sub-queries came from a Fan-Out v2 cluster. Per-page fetch failures are surfaced under `failed_urls` instead of poisoning the batch. |
| `POST /api/site/ingest` | Free | Persist a `Site` row + `SitePage` rows for every URL in a sitemap (or a pre-supplied list). Idempotent — re-ingesting the same root URL leaves existing pages untouched. |
| `POST /api/site/{id}/audit` | Free (deterministic checks only) | Run the seven AEO checks against the oldest-audited (or never-audited) pages of a site, append an append-only `SitePageAudit` row per page, and refresh the page's denormalised `last_*` rollup columns. Per-page fetch/parse failures land in `failed` instead of aborting the batch. |
| `GET /api/site/{id}/dashboard` | Free | JSON envelope: page totals, mean/median score, band distribution, 7-day score trend (per-day mean + audit count), top-10 worst pages by score, top-10 most-frequent missing fan-out types. Pure rollup math lives in `app/services/site/dashboard.py`, separable from the SQL adapters so the helpers are unit-testable without Postgres. |
| `GET /api/site/dashboard/{id}` | Free | HTML rendering of the same dashboard via Jinja (`templates/dashboard/site.html`). Tailwind CDN, no JS — paste-ready link for a customer to drop into a sales call. |
| `POST /api/geo/{site_id}/queries` | Free | Register a target query whose answer-engine citation rate the site wants tracked. Idempotent on `(site, query, locale)`. |
| `GET /api/geo/{site_id}/queries` | Free | List tracked queries for a site. |
| `POST /api/geo/queries/{query_id}/probe` | LLM (Pro path) | Run a probe — hits TTL cache (default 7 days) on `(query, provider, model)`; otherwise asks `OpenAIChatProbe` what URLs it would cite, persists a `GEOProbeRecord`, refreshes `last_*` rollup. Query params: `force_refresh=true` to bypass cache, `ttl_days=N` to override the window. Response carries `from_cache` + `cached_age_seconds`. |
| `GET /api/geo/queries/{query_id}/history` | Free | Citation-rate history: per-week bucket plus overall-window rollup over the last 30 days. Powers the trend chart. |

The autopilot CLI grew a sibling for the weekly job: `python -m app.autopilot site_reaudit` (wired through `app/autopilot/site_runner.py::run_weekly_reaudit`) re-runs the seven checks across every `Site`'s pages, compares each page's two most recent `SitePageAudit` rows, and emits a `ScoreDropAlert` for any page that lost ≥10 points week-over-week. Hooks for routing alerts out land in Phase D via `SlackNotifier` / `LinearNotifier` (below) — both honour the `Notifier` Protocol so the site runner can fan out without knowing which destinations are wired.

**Critical Decision #1 — GEO probe TTL cache.** GEO probes are LLM-priced, and the roadmap target is >90% gross margin. `probe_and_record` now does a recency lookup against `GEOProbeRecord` keyed on `(geo_query_id, provider, model_name)` with a default 7-day window before spending an LLM call; on a hit, the existing row is returned with `from_cache=True` and `cached_age_seconds=<int>`. `POST /api/geo/queries/{id}/probe` accepts `force_refresh=true` (bypasses cache, always spends) and `ttl_days` (override the window for ad-hoc admin runs). Schema-free — we reuse the append-only history table the dashboard already reads, so the citation-rate-over-time chart stays accurate while the hot path gets cheap. See `app/services/geo/citation_tracker.py::probe_and_record` + `_lookup_cached_probe` for the implementation, and `tests/test_geo_probe_cache.py` for the 8 cases that pin the cold/hit/expired/force-refresh/model-key/ttl=0 branches.

**Critical Decision #4 — Site → Org migration.** Multi-tenancy moves *additively*: `Site` carries both `user_id` (legacy, pre-Phase-D) and `org_id` (new, Phase-D-onwards), both nullable. Alembic 0006 adds the column + an `ix_site_org_id` index + a partial unique index `uq_site_org_root ON site (org_id, root_url) WHERE org_id IS NOT NULL` so the same org can't double-ingest a root URL while leaving legacy `user_id`-keyed rows untouched. `ingest_site(org_id=…)` honours an org-first lookup; legacy callers (no org_id) match user-owned rows that still have `org_id IS NULL`; org-scoped callers never silently absorb a legacy user row — they create a fresh row instead, so an admin "promote this user's sites into our org" backfill stays a deliberate operation. `comments.org_id_for_audit` now projects `Site.org_id`, with `user_id_for_audit` kept as the legacy helper for routes that still gate on a user owner. Subscription FK re-pivot stays on the autopilot track's timeline — no destructive churn here.

**Critical Decision #5 — Pro-feature gating audit.**

| Endpoint | LLM cost? | Current gate | Verdict |
|---|---|---|---|
| `POST /api/aeo/analyze` | No | IP rate limit (3/day, in-memory) | OK — free-tier hook, no LLM spend |
| `POST /api/fanout/generate` | Yes | `Depends(require_pro_or_byok_or_quota)` | OK — Pro / BYOK / 3-free quota |
| `POST /api/rewrite/direct_answer` | Yes | none | **GAP** — needs `require_pro_or_byok_or_quota` |
| `POST /api/rewrite/headings` | No | none | OK — deterministic, free |
| `POST /api/rewrite/schema_gen` | Yes | none | **GAP** — needs `require_pro_or_byok_or_quota` |
| `POST /api/rewrite/bulk` | Yes (cascades) | none | **GAP** — needs `require_pro_or_byok_or_quota` |
| `POST /api/linking/suggest` | No (embeddings) | none | OK — embedder is local |
| `POST /api/site/ingest` | No | none | **GAP** — should require `OrgRole.editor` once Site→Org migration finishes routing through `require_role` |
| `POST /api/site/{id}/audit` | No (deterministic checks) | none | **GAP** — same as above; also large-batch DoS risk |
| `GET /api/site/{id}/dashboard` | No | none | **GAP** — should require `OrgRole.viewer` |
| `POST /api/geo/{site_id}/queries` | No | none | **GAP** — should require `OrgRole.editor` |
| `POST /api/geo/queries/{id}/probe` | Yes | TTL cache (7d) only | **GAP** — needs `require_pro_or_byok_or_quota`; cache softens the cost gap but doesn't replace billing intent |
| `GET /api/geo/queries/{id}/history` | No | none | **GAP** — should require `OrgRole.viewer` |
| `POST /api/orgs` | No | `Depends(get_current_user)` | OK — any signed-in user can create an org |
| `POST /api/orgs/{id}/members` | No | `require_role(owner)` | OK |
| `GET /api/orgs/{id}/members` | No | `require_role(viewer)` | OK |
| `POST /api/orgs/{id}/keys` | No | `require_role(owner)` | OK |
| `POST /api/orgs/{id}/webhooks` | No | `require_role(owner)` | OK |
| `POST /api/audits/{id}/comments` | No | `get_current_user` | Partial — should also enforce org membership via `org_id_for_audit` projection |
| `POST /api/v1/audit` | No | `require_scope("audit:write")` | OK — Bearer-key path |
| `POST /webhooks/stripe`, `/webhooks/razorpay` | No | signature verification | OK — processor webhooks |
| `GET /pricing`, `/signup`, `/login`, `/account` | No | n/a (public marketing) | OK |

Gating gaps are **flagged but not wired in this pass** — the parallel autopilot track owns `Subscription` + `BYOKValidation` + `gating.require_pro_or_byok_or_quota`; coordinating the role + paywall stack onto the seven gap endpoints is one focused PR rather than scattered through Phase A–F. Backlog item: route `/api/rewrite/*` + `/api/site/*` + `/api/geo/*` through `require_pro_or_byok_or_quota` (LLM endpoints) and `require_role` (read/write endpoints) once the Subscription↔Org FK lands. The TTL cache from Decision #1 already absorbs most of the GEO-probe LLM cost in the meantime, so the operational risk of leaving `/api/geo/queries/{id}/probe` ungated is "free LLM tokens to anonymous callers but with a 7-day repeat-call dampener" — bounded, not unlimited.

### Phase D — Org / collaboration / integrations

The roadmap's Critical Decision #4 ("migrate `User` → `Org` in place vs ship clean v2") settled toward *additive*: the existing autopilot `Subscription`/`Site` rows keep their `user_id` FKs, and `Org` lives alongside `User`. The bridge is `OrgMember(org_id, user_id, role)`, three-tier RBAC enforced via `app.services.orgs.require_role(min_role)` (FastAPI dependency factory). Endpoints:

| Endpoint | Min role | What it does |
|---|---|---|
| `POST /api/orgs` | _auth_ | Create an org; calling user becomes the lone `owner`. Slug is validated (kebab-case) or generated from the name + a nonce. |
| `GET /api/orgs/{id}` | viewer | Fetch the org. |
| `POST /api/orgs/{id}/members` | owner | Add an existing `User` with `owner` / `editor` / `viewer`. Idempotent (re-invite updates role). |
| `GET /api/orgs/{id}/members` | viewer | List members. |
| `DELETE /api/orgs/{id}/members/{user_id}` | owner | Remove. Refuses to delete the last `owner` (409). |
| `POST /api/audits/{audit_id}/comments` | _auth_ | Inline note on a `SitePageAudit`, optionally scoped to `check_id`. |
| `GET /api/audits/{audit_id}/comments` | _auth_ | List comments, optional `include_resolved=false` filter. |
| `POST /api/comments/{id}/resolve` | _auth_ | Toggle `resolved`. |
| `DELETE /api/comments/{id}` | _auth_ | Delete. |

`Notifier` Protocol (`app/integrations/notifier.py`) is the contract for outbound integrations; concrete implementations live in `app/integrations/slack.py` (incoming-webhook POST, returns `delivered=False` when the webhook env var is unset) and `app/integrations/linear.py` (GraphQL `issueCreate`, returns `delivered=False` when API key / team ID is unset). Both inject `httpx.AsyncClient` for test stubbing; `FakeNotifier` records every call in-memory for unit tests.

### Phase E — REST API keys + webhooks + CI plugin

| Endpoint | Min role | Notes |
|---|---|---|
| `POST /api/orgs/{org_id}/keys` | owner | Mint a key. Token format: `aegis_ak_<prefix6>_<secret32>`. Only the sha256 hash is stored; the plaintext is shown ONCE in the response. |
| `GET /api/orgs/{org_id}/keys` | viewer | List keys (no plaintext; prefix + last_used_at + revoked_at only). |
| `DELETE /api/orgs/{org_id}/keys/{api_key_id}` | owner | Revoke. |
| `POST /api/orgs/{org_id}/webhooks` | owner | Subscribe to events; `secret` (HMAC signing key) returned ONCE. |
| `GET /api/orgs/{org_id}/webhooks` | viewer | List. |
| `DELETE /api/orgs/{org_id}/webhooks/{webhook_id}` | owner | Unsubscribe. |
| `GET /api/orgs/{org_id}/webhooks/supported-events` | viewer | Static list of allowed event names. |
| `POST /api/v1/audit` | scope `audit:write` | Bearer-token-gated audit endpoint mirroring `/api/aeo/analyze` for external CI/CMS callers. |
| `GET /api/v1/ping` | scope `audit:read` | Cheap key/scope sanity check, no audit cost. |

API-key auth flow:

1. Owner mints a key with explicit scopes (`audit:write`, `linking:read`, etc.; `*` and `resource:*` wildcards supported).
2. Caller sends `Authorization: Bearer aegis_ak_…` on every request.
3. `app/services/api_keys.resolve_api_key` parses the prefix, looks up the row, verifies the hash via constant-time compare, refreshes `last_used_at`. Rejected paths (missing/malformed/invalid/revoked) all return the same `401` envelope to avoid leaking key-existence side channels.
4. `require_scope("…")` factory layers on top so each route can declare its required scope independently.

Webhook signing is HMAC-SHA256 over the raw JSON body, sent in `X-Aegis-Signature: sha256=<hex>`; the secret is shown ONCE at creation. `app/services/webhooks_out.dispatch_event(...)` fans an envelope (`{id, event, occurred_at, data}`) out to every enabled subscriber, persists a `WebhookDelivery` row per attempt, and returns one `DispatchResult` per receiver — failures are captured but never abort the fan-out. Retry policy is intentionally external (a scheduled runner can re-call `dispatch_event` with `attempt+1` on rows where `delivered=False` and `attempt < MAX`), so the audit endpoint's latency stays bound only by its own work, not by customer webhook receivers.

`cli/aegis_ci.py` is the GitHub-Action-friendly CLI: `python -m cli.aegis_ci --threshold 70 <url1> <url2>` posts each target through `/api/v1/audit` (or `--paste path/to/file.html` to send local content) and exits non-zero if any aeo_score falls below `--threshold`. Distinct exit codes for usage (`2`), auth (`3`), network (`4`), and below-threshold (`1`) so CI runners can branch on the failure mode.

### Phase F — Multi-language / localisation

| Locale | Code | Readability metric | spaCy fallback |
|---|---|---|---|
| English | `en` | Flesch-Kincaid grade (target 7.0–9.0) | `en_core_web_lg` |
| Spanish | `es` | Fernandez-Huerta (60–70 ideal, higher = simpler) | `es_core_news_lg` → `xx_ent_wiki_sm` → `spacy.blank("es") + sentencizer` |
| French | `fr` | Crawford (Kandel-Moles proxy) | `fr_core_news_lg` |
| German | `de` | Wiener Sachtextformel / Flesch-tuned | `de_core_news_lg` |
| Italian | `it` | Gulpease | `it_core_news_lg` |
| Portuguese | `pt` | Fernandez-Huerta | `pt_core_news_lg` |
| Dutch | `nl` | LIX | `nl_core_news_lg` |
| Swedish / Danish / Norwegian | `sv`/`da`/`no` | LIX | `…_core_news_lg` |
| Hindi / Tamil / Telugu / Kannada / Bengali / Malayalam | `hi`/`ta`/`te`/`kn`/`bn`/`ml` | syllable density (vowel-mark heuristic; target 1.4–2.0 syllables/word) | `xx_ent_wiki_sm` → `spacy.blank(code) + sentencizer` |

Language detection (`app/services/i18n/lang_detect.py`) is a script-range + stop-word heuristic — Devanagari/Tamil/etc. Unicode blocks route directly to their ISO-639-1 code; Latin-script content is graded by curated 15-word stop-word sets per language (accent-stripped, case-folded). The detector is exposed as a `Detector` Protocol so a `langdetect`-backed implementation can be dropped in later without touching callers.

spaCy loader (`app/services/nlp.get_nlp_for_locale`) tries the per-locale full model first, falls back to `xx_ent_wiki_sm`, then to `spacy.blank(code)` plus a `sentencizer` pipe so `doc.sents` keeps working. The English `get_nlp()` singleton is unchanged.

Fan-out v1 prompts: `POST /api/fanout/generate` now accepts `target_locale`; when set, an explicit "every `query` MUST be written in <language> (ISO 639-1 `<code>`)" clause is appended to the system prompt. GEO probes carry the locale through to `OpenAIChatProbe.probe(target_query, locale=…)` so the LLM is told to prefer regional / native-language primary sources.

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

### Autopilot loop (cron / systemd)

The passive outbound pipeline needs three optional env vars on top of
the core API set:

| Variable | Required? | Notes |
|---|---|---|
| `SERPAPI_API_KEY` | Yes for `prospect` | SERP fetcher in `app/autopilot/prospector.py`. |
| `HUNTER_API_KEY`  | Optional | Enables `HunterContactFinder`; the composite finder falls back to the homepage `mailto:` scrape when missing. |
| `RESEND_API_KEY`  | Pref'd over SMTP | Used by `app/integrations/resend.py`. When set, `outbox_mailer._select_transport()` prefers it. |
| `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASS` | Fallback | Old aiosmtplib path; used only when `RESEND_API_KEY` is unset. |
| `SMTP_FROM`       | Recommended | Envelope-from used by both transports (no SMTP_-prefix variant for Resend). |
| `MAIL_REPLY_TO`   | Optional | `Reply-To` header injected on every send. |
| `SLACK_WEBHOOK_URL` / `LINEAR_API_KEY` + `LINEAR_TEAM_ID` | Optional | `site_reaudit` Slack/Linear score-drop alerts. |
| `AEGIS_ENV`       | See note  | Set `local` to unlock the demo billing mock; `production` (or unset) requires real Stripe/Razorpay keys. Demo box at `52.64.13.171` runs in `local` intentionally — flip before accepting paying customers. |

The autopilot ships three systemd timers in `infra/systemd/`:

- `aegis-autopilot-daily.timer` (03:30 UTC daily) — runs `prospect --auto-seed`, then `audit`, `contacts`, `report`.
- `aegis-autopilot-mail.timer` (14:00 UTC daily) — runs `mail`, then `sequence`.
- `aegis-autopilot-reaudit.timer` (Monday 04:00 UTC) — runs `site_reaudit` + dispatches Slack/Linear alerts.

Install:

```bash
sudo cp infra/systemd/*.service /etc/systemd/system/
sudo cp infra/systemd/*.timer  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
    aegis-autopilot-daily.timer \
    aegis-autopilot-mail.timer \
    aegis-autopilot-reaudit.timer
systemctl list-timers --all | grep aegis
```

`--auto-seed` rotates through `app/autopilot/seed_queries.SEED_QUERIES`
picking the least-recently-used slug (state lives in the
`seed_query_usage` table — Alembic 0007). Operators can override on
demand with `python -m app.autopilot prospect --seed "best X"` from the
CLI; explicit `--seed` always beats `--auto-seed`.

## Tests

```bash
pytest -v
```

Coverage:
- `tests/test_direct_answer.py` — Check A, all four scoring tiers (20/12/8/0)
- `tests/test_htag_hierarchy.py` — Check B, all violation rules + scoring tiers
- `tests/test_readability.py` — Check C, scoring band table (parametrized) + integration tests with `textstat` mocked for FK determinism
- `tests/test_entity_coverage.py` — Check D, density/diversity score bands, KB-hook contract, dedupe + min-word edge cases
- `tests/test_schema_markup.py` — Check E, JSON-LD + microdata extraction, `@graph` unwrap, multi-type arrays, broken-block recovery, populated-property scoring
- `tests/test_citations.py` — Check F, authoritative classification (whitelist + TLD suffix), boundary-safe `.gov` matching, density/count score bands
- `tests/test_freshness.py` — Check G, age bands (6/12/24mo+), source priority (JSON-LD → meta → time), `@graph` unwrap, body-year drift, future-date guard
- `tests/test_fanout_v2.py` — Single-link union-find correctness (incl. chain through intermediate), real-embedding clustering of semantically similar queries, coverage stats wiring, label truncation
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
The assignment marks `en_core_web_lg` as preferred over `en_core_web_sm`. Feature 1 uses the dependency parser (Check A's declarative detection) and the NER component (Check D's entity extraction); `lg`'s word vectors also back Feature 2's gap analysis. Loaded lazily via `app/services/nlp.py::get_nlp()` with the full pipeline enabled.

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
    ├── fanout_v2.py                 # Fan-Out v2 — single-link clustering on MiniLM cosine
    ├── rewrite/
    │   ├── __init__.py
    │   ├── headings.py              # B.1 deterministic heading auto-fix (promote/demote/collapse)
    │   ├── direct_answer.py         # B.1 LLM rewrites with Check A back-validation
    │   ├── schema_gen.py            # B.2 intent detection + LLM JSON-LD with Check E back-check
    │   └── bulk.py                  # B.2 orchestrator producing markdown/HTML diff + after-score estimate
    ├── linking/
    │   ├── __init__.py
    │   ├── sitemap_fetcher.py       # B.3 sitemap.xml + <sitemapindex> recursive walk
    │   ├── page_index.py            # B.3 MiniLM in-memory page index with top-K retrieval
    │   └── suggest.py               # B.3 sub-query/cluster → top-K link suggestions
    ├── site/
    │   ├── __init__.py
    │   ├── ingest.py                # C.1 Site/SitePage persistence (idempotent upsert)
    │   ├── audit.py                 # C.1 run AEO checks on each SitePage, append history
    │   └── dashboard.py             # C.1 pure rollup math (summary, trend, worst, missing)
    ├── geo/
    │   ├── __init__.py
    │   ├── probe.py                 # C.2 GEOProbe Protocol + OpenAIChatProbe + URL extraction (+ F locale)
    │   └── citation_tracker.py      # C.2 probe orchestration, persistence, weekly rate aggregation
    ├── i18n/
    │   ├── __init__.py
    │   ├── locale.py                # F locale registry + readability metric routing
    │   ├── lang_detect.py           # F script + stop-word language detector
    │   └── readability_metrics.py   # F per-locale metric computation + scoring
    ├── orgs.py                      # D org CRUD + require_role dep + slug helpers
    ├── comments.py                  # D audit comments (add/list/resolve/delete)
    ├── api_keys.py                  # E REST API keys (mint/hash/verify/scope) + Bearer dep
    ├── webhooks_out.py              # E outbound webhook signing + dispatch
    └── aeo_checks/

app/integrations/                    # D outbound notifier integrations
├── __init__.py
├── notifier.py                      # Notifier Protocol + FakeNotifier test double
├── slack.py                         # incoming-webhook POST
└── linear.py                        # GraphQL issueCreate

cli/                                 # E external CLI plugins
├── __init__.py
└── aegis_ci.py                      # GitHub-Action-friendly audit gate (httpx-only)
        ├── base.py                  # BaseCheck abstract class
        ├── direct_answer.py         # Check A
        ├── htag_hierarchy.py        # Check B
        ├── readability.py           # Check C
        ├── entity_coverage.py       # Check D (spaCy NER + density/diversity, KB hook for Wikidata)
        ├── schema_markup.py         # Check E (JSON-LD + microdata type detection, populated-property scoring)
        ├── citations.py             # Check F (link extraction + authoritative-root classification)
        ├── freshness.py             # Check G (dateModified/datePublished from JSON-LD/meta/<time>, age bands)
        └── __init__.py              # default_checks() registry

tests/
├── conftest.py                      # nlp + embedder + fake_llm fixtures
├── test_direct_answer.py            # Check A — 6 cases
├── test_htag_hierarchy.py           # Check B — 7 cases
├── test_readability.py              # Check C — 17 parametrized + 4 integration
├── test_entity_coverage.py          # Check D — 7 cases (score bands, dedupe, KB hook)
├── test_schema_markup.py            # Check E — 9 cases (JSON-LD shapes, microdata, broken-block recovery)
├── test_citations.py                # Check F — 12 cases (classification, score bands, boundary-safe TLD)
├── test_freshness.py                # Check G — 12 cases (age bands, source priority, future-date guard)
├── test_fanout_v2.py                # Fan-Out v2 — 8 cases (union-find, real-embedding clustering)
├── test_rewrite_headings.py         # B.1 heading auto-fix — 10 cases
├── test_rewrite_direct_answer.py    # B.1 LLM rewrite — 8 cases (back-validation, retries, schema)
├── test_rewrite_schema_gen.py       # B.2 schema-gen — 9 cases (intent detect, back-check, retries)
├── test_rewrite_bulk.py             # B.2 bulk orchestrator — 6 cases (apply-all, skip-passing, disabled flags, failed LLM, escaping)
├── test_linking_sitemap.py          # B.3 sitemap fetcher — 8 cases (urlset, sitemapindex, broken-child recovery, cycle guard)
├── test_linking_page_index.py       # B.3 page index — 7 cases (empty, blank drop, top-K ranking, embedding-text join)
├── test_linking_suggest.py          # B.3 suggestion engine — 8 cases (source dedupe, min-similarity, cluster-skip, URL normalisation)
├── test_site_dashboard.py           # C.1 rollup math — 9 cases (summary, trend, worst, missing)
├── test_site_ingest.py              # C.1 ingest helpers — 7 cases (URL canonicalisation, dedupe)
├── test_site_runner.py              # C.2 score-drop alert math — 7 cases (threshold bands)
├── test_geo_probe.py                # C.2 probe + JSON/regex URL extraction — 8 cases
├── test_geo_citation_tracker.py     # C.2 host matching + weekly rate aggregation — 8 cases
├── test_geo_probe_cache.py          # Decision #1 TTL cache — 8 cases (cold/hit/expired/force-refresh/model-key/ttl=0)
├── test_site_org_bridge.py          # Decision #4 Site→Org bridge — 7 cases (precedence, backfill guards, anonymous orphan)
├── test_orgs_service.py             # D role rank + slug validation — 8 cases
├── test_integrations_notifier.py    # D Slack + Linear + FakeNotifier — 9 cases
├── test_api_keys.py                 # E mint/hash/parse/scope — 11 cases
├── test_webhooks_out.py             # E HMAC signing + filter — 11 cases
├── test_cli_aegis_ci.py             # E CLI exit codes + payload shape — 10 cases
├── test_i18n_lang_detect.py         # F heuristic detector — 10 cases (Latin stop-words + Indic scripts + fallbacks)
├── test_i18n_readability_metrics.py # F per-locale metric compute + score banding — 9 cases
├── test_readability_locale.py       # F Check C locale dispatch — 8 cases (forced + auto-detect)
├── test_fanout_locale.py            # F fan-out + GEO probe locale plumbing — 9 cases
├── test_content_parser.py           # Parser correctness — 5 cases
└── test_fanout_parsing.py           # F2 — 28 cases across 4 groups

tools/
└── iterate_prompt.py                # Live prompt-iteration harness (F2)
```

## Test stats

```bash
$ pytest --tb=no -q
............................................................................................................................. 143 passed in ~15s
```

- 99 Feature 1 tests (direct_answer, htag_hierarchy, readability, entity_coverage, schema_markup, citations, freshness, content_parser)
- 44 Feature 2 tests (extract_json, schema, retry loop, gap analyzer, intent clustering, endpoint)
- All run without network access; LLM is mocked via `FakeLLMClient` in conftest.
