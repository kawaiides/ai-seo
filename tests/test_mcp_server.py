"""Unit tests for `app.integrations.mcp_server`.

JSON-RPC `handle_request` is exercised directly with a fake HTTP client
so we never touch the network. Stdio loop is not under test — it's a
thin asyncio adapter around the dispatcher.
"""

from __future__ import annotations

import json

import pytest

from app.integrations import mcp_server
from app.integrations.mcp_server import (
    MCP_PROTOCOL_VERSION,
    SERVER_NAME,
    TOOLS,
    AegisHTTPClient,
    handle_request,
)


class _FakeHTTP(AegisHTTPClient):
    """Captures method + path + payload, returns canned responses."""

    def __init__(self, *, get_response=None, post_response=None):
        super().__init__(api_url="http://test/api/v1", api_key="aegis_ak_test")
        self.calls: list[tuple[str, str, dict]] = []
        self._get_response = get_response or {"ok": True}
        self._post_response = post_response or {"ok": True}

    async def get(self, path):
        self.calls.append(("GET", path, {}))
        return self._get_response

    async def post(self, path, payload):
        self.calls.append(("POST", path, payload))
        return self._post_response

    async def _request(self, method, path, *, json_body=None):
        self.calls.append((method, path, json_body or {}))
        return self._post_response if method == "POST" else self._get_response


@pytest.mark.asyncio
async def test_initialize_echoes_protocol_version():
    out = await handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, _FakeHTTP())
    assert out is not None
    assert out["id"] == 1
    assert out["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert out["result"]["serverInfo"]["name"] == SERVER_NAME


@pytest.mark.asyncio
async def test_initialized_notification_returns_none():
    out = await handle_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, _FakeHTTP()
    )
    assert out is None


@pytest.mark.asyncio
async def test_tools_list_returns_all_four():
    out = await handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _FakeHTTP())
    assert out is not None
    tool_names = [t["name"] for t in out["result"]["tools"]]
    assert set(tool_names) == {
        "aegis_ping",
        "aegis_audit_url",
        "aegis_audit_text",
        "aegis_fanout",
    }
    # Each tool MUST carry an inputSchema (MCP spec requirement).
    for t in out["result"]["tools"]:
        assert "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
async def test_tool_count_matches_module_constant():
    assert len(TOOLS) == 4


@pytest.mark.asyncio
async def test_tools_call_aegis_ping_hits_ping_endpoint():
    http = _FakeHTTP(get_response={"org_slug": "acme", "scopes": ["audit:read"]})
    out = await handle_request({
        "jsonrpc": "2.0", "id": 3,
        "method": "tools/call",
        "params": {"name": "aegis_ping", "arguments": {}},
    }, http)
    assert out["id"] == 3
    assert out["result"]["isError"] is False
    assert http.calls == [("GET", "/ping", {})]
    # Result text is JSON-serialised AEGIS body.
    body = json.loads(out["result"]["content"][0]["text"])
    assert body["org_slug"] == "acme"


@pytest.mark.asyncio
async def test_tools_call_aegis_audit_url_posts_input_value():
    http = _FakeHTTP(post_response={"aeo_score": 87, "band": "AEO Optimized"})
    await handle_request({
        "jsonrpc": "2.0", "id": 4,
        "method": "tools/call",
        "params": {"name": "aegis_audit_url",
                   "arguments": {"url": "https://example.com/post"}},
    }, http)
    assert http.calls == [
        ("POST", "/audit",
         {"input_type": "url", "input_value": "https://example.com/post"}),
    ]


@pytest.mark.asyncio
async def test_tools_call_aegis_audit_text_posts_text_body():
    http = _FakeHTTP(post_response={"aeo_score": 50, "band": "Needs Improvement"})
    await handle_request({
        "jsonrpc": "2.0", "id": 5,
        "method": "tools/call",
        "params": {"name": "aegis_audit_text",
                   "arguments": {"text": "<h1>Hello</h1>"}},
    }, http)
    assert http.calls == [
        ("POST", "/audit",
         {"input_type": "text", "input_value": "<h1>Hello</h1>"}),
    ]


@pytest.mark.asyncio
async def test_tools_call_aegis_audit_url_missing_arg_is_isError():
    http = _FakeHTTP()
    out = await handle_request({
        "jsonrpc": "2.0", "id": 6,
        "method": "tools/call",
        "params": {"name": "aegis_audit_url", "arguments": {}},
    }, http)
    assert out["result"]["isError"] is True
    assert "missing" in out["result"]["content"][0]["text"]
    assert http.calls == []


@pytest.mark.asyncio
async def test_tools_call_unknown_tool_returns_jsonrpc_error():
    out = await handle_request({
        "jsonrpc": "2.0", "id": 7,
        "method": "tools/call",
        "params": {"name": "aegis_nuke", "arguments": {}},
    }, _FakeHTTP())
    assert "error" in out
    assert "unknown tool" in out["error"]["message"]


@pytest.mark.asyncio
async def test_unknown_method_returns_jsonrpc_error():
    out = await handle_request({
        "jsonrpc": "2.0", "id": 8, "method": "rpc.invent",
    }, _FakeHTTP())
    assert "error" in out
    assert "not implemented" in out["error"]["message"]


@pytest.mark.asyncio
async def test_aegis_fanout_uses_dedicated_fanout_url():
    http = _FakeHTTP(post_response={"sub_queries": []})
    await handle_request({
        "jsonrpc": "2.0", "id": 9,
        "method": "tools/call",
        "params": {
            "name": "aegis_fanout",
            "arguments": {"target_query": "best ai seo tool"},
        },
    }, http)
    assert len(http.calls) == 1
    method, path, body = http.calls[0]
    assert method == "POST"
    assert path.endswith("/generate")
    assert body["target_query"] == "best ai seo tool"


def test_http_client_picks_up_env_defaults(monkeypatch):
    monkeypatch.setenv("AEGIS_API_URL", "https://x.test/api/v1")
    monkeypatch.setenv("AEGIS_API_KEY", "aegis_ak_envkey")
    c = mcp_server.AegisHTTPClient()
    assert c.api_url == "https://x.test/api/v1"
    assert c.api_key == "aegis_ak_envkey"
    assert c._auth_headers()["Authorization"] == "Bearer aegis_ak_envkey"


def test_http_client_falls_back_to_demo_box_when_env_unset(monkeypatch):
    monkeypatch.delenv("AEGIS_API_URL", raising=False)
    monkeypatch.delenv("AEGIS_API_KEY", raising=False)
    c = mcp_server.AegisHTTPClient()
    assert c.api_url == "http://52.64.13.171/api/v1"
    assert c.api_key is None
    # Without a key, no Authorization header is attached.
    assert "Authorization" not in c._auth_headers()
