# AEGIS — Chrome Extension

One-click AEO audit of the current tab. Targets the same `/api/v1/audit`
surface used by the WordPress + CI plugins.

## Install (unpacked dev)

1. Open `chrome://extensions`.
2. Toggle **Developer mode** (top right).
3. Click **Load unpacked** → select this directory.
4. Pin the AEGIS icon to the toolbar.
5. Right-click the icon → **Options** → paste your API key.

## Usage

- Visit any http(s) page.
- Click the AEGIS icon.
- Hit **Audit this page**.
- Score, band, and per-check pass/fail render inline.

## Where the Bearer key lives

`chrome.storage.sync` (encrypted at rest by Chrome, synced to your
Google account). The key NEVER reaches a content script — all fetches
go through the service worker (`background.js`).

## Icons

The manifest references `icons/icon{16,48,128}.png`. The repo ships
without binary icons; drop your own PNGs in `icons/` before
distributing.

## Submitting to the Chrome Web Store

Zip this directory + a 128px icon. Listing copy lives in
`../../README.md` under the Pro features section.
