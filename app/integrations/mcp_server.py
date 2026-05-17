"""AEGIS MCP server — bridges the public REST API into Model Context Protocol.

Exposes four tools an MCP-aware client (Claude Code, Claude Desktop,
Cursor, Continue, etc.) can call from a conversation:

  aegis_audit_url   → POST /api/v1/audit    (input_type=url)
  aegis_audit_text  → POST /api/v1/audit    (input_type=text)
  aegis_fanout      → POST /api/fanout/generate
  aegis_ping        → GET  /api/v1/ping

Wire format is JSON-RPC 2.0 over stdio per the MCP spec. We hand-roll the
loop (~80 LOC) so the dependency on the upstream `mcp` Python SDK stays
optional — that SDK pins newer Python + brings a websocket/sse stack we
don't need for a stdio transport.

Config the user pastes into their MCP host:

    {
      "mcpServers": {
        "aegis": {
          "command": "aegis-mcp",
          "env": {
            "AEGIS_API_URL": "http://52.64.13.171/api/v1",
            "AEGIS_API_KEY": "aegis_ak_..."
          }
        }
      }
    }

`AEGIS_API_KEY` is the Bearer key minted in the Customer Console
(`/account#keys`). Required scopes per tool are declared in the tool's
schema so the MCP host can surface "this needs audit:write" in its UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger("aegis.mcp")

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aegis"
SERVER_VERSION = "0.1.0"

# Where this MCP server talks. Defaults to the demo box; operators can
# point at localhost or a future custom domain via env.
DEFAULT_API_URL = "http://52.64.13.171/api/v1"
DEFAULT_FANOUT_URL = "http://52.64.13.171/api/fanout"


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], "AegisHTTPClient"], Awaitable[dict[str, Any]]]


class AegisHTTPClient:
    """Async REST client carrying the user-supplied Bearer key.

    Pulled out so tests can monkey-patch the transport without poking
    every tool handler. The same client instance is reused across tool
    invocations within one process lifetime.
    """

    def __init__(
        self,
        *,
        api_url: str | None = None,
        fanout_url: str | None = None,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_url = (api_url or os.environ.get("AEGIS_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.fanout_url = (
            fanout_url
            or os.environ.get("AEGIS_FANOUT_URL")
            or DEFAULT_FANOUT_URL
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("AEGIS_API_KEY")
        self._client = client
        self._timeout = timeout

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json_body=payload)

    async def get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.api_url.rstrip('/')}{path}"
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._client is None
        try:
            if method == "POST":
                resp = await client.post(
                    url,
                    content=json.dumps(json_body or {}).encode("utf-8"),
                    headers=self._auth_headers(),
                )
            else:
                resp = await client.get(url, headers=self._auth_headers())
        finally:
            if owns_client:
                await client.aclose()

        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        if 200 <= resp.status_code < 300:
            return body
        return {
            "error": {
                "status": resp.status_code,
                "message": (body or {}).get("message")
                or (body or {}).get("detail")
                or f"HTTP {resp.status_code}",
                "body": body,
            }
        }


# ---- tool handlers ----


async def _handle_ping(_args: dict[str, Any], http: AegisHTTPClient) -> dict[str, Any]:
    return await http.get("/ping")


async def _handle_audit_url(args: dict[str, Any], http: AegisHTTPClient) -> dict[str, Any]:
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": {"message": "missing required arg 'url'"}}
    return await http.post("/audit", {"input_type": "url", "input_value": url})


async def _handle_audit_text(args: dict[str, Any], http: AegisHTTPClient) -> dict[str, Any]:
    text = args.get("text") or ""
    if not text.strip():
        return {"error": {"message": "missing required arg 'text'"}}
    return await http.post("/audit", {"input_type": "text", "input_value": text})


async def _handle_fanout(args: dict[str, Any], http: AegisHTTPClient) -> dict[str, Any]:
    target_query = (args.get("target_query") or "").strip()
    if not target_query:
        return {"error": {"message": "missing required arg 'target_query'"}}
    payload: dict[str, Any] = {"target_query": target_query}
    if args.get("content"):
        payload["content"] = args["content"]
    if args.get("target_locale"):
        payload["target_locale"] = args["target_locale"]
    # Fan-out lives outside /api/v1; use the dedicated host endpoint.
    return await http._request("POST", f"{http.fanout_url}/generate", json_body=payload)


TOOLS: tuple[_Tool, ...] = (
    _Tool(
        name="aegis_ping",
        description=(
            "Verify the AEGIS API key is valid and report the org + scopes it "
            "carries. Cheap; safe to call repeatedly. Requires scope audit:read."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_handle_ping,
    ),
    _Tool(
        name="aegis_audit_url",
        description=(
            "Run the AEGIS Answer-Engine-Optimization audit against a public URL. "
            "Fetches the page server-side, parses the HTML, and runs 7 checks "
            "(direct-answer length, heading hierarchy, readability, entity "
            "coverage, schema, citations, freshness). Returns a 0-100 score, "
            "band label, and per-check pass/fail breakdown. Requires scope audit:write."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Public URL of the page to audit.",
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        handler=_handle_audit_url,
    ),
    _Tool(
        name="aegis_audit_text",
        description=(
            "Same AEO audit as aegis_audit_url, but takes raw HTML or plain "
            "text directly. Useful for drafts that aren't yet published. "
            "Requires scope audit:write."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Raw HTML or plain text to audit.",
                }
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=_handle_audit_text,
    ),
    _Tool(
        name="aegis_fanout",
        description=(
            "Generate 10-15 sub-queries that AI search engines will fan out "
            "from a target search query, grouped by intent (comparative, "
            "feature, use-case, trust, how-to, definitional). Optionally "
            "checks which sub-queries the supplied content covers. "
            "Counts against the daily free-scan quota unless a BYOK header "
            "is set on the underlying request."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_query": {
                    "type": "string",
                    "description": "The buyer-intent search query to expand.",
                },
                "content": {
                    "type": "string",
                    "description": "Optional draft / page text to check coverage against.",
                },
                "target_locale": {
                    "type": "string",
                    "description": "BCP-47 locale (e.g. en-US, en-IN, hi-IN). "
                                   "Routes the LLM prompt for non-English fan-out.",
                },
            },
            "required": ["target_query"],
            "additionalProperties": False,
        },
        handler=_handle_fanout,
    ),
)


_TOOL_INDEX: dict[str, _Tool] = {t.name: t for t in TOOLS}


# ---- JSON-RPC plumbing ----


def _tool_descriptors() -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        }
        for t in TOOLS
    ]


def _rpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def handle_request(
    req: dict[str, Any], http: AegisHTTPClient
) -> dict[str, Any] | None:
    """Dispatch a single JSON-RPC request.

    Returns None for notifications (no `id`), the full response dict
    otherwise. Tests call this directly to avoid spinning the stdio loop.
    """
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

    if method == "initialize":
        return _rpc_result(req_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications get no response

    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _tool_descriptors()})

    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        tool = _TOOL_INDEX.get(name)
        if tool is None:
            return _rpc_error(req_id, -32601, f"unknown tool: {name}")
        try:
            result = await tool.handler(args, http)
        except Exception as e:  # noqa: BLE001 — protocol error envelope handles it
            log.exception("tool call failed: %s", name)
            return _rpc_result(req_id, {
                "content": [{"type": "text", "text": f"error: {type(e).__name__}: {e}"}],
                "isError": True,
            })
        # MCP wraps tool output in a `content` array; for structured data we
        # emit a single text block carrying the JSON so clients can pretty-
        # print or parse it back. Clients that want structured output can
        # set `structuredContent: result` once the spec ratifies that field.
        return _rpc_result(req_id, {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            "isError": "error" in result,
        })

    return _rpc_error(req_id, -32601, f"method not implemented: {method}")


# ---- stdio loop ----


async def _run_stdio(http: AegisHTTPClient) -> int:
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_running_loop()
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    transport, _ = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(transport, _, None, loop)

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            req = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as e:
            log.warning("malformed JSON-RPC line dropped: %s", e)
            continue
        resp = await handle_request(req, http)
        if resp is not None:
            writer.write((json.dumps(resp) + "\n").encode("utf-8"))
            await writer.drain()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("AEGIS_MCP_LOG", "WARNING"),
        stream=sys.stderr,  # JSON-RPC owns stdout
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    http = AegisHTTPClient()
    if not http.api_key:
        log.warning(
            "AEGIS_API_KEY not set — tool calls will hit /api/v1 without "
            "auth and most will 401. Mint a key at /account#keys."
        )
    return asyncio.run(_run_stdio(http))


if __name__ == "__main__":
    sys.exit(main())
