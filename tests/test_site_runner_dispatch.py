"""Notifier-dispatch coverage for `site_runner._dispatch_alerts`.

`run_weekly_reaudit` itself requires a real DB + audit pipeline, so we
exercise the inner dispatch helper directly. The contract we care about:

  - one NotifyDispatch row per (alert, enabled-notifier) pair
  - notifiers whose `enabled` property is falsy are skipped silently
  - a notifier that raises does not break the loop or downstream rows
  - empty alerts / no notifiers → empty list (cheap fast-path)
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.autopilot.site_runner import _dispatch_alerts, ScoreDropAlert
from app.integrations.notifier import FakeNotifier, NotifyResult


def _alert(url: str = "https://a/p", delta: int = -20) -> ScoreDropAlert:
    return ScoreDropAlert(
        site_id=UUID(int=0),
        page_id=1,
        url=url,
        prior_score=80,
        new_score=80 + delta,
        delta=delta,
        failed_checks=("readability",),
    )


@pytest.mark.asyncio
async def test_empty_alerts_returns_empty():
    out = await _dispatch_alerts(
        site_root_url="https://a/", alerts=[], notifiers=[FakeNotifier()]
    )
    assert out == []


@pytest.mark.asyncio
async def test_no_notifiers_returns_empty():
    out = await _dispatch_alerts(
        site_root_url="https://a/", alerts=[_alert()], notifiers=None
    )
    assert out == []


@pytest.mark.asyncio
async def test_each_alert_fans_to_each_notifier():
    n1, n2 = FakeNotifier(), FakeNotifier()
    n2.name = "fake2"
    alerts = [_alert(url="https://a/p1"), _alert(url="https://a/p2", delta=-30)]
    out = await _dispatch_alerts(
        site_root_url="https://a/", alerts=alerts, notifiers=[n1, n2]
    )
    assert len(out) == 4
    assert [d.page_url for d in out] == [
        "https://a/p1",
        "https://a/p1",
        "https://a/p2",
        "https://a/p2",
    ]
    assert {d.notifier for d in out} == {"fake", "fake2"}
    assert all(d.result.delivered for d in out)
    assert len(n1.calls) == 2 and len(n2.calls) == 2


@pytest.mark.asyncio
async def test_disabled_notifier_is_skipped():
    class DisabledNotifier(FakeNotifier):
        name = "off"
        enabled = False

    enabled = FakeNotifier()
    out = await _dispatch_alerts(
        site_root_url="https://a/",
        alerts=[_alert()],
        notifiers=[DisabledNotifier(), enabled],
    )
    assert len(out) == 1
    assert out[0].notifier == "fake"


@pytest.mark.asyncio
async def test_raising_notifier_is_caught_and_recorded_as_failed():
    class BoomNotifier:
        name = "boom"
        enabled = True

        async def notify_score_drop(self, **_kw):
            raise RuntimeError("network gone")

        async def notify_missing_cluster(self, **_kw):
            return NotifyResult(delivered=False)

    good = FakeNotifier()
    out = await _dispatch_alerts(
        site_root_url="https://a/",
        alerts=[_alert()],
        notifiers=[BoomNotifier(), good],
    )
    assert len(out) == 2
    boom_row = next(d for d in out if d.notifier == "boom")
    assert boom_row.result.delivered is False
    assert "RuntimeError" in (boom_row.result.detail or "")
    # The exception did not abort the loop — `good` still ran.
    fake_row = next(d for d in out if d.notifier == "fake")
    assert fake_row.result.delivered is True


@pytest.mark.asyncio
async def test_alert_payload_is_forwarded_intact():
    n = FakeNotifier()
    alert = _alert()
    await _dispatch_alerts(
        site_root_url="https://acme.test/",
        alerts=[alert],
        notifiers=[n],
    )
    payload = n.calls[0].payload
    assert payload["site_root_url"] == "https://acme.test/"
    assert payload["page_url"] == alert.url
    assert payload["prior_score"] == 80
    assert payload["new_score"] == 60
    assert payload["delta"] == -20
    assert payload["failed_checks"] == ("readability",)
