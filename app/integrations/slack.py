"""Slack notifier — incoming-webhook post.

Honours the `Notifier` Protocol. Auto-skips delivery if `webhook_url`
isn't set so dev environments don't accidentally page #content with
their staging audits.
"""

from __future__ import annotations

import json
import os

import httpx

from app.integrations.notifier import NotifyResult


class SlackNotifier:
    name = "slack"

    def __init__(
        self,
        *,
        webhook_url: str | None = None,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
        self._timeout = timeout
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._webhook_url)

    async def notify_score_drop(
        self,
        *,
        site_root_url: str,
        page_url: str,
        prior_score: int,
        new_score: int,
        delta: int,
        failed_checks: tuple[str, ...] = (),
    ) -> NotifyResult:
        text = (
            f":warning: *AEO score drop* for `{site_root_url}`\n"
            f"`{page_url}`: *{prior_score} → {new_score}* ({delta:+d})"
        )
        if failed_checks:
            text += "\nNewly failing: " + ", ".join(f"`{c}`" for c in failed_checks)
        return await self._post({"text": text})

    async def notify_missing_cluster(
        self,
        *,
        site_root_url: str,
        cluster_label: str,
        dominant_type: str,
        page_url: str | None = None,
    ) -> NotifyResult:
        text = (
            f":mag: *Missing intent cluster* on `{site_root_url}`\n"
            f"`{dominant_type}` — {cluster_label}"
        )
        if page_url:
            text += f"\nPage: <{page_url}>"
        return await self._post({"text": text})

    async def _post(self, payload: dict) -> NotifyResult:
        if not self._webhook_url:
            return NotifyResult(delivered=False, detail="slack webhook not configured")
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._client is None
        try:
            resp = await client.post(
                self._webhook_url,
                content=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                return NotifyResult(
                    delivered=False,
                    detail=f"slack HTTP {resp.status_code}: {resp.text[:200]}",
                )
            return NotifyResult(delivered=True)
        except httpx.HTTPError as e:
            return NotifyResult(
                delivered=False,
                detail=f"slack network error: {type(e).__name__}: {e}",
            )
        finally:
            if owns_client:
                await client.aclose()
