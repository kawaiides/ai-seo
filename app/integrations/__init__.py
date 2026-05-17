"""Outbound integrations (Phase D).

Slack + Linear webhook-out helpers that fire on score-drop and missing
comparative-cluster events. Both implementations honour a `Notifier`
Protocol so tests can substitute a `FakeNotifier`.
"""
