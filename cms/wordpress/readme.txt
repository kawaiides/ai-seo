=== AEGIS — AEO Optimizer ===
Contributors: aegis
Tags: aeo, seo, ai search, llm, optimization, gpt, perplexity
Requires at least: 6.0
Tested up to: 6.5
Requires PHP: 7.4
Stable tag: 0.1.0
License: MIT
License URI: https://opensource.org/licenses/MIT

Audit any WordPress draft against AEGIS — the AI search optimization platform.
Run the same A–G AEO checks (direct answer, heading hierarchy, readability,
entity coverage, schema markup, citations, freshness) used by the AEGIS
dashboard, right from the Gutenberg sidebar.

== Description ==

The AEGIS WordPress plugin adds an "AEGIS AEO" panel to the Gutenberg
post sidebar. Click "Audit now" and the plugin posts the current post
content to your AEGIS account via `/api/v1/audit`; the result — score,
band, per-check pass/fail breakdown — renders inline.

* Free checks: Direct Answer, Heading Hierarchy, Readability.
* Pro checks (require an active subscription on the AEGIS side): Entity
  Coverage, Schema Markup, Citations, Freshness.

The API key never reaches the browser bundle. The editor calls a
WordPress REST proxy (`/wp-json/aegis/v1/audit`) registered by this
plugin; the proxy forwards to AEGIS with the configured Bearer token.

== Installation ==

1. Copy this directory to `wp-content/plugins/aegis/`.
2. Activate "AEGIS — AEO Optimizer" from the WordPress Plugins page.
3. Go to Settings → AEGIS and paste your AEGIS API key
   (Org → API keys, scope `audit:write`).
4. Edit any post; the AEGIS sidebar panel appears in the Gutenberg
   right-hand menu (chart-bar icon).

== Frequently Asked Questions ==

= Where do I get an API key? =

aegis-autopilot.com → Org → API keys. Mint a key with the
`audit:write` scope.

= Does this send my post content to a third party? =

The plugin sends the post body to your AEGIS account (server-to-server
via Bearer auth). No third-party trackers are loaded.

== Changelog ==

= 0.1.0 =
* Initial release: Gutenberg sidebar audit panel.
