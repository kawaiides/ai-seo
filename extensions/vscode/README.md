# AEGIS — VS Code Extension

Audit Markdown / HTML / MDX drafts for AI search optimization (AEO)
without leaving the editor. Talks to the same `/api/v1/audit` surface
as the WordPress + Chrome + CI plugins.

## Features

- **AEGIS: Audit active file** — score the whole document.
- **AEGIS: Audit selection** — right-click a passage, audit just that
  block. Useful for tightening a single paragraph before publishing.
- **Status-bar score badge** — last score visible at a glance.
- **SecretStorage-backed key** — the Bearer token is stored via VS
  Code's encrypted SecretStorage, not in `settings.json`.

## Setup

1. `npm install && npm run compile`
2. `code --install-extension .` (or press `F5` to launch the
   Extension Development Host).
3. Run **AEGIS: Configure API key** and paste your org Bearer key
   (Org → API keys → scope `audit:write`).

## Configuration

`settings.json`:

```json
{
  "aegis.apiBase": "https://aegis-autopilot.com",
  "aegis.threshold": 70
}
```

Below `threshold`, the status-bar badge turns red.

## Why a webview, not inline diagnostics?

Per-line diagnostics would conflate AEO checks (which are document-level
signals like "is there a direct-answer paragraph?") with line-level
errors. A side webview surfaces the checks at the granularity they
actually operate on.
