# AEGIS MCP server

Lets any MCP host (Claude Code, Claude Desktop, Cursor, Continue, …) call
AEGIS's audit + fan-out endpoints from inside a conversation.

## Tools exposed

| Tool name           | What it does                                                  | Scope required |
|---------------------|---------------------------------------------------------------|----------------|
| `aegis_ping`        | Verify API key, return org slug + scopes                      | `audit:read`   |
| `aegis_audit_url`   | Fetch + audit a public URL, return 0–100 AEO score + checks   | `audit:write`  |
| `aegis_audit_text`  | Same audit against raw HTML / plain text                      | `audit:write`  |
| `aegis_fanout`      | Generate 10–15 sub-queries grouped by intent, plus coverage   | (none, quota-gated) |

The handlers are thin wrappers around `POST /api/v1/audit` and
`POST /api/fanout/generate`. Errors and rate-limit envelopes are passed
through verbatim — the host renders them inline so the conversation can
react.

## Setup

1. Mint an API key in the Customer Console (`/account#keys`). Scopes
   `audit:read` + `audit:write` cover all four tools.
2. Drop one of the following snippets into your MCP host config.

### Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "aegis": {
      "command": "/opt/aegis/cli/aegis-mcp",
      "env": {
        "AEGIS_API_URL": "http://52.64.13.171/api/v1",
        "AEGIS_API_KEY": "aegis_ak_..."
      }
    }
  }
}
```

### Claude Code / Cursor

Both accept the same shape under `mcpServers`. Replace the path with
wherever you cloned the repo.

### Module form (no shebang, works on Windows)

```json
{
  "mcpServers": {
    "aegis": {
      "command": "/opt/aegis/.venv/bin/python",
      "args": ["-m", "cli.aegis_mcp"],
      "cwd": "/opt/aegis",
      "env": {
        "AEGIS_API_URL": "http://52.64.13.171/api/v1",
        "AEGIS_API_KEY": "aegis_ak_..."
      }
    }
  }
}
```

## Verify

```bash
AEGIS_API_URL=http://52.64.13.171/api/v1 \
AEGIS_API_KEY=aegis_ak_... \
  python -m cli.aegis_mcp <<< '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Should print a JSON-RPC response listing all four tool descriptors.

## Implementation notes

- JSON-RPC 2.0 over stdio. Stdout is the wire; logs go to stderr.
- Hand-rolled — no dependency on the upstream `mcp` Python SDK so the
  install footprint stays at `httpx` (already a project dep).
- Protocol version pinned at `2024-11-05`. Bump in
  `app/integrations/mcp_server.py::MCP_PROTOCOL_VERSION` when the host
  side moves.
