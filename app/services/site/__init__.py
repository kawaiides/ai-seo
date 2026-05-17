"""Site-level intelligence (Phase C.1).

Three sub-services:
  - `ingest`    — accept a root URL (+ optional sitemap), persist a `Site`
                  row and `SitePage` rows for every URL discovered.
  - `audit`     — run the seven AEO checks against each `SitePage`,
                  append a `SitePageAudit` history row, refresh the
                  page's denormalised `last_*` rollup columns.
  - `dashboard` — aggregate the rolled-up data: average score, trend
                  over time, top-10 worst pages, top-10 missing query
                  clusters. Pure functions over ORM rows so the rollup
                  math is unit-testable without a live database.
"""
