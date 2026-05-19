# AEGIS — Ghost integration

Two pieces:

1. **`server.js`** — a tiny Node 18+ HTTP server that receives Ghost
   webhooks and audits each new / edited post against AEGIS. Pure
   Node, no npm deps. Verifies the `X-Ghost-Signature` HMAC when
   `GHOST_WEBHOOK_SECRET` is set.
2. **`audit-one.js`** — one-shot CLI: pull a post by slug via Ghost's
   Content API, send its HTML to `/api/v1/audit`, print the score +
   per-check breakdown. Use for backfilling old posts.

## Webhook setup

In Ghost admin: **Integrations → Add custom integration → Webhooks**:

| Field         | Value                                  |
|---------------|----------------------------------------|
| Name          | AEGIS AEO                              |
| Event         | `Post published` (repeat for `edited`) |
| Target URL    | `https://your-aegis-bridge.example.com/webhook` |
| Secret        | (paste a random value, also set as `GHOST_WEBHOOK_SECRET`) |

Then run the receiver:

```bash
AEGIS_API_KEY=aegis_ak_... \
GHOST_WEBHOOK_SECRET=$(openssl rand -hex 32) \
PORT=8788 \
node server.js
```

## Backfill an existing post

```bash
AEGIS_API_KEY=aegis_ak_... \
GHOST_URL=https://your.ghost.io \
GHOST_CONTENT_API_KEY=... \
  node audit-one.js my-post-slug
```

## Writeback (optional)

Set `WRITEBACK=1` plus `GHOST_ADMIN_URL` / `GHOST_ADMIN_API_KEY` to
have the score pushed back into the post as a hidden meta tag. The
JWT signing required by Ghost's Admin API is left as an integration
point in `server.js` — drop in `jsonwebtoken` and the key id/secret
split when you wire it.
