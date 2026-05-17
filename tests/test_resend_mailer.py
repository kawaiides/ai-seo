"""Unit tests for `app.integrations.resend.ResendMailer`."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.resend import RESEND_API_URL, ResendMailer


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)


@pytest.mark.asyncio
async def test_disabled_when_no_key():
    n = ResendMailer(api_key=None)
    assert n.enabled is False
    out = await n.send(
        to="t@x", subject="s", html="<p>x</p>", text="x", from_addr="a@b"
    )
    assert out.delivered is False
    assert "not configured" in (out.detail or "")
    assert out.retryable is False


@pytest.mark.asyncio
async def test_success_returns_provider_id():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "abc123"})

    async with _mock_client(handler) as client:
        n = ResendMailer(api_key="re_test", client=client)
        out = await n.send(
            to="t@x.com",
            subject="hello",
            html="<p>hi</p>",
            text="hi",
            from_addr="hi@aegis.test",
        )
    assert out.delivered is True
    assert out.provider_id == "abc123"
    assert len(captured) == 1
    req = captured[0]
    assert req.url == httpx.URL(RESEND_API_URL)
    assert req.headers["Authorization"] == "Bearer re_test"
    body = json.loads(req.content.decode("utf-8"))
    assert body["from"] == "hi@aegis.test"
    assert body["to"] == ["t@x.com"]
    assert body["subject"] == "hello"
    assert body["html"] == "<p>hi</p>"
    assert body["text"] == "hi"
    assert "reply_to" not in body


@pytest.mark.asyncio
async def test_reply_to_forwarded_when_set():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "abc"})

    async with _mock_client(handler) as client:
        n = ResendMailer(api_key="re_test", client=client)
        await n.send(
            to="t@x.com",
            subject="s",
            html="<p>h</p>",
            text="h",
            from_addr="a@b.com",
            reply_to="reply@b.com",
        )
    body = json.loads(captured[0].content.decode("utf-8"))
    assert body["reply_to"] == "reply@b.com"


@pytest.mark.asyncio
async def test_4xx_is_hard_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"message":"bad from"}')

    async with _mock_client(handler) as client:
        n = ResendMailer(api_key="re_test", client=client)
        out = await n.send(
            to="t@x.com", subject="s", html="<p>h</p>", text="h", from_addr="a@b"
        )
    assert out.delivered is False
    assert "HTTP 400" in (out.detail or "")
    assert out.retryable is False


@pytest.mark.asyncio
async def test_429_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async with _mock_client(handler) as client:
        n = ResendMailer(api_key="re_test", client=client)
        out = await n.send(
            to="t@x.com", subject="s", html="<p>h</p>", text="h", from_addr="a@b"
        )
    assert out.delivered is False
    assert out.retryable is True


@pytest.mark.asyncio
async def test_5xx_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream")

    async with _mock_client(handler) as client:
        n = ResendMailer(api_key="re_test", client=client)
        out = await n.send(
            to="t@x.com", subject="s", html="<p>h</p>", text="h", from_addr="a@b"
        )
    assert out.delivered is False
    assert out.retryable is True
    assert "HTTP 503" in (out.detail or "")


@pytest.mark.asyncio
async def test_network_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect refused")

    async with _mock_client(handler) as client:
        n = ResendMailer(api_key="re_test", client=client)
        out = await n.send(
            to="t@x.com", subject="s", html="<p>h</p>", text="h", from_addr="a@b"
        )
    assert out.delivered is False
    assert out.retryable is True
    assert "ConnectError" in (out.detail or "")


@pytest.mark.asyncio
async def test_env_key_is_picked_up_when_no_arg(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_env")
    n = ResendMailer()
    assert n.enabled is True
