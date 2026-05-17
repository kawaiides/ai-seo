# AEGIS Autopilot — Build Plan

## Context

The repo currently ships **Feature 1 (AEO Content Scorer)** and **Feature 2 (Query Fan-Out Engine)** behind FastAPI endpoints (`/api/aeo/analyze`, `/api/fanout/generate`) plus a working Tailwind UI at `/` — 67 tests pass. This plan extends it into **AEGIS Autopilot**: a passive micro-SaaS that prospects content sites, audits them with the existing engines, mails personalized reports, and converts via a BYOK/Stripe/Razorpay paywall — operating end-to-end without human input.

Locked decisions: **full phased build**, **SerpAPI** for SERP, **generic SMTP** for email, **Postgres day-1** via SQLAlchemy, **UPI Autopay via Razorpay** for India (sidesteps RBI card e-mandate friction), Stripe USD elsewhere.

---

## Architecture

```
CRON ─▶ prospector (SerpAPI) ─▶ Postgres.Prospect
            │
            ▼
       audit_runner ─reuses─▶ aeo_checks + fanout_engine + gap_analyzer
            │
            ▼
       Postgres.Audit ─▶ report_builder (Jinja2 + WeasyPrint)
            │
            ▼
       outbox_mailer (aiosmtplib) ─▶ MailHog/SES/Postmark
            │                                      ▲
            ▼                                      │
       /r/{signed_token} ─▶ HTML report ─▶ Paywall modal
                                              │
                            ┌─────────────────┴─────────────────┐
                            │   /api/geo (CF-IPCountry → US/IN) │
                            └──┬──────────────────────────────┬─┘
                               ▼                              ▼
                       Stripe $49 (USD)            Razorpay ₹999 UPI Autopay
                               │                              │
                               └──────▶ webhooks ─▶ Subscription row ─▶ unlimited
```

---

## Sales funnel & autopilot integration

The autopilot **is** the funnel — every pipeline stage writes a tracked event so conversion is measurable end-to-end. Two funnels run in parallel: **outbound** (autopilot pushes prospects in) and **inbound** (organic visitors land on `/`).

### Funnel stages

```
            OUTBOUND (autopilot-driven)              INBOUND (organic / referral)
            ─────────────────────────────            ────────────────────────────
  TOFU      1. DISCOVERED   ← prospector             1. VISITED      ← GET /
              SerpAPI organic top-N                    pageview, geo, UTM tags
                       │                                       │
                       ▼                                       ▼
  MOFU      2. AUDITED      ← audit_runner          2. SCANNED      ← /api/aeo/analyze
              aeo_score + missing_gap_types            free tier, scan_count++
              (score < 70 ⇒ MQL)                       (3rd scan ⇒ MQL)
                       │                                       │
                       ▼                                       ▼
            3. CONTACTED    ← outbox_mailer
              cold email, signed report link
                       │
                       ▼
  BOFU      4. ENGAGED      ← /r/{token} hit OR     3. PAYWALLED    ← 429 envelope
              /r/{token}/p.gif open-pixel              modal shown, geo-routed CTA
                       │                                       │
                       └─────────────────┬─────────────────────┘
                                         ▼
            5. TRIALING     ← BYOK key validated OR continued free usage
                                         │
                                         ▼
  CONVERT   6. CONVERTED    ← Stripe checkout.session.completed
                              OR Razorpay subscription.activated
                                         │
                                         ▼
  RETAIN    7. RETAINED     ← invoice.payment_succeeded / subscription.charged
            8. AT_RISK      ← invoice.payment_failed / subscription.halted (dunning)
            9. CHURNED      ← customer.subscription.deleted
```

### Stage → code → event mapping

| Stage | Trigger (code) | Writes event | Funnel exit metric |
|---|---|---|---|
| DISCOVERED | `app/autopilot/prospector.py` insert Prospect | `funnel_event(prospect_id, 'discovered')` | n/a (top of funnel) |
| VISITED | `app/main.py` GET `/` middleware | `funnel_event(user_id, 'visited', utm)` | visit→scan rate |
| AUDITED | `app/autopilot/audit_runner.py` insert Audit | `funnel_event(prospect_id, 'audited', {score, band})` | audit success rate |
| SCANNED | `app/api/aeo.py` + `app/api/fanout.py` success | `funnel_event(user_id, 'scanned')` | scan→paywall rate |
| CONTACTED | `app/autopilot/outbox_mailer.py` send success | `funnel_event(contact_id, 'contacted', {subject_variant})` | send→open rate |
| ENGAGED | `app/api/reports.py` GET `/r/{token}` 200 | `funnel_event(audit_id, 'engaged', {via:'click'|'pixel'})` | open / CTR |
| PAYWALLED | `app/services/gating.py` 429 raised | `funnel_event(user_id, 'paywalled', {processor, price})` | paywall→trial rate |
| TRIALING | `app/api/byok.py` validate 200 | `funnel_event(user_id, 'trialing', {provider:'openai'})` | BYOK→paid lift |
| CONVERTED | `app/api/webhooks.py` Stripe `session.completed` / Razorpay `subscription.activated` | `funnel_event(user_id, 'converted', {processor, amount})` | trial→paid (primary KPI) |
| RETAINED | Stripe `invoice.payment_succeeded` / Razorpay `subscription.charged` | `funnel_event(user_id, 'retained', {period_end})` | MRR, churn |
| AT_RISK | Stripe `invoice.payment_failed` / Razorpay `subscription.halted` → enqueue dunning Outreach | `funnel_event(user_id, 'at_risk', {reason})` | recovery rate |
| CHURNED | `customer.subscription.deleted` | `funnel_event(user_id, 'churned')` | gross churn |

### Schema add for funnel tracking (M1 or M5)

```python
class FunnelEvent(Base):
    id: UUID (pk)
    user_id: FK User NULL
    prospect_id: FK Prospect NULL
    audit_id: FK Audit NULL
    contact_id: FK Contact NULL
    stage: Enum('discovered','visited','audited','scanned','contacted',
                'engaged','paywalled','trialing','converted','retained',
                'at_risk','churned')
    occurred_at: datetime default now
    metadata: JSONB                  # score, processor, price, utm, variant…
```

Indexes: `(stage, occurred_at)`, `(user_id, stage)`, `(prospect_id, stage)`. Append-only — no updates, ever; enables reliable cohort/funnel queries.

### Lead-magnet landing page (inbound TOFU)

Add `app/templates/landing.html` served at `GET /` (replaces current bare scorer UI as the default; existing tool moves to `/scorer`). Sections: hero ("Find the AI search queries your site is *invisible* for"), 3 social-proof audit screenshots, "Run free audit" CTA → `/scorer`. CTA click writes `funnel_event('visited', {source:'landing'})`. UTM params captured into User session and persisted on first scan. Existing `index.html` becomes `scorer.html` and stays the conversion-point template (no rewrite needed).

### Email sequence (outbound BOFU drip)

Single cold-touch in M4 is the floor; add `app/autopilot/email_sequence.py` (M4.5) that schedules follow-ups based on funnel state:

| Delay | Template | Send condition |
|---|---|---|
| T+0 | `cold_outreach` (the audit) | `contacted` event missing |
| T+3d | `followup_competitor` (one specific competitor query they're missing) | `engaged` event missing |
| T+7d | `followup_byok` (offer BYOK trial — "use your own API key, audit unlimited") | still no `engaged` |
| T+14d | `breakup` (last touch, soft) | still no `engaged` |
| Post-engage | `trial_nudge` (BYOK→paid offer with INR/USD price) | `engaged` exists, `converted` missing, T+2d after engage |
| Post-convert | `onboarding` (here's how to add 10 more URLs) | `converted` event |
| At_risk | `dunning` (already in M6/M7 webhooks) | webhook trigger |

Driven by a new cron subcommand `python -m app.autopilot sequence` that runs every hour, queries `funnel_event` for stage gaps + elapsed time, enqueues the next Outreach row. Idempotency via existing `UNIQUE(contact_id, audit_id)` plus a new `outreach.template_variant` column UNIQUE'd into the constraint.

### Conversion dashboard endpoint

`GET /admin/funnel` (auth-gated via `ADMIN_TOKEN` env header) returns JSON cohort funnel for a date range: counts at each stage + conversion rates between adjacent stages. Uses one CTE over `funnel_event`. Lightweight Jinja page `app/templates/admin/funnel.html` renders bar chart with [Chart.js](https://www.chartjs.org) from CDN.

Key formulas:

```
visit_to_scan      = count(stage='scanned')   / count(stage='visited')
scan_to_paywall    = count(stage='paywalled') / count(stage='scanned')
paywall_to_trial   = count(stage='trialing')  / count(stage='paywalled')
trial_to_paid      = count(stage='converted') / count(stage='trialing')
discover_to_paid   = count('converted' via prospect_id) / count(stage='discovered')   # outbound funnel
mrr                = SUM(active subscriptions × plan_price by currency)
gross_churn_30d    = count(stage='churned' last 30d) / count(stage='retained' 30d ago)
```

### How autopilot drives the funnel

- **prospector** = TOFU generator. Daily cron pushes new `discovered` rows.
- **audit_runner** = MQL qualifier. Score < 70 = qualified lead; this is the **only** filter for the mailer (`outbox_mailer.send_pending(min_score_cutoff=70)`).
- **report_builder** + `/r/{token}` = the lead magnet itself. The cold email's only job is to drive the click; the report page is what converts.
- **outbox_mailer** + `email_sequence` = nurture cadence; every send is gated on funnel state, never blind blast.
- **gating** = the conversion event itself; 429 envelope is intentional friction that auto-presents the BYOK fork (low intent → free retention) vs paid CTA (high intent → MRR).
- **webhooks** = closed-loop attribution; CONVERTED event lets you measure prospect → paid all the way from a single SerpAPI keyword seed.

### Funnel-driven priorities (informs M-ordering)

M1–M4 build the outbound funnel skeleton (TOFU → BOFU). M5 wires the inbound conversion path (paywall + BYOK). M6–M7 close the retention loop. Cohort dashboard ships in **M4.5** so you can see funnel leaks before the paywall is live — without the dashboard you're flying blind on which stage to invest in next.

---

## New dependencies (`requirements.txt` additions)

```
sqlalchemy[asyncio]>=2.0
asyncpg
psycopg2-binary        # alembic CLI only
alembic
weasyprint             # HTML→PDF (single source w/ Jinja2)
aiosmtplib
itsdangerous           # signed report + session tokens
tenacity               # SerpAPI/SMTP retries
stripe
razorpay
```

`requirements-dev.txt`: `respx`, `aiosmtpd`, `testcontainers[postgres]`.

---

## Postgres schema (SQLAlchemy 2.0 async)

`app/db/base.py` (engine + `async_session_maker` + `get_session` dep), `app/db/models.py`:

| Table | Key columns | Notes |
|---|---|---|
| `prospect` | id, url UNIQUE, domain, target_keyword, status enum(`queued/audited/failed/skipped`), discovered_at | idx(status), idx(target_keyword) |
| `audit` | id, prospect_id FK CASCADE, aeo_score, band, fanout_payload JSONB, missing_gap_types JSONB, failed_checks JSONB, report_path, token_jti UUID UNIQUE, created_at | |
| `contact` | id, prospect_id FK, email CITEXT, name, role, source, verified, discovered_at | UNIQUE(prospect_id, email) |
| `outreach` | id, contact_id FK, audit_id FK, subject, body_hash, sent_at NULL, opens, clicks, replied, error | UNIQUE(contact_id, audit_id) — idempotency |
| `scan` | id, ip INET, path, scanned_at | replaces in-memory `_counts` in [main.py](app/main.py) |
| `seed_keyword` | id, keyword, vertical, enabled | |
| `user` | id UUID, session_id UNIQUE (signed cookie), email NULL UNIQUE, ip_country, plan enum(`free/pro_usd/pro_inr`), created_at | anonymous-friendly |
| `subscription` | id, user_id FK, processor enum(`stripe/razorpay`), external_id UNIQUE, status enum(`active/past_due/canceled/halted`), current_period_end | |
| `byok_validation` | id, user_id FK, key_hash sha256, provider enum(`openai/gemini`), last_verified_at, valid | UNIQUE(user_id, key_hash) — hash only, **never the raw key** |
| `webhook_event` | id PK (provider event id, replay-proof), processor, event_type, payload JSONB, received_at, processed_at, error | |

`alembic init app/db/migrations` → one initial revision; later revision adds payment tables.

---

## Phased milestones

### M1 — DB foundation
- Add deps, `docker-compose.yml` (postgres:16 + mailhog), `app/db/base.py`, `app/db/models.py`, alembic init + revision `0001_initial`.
- Replace in-memory `_counts` rate-limit in [app/main.py](app/main.py) with `Scan`-table reads (move to gating dep in M5).
- **Demo gate**: `alembic upgrade head`; psql lists all tables.

### M2 — Pipeline
- `app/autopilot/prospector.py`: async SerpAPI fetch (httpx, `https://serpapi.com/search.json`), dedup-insert `Prospect`. Concurrency `asyncio.Semaphore(AUTOPILOT_CONCURRENCY=5)`.
- `app/autopilot/audit_runner.py`: pulls `queued` prospects, reuses [content_parser.py](app/services/content_parser.py) + [aeo_checks/](app/services/aeo_checks/) + [fanout_engine.py](app/services/fanout_engine.py) + [gap_analyzer.py](app/services/gap_analyzer.py) **in-process** (no HTTP self-call). Wrap blocking `sentence-transformers.encode` and spaCy parse in `asyncio.to_thread`. Errors → `status='failed'` with reason.
- `app/autopilot/__main__.py`: argparse subcommands `prospect | audit | report | mail | run`.
- **Demo gate**: `python -m app.autopilot prospect --seed "best SEO tool" --limit 3 && python -m app.autopilot audit` produces ≥1 Audit row with real score.

### M3 — Reports + public link
- `app/templates/report.html` (Jinja2): score gauge, failed structural rules, top-3 missing **comparative** sub-queries, FK warning, `{{ checkout_url }}` placeholder.
- `app/autopilot/report_builder.py`: `build(audit_id)` renders HTML + `weasyprint.HTML(string=...).write_pdf("reports/{token}.pdf")`. Store relative path on `Audit`.
- `app/api/reports.py`: `GET /r/{token}` (decode via `itsdangerous.URLSafeTimedSerializer`, 90-day TTL, 404 on bad sig) + `GET /r/{token}/p.gif` 1×1 pixel that increments `Outreach.opens`.
- Mount router in [app/main.py](app/main.py).
- **Demo gate**: `curl /r/{token}` returns 200 HTML with domain + score.

### M4 — Mailer
- `app/autopilot/contact_finder.py`: `ContactFinder` Protocol, `StubContactFinder`, `WhoisFallbackContactFinder` (scrape `mailto:` from homepage). Hunter.io/Apollo swap later.
- `app/autopilot/outbox_mailer.py`: `send_pending(min_score_cutoff=70)` — JOIN `outreach.sent_at IS NULL AND audit.aeo_score < cutoff`. Render `app/templates/emails/cold_outreach.{html,txt}.j2`. Sign report URL. Send via `aiosmtplib.send` with STARTTLS on port 587. `INSERT … ON CONFLICT DO NOTHING` first → idempotency. Hard cap 30 sends/day during warm-up.
- Write `funnel_event` rows on every send + every `/r/{token}` engagement (M3 endpoint extended).
- **Demo gate**: end-to-end `python -m app.autopilot run` → MailHog UI shows message; click link → report loads; `outreach.opens` increments; re-run mail → zero new sends.

### M4.5 — Funnel tracking + admin dashboard
- Schema add: `funnel_event` table (or fold into M1 migration).
- Helper `app/services/funnel.py` `record(stage, **fks, **metadata)` — single import point used by every pipeline module + every API route writes via FastAPI middleware on success status.
- `app/api/admin.py`: `GET /admin/funnel?from=&to=` returns cohort counts + conversion rates (single CTE); `GET /admin/funnel/view` renders `app/templates/admin/funnel.html` with Chart.js bar chart. Header `X-Admin-Token: $ADMIN_TOKEN` required.
- `app/autopilot/email_sequence.py`: scheduler subcommand `python -m app.autopilot sequence` — reads `funnel_event` for stage gaps + elapsed time, enqueues next Outreach (T+3d, T+7d, T+14d follow-ups; T+2d trial-nudge after engage). Idempotency: add `outreach.template_variant` column, extend UNIQUE constraint to `(contact_id, audit_id, template_variant)`.
- Landing page split: existing [index.html](app/templates/index.html) → renamed `scorer.html` (served at `/scorer`); new `app/templates/landing.html` at `/` with hero + CTA. UTM capture middleware writes to `User` session.
- **Demo gate**: run prospector → audit → mail; visit `/admin/funnel/view` → bar chart shows non-zero counts at `discovered`, `audited`, `contacted`; click report link → `engaged` count increments.

### M5 — BYOK + paywall envelope
- Schema add: `user`, `byok_validation` (+ alembic revision).
- Signed-cookie session middleware in [app/main.py](app/main.py) (`session_id` per visitor).
- `app/api/byok.py`: `POST /api/byok/validate` → HEAD `https://api.openai.com/v1/models` with the key, upsert `byok_validation` (hash, TTL 6h). **Hash-only at rest; structured-log redaction filter strips `X-BYOK-*` and `key_*` keys.**
- Refactor `run_fanout()` signature → accept `client: LLMClient` arg; resolve client in [app/api/fanout.py](app/api/fanout.py) via new dep `get_request_llm_client(request, db)` (BYOK header → per-request `OpenAIClient(api_key=...)`, else singleton).
- `app/services/gating.py`: dependency `require_pro_or_byok_or_quota` → valid BYOK | active Subscription | `<3 scans today` else `429 {error:"quota_exhausted", processor, currency, price, byok_supported:true}`. Apply to `/api/fanout/generate` only (AEO stays free — no LLM cost).
- Frontend ([app/templates/index.html](app/templates/index.html)): paywall modal on 429; BYOK input → `localStorage.aegis_byok_openai`; `fetchWithAuth()` wrapper attaches `X-BYOK-OpenAI-Key`; badge green/red on validation result; CSP `connect-src 'self' api.openai.com`.
- **Demo gate**: 4 free scans → 4th returns 429 envelope; paste valid key → unlimited; bad key → 401 + badge red.

### M6 — Stripe
- Schema add: `subscription`, `webhook_event`.
- `app/payments/stripe_client.py`: `create_checkout_session(user)` (`mode='subscription'`, `client_reference_id=user.id`, signed `metadata.sig`).
- `app/api/checkout.py`: `POST /api/checkout/stripe` → `{url}`.
- `app/api/webhooks.py`: `POST /webhooks/stripe` reads **raw body** via `await request.body()` BEFORE Pydantic, verifies with `stripe.Webhook.construct_event(body, sig, STRIPE_WEBHOOK_SECRET)`, upserts `webhook_event.id = event.id` (UNIQUE = replay-proof). Handlers: `checkout.session.completed`, `invoice.payment_succeeded`, `invoice.payment_failed` (enqueue dunning `Outreach`), `customer.subscription.deleted`.
- `app/api/account.py`: `GET /account/{token}` post-checkout return page.
- **Demo gate**: Stripe test card 4242… → checkout completes → user upgraded → unlimited fanouts; `stripe trigger invoice.payment_failed` → dunning Outreach row; replay → 200 no-op, no dup.

### M7 — Razorpay UPI Autopay + geo
- `app/services/geo.py`: resolver chain `CF-IPCountry` → `X-Forwarded-For` first hop → `httpx.get https://ipapi.co/{ip}/country` (24h TTL cache). `app/api/geo.py`: `GET /api/geo` returns `{country, currency, price, processor}`. India → INR/Razorpay.
- `app/payments/razorpay_client.py`: `client.subscription.create({plan_id: RAZORPAY_PLAN_ID_UPI, total_count:120, customer_notify:1})` — UPI Autopay rails (NPCI, single-PIN consent, MDR 0%, **sidesteps RBI card e-mandate 24h-prior SMS + AFA per renewal**).
- `POST /webhooks/razorpay`: HMAC-SHA256 raw body vs `X-Razorpay-Signature` using `hmac.compare_digest`. Same `webhook_event` idempotency. Handle `subscription.activated/charged/halted`, `payment.failed`.
- Frontend: Razorpay button shown iff `geo.country === 'IN'`.
- **Demo gate**: `curl -H "CF-IPCountry: IN" /api/geo` → INR envelope; Razorpay test UPI → `subscription.activated` → `plan='pro_inr'`.

---

## Critical files (nail first within each milestone)

- [app/db/models.py](app/db/models.py) — schema is the contract; bad columns cascade-rework everything downstream.
- [app/autopilot/audit_runner.py](app/autopilot/audit_runner.py) — load-bearing in-process reuse of all Feature 1+2 services; async-in-sync hot spot.
- [app/autopilot/report_builder.py](app/autopilot/report_builder.py) + [app/templates/report.html](app/templates/report.html) — only customer-visible artifact; PDF lib choice locks here.
- [app/api/reports.py](app/api/reports.py) — security boundary for unauthenticated public URLs (signed tokens).
- [app/services/llm_client.py](app/services/llm_client.py) + [app/api/fanout.py](app/api/fanout.py) — BYOK refactor pivots here; `run_fanout()` signature change ripples through tests.
- [app/api/webhooks.py](app/api/webhooks.py) — raw-body-before-parsing quirk + idempotency = highest-blast-radius security code in the project.
- [app/services/funnel.py](app/services/funnel.py) + `funnel_event` table — append-only event log; bad stage taxonomy or missing call-sites silently break every conversion metric for the life of the product.

---

## Reused services (do not redesign)

- [content_parser.py](app/services/content_parser.py) `fetch_url` + `parse` — used by prospector prefetch & audit_runner.
- [aeo_checks/](app/services/aeo_checks/) `default_checks()` registry — audit_runner imports verbatim.
- [fanout_engine.py](app/services/fanout_engine.py) `run_fanout()` — **signature change in M5**: accept `client: LLMClient` arg.
- [gap_analyzer.py](app/services/gap_analyzer.py) `chunk_content` + `score_subqueries` — wrap in `asyncio.to_thread` when called from autopilot.
- [embeddings.py](app/services/embeddings.py), [nlp.py](app/services/nlp.py) — singletons stay; M3+ wrap blocking calls.

---

## Env additions (`.env`)

**Required to run:**
```
DATABASE_URL=postgresql+asyncpg://aegis:aegis@localhost:5432/aegis
SERPAPI_KEY=
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USER=
SMTP_PASS=
SMTP_FROM=audits@aegis.local
REPORT_SIGNING_KEY=
SESSION_SIGNING_KEY=
REPORT_BASE_URL=http://localhost:8000
AUTOPILOT_CONCURRENCY=5
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_ID=
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
RAZORPAY_PLAN_ID_UPI=
APP_BASE_URL=http://localhost:8000
```

**Optional:** `IPAPI_TOKEN`, `BYOK_VALIDATION_TTL_SECONDS=21600`, `STRIPE_BILLING_PORTAL_CONFIG_ID`, `AEGIS_DISABLE_RATE_LIMIT=1` (tests).

---

## Tests (new files; reuse existing `nlp`, `embedder`, `fake_llm` fixtures)

Extend [tests/conftest.py](tests/conftest.py) with `db_session` (testcontainers-postgres), `prospect_factory`, `audit_factory`, `respx`-mocked SerpAPI.

| File | Covers |
|---|---|
| `tests/test_prospector.py` | SerpAPI mock → N Prospects, dedup on rerun |
| `tests/test_audit_runner.py` | Stub fetch_url + queued FakeLLMClient → Audit row populated, status='audited' |
| `tests/test_report_builder.py` | HTML contains domain/score/top-3 comparative queries; PDF non-empty |
| `tests/test_outbox_mailer.py` | aiosmtpd captures send; second run = zero (idempotency); body contains signed URL |
| `tests/test_report_endpoint.py` | `/r/{valid}` → 200; expired → 404; pixel → opens++ |
| `tests/test_db_models.py` | Constraint smoke: duplicate Outreach → IntegrityError |
| `tests/test_geo_routing.py` | CF header / ipapi mock → correct envelope |
| `tests/test_byok.py` | Header path → request key; bad key → 401; over quota + no key → 429 paywall envelope |
| `tests/test_paywall_gating.py` | Quota / BYOK / active-sub branches |
| `tests/test_stripe_webhook.py` | Signed payload → Subscription row; replay → 200 no-op |
| `tests/test_razorpay_webhook.py` | HMAC sig → `subscription.activated` row; replay no-op |
| `tests/test_funnel.py` | Every pipeline stage writes correct `funnel_event` row; conversion-rate CTE returns expected ratios on seeded fixtures |
| `tests/test_email_sequence.py` | T+3d follow-up scheduled only when `engaged` missing; trial_nudge scheduled only post-engage pre-convert; idempotency on `(contact, audit, variant)` |

---

## End-to-end verification

```bash
docker compose up -d postgres mailhog
alembic upgrade head
INSERT INTO seed_keywords (keyword, vertical, enabled) VALUES ('best SEO tool','martech',true);
python -m app.autopilot run --seed "best SEO tool" --limit 3
```

Assert via psql: `prospect` ≥3 with `status='audited'`, matching `audit` rows, `outreach.sent_at` populated for audits where `aeo_score<70`.

Open MailHog at `http://localhost:8025` → click report link → 200 HTML → `outreach.opens` increments. Re-run `mail` subcommand → zero new messages.

Paywall flow: 4 anonymous scans → 4th = 429 envelope → paste OpenAI key → unlimited → Stripe test card 4242 4242 4242 4242 → `/account/{token}` → `user.plan='pro_usd'`. `curl -H "CF-IPCountry: IN" /api/geo` → INR/Razorpay envelope.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| **WeasyPrint native deps** (Pango/Cairo) flaky locally | Document brew/apt install in SETUP.md; provide Docker shell; HTML report is the primary artifact, PDF optional |
| **SerpAPI cost/quota** ($75/5k) | Cache raw JSON in a `serp_cache` table (7-day TTL); hard-cap `limit` per seed |
| **Cold SMTP from VPS = spam folder** | M4 ships against MailHog only; production swap to Postmark/SES via same env vars; warm-up 30 sends/day; SPF/DKIM/DMARC docs in SETUP.md |
| **Async-in-sync** (spaCy + sentence-transformers block event loop) | `asyncio.to_thread` wrap in audit_runner; keeps httpx fetches parallel |
| **`fanout_engine` retries multiply LLM spend in batch** | Per-batch budget cap; skip prospects with Audit <30 days old |
| **BYOK leak surface** | HSTS preload, hash-only at rest, structured-log redaction, header-only transport (never query/body), CSP `connect-src 'self' api.openai.com` |
| **Webhook race** (Stripe sends `session.completed` + `payment_succeeded` near-simultaneously) | `ON CONFLICT (external_id) DO UPDATE` makes order-independent |
| **Stripe-India entity** required for INR | Bypassed by routing IN traffic to Razorpay UPI Autopay; documented in SETUP.md |
| **UPI Autopay bank coverage** ~80% | M8 fallback NetBanking emandate plan group; surface "bank unsupported" branch in checkout response |
| **GDPR / IT Act**: IP+email = personal data | Privacy Policy v2: purpose (geo, dunning), retention (90d after cancel), DPO contact; cookie banner for EU branch |
