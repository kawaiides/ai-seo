# AEGIS — Core Product Roadmap

Sister plan to [to-build-aegis-autopilot-synthetic-flask.md](/Users/kawaii/.claude/plans/to-build-aegis-autopilot-synthetic-flask.md). That plan = business automation (prospecting, paywall, payments). **This plan = the product itself** — the analysis engine prospects pay for. Autopilot drives traffic; product retains it.

## Context

Shipped today (Features 1+2): AEO Content Scorer (Checks A/B/C → 0–100 score) + Query Fan-Out Engine (LLM generates 10–15 sub-queries across 6 types → cosine-similarity gap analysis at threshold 0.72). 67 tests pass. Single-page Tailwind UI. Solid foundation but thin product surface — current value prop fits free tier, not Pro plan.

Pro plan promise (from spec): multi-user dashboards, programmatic PDF exports, automatic internal-linking suggestions, no BYOK requirement. Roadmap below builds that value ladder.

---

## Product phases

### Phase A — Engine depth (the moat)

The two existing checks are a baseline; competitors will clone them. Depth comes from adding analyses they can't trivially replicate.

| Feature | Module | Why it matters |
|---|---|---|
| **Check D: Entity coverage** | `app/services/aeo_checks/entity_coverage.py` | spaCy NER + Wikidata/DBpedia lookup → does content cover the named entities competitors do for this query? Surfaces missing facts. |
| **Check E: Schema.org markup** | `app/services/aeo_checks/schema_markup.py` | Parse JSON-LD / microdata in original HTML; score presence of `FAQPage`, `HowTo`, `Article`, `Product` types appropriate to query intent. |
| **Check F: Citation density** | `app/services/aeo_checks/citations.py` | Count of `<a>` to authoritative roots (gov, edu, top-100 domains via Tranco list) per 1k words. LLMs preferentially cite cited content. |
| **Check G: Freshness signals** | `app/services/aeo_checks/freshness.py` | Detect `datePublished` / `dateModified`, language drift ("as of 2024"), version numbers. |
| **Fan-out v2: Intent clustering** | `app/services/fanout_v2.py` | Cluster generated sub-queries into intent groups via UMAP+HDBSCAN on embeddings; surface "you're missing a whole comparative cluster" not just individual queries. |
| **Gap analyzer v2: Passage-level** | `app/services/gap_analyzer.py` extend | Currently sentence-chunks; add 3-sentence sliding windows + per-section heading-aware chunking. Better recall on long-form. |

**Pro-gated vs free:** Checks A/B/C stay free (table stakes). D/E/F/G + Fan-out v2 = Pro-only. Free shows score with placeholders ("Entity coverage check available on Pro").

**Deliverable per check:** new module + `default_checks()` registration + 6+ tests + UI surface in `scorer.html` accordion.

### Phase B — Rewrite assistance (the activation hook)

Audit reports diagnose; users want a fix. Auto-fix is the moment "tool" becomes "product."

| Feature | Module | LLM cost path |
|---|---|---|
| **Suggest direct-answer rewrite** | `app/services/rewrite/direct_answer.py` | When Check A fails, LLM rewrites first paragraph to ≤60 words + declarative. Returns 3 variants. |
| **Heading hierarchy auto-fix** | `app/services/rewrite/headings.py` | Pure-deterministic restructure: collapse skipped levels, demote pre-H1 headings. No LLM. |
| **Internal linking suggestions** | `app/services/linking/suggest.py` | Embed user's content + their own sitemap (separate ingest); top-3 most semantically related URLs per missing-subquery cluster → suggested anchor + target. |
| **Schema.org snippet generator** | `app/services/rewrite/schema_gen.py` | Detect intent → emit JSON-LD `FAQPage` / `HowTo` block populated from existing content via LLM. |
| **Bulk-rewrite export** | `app/api/rewrite.py` endpoint | POST URL → returns diff patch (markdown or HTML) + Pro PDF export with redlined version. |

**Cost control:** rewrites are LLM-heavy. Pro plan absorbs cost; BYOK users pay their own. Cache rewrites by `(url, check_id, content_hash)` → avoid re-spend.

### Phase C — Site-level intelligence (the retention hook)

Single-URL audits = one-shot. Site-level = workflow.

| Feature | Module | Schema add |
|---|---|---|
| **Site ingest (sitemap → batch audit)** | `app/services/site/ingest.py` | `Site(id, root_url, user_id)`, `SitePage(site_id, url, last_audited_at)` |
| **Site dashboard** | `app/templates/dashboard/site.html` | Aggregate scores, trend over time, top-10 worst pages, top-10 missing query clusters across the site |
| **Re-audit scheduling** | `app/autopilot/site_runner.py` | Weekly cron re-audits all SitePages; diff vs prior; alert on score drop |
| **Competitor benchmarking** | `app/services/site/competitors.py` | Pin 1–3 competitor domains; weekly side-by-side score comparison + "they cover query X, you don't" |
| **GEO module (Generative Engine Optimization)** | `app/services/geo/*` | Already-staged demo files (`demo/geo_content_good.txt`, `geo_content_bad.txt`) suggest planned Feature 3 — actually probe ChatGPT/Perplexity/Gemini for the target query, check if user's URL appears in the citations, score citation rate over time |

GEO module is the highest-value piece — currently nobody measures "does ChatGPT actually cite my page." Drives churn defense and Pro-tier price defensibility.

### Phase D — Collaboration & permissions

| Feature | Module | Notes |
|---|---|---|
| **Org / team** | extend `User` → `Org(id, name)`, `OrgMember(org_id, user_id, role)` | One Subscription per Org (not per User). |
| **Role-based access** | `app/services/auth.py` | `owner / editor / viewer`. Owner pays; editors run audits; viewers consume reports. |
| **Shareable report links** | extend `/r/{token}` | Already public for cold outreach; add Pro-tier "branded" variant with org logo + custom subdomain CNAME later. |
| **Audit comments** | `app/api/comments.py` | Inline notes per failed check. Routes content team → engineering team conversations. |
| **Slack / Linear integration** | `app/integrations/slack.py`, `linear.py` | Webhook out: score dropped > 10 points → post to channel; missing comparative query → create Linear issue. |

### Phase E — API & embeddability

Pro+ feature: customers run audits programmatically inside their own CI/CMS.

| Feature | Module | Notes |
|---|---|---|
| **REST API keys** | `app/api/keys.py` + `ApiKey(org_id, key_hash, scopes, last_used_at)` | `Bearer` token auth, scoped per endpoint, rotatable. |
| **CI plugin** | `cli/aegis-ci` (separate package) | GitHub Action: audit changed docs in a PR, fail build on score < threshold. |
| **CMS plugins** | WordPress, Webflow, Ghost | Native panel showing live AEO score as author writes. Largest distribution lever. |
| **Webhook out** | `app/services/webhooks_out.py` | Customer-configured POST on `audit.completed`, `score.dropped`. Mirrors processor webhooks pattern. |
| **OpenAPI spec generation** | already free via FastAPI `/openapi.json` | Document + publish at `/docs`; reference in marketing. |

### Phase F — Multi-language / localization

Currently English-only (spaCy `en_core_web_lg`, FK readability English-tuned).

| Feature | Notes |
|---|---|
| **Multi-language NLP** | Load language-specific spaCy models lazily; detect content language via `langdetect`; route to correct pipeline. |
| **Locale-aware readability** | Replace FK with locale-appropriate metric (LIX for Scandinavian, Fernandez-Huerta for Spanish). |
| **Localized fan-out prompts** | Generate sub-queries in content's language, not always English. |
| **GEO probing per locale** | Probe LLMs from IPs in target locale (ChatGPT answers differ by region). |

Indian market is a major TAM per business plan (UPI Autopay rationale) — Hindi + 4 major Indic languages prioritized.

---

## Product → autopilot integration points

The two plans interlock at these seams:

| Autopilot module | Consumes from product | Product module that owns it |
|---|---|---|
| `prospector.py` | `default_checks()` + `run_fanout()` | `app/services/aeo_checks/`, `app/services/fanout_engine.py` (existing) |
| `audit_runner.py` | All Phase A checks once landed | `app/services/aeo_checks/{entity_coverage,schema_markup,citations,freshness}.py` |
| `report_builder.py` | Rewrite suggestions (Phase B) — preview in report → CTA "Get full rewrites on Pro" | `app/services/rewrite/*` |
| `outbox_mailer.py` email body | One specific missing competitor query (Phase A fan-out v2 intent cluster) | `app/services/fanout_v2.py` |
| `/r/{token}` lead-magnet page | Site-level preview if domain has > 1 prospect row already | `app/services/site/*` (Phase C) |
| Funnel dashboard `/admin/funnel` | Same `funnel_event` table feeds Phase D org-level dashboards | shared |
| Pro plan unlock | Site dashboards, programmatic API, internal-linking suggestions = Phase B + C + E features | shared |

**Funnel implication:** without Phase B (rewrites) and Phase C (site dashboards), TRIAL → CONVERTED rate is anemic — paywall hits but value above the line is thin. Build product Phase B in parallel with autopilot M5–M7.

---

## Phase ordering recommendation

Run in **parallel with autopilot milestones** — both tracks ship together:

| Sprint | Autopilot | Product |
|---|---|---|
| Sprint 1 | M1 — DB foundation | Phase A.1 — Check D (Entity coverage) |
| Sprint 2 | M2 — Pipeline | Phase A.2 — Check E + F (Schema, Citations) |
| Sprint 3 | M3 — Reports + public link | Phase A.3 — Check G (Freshness) + Fan-out v2 |
| Sprint 4 | M4 — Mailer | Phase B.1 — Direct-answer rewrite + heading auto-fix |
| Sprint 5 | M4.5 — Funnel dashboard | Phase B.2 — Schema gen + bulk-rewrite export |
| Sprint 6 | M5 — BYOK + paywall | Phase B.3 — Internal-linking suggestions |
| Sprint 7 | M6 — Stripe | Phase C.1 — Site ingest + dashboard |
| Sprint 8 | M7 — Razorpay UPI | Phase C.2 — Re-audit scheduling + GEO module v0 |
| Sprint 9+ | scale + observability | Phase D + E + F — depends on revenue signal |

GEO module pulled forward to Sprint 8 — strongest defensible differentiator + already partially scaffolded in demo dir.

---

## Critical product decisions ahead

1. **GEO probing mechanics** — call ChatGPT / Perplexity / Gemini APIs with the target query, parse cited URLs, match domain. Each probe costs LLM tokens; need a Probe table with TTL caching (7-day default) to keep margin > 90%.
2. **Internal-linking ingest scope** — need user's sitemap to suggest internal links. Add `Site.sitemap_url` + a `sitemap_fetcher.py` service. Privacy: customer's URLs stored — encrypt or hash.
3. **Rewrite quality bar** — LLM rewrites need a back-check: re-run Check A on the rewrite to verify it actually passes. Loop if fails (max 2 retries). Document this validation pattern.
4. **Multi-tenancy migration** — moving from `User` → `Org` is invasive. Decide in Sprint 6 whether to ship Phase D as a clean v2 or migrate in place.
5. **Pricing pressure from BYOK** — BYOK users escape LLM spend but also Pro's rewrite/GEO value. Pro unlocks should be features BYOK can't trivially replicate (site dashboards, scheduled re-audits, GEO citation tracking, internal linking) — not raw LLM access.

---

## Test strategy (additive)

Each Phase A check: 6 cases (4 score-band examples + 2 edge cases). Each Phase B rewrite: golden-set test (input → expected pass on re-check). Phase C: integration test on a fixture sitemap of 10 pages. Phase E: API key auth happy path + scope violation 403. Phase F: language-detection routing + per-locale FK substitute.

Reuse existing `conftest.py` fixtures (`nlp`, `embedder`, `fake_llm`, `parsed_factory`). Add `fake_geo_probe` fixture for GEO module tests.

---

## Verification per phase

- **Phase A**: each new check passes individually + included in `/api/aeo/analyze` aggregate score with weighting documented in SETUP.md.
- **Phase B**: rewrite endpoint returns valid Check A-passing variant for 9/10 fixture samples.
- **Phase C**: site dashboard at `/dashboard/site/{id}` renders trend chart for ≥7 days of audits.
- **Phase D**: org owner invites editor; editor runs audit; viewer sees report; viewer cannot trigger billing.
- **Phase E**: `curl -H "Authorization: Bearer $KEY" /api/v1/audit` returns 200 with audit JSON; revoked key returns 401.
- **Phase F**: Hindi content returns scored audit with Hindi sub-queries.
