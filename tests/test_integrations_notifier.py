"""Unit tests for Slack + Linear notifiers (Phase D)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.linear import LinearNotifier
from app.integrations.notifier import FakeNotifier
from app.integrations.slack import SlackNotifier


# -------------------- FakeNotifier --------------------


@pytest.mark.asyncio
async def test_fake_notifier_records_score_drop():
    n = FakeNotifier()
    out = await n.notify_score_drop(
        site_root_url="https://a/",
        page_url="https://a/p",
        prior_score=80,
        new_score=60,
        delta=-20,
        failed_checks=("readability",),
    )
    assert out.delivered is True
    assert len(n.calls) == 1
    assert n.calls[0].kind == "score_drop"
    assert n.calls[0].payload["new_score"] == 60


@pytest.mark.asyncio
async def test_fake_notifier_records_missing_cluster():
    n = FakeNotifier()
    await n.notify_missing_cluster(
        site_root_url="https://a/",
        cluster_label="HubSpot vs Salesforce",
        dominant_type="comparative",
        page_url="https://a/blog/x",
    )
    assert n.calls[0].kind == "missing_cluster"
    assert n.calls[0].payload["dominant_type"] == "comparative"


# -------------------- SlackNotifier --------------------


def _make_client(captured: list[httpx.Request], status: int = 200, body: str = "ok") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="")


@pytest.mark.asyncio
async def test_slack_notifier_disabled_when_no_webhook():
    n = SlackNotifier(webhook_url=None)
    assert n.enabled is False
    out = await n.notify_score_drop(
        site_root_url="x", page_url="x", prior_score=80, new_score=60, delta=-20
    )
    assert out.delivered is False
    assert "not configured" in (out.detail or "")


@pytest.mark.asyncio
async def test_slack_notifier_posts_when_enabled():
    captured: list[httpx.Request] = []
    async with _make_client(captured) as client:
        n = SlackNotifier(
            webhook_url="https://hooks.slack.com/services/x", client=client
        )
        assert n.enabled
        out = await n.notify_score_drop(
            site_root_url="https://a/",
            page_url="https://a/p",
            prior_score=80,
            new_score=60,
            delta=-20,
            failed_checks=("readability",),
        )
    assert out.delivered is True
    assert len(captured) == 1
    body = json.loads(captured[0].content.decode("utf-8"))
    assert "AEO score drop" in body["text"]
    assert "80 → 60" in body["text"]
    assert "readability" in body["text"]


@pytest.mark.asyncio
async def test_slack_notifier_reports_http_failure():
    captured: list[httpx.Request] = []
    async with _make_client(captured, status=500, body="boom") as client:
        n = SlackNotifier(webhook_url="https://hooks.slack.com/x", client=client)
        out = await n.notify_score_drop(
            site_root_url="x", page_url="x", prior_score=80, new_score=60, delta=-20
        )
    assert out.delivered is False
    assert "HTTP 500" in (out.detail or "")


# -------------------- LinearNotifier --------------------


@pytest.mark.asyncio
async def test_linear_notifier_disabled_without_key_or_team():
    n = LinearNotifier(api_key=None, team_id=None)
    assert n.enabled is False
    out = await n.notify_missing_cluster(
        site_root_url="x", cluster_label="y", dominant_type="how_to"
    )
    assert out.delivered is False


@pytest.mark.asyncio
async def test_linear_notifier_creates_issue_on_success():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "issueCreate": {
                        "success": True,
                        "issue": {"id": "iss-1", "identifier": "ENG-42"},
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="") as client:
        n = LinearNotifier(api_key="k", team_id="t", client=client)
        out = await n.notify_missing_cluster(
            site_root_url="https://a/",
            cluster_label="HubSpot vs Salesforce",
            dominant_type="comparative",
            page_url="https://a/p",
        )
    assert out.delivered is True
    body = json.loads(captured[0].content.decode("utf-8"))
    assert "issueCreate" in body["query"]
    assert "comparative" in body["variables"]["input"]["title"]


@pytest.mark.asyncio
async def test_linear_notifier_reports_graphql_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Unauthorized"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="") as client:
        n = LinearNotifier(api_key="k", team_id="t", client=client)
        out = await n.notify_score_drop(
            site_root_url="x", page_url="x", prior_score=80, new_score=60, delta=-20
        )
    assert out.delivered is False
    assert "GraphQL" in (out.detail or "")
