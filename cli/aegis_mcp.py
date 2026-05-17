"""CLI entry point for the AEGIS MCP server.

Two ways to invoke this in an MCP host's config (Claude Code, Claude
Desktop, Cursor, Continue, etc.):

  # Module form — works wherever /opt/aegis is on PYTHONPATH:
  command: /opt/aegis/.venv/bin/python
  args:    ["-m", "cli.aegis_mcp"]

  # Direct shebang form (after `chmod +x cli/aegis-mcp`):
  command: /opt/aegis/cli/aegis-mcp

Either way, set `AEGIS_API_URL` and `AEGIS_API_KEY` in `env`. See
`cli/AEGIS_MCP_README.md` for the full Claude Desktop config snippet.
"""

from __future__ import annotations

import sys

from app.integrations.mcp_server import main

if __name__ == "__main__":
    sys.exit(main())
