"""Linear notifier — issue creation via GraphQL.

Honours the `Notifier` Protocol. Skips delivery when `api_key` or
`team_id` aren't set; tests can also inject an `httpx.AsyncClient`.
"""

from __future__ import annotations

import json
import os

import httpx

from app.integrations.notifier import NotifyResult

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


class LinearNotifier:
    name = "linear"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        team_id: str | None = None,
        timeout: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("LINEAR_API_KEY")
        self._team_id = team_id or os.environ.get("LINEAR_TEAM_ID")
        self._timeout = timeout
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self._api_key and self._team_id)

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
        title = (
            f"AEO score drop: {page_url} ({prior_score} → {new_score}, {delta:+d})"
        )
        body = (
            f"`{page_url}` on `{site_root_url}` dropped by {abs(delta)} points.\n\n"
            "Failed checks:\n"
            + ("\n".join(f"- `{c}`" for c in failed_checks) or "_none reported_")
        )
        return await self._create_issue(title=title, body=body)

    async def notify_missing_cluster(
        self,
        *,
        site_root_url: str,
        cluster_label: str,
        dominant_type: str,
        page_url: str | None = None,
    ) -> NotifyResult:
        title = f"Missing {dominant_type} cluster on {site_root_url}: {cluster_label}"
        body = (
            f"Site `{site_root_url}` is not covering the `{dominant_type}` intent "
            f"cluster: **{cluster_label}**."
        )
        if page_url:
            body += f"\n\nReference page: <{page_url}>"
        return await self._create_issue(title=title, body=body)

    async def _create_issue(self, *, title: str, body: str) -> NotifyResult:
        if not self.enabled:
            return NotifyResult(delivered=False, detail="linear key/team not configured")
        mutation = """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) { success issue { id identifier } }
        }
        """
        variables = {
            "input": {
                "teamId": self._team_id,
                "title": title,
                "description": body,
            }
        }
        payload = {"query": mutation, "variables": variables}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._client is None
        try:
            resp = await client.post(
                LINEAR_GRAPHQL_URL,
                content=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self._api_key or "",
                },
            )
            if resp.status_code >= 400:
                return NotifyResult(
                    delivered=False,
                    detail=f"linear HTTP {resp.status_code}: {resp.text[:200]}",
                )
            data = resp.json()
            if "errors" in data:
                return NotifyResult(
                    delivered=False,
                    detail=f"linear GraphQL error: {data['errors'][:1]!r}",
                )
            success = (
                data.get("data", {}).get("issueCreate", {}).get("success", False)
            )
            return NotifyResult(
                delivered=bool(success),
                detail=None if success else "linear issueCreate returned success=false",
            )
        except httpx.HTTPError as e:
            return NotifyResult(
                delivered=False,
                detail=f"linear network error: {type(e).__name__}: {e}",
            )
        finally:
            if owns_client:
                await client.aclose()
