"""Notifier protocol shared by Slack + Linear integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NotifyResult:
    delivered: bool
    detail: str | None = None


class Notifier(Protocol):
    """Side-effecting downstream — production HTTPS post, test in-memory record."""

    name: str

    async def notify_score_drop(
        self,
        *,
        site_root_url: str,
        page_url: str,
        prior_score: int,
        new_score: int,
        delta: int,
        failed_checks: tuple[str, ...] = (),
    ) -> NotifyResult: ...

    async def notify_missing_cluster(
        self,
        *,
        site_root_url: str,
        cluster_label: str,
        dominant_type: str,
        page_url: str | None = None,
    ) -> NotifyResult: ...


@dataclass
class RecordedNotification:
    kind: str
    payload: dict


class FakeNotifier:
    """Test double; records every notification in-memory."""

    name: str = "fake"

    def __init__(self) -> None:
        self.calls: list[RecordedNotification] = []

    async def notify_score_drop(self, **payload) -> NotifyResult:  # noqa: ANN003
        self.calls.append(RecordedNotification(kind="score_drop", payload=payload))
        return NotifyResult(delivered=True)

    async def notify_missing_cluster(self, **payload) -> NotifyResult:  # noqa: ANN003
        self.calls.append(
            RecordedNotification(kind="missing_cluster", payload=payload)
        )
        return NotifyResult(delivered=True)
