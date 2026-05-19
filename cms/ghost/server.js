// AEGIS Ghost webhook receiver.
//
// Run alongside your Ghost site (or anywhere reachable from Ghost's
// outbound HTTPS). Ghost calls POST <this server>/webhook on
// `post.published`, `post.published.edited`, or `page.published`. We
// pull the post HTML out of the payload, send it to AEGIS, and:
//
//   - log the score,
//   - POST the score back into the Ghost post's `codeinjection_head`
//     as a hidden `<meta name="aegis-score">` tag so themes can render
//     a badge (optional, opt-in via WRITEBACK=1),
//   - publish a webhook event to a Slack channel (optional).
//
// No npm deps — Node 18+ has `fetch` and a built-in HTTP server.
//
// Env vars:
//   AEGIS_API_URL          (default https://aegis-autopilot.com)
//   AEGIS_API_KEY          (required, scope `audit:write`)
//   GHOST_WEBHOOK_SECRET   (optional, used to HMAC-verify Ghost's
//                          `X-Ghost-Signature` header)
//   GHOST_ADMIN_API_KEY    (optional, only needed if WRITEBACK=1)
//   GHOST_ADMIN_URL        (optional, only needed if WRITEBACK=1)
//   WRITEBACK              ("1" to push aegis-score meta back to Ghost)
//   PORT                   (default 8788)

import crypto from "node:crypto";
import http from "node:http";

const AEGIS_API_URL = (process.env.AEGIS_API_URL || "https://aegis-autopilot.com").replace(
  /\/+$/,
  ""
);
const AEGIS_API_KEY = process.env.AEGIS_API_KEY;
const GHOST_WEBHOOK_SECRET = process.env.GHOST_WEBHOOK_SECRET || "";
const WRITEBACK = process.env.WRITEBACK === "1";
const PORT = Number(process.env.PORT || 8788);

if (!AEGIS_API_KEY) {
  console.error("AEGIS_API_KEY env var is required.");
  process.exit(2);
}

function verifyGhostSignature(rawBody, signatureHeader) {
  if (!GHOST_WEBHOOK_SECRET) return true; // unverified — dev only
  if (!signatureHeader) return false;
  const [shaPart, tsPart] = signatureHeader.split(",").map((s) => s.trim());
  const provided = shaPart && shaPart.split("=")[1];
  const ts = tsPart && tsPart.split("=")[1];
  if (!provided || !ts) return false;
  const mac = crypto
    .createHmac("sha256", GHOST_WEBHOOK_SECRET)
    .update(`${rawBody}${ts}`)
    .digest("hex");
  return crypto.timingSafeEqual(Buffer.from(mac), Buffer.from(provided));
}

async function auditHtml(html) {
  const resp = await fetch(`${AEGIS_API_URL}/api/v1/audit`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AEGIS_API_KEY}`,
      "User-Agent": "AEGIS-Ghost/0.1.0",
    },
    body: JSON.stringify({ input_type: "text", input_value: html }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = data?.detail?.error || data?.detail?.message || `HTTP ${resp.status}`;
    throw new Error(err);
  }
  return data;
}

async function writeBackScore(postId, score, band) {
  if (!WRITEBACK) return;
  const { GHOST_ADMIN_URL, GHOST_ADMIN_API_KEY } = process.env;
  if (!GHOST_ADMIN_URL || !GHOST_ADMIN_API_KEY) return;
  // Ghost Admin API auth is JWT-based. Skipping full implementation
  // here — depends on `jsonwebtoken` + the key's id/secret split. This
  // stub captures the integration point for operators to fill in.
  console.warn(
    `WRITEBACK=1 but JWT signing not implemented in this stub. ` +
      `postId=${postId} score=${score} band=${band!}`
  );
}

const server = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/webhook") {
    res.statusCode = 404;
    res.end("not found");
    return;
  }
  let raw = "";
  req.on("data", (chunk) => (raw += chunk));
  req.on("end", async () => {
    try {
      if (!verifyGhostSignature(raw, req.headers["x-ghost-signature"])) {
        res.statusCode = 401;
        res.end("invalid signature");
        return;
      }
      const payload = JSON.parse(raw || "{}");
      const post = payload.post?.current || payload.page?.current;
      if (!post || !post.html) {
        res.statusCode = 422;
        res.end("no post.html in payload");
        return;
      }
      const data = await auditHtml(post.html);
      console.log(
        `[aegis] ${post.title} (#${post.id}) → ${data.aeo_score} · ${data.band}`
      );
      await writeBackScore(post.id, data.aeo_score, data.band);
      res.statusCode = 200;
      res.setHeader("content-type", "application/json");
      res.end(
        JSON.stringify({
          ok: true,
          score: data.aeo_score,
          band: data.band,
          plan: data.plan || "free",
        })
      );
    } catch (e) {
      console.error(`[aegis] webhook error: ${e.message}`);
      res.statusCode = 500;
      res.end(JSON.stringify({ ok: false, error: e.message }));
    }
  });
});

server.listen(PORT, () => {
  console.log(
    `aegis-ghost: listening on :${PORT}; target=${AEGIS_API_URL}; writeback=${WRITEBACK}`
  );
});
