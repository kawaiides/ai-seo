#!/usr/bin/env node
// One-shot helper: audit a single Ghost post by slug from the Content
// API. Useful for local smoke tests + scripted catch-up runs.
//
//   AEGIS_API_KEY=... \
//   GHOST_URL=https://your.ghost.io \
//   GHOST_CONTENT_API_KEY=... \
//     node audit-one.js my-post-slug

const slug = process.argv[2];
if (!slug) {
  console.error("usage: audit-one.js <slug>");
  process.exit(2);
}

const {
  AEGIS_API_URL = "https://aegis-autopilot.com",
  AEGIS_API_KEY,
  GHOST_URL,
  GHOST_CONTENT_API_KEY,
} = process.env;

if (!AEGIS_API_KEY || !GHOST_URL || !GHOST_CONTENT_API_KEY) {
  console.error(
    "Need AEGIS_API_KEY, GHOST_URL, GHOST_CONTENT_API_KEY env vars."
  );
  process.exit(2);
}

const ghostBase = GHOST_URL.replace(/\/+$/, "");
const apiBase = AEGIS_API_URL.replace(/\/+$/, "");

const postUrl =
  `${ghostBase}/ghost/api/content/posts/slug/${encodeURIComponent(slug)}/` +
  `?key=${GHOST_CONTENT_API_KEY}&include=tags&fields=id,title,html`;

const postResp = await fetch(postUrl);
if (!postResp.ok) {
  console.error(`Ghost API: HTTP ${postResp.status}`);
  process.exit(4);
}
const { posts } = await postResp.json();
if (!posts || !posts[0]) {
  console.error(`No post with slug ${slug}`);
  process.exit(1);
}
const post = posts[0];

const auditResp = await fetch(`${apiBase}/api/v1/audit`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${AEGIS_API_KEY}`,
    "User-Agent": "AEGIS-Ghost-CLI/0.1.0",
  },
  body: JSON.stringify({ input_type: "text", input_value: post.html || "" }),
});
const audit = await auditResp.json();
if (!auditResp.ok) {
  console.error("AEGIS audit failed:", audit);
  process.exit(4);
}

console.log(
  `${post.title}\n  score: ${audit.aeo_score}\n  band:  ${audit.band}\n  plan:  ${audit.plan || "free"}`
);
for (const c of audit.checks || []) {
  const mark = c.passed ? "✓" : "✗";
  console.log(`  ${mark} ${c.name} (${c.score}/${c.max_score})`);
}
if (audit.locked_checks?.length) {
  console.log(
    "\nPro checks (locked):",
    audit.locked_checks.map((l) => l.name).join(", ")
  );
}
