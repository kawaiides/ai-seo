"""Internal-linking suggestions (Phase B.3).

Three modules:
  - `sitemap_fetcher` — pull URLs from a sitemap.xml (handles `<urlset>`
    and the `<sitemapindex>` two-level case).
  - `page_index`     — fetch + parse + embed the user's pages so we have
    a queryable in-memory index.
  - `suggest`        — given a list of missing sub-queries, return the
    top-K semantically-related URLs from the index with a suggested
    anchor text.

The split keeps each step independently testable: tests can hand a fake
list of `PageRecord`s to `page_index` without touching the network, and
fake an HTTP transport for the fetcher.
"""
