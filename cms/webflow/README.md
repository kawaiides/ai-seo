# AEGIS — Webflow Designer Extension

Audit the current Webflow page from inside the Designer. Uses the
Webflow Designer API to walk the live element tree, assembles a flat
HTML approximation, and POSTs it to `/api/v1/audit`.

## Install

1. `webflow extension bundle` (Webflow CLI) — or zip `public/` +
   `webflow.json`.
2. In Webflow: **Settings → Apps & Integrations → Build an App →
   Designer Extension**.
3. Upload the bundle and install on your workspace.

## Run locally

```bash
npx webflow-cli extension dev
```

The extension boots inside the Designer's right-hand panel.

## Config

API base + Bearer key are entered in the **Settings** disclosure inside
the panel and persisted in `localStorage`. They never leave the
browser.

## Limits

- The Designer API exposes elements as a tree, not as serialised HTML,
  so we reconstruct an approximation that's enough for the AEO checks
  (h-tags + first paragraph + body text). Schema markup and JSON-LD
  blocks won't surface here — run the published URL through
  `/api/v1/audit` (or the Chrome extension) for those.
- Symbol contents nested inside other symbols may not be reachable
  depending on the user's `siteDataAccess` permission grant.
